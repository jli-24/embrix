from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Mapping

from embedded_copilot.correlation import CorrelationContext
from embedded_copilot.engineering_copilot import (
    HardwareSelectionState,
    SchematicPlan,
    SchematicProductionRequest,
    SchematicProposal,
    SchematicProposalDecision,
    SchematicProposalDecisionStatus,
    decide_schematic_proposal,
    serialize_kicad_schematic,
)
from embedded_copilot.engineering_workspace_apply import (
    ApplyOperation,
    ChangeIntent,
    ChangeType,
    WorkspaceApplyService,
)
from embedded_copilot.engineering_workspace_apply.contracts import content_fingerprint
from embedded_copilot.execution_worker import (
    ExecutionOperationService,
    OperationRequest,
    OperationStatus,
    OperationType,
    SchematicErcOperation,
    SchematicWriteOperation,
)


def _id(prefix: str, *parts: str) -> str:
    return f"{prefix}-" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def _safe_schematic_preview(content: str) -> str:
    """Redact external URLs and host paths from the review-only preview."""

    del content
    # The exact artifact remains in ``after_content`` for the controlled
    # WorkspaceApply path.  Keep the review preview intentionally metadata-only
    # so provider URLs, escaped paths, and library text cannot cross the
    # preview contract even when KiCad emits unusual quoting or line wrapping.
    return "Approved deterministic KiCad schematic artifact; content is held by the controlled apply receipt."


@dataclass(frozen=True, slots=True)
class _ApprovedProposal:
    status: str
    proposal_id: str
    proposal_fingerprint: str
    tenant_id: str
    project_id: str
    requester_id: str


class SchematicExecutionBridge:
    """Bind one approved structured proposal to the existing Worker and Workspace boundaries."""

    def __init__(
        self,
        *,
        workspace: WorkspaceApplyService,
        execution: ExecutionOperationService,
        clock: Callable[[], datetime] | None = None,
        symbol_binding_validator: Callable[..., None] | None = None,
        symbol_library_provider: Callable[..., Mapping[str, str]] | None = None,
        private_definition_validator: Callable[..., tuple[tuple[str, str, str], ...]] | None = None,
    ) -> None:
        if not isinstance(workspace, WorkspaceApplyService) or not isinstance(execution, ExecutionOperationService):
            raise TypeError("canonical execution dependencies are required")
        self._workspace = workspace
        self._execution = execution
        self._clock = clock or (lambda: datetime.now(UTC))
        self._private_definition_validator = private_definition_validator
        self._symbol_binding_validator = symbol_binding_validator
        inferred_provider = getattr(symbol_binding_validator, "resolve_definitions", None)
        self._symbol_library_provider = symbol_library_provider or (
            inferred_provider if callable(inferred_provider) else None
        )

    def approve_and_enqueue(
        self,
        *,
        production_request: SchematicProductionRequest,
        proposal: SchematicProposal,
        decision: SchematicProposalDecision,
        tenant_id: str = "default-tenant",
        expected_existing_fingerprint: str | None = None,
        approved_schematic_plan: SchematicPlan | None = None,
        hardware_selection: HardwareSelectionState | None = None,
    ) -> OperationRequest:
        prepared = self.prepare_approved(
            production_request=production_request, proposal=proposal, decision=decision,
            tenant_id=tenant_id, expected_existing_fingerprint=expected_existing_fingerprint,
            approved_schematic_plan=approved_schematic_plan, hardware_selection=hardware_selection,
        )
        return self.enqueue_prepared(operation_request=prepared, decision=decision)

    def prepare_approved(
        self,
        *,
        production_request: SchematicProductionRequest,
        proposal: SchematicProposal,
        decision: SchematicProposalDecision,
        tenant_id: str = "default-tenant",
        expected_existing_fingerprint: str | None = None,
        approved_schematic_plan: SchematicPlan | None = None,
        hardware_selection: HardwareSelectionState | None = None,
    ) -> OperationRequest:
        request = SchematicProductionRequest.model_validate(production_request.model_dump(mode="python"), strict=False)
        checked = SchematicProposal.model_validate(proposal.model_dump(mode="python"), strict=False)
        approved = SchematicProposalDecision.model_validate(decision.model_dump(mode="python"), strict=False)
        if (
            checked.request_id != request.request_id
            or checked.project_id != request.project_id
            or checked.conversation_id != request.conversation_id
            or checked.design_truth_fingerprint != request.design_truth_fingerprint
            or checked.schematic_plan_id != request.schematic_plan_id
            or checked.schematic_plan_fingerprint != request.schematic_plan_fingerprint
            or approved.proposal_id != checked.proposal_id
            or approved.proposal_fingerprint != checked.fingerprint
            or approved.decision is not SchematicProposalDecisionStatus.APPROVED
            or approved.reviewer is None
            or approved.decided_at is None
        ):
            raise ValueError("schematic proposal approval binding mismatch")
        expected_decision = decide_schematic_proposal(
            checked,
            decision=SchematicProposalDecisionStatus.APPROVED,
            reviewer=approved.reviewer,
            decided_at=approved.decided_at,
        )
        if (
            approved.decision_id != expected_decision.decision_id
            or approved.fingerprint != expected_decision.fingerprint
            or approved.reason != expected_decision.reason
        ):
            raise ValueError("schematic proposal decision integrity mismatch")
        if self._symbol_binding_validator is not None:
            self._symbol_binding_validator(
                tenant_id=tenant_id,
                project_id=request.project_id,
                target_id=request.workspace_identity,
                bindings=tuple(item.symbol_binding for item in checked.components),
            )
        private = any(item.symbol_binding == "SHTC3_EP:SHTC3_EP" for item in checked.components) or (
            approved_schematic_plan is not None and any(
                item.symbol == "SHTC3_EP:SHTC3_EP" for item in approved_schematic_plan.component_instances
            )
        )
        if private and not callable(self._private_definition_validator):
            raise ValueError("private schematic definition validator is unavailable")
        symbol_definitions = None
        if self._symbol_library_provider is not None:
            try:
                symbol_definitions = self._symbol_library_provider(
                    tenant_id=tenant_id,
                    project_id=request.project_id,
                    target_id=request.workspace_identity,
                    bindings=tuple(item.symbol_binding for item in checked.components),
                )
            except (OSError, TypeError, ValueError, UnicodeError) as error:
                raise ValueError("authoritative KiCad symbol definitions are unavailable") from error
            if not isinstance(symbol_definitions, Mapping):
                raise ValueError("authoritative KiCad symbol definitions are invalid")
            symbol_definitions = dict(symbol_definitions)
            if any(type(key) is not str or type(value) is not str for key, value in symbol_definitions.items()):
                raise ValueError("authoritative KiCad symbol definitions are invalid")
        binding_token = None

        def check_private_definitions():
            nonlocal binding_token
            if not private:
                return
            token = self._private_definition_validator(
                production_request=request, approved_schematic_plan=approved_schematic_plan,
                hardware_selection=hardware_selection, tenant_id=tenant_id,
                symbol_definitions=symbol_definitions, expected_binding=binding_token,
            )
            if (type(token) is not tuple or not 0 < len(token) <= 128
                or any(type(item) is not tuple or len(item) != 3
                    or any(type(value) is not str or not value or len(value) > 256 for value in item)
                    for item in token)
                or (binding_token is not None and token != binding_token)):
                raise ValueError("private schematic definition binding changed")
            binding_token = token

        check_private_definitions()
        content = serialize_kicad_schematic(
            checked, symbol_definitions=symbol_definitions,
            approved_schematic_plan=approved_schematic_plan,
            hardware_selection=hardware_selection,
        )
        content_fp = content_fingerprint(content)
        snapshot = self._workspace.snapshot(tenant_id=tenant_id, project_id=request.project_id, target_id=request.workspace_identity)
        check_private_definitions()
        existing_file = next(
            (item for item in snapshot.files if item.relative_file_reference == request.target_relative_path),
            None,
        )
        if existing_file is None:
            if expected_existing_fingerprint is not None:
                raise ValueError("expected existing schematic is unavailable")
            change_type = ChangeType.CREATE_FILE
            before_fingerprint = "sha256:" + "0" * 64
        else:
            if expected_existing_fingerprint is None:
                raise ValueError("existing schematic requires an explicit fingerprint")
            if expected_existing_fingerprint != existing_file.content_fingerprint:
                raise ValueError("existing schematic fingerprint mismatch")
            change_type = ChangeType.MODIFY_FILE
            before_fingerprint = existing_file.content_fingerprint
        allow_explicit_revision = change_type is ChangeType.MODIFY_FILE
        for prior in self._execution.list_operations(tenant_id=tenant_id, project_id=request.project_id, limit=100):
            if not isinstance(prior, SchematicWriteOperation):
                continue
            if (
                (prior.schematic_request_id == request.request_id or prior.proposal_id == checked.proposal_id)
                and (prior.content_fingerprint != content_fp or prior.proposal_fingerprint != checked.fingerprint)
                and not allow_explicit_revision
            ):
                raise ValueError("schematic operation identity conflict")
        operation_id = _id(
            "kicad-schematic-write",
            tenant_id,
            request.project_id,
            checked.fingerprint,
            request.design_truth_fingerprint,
            before_fingerprint,
            content_fp,
        )
        correlation = None
        if request.source_request_id is not None or request.trace_id is not None:
            if request.source_request_id is None or request.trace_id is None:
                raise ValueError("schematic production correlation is incomplete")
            correlation = CorrelationContext(
                request_id=request.source_request_id,
                trace_id=request.trace_id,
                project_id=request.project_id,
                task_id=request.implementation_task_id,
                operation_id=operation_id,
            )
        existing = self._execution.get_operation(tenant_id=tenant_id, project_id=request.project_id, operation_id=operation_id)
        if existing is not None:
            if not isinstance(existing, SchematicWriteOperation):
                raise ValueError("schematic operation identity conflict")
            check_private_definitions()
            return OperationRequest(operation=existing)
        proposal_view = _ApprovedProposal(
            status="APPROVED",
            proposal_id=checked.proposal_id,
            proposal_fingerprint=checked.fingerprint,
            tenant_id=tenant_id,
            project_id=request.project_id,
            requester_id=request.requester,
        )
        intent = ChangeIntent(
            relative_file_reference=request.target_relative_path,
            change_type=change_type,
            before_fingerprint=before_fingerprint,
            after_fingerprint=content_fp,
            safe_preview=_safe_schematic_preview(content.decode("utf-8")),
            reason="Explicitly approved deterministic KiCad schematic production.",
            source_proposal_reference=checked.proposal_id,
            after_content=content.decode("utf-8"),
        )
        changeset = self._workspace.create_changeset(
            proposal=proposal_view,
            snapshot=snapshot,
            intents=(intent,),
            operation=ApplyOperation.WORKSPACE_APPLY,
        )
        check_private_definitions()
        workspace_approval = self._workspace.issue_apply_approval(
            changeset=changeset,
            reviewer_id=approved.reviewer,
            nonce=_id("workspace-approval-nonce", approved.fingerprint, changeset.fingerprint),
        )
        now = self._clock()
        operation = SchematicWriteOperation(
            operation_id=operation_id,
            tenant_id=tenant_id,
            project_id=request.project_id,
            requester_id=request.requester,
            target_id=changeset.target.target_id,
            target_fingerprint=changeset.target_fingerprint,
            workspace_snapshot_id=_id("workspace-snapshot", snapshot.fingerprint),
            workspace_fingerprint=changeset.base_project_fingerprint,
            proposal_id=checked.proposal_id,
            proposal_fingerprint=checked.fingerprint,
            operation_type=OperationType.KICAD_SCHEMATIC_WRITE,
            created_at=now,
            expires_at=changeset.expires_at,
            nonce=_id("schematic-operation-nonce", approved.fingerprint, checked.fingerprint),
            conversation_id=request.conversation_id,
            implementation_id=request.implementation_id,
            implementation_task_id=request.implementation_task_id,
            authorization_id=request.authorization_id,
            authorization_request_id=request.authorization_request_id,
            proposal_decision_id=approved.decision_id,
            proposal_decision_fingerprint=approved.fingerprint,
            schematic_request_id=request.request_id,
            schematic_request_fingerprint=request.fingerprint,
            design_truth_fingerprint=request.design_truth_fingerprint,
            target_relative_path=request.target_relative_path,
            content=content.decode("utf-8"),
            content_fingerprint=content_fp,
            changeset=changeset,
            workspace_approval=workspace_approval,
            correlation=correlation,
        )
        operation_request = OperationRequest(operation=operation)
        check_private_definitions()
        self._execution.create_operation(operation_request)
        return operation_request

    def enqueue_prepared(
        self, *, operation_request: OperationRequest, decision: SchematicProposalDecision,
    ) -> OperationRequest:
        """Publish only after the Catalog has durably saved its queue binding."""
        checked_request = OperationRequest.model_validate(operation_request.model_dump(mode="python"), strict=False)
        operation = checked_request.operation
        approved = SchematicProposalDecision.model_validate(decision.model_dump(mode="python"), strict=False)
        if (
            not isinstance(operation, SchematicWriteOperation)
            or approved.decision is not SchematicProposalDecisionStatus.APPROVED
            or approved.reviewer is None
            or approved.proposal_id != operation.proposal_id
            or approved.proposal_fingerprint != operation.proposal_fingerprint
            or approved.decision_id != operation.proposal_decision_id
            or approved.fingerprint != operation.proposal_decision_fingerprint
        ):
            raise ValueError("prepared schematic approval binding mismatch")
        stored = self._execution.get_operation(
            tenant_id=operation.tenant_id, project_id=operation.project_id, operation_id=operation.operation_id,
        )
        status = self._execution.get_status(
            tenant_id=operation.tenant_id, project_id=operation.project_id, operation_id=operation.operation_id,
        )
        if stored != operation or status is None:
            raise ValueError("prepared schematic operation is unavailable")
        if status.status is not OperationStatus.WAIT_APPROVAL:
            return checked_request
        grant = self._execution.issue_approval(
            operation=checked_request,
            reviewer_id=approved.reviewer,
            nonce=_id("operation-approval-nonce", approved.fingerprint, operation.fingerprint),
            expires_at=operation.changeset.expires_at,
        )
        self._execution.enqueue(operation=checked_request, approval_id=grant.grant_id)
        return checked_request

    def enqueue_erc(
        self,
        *,
        write_operation: SchematicWriteOperation,
        receipt_fingerprint: str,
        reviewer_id: str,
    ) -> OperationRequest:
        """Create ERC awaiting its own explicit approval; never grant or enqueue."""
        source = SchematicWriteOperation.model_validate(write_operation.model_dump(mode="python"), strict=False)
        if not isinstance(receipt_fingerprint, str) or not receipt_fingerprint.startswith("sha256:"):
            raise ValueError("schematic receipt fingerprint is invalid")
        if source.requester_id != reviewer_id:
            raise ValueError("schematic ERC reviewer binding mismatch")
        receipt = self._execution.get_receipt(
            tenant_id=source.tenant_id,
            project_id=source.project_id,
            operation_id=source.operation_id,
        )
        if (
            receipt is None
            or receipt.fingerprint != receipt_fingerprint
            or receipt.operation_type is not OperationType.KICAD_SCHEMATIC_WRITE
            or receipt.status.value != "SUCCEEDED"
            or receipt.workspace_apply_receipt_fingerprint is None
            or not receipt.workspace_mutations
        ):
            raise ValueError("schematic ERC requires a successful write receipt")
        operation_id = _id("kicad-schematic-erc", source.operation_id, receipt_fingerprint, source.content_fingerprint)
        correlation = None
        if source.correlation is not None:
            correlation = CorrelationContext(
                **{
                    **source.correlation.model_dump(mode="python"),
                    "operation_id": operation_id,
                    "approval_id": None,
                }
            )
        existing = self._execution.get_operation(tenant_id=source.tenant_id, project_id=source.project_id, operation_id=operation_id)
        if existing is not None:
            if not isinstance(existing, SchematicErcOperation):
                raise ValueError("schematic ERC operation identity conflict")
            return OperationRequest(operation=existing)
        now = self._clock()
        operation = SchematicErcOperation(
            operation_id=operation_id,
            tenant_id=source.tenant_id,
            project_id=source.project_id,
            requester_id=reviewer_id,
            target_id=source.target_id,
            target_fingerprint=source.target_fingerprint,
            workspace_snapshot_id=source.workspace_snapshot_id,
            workspace_fingerprint=source.workspace_fingerprint,
            proposal_id=source.proposal_id,
            proposal_fingerprint=source.proposal_fingerprint,
            operation_type=OperationType.KICAD_SCHEMATIC_ERC,
            created_at=now,
            expires_at=max(source.expires_at, now + timedelta(minutes=15)),
            nonce=_id("schematic-erc-nonce", source.operation_id, receipt_fingerprint),
            conversation_id=source.conversation_id,
            schematic_artifact_ref=source.target_relative_path,
            schematic_artifact_fingerprint=source.content_fingerprint,
            source_operation_id=source.operation_id,
            source_operation_receipt_fingerprint=receipt_fingerprint,
            target_relative_path=source.target_relative_path,
            correlation=correlation,
        )
        request = OperationRequest(operation=operation)
        self._execution.create_operation(request)
        return request


__all__ = ("SchematicExecutionBridge",)
