from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from embedded_copilot.execution_worker import KiCadCliSchematicParseAdapter


def test_parse_adapter_uses_netlist_export_without_mutating_source(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "hardware" / "main.kicad_sch"
    artifact.parent.mkdir()
    artifact.write_text(
        '(kicad_sch (version 20231120) (generator "eeschema"))\n', encoding="utf-8"
    )
    before = artifact.read_bytes()
    calls: list[tuple[str, ...]] = []

    def runner(argv: tuple[str, ...], *, cwd: Path, timeout_seconds: float) -> object:
        del cwd, timeout_seconds
        calls.append(argv)
        output = Path(argv[argv.index("--output") + 1])
        output.write_text("(exported-netlist)\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout=b"parsed", stderr=b"")

    evidence = KiCadCliSchematicParseAdapter(
        executable="kicad-cli.exe",
        kicad_version="10.0.5",
        runner=runner,
    ).execute(
        project_id="project-1",
        conversation_id="conversation-1",
        operation_id="operation-1",
        artifact=artifact,
    )

    assert evidence.validation_status == "PARSE_VERIFIED"
    assert evidence.exit_code == 0
    assert evidence.artifact_unchanged is True
    assert evidence.artifact_fingerprint.startswith("sha256:")
    assert evidence.kicad_version == "10.0.5"
    assert evidence.command_identity == "kicad-cli sch export netlist"
    assert calls and calls[0][1:4] == ("sch", "export", "netlist")
    assert str(artifact) in calls[0]
    assert artifact.read_bytes() == before


def test_parse_adapter_returns_failed_evidence_for_nonzero_cli(tmp_path: Path) -> None:
    artifact = tmp_path / "main.kicad_sch"
    artifact.write_text("not-a-schematic", encoding="utf-8")

    def runner(argv: tuple[str, ...], *, cwd: Path, timeout_seconds: float) -> object:
        del argv, cwd, timeout_seconds
        return SimpleNamespace(returncode=2, stdout=b"", stderr=b"invalid schematic")

    evidence = KiCadCliSchematicParseAdapter(
        executable="kicad-cli",
        kicad_version="10.0.5",
        runner=runner,
    ).execute(
        project_id="project-1",
        conversation_id="conversation-1",
        operation_id="operation-1",
        artifact=artifact,
    )

    assert evidence.validation_status == "FAILED"
    assert evidence.exit_code == 2
    assert evidence.artifact_unchanged is True
