from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from embedded_copilot.desktop_workspace_projection import (
    DesktopWorkspaceProjectionService,
)
from embedded_copilot.engineering_copilot import SchematicProductionStatus
from embedded_copilot.engineering_runtime import (
    CalculationRecord,
    CircuitIntentObject,
    LifecycleStatus,
    Provenance,
)
from embedded_copilot.engineering_runtime.evidence import EvidenceReceiptReference
from embedded_copilot.engineering_runtime.objects.models import ObjectType
from embedded_copilot.engineering_runtime.proposals import (
    ApprovalRecord,
    ObjectMutationRecord,
    ObjectProposal,
    ProposalStatus,
)
from embedded_copilot.execution_worker import SchematicErcOperation
from embedded_copilot.project_catalog import (
    ProjectCatalogConflict,
    ProjectCatalogForbidden,
    ProjectCatalogRuntime,
    ProjectCreationRequest,
    ProjectType,
)
from embedded_copilot.project_catalog.models import (
    CircuitProposalCandidate,
    CircuitPublication,
    ConversationDetail,
    ConversationTurn,
    ConversationTurnStatus,
    circuit_source_fingerprint,
)
from tests.execution_worker.test_schematic_erc import _operation as _erc_operation

NOW = datetime(2026, 9, 29, tzinfo=UTC)
FP = "sha256:" + "1" * 64
TENANT = "tenant-1"
OWNER = "engineer-1"


class Clock:
    def now(self) -> datetime:
        return NOW


def _runtime(tmp_path: Path) -> ProjectCatalogRuntime:
    root = tmp_path / "projects"
    root.mkdir(exist_ok=True)
    return ProjectCatalogRuntime(project_root=root, state_database=tmp_path / "catalog.sqlite3", clock=Clock(), initialize_async=False)


def _seed(tmp_path: Path) -> tuple[ProjectCatalogRuntime, str, ConversationDetail]:
    catalog = _runtime(tmp_path)
    preview = catalog.preview(ProjectCreationRequest(name="Circuit Fixture", relative_path="circuit-fixture", project_type=ProjectType.STM32), tenant_id=TENANT, requester=OWNER)
    project = catalog.create(preview.preview_id, preview.fingerprint, confirmed_at=NOW, tenant_id=TENANT, requester=OWNER)
    session = catalog.create_conversation(project.project_id, tenant_id=TENANT, requester=OWNER)
    detail = session.conversation
    turn = ConversationTurn(turn_id="turn-design", request_id="request-design", route="GREENFIELD_DESIGN", status=ConversationTurnStatus.COMPLETED, question="Circuit intent", answer_text="Design analysis", created_at=NOW, completed_at=NOW)
    updated = detail.model_copy(update={"turns": (turn,), "fingerprint": "sha256:" + "0" * 64})
    catalog._repository.save_conversation(ConversationDetail.model_validate(updated.model_dump(mode="python")))
    return catalog, project.project_id, catalog.conversation(project.project_id, detail.conversation_id, tenant_id=TENANT, requester=OWNER)


def _candidate(detail: ConversationDetail, *, object_id: str = "circuit-1", revision: str = "r1", base_revision: str | None = None, base_fingerprint: str | None = None, key: str = "key-1", proposal_id: str = "proposal-1") -> CircuitProposalCandidate:
    project_id = detail.project_id
    circuit = CircuitIntentObject.create(object_id=object_id, project_id=project_id, revision=revision, function="Power regulation", topology=("input -> regulator -> load",), provenance=Provenance.create(source="USER_INPUT", source_ref=f"{project_id}:conversation", created_by=OWNER, created_at=NOW))
    proposal = ObjectProposal.create(proposal_id=proposal_id, project_id=project_id, target_object_id=object_id, target_object_type=ObjectType.CIRCUIT_INTENT, proposed_object=circuit, base_revision=base_revision, base_fingerprint=base_fingerprint, context_fingerprint=FP, proposer=OWNER)
    evidence = EvidenceReceiptReference(reference_id="evidence-1", receipt_id="receipt-1", tenant_id=TENANT, project_id=project_id, source="KICAD_SCHEMATIC_ERC", tool="ERC", scope="schematic", result_summary="PASS", occurred_at=NOW, source_fingerprint=FP, receipt_fingerprint=FP)
    return CircuitProposalCandidate(tenant_id=TENANT, project_id=project_id, proposal=proposal, evidence_reference=evidence, idempotency_key=key, request_fingerprint=FP, conversation_id=detail.conversation_id, turn_id="turn-design", object_id=object_id, base_revision=base_revision, base_fingerprint=base_fingerprint, context_fingerprint=FP, conversation_fingerprint=detail.fingerprint, source_fingerprint=circuit_source_fingerprint(detail, "turn-design"), constraints=(), route="GREENFIELD_DESIGN", created_at=NOW)


def _approval(candidate: CircuitProposalCandidate, *, rejected: bool = False) -> ApprovalRecord:
    fields = {
        "approval_id": "approval-" + candidate.proposal.proposal_id,
        "project_id": candidate.project_id,
        "proposal_id": candidate.proposal.proposal_id,
        "proposal_fingerprint": candidate.proposal.fingerprint,
        "reviewer": OWNER,
        "reviewed_at": NOW,
    }
    if rejected:
        from embedded_copilot.engineering_runtime.common import (
            ZERO_FINGERPRINT,
            canonical_fingerprint,
        )
        value = ApprovalRecord(**fields, decision=ProposalStatus.REJECTED, fingerprint=ZERO_FINGERPRINT)
        return value.model_copy(update={"fingerprint": canonical_fingerprint(value, exclude={"fingerprint"})})
    return ApprovalRecord.approve(**fields)


def _publication(candidate: CircuitProposalCandidate, approval: ApprovalRecord) -> CircuitPublication:
    proposed = candidate.proposal.proposed_object
    assert isinstance(proposed, CircuitIntentObject)
    circuit = CircuitIntentObject.model_validate(proposed.model_copy(update={"status": LifecycleStatus.ACTIVE}).model_dump(mode="python"))
    applied = ObjectMutationRecord.create(apply_id="apply-" + candidate.proposal.proposal_id, project_id=candidate.project_id, proposal_id=candidate.proposal.proposal_id, new_revision=circuit.revision, object_fingerprint=circuit.fingerprint, applied_at=NOW)
    return CircuitPublication(tenant_id=TENANT, project_id=candidate.project_id, idempotency_key=candidate.idempotency_key, request_fingerprint=candidate.request_fingerprint, base_revision=candidate.base_revision, base_fingerprint=candidate.base_fingerprint, circuit=circuit, proposal=candidate.proposal, approval=approval, apply_record=applied)


def test_circuit_catalog_publish_restart_and_idempotent_retry(tmp_path: Path) -> None:
    catalog, project_id, detail = _seed(tmp_path)
    candidate = _candidate(detail)
    assert catalog.save_circuit_proposal(candidate, tenant_id=TENANT, requester=OWNER) == candidate
    assert catalog.save_circuit_proposal(candidate, tenant_id=TENANT, requester=OWNER) == candidate
    approval = _approval(candidate)
    publication = _publication(candidate, approval)
    assert catalog.decide_circuit_proposal(project_id, candidate.proposal.proposal_id, tenant_id=TENANT, requester=OWNER, proposal_fingerprint=candidate.proposal.fingerprint, decision=approval, publication=publication) == publication
    reopened = _runtime(tmp_path)
    assert reopened.current_circuit_intents(project_id, tenant_id=TENANT, requester=OWNER) == (publication.circuit,)
    assert reopened.circuit_intent_revision(project_id, candidate.object_id, "r1", publication.circuit.fingerprint, tenant_id=TENANT, requester=OWNER) == publication.circuit
    assert not reopened.circuit_source_current(project_id, candidate.object_id, "r1", tenant_id=TENANT, requester=OWNER)
    with pytest.raises(ProjectCatalogForbidden):
        reopened.current_circuit_intents(project_id, tenant_id=TENANT, requester="other-user")
    with pytest.raises(ProjectCatalogForbidden):
        reopened.circuit_source_current(project_id, candidate.object_id, "r1", tenant_id=TENANT, requester="other-user")
    projection = DesktopWorkspaceProjectionService(catalog=reopened).project(
        tenant_id=TENANT, requester_id=OWNER, project_id=project_id,
    )
    circuit_facts = [item for item in projection.authoritative_facts if item.domain == "CIRCUIT"]
    assert len(circuit_facts) == 1
    assert (circuit_facts[0].object_id, circuit_facts[0].revision, circuit_facts[0].currentness) == (
        candidate.object_id, "r1", "STALE",
    )
    assert reopened.decide_circuit_proposal(project_id, candidate.proposal.proposal_id, tenant_id=TENANT, requester=OWNER, proposal_fingerprint=candidate.proposal.fingerprint, decision=approval) == publication
    assert reopened.circuit_proposal(project_id, candidate.proposal.proposal_id, tenant_id=TENANT, requester=OWNER).status is ProposalStatus.APPLIED
    reused = candidate.model_copy(update={"request_fingerprint": "sha256:" + "2" * 64})
    with pytest.raises(ProjectCatalogConflict, match="idempotency"):
        reopened.save_circuit_proposal(reused, tenant_id=TENANT, requester=OWNER)


def test_circuit_workspace_projection_recovers_in_new_process(tmp_path: Path) -> None:
    catalog, project_id, detail = _seed(tmp_path)
    candidate = _candidate(detail)
    catalog.save_circuit_proposal(candidate, tenant_id=TENANT, requester=OWNER)
    approval = _approval(candidate)
    publication = _publication(candidate, approval)
    catalog.decide_circuit_proposal(
        project_id, candidate.proposal.proposal_id,
        tenant_id=TENANT, requester=OWNER,
        proposal_fingerprint=candidate.proposal.fingerprint,
        decision=approval, publication=publication,
    )
    reader = """
import json
import sys
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from embedded_copilot.api.desktop_identity import TrustedDesktopIdentity
from embedded_copilot.api.desktop_workspace_projection_routes import router
from embedded_copilot.desktop_workspace_projection import DesktopWorkspaceProjectionService
from embedded_copilot.project_catalog import ProjectCatalogRuntime

state_root = Path(sys.argv[1])
catalog = ProjectCatalogRuntime(
    project_root=state_root / "projects",
    state_database=state_root / "catalog.sqlite3",
    initialize_async=False,
)
project_id = sys.argv[2]
service = DesktopWorkspaceProjectionService(catalog=catalog)
application = FastAPI()
application.state.desktop_workspace_projection = service
application.include_router(router)
class IdentityProvider:
    def __init__(self, requester):
        self.requester = requester
    def resolve(self, request):
        return TrustedDesktopIdentity(self.requester, "tenant-1", "TEST")
url = "/api/v1/copilot/desktop/projects/" + project_id + "/workspace"
application.state.desktop_identity_provider = IdentityProvider("engineer-1")
with TestClient(application) as client:
    owner_response = client.get(url)
application.state.desktop_identity_provider = IdentityProvider("other-user")
with TestClient(application) as client:
    nonowner_response = client.get(url)
circuit = [fact for fact in owner_response.json()["authoritative_facts"]
           if fact["domain"] == "CIRCUIT"]
print("S01_RESULT=" + json.dumps({
    "owner_status": owner_response.status_code,
    "facts": [[fact["object_id"], fact["revision"], fact["currentness"], fact["reason_code"]]
              for fact in circuit],
    "nonowner_status": nonowner_response.status_code,
}))
"""

    def read_in_new_process() -> dict[str, object]:
        result = subprocess.run(
            [sys.executable, "-B", "-c", reader, str(tmp_path), project_id],
            check=True, capture_output=True, text=True,
        )
        payloads = [line.removeprefix("S01_RESULT=") for line in result.stdout.splitlines()
                    if line.startswith("S01_RESULT=")]
        assert len(payloads) == 1
        return json.loads(payloads[0])

    assert read_in_new_process() == {
        "owner_status": 200,
        "facts": [[candidate.object_id, "r1", "STALE", "CIRCUIT_SOURCE_CHANGED"]],
        "nonowner_status": 403,
    }
    changed_turn = ConversationTurn.model_validate(detail.turns[0].model_copy(update={
        "question": "Changed circuit intent", "fingerprint": "sha256:" + "0" * 64,
    }).model_dump(mode="python"))
    changed = ConversationDetail.model_validate(detail.model_copy(update={
        "turns": (changed_turn,), "fingerprint": "sha256:" + "0" * 64,
    }).model_dump(mode="python"))
    catalog._repository.save_conversation(changed)
    assert read_in_new_process() == {
        "owner_status": 200,
        "facts": [[candidate.object_id, "r1", "STALE", "CIRCUIT_SOURCE_CHANGED"]],
        "nonowner_status": 403,
    }


def test_s01_legacy_calculation_row_reopens_in_new_process_without_new_field(tmp_path: Path) -> None:
    catalog, project_id, detail = _seed(tmp_path)
    original = _candidate(detail)
    legacy_circuit = CircuitIntentObject.create(**{
        **original.proposal.proposed_object.model_dump(mode="python", exclude={"fingerprint"}),
        "calculations": (CalculationRecord(
            calculation_id="calc-legacy", owner_ref="circuit-1", equation="V = I * R",
            inputs={"V": 3.3}, corner="NOMINAL",
        ),),
    })
    proposal = ObjectProposal.create(
        proposal_id=original.proposal.proposal_id, project_id=project_id,
        target_object_id=original.object_id, target_object_type=ObjectType.CIRCUIT_INTENT,
        proposed_object=legacy_circuit, context_fingerprint=FP, proposer=OWNER,
    )
    candidate = CircuitProposalCandidate.model_validate({
        **original.model_dump(mode="python"), "proposal": proposal,
    })
    catalog.save_circuit_proposal(candidate, tenant_id=TENANT, requester=OWNER)
    approval = _approval(candidate)
    publication = _publication(candidate, approval)
    catalog.decide_circuit_proposal(
        project_id, proposal.proposal_id, tenant_id=TENANT, requester=OWNER,
        proposal_fingerprint=proposal.fingerprint, decision=approval,
        publication=publication,
    )
    with sqlite3.connect(tmp_path / "catalog.sqlite3") as connection:
        stored_json = connection.execute(
            "SELECT circuit_body FROM project_circuit_intents WHERE project_id=? AND object_id=?",
            (project_id, candidate.object_id),
        ).fetchone()[0]
    assert '"input_sources"' not in stored_json
    reader = """
import json
import sys
from pathlib import Path
from embedded_copilot.project_catalog import ProjectCatalogRuntime
root = Path(sys.argv[1])
catalog = ProjectCatalogRuntime(
    project_root=root / "projects", state_database=root / "catalog.sqlite3",
    initialize_async=False,
)
project_id, object_id, fingerprint = sys.argv[2:5]
circuit = catalog.circuit_intent_revision(
    project_id, object_id, "r1", fingerprint,
    tenant_id="tenant-1", requester="engineer-1",
)
print("S01_LEGACY=" + json.dumps({
    "fingerprint": circuit.fingerprint,
    "calculation_fingerprint": circuit.calculations[0].fingerprint,
    "calculation_has_input_sources": "input_sources" in circuit.calculations[0].model_dump(mode="json"),
    "source_current": catalog.circuit_source_current(
        project_id, object_id, "r1", tenant_id="tenant-1", requester="engineer-1",
    ),
}))
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", reader, str(tmp_path), project_id,
         candidate.object_id, publication.circuit.fingerprint],
        check=True, capture_output=True, text=True,
    )
    payload = next(
        json.loads(line.removeprefix("S01_LEGACY=")) for line in result.stdout.splitlines()
        if line.startswith("S01_LEGACY=")
    )
    assert payload == {
        "fingerprint": publication.circuit.fingerprint,
        "calculation_fingerprint": legacy_circuit.calculations[0].fingerprint,
        "calculation_has_input_sources": False,
        "source_current": False,
    }


def test_circuit_catalog_reject_and_nonowner(tmp_path: Path) -> None:
    catalog, project_id, detail = _seed(tmp_path)
    candidate = _candidate(detail)
    with pytest.raises(ProjectCatalogForbidden):
        catalog.save_circuit_proposal(candidate, tenant_id=TENANT, requester="other-user")
    catalog.save_circuit_proposal(candidate, tenant_id=TENANT, requester=OWNER)
    decision = _approval(candidate, rejected=True)
    rejected = catalog.decide_circuit_proposal(project_id, candidate.proposal.proposal_id, tenant_id=TENANT, requester=OWNER, proposal_fingerprint=candidate.proposal.fingerprint, decision=decision)
    assert rejected.status is ProposalStatus.REJECTED
    assert catalog.decide_circuit_proposal(project_id, candidate.proposal.proposal_id, tenant_id=TENANT, requester=OWNER, proposal_fingerprint=candidate.proposal.fingerprint, decision=decision) == rejected
    assert catalog.current_circuit_intents(project_id, tenant_id=TENANT, requester=OWNER) == ()


def test_circuit_decision_allows_unrelated_turn_added_after_preview(tmp_path: Path) -> None:
    catalog, project_id, detail = _seed(tmp_path)
    candidate = _candidate(detail)
    catalog.save_circuit_proposal(candidate, tenant_id=TENANT, requester=OWNER)
    unrelated = ConversationTurn(
        turn_id="turn-unrelated", request_id="request-unrelated", route="KNOWLEDGE",
        status=ConversationTurnStatus.COMPLETED, question="Explain an unrelated term",
        answer_text="Explanation", created_at=NOW, completed_at=NOW,
    )
    appended = ConversationDetail.model_validate(detail.model_copy(update={
        "turns": (*detail.turns, unrelated), "fingerprint": "sha256:" + "0" * 64,
    }).model_dump(mode="python"))
    catalog._repository.save_conversation(appended)
    assert circuit_source_fingerprint(appended, candidate.turn_id) == candidate.source_fingerprint
    assert appended.fingerprint != candidate.conversation_fingerprint
    decision = _approval(candidate)
    publication = _publication(candidate, decision)
    assert catalog.decide_circuit_proposal(
        project_id, candidate.proposal.proposal_id, tenant_id=TENANT, requester=OWNER,
        proposal_fingerprint=candidate.proposal.fingerprint, decision=decision,
        publication=publication,
    ) == publication


def test_circuit_decision_rejects_selected_source_change(tmp_path: Path) -> None:
    catalog, project_id, detail = _seed(tmp_path)
    candidate = _candidate(detail)
    catalog.save_circuit_proposal(candidate, tenant_id=TENANT, requester=OWNER)
    changed_turn = ConversationTurn.model_validate(detail.turns[0].model_copy(update={
        "question": "Changed circuit intent", "fingerprint": "sha256:" + "0" * 64,
    }).model_dump(mode="python"))
    changed = ConversationDetail.model_validate(detail.model_copy(update={
        "turns": (changed_turn,), "fingerprint": "sha256:" + "0" * 64,
    }).model_dump(mode="python"))
    catalog._repository.save_conversation(changed)
    decision = _approval(candidate)
    with pytest.raises(ProjectCatalogConflict, match="source changed"):
        catalog.decide_circuit_proposal(
            project_id, candidate.proposal.proposal_id, tenant_id=TENANT, requester=OWNER,
            proposal_fingerprint=candidate.proposal.fingerprint, decision=decision,
            publication=_publication(candidate, decision),
        )
    assert catalog.current_circuit_intents(project_id, tenant_id=TENANT, requester=OWNER) == ()


def test_circuit_catalog_stale_base_and_tampered_body_fail_closed(tmp_path: Path) -> None:
    catalog, project_id, detail = _seed(tmp_path)
    first = _candidate(detail)
    catalog.save_circuit_proposal(first, tenant_id=TENANT, requester=OWNER)
    approval = _approval(first)
    publication = _publication(first, approval)
    catalog.decide_circuit_proposal(project_id, first.proposal.proposal_id, tenant_id=TENANT, requester=OWNER, proposal_fingerprint=first.proposal.fingerprint, decision=approval, publication=publication)
    stale = _candidate(detail, object_id=first.object_id, key="key-2", proposal_id="proposal-2")
    catalog.save_circuit_proposal(stale, tenant_id=TENANT, requester=OWNER)
    with pytest.raises(ProjectCatalogConflict):
        catalog.decide_circuit_proposal(project_id, stale.proposal.proposal_id, tenant_id=TENANT, requester=OWNER, proposal_fingerprint=stale.proposal.fingerprint, decision=_approval(stale), publication=_publication(stale, _approval(stale)))
    with sqlite3.connect(tmp_path / "catalog.sqlite3") as connection:
        connection.execute("UPDATE project_circuit_intents SET object_fingerprint=? WHERE project_id=?", (FP, project_id))
    from embedded_copilot.project_catalog import ProjectCatalogUnavailable
    with pytest.raises(ProjectCatalogUnavailable):
        catalog.current_circuit_intents(project_id, tenant_id=TENANT, requester=OWNER)


def test_schematic_plan_selector_requires_exact_current_owner_revision(tmp_path: Path) -> None:
    catalog, project_id, detail = _seed(tmp_path)
    candidate = _candidate(detail)
    approval = _approval(candidate)
    catalog.save_circuit_proposal(candidate, tenant_id=TENANT, requester=OWNER)
    published = catalog.decide_circuit_proposal(
        project_id, candidate.proposal.proposal_id, tenant_id=TENANT, requester=OWNER,
        proposal_fingerprint=candidate.proposal.fingerprint,
        decision=approval, publication=_publication(candidate, approval),
    )
    selected = (candidate.object_id, "r1", published.circuit.fingerprint)
    with pytest.raises(ProjectCatalogConflict):
        catalog.create_schematic_plan(
            project_id, detail.conversation_id, tenant_id=TENANT, requester=OWNER,
            circuit_intent_selector=(selected[0], "r2", selected[2]),
        )
    with pytest.raises(ProjectCatalogConflict):
        catalog.create_schematic_plan(
            project_id, detail.conversation_id, tenant_id=TENANT, requester=OWNER,
            circuit_intent_selector=(selected[0], selected[1], "sha256:" + "f" * 64),
        )
    with pytest.raises(ProjectCatalogForbidden):
        catalog.create_schematic_plan(
            project_id, detail.conversation_id, tenant_id=TENANT, requester="other-user",
            circuit_intent_selector=selected,
        )
    with (
        patch.object(ProjectCatalogRuntime, "circuit_source_current", return_value=False),
        pytest.raises(ProjectCatalogConflict, match="stale"),
    ):
        catalog.create_schematic_plan(
            project_id, detail.conversation_id, tenant_id=TENANT, requester=OWNER,
            circuit_intent_selector=selected,
        )


def test_explicit_circuit_binding_rejects_superseded_revision(tmp_path: Path) -> None:
    catalog, project_id, detail = _seed(tmp_path)
    first = _candidate(detail)
    first_approval = _approval(first)
    catalog.save_circuit_proposal(first, tenant_id=TENANT, requester=OWNER)
    first_publication = _publication(first, first_approval)
    catalog.decide_circuit_proposal(
        project_id, first.proposal.proposal_id, tenant_id=TENANT, requester=OWNER,
        proposal_fingerprint=first.proposal.fingerprint,
        decision=first_approval, publication=first_publication,
    )
    bound = SimpleNamespace(
        circuit_intent_id=first.object_id, circuit_intent_revision="r1",
        circuit_intent_fingerprint=first_publication.circuit.fingerprint,
    )
    with patch.object(ProjectCatalogRuntime, "circuit_source_current", return_value=True):
        catalog.require_current_circuit_binding(project_id, bound, tenant_id=TENANT, requester=OWNER)
    catalog.require_current_circuit_binding(project_id, None, tenant_id=TENANT, requester=OWNER)

    second = _candidate(
        detail, revision="r2", base_revision="r1",
        base_fingerprint=first_publication.circuit.fingerprint,
        key="key-2", proposal_id="proposal-2",
    )
    second_approval = _approval(second)
    catalog.save_circuit_proposal(second, tenant_id=TENANT, requester=OWNER)
    competing = _candidate(
        detail, revision="r2", base_revision="r1",
        base_fingerprint=first_publication.circuit.fingerprint,
        key="key-competing", proposal_id="proposal-competing",
    )
    competing_approval = _approval(competing)
    catalog.save_circuit_proposal(competing, tenant_id=TENANT, requester=OWNER)
    second_publication = catalog.decide_circuit_proposal(
        project_id, second.proposal.proposal_id, tenant_id=TENANT, requester=OWNER,
        proposal_fingerprint=second.proposal.fingerprint,
        decision=second_approval, publication=_publication(second, second_approval),
    )
    with pytest.raises(ProjectCatalogConflict, match="base revision"):
        catalog.decide_circuit_proposal(
            project_id, competing.proposal.proposal_id, tenant_id=TENANT, requester=OWNER,
            proposal_fingerprint=competing.proposal.fingerprint,
            decision=competing_approval,
            publication=_publication(competing, competing_approval),
        )
    with pytest.raises(ProjectCatalogConflict, match="stale"):
        catalog.require_current_circuit_binding(
            project_id, bound, tenant_id=TENANT, requester=OWNER,
        )
    scope: dict[str, str] = {
        "tenant_id": TENANT, "project_id": project_id,
        "conversation_id": detail.conversation_id,
    }
    stale_detail = SimpleNamespace(
        **scope,
        schematic_planning=SimpleNamespace(approved_plan=bound, proposal=bound),
        pcb_engineering_state=SimpleNamespace(intent_source_binding=bound),
        pcb_production_request=object(), pcb_production=object(),
    )
    with patch.object(ProjectCatalogRuntime, "conversation", return_value=stale_detail):
        with pytest.raises(ProjectCatalogConflict, match="stale"):
            catalog.create_schematic_plan(
                project_id, detail.conversation_id, tenant_id=TENANT, requester=OWNER,
            )
        with pytest.raises(ProjectCatalogConflict, match="stale"):
            catalog.create_pcb_production_proposal(
                project_id, detail.conversation_id, tenant_id=TENANT, requester=OWNER,
            )
        operation = SchematicErcOperation.model_validate({
            **_erc_operation().model_dump(mode="python"),
            "project_id": project_id, "conversation_id": detail.conversation_id,
            "tenant_id": TENANT, "requester_id": OWNER,
            "fingerprint": "sha256:" + "0" * 64,
        })
        assert not catalog.revalidate_circuit_bound_operation(operation)
    current_plan = SimpleNamespace(
        circuit_intent_id=second.object_id, circuit_intent_revision="r2",
        circuit_intent_fingerprint=second_publication.circuit.fingerprint,
    )
    superseded_generation = SimpleNamespace(
        status=SchematicProductionStatus.STALE, generation_receipt=SimpleNamespace(
            operation_id=operation.source_operation_id,
            operation_receipt_fingerprint=operation.source_operation_receipt_fingerprint,
        ),
    )
    with patch.object(ProjectCatalogRuntime, "conversation", return_value=SimpleNamespace(
        **scope,
        schematic_planning=SimpleNamespace(approved_plan=current_plan, proposal=current_plan),
        schematic_production=superseded_generation,
    )):
        assert not catalog.revalidate_circuit_bound_operation(operation)
    current_generation = SimpleNamespace(
        operation_id=operation.source_operation_id,
        operation_receipt_fingerprint=operation.source_operation_receipt_fingerprint,
        artifact_fingerprint=operation.schematic_artifact_fingerprint,
        artifact_path=operation.target_relative_path,
    )
    with patch.object(ProjectCatalogRuntime, "conversation", return_value=SimpleNamespace(
        **scope,
        schematic_planning=SimpleNamespace(approved_plan=current_plan, proposal=current_plan),
        schematic_production=SimpleNamespace(
            status=SchematicProductionStatus.GENERATED,
            generation_receipt=current_generation,
        ),
    )), patch.object(ProjectCatalogRuntime, "circuit_source_current", return_value=True):
        assert catalog.revalidate_circuit_bound_operation(operation)
    mismatched_generation = SimpleNamespace(
        **{**vars(current_generation), "operation_receipt_fingerprint": "sha256:" + "f" * 64},
    )
    with patch.object(ProjectCatalogRuntime, "conversation", return_value=SimpleNamespace(
        **scope,
        schematic_planning=SimpleNamespace(approved_plan=current_plan, proposal=current_plan),
        schematic_production=SimpleNamespace(
            status=SchematicProductionStatus.GENERATED,
            generation_receipt=mismatched_generation,
        ),
    )):
        assert not catalog.revalidate_circuit_bound_operation(operation)
    with patch.object(ProjectCatalogRuntime, "conversation", return_value=SimpleNamespace(
        **scope,
        schematic_planning=None, schematic_production=None,
    )):
        assert catalog.revalidate_circuit_bound_operation(operation)
    assert catalog._repository.circuit_source_current(
        project_id, second.object_id, "r2", tenant_id=TENANT, requester=OWNER,
    )
    unrelated_turn = ConversationTurn(
        turn_id="turn-unrelated", request_id="request-unrelated", route="KNOWLEDGE",
        status=ConversationTurnStatus.COMPLETED, question="Explain an unrelated term",
        answer_text="Explanation", created_at=NOW, completed_at=NOW,
    )
    appended = ConversationDetail.model_validate(detail.model_copy(update={
        "turns": (*detail.turns, unrelated_turn), "fingerprint": "sha256:" + "0" * 64,
    }).model_dump(mode="python"))
    catalog._repository.save_conversation(appended)
    assert catalog._repository.circuit_source_current(
        project_id, second.object_id, "r2", tenant_id=TENANT, requester=OWNER,
    )
    revised_turn = ConversationTurn.model_validate(detail.turns[0].model_copy(update={
        "question": "Changed source intent", "fingerprint": "sha256:" + "0" * 64,
    }).model_dump(mode="python"))
    changed = ConversationDetail.model_validate(appended.model_copy(update={
        "turns": (revised_turn, unrelated_turn), "fingerprint": "sha256:" + "0" * 64,
    }).model_dump(mode="python"))
    catalog._repository.save_conversation(changed)
    assert not catalog._repository.circuit_source_current(
        project_id, second.object_id, "r2", tenant_id=TENANT, requester=OWNER,
    )
    with pytest.raises(ProjectCatalogConflict, match="stale"):
        catalog.require_current_circuit_binding(
            project_id, second_publication.circuit,
            tenant_id=TENANT, requester=OWNER,
        )


def test_project_delete_removes_circuit_candidates_and_publications(tmp_path: Path) -> None:
    catalog, project_id, detail = _seed(tmp_path)
    candidate = _candidate(detail)
    approval = _approval(candidate)
    catalog.save_circuit_proposal(candidate, tenant_id=TENANT, requester=OWNER)
    catalog.decide_circuit_proposal(
        project_id, candidate.proposal.proposal_id, tenant_id=TENANT, requester=OWNER,
        proposal_fingerprint=candidate.proposal.fingerprint,
        decision=approval, publication=_publication(candidate, approval),
    )
    project = catalog.get_project_for_requester(project_id, tenant_id=TENANT, requester=OWNER)
    catalog.delete_project(
        project_id, project_fingerprint=project.fingerprint,
        tenant_id=TENANT, requester=OWNER,
    )
    with sqlite3.connect(tmp_path / "catalog.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM project_circuit_proposals WHERE project_id=?", (project_id,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM project_circuit_intents WHERE project_id=?", (project_id,)
        ).fetchone()[0] == 0
