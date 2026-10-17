from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from embedded_copilot.engineering_workspace_apply import (
    FixedWorkspaceTargetResolver,
    SQLiteWorkspaceApplyStore,
    WorkspaceApplyService,
)
from embedded_copilot.execution_worker import (
    ExecutionOperationService,
    ExecutionWorker,
    KiCadCliPCBParseAdapter,
    OperationStatus,
    OperationType,
    PCBWriteAdapter,
    SQLiteExecutionStore,
)
from embedded_copilot.pcb_production import (
    PCBParseValidationEvidence,
    PCBParseValidationStatus,
    PCBProductionService,
    PCBProposalDecisionStatus,
)
from embedded_copilot.pcb_production.execution import PCBExecutionBridge

from .test_service import _Availability, _request


NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return NOW


def _write_fixture(tmp_path: Path):
    request = _request()
    root = tmp_path / "project"
    root.mkdir()
    (root / "seed.txt").write_text("seed", encoding="utf-8")
    workspace = WorkspaceApplyService(
        resolver=FixedWorkspaceTargetResolver(
            root,
            tenant_id="tenant-golden",
            project_id=request.project_id,
        ),
        store=SQLiteWorkspaceApplyStore(tmp_path / "workspace.sqlite3"),
        state_root=tmp_path / "workspace-state",
        clock=_Clock(),
        approval_ttl=timedelta(minutes=15),
    )
    execution = ExecutionOperationService(
        store=SQLiteExecutionStore(tmp_path / "execution.sqlite3"),
        clock=lambda: NOW,
    )
    proposal = PCBProductionService(footprints=_Availability()).create_proposal(request)
    from embedded_copilot.pcb_production import decide_pcb_proposal

    decision = decide_pcb_proposal(
        proposal,
        decision=PCBProposalDecisionStatus.APPROVED,
        reviewer=request.requester,
        decided_at=NOW,
    )
    bridge = PCBExecutionBridge(workspace=workspace, execution=execution, clock=lambda: NOW)
    write = bridge.approve_and_enqueue(
        production_request=request,
        proposal=proposal,
        decision=decision,
        tenant_id="tenant-golden",
    )
    worker = ExecutionWorker(
        worker_id="phase5-completion-worker",
        store=execution.store,
        registry=None,
        workspace_resolver=None,
        targets=None,
        pcb_write=PCBWriteAdapter(workspace),
        allowed_operation_types=(OperationType.KICAD_PCB_WRITE,),
        clock=lambda: NOW,
    )
    assert worker.run_once().status is OperationStatus.SUCCEEDED
    receipt = execution.get_receipt(
        tenant_id="tenant-golden",
        project_id=request.project_id,
        operation_id=write.operation.operation_id,
    )
    assert receipt is not None
    return request, root, execution, bridge, write, receipt


def test_parse_failure_cannot_enqueue_drc(tmp_path: Path) -> None:
    request, _root, _execution, bridge, write, receipt = _write_fixture(tmp_path)
    failed = PCBParseValidationEvidence.create(
        validation_id="phase5-parse-failed",
        project_id=request.project_id,
        conversation_id=request.conversation_id,
        operation_id=write.operation.operation_id,
        artifact_path=request.target_relative_path,
        artifact_fingerprint=write.operation.content_fingerprint,
        kicad_cli_path="C:/KiCad/bin/kicad-cli.exe",
        kicad_version="10.0.5",
        command_identity="kicad-cli pcb export gerbers (parse validation)",
        exit_code=1,
        validation_status=PCBParseValidationStatus.FAILED,
        artifact_unchanged=True,
        validated_at=NOW,
    )

    with pytest.raises(ValueError, match="parse-verified"):
        bridge.enqueue_drc(
            write_operation=write.operation,
            receipt_fingerprint=receipt.fingerprint,
            reviewer_id=request.requester,
            parse_evidence=failed,
        )


def test_parse_adapter_binds_write_to_source_artifact_before_drc(tmp_path: Path) -> None:
    request, root, _execution, _bridge, write, _receipt = _write_fixture(tmp_path)
    executable = tmp_path / "kicad-cli.exe"
    executable.write_bytes(b"trusted test executable")

    def command_builder(executable_path: str, board_path: Path, output_directory: Path) -> tuple[str, ...]:
        return (
            executable_path,
            "pcb",
            "export",
            "gerbers",
            "--output",
            str(output_directory),
            str(board_path),
        )

    def runner(argv: tuple[str, ...], cwd: Path, timeout: int):
        output_directory = Path(argv[argv.index("--output") + 1])
        output_directory.mkdir(parents=True)
        (output_directory / "main-F_Cu.gbr").write_text("gerber", encoding="utf-8")
        return type("Completed", (), {"returncode": 0, "stdout": b"", "stderr": b""})()

    evidence = KiCadCliPCBParseAdapter(
        executable=str(executable),
        workspace_resolver=lambda operation: root,
        command_builder=command_builder,
        kicad_version="10.0.5",
        runner=runner,
        clock=lambda: NOW,
    ).execute(write.operation)

    assert evidence.validation_status is PCBParseValidationStatus.PARSE_VERIFIED
    assert evidence.operation_id == write.operation.operation_id
    assert evidence.artifact_fingerprint == write.operation.content_fingerprint


def test_parse_evidence_identity_mismatch_is_not_current(tmp_path: Path) -> None:
    request, _root, _execution, bridge, write, receipt = _write_fixture(tmp_path)
    mismatched = PCBParseValidationEvidence.create(
        validation_id="phase5-parse-mismatch",
        project_id=request.project_id,
        conversation_id=request.conversation_id,
        operation_id=write.operation.operation_id,
        artifact_path=request.target_relative_path,
        artifact_fingerprint="sha256:" + "9" * 64,
        kicad_cli_path="C:/KiCad/bin/kicad-cli.exe",
        kicad_version="10.0.5",
        command_identity="kicad-cli pcb export gerbers (parse validation)",
        exit_code=0,
        validation_status=PCBParseValidationStatus.PARSE_VERIFIED,
        artifact_unchanged=True,
        validated_at=NOW,
    )

    with pytest.raises(ValueError, match="parse-verified"):
        bridge.enqueue_drc(
            write_operation=write.operation,
            receipt_fingerprint=receipt.fingerprint,
            reviewer_id=request.requester,
            parse_evidence=mismatched,
        )
