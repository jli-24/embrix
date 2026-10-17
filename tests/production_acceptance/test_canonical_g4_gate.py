from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from embedded_copilot.project_catalog import ProjectCatalogRuntime
from scripts.bootstrap_canonical_golden_project import REQUESTER, TENANT_ID, bootstrap
from tests.production_acceptance.kicad_toolchain import discover_test_kicad_cli, discover_test_symbol_root


def _file_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file() and ".embedded-copilot-state" not in path.relative_to(root).parts
    }


def test_canonical_g4_gate_stops_after_real_erc_and_recovers_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = discover_test_kicad_cli()
    symbol_dir = None if executable is None else discover_test_symbol_root(executable)
    if executable is None or symbol_dir is None:
        pytest.skip("real KiCad CLI and symbol library are required for the G4 acceptance test")
    monkeypatch.setenv("EMBRIX_KICAD_CLI", str(executable))
    monkeypatch.setenv("KICAD_SYMBOL_DIR", str(symbol_dir))

    project_root = tmp_path / "projects"
    state_database = tmp_path / "catalog.sqlite3"
    first = bootstrap(
        project_root=project_root,
        state_database=state_database,
        executable=str(executable),
        stop_after_g4=True,
    )
    golden_root = project_root / "Minimal MCU Control Core"
    first_hashes = _file_hashes(golden_root)

    assert first["g4_erc_gate"] == "PASS"
    assert first["g4_erc_status"] == "ERC_PASS"
    assert first["g4_erc_issue_count"] == 0
    assert first["g4_stop_before_pcb"] is True
    assert not (golden_root / "hardware/main.kicad_pcb").exists()

    second = bootstrap(
        project_root=project_root,
        state_database=state_database,
        executable=str(executable),
        stop_after_g4=True,
    )
    assert second["project_id"] == first["project_id"]
    assert second["conversation_id"] == first["conversation_id"]
    assert second["g4_erc_gate"] == "PASS"
    assert second["g4_erc_status"] == "ERC_PASS"
    assert _file_hashes(golden_root) == first_hashes

    runtime = ProjectCatalogRuntime(
        project_root=project_root,
        state_database=state_database,
        initialize_async=False,
        production_mode=True,
    )
    project = runtime.projects(TENANT_ID)[0]
    detail = runtime.conversation(
        project.project_id,
        first["conversation_id"],
        tenant_id=TENANT_ID,
        requester=REQUESTER,
    )
    assert detail.schematic_production is not None
    assert detail.schematic_production.erc_gate_status.value == "PASS"
    assert detail.schematic_production.erc_receipt is not None
    assert detail.schematic_production.erc_receipt.result.status.value == "ERC_PASS"
    assert detail.schematic_production.generation_receipt is not None
    assert detail.schematic_production.generation_receipt.parse_validation is not None
