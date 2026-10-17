from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from embedded_copilot.engineering_copilot import ArtifactIntelligenceService
from embedded_copilot.engineering_workspace_apply import FixedWorkspaceTargetResolver, SQLiteWorkspaceApplyStore, WorkspaceApplyService
from embedded_copilot.execution_worker import (
    ExecutionOperationService,
    ExecutionWorker,
    KiCadCliPCBParseAdapter,
    KiCadCliPCBDrCAdapter,
    OperationStatus,
    OperationType,
    PCBWriteAdapter,
    SQLiteExecutionStore,
    discover_kicad_footprint_root,
    discover_kicad_pcb_parse_command,
    discover_kicad_pcb_drc_command,
    probe_kicad_cli_version,
)
from embedded_copilot.pcb_production import (
    PCBProductionService,
    PCBProposalDecisionStatus,
    PCBDrcStatus,
    PCBRouteSegment,
    KiCadFootprintAvailability,
    decide_pcb_proposal,
)
from embedded_copilot.pcb_production.execution import PCBExecutionBridge

from tests.pcb_production.test_service import _request
from tests.production_acceptance.kicad_toolchain import discover_test_kicad_cli


NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)


def _kicad_cli() -> str | None:
    executable = discover_test_kicad_cli()
    return None if executable is None else str(executable)


class _Clock:
    def now(self) -> datetime:
        return NOW


def test_real_pcb_production_golden(tmp_path: Path) -> None:
    executable = _kicad_cli()
    if executable is None:
        pytest.skip("real KiCad CLI is not configured; set EMBRIX_KICAD_CLI")
    root = tmp_path / "project"
    root.mkdir()
    footprint_root = discover_kicad_footprint_root(executable)
    if footprint_root is None:
        pytest.skip("the configured KiCad installation has no trusted footprint library")
    command_builder = discover_kicad_pcb_drc_command(executable, cwd=root)
    parse_builder = discover_kicad_pcb_parse_command(executable, cwd=root)
    if command_builder is None or parse_builder is None:
        pytest.skip("the configured KiCad installation has no supported PCB DRC command")

    base_request = _request()
    request_material = base_request.model_dump(mode="python")
    request_material.pop("fingerprint", None)
    request_material["routes"] = (
                PCBRouteSegment.create(
                    net_name="NET_A",
                    layer="F.Cu",
                    width_mm=0.25,
                    points=((109.175, 110.0), (109.175, 108.5), (129.175, 108.5), (129.175, 110.0)),
                    source_refs=("real-truth-route-a",),
                ),
                PCBRouteSegment.create(
                    net_name="NET_B",
                    layer="F.Cu",
                    width_mm=0.25,
                    points=((110.825, 110.0), (110.825, 111.5), (130.825, 111.5), (130.825, 110.0)),
                    source_refs=("real-truth-route-b",),
                ),
    )
    request = type(base_request).create(**request_material)
    # WorkspaceApplyService requires a non-empty source snapshot.  Seed the
    # temporary project with an inert project-owned file so the golden covers
    # the real write boundary instead of failing before the PCB operation is
    # enqueued.
    (root / "workspace-seed.txt").write_text("real PCB production golden workspace\n", encoding="utf-8")
    proposal = PCBProductionService(
        footprints=KiCadFootprintAvailability(footprint_root)
    ).create_proposal(request)
    decision = decide_pcb_proposal(
        proposal,
        decision=PCBProposalDecisionStatus.APPROVED,
        reviewer=request.requester,
        decided_at=NOW,
    )
    workspace = WorkspaceApplyService(
        resolver=FixedWorkspaceTargetResolver(root, tenant_id="tenant-golden", project_id=request.project_id),
        store=SQLiteWorkspaceApplyStore(tmp_path / "workspace.sqlite3"),
        state_root=tmp_path / "workspace-state",
        clock=_Clock(),
        approval_ttl=timedelta(minutes=15),
    )
    execution = ExecutionOperationService(
        store=SQLiteExecutionStore(tmp_path / "execution.sqlite3"),
        clock=lambda: NOW,
    )
    bridge = PCBExecutionBridge(
        workspace=workspace,
        execution=execution,
        footprint_root=footprint_root,
        clock=lambda: NOW,
    )
    write_request = bridge.approve_and_enqueue(
        production_request=request,
        proposal=proposal,
        decision=decision,
        tenant_id="tenant-golden",
    )
    worker = ExecutionWorker(
        worker_id="pcb-real-golden-worker",
        store=execution.store,
        registry=None,
        workspace_resolver=None,
        targets=None,
        pcb_write=PCBWriteAdapter(workspace),
        pcb_drc=KiCadCliPCBDrCAdapter(
            executable=executable,
            workspace_resolver=lambda operation: root,
            command_builder=command_builder,
            kicad_version=probe_kicad_cli_version(executable, cwd=root),
            clock=lambda: NOW,
        ),
        allowed_operation_types=(OperationType.KICAD_PCB_WRITE, OperationType.KICAD_PCB_DRC),
        clock=lambda: NOW,
    )

    write_status = worker.run_once()
    assert write_status is not None and write_status.status is OperationStatus.SUCCEEDED
    target = root / request.target_relative_path
    assert target.is_file()
    content = target.read_bytes()
    content_hash = "sha256:" + hashlib.sha256(content).hexdigest()
    assert content_hash == write_request.operation.content_fingerprint
    write_receipt = execution.get_receipt(
        tenant_id="tenant-golden", project_id=request.project_id, operation_id=write_request.operation.operation_id
    )
    assert write_receipt is not None and write_receipt.pcb_artifact is not None

    parse_evidence = KiCadCliPCBParseAdapter(
        executable=executable,
        workspace_resolver=lambda operation: root,
        command_builder=parse_builder,
        kicad_version=probe_kicad_cli_version(executable, cwd=root),
        clock=lambda: NOW,
    ).execute(write_request.operation)
    assert parse_evidence.validation_status.value == "PARSE_VERIFIED"
    assert parse_evidence.operation_id == write_request.operation.operation_id
    drc_request = bridge.enqueue_drc(
        write_operation=write_request.operation,
        receipt_fingerprint=write_receipt.fingerprint,
        reviewer_id=request.requester,
        parse_evidence=parse_evidence,
    )
    drc_status = worker.run_once()
    assert drc_status is not None and drc_status.status is OperationStatus.SUCCEEDED
    drc_receipt = execution.get_receipt(
        tenant_id="tenant-golden", project_id=request.project_id, operation_id=drc_request.operation.operation_id
    )
    assert drc_receipt is not None and drc_receipt.pcb_drc_result is not None
    assert drc_receipt.pcb_drc_result.status is PCBDrcStatus.DRC_PASS
    assert drc_receipt.pcb_drc_result.exit_status == 0
    assert drc_receipt.pcb_drc_result.violations == ()
    assert drc_receipt.pcb_drc_result.report_artifact_ref is not None

    snapshot = ArtifactIntelligenceService().scan_directory(
        root=root,
        tenant_id="tenant-golden",
        project_id=request.project_id,
        conversation_id=request.conversation_id,
        source_operation_id=write_request.operation.operation_id,
        source_proposal_fingerprint=proposal.fingerprint,
    )
    artifact = next(item for item in snapshot.inventory.artifacts if item.path_reference == request.target_relative_path)
    # The artifact registry records the real PCB after a successful receipt.
    assert artifact.status.value == "AVAILABLE"
    assert artifact.hash == content_hash
    assert drc_receipt.evidence_references
    assert tuple(
        item.operation_type for item in execution.list_operations(
            tenant_id="tenant-golden", project_id=request.project_id, limit=10
        )
    ) == (OperationType.KICAD_PCB_DRC, OperationType.KICAD_PCB_WRITE)
