from __future__ import annotations

import os
from pathlib import Path

import pytest

from embedded_copilot.engineering_copilot import ArtifactIntelligenceService
from embedded_copilot.virtual_assembly import (
    AssemblyStatus,
    CanonicalVirtualAssemblyAdapter,
    SQLiteVirtualAssemblyStore,
    VirtualAssemblyService,
)

PROJECT_ROOT = Path(
    os.environ.get(
        "EMBRIX_CANONICAL_PROJECT_ROOT",
        r"D:\EmbeddedProjects\Minimal MCU Control Core",
    )
)
TOOLCHAIN_BIN = Path(
    os.environ.get(
        "EMBRIX_CANONICAL_TOOLCHAIN_BIN",
        r"D:\EmbeddedToolchains\ArmGNU\15.3.rel1\bin",
    )
)


def test_virtual_assembly_real_canonical_golden(tmp_path: Path) -> None:
    if not PROJECT_ROOT.is_dir() or not TOOLCHAIN_BIN.is_dir():
        pytest.skip("canonical project or trusted ARM toolchain is unavailable")

    source = CanonicalVirtualAssemblyAdapter(
        PROJECT_ROOT,
        project_id="minimal-mcu-control-core",
        toolchain_bin=TOOLCHAIN_BIN,
    ).collect()
    assembly = VirtualAssemblyService().assemble(source)
    store = SQLiteVirtualAssemblyStore(tmp_path / "virtual-assembly.sqlite3")
    store.save(assembly)
    reloaded = store.get(assembly.project_id, assembly.assembly_id)

    assert source.consistency_input.hardware.schematic.artifact_id == "artifact-schematic"
    assert source.consistency_input.hardware.pcb.artifact_id == "artifact-pcb"
    assert source.consistency_input.hardware.bom.artifact_id == "artifact-bom"
    assert source.consistency_input.build.elf.artifact_id == "artifact-elf"
    assert source.consistency_input.build.binary.artifact_id == "artifact-bin"
    assert len(assembly.component_bindings) == 11
    assert len(assembly.mechanical_model_bindings) == 11
    assert assembly.coverage is not None
    assert assembly.coverage.component_binding_coverage == 100.0
    assert assembly.artifact_integrity.value == "PASS"
    assert assembly.firmware_binding.value == "PASS"
    assert assembly.pin_pad_mapping.value == "PASS"
    assert {
        item.logical_pin: (
            item.symbol_pin_number,
            item.schematic_net,
            item.footprint_pad_number,
            item.pcb_net,
            item.status.value,
        )
        for item in assembly.pin_pad_bindings
    } == {
        "PA8": ("29", "LED_PA8", "29", "LED", "PASS"),
        "PB0": ("18", "BUTTON_PB0", "18", "BUTTON", "PASS"),
        "BOOT0": ("44", "BOOT0", "44", "BOOT0", "PASS"),
        "NRST": ("7", "NRST", "7", "NRST", "PASS"),
        "PA13-SWDIO": ("34", "SWDIO", "34", "SWDIO", "PASS"),
        "PA14-SWCLK": ("37", "SWCLK", "37", "SWCLK", "PASS"),
        "3V3": ("9", "3V3", "9", "3V3_MCU", "PASS"),
        "GND": ("8", "GND", "8", "GND_MCU", "PASS"),
    }
    artifact_snapshot = ArtifactIntelligenceService().scan_directory(
        root=PROJECT_ROOT,
        tenant_id="desktop-tenant",
        project_id="project-cd1617ef-0029-4628-882b-a2cc4bf518dc",
        conversation_id="conversation-ba96322b-c712-4485-9bfb-5901fa0d5878",
        source_operation_id="kicad-schematic-write-cb9682cd4899bdf5d4faeed534637b56",
        source_proposal_fingerprint="sha256:bc79e431a1f4ea457ed7e48e6aa5b38d9b8ce61079f109195c9262fe37a7fb87",
    )
    schematic_artifact = next(
        item
        for item in artifact_snapshot.inventory.artifacts
        if item.path_reference == "hardware/main.kicad_sch"
    )
    assert schematic_artifact.status.value == "PARSED"
    assert schematic_artifact.hash == source.consistency_input.hardware.schematic.content_fingerprint
    assert dict(schematic_artifact.metadata)["source_operation_id"] == "kicad-schematic-write-cb9682cd4899bdf5d4faeed534637b56"
    assert assembly.simulation_gaps
    assert assembly.assembly_status is AssemblyStatus.BLOCKED
    assert reloaded is not None
    assert reloaded.assembly_id == assembly.assembly_id
    assert reloaded.assembly_fingerprint == assembly.assembly_fingerprint
