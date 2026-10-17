from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import embedded_copilot.execution_worker.pcb as pcb_module
import embedded_copilot.pcb_production.adapters as footprint_adapters
from embedded_copilot.execution_worker import (
    discover_kicad_footprint_root,
    discover_kicad_pcb_drc_command,
    discover_kicad_pcb_parse_command,
)
from embedded_copilot.execution_worker.pcb import KiCadCliPCBDrCAdapter
from embedded_copilot.pcb_production import KiCadFootprintAvailability


def test_kicad_footprint_root_uses_explicit_configured_library(tmp_path: Path) -> None:
    root = tmp_path / "footprints"
    library = root / "Resistor_SMD.pretty"
    library.mkdir(parents=True)
    (library / "R_0603_1608Metric.kicad_mod").write_text("(footprint)", encoding="utf-8")
    executable = tmp_path / "kicad-cli.exe"
    executable.write_bytes(b"trusted-test-placeholder")

    discovered = discover_kicad_footprint_root(str(executable), configured_root=str(root))

    assert discovered == root.resolve()
    assert KiCadFootprintAvailability(discovered).is_available("Resistor_SMD:R_0603_1608Metric")
    assert not KiCadFootprintAvailability(discovered).is_available("Resistor_SMD:../secret")


def test_kicad_footprint_root_fails_closed_when_library_is_missing(tmp_path: Path) -> None:
    executable = tmp_path / "kicad-cli.exe"
    executable.write_bytes(b"trusted-test-placeholder")

    assert discover_kicad_footprint_root(str(executable), configured_root=str(tmp_path / "missing")) is None


def test_kicad_footprint_lookups_reuse_unchanged_definition(
    monkeypatch, tmp_path: Path
) -> None:
    root = tmp_path / "footprints"
    library = root / "Test.pretty"
    library.mkdir(parents=True)
    (library / "Demo.kicad_mod").write_text(
        '(footprint "Demo" (pad "1" thru_hole circle (at 0 0) (size 1 1) (drill 0.5) (layers "*.Cu" "*.Mask")))',
        encoding="utf-8",
    )
    adapter = KiCadFootprintAvailability(root)
    reads = 0
    original = footprint_adapters._read_footprint_definition

    def counted_read(footprint_root: Path, binding: str) -> str:
        nonlocal reads
        reads += 1
        return original(footprint_root, binding)

    monkeypatch.setattr(footprint_adapters, "_read_footprint_definition", counted_read)
    placement = SimpleNamespace(x_mm=10.0, y_mm=20.0, rotation_deg=0.0)

    assert adapter.pad_world_position(placement, "Test:Demo", "1") == (10.0, 20.0)
    assert adapter.pad_world_positions(placement, "Test:Demo", "1") == ((10.0, 20.0),)
    assert adapter.pad_is_through_hole("Test:Demo", "1") is True
    assert adapter.pad_is_through_hole("Test:Demo", "1") is True
    assert reads == 1

    (library / "Demo.kicad_mod").write_text(
        '(footprint "Demo" (pad "1" smd round (at 0 0) (size 1 1) (layers "F.Cu" "F.Paste" "F.Mask")))',
        encoding="utf-8",
    )
    assert adapter.pad_is_through_hole("Test:Demo", "1") is False
    assert reads == 2


def test_kicad_drc_discovery_requests_json_report(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "kicad-cli.exe"
    board = tmp_path / "main.kicad_pcb"
    report = tmp_path / "drc.json"
    executable.write_bytes(b"trusted-test-placeholder")
    observed: list[tuple[str, ...]] = []

    def fake_run(argv: tuple[str, ...], *, cwd: Path, timeout_seconds: float) -> object:
        observed.append(argv)
        return type("Completed", (), {
            "stdout": b"Usage: pcb drc [--output OUTPUT] [--format FORMAT] [--severity-error] [--severity-warning] [--exit-code-violations] options: json, report",
            "stderr": b"",
        })()

    monkeypatch.setattr(pcb_module, "run_kicad_cli", fake_run)
    builder = discover_kicad_pcb_drc_command(str(executable), cwd=tmp_path)

    assert builder is not None
    assert observed == [(str(executable), "pcb", "drc", "--help")]
    assert builder(str(executable), board, report) == (
        str(executable), "pcb", "drc", "--output", str(report),
        "--format", "json", "--severity-error", "--severity-warning", "--exit-code-violations", str(board),
    )


def test_kicad_drc_report_preserves_raw_identity_and_structured_issue(tmp_path: Path) -> None:
    report = tmp_path / "drc.json"
    report.write_text(
        json.dumps(
            {
                "violations": [
                    {
                        "type": "clearance",
                        "severity": "error",
                        "description": "clearance violation",
                        "items": [{"footprint": "R1", "pad": "1", "pos": {"x": 12.5, "y": 8.0}}],
                    }
                ],
                "warnings": [],
            }
        ),
        encoding="utf-8",
    )
    violations, warnings = KiCadCliPCBDrCAdapter._parse_report(report)
    issues = KiCadCliPCBDrCAdapter._issues(
        violations,
        SimpleNamespace(operation_id="drc-operation", pcb_content_fingerprint="sha256:" + "1" * 64),
        "artifact://pcb-drc/report/drc-operation",
    )

    assert warnings == ()
    assert issues[0].severity.value == "ERROR"
    assert issues[0].rule_id == "clearance"
    assert issues[0].footprint_ref == "R1"
    assert issues[0].pad_ref == "1"
    assert issues[0].location == (12.5, 8.0)
    assert issues[0].classification.value == "PCB_PHYSICAL_DESIGN_DEFECT"
    assert issues[0].raw_evidence_ref.endswith("/item/0")


def test_kicad_drc_report_parses_real_schema_categories(tmp_path: Path) -> None:
    report = tmp_path / "drc.json"
    report.write_text(
        json.dumps(
            {
                "$schema": "https://schemas.kicad.org/drc.v1.json",
                "source": "main.kicad_pcb",
                "date": "2026-09-17T00:00:00Z",
                "kicad_version": "10.0.5",
                "violations": [],
                "unconnected_items": [
                    {
                        "type": "unconnected_items",
                        "description": "unconnected item",
                        "severity": "error",
                        "items": [
                            {
                                "uuid": "00000000-0000-0000-0000-000000000001",
                                "description": "R1 pad 1",
                                "pos": {"x": 12.5, "y": 8.0},
                            }
                        ],
                    }
                ],
                "schematic_parity": [
                    {
                        "type": "lib_footprint_mismatch",
                        "description": "footprint mismatch",
                        "severity": "warning",
                        "items": [],
                    }
                ],
                "coordinate_units": "mm",
                "included_severities": ["error", "warning"],
                "ignored_checks": [],
            }
        ),
        encoding="utf-8",
    )
    violations, warnings = KiCadCliPCBDrCAdapter._parse_report(report)
    assert len(violations) == 2
    assert warnings == ()
    assert violations[0].rule == "unconnected_items"
    assert violations[0].object_refs == ("uuid:00000000-0000-0000-0000-000000000001", "location:12.5,8.0")
    assert violations[1].rule == "lib_footprint_mismatch"


def test_kicad_drc_report_rejects_schema_empty_report(tmp_path: Path) -> None:
    report = tmp_path / "drc.json"
    report.write_text("{}", encoding="utf-8")

    assert KiCadCliPCBDrCAdapter._parse_report(report) is None


def test_kicad_pcb_parse_discovery_requires_read_only_export_command(monkeypatch, tmp_path: Path) -> None:
    executable = tmp_path / "kicad-cli.exe"
    board = tmp_path / "main.kicad_pcb"
    output = tmp_path / "gerbers"
    executable.write_bytes(b"trusted-test-placeholder")
    observed: list[tuple[str, ...]] = []

    def fake_run(argv: tuple[str, ...], *, cwd: Path, timeout_seconds: float) -> object:
        observed.append(argv)
        return type("Completed", (), {
            "stdout": b"Usage: pcb export gerbers --output OUTPUT input.kicad_pcb",
            "stderr": b"",
        })()

    monkeypatch.setattr(pcb_module, "run_kicad_cli", fake_run)
    builder = discover_kicad_pcb_parse_command(str(executable), cwd=tmp_path)

    assert builder is not None
    assert observed == [(str(executable), "pcb", "export", "gerbers", "--help")]
    assert builder(str(executable), board, output) == (
        str(executable), "pcb", "export", "gerbers", "--output", str(output), str(board),
    )
