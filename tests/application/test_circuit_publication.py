from __future__ import annotations

from types import SimpleNamespace

import pytest

from embedded_copilot.application.circuit_publication import (
    CircuitDecisionCommand,
    CircuitPreviewCommand,
    CircuitPublicationService,
)
from embedded_copilot.application.engineering_runtime_integration import (
    EngineeringObjectIntegrationService,
)
from embedded_copilot.engineering_runtime import EngineeringObjectRuntime
from embedded_copilot.project_catalog.exceptions import (
    ProjectCatalogConflict,
    ProjectCatalogRejected,
)
from embedded_copilot.project_catalog.models import (
    ConversationTurn,
    ConversationTurnStatus,
)
from tests.application.fixtures import NOW
from tests.application.test_engineering_runtime_integration import (
    FP,
    FixtureQualifiedReceiptReferencePort,
    _runtime,
)
from tests.application.test_operation_receipt_evidence_resolver import (
    _source as canonical_source,
)


def _execution():
    source, reference = canonical_source()
    receipts = {}
    for (_, _, operation_id), receipt in source.receipts.items():
        receipts[("default-tenant", "project-1", operation_id)] = receipt.model_copy(
            update={"tenant_id": "default-tenant", "project_id": "project-1"}
        )
    operations = {}
    for (_, _, operation_id), operation in source.operations.items():
        operations[("default-tenant", "project-1", operation_id)] = (
            operation.model_copy(update={
                "tenant_id": "default-tenant", "project_id": "project-1",
                "requester_id": "engineer-1", "conversation_id": "conversation-1",
            }) if hasattr(operation, "model_copy") else SimpleNamespace(
                **{**vars(operation), "tenant_id": "default-tenant", "project_id": "project-1"}
            )
        )

    class Execution:
        def get_operation(self, *, tenant_id, project_id, operation_id):
            return operations.get((tenant_id, project_id, operation_id))

        def get_receipt(self, *, tenant_id, project_id, operation_id):
            return receipts.get((tenant_id, project_id, operation_id))

        def get_status(self, *, tenant_id, project_id, operation_id):
            receipt = self.get_receipt(tenant_id=tenant_id, project_id=project_id, operation_id=operation_id)
            return None if receipt is None else SimpleNamespace(
                operation_id=operation_id, tenant_id=tenant_id, project_id=project_id,
                operation_type=receipt.operation_type,
                status=receipt.status, receipt_fingerprint=receipt.fingerprint,
            )

    return Execution(), reference.receipt_id


class _Catalog:
    def __init__(self, detail: object, routing: object) -> None:
        self.detail = detail
        self.routing = routing
        self.candidate = None
        self.publication = None

    def get_project_for_requester(self, project_id: str, *, tenant_id: str, requester: str):
        assert (project_id, tenant_id, requester) == ("project-1", "default-tenant", "engineer-1")
        return SimpleNamespace(project_id=project_id)

    def conversation(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str):
        self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        assert conversation_id == "conversation-1"
        return self.detail

    def routing_context_for_conversation(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str):
        self.conversation(project_id, conversation_id, tenant_id=tenant_id, requester=requester)
        return self.routing

    def current_circuit_intents(self, project_id: str, *, tenant_id: str, requester: str):
        self.get_project_for_requester(project_id, tenant_id=tenant_id, requester=requester)
        return () if self.publication is None else (self.publication.circuit,)

    def circuit_proposal_by_idempotency(self, project_id: str, key: str, *, tenant_id: str, requester: str):
        return self.candidate if self.candidate is not None and self.candidate.idempotency_key == key else None

    def save_circuit_proposal(self, candidate, *, tenant_id: str, requester: str):
        self.candidate = candidate
        return candidate

    def circuit_proposal(self, project_id: str, proposal_id: str, *, tenant_id: str, requester: str):
        assert self.candidate.proposal.proposal_id == proposal_id
        return self.candidate

    def decide_circuit_proposal(self, project_id: str, proposal_id: str, *, tenant_id: str, requester: str, proposal_fingerprint: str, decision, publication):
        assert self.candidate.proposal.fingerprint == proposal_fingerprint
        self.publication = publication
        return publication

    def validate_current_schematic_erc_source(self, *, operation, receipt, source_receipt, tenant_id, requester):
        self.get_project_for_requester(operation.project_id, tenant_id=tenant_id, requester=requester)
        assert source_receipt.operation_id == operation.source_operation_id
        assert receipt.operation_id == operation.operation_id


def _review_service(catalog: _Catalog, execution: object):
    application, _, request, routing, spec, receipt_port, context_provider = _runtime(
        receipt_port=FixtureQualifiedReceiptReferencePort()
    )
    turn = ConversationTurn(
        turn_id="turn-1", request_id=request.request_id, trace_id=request.trace_id,
        route="GREENFIELD_DESIGN", status=ConversationTurnStatus.COMPLETED,
        question=request.question, answer_text="Design analysis",
        created_at=NOW, completed_at=NOW,
    )
    catalog.detail = SimpleNamespace(
        project_id="project-1", conversation_id="conversation-1",
        fingerprint=FP, turns=(turn,), requirement_context=routing.requirement_context,
        architecture_design=None, design_truth=routing.design_truth,
    )
    catalog.routing = routing
    return CircuitPublicationService(
        catalog=catalog, application=_Application(application, receipt_port, context_provider),
        execution=execution,
    ), spec


@pytest.mark.parametrize("change", ["modify", "delete"])
@pytest.mark.parametrize("stage", ["preview", "decision"])
def test_circuit_rechecks_current_source_bytes_at_each_review_boundary(
    tmp_path, change: str, stage: str,
) -> None:
    source = tmp_path / "current.kicad_sch"
    source.write_bytes(b"current")
    catalog = _Catalog(None, None)
    original_check = catalog.validate_current_schematic_erc_source

    def check(**kwargs):
        original_check(**kwargs)
        if not source.is_file() or source.read_bytes() != b"current":
            raise ProjectCatalogRejected("current ERC source bytes changed")

    catalog.validate_current_schematic_erc_source = check
    execution, erc_operation_id = _execution()
    service, spec = _review_service(catalog, execution)
    command = CircuitPreviewCommand(
        conversation_id="conversation-1", turn_id="turn-1", circuit_intent=spec,
        erc_operation_id=erc_operation_id, idempotency_key=f"source-{change}-{stage}",
    )
    candidate = None
    if stage == "decision":
        candidate = service.preview(
            project_id="project-1", tenant_id="default-tenant", requester="engineer-1",
            command=command,
        )
    if change == "modify":
        source.write_bytes(b"changed")
    else:
        source.unlink()
    with pytest.raises(ProjectCatalogRejected, match="source bytes changed"):
        if stage == "preview":
            service.preview(
                project_id="project-1", tenant_id="default-tenant", requester="engineer-1",
                command=command,
            )
        else:
            assert candidate is not None
            service.decide(
                project_id="project-1", tenant_id="default-tenant", requester="engineer-1",
                command=CircuitDecisionCommand(
                    proposal_id=candidate.proposal.proposal_id,
                    proposal_fingerprint=candidate.proposal.fingerprint,
                    context_fingerprint=candidate.context_fingerprint,
                    evidence_receipt_fingerprint=candidate.evidence_reference.receipt_fingerprint,
                    decision="APPROVE",
                ),
            )


@pytest.mark.parametrize("invalid", ["non_erc", "failed", "missing_receipt", "cross_project", "non_owner"])
def test_circuit_preview_rejects_unqualified_erc_operation(invalid: str) -> None:
    catalog = _Catalog(None, None)
    execution, erc_operation_id = _execution()
    service, spec = _review_service(catalog, execution)
    if invalid == "non_erc":
        execution.get_operation = lambda **kwargs: object()
    elif invalid == "failed":
        original = execution.get_status
        execution.get_status = lambda **kwargs: SimpleNamespace(**{
            **vars(original(**kwargs)), "status": "FAILED",
        })
    elif invalid == "missing_receipt":
        execution.get_receipt = lambda **kwargs: None
    if invalid in {"cross_project", "non_owner"}:
        with pytest.raises(ProjectCatalogRejected):
            service._canonical_reference(
                project_id="project-other" if invalid == "cross_project" else "project-1",
                tenant_id="default-tenant",
                requester="other-user" if invalid == "non_owner" else "engineer-1",
                operation_id=erc_operation_id,
            )
    else:
        with pytest.raises(ProjectCatalogRejected):
            service.preview(
                project_id="project-1", tenant_id="default-tenant", requester="engineer-1",
                command=CircuitPreviewCommand(
                    conversation_id="conversation-1", turn_id="turn-1",
                    circuit_intent=spec, erc_operation_id=erc_operation_id,
                    idempotency_key=f"invalid-{invalid}",
                ),
            )


class _Application:
    def __init__(self, application: object, receipt_port: object, context_provider: object) -> None:
        self.application = application
        self.receipt_port = receipt_port
        self.context_provider = context_provider

    def classify(self, request, *, routing_context):
        return self.application.classify(request, routing_context=routing_context)

    def run_engineering_object_flow(self, request, routing, spec, evidence, *, approved_circuit_intent):
        integration = EngineeringObjectIntegrationService(
            EngineeringObjectRuntime(project_id=request.project_id),
            evidence_port=self.receipt_port, context_provider=self.context_provider,
        )
        return self.application.run_engineering_object_flow(
            request, routing, spec, evidence, integration=integration,
            approved_circuit_intent=approved_circuit_intent,
        )


def test_preview_then_separate_decision_publishes_circuit() -> None:
    application, _, request, routing, spec, receipt_port, context_provider = _runtime(
        receipt_port=FixtureQualifiedReceiptReferencePort()
    )
    turn = ConversationTurn(
        turn_id="turn-1", request_id=request.request_id, trace_id=request.trace_id,
        route="GREENFIELD_DESIGN", status=ConversationTurnStatus.COMPLETED,
        question=request.question, answer_text="Design analysis",
        created_at=NOW, completed_at=NOW,
    )
    detail = SimpleNamespace(
        project_id="project-1", conversation_id="conversation-1",
        fingerprint=FP, turns=(turn,), requirement_context=routing.requirement_context,
        architecture_design=None, design_truth=routing.design_truth,
    )
    catalog = _Catalog(detail, routing)
    execution, erc_operation_id = _execution()
    service = CircuitPublicationService(
        catalog=catalog, application=_Application(application, receipt_port, context_provider),
        execution=execution,
    )
    candidate = service.preview(
        project_id="project-1", tenant_id="default-tenant", requester="engineer-1",
        command=CircuitPreviewCommand(
            conversation_id="conversation-1", turn_id="turn-1", circuit_intent=spec,
            erc_operation_id=erc_operation_id, idempotency_key="circuit-request-1",
        ),
    )
    assert catalog.publication is None
    # An unrelated later conversation turn must not invalidate the selected
    # source turn and its reviewed engineering context.
    detail.turns = (turn, turn.model_copy(update={
        "turn_id": "turn-unrelated", "request_id": "request-unrelated",
        "route": "KNOWLEDGE", "question": "Explain an unrelated term",
    }))
    detail.fingerprint = "sha256:" + "1" * 64
    publication = service.decide(
        project_id="project-1", tenant_id="default-tenant", requester="engineer-1",
        command=CircuitDecisionCommand(
            proposal_id=candidate.proposal.proposal_id,
            proposal_fingerprint=candidate.proposal.fingerprint,
            context_fingerprint=candidate.context_fingerprint,
            evidence_receipt_fingerprint=candidate.evidence_reference.receipt_fingerprint, decision="APPROVE",
        ),
    )
    assert publication.circuit.revision == "r1"
    assert catalog.publication == publication

    revised_spec = spec.model_copy(update={"function": spec.function + " with revised current budget"})
    revised = service.preview(
        project_id="project-1", tenant_id="default-tenant", requester="engineer-1",
        command=CircuitPreviewCommand(
            conversation_id="conversation-1", turn_id="turn-1",
            circuit_intent=revised_spec, object_id=publication.circuit.object_id,
            erc_operation_id=erc_operation_id, idempotency_key="circuit-request-2",
        ),
    )
    assert revised.proposal.proposed_object.revision == "r2"
    detail.turns = (turn.model_copy(update={"question": turn.question + " with changed voltage"}), detail.turns[1])
    with pytest.raises((ProjectCatalogConflict, ProjectCatalogRejected)):
        service.decide(
            project_id="project-1", tenant_id="default-tenant", requester="engineer-1",
            command=CircuitDecisionCommand(
                proposal_id=revised.proposal.proposal_id,
                proposal_fingerprint=revised.proposal.fingerprint,
                base_revision=publication.circuit.revision,
                base_fingerprint=publication.circuit.fingerprint,
                context_fingerprint=revised.context_fingerprint,
                evidence_receipt_fingerprint=revised.evidence_reference.receipt_fingerprint, decision="APPROVE",
            ),
        )
    detail.turns = (turn, detail.turns[1])
    second_publication = service.decide(
        project_id="project-1", tenant_id="default-tenant", requester="engineer-1",
        command=CircuitDecisionCommand(
            proposal_id=revised.proposal.proposal_id,
            proposal_fingerprint=revised.proposal.fingerprint,
            base_revision=publication.circuit.revision,
            base_fingerprint=publication.circuit.fingerprint,
            context_fingerprint=revised.context_fingerprint,
            evidence_receipt_fingerprint=revised.evidence_reference.receipt_fingerprint, decision="APPROVE",
        ),
    )
    assert second_publication.circuit.revision == "r2"
