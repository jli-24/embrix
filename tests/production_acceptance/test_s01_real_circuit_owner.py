"""Opt-in S01 acceptance: real schematic ERC through Catalog-owned Circuit review."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from embedded_copilot.api.desktop_identity import TrustedDesktopIdentity
from embedded_copilot.api.main import create_app
from embedded_copilot.core.config import Settings
from embedded_copilot.engineering_copilot import (
    SchematicProductionRequest,
    SchematicProductionState,
    SchematicProductionStatus,
    SchematicProposal,
    SchematicProposalDecisionStatus,
    create_schematic_production_state,
    decide_schematic_proposal,
)
from embedded_copilot.engineering_workspace_apply import (
    ProjectCatalogWorkspaceTargetResolver,
    SQLiteWorkspaceApplyStore,
    WorkspaceApplyService,
)
from embedded_copilot.execution_worker import (
    ArtifactStore,
    ExecutionOperationService,
    ExecutionWorker,
    KiCadCliSchematicErcAdapter,
    KiCadCliSchematicParseAdapter,
    OperationStatus,
    OperationType,
    SchematicSymbolLibraryValidator,
    SchematicWriteAdapter,
    SQLiteExecutionStore,
    probe_kicad_cli_version,
)
from embedded_copilot.project_catalog.models import (
    ConversationDetail,
    ConversationTurn,
    ConversationTurnStatus,
)
from embedded_copilot.schematic_execution import SchematicExecutionBridge
from tests.engineering_copilot.test_schematic_production_g3 import (
    NOW,
    _runtime_with_approved_plan,
)
from tests.production_acceptance.test_real_schematic_production_golden import (
    _proposal as _device_resistor_proposal,
)

TENANT = "tenant-1"
OWNER = "engineer-1"
CLI = Path(r"D:\ruanjian\32\kicad\bin\kicad-cli.exe")


class _Identity:
    def __init__(self, requester: str) -> None:
        self.requester = requester

    def resolve(self, request: Request) -> TrustedDesktopIdentity:
        del request
        return TrustedDesktopIdentity(self.requester, TENANT, "TEST")


def test_s01_real_erc_circuit_review_owner_and_restart(tmp_path: Path) -> None:
    # This must precede tool discovery, version probing, and every subprocess.
    if os.environ.get("EMBRIX_RUN_S01_REAL_KICAD") != "1":
        pytest.skip("set EMBRIX_RUN_S01_REAL_KICAD=1 for the isolated real KiCad S01 gate")
    if not CLI.is_file():
        pytest.skip("authorized KiCad CLI is unavailable")

    catalog, project, session, proposed = _runtime_with_approved_plan(tmp_path)
    project_root = catalog.knowledge_snapshot_root(project.project_id, tenant_id=TENANT)
    symbol_library = CLI.parent.parent / "share" / "kicad" / "symbols" / "Device.kicad_sym"
    if not symbol_library.is_file():
        pytest.skip("authorized KiCad installation has no Device symbol library")
    (project_root / "sym-lib-table").write_text(
        '(sym_lib_table (version 7) (lib (name "Device") (type "KiCad") '
        f'(uri "{symbol_library.as_posix()}") (options "") (descr "")))',
        encoding="utf-8",
    )
    workspace = WorkspaceApplyService(
        resolver=ProjectCatalogWorkspaceTargetResolver(catalog),
        store=SQLiteWorkspaceApplyStore(tmp_path / "workspace.sqlite3"),
        state_root=tmp_path / "workspace-state",
        clock=type("Clock", (), {"now": lambda self: NOW})(),
    )
    execution_db = tmp_path / "execution.sqlite3"
    artifact_root = tmp_path / "execution-artifacts"
    artifact_store = ArtifactStore(artifact_root)
    execution = ExecutionOperationService(
        store=SQLiteExecutionStore(execution_db), artifact_store=artifact_store,
        clock=lambda: NOW,
    )
    # Scope of preseeded domain facts: the existing fixture supplies the
    # approved project design context and SchematicPlan.  The Device:R proposal
    # supplies only the isolated schematic test input.  No Operation, Status,
    # Receipt, Artifact, or Circuit publication is constructed by this seed.
    plan = proposed.schematic_planning.approved_plan
    assert plan is not None and proposed.schematic_production is not None
    seeded_request = proposed.schematic_production.request
    seeded_proposal = proposed.schematic_production.proposal
    device_proposal = _device_resistor_proposal()
    proposal = SchematicProposal.create(**{
        **seeded_proposal.model_dump(mode="python", exclude={"fingerprint"}),
        "components": device_proposal.components,
        "pins": device_proposal.pins,
        "nets": device_proposal.nets,
        "power_symbols": device_proposal.power_symbols,
    })
    production_request = SchematicProductionRequest.create(**{
        **seeded_request.model_dump(mode="python", exclude={"fingerprint"}),
        "source_request_id": None,
        "trace_id": None,
    })
    proposal_decision = decide_schematic_proposal(
        proposal, decision=SchematicProposalDecisionStatus.APPROVED,
        reviewer=OWNER, decided_at=NOW,
    )
    bridge = SchematicExecutionBridge(
        workspace=workspace, execution=execution, clock=lambda: NOW,
        symbol_binding_validator=SchematicSymbolLibraryValidator(lambda **kwargs: project_root),
    )
    write_request = bridge.approve_and_enqueue(
        production_request=production_request, proposal=proposal,
        decision=proposal_decision, tenant_id=TENANT,
    )
    write_id = write_request.operation.operation_id
    initial_state = create_schematic_production_state(production_request, proposal)
    queued_state = SchematicProductionState.create(
        production_id=initial_state.production_id,
        project_id=project.project_id,
        conversation_id=session.conversation.conversation_id,
        request=production_request, proposal=proposal, decision=proposal_decision,
        operation_id=write_id, operation_fingerprint=write_request.operation.fingerprint,
        status=SchematicProductionStatus.QUEUED,
    )
    catalog.persist_conversation_detail(ConversationDetail.model_validate(
        proposed.model_copy(update={
            "schematic_production": queued_state,
            "fingerprint": "sha256:" + "0" * 64,
        }).model_dump(mode="python")
    ))
    write_worker = ExecutionWorker(
        worker_id="s01-write", store=execution.store, registry=None,
        workspace_resolver=None, targets=None,
        schematic_write=SchematicWriteAdapter(workspace), artifact_store=artifact_store,
        allowed_operation_types=(OperationType.KICAD_SCHEMATIC_WRITE,), clock=lambda: NOW,
    )
    write_status = write_worker.run_once()
    assert write_status is not None and write_status.status is OperationStatus.SUCCEEDED
    write_operation = execution.get_operation(tenant_id=TENANT, project_id=project.project_id, operation_id=write_id)
    write_receipt = execution.get_receipt(tenant_id=TENANT, project_id=project.project_id, operation_id=write_id)
    assert write_operation is not None and write_receipt is not None
    generated = catalog.record_schematic_generation(
        operation=write_operation, receipt=write_receipt, external_validation_status="UNAVAILABLE",
    )
    generation = generated.schematic_production.generation_receipt
    assert generation is not None and generation.status.value == "ARTIFACT_GENERATED"
    schematic = project_root / generation.artifact_path
    assert schematic.is_file()
    source_hash = "sha256:" + hashlib.sha256(schematic.read_bytes()).hexdigest()
    assert source_hash == generation.artifact_fingerprint

    version = probe_kicad_cli_version(str(CLI), cwd=project_root)
    assert version == "10.0.5", f"KiCad version {version!r} is outside the S01 verified scope"
    parse = KiCadCliSchematicParseAdapter(
        executable=str(CLI), kicad_version=version, clock=lambda: NOW,
    ).execute(
        project_id=project.project_id, conversation_id=session.conversation.conversation_id,
        operation_id=write_id, artifact=schematic, artifact_path=generation.artifact_path,
        expected_artifact_fingerprint=source_hash,
    )
    assert parse.validation_status == "PARSE_VERIFIED" and parse.artifact_unchanged
    catalog.record_schematic_parse_validation(evidence=parse)

    erc_request = bridge.enqueue_erc(
        write_operation=write_operation, receipt_fingerprint=write_receipt.fingerprint,
        reviewer_id=OWNER,
    )
    erc_worker = ExecutionWorker(
        worker_id="s01-erc", store=execution.store, registry=None,
        workspace_resolver=None, targets=None,
        schematic_erc=KiCadCliSchematicErcAdapter(
            executable=str(CLI), kicad_version=version,
            workspace_resolver=lambda operation: project_root,
        ),
        artifact_store=artifact_store,
        allowed_operation_types=(OperationType.KICAD_SCHEMATIC_ERC,), clock=lambda: NOW,
    )
    erc_status = erc_worker.run_once()
    assert erc_status is not None and erc_status.status is OperationStatus.SUCCEEDED
    erc_id = erc_request.operation.operation_id
    erc_operation = execution.get_operation(tenant_id=TENANT, project_id=project.project_id, operation_id=erc_id)
    erc_receipt = execution.get_receipt(tenant_id=TENANT, project_id=project.project_id, operation_id=erc_id)
    assert erc_operation is not None and erc_receipt is not None
    assert erc_receipt.schematic_erc_status == "ERC_PASS"
    assert erc_receipt.schematic_erc_result is not None
    assert erc_receipt.schematic_erc_result.schematic_content_fingerprint == source_hash
    report = erc_receipt.schematic_erc_result.report_artifact
    assert report is not None
    assert erc_receipt.schematic_erc_result.raw_evidence
    assert erc_receipt.schematic_erc_result.raw_evidence_fingerprint
    persisted = catalog.record_schematic_erc_validation(operation=erc_operation, receipt=erc_receipt)
    assert persisted.schematic_production.erc_gate_status.value == "PASS"

    # Domain context is seeded from the existing approved-plan fixture.  The
    # completed design turn is an explicit review input, not execution evidence.
    turn = ConversationTurn(
        turn_id="turn-s01-circuit", request_id="request-s01-circuit",
        route="GREENFIELD_DESIGN", status=ConversationTurnStatus.COMPLETED,
        question="Design the STM32 control circuit", answer_text="Review circuit intent",
        created_at=NOW, completed_at=NOW,
    )
    detail = catalog.conversation(
        project.project_id, session.conversation.conversation_id,
        tenant_id=TENANT, requester=OWNER,
    )
    catalog.persist_conversation_detail(ConversationDetail.model_validate(
        detail.model_copy(update={
            "turns": (*detail.turns, turn), "fingerprint": "sha256:" + "0" * 64,
        }).model_dump(mode="python")
    ))
    settings = Settings(
        environment="test", desktop_state_database=tmp_path / "desktop.sqlite3",
        desktop_project_metadata_directory=tmp_path / "metadata",
        project_root=tmp_path / "projects", _env_file=None,
    )
    app = create_app(
        settings=settings, project_catalog_runtime=catalog,
        execution_operation_port=execution, desktop_identity_provider=_Identity(OWNER),
    )
    url = f"/api/v1/copilot/projects/{project.project_id}/circuit-intents"
    with TestClient(app) as client:
        preview = client.post(f"{url}/preview", json={
            "conversation_id": session.conversation.conversation_id,
            "turn_id": turn.turn_id,
            "circuit_intent": {
                "function": "STM32 control and power circuit",
                "topology": ["input -> regulation -> STM32 control"],
            },
            "erc_operation_id": erc_id,
            "idempotency_key": "s01-real-circuit-review",
        })
        assert preview.status_code == 200, preview.json()
        shown = preview.json()
        assert shown["evidence_receipt_id"] == erc_id
        app.state.desktop_identity_provider = _Identity("other-user")
        forbidden = client.post(f"{url}/decision", json={
            "proposal_id": shown["proposal_id"],
            "proposal_fingerprint": shown["proposal_fingerprint"],
            "base_revision": shown["base_revision"],
            "base_fingerprint": shown["base_fingerprint"],
            "context_fingerprint": shown["context_fingerprint"],
            "evidence_receipt_fingerprint": shown["evidence_receipt_fingerprint"],
            "decision": "APPROVE",
        })
        assert forbidden.status_code == 403
        app.state.desktop_identity_provider = _Identity(OWNER)
        decision = client.post(f"{url}/decision", json={
            "proposal_id": shown["proposal_id"],
            "proposal_fingerprint": shown["proposal_fingerprint"],
            "base_revision": shown["base_revision"],
            "base_fingerprint": shown["base_fingerprint"],
            "context_fingerprint": shown["context_fingerprint"],
            "evidence_receipt_fingerprint": shown["evidence_receipt_fingerprint"],
            "decision": "APPROVE",
        })
        assert decision.status_code == 200, decision.json()
        assert decision.json()["status"] == "APPLIED"
    circuit_id = decision.json()["object_id"]
    revision = decision.json()["revision"]
    circuit_fingerprint = decision.json()["object_fingerprint"]
    assert catalog.circuit_source_current(
        project.project_id, circuit_id, revision, tenant_id=TENANT, requester=OWNER,
    )

    reader = """
import json, sys
from pathlib import Path
from embedded_copilot.project_catalog import ProjectCatalogForbidden, ProjectCatalogRuntime
from embedded_copilot.execution_worker import ExecutionOperationService, SQLiteExecutionStore
root, project_id, circuit_id, revision, circuit_fingerprint, erc_id = sys.argv[1:]
root = Path(root)
catalog = ProjectCatalogRuntime(project_root=root / 'projects', state_database=root / 'desktop.sqlite3', initialize_async=False)
execution = ExecutionOperationService(store=SQLiteExecutionStore(root / 'execution.sqlite3'))
circuit = catalog.circuit_intent_revision(project_id, circuit_id, revision, circuit_fingerprint, tenant_id='tenant-1', requester='engineer-1')
receipt = execution.get_receipt(tenant_id='tenant-1', project_id=project_id, operation_id=erc_id)
try:
    catalog.circuit_source_current(project_id, circuit_id, revision, tenant_id='tenant-1', requester='other-user')
except ProjectCatalogForbidden:
    nonowner_forbidden = True
else:
    nonowner_forbidden = False
print('S01_RESULT=' + json.dumps({'circuit': circuit.object_id, 'revision': circuit.revision, 'current': catalog.circuit_source_current(project_id, circuit_id, revision, tenant_id='tenant-1', requester='engineer-1'), 'erc_receipt': receipt.operation_id, 'nonowner_forbidden': nonowner_forbidden}))
"""
    recovered = subprocess.run(
        [sys.executable, "-B", "-c", reader, str(tmp_path), project.project_id,
         circuit_id, revision, circuit_fingerprint, erc_id],
        check=True, capture_output=True, text=True,
    )
    payloads = [line.removeprefix("S01_RESULT=") for line in recovered.stdout.splitlines()
                if line.startswith("S01_RESULT=")]
    assert len(payloads) == 1
    assert json.loads(payloads[0]) == {
        "circuit": circuit_id, "revision": revision, "current": True,
        "erc_receipt": erc_id, "nonowner_forbidden": True,
    }
    print("S01_IDENTITIES=" + json.dumps({
        "state_root": str(tmp_path), "catalog_db": str(tmp_path / "desktop.sqlite3"),
        "execution_db": str(execution_db), "artifact_root": str(artifact_root),
        "project_id": project.project_id, "write_operation_id": write_id,
        "erc_operation_id": erc_id, "erc_receipt_fingerprint": erc_receipt.fingerprint,
        "schematic_artifact_ref": generation.artifact_path,
        "schematic_fingerprint": source_hash, "erc_report_ref": report,
        "erc_raw_evidence_fingerprint": erc_receipt.schematic_erc_result.raw_evidence_fingerprint,
        "circuit_id": circuit_id, "circuit_revision": revision,
        "circuit_fingerprint": circuit_fingerprint,
    }, sort_keys=True))
    schematic.write_bytes(schematic.read_bytes() + b"\nS01 isolated source change\n")
    assert not catalog.circuit_source_current(
        project.project_id, circuit_id, revision, tenant_id=TENANT, requester=OWNER,
    )
    stale_recovered = subprocess.run(
        [sys.executable, "-B", "-c", reader, str(tmp_path), project.project_id,
         circuit_id, revision, circuit_fingerprint, erc_id],
        check=True, capture_output=True, text=True,
    )
    stale_payloads = [line.removeprefix("S01_RESULT=") for line in stale_recovered.stdout.splitlines()
                      if line.startswith("S01_RESULT=")]
    assert len(stale_payloads) == 1
    assert json.loads(stale_payloads[0])["current"] is False
