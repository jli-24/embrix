from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from embedded_copilot.manufacturing import (
    FabricationAssemblyIntent,
    FabricationMode,
    FabricationModeRequirementStatus,
    FabricationModeResolver,
    FabricationModeStatus,
    FabricationReadinessService,
    FabricationStore,
    ManufacturingSpecificationStatus,
    build_manufacturing_specification,
    build_source_revision,
    read_board_facts,
)

NOW = datetime(2026, 9, 9, tzinfo=UTC)
BOARD = """(kicad_pcb (version 20240108) (generator pcbnew)
  (general (thickness 1.6))
  (layers (0 \"F.Cu\" signal) (31 \"B.Cu\" signal) (36 \"B.SilkS\" user \"B.Silkscreen\") (37 \"F.SilkS\" user \"F.Silkscreen\") (44 \"Edge.Cuts\" user))
  (gr_rect (start 0 0) (end 20 10) (stroke (width 0.05) (type default)) (fill none) (layer \"Edge.Cuts\"))
  (footprint \"Test:Package\" (layer \"F.Cu\") (property \"Reference\" \"R1\") (property \"Value\" \"10k\"))
)"""


def _source(tmp_path: Path):
    pcb = tmp_path / "main.kicad_pcb"
    bom = tmp_path / "BOM.csv"
    schematic = tmp_path / "main.kicad_sch"
    elf = tmp_path / "main.elf"
    pcb.write_text(BOARD, encoding="utf-8")
    bom.write_text("Reference,Value\nR1,10k\n", encoding="utf-8")
    schematic.write_text("schematic", encoding="utf-8")
    elf.write_bytes(b"ELF")
    return (
        build_source_revision(
            project_id="project-1",
            project_revision="revision-1",
            requirement_revision="requirement-1",
            design_truth_id="design-truth-1",
            design_truth_fingerprint="sha256:" + "a" * 64,
            schematic_artifact_id="schematic-1",
            schematic_path=schematic,
            pcb_artifact_id="pcb-1",
            pcb_path=pcb,
            bom_artifact_id="bom-1",
            bom_path=bom,
            firmware_specification_id="firmware-spec-1",
            build_configuration_id="build-config-1",
            elf_artifact_id="elf-1",
            elf_path=elf,
            hw_fw_consistency_result_id="hw-fw-1",
            virtual_assembly_id="virtual-assembly-1",
            virtual_assembly_fingerprint="sha256:" + "b" * 64,
            simulation_result_id="simulation-1",
            functional_verification_result_id="functional-1",
            functional_verification_fingerprint="sha256:" + "c" * 64,
            source_refs=("requirement-1", "design-truth-1", "pcb-1", "bom-1"),
        ),
        pcb,
        bom,
        schematic,
        elf,
    )


def test_explicit_modes_are_resolved_without_board_shape_inference() -> None:
    resolver = FabricationModeResolver()
    assert resolver.resolve(project_id="project-1", explicit_mode="PCB_ONLY").mode is FabricationMode.PCB_ONLY
    assert resolver.resolve(project_id="project-1", explicit_mode="PCB_ASSEMBLY").mode is FabricationMode.PCB_ASSEMBLY
    audit = resolver.audit(
        project_id="project-1",
        project_revision="revision-1",
        source_values=({"fabrication_mode": "PCB_ONLY"},),
    )
    assert audit.existing_mode is FabricationMode.PCB_ONLY
    assert audit.missing_facts == ()


def test_missing_mode_is_required_and_no_assembly_policy_requires_approval() -> None:
    resolver = FabricationModeResolver()
    audit = resolver.audit(
        project_id="project-1",
        project_revision="revision-1",
        requirement_context={"requirements": ["two-layer MCU control board"]},
        source_values=({"decision": "PCB proposal explicitly approved"},),
    )
    assert audit.assembly_intent is FabricationAssemblyIntent.ABSENT
    required = resolver.resolve(
        project_id="project-1",
        requirement_context={"requirements": ["two-layer MCU control board"]},
    )
    assert required.status is FabricationModeStatus.REQUIRED
    resolved = resolver.resolve(
        project_id="project-1",
        requirement_context={"requirements": ["two-layer MCU control board"]},
        allow_pcb_only_without_assembly=True,
        mode_approval_ref="approval-1",
        mode_approved_by="reviewer-1",
    )
    assert resolved.mode is FabricationMode.PCB_ONLY
    assert resolved.source_ref == "approval-1"


def test_ambiguous_and_unsupported_modes_fail_closed() -> None:
    resolver = FabricationModeResolver()
    result = resolver.resolve(
        project_id="project-1",
        requirement_context={"first": "PCB_ONLY", "second": "PCB_ASSEMBLY"},
    )
    assert result.status is FabricationModeStatus.REQUIRED
    with pytest.raises(ValueError):
        resolver.resolve(project_id="project-1", explicit_mode="PANEL_ASSEMBLY")


def test_specification_and_engineering_decision_survive_restart_with_stable_fingerprint(tmp_path: Path) -> None:
    source, pcb, bom, schematic, elf = _source(tmp_path)
    board = read_board_facts(pcb)
    specification = build_manufacturing_specification(
        source=source,
        board=board,
        specification_id="specification-1",
        fabrication_mode=FabricationMode.PCB_ONLY,
        source_refs=("requirement-1", "design-truth-1", "pcb-1", "bom-1"),
    )
    assert specification.status is ManufacturingSpecificationStatus.INCOMPLETE
    store = FabricationStore(tmp_path / "fabrication")
    service = FabricationReadinessService(store=store)
    first = service.evaluate(
        source=source,
        pcb_path=pcb,
        bom_path=bom,
        schematic_path=schematic,
        elf_path=elf,
        requirement_context={"requirements": ["two-layer MCU control board"]},
        mode_source_values=({"decision": "PCB proposal explicitly approved"},),
        allow_pcb_only_without_assembly=True,
        mode_approval_ref="approval-1",
        mode_approved_by="reviewer-1",
        mode_evidence_refs=("requirement-1", "design-truth-1"),
        specification=specification,
        now=NOW,
    )
    assert first.engineering_decision is not None
    assert first.mode_requirement is None
    assert first.specification is not None
    assert first.readiness.fabrication_mode is FabricationMode.PCB_ONLY
    assert first.readiness.blocking_reason == "BLOCKED_MANUFACTURING_PARAMETER_REQUIRED"
    assert not first.readiness.fabrication_ready

    restored = FabricationStore(tmp_path / "fabrication").get(
        project_id="project-1", result_id=first.readiness.result_id
    )
    assert restored is not None
    assert restored.specification is not None
    assert restored.specification.fingerprint == specification.fingerprint
    assert restored.engineering_decision is not None
    assert restored.engineering_decision.fingerprint == first.engineering_decision.fingerprint
    assert restored.mode_requirement is None


def test_missing_mode_persists_a_fabrication_mode_requirement(tmp_path: Path) -> None:
    source, pcb, bom, schematic, elf = _source(tmp_path)
    record = FabricationReadinessService(store=FabricationStore(tmp_path / "fabrication")).evaluate(
        source=source,
        pcb_path=pcb,
        bom_path=bom,
        schematic_path=schematic,
        elf_path=elf,
        requirement_context={"requirements": ["two-layer MCU control board"]},
        now=NOW,
    )
    assert record.mode_requirement is not None
    assert record.mode_requirement.status is FabricationModeRequirementStatus.BLOCKED
    assert record.mode_requirement.required_fact == "PCB_ONLY or PCB_ASSEMBLY"
    assert record.engineering_decision is None
