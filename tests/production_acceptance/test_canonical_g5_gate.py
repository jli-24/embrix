from __future__ import annotations

from pathlib import Path

import pytest

from scripts.bootstrap_canonical_golden_project import bootstrap
from tests.production_acceptance.kicad_toolchain import discover_test_kicad_cli, discover_test_symbol_root


def test_canonical_g5_gate_stops_after_real_pcb_drc(tmp_path: Path) -> None:
    executable = discover_test_kicad_cli()
    symbol_dir = None if executable is None else discover_test_symbol_root(executable)
    if executable is None or symbol_dir is None:
        pytest.skip("real KiCad CLI, footprint library, and symbol library are required for the G5 acceptance test")

    report = bootstrap(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "catalog.sqlite3",
        executable=str(executable),
        stop_after_g5=True,
    )

    assert report["report_label"] == "EMBRIX_G5V_REAL_PCB_DRC_VALIDATION_REPORT"
    assert report["g4_erc_gate"] == "PASS"
    assert report["g5_production_status"] == "DRC_PASS"
    assert report["g5_parse_validation"] == "PARSE_VERIFIED"
    assert report["g5_drc_gate"] == "PASS"
    assert report["g5_stop_before_firmware"] is True
    assert report["simulation_runtime_entered"] is False
