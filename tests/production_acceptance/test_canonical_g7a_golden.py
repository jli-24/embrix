from __future__ import annotations

from pathlib import Path

import pytest

from embedded_copilot.project_catalog import ProjectCatalogRuntime
from scripts.bootstrap_canonical_golden_project import REQUESTER, TENANT_ID, bootstrap
from tests.production_acceptance.kicad_toolchain import (
    discover_test_kicad_cli,
    discover_test_symbol_root,
)


def test_canonical_g7a_projects_the_real_g5_artifact_and_survives_restart(tmp_path: Path) -> None:
    executable = discover_test_kicad_cli()
    symbol_dir = None if executable is None else discover_test_symbol_root(executable)
    if executable is None or symbol_dir is None:
        pytest.skip("real KiCad CLI, footprint library, and symbol library are required for the G7-A acceptance test")

    report = bootstrap(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "catalog.sqlite3",
        executable=str(executable),
        stop_after_g5=True,
    )
    assert report["g5_parse_validation"] == "PARSE_VERIFIED"
    assert report["g5_production_status"] == "DRC_PASS"
    project_id = report["project_id"]
    conversation_id = report["conversation_id"]

    runtime = ProjectCatalogRuntime(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "catalog.sqlite3",
        initialize_async=False,
    )
    projected = runtime.refresh_pcb_engineering_state(
        project_id,
        conversation_id,
        tenant_id=TENANT_ID,
        requester=REQUESTER,
    )

    assert projected.pcb_engineering_state is not None
    assert projected.pcb_constraint_set is not None
    assert projected.pcb_verification_plan is not None
    assert projected.pcb_engineering_state.source_artifact.filename == "main.kicad_pcb"
    assert projected.pcb_engineering_state.source_artifact.content_fingerprint == report["pcb_sha256"]
    assert projected.pcb_engineering_state.components
    assert projected.pcb_engineering_state.footprints
    assert projected.pcb_engineering_state.nets
    assert projected.pcb_engineering_state.power_nets
    assert projected.pcb_engineering_state.signal_classes
    assert projected.pcb_engineering_state.placement
    assert projected.pcb_engineering_state.routing
    assert projected.pcb_engineering_state.planes
    assert projected.pcb_engineering_state.vias
    assert projected.pcb_engineering_state.unknowns
    assert projected.pcb_constraint_set.state_fingerprint == projected.pcb_engineering_state.fingerprint
    assert projected.pcb_verification_plan.constraint_set_fingerprint == projected.pcb_constraint_set.fingerprint
    assert projected.pcb_verification_plan.required_verifiers
    assert "DifferentialPairVerifier" in projected.pcb_verification_plan.not_applicable_verifiers

    restarted = ProjectCatalogRuntime(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "catalog.sqlite3",
        initialize_async=False,
    )
    restored = restarted.conversation(
        project_id,
        conversation_id,
        tenant_id=TENANT_ID,
        requester=REQUESTER,
    )
    assert restored.pcb_engineering_state is not None
    assert restored.pcb_constraint_set is not None
    assert restored.pcb_verification_plan is not None
    assert restored.pcb_engineering_state.fingerprint == projected.pcb_engineering_state.fingerprint
    assert restored.pcb_constraint_set.fingerprint == projected.pcb_constraint_set.fingerprint
    assert restored.pcb_verification_plan.fingerprint == projected.pcb_verification_plan.fingerprint
