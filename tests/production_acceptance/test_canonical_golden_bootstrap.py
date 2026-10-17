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


def test_canonical_golden_bootstrap_is_restart_stable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kicad_cli = discover_test_kicad_cli()
    symbol_dir = None if kicad_cli is None else discover_test_symbol_root(kicad_cli)
    if kicad_cli is None or symbol_dir is None:
        pytest.skip("real KiCad CLI and symbol library are required for the production Golden acceptance test")
    monkeypatch.setenv("EMBRIX_KICAD_CLI", str(kicad_cli))
    monkeypatch.setenv("KICAD_SYMBOL_DIR", str(symbol_dir))

    project_root = tmp_path / "projects"
    state_database = tmp_path / "catalog.sqlite3"
    first = bootstrap(project_root=project_root, state_database=state_database, executable=str(kicad_cli))
    golden_root = project_root / "Minimal MCU Control Core"
    first_hashes = _file_hashes(golden_root)
    second = bootstrap(project_root=project_root, state_database=state_database, executable=str(kicad_cli))

    for key in (
        "project_id", "conversation_id", "design_truth_id", "design_truth_fingerprint",
        "firmware_spec_id", "firmware_spec_fingerprint", "implementation_id",
        "implementation_fingerprint", "golden_facts_fingerprint",
    ):
        assert second[key] == first[key]
    assert first["build_readiness"]["status"] == "BLOCKED_TOOLCHAIN_UNAVAILABLE"
    assert first["erc_receipt"]["erc"] == "ERC_PASS"
    assert first["drc_receipt"]["drc"] == "DRC_PASS"
    assert second["build_readiness"]["status"] == "BLOCKED_TOOLCHAIN_UNAVAILABLE"
    assert second["artifacts"] == first["artifacts"]
    assert _file_hashes(golden_root) == first_hashes

    runtime = ProjectCatalogRuntime(
        project_root=project_root,
        state_database=state_database,
        initialize_async=False,
        production_mode=True,
    )
    project = runtime.projects(TENANT_ID)[0]
    detail = runtime.conversation(project.project_id, first["conversation_id"], tenant_id=TENANT_ID, requester=REQUESTER)
    assert detail.firmware_spec is not None
    assert detail.firmware_spec.mcu_exact_part == "STM32F103C8T6"
    assert detail.firmware_spec.firmware_framework == "CMSIS + Minimal Bare-metal"
    assert detail.firmware_spec.design_truth_id == detail.design_truth.truth_id
    assert detail.implementation_spec is not None
    assert detail.build_configuration is not None
    assert detail.implementation_spec.build_configuration_id is not None
    assert detail.implementation_spec.build_configuration_fingerprint is not None
    assert detail.build_configuration.toolchain_id == "ARM_NONE_EABI_GCC"
    assert detail.build_configuration.toolchain_version == "UNAVAILABLE"
    assert (golden_root / "hardware/main.kicad_sch").is_file()
    assert (golden_root / "hardware/main.kicad_pcb").is_file()
    assert (golden_root / "hardware/Minimal_MCU_Control_Core_BOM.csv").is_file()
    assert (golden_root / "generated/minimal_mcu_control_core.c").is_file()
    assert (golden_root / "generated/minimal_mcu_control_core.h").is_file()
