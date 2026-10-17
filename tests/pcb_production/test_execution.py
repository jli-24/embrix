from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from embedded_copilot.engineering_workspace_apply import FixedWorkspaceTargetResolver, SQLiteWorkspaceApplyStore, WorkspaceApplyService
from embedded_copilot.execution_worker import ExecutionOperationService, ExecutionWorker, KiCadCliPCBParseAdapter, KiCadCliPCBDrCAdapter, OperationStatus, OperationType, PCBWriteAdapter, SQLiteExecutionStore
from embedded_copilot.pcb_production import PCBProposalDecision, PCBProductionService
from embedded_copilot.pcb_production.execution import PCBExecutionBridge

from .test_service import _Availability, _request


NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return NOW


def test_approved_pcb_uses_canonical_worker_and_workspace_apply(tmp_path: Path) -> None:
    request = _request()
    proposal = PCBProductionService(footprints=_Availability()).create_proposal(request)
    decision = PCBProposalDecision.create(
        decision_id="pcb-decision-golden",
        proposal_id=proposal.proposal_id,
        proposal_fingerprint=proposal.fingerprint,
        decision="APPROVED",
        reviewer="engineer-golden",
        decided_at=NOW,
        reason="golden approval",
    )
    root = tmp_path / "project"
    root.mkdir()
    (root / "project.txt").write_text("golden project", encoding="utf-8")
    workspace = WorkspaceApplyService(
        resolver=FixedWorkspaceTargetResolver(root, tenant_id="tenant-golden", project_id=request.project_id),
        store=SQLiteWorkspaceApplyStore(tmp_path / "workspace.sqlite3"),
        state_root=tmp_path / "workspace-state",
        clock=_Clock(),
        approval_ttl=timedelta(minutes=15),
    )
    execution = ExecutionOperationService(store=SQLiteExecutionStore(tmp_path / "execution.sqlite3"), clock=lambda: NOW)
    bridge = PCBExecutionBridge(workspace=workspace, execution=execution, clock=lambda: NOW)
    write_request = bridge.approve_and_enqueue(production_request=request, proposal=proposal, decision=decision, tenant_id="tenant-golden")
    assert write_request.operation.operation_type is OperationType.KICAD_PCB_WRITE
    assert write_request.operation.correlation is not None
    assert write_request.operation.correlation.request_id == "application-request-pcb"
    assert write_request.operation.correlation.trace_id == "application-trace-pcb"

    worker = ExecutionWorker(
        worker_id="pcb-golden-worker",
        store=execution.store,
        registry=None,
        workspace_resolver=None,
        targets=None,
        pcb_write=PCBWriteAdapter(workspace),
        allowed_operation_types=(OperationType.KICAD_PCB_WRITE,),
        clock=lambda: NOW,
    )
    status = worker.run_once()
    assert status is not None and status.status is OperationStatus.SUCCEEDED
    target = root / request.target_relative_path
    assert target.is_file()
    content = target.read_bytes()
    assert "sha256:" + hashlib.sha256(content).hexdigest() == write_request.operation.content_fingerprint
    receipt = execution.get_receipt(tenant_id="tenant-golden", project_id=request.project_id, operation_id=write_request.operation.operation_id)
    assert receipt is not None and receipt.pcb_artifact is not None
    assert receipt.correlation is not None
    assert receipt.correlation.request_id == "application-request-pcb"
    assert receipt.correlation.trace_id == "application-trace-pcb"
    assert receipt.correlation.approval_id is not None
    assert receipt.workspace_mutations and receipt.workspace_apply_receipt_fingerprint is not None

    def command_builder(executable: str, board_path: Path, report_path: Path) -> tuple[str, ...]:
        return (executable, "pcb", "drc", "--output", str(report_path), str(board_path))

    def runner(argv: tuple[str, ...], cwd: Path, timeout: int):
        report = Path(argv[argv.index("--output") + 1])
        report.write_text(json.dumps({"violations": [], "warnings": []}), encoding="utf-8")
        return type("Completed", (), {"returncode": 0})()

    executable = tmp_path / "kicad-cli.exe"
    executable.write_bytes(b"trusted test executable")

    def parse_command_builder(executable: str, board_path: Path, output_directory: Path) -> tuple[str, ...]:
        return (executable, "pcb", "export", "gerbers", "--output", str(output_directory), str(board_path))

    def parse_runner(argv: tuple[str, ...], cwd: Path, timeout: int):
        output_directory = Path(argv[argv.index("--output") + 1])
        output_directory.mkdir(parents=True)
        (output_directory / "main-F_Cu.gbr").write_text("gerber", encoding="utf-8")
        return type("Completed", (), {"returncode": 0, "stdout": b"", "stderr": b""})()

    parse_evidence = KiCadCliPCBParseAdapter(
        executable=str(executable),
        workspace_resolver=lambda operation: root,
        command_builder=parse_command_builder,
        kicad_version="10.0.5",
        runner=parse_runner,
        clock=lambda: NOW,
    ).execute(write_request.operation)
    drc_request = bridge.enqueue_drc(write_operation=write_request.operation, receipt_fingerprint=receipt.fingerprint, reviewer_id="engineer-golden", parse_evidence=parse_evidence)
    assert drc_request.operation.correlation is not None
    assert drc_request.operation.correlation.request_id == "application-request-pcb"
    assert drc_request.operation.correlation.trace_id == "application-trace-pcb"
    drc_adapter = KiCadCliPCBDrCAdapter(
        executable=str(executable),
        workspace_resolver=lambda operation: root,
        command_builder=command_builder,
        kicad_version="10.0.5",
        runner=runner,
        clock=lambda: NOW,
    )
    drc_worker = ExecutionWorker(
        worker_id="pcb-golden-worker",
        store=execution.store,
        registry=None,
        workspace_resolver=None,
        targets=None,
        pcb_drc=drc_adapter,
        allowed_operation_types=(OperationType.KICAD_PCB_DRC,),
        clock=lambda: NOW,
    )
    drc_status = drc_worker.run_once()
    assert drc_status is not None and drc_status.status is OperationStatus.SUCCEEDED
    drc_receipt = execution.get_receipt(tenant_id="tenant-golden", project_id=request.project_id, operation_id=drc_request.operation.operation_id)
    assert drc_receipt is not None and drc_receipt.pcb_drc_result is not None
    assert drc_receipt.pcb_drc_result.status.value == "DRC_PASS"
    post_enqueue_parse_evidence = KiCadCliPCBParseAdapter(
        executable=str(executable),
        workspace_resolver=lambda operation: root,
        command_builder=parse_command_builder,
        kicad_version="10.0.5",
        runner=parse_runner,
        clock=lambda: NOW,
    ).execute(drc_request.operation)
    assert post_enqueue_parse_evidence.operation_id == write_request.operation.operation_id
    assert post_enqueue_parse_evidence.validation_status.value == "PARSE_VERIFIED"
    assert post_enqueue_parse_evidence.artifact_unchanged is True
    assert drc_receipt.pcb_drc_result.parse_validation is None
    assert tuple(event.operation_type for event in execution.list_operation_events(tenant_id="tenant-golden", project_id=request.project_id, operation_id=write_request.operation.operation_id)) == (OperationType.KICAD_PCB_WRITE,) * 6
    assert tuple(event.operation_type for event in execution.list_operation_events(tenant_id="tenant-golden", project_id=request.project_id, operation_id=drc_request.operation.operation_id)) == (OperationType.KICAD_PCB_DRC,) * 6
