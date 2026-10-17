"""Explicit Desktop review boundary for the Catalog-owned CircuitIntent."""

from __future__ import annotations

import copy
import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Literal

from pydantic import model_validator

from embedded_copilot.engineering_runtime import (
    CircuitIntentObject,
    ContextRequest,
    LifecycleActor,
    LifecycleManager,
    LifecycleStatus,
    ObjectMutationRecord,
    ObjectProposal,
    ObjectType,
    ProposalStatus,
    Provenance,
    RevisionManager,
)
from embedded_copilot.engineering_runtime.common import (
    RuntimeContract,
    canonical_fingerprint,
)
from embedded_copilot.engineering_runtime.evidence import (
    CanonicalOperationReceiptEvidenceResolver,
    EvidenceReceiptResolutionError,
    EvidenceReferenceStatus,
)
from embedded_copilot.execution_worker.contracts import (
    OperationStatus,
    OperationType,
    SchematicErcOperation,
)
from embedded_copilot.execution_worker.service import (
    ExecutionRejected,
    ExecutionUnavailable,
)
from embedded_copilot.project_catalog.exceptions import (
    ProjectCatalogConflict,
    ProjectCatalogRejected,
)
from embedded_copilot.project_catalog.models import (
    CircuitProposalCandidate,
    CircuitPublication,
    ConversationDetail,
    ConversationTurnStatus,
    PredesignCircuitCandidate,
    PredesignSourceSnapshot,
    circuit_decision_record,
    circuit_source_fingerprint,
    predesign_source_snapshot,
)

from .engineering_runtime_integration import (
    CircuitIntentSpec,
    EngineeringObjectFlowResult,
    EvidenceReceiptReference,
)
from .models import ApplicationQuestionRequest, ApplicationRoute


class CircuitPreviewCommand(RuntimeContract):
    conversation_id: str
    turn_id: str
    circuit_intent: CircuitIntentSpec
    erc_operation_id: str
    object_id: str | None = None
    idempotency_key: str
    constraints: tuple[str, ...] = ()


class CircuitDecisionCommand(RuntimeContract):
    proposal_id: str
    proposal_fingerprint: str
    base_revision: str | None = None
    base_fingerprint: str | None = None
    context_fingerprint: str
    evidence_receipt_fingerprint: str
    decision: Literal["APPROVE", "REJECT"]


class PredesignSourceExpectations(RuntimeContract):
    requirement_context_fingerprint: str
    architecture_id: str
    architecture_fingerprint: str
    hardware_selection_approval_id: str
    hardware_truth_fingerprint: str


class PredesignPreviewCommand(RuntimeContract):
    purpose: Literal["PREDESIGN_APPROVAL"]
    conversation_id: str
    turn_id: str
    circuit_intent: CircuitIntentSpec
    idempotency_key: str
    object_id: str | None = None
    base_revision: str | None = None
    base_fingerprint: str | None = None
    source_expectations: PredesignSourceExpectations

    @model_validator(mode="after")
    def _base(self) -> PredesignPreviewCommand:
        if (self.object_id is None) != (self.base_revision is None) or (
            (self.base_revision is None) != (self.base_fingerprint is None)
        ):
            raise ValueError("predesign base identity is incomplete")
        return self


class PredesignDecisionCommand(RuntimeContract):
    purpose: Literal["PREDESIGN_APPROVAL"]
    proposal_id: str
    proposal_fingerprint: str
    source_fingerprint: str
    base_revision: str | None = None
    base_fingerprint: str | None = None
    decision: Literal["APPROVE", "REJECT"]


def _id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _spec(obj: CircuitIntentObject) -> CircuitIntentSpec:
    return CircuitIntentSpec.model_validate(
        {name: getattr(obj, name) for name in CircuitIntentSpec.model_fields}, strict=False
    )


class CircuitPublicationService:
    """Rebuild a reviewed proposal before one Catalog CAS publication."""

    __slots__ = ("_application", "_catalog", "_execution")

    def __init__(self, *, catalog: object, application: object, execution: object) -> None:
        for method in (
            "get_project_for_requester", "conversation", "routing_context_for_conversation",
            "current_circuit_intents", "save_circuit_proposal", "circuit_proposal",
            "circuit_proposal_by_idempotency", "decide_circuit_proposal",
            "validate_current_schematic_erc_source",
        ):
            if not callable(getattr(catalog, method, None)):
                raise TypeError("Circuit Catalog boundary is unavailable")
        for method in ("classify", "run_engineering_object_flow"):
            if not callable(getattr(application, method, None)):
                raise TypeError("Circuit application boundary is unavailable")
        for method in ("get_operation", "get_status", "get_receipt"):
            if not callable(getattr(execution, method, None)):
                raise TypeError("Circuit execution boundary is unavailable")
        self._catalog = catalog
        self._application = application
        self._execution = execution

    def _canonical_reference(
        self, *, project_id: str, tenant_id: str, requester: str,
        operation_id: str,
    ) -> EvidenceReceiptReference:
        operation = self._execution.get_operation(
            tenant_id=tenant_id, project_id=project_id, operation_id=operation_id,
        )
        status = self._execution.get_status(
            tenant_id=tenant_id, project_id=project_id, operation_id=operation_id,
        )
        try:
            receipt = self._execution.get_receipt(
                tenant_id=tenant_id, project_id=project_id, operation_id=operation_id,
            )
        except (ExecutionRejected, ExecutionUnavailable) as error:
            raise ProjectCatalogRejected("canonical ERC receipt is unavailable") from error
        if not isinstance(operation, SchematicErcOperation) or (
            operation.operation_type is not OperationType.KICAD_SCHEMATIC_ERC
            or operation.project_id != project_id or operation.tenant_id != tenant_id
            or operation.requester_id != requester
            or status is None or status.status is not OperationStatus.SUCCEEDED
            or status.operation_id != operation_id
            or status.tenant_id != tenant_id or status.project_id != project_id
            or status.operation_type is not OperationType.KICAD_SCHEMATIC_ERC
            or receipt is None or receipt.status is not OperationStatus.SUCCEEDED
            or receipt.tool_execution_status != "SUCCEEDED"
            or status.receipt_fingerprint != receipt.fingerprint
            or receipt.operation_id != operation_id
            or receipt.tenant_id != tenant_id or receipt.project_id != project_id
            or receipt.operation_type is not OperationType.KICAD_SCHEMATIC_ERC
            or len(receipt.evidence_references) != 1
            or receipt.schematic_erc_result is None
            or receipt.schematic_erc_status != "ERC_PASS"
        ):
            raise ProjectCatalogRejected("canonical ERC operation and receipt are required")
        result = receipt.schematic_erc_result
        try:
            source_receipt = self._execution.get_receipt(
                tenant_id=tenant_id, project_id=project_id,
                operation_id=operation.source_operation_id,
            )
        except (ExecutionRejected, ExecutionUnavailable) as error:
            raise ProjectCatalogRejected("source schematic receipt is unavailable") from error
        self._catalog.validate_current_schematic_erc_source(
            operation=operation, receipt=receipt, source_receipt=source_receipt,
            tenant_id=tenant_id, requester=requester,
        )
        reference = EvidenceReceiptReference(
            reference_id=receipt.evidence_references[0], receipt_id=operation_id,
            tenant_id=tenant_id, project_id=project_id,
            source="KICAD_EXECUTION_WORKER", tool=result.command_identity,
            scope="SCHEMATIC_ERC", result_summary=receipt.safe_diagnostics_summary or "ERC completed",
            occurred_at=receipt.created_at,
            source_fingerprint=result.schematic_content_fingerprint,
            verification_status=EvidenceReferenceStatus.UNVERIFIED,
            receipt_fingerprint=receipt.fingerprint,
            artifact_ref=result.schematic_artifact_ref,
            artifact_fingerprint=operation.schematic_artifact_fingerprint,
            schematic_revision=operation.schematic_revision,
            tool_version=result.kicad_version,
            execution_result=result.status.value,
        )
        try:
            CanonicalOperationReceiptEvidenceResolver(self._execution).resolve(
                SimpleNamespace(tenant_id=tenant_id, project_id=project_id), reference,
            )
        except EvidenceReceiptResolutionError as error:
            raise ProjectCatalogRejected("canonical ERC evidence is unavailable") from error
        return reference

    def _source(self, *, project_id: str, tenant_id: str, requester: str,
                conversation_id: str, turn_id: str, constraints: tuple[str, ...]):
        self._catalog.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        detail = self._catalog.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        turns = [turn for turn in detail.turns if turn.turn_id == turn_id]
        if len(turns) != 1 or turns[0].status is not ConversationTurnStatus.COMPLETED:
            raise ProjectCatalogRejected("completed source turn is required")
        turn = turns[0]
        if turn.route != ApplicationRoute.GREENFIELD_DESIGN.value:
            raise ProjectCatalogRejected("source turn is not a design route")
        routing = self._catalog.routing_context_for_conversation(
            project_id, conversation_id, tenant_id=tenant_id, requester=requester
        )
        request = ApplicationQuestionRequest(
            request_id=turn.request_id, trace_id=turn.trace_id or turn.request_id,
            project_id=project_id, tenant_id=tenant_id, requester=requester,
            question=turn.question, constraints=constraints, external_search=False,
            requested_at=turn.created_at,
        )
        classified = self._application.classify(request, routing_context=routing)
        if classified.route is not ApplicationRoute.GREENFIELD_DESIGN:
            raise ProjectCatalogRejected("current application route is not design")
        return detail, request, routing

    def _proposal(self, *, project_id: str, tenant_id: str, requester: str,
                  command: CircuitPreviewCommand) -> tuple[object, EngineeringObjectFlowResult, ObjectProposal, EvidenceReceiptReference]:
        detail, request, routing = self._source(
            project_id=project_id, tenant_id=tenant_id, requester=requester,
            conversation_id=command.conversation_id, turn_id=command.turn_id,
            constraints=command.constraints,
        )
        current = None
        if command.object_id is not None:
            matches = [item for item in self._catalog.current_circuit_intents(
                project_id, tenant_id=tenant_id, requester=requester
            ) if item.object_id == command.object_id]
            if len(matches) != 1:
                raise ProjectCatalogConflict("Circuit revision base is unavailable")
            current = matches[0]
        reference = self._canonical_reference(
            project_id=project_id, tenant_id=tenant_id, requester=requester,
            operation_id=command.erc_operation_id,
        )
        result = self._application.run_engineering_object_flow(
            request, routing, command.circuit_intent, reference,
            approved_circuit_intent=current,
        )
        if result.route is not ApplicationRoute.GREENFIELD_DESIGN:
            raise ProjectCatalogRejected("prepared route is not design")
        generated = result.circuit_intent
        if current is None:
            # An explicit new identity is not accepted as a revision shortcut.
            circuit = generated
        else:
            changes = {
                name: getattr(command.circuit_intent, name)
                for name in CircuitIntentSpec.model_fields
                if getattr(current, name) != getattr(command.circuit_intent, name)
            }
            dependency = canonical_fingerprint(ContextRequest.create(
                project_id=project_id, target_object_type=ObjectType.CIRCUIT_INTENT,
                required_object_ids=(result.requirement.object_id, result.hardware_architecture.object_id),
                constraints=request.constraints,
            ))
            if current.dependency_fingerprint != dependency:
                changes["dependency_fingerprint"] = dependency
            if not changes:
                raise ProjectCatalogConflict("Circuit revision has no content change")
            provenance = Provenance.create(
                source="APPLICATION_CIRCUIT_INTENT_SPEC",
                source_ref=f"{project_id}:circuit-intent:{request.request_id}",
                created_by=requester, created_at=request.requested_at,
                source_fingerprint=canonical_fingerprint(command.circuit_intent),
                derived_from=(result.requirement.fingerprint, result.hardware_architecture.fingerprint),
            )
            circuit = RevisionManager().create_revision(
                current, changes, provenance=provenance
            )
        proposal_id = _id(
            "proposal", project_id, circuit.object_id, command.idempotency_key,
            canonical_fingerprint(command),
            current.fingerprint if current is not None else "new",
            result.runtime_context_snapshot.fingerprint,
        )
        proposal = ObjectProposal.create(
            proposal_id=proposal_id, project_id=project_id,
            target_object_id=circuit.object_id, target_object_type=circuit.object_type,
            proposed_object=circuit,
            base_revision=None if current is None else current.revision,
            base_fingerprint=None if current is None else current.fingerprint,
            context_fingerprint=result.runtime_context_snapshot.fingerprint,
            proposer=requester,
        )
        return detail, result, proposal, reference

    def preview(self, *, project_id: str, tenant_id: str, requester: str,
                command: CircuitPreviewCommand | PredesignPreviewCommand) -> CircuitProposalCandidate | PredesignCircuitCandidate:
        if isinstance(command, PredesignPreviewCommand):
            return self._preview_predesign(
                project_id=project_id, tenant_id=tenant_id, requester=requester, command=command,
            )
        command = CircuitPreviewCommand.model_validate(copy.deepcopy(command.model_dump(mode="python")), strict=False)
        self._catalog.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        existing = self._catalog.circuit_proposal_by_idempotency(
            project_id, command.idempotency_key, tenant_id=tenant_id, requester=requester
        )
        if existing is not None:
            if existing.request_fingerprint != canonical_fingerprint(command):
                raise ProjectCatalogConflict("Circuit idempotency key was reused")
            self._canonical_reference(
                project_id=project_id, tenant_id=tenant_id, requester=requester,
                operation_id=command.erc_operation_id,
            )
            return existing
        detail, result, proposal, reference = self._proposal(
            project_id=project_id, tenant_id=tenant_id, requester=requester, command=command
        )
        if command.object_id is None and proposal.proposed_object.revision != "r1":
            raise ProjectCatalogConflict("new Circuit revision is invalid")
        if command.object_id is not None and proposal.target_object_id != command.object_id:
            raise ProjectCatalogConflict("Circuit target changed")
        candidate = CircuitProposalCandidate(
            tenant_id=tenant_id, project_id=project_id, proposal=proposal,
            evidence_reference=reference,
            idempotency_key=command.idempotency_key,
            request_fingerprint=canonical_fingerprint(command),
            conversation_id=command.conversation_id, turn_id=command.turn_id,
            object_id=proposal.target_object_id,
            base_revision=proposal.base_revision, base_fingerprint=proposal.base_fingerprint,
            context_fingerprint=proposal.context_fingerprint,
            conversation_fingerprint=detail.fingerprint,
            source_fingerprint=circuit_source_fingerprint(detail, command.turn_id),
            constraints=command.constraints, route=result.route.value,
            created_at=datetime.now(UTC),
        )
        return self._catalog.save_circuit_proposal(candidate, tenant_id=tenant_id, requester=requester)

    def decide(self, *, project_id: str, tenant_id: str, requester: str,
               command: CircuitDecisionCommand | PredesignDecisionCommand) -> CircuitProposalCandidate | PredesignCircuitCandidate | CircuitPublication:
        if isinstance(command, PredesignDecisionCommand):
            return self._decide_predesign(
                project_id=project_id, tenant_id=tenant_id, requester=requester, command=command,
            )
        self._catalog.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        candidate = self._catalog.circuit_proposal(
            project_id, command.proposal_id, tenant_id=tenant_id, requester=requester
        )
        current_reference = self._canonical_reference(
            project_id=project_id, tenant_id=tenant_id, requester=requester,
            operation_id=candidate.evidence_reference.receipt_id,
        )
        if current_reference != candidate.evidence_reference:
            raise ProjectCatalogConflict("reviewed ERC evidence changed")
        if (
            candidate.proposal.fingerprint != command.proposal_fingerprint
            or candidate.base_revision != command.base_revision
            or candidate.base_fingerprint != command.base_fingerprint
            or candidate.context_fingerprint != command.context_fingerprint
            or candidate.evidence_reference.receipt_fingerprint != command.evidence_receipt_fingerprint
        ):
            raise ProjectCatalogConflict("shown Circuit proposal changed")
        if candidate.status != ProposalStatus.CREATED:
            if candidate.decision is None or (
                candidate.decision.reviewer != requester
                or candidate.decision.decision.value != ("APPROVED" if command.decision == "APPROVE" else "REJECTED")
            ):
                raise ProjectCatalogConflict("Circuit proposal was already decided")
            return self._catalog.decide_circuit_proposal(
                project_id, candidate.proposal.proposal_id,
                tenant_id=tenant_id, requester=requester,
                proposal_fingerprint=candidate.proposal.fingerprint,
                decision=candidate.decision, publication=None,
            )
        recheck = CircuitPreviewCommand(
            conversation_id=candidate.conversation_id, turn_id=candidate.turn_id,
            circuit_intent=_spec(candidate.proposal.proposed_object),
            erc_operation_id=candidate.evidence_reference.receipt_id,
            object_id=None if candidate.base_revision is None else candidate.object_id,
            idempotency_key=candidate.idempotency_key,
            constraints=candidate.constraints,
        )
        if canonical_fingerprint(recheck) != candidate.request_fingerprint:
            raise ProjectCatalogConflict("Circuit preview request changed")
        detail, result, proposed, _ = self._proposal(
            project_id=project_id, tenant_id=tenant_id, requester=requester, command=recheck
        )
        if (
            circuit_source_fingerprint(detail, candidate.turn_id) != candidate.source_fingerprint
            or proposed.fingerprint != candidate.proposal.fingerprint
            or proposed.context_fingerprint != candidate.context_fingerprint
            or result.route.value != candidate.route
        ):
            raise ProjectCatalogConflict("Circuit source or proposal is stale")
        decision = circuit_decision_record(candidate, requester, command.decision)
        publication = None
        if command.decision == "APPROVE":
            lifecycle = LifecycleManager()
            active = proposed.proposed_object
            for status, actor in (
                (LifecycleStatus.REVIEW, LifecycleActor.RUNTIME),
                (LifecycleStatus.APPROVED, LifecycleActor.REVIEWER),
                (LifecycleStatus.ACTIVE, LifecycleActor.RUNTIME),
            ):
                active = lifecycle.transition(active, status, actor)
            apply_record = ObjectMutationRecord.create(
                apply_id=_id("apply", proposed.proposal_id, active.revision),
                project_id=project_id, proposal_id=proposed.proposal_id,
                new_revision=active.revision, object_fingerprint=active.fingerprint,
                applied_at=datetime.now(UTC),
            )
            publication = CircuitPublication(
                tenant_id=tenant_id, project_id=project_id, circuit=active,
                proposal=proposed, approval=decision, apply_record=apply_record,
                idempotency_key=candidate.idempotency_key,
                request_fingerprint=candidate.request_fingerprint,
                base_revision=candidate.base_revision,
                base_fingerprint=candidate.base_fingerprint,
            )
        return self._catalog.decide_circuit_proposal(
            project_id, candidate.proposal.proposal_id,
            tenant_id=tenant_id, requester=requester,
            proposal_fingerprint=candidate.proposal.fingerprint,
            decision=decision, publication=publication,
        )

    def _predesign_source(
        self, *, project_id: str, tenant_id: str, requester: str,
        conversation_id: str, turn_id: str,
    ) -> tuple[ConversationDetail, PredesignSourceSnapshot]:
        detail = self._catalog.conversation(
            project_id, conversation_id, tenant_id=tenant_id, requester=requester,
        )
        try:
            return detail, predesign_source_snapshot(detail, turn_id)
        except ValueError as error:
            raise ProjectCatalogRejected("approved design sources are unavailable") from error

    @staticmethod
    def _check_predesign_links(detail: ConversationDetail, spec: CircuitIntentSpec | CircuitIntentObject) -> None:
        requirements = detail.requirement_context
        architecture = detail.architecture_design
        if requirements is None or architecture is None:
            raise ProjectCatalogRejected("approved block sources are unavailable")
        requirement_ids = {
            item.requirement_id
            for item in (*requirements.requirements, *requirements.constraints)
            if item.status.value == "CONFIRMED"
        }
        architecture_ids = {
            *(item.component_id for item in architecture.system_components),
            *(item.interface_id for item in architecture.interfaces),
            *(item.decision_id for item in architecture.decisions),
        }
        if any(
            not block.requirement_refs or not set(block.requirement_refs) <= requirement_ids
            or not block.architecture_refs or not set(block.architecture_refs) <= architecture_ids
            for block in spec.blocks
        ):
            raise ProjectCatalogRejected("predesign block source references are invalid")
        blocks = {item.block_id: item for item in spec.blocks}
        decisions = {item.decision_id: item for item in spec.decisions}
        calculations = {item.calculation_id: item for item in spec.calculations}
        claims = {item.verification_id: item for item in spec.verification_requirements}
        if (len(decisions) != len(spec.decisions) or len(calculations) != len(spec.calculations)
                or any(item.block_ref not in blocks for item in spec.decisions)
                or any(item.owner_ref not in blocks for item in spec.calculations)
                or any(
                    item.owner_ref not in blocks
                    or item.verification_id not in blocks[item.owner_ref].verification_refs
                    for item in spec.verification_requirements
                )
                or any(
                    not set(block.decision_refs) <= decisions.keys()
                    or not set(block.calculation_refs) <= calculations.keys()
                    or not set(block.verification_refs) <= claims.keys()
                    or any(decisions[ref].block_ref != block.block_id for ref in block.decision_refs if ref in decisions)
                    or any(calculations[ref].owner_ref != block.block_id for ref in block.calculation_refs if ref in calculations)
                    or any(claims[ref].owner_ref != block.block_id for ref in block.verification_refs if ref in claims)
                    for block in spec.blocks
                )
                or any(item.decision_id not in blocks[item.block_ref].decision_refs for item in spec.decisions if item.block_ref in blocks)
                or any(item.calculation_id not in blocks[item.owner_ref].calculation_refs for item in spec.calculations if item.owner_ref in blocks)
                or any(
                    not set(item.calculation_refs) <= calculations.keys()
                    or any(calculations[ref].owner_ref != item.block_ref for ref in item.calculation_refs if ref in calculations)
                    for item in spec.decisions
                )):
            raise ProjectCatalogRejected("predesign claim bindings are invalid")

    def _preview_predesign(
        self, *, project_id: str, tenant_id: str, requester: str,
        command: PredesignPreviewCommand,
    ) -> PredesignCircuitCandidate:
        self._catalog.get_project_for_requester(
            project_id, tenant_id=tenant_id, requester=requester,
        )
        request_fingerprint = canonical_fingerprint(command)
        prior = self._catalog.predesign_proposal_by_idempotency(
            project_id, command.idempotency_key, tenant_id=tenant_id, requester=requester,
        )
        detail, source = self._predesign_source(
            project_id=project_id, tenant_id=tenant_id, requester=requester,
            conversation_id=command.conversation_id, turn_id=command.turn_id,
        )
        expected = command.source_expectations
        if (source.requirement_fingerprint != expected.requirement_context_fingerprint
                or source.architecture_id != expected.architecture_id
                or source.architecture_fingerprint != expected.architecture_fingerprint
                or source.hardware_selection_approval_id != expected.hardware_selection_approval_id
                or source.hardware_truth_fingerprint != expected.hardware_truth_fingerprint):
            raise ProjectCatalogConflict("predesign source expectation changed")
        self._check_predesign_links(detail, command.circuit_intent)
        if prior is not None:
            if (prior.request_fingerprint != request_fingerprint
                    or prior.source_snapshot != source):
                raise ProjectCatalogConflict("predesign idempotency or source changed")
            return prior
        current = None
        if command.object_id is not None:
            matches = [item for item in self._catalog.current_circuit_intents(
                project_id, tenant_id=tenant_id, requester=requester,
            ) if item.object_id == command.object_id]
            if (len(matches) != 1 or matches[0].revision != command.base_revision
                    or matches[0].fingerprint != command.base_fingerprint):
                raise ProjectCatalogConflict("predesign base revision changed")
            current = matches[0]
        source_fingerprint = canonical_fingerprint(source)
        spec_fingerprint = canonical_fingerprint(command.circuit_intent)
        blocks = command.circuit_intent.blocks
        claims = command.circuit_intent.verification_requirements
        if not blocks or not claims:
            raise ProjectCatalogRejected("predesign blocks and verification claims are required")
        block_ids = {item.block_id for item in blocks}
        claim_ids = {item.verification_id for item in claims}
        if (len(block_ids) != len(blocks) or len(claim_ids) != len(claims)
                or any(item.owner_ref not in block_ids for item in claims)
                or any(not item.verification_refs or not set(item.verification_refs) <= claim_ids for item in blocks)
                or any(item.status.upper() != "DRAFT" or item.schematic_binding_refs for item in blocks)
                or any(item.status.upper() != "DRAFT" for item in command.circuit_intent.decisions)
                or any(item.status.upper() not in {"PLANNED", "UNKNOWN"} for item in command.circuit_intent.calculations)
                or any(
                    item.status.upper() != "PLANNED"
                    or item.currentness.upper() != "CURRENT"
                    or item.produced_evidence_refs
                    for item in claims
                )):
            raise ProjectCatalogRejected("predesign claim bindings are invalid")
        provenance = Provenance.create(
            source="CATALOG_PREDESIGN_APPROVAL",
            source_ref=f"{project_id}:conversation:{command.conversation_id}:turn:{command.turn_id}",
            created_by=requester, source_fingerprint=source_fingerprint,
            derived_from=(
                source.requirement_fingerprint, source.plan_fingerprint,
                source.architecture_fingerprint, source.hardware_truth_fingerprint,
            ),
        )
        if current is None:
            object_id = _id("circuit-intent", project_id, command.circuit_intent.function, spec_fingerprint)
            if any(item.object_id == object_id for item in self._catalog.current_circuit_intents(
                project_id, tenant_id=tenant_id, requester=requester,
            )):
                raise ProjectCatalogConflict("predesign Circuit already exists")
            circuit = CircuitIntentObject.create(
                object_id=object_id, project_id=project_id,
                **command.circuit_intent.model_dump(mode="python"),
                provenance=provenance, dependency_fingerprint=source_fingerprint,
            )
        else:
            changes = {
                name: getattr(command.circuit_intent, name)
                for name in CircuitIntentSpec.model_fields
                if getattr(current, name) != getattr(command.circuit_intent, name)
            }
            if current.dependency_fingerprint != source_fingerprint:
                changes["dependency_fingerprint"] = source_fingerprint
            if not changes:
                raise ProjectCatalogConflict("Circuit revision has no content change")
            circuit = RevisionManager().create_revision(current, changes, provenance=provenance)
        proposal = ObjectProposal.create(
            proposal_id=_id(
                "proposal", project_id, circuit.object_id, command.idempotency_key,
                request_fingerprint, source_fingerprint,
            ),
            project_id=project_id, target_object_id=circuit.object_id,
            target_object_type=ObjectType.CIRCUIT_INTENT, proposed_object=circuit,
            base_revision=None if current is None else current.revision,
            base_fingerprint=None if current is None else current.fingerprint,
            context_fingerprint=source_fingerprint, proposer=requester,
        )
        candidate = PredesignCircuitCandidate(
            tenant_id=tenant_id, project_id=project_id, proposal=proposal,
            source_snapshot=source, idempotency_key=command.idempotency_key,
            request_fingerprint=request_fingerprint,
            conversation_id=command.conversation_id, turn_id=command.turn_id,
            object_id=circuit.object_id, base_revision=proposal.base_revision,
            base_fingerprint=proposal.base_fingerprint, created_at=datetime.now(UTC),
        )
        return self._catalog.save_predesign_proposal(
            candidate, tenant_id=tenant_id, requester=requester,
        )

    def _decide_predesign(
        self, *, project_id: str, tenant_id: str, requester: str,
        command: PredesignDecisionCommand,
    ) -> PredesignCircuitCandidate | CircuitPublication:
        self._catalog.get_project_for_requester(
            project_id, tenant_id=tenant_id, requester=requester,
        )
        candidate = self._catalog.predesign_proposal(
            project_id, command.proposal_id, tenant_id=tenant_id, requester=requester,
        )
        if (candidate.proposal.fingerprint != command.proposal_fingerprint
                or candidate.proposal.context_fingerprint != command.source_fingerprint
                or candidate.base_revision != command.base_revision
                or candidate.base_fingerprint != command.base_fingerprint):
            raise ProjectCatalogConflict("predesign proposal changed")
        detail, source = self._predesign_source(
            project_id=project_id, tenant_id=tenant_id, requester=requester,
            conversation_id=candidate.conversation_id, turn_id=candidate.turn_id,
        )
        if source != candidate.source_snapshot:
            raise ProjectCatalogConflict("predesign source changed")
        proposed = candidate.proposal.proposed_object
        if not isinstance(proposed, CircuitIntentObject):
            raise ProjectCatalogRejected("predesign Circuit proposal is invalid")
        self._check_predesign_links(detail, proposed)
        if candidate.status is not ProposalStatus.CREATED:
            if (candidate.decision is None or candidate.decision.reviewer != requester
                    or candidate.decision.decision.value != (
                        "APPROVED" if command.decision == "APPROVE" else "REJECTED"
                    )):
                raise ProjectCatalogConflict("predesign proposal already decided")
            return self._catalog.decide_predesign_proposal(
                project_id, command.proposal_id, tenant_id=tenant_id, requester=requester,
                proposal_fingerprint=command.proposal_fingerprint,
                decision=candidate.decision, publication=None,
            )
        decision = circuit_decision_record(candidate, requester, command.decision)
        publication = None
        if command.decision == "APPROVE":
            active = candidate.proposal.proposed_object
            lifecycle = LifecycleManager()
            for status, actor in (
                (LifecycleStatus.REVIEW, LifecycleActor.RUNTIME),
                (LifecycleStatus.APPROVED, LifecycleActor.REVIEWER),
                (LifecycleStatus.ACTIVE, LifecycleActor.RUNTIME),
            ):
                active = lifecycle.transition(active, status, actor)
            apply_record = ObjectMutationRecord.create(
                apply_id=_id("apply", candidate.proposal.proposal_id, active.revision),
                project_id=project_id, proposal_id=candidate.proposal.proposal_id,
                new_revision=active.revision, object_fingerprint=active.fingerprint,
                applied_at=datetime.now(UTC),
            )
            publication = CircuitPublication(
                tenant_id=tenant_id, project_id=project_id, circuit=active,
                proposal=candidate.proposal, approval=decision, apply_record=apply_record,
                idempotency_key=candidate.idempotency_key,
                request_fingerprint=candidate.request_fingerprint,
                base_revision=candidate.base_revision,
                base_fingerprint=candidate.base_fingerprint,
            )
        return self._catalog.decide_predesign_proposal(
            project_id, command.proposal_id, tenant_id=tenant_id, requester=requester,
            proposal_fingerprint=command.proposal_fingerprint,
            decision=decision, publication=publication,
        )
