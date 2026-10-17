from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from embedded_copilot.execution_worker import KiCadCliSchematicErcAdapter
from embedded_copilot.execution_worker.contracts import SchematicErcOperation

FP = "sha256:" + "a" * 64


def _operation(*, artifact_fingerprint: str = FP) -> SchematicErcOperation:
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 9, 17, tzinfo=UTC)
    return SchematicErcOperation(
        operation_id="erc-operation-g4",
        tenant_id="tenant-g4",
        project_id="project-g4",
        requester_id="engineer-g4",
        target_id="project-root",
        target_fingerprint=FP,
        workspace_snapshot_id="snapshot-g4",
        workspace_fingerprint=FP,
        proposal_id="proposal-g4",
        proposal_fingerprint=FP,
        operation_type="KICAD_SCHEMATIC_ERC",
        created_at=now,
        expires_at=now + timedelta(minutes=15),
        nonce="nonce-g4",
        conversation_id="conversation-g4",
        schematic_artifact_ref="hardware/main.kicad_sch",
        schematic_artifact_fingerprint=artifact_fingerprint,
        source_operation_id="write-operation-g4",
        source_operation_receipt_fingerprint=FP,
        target_relative_path="hardware/main.kicad_sch",
    )


def test_g4_adapter_preserves_raw_report_and_projects_warning_issues(tmp_path: Path) -> None:
    target = tmp_path / "hardware" / "main.kicad_sch"
    target.parent.mkdir()
    target.write_text("(kicad_sch)", encoding="utf-8")
    artifact_fingerprint = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()

    def runner(argv, **kwargs):
        report_path = Path(argv[argv.index("--output") + 1])
        report_path.write_text(
            "[lib_symbol_issues]: Symbol library is unavailable\n"
            "    ; warning\n"
            "    @(10.00 mm, 20.00 mm): Symbol U1 Pin 1\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 5, stdout=b"", stderr=b"")

    result = KiCadCliSchematicErcAdapter(
        executable="kicad-cli",
        workspace_resolver=lambda operation: tmp_path,
        runner=runner,
    ).execute(_operation(artifact_fingerprint=artifact_fingerprint))

    assert result.status == "SUCCEEDED"
    erc = result.schematic_erc_result
    assert erc.engineering_status.value == "WARNINGS_REMAIN"
    assert erc.status.value == "ERC_WARNINGS"
    assert len(erc.issues) == 1
    assert erc.issues[0].severity.value == "WARNING"
    assert erc.issues[0].rule_id == "lib_symbol_issues"
    assert erc.raw_evidence_fingerprint.startswith("sha256:")
    assert "Symbol library is unavailable" in erc.raw_evidence


def test_g4_adapter_rejects_source_drift_after_real_tool_returns(tmp_path: Path) -> None:
    target = tmp_path / "hardware" / "main.kicad_sch"
    target.parent.mkdir()
    target.write_text("(kicad_sch)", encoding="utf-8")

    def runner(argv, **kwargs):
        target.write_text("(kicad_sch changed)", encoding="utf-8")
        report_path = Path(argv[argv.index("--output") + 1])
        report_path.write_text("** ERC messages: 0  Errors 0  Warnings 0\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    result = KiCadCliSchematicErcAdapter(
        executable="kicad-cli",
        workspace_resolver=lambda operation: tmp_path,
        runner=runner,
    ).execute(_operation())

    assert result.status == "FAILED"
    assert result.summary == "KICAD_SCHEMATIC_WORKSPACE_DRIFT"
    assert result.schematic_erc_result.status.value == "ERC_BLOCKED"
