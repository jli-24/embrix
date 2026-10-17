from datetime import UTC, datetime, timedelta
from pathlib import Path
import hashlib
import subprocess
import pytest

from embedded_copilot.execution_worker import (
    KiCadCliSchematicErcAdapter,
    OperationType,
    probe_kicad_cli_version,
    SchematicSymbolLibraryValidator,
    SchematicErcOperation,
)

FP = "sha256:" + "a" * 64
NOW = datetime(2026, 9, 5, tzinfo=UTC)


def test_probe_kicad_cli_version_accepts_only_reported_version(monkeypatch) -> None:
    def runner(argv, **kwargs):
        assert argv == ("kicad-cli", "--version")
        return subprocess.CompletedProcess(argv, 0, stdout=b"10.0.5\n", stderr=b"")

    from embedded_copilot.execution_worker import adapters

    monkeypatch.setattr(adapters, "run_kicad_cli", runner)
    assert probe_kicad_cli_version("kicad-cli", cwd=Path(".")) == "10.0.5"


def test_real_kicad_adapter_rejects_unresolved_executable_without_injected_runner() -> None:
    with pytest.raises(ValueError, match="trusted"):
        KiCadCliSchematicErcAdapter(
            executable="kicad-cli.exe",
            workspace_resolver=lambda operation: Path("."),
        )


def _operation(*, artifact_fingerprint: str = FP) -> SchematicErcOperation:
    return SchematicErcOperation(
        operation_id="erc-operation-1",
        tenant_id="tenant-1",
        project_id="project-1",
        requester_id="engineer-1",
        target_id="project-root",
        target_fingerprint=FP,
        workspace_snapshot_id="snapshot-1",
        workspace_fingerprint=FP,
        proposal_id="proposal-1",
        proposal_fingerprint=FP,
        operation_type=OperationType.KICAD_SCHEMATIC_ERC,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
        nonce="nonce-1",
        conversation_id="conversation-1",
        schematic_artifact_ref="hardware/main.kicad_sch",
        schematic_artifact_fingerprint=artifact_fingerprint,
        source_operation_id="write-operation-1",
        source_operation_receipt_fingerprint=FP,
        target_relative_path="hardware/main.kicad_sch",
    )


def test_kicad_cli_adapter_projects_explicit_pass_without_guessing(tmp_path: Path) -> None:
    target = tmp_path / "hardware" / "main.kicad_sch"
    target.parent.mkdir()
    target.write_text("(kicad_sch)", encoding="utf-8")
    artifact_fingerprint = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()

    def runner(argv, **kwargs):
        assert argv[3] == "hardware/main.kicad_sch"
        assert not Path(argv[3]).is_absolute()
        assert Path(kwargs["cwd"]).resolve() == tmp_path.resolve()
        Path(argv[argv.index("--output") + 1]).write_text(
            "ERC report (2026-09-05T18:20:39, Encoding UTF8)\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 0, stdout=b"KiCad 9.0", stderr=b"")

    adapter = KiCadCliSchematicErcAdapter(
        executable="kicad-cli.exe",
        workspace_resolver=lambda operation: tmp_path,
        runner=runner,
    )
    result = adapter.execute(_operation(artifact_fingerprint=artifact_fingerprint))
    assert result.status == "SUCCEEDED"
    assert result.schematic_erc_result.status.value == "ERC_PASS"
    assert result.schematic_erc_result.violations == ()
    assert result.schematic_erc_result.kicad_version == "KiCad 9.0"


def test_kicad_cli_adapter_only_accepts_explicit_structured_violation(tmp_path: Path) -> None:
    target = tmp_path / "hardware" / "main.kicad_sch"
    target.parent.mkdir()
    target.write_text("(kicad_sch)", encoding="utf-8")
    artifact_fingerprint = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()

    def runner(argv, **kwargs):
        report_path = Path(argv[argv.index("--output") + 1])
        report_path.write_text(
            "[error] rule=PIN_NOT_CONNECTED message=explicit diagnostic\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    result = KiCadCliSchematicErcAdapter(
        executable="kicad-cli",
        workspace_resolver=lambda operation: tmp_path,
        runner=runner,
    ).execute(_operation(artifact_fingerprint=artifact_fingerprint))
    assert result.status == "SUCCEEDED"
    erc = result.schematic_erc_result
    assert erc.status.value == "ERC_FAIL"
    assert erc.violations[0].severity == "ERROR"
    assert len(result.evidence_references) == 1
    assert result.evidence_references[0].startswith("erc-evidence-erc-operation-1-")


def test_kicad_cli_adapter_parses_real_report_blocks(tmp_path: Path) -> None:
    target = tmp_path / "hardware" / "main.kicad_sch"
    target.parent.mkdir()
    target.write_text("(kicad_sch)", encoding="utf-8")
    artifact_fingerprint = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()

    def runner(argv, **kwargs):
        report_path = Path(argv[argv.index("--output") + 1])
        report_path.write_text(
            "ERC report (2026-09-05T18:20:39, Encoding UTF8)\n"
            "***** Sheet /\n"
            "[pin_not_connected]: Pin not connected\n"
            "    ; error\n"
            "    @(10.00 mm, 20.00 mm): Symbol U1 Pin 1\n"
            "[endpoint_off_grid]: Endpoint off grid\n"
            "    ; warning\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 5, stdout=b"", stderr=b"")

    result = KiCadCliSchematicErcAdapter(
        executable="kicad-cli",
        workspace_resolver=lambda operation: tmp_path,
        runner=runner,
    ).execute(_operation(artifact_fingerprint=artifact_fingerprint))
    assert result.status == "SUCCEEDED"
    assert result.schematic_erc_result.status.value == "ERC_FAIL"
    assert [(item.rule, item.severity) for item in result.schematic_erc_result.violations] == [
        ("pin_not_connected", "ERROR"),
    ]
    assert result.schematic_erc_result.warnings == ("WARNING: endpoint_off_grid: Endpoint off grid",)


def test_kicad_cli_adapter_passes_with_only_non_blocking_warning(tmp_path: Path) -> None:
    target = tmp_path / "hardware" / "main.kicad_sch"
    target.parent.mkdir()
    target.write_text("(kicad_sch)", encoding="utf-8")
    artifact_fingerprint = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()

    def runner(argv, **kwargs):
        report_path = Path(argv[argv.index("--output") + 1])
        report_path.write_text(
            "[endpoint_off_grid]: Endpoint off grid\n    ; warning\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    result = KiCadCliSchematicErcAdapter(
        executable="kicad-cli",
        workspace_resolver=lambda operation: tmp_path,
        runner=runner,
    ).execute(_operation(artifact_fingerprint=artifact_fingerprint))
    assert result.status == "SUCCEEDED"
    assert result.schematic_erc_result.status.value == "ERC_WARNINGS"
    assert result.schematic_erc_result.violations == ()
    assert result.schematic_erc_result.warnings == ("WARNING: endpoint_off_grid: Endpoint off grid",)


def test_kicad_cli_adapter_blocks_workspace_content_drift(tmp_path: Path) -> None:
    target = tmp_path / "hardware" / "main.kicad_sch"
    target.parent.mkdir()
    target.write_text("(kicad_sch)", encoding="utf-8")

    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    result = KiCadCliSchematicErcAdapter(
        executable="kicad-cli",
        workspace_resolver=lambda operation: tmp_path,
        runner=runner,
    ).execute(_operation(artifact_fingerprint=FP))
    assert result.status == "FAILED"
    assert result.summary == "KICAD_SCHEMATIC_WORKSPACE_DRIFT"
    assert result.schematic_erc_result.status.value == "ERC_BLOCKED"
    assert result.evidence_references == ()
    assert calls == []


def test_kicad_cli_adapter_reports_workspace_binding_rejection_as_blocked(tmp_path: Path) -> None:
    target = tmp_path / "hardware" / "main.kicad_sch"
    target.parent.mkdir()
    target.write_text("(kicad_sch)", encoding="utf-8")
    artifact_fingerprint = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()

    result = KiCadCliSchematicErcAdapter(
        executable="kicad-cli",
        workspace_resolver=lambda operation: tmp_path / "missing-workspace",
        runner=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("runner must not be called")),
    ).execute(_operation(artifact_fingerprint=artifact_fingerprint))

    assert result.status == "FAILED"
    assert result.summary == "KICAD_ERC_BLOCKED"
    assert result.schematic_erc_result.status.value == "ERC_BLOCKED"
    assert result.evidence_references == ()


def test_symbol_library_validator_requires_explicit_current_library_binding(tmp_path: Path, monkeypatch) -> None:
    library_root = tmp_path / "symbols"
    library_root.mkdir()
    (library_root / "Device.kicad_sym").write_text(
        '(kicad_symbol_lib (version 20231120) (generator "eeschema") (symbol "R"))',
        encoding="utf-8",
    )
    (tmp_path / "sym-lib-table").write_text(
        '(sym_lib_table (version 7) (lib (name "Device") (type "KiCad") '
        '(uri "${KICAD_SYMBOL_DIR}/Device.kicad_sym") (options "") (descr "")))',
        encoding="utf-8",
    )
    monkeypatch.setenv("KICAD_SYMBOL_DIR", str(library_root))
    validator = SchematicSymbolLibraryValidator(lambda **kwargs: tmp_path)
    validator(tenant_id="tenant-1", project_id="project-1", target_id="project-root", bindings=("Device:R",))
    with pytest.raises(ValueError, match="BLOCKED_KICAD_SYMBOL_UNAVAILABLE"):
        validator(tenant_id="tenant-1", project_id="project-1", target_id="project-root", bindings=("Device:C",))


def test_symbol_library_validator_uses_trusted_kicad_symbol_dir_without_table(tmp_path: Path, monkeypatch) -> None:
    library_root = tmp_path / "symbols"
    library_root.mkdir()
    (library_root / "Device.kicad_sym").write_text(
        '(kicad_symbol_lib (version 20231120) (generator "eeschema") (symbol "R"))',
        encoding="utf-8",
    )
    monkeypatch.setenv("KICAD_SYMBOL_DIR", str(library_root))
    validator = SchematicSymbolLibraryValidator(lambda **kwargs: tmp_path)

    validator(tenant_id="tenant-1", project_id="project-1", target_id="project-root", bindings=("Device:R",))
    with pytest.raises(ValueError, match="BLOCKED_KICAD_SYMBOL_UNAVAILABLE"):
        validator(tenant_id="tenant-1", project_id="project-1", target_id="project-root", bindings=("Device:C",))
