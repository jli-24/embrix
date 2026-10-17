from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from embedded_copilot.project_catalog import ProjectCatalogRuntime
from embedded_copilot.verification_loop import VerificationStatus
from scripts.bootstrap_canonical_golden_project import REQUESTER, TENANT_ID, bootstrap
from tests.production_acceptance.kicad_toolchain import (
    discover_test_kicad_cli,
    discover_test_symbol_root,
)


def _artifact_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_canonical_g7b3_power_loop_is_not_applicable_and_survives_restart(
    tmp_path: Path,
) -> None:
    executable = discover_test_kicad_cli()
    symbol_dir = None if executable is None else discover_test_symbol_root(executable)
    if executable is None or symbol_dir is None:
        pytest.skip("real KiCad CLI, footprint library, and symbol library are required for the G7-B3 acceptance test")

    report = bootstrap(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "catalog.sqlite3",
        executable=str(executable),
        stop_after_g5=True,
    )
    assert report["g5_parse_validation"] == "PARSE_VERIFIED"
    assert report["g5_production_status"] == "DRC_PASS"

    runtime = ProjectCatalogRuntime(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "catalog.sqlite3",
        initialize_async=False,
    )
    projected = runtime.refresh_pcb_engineering_state(
        report["project_id"],
        report["conversation_id"],
        tenant_id=TENANT_ID,
        requester=REQUESTER,
    )
    assert projected.pcb_engineering_state is not None
    assert projected.pcb_constraint_set is not None
    assert projected.pcb_verification_plan is not None
    power_constraint = next(
        item for item in projected.pcb_constraint_set.constraints
        if item.constraint_type.value == "SWITCHING_POWER"
    )
    assert power_constraint.applicability.value == "NOT_APPLICABLE"
    assert "PowerLoopVerifier" in projected.pcb_verification_plan.not_applicable_verifiers

    artifact_path = (
        tmp_path
        / "projects"
        / str(report["project_relative_path"])
        / projected.pcb_production.generation_receipt.artifact_path
    )
    before_hash = _artifact_hash(artifact_path)
    verified = runtime.verify_pcb_power_loop(
        report["project_id"],
        report["conversation_id"],
        tenant_id=TENANT_ID,
        requester=REQUESTER,
    )
    result = verified.pcb_power_loop_verification
    assert result is not None
    assert result.verifier == "POWER_LOOP"
    assert result.scope == "PCB"
    assert result.status is VerificationStatus.NOT_APPLICABLE
    assert result.applicability == "NOT_APPLICABLE"
    assert result.compared_fingerprints
    assert not verified.pcb_power_stage_relationships
    assert not verified.pcb_power_loop_measurements
    assert _artifact_hash(artifact_path) == before_hash

    restarted = ProjectCatalogRuntime(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "catalog.sqlite3",
        initialize_async=False,
    )
    restored = restarted.conversation(
        report["project_id"],
        report["conversation_id"],
        tenant_id=TENANT_ID,
        requester=REQUESTER,
    )
    assert restored.pcb_power_loop_verification is not None
    assert restored.pcb_power_loop_verification.fingerprint == result.fingerprint
    assert _artifact_hash(artifact_path) == before_hash
