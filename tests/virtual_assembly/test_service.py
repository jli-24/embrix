from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from embedded_copilot.engineering_copilot.common import ZERO
from embedded_copilot.engineering_pcb import PCBConnection
from embedded_copilot.virtual_assembly import (
    AssemblyStatus,
    BindingStatus,
    CanonicalVirtualAssemblyAdapter,
    GateStatus,
    InMemoryArtifactReader,
    SQLiteVirtualAssemblyStore,
    VirtualAssemblyService,
)
from embedded_copilot.virtual_assembly.service import _normalized_net

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


@pytest.fixture(scope="module")
def source():
    if not PROJECT_ROOT.is_dir() or not TOOLCHAIN_BIN.is_dir():
        pytest.skip("canonical project or trusted ARM toolchain is unavailable")
    return CanonicalVirtualAssemblyAdapter(
        PROJECT_ROOT,
        project_id="minimal-mcu-control-core",
        toolchain_bin=TOOLCHAIN_BIN,
    ).collect()


def _rebuild(model, **updates):
    payload = model.model_dump(mode="python")
    payload.update(updates)
    payload["fingerprint"] = ZERO
    return type(model).model_validate(payload)


def _with_schematic_connection(source, *, pin_number: str, net_id: str | None):
    connections = tuple(
        item
        for item in source.schematic.connections
        if not (item.component_reference == "U1" and item.pin_number == pin_number)
    )
    if net_id is not None:
        connections += (
            PCBConnection.create(
                component_reference="U1",
                pin_number=pin_number,
                net_id=net_id,
                connection_kind="TEST_SCHEMATIC_LABEL",
                artifact_fingerprints=(source.schematic.artifact.fingerprint,),
                evidence_refs=source.schematic.connections[0].evidence_refs,
            ),
        )
    return replace(
        source,
        schematic=_rebuild(source.schematic, connections=connections),
    )


def test_real_golden_binds_artifacts_and_is_blocked_only_by_simulation(source) -> None:
    service = VirtualAssemblyService()
    first = service.assemble(source)
    second = service.assemble(source)

    assert first.assembly_id == second.assembly_id
    assert first.assembly_fingerprint == second.assembly_fingerprint
    assert first.artifact_integrity.value == "PASS"
    assert first.design_truth_binding.value == "PASS"
    assert first.firmware_binding.value == "PASS"
    assert first.coverage is not None
    assert first.coverage.total_bom_components == 11
    assert first.coverage.bound_components == 11
    assert first.coverage.component_binding_coverage == 100.0
    assert first.coverage.mechanical_model_coverage == 0.0
    assert first.coverage.firmware_execution_model_coverage == 0.0
    assert first.blocking_simulation_gaps >= 1
    assert first.non_blocking_simulation_gaps >= 1
    assert first.ready_for_simulation is False
    assert first.assembly_status is AssemblyStatus.BLOCKED
    assert first.pin_pad_mapping is GateStatus.PASS
    assert {item.logical_pin for item in first.pin_pad_bindings} == {
        "PA8",
        "PB0",
        "BOOT0",
        "NRST",
        "PA13-SWDIO",
        "PA14-SWCLK",
        "3V3",
        "GND",
    }
    assert all(
        item.severity.value == "NON_BLOCKING"
        and not item.blocking
        for item in first.simulation_gaps
        if item.missing_model == "Ordinary 3D mechanical model"
    )


@pytest.mark.parametrize(
    ("observed", "canonical"),
    (
        ("3V3", "3V3_MCU"),
        ("GND", "GND_MCU"),
        ("LED_PA8", "LED"),
        ("BUTTON_PB0", "BUTTON"),
    ),
)
def test_net_aliases_are_explicitly_canonicalized(observed, canonical) -> None:
    assert _normalized_net(observed) == canonical


def test_persistence_restart_recovers_identity_and_bindings(source, tmp_path: Path) -> None:
    assembly = VirtualAssemblyService().assemble(source)
    store = SQLiteVirtualAssemblyStore(tmp_path / "virtual-assembly.sqlite3")

    store.save(assembly)
    reloaded = store.get(assembly.project_id, assembly.assembly_id)

    assert reloaded is not None
    assert reloaded.assembly_id == assembly.assembly_id
    assert reloaded.assembly_fingerprint == assembly.assembly_fingerprint
    assert reloaded.component_bindings == assembly.component_bindings
    assert reloaded.mechanical_model_bindings == assembly.mechanical_model_bindings
    assert reloaded.simulation_gaps == assembly.simulation_gaps
    assert reloaded.assembly_status is assembly.assembly_status


def test_artifact_tamper_is_stale_and_never_silently_rehashed(source) -> None:
    paths = {
        source.consistency_input.hardware.schematic.relative_path,
        source.consistency_input.hardware.pcb.relative_path,
        source.consistency_input.hardware.bom.relative_path,
        *(item.relative_path for item in source.consistency_input.build.source_artifacts),
        source.consistency_input.build.elf.relative_path,
        source.consistency_input.build.binary.relative_path,
    }
    contents = {path: source.artifact_reader.read(path) for path in paths}
    pcb_path = source.consistency_input.hardware.pcb.relative_path
    contents[pcb_path] = contents[pcb_path] + b"\n# tampered\n"
    tampered = replace(source, artifact_reader=InMemoryArtifactReader(contents))

    assembly = VirtualAssemblyService().assemble(tampered)

    assert assembly.artifact_integrity.value == "FAIL"
    assert assembly.assembly_status is AssemblyStatus.STALE
    assert pcb_path in assembly.stale_inputs
    assert assembly.pcb_sha256 == source.consistency_input.hardware.pcb.content_fingerprint


def test_bom_identity_conflict_blocks_without_selecting_a_winner(source) -> None:
    u1 = next(item for item in source.bom.items if item.references == ("U1",))
    changed_u1 = _rebuild(u1, value="STM32F401C8T6")
    changed_items = tuple(changed_u1 if item is u1 else item for item in source.bom.items)
    changed_bom = _rebuild(source.bom, items=changed_items)
    conflicted = replace(source, bom=changed_bom)

    assembly = VirtualAssemblyService().assemble(conflicted)

    assert assembly.component_binding.value == "CONFLICT"
    assert assembly.assembly_status is AssemblyStatus.BLOCKED
    assert next(item for item in assembly.component_bindings if item.reference == "U1").binding_status is BindingStatus.PARTIAL


def test_dnp_is_explicitly_not_populated_and_not_counted_as_missing(source) -> None:
    d1 = next(item for item in source.bom.items if item.references == ("D1",))
    changed_d1 = _rebuild(d1, description="DNP LED; do not populate")
    changed_bom = _rebuild(
        source.bom,
        items=tuple(changed_d1 if item is d1 else item for item in source.bom.items),
    )

    assembly = VirtualAssemblyService().assemble(replace(source, bom=changed_bom))
    binding = next(item for item in assembly.component_bindings if item.reference == "D1")

    assert binding.population_status == "NOT_POPULATED"
    assert binding.binding_status is BindingStatus.NOT_APPLICABLE
    assert assembly.coverage is not None
    assert assembly.coverage.unbound_components == 0


def test_missing_bom_component_is_unbound_and_incomplete(source) -> None:
    r1 = next(item for item in source.bom.items if item.references == ("R1",))
    missing = _rebuild(r1, item_id="bom-R5", references=("R5",), quantity=1)
    changed_bom = _rebuild(source.bom, items=(*source.bom.items, missing))

    assembly = VirtualAssemblyService().assemble(replace(source, bom=changed_bom))
    binding = next(item for item in assembly.component_bindings if item.reference == "R5")

    assert binding.binding_status is BindingStatus.UNBOUND
    assert assembly.component_binding is GateStatus.INCOMPLETE
    assert assembly.footprint_binding is GateStatus.INCOMPLETE


def test_wrong_schematic_footprint_is_a_conflict(source) -> None:
    u1 = next(item for item in source.schematic.components if item.reference == "U1")
    changed_u1 = _rebuild(u1, footprint="Package_QFP:WRONG_FOOTPRINT")
    changed_schematic = _rebuild(
        source.schematic,
        components=tuple(changed_u1 if item is u1 else item for item in source.schematic.components),
    )

    assembly = VirtualAssemblyService().assemble(replace(source, schematic=changed_schematic))

    assert assembly.footprint_binding is GateStatus.CONFLICT
    assert assembly.assembly_status is AssemblyStatus.BLOCKED


def test_firmware_target_revision_mismatch_is_conflict(source) -> None:
    changed_build = _rebuild(source.consistency_input.build, target_kind="STM32F401C8T6")
    changed_input = _rebuild(source.consistency_input, build=changed_build)

    assembly = VirtualAssemblyService().assemble(replace(source, consistency_input=changed_input))

    assert assembly.firmware_binding is GateStatus.CONFLICT
    assert assembly.assembly_status is AssemblyStatus.BLOCKED


def test_missing_required_schematic_label_is_unknown(source) -> None:
    changed = _with_schematic_connection(source, pin_number="29", net_id=None)
    changed = _with_schematic_connection(changed, pin_number="35", net_id=None)
    assembly = VirtualAssemblyService().assemble(changed)

    binding = next(item for item in assembly.pin_pad_bindings if item.logical_pin == "PA8")
    assert binding.status.value == "UNKNOWN"
    assert "not exposed" in binding.reason


def test_required_schematic_label_on_wrong_net_is_conflict(source) -> None:
    assembly = VirtualAssemblyService().assemble(
        _with_schematic_connection(source, pin_number="29", net_id="WRONG_NET")
    )

    binding = next(item for item in assembly.pin_pad_bindings if item.logical_pin == "PA8")
    assert binding.status.value == "CONFLICT"
    assert binding.schematic_net == "WRONG_NET"


def test_required_schematic_net_on_wrong_symbol_pin_is_conflict(source) -> None:
    changed = _with_schematic_connection(source, pin_number="29", net_id=None)
    changed = _with_schematic_connection(changed, pin_number="35", net_id="LED")
    assembly = VirtualAssemblyService().assemble(changed)

    binding = next(item for item in assembly.pin_pad_bindings if item.logical_pin == "PA8")
    assert binding.status.value == "CONFLICT"
    assert "instead of the expected symbol pin" in binding.reason
