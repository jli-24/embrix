from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from embedded_copilot.manufacturing import (
    FabricationMode,
    FabricationModeStatus,
    FabricationReadinessService,
    FabricationStore,
    build_manufacturing_specification,
    build_source_revision,
    read_board_facts,
    render_board_facts,
    render_fabrication_closure,
    render_fabrication_mode_audit,
)

PROJECT_ROOT = Path(r"D:\EmbeddedProjects\Minimal MCU Control Core")
STATE_DB = Path(".data/canonical-golden-project.sqlite3")
NOW = datetime(2026, 9, 9, tzinfo=UTC)


def _canonical_body() -> dict[str, object] | None:
    if not STATE_DB.is_file():
        return None
    with sqlite3.connect(STATE_DB) as connection:
        row = connection.execute(
            "SELECT body FROM conversations ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
    return None if row is None else json.loads(row[0])


def test_real_canonical_fabrication_mode_and_specification_survive_restart(
    tmp_path: Path,
) -> None:
    body = _canonical_body()
    paths = tuple(
        PROJECT_ROOT / relative
        for relative in (
            "hardware/main.kicad_pcb",
            "hardware/main.kicad_sch",
            "hardware/Minimal_MCU_Control_Core_BOM.csv",
            "build/Minimal_MCU_Control_Core.elf",
        )
    )
    if body is None or not all(path.is_file() for path in paths):
        pytest.skip("canonical requirement database or project artifacts are unavailable")

    requirement = body.get("requirement_context")
    truth = body.get("design_truth")
    pcb_production = body.get("pcb_production")
    if not isinstance(requirement, dict) or not isinstance(truth, dict) or not isinstance(pcb_production, dict):
        pytest.skip("canonical requirement, DesignTruth or PCB decision is unavailable")
    decision = pcb_production.get("decision")
    proposal = pcb_production.get("proposal")
    if not isinstance(decision, dict) or not isinstance(proposal, dict):
        pytest.skip("canonical approved PCB decision is unavailable")

    pcb, schematic, bom, elf = paths
    source = build_source_revision(
        project_id=str(requirement["project_id"]),
        project_revision=str(truth["design_revision"]),
        requirement_revision=str(requirement["fingerprint"]),
        design_truth_id=str(truth["truth_id"]),
        design_truth_fingerprint=str(truth["fingerprint"]),
        schematic_artifact_id="canonical-schematic",
        schematic_path=schematic,
        pcb_artifact_id="canonical-pcb",
        pcb_path=pcb,
        bom_artifact_id="canonical-bom",
        bom_path=bom,
        firmware_specification_id="canonical-firmware-spec",
        build_configuration_id="canonical-build-config",
        elf_artifact_id="canonical-elf",
        elf_path=elf,
        hw_fw_consistency_result_id="canonical-hw-fw-consistency",
        virtual_assembly_id="canonical-virtual-assembly",
        virtual_assembly_fingerprint="sha256:" + "c" * 64,
        simulation_result_id="canonical-simulation",
        functional_verification_result_id="canonical-functional-verification",
        functional_verification_fingerprint="sha256:" + "d" * 64,
        source_refs=(
            str(requirement["fingerprint"]),
            str(truth["truth_id"]),
            str(truth["fingerprint"]),
            str(decision["decision_id"]),
            str(proposal["proposal_id"]),
        ),
    )
    board = read_board_facts(pcb)
    assert board.layer_count == 2
    assert board.outline_valid
    assert board.board_dimensions_mm == (120.0, 90.0)
    assert board.smd_components == 11
    assert board.through_holes == 0
    assert board.mechanical_only == 0

    specification = build_manufacturing_specification(
        source=source,
        board=board,
        specification_id="canonical-manufacturing-spec-v1",
        fabrication_mode=FabricationMode.PCB_ONLY,
        source_refs=source.source_refs,
    )
    store = FabricationStore(tmp_path / "fabrication")
    record = FabricationReadinessService(store=store).evaluate(
        source=source,
        pcb_path=pcb,
        bom_path=bom,
        schematic_path=schematic,
        elf_path=elf,
        requirement_context=requirement,
        mode_source_values=(truth, pcb_production, decision, proposal),
        allow_pcb_only_without_assembly=True,
        mode_approval_ref=str(decision["decision_id"]),
        mode_approved_by=str(decision["reviewer"]),
        mode_evidence_refs=(str(proposal["proposal_id"]), str(truth["truth_id"])),
        specification=specification,
        now=NOW,
    )

    assert record.readiness.mode_resolution.status is FabricationModeStatus.RESOLVED
    assert record.readiness.fabrication_mode is FabricationMode.PCB_ONLY
    assert record.mode_audit is not None
    assert record.mode_audit.assembly_intent.value == "ABSENT"
    assert record.engineering_decision is not None
    assert record.engineering_decision.selected_mode is FabricationMode.PCB_ONLY
    assert record.specification is not None
    assert record.specification.fingerprint == specification.fingerprint
    assert record.specification.requirement_revision == source.requirement_revision
    assert record.specification.design_truth_fingerprint == source.design_truth_fingerprint
    assert record.specification.pcb_sha256 == source.pcb_sha256
    assert record.specification.bom_sha256 == source.bom_sha256
    assert record.readiness.blocking_reason == "BLOCKED_MANUFACTURING_PARAMETER_REQUIRED"
    assert not record.readiness.fabrication_ready

    restarted = FabricationStore(tmp_path / "fabrication").get(
        project_id=source.project_id,
        result_id=record.readiness.result_id,
    )
    assert restarted == record
    assert restarted is not None
    assert restarted.specification is not None
    assert restarted.specification.fingerprint == specification.fingerprint
    assert restarted.engineering_decision is not None
    assert restarted.engineering_decision.fingerprint == record.engineering_decision.fingerprint

    closure = render_fabrication_closure(record)
    assert "fabrication_mode=PCB_ONLY" in closure
    assert "gerber_ready=FAIL" in closure
    assert "drill_ready=FAIL" in closure
    assert "status=BLOCKED_MANUFACTURING_PARAMETER_REQUIRED" in closure
    print(render_board_facts(board))
    print(render_fabrication_mode_audit(record.mode_audit))
    print(closure)
