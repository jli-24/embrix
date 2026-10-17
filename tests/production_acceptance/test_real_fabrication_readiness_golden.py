from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from embedded_copilot.engineering_requirements import RequirementContext
from embedded_copilot.manufacturing import (
    FabricationModeStatus,
    FabricationReadinessService,
    FabricationStore,
    build_source_revision,
    render_fabrication_report,
)

PROJECT_ROOT = Path(r"D:\EmbeddedProjects\Minimal MCU Control Core")
STATE_DB = Path(".data/canonical-golden-project.sqlite3")


def _canonical_context() -> RequirementContext | None:
    if not STATE_DB.is_file():
        return None
    import sqlite3

    connection = sqlite3.connect(STATE_DB)
    try:
        row = connection.execute("SELECT body FROM conversations ORDER BY updated_at DESC LIMIT 1").fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    body = json.loads(row[0])
    material = body.get("requirement_context")
    return None if material is None else RequirementContext.model_validate(material, strict=False)


def test_canonical_fabrication_readiness_requires_real_mode_and_does_not_infer_from_bom(tmp_path: Path) -> None:
    pcb = PROJECT_ROOT / "hardware" / "main.kicad_pcb"
    bom = PROJECT_ROOT / "hardware" / "Minimal_MCU_Control_Core_BOM.csv"
    schematic = PROJECT_ROOT / "hardware" / "main.kicad_sch"
    elf = PROJECT_ROOT / "build" / "Minimal_MCU_Control_Core.elf"
    if not all(path.is_file() for path in (pcb, bom, schematic, elf)):
        pytest.skip("canonical real PCB, BOM, schematic and ELF are unavailable")
    context = _canonical_context()
    if context is None:
        pytest.skip("persisted canonical RequirementContext is unavailable")
    source = build_source_revision(
        project_id=context.project_id,
        project_revision="canonical-golden-head",
        requirement_revision=context.fingerprint,
        design_truth_id="canonical-design-truth",
        design_truth_fingerprint="sha256:" + "b" * 64,
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
        source_refs=(context.fingerprint, "canonical-pcb", "canonical-bom"),
    )
    record = FabricationReadinessService(store=FabricationStore(tmp_path / "fabrication")).evaluate(
        source=source,
        pcb_path=pcb,
        bom_path=bom,
        schematic_path=schematic,
        elf_path=elf,
        requirement_context=context,
        now=datetime(2026, 9, 9, tzinfo=UTC),
    )
    assert record.readiness.mode_resolution.status is FabricationModeStatus.REQUIRED
    assert not record.readiness.fabrication_ready
    assert record.baseline is None
    report = render_fabrication_report(record)
    assert "EMBRIX_FABRICATION_MODE=REQUIRED" in report
    assert "EMBRIX_FABRICATION_MODE_STATUS=FABRICATION_MODE_REQUIRED" in report
    assert "EMBRIX_FABRICATION_READY=false" in report
