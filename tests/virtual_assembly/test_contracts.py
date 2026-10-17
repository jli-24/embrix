from __future__ import annotations

import pytest

from embedded_copilot.virtual_assembly import (
    AssemblyStatus,
    BindingStatus,
    ComponentBinding,
    GapSeverity,
    ModelAvailability,
    ModelType,
    SimulationGap,
    VirtualBoardAssembly,
)


def test_virtual_board_assembly_is_fingerprinted_and_strict() -> None:
    component = ComponentBinding.create(
        reference="U1",
        component_id="component-U1",
        value="STM32F103C8T6",
        part_number=None,
        role="MCU",
        symbol_library_id="MCU_ST_STM32F1",
        symbol_reference="STM32F103C8Tx",
        footprint_library_id="Package_QFP",
        footprint_reference="LQFP-48_7x7mm_P0.5mm",
        pcb_instance_reference="U1",
        binding_status=BindingStatus.BOUND,
        binding_evidence=("artifact-schematic", "artifact-pcb", "artifact-bom"),
        mechanical_model_status=ModelAvailability.MISSING,
        electrical_model=ModelType.NONE,
        simulation_capability=ModelType.NONE,
    )
    assembly = VirtualBoardAssembly.create(
        assembly_id="assembly-test",
        project_id="project-test",
        conversation_id="conversation-test",
        design_truth_id="truth-test",
        design_truth_fingerprint="sha256:" + "1" * 64,
        schematic_artifact_id="artifact-schematic",
        schematic_sha256="sha256:" + "2" * 64,
        pcb_artifact_id="artifact-pcb",
        pcb_sha256="sha256:" + "3" * 64,
        bom_artifact_id="artifact-bom",
        bom_sha256="sha256:" + "4" * 64,
        component_bindings=(component,),
        assembly_status=AssemblyStatus.INCOMPLETE,
    )

    assert assembly.assembly_fingerprint.startswith("sha256:")
    assert assembly.component_bindings == (component,)
    with pytest.raises(ValueError):
        VirtualBoardAssembly.model_validate(
            {**assembly.model_dump(mode="python"), "assembly_fingerprint": "sha256:" + "f" * 64}
        )


def test_component_binding_rejects_unsafe_model_reference() -> None:
    with pytest.raises(ValueError):
        ComponentBinding.create(
            reference="R1",
            component_id="component-R1",
            value="1k",
            part_number=None,
            role="PASSIVE",
            symbol_library_id="Device",
            symbol_reference="R",
            footprint_library_id="Resistor_SMD",
            footprint_reference="R_0603_1608Metric",
            pcb_instance_reference="R1",
            binding_status=BindingStatus.BOUND,
            binding_evidence=("artifact-schematic",),
            mechanical_model_status=ModelAvailability.AVAILABLE,
            mechanical_model_reference="C:\\private\\model.step",
            electrical_model=ModelType.IDEAL,
            simulation_capability=ModelType.IDEAL,
        )


def test_simulation_gap_blocking_flag_must_match_severity() -> None:
    with pytest.raises(ValueError, match="blocking flag must match gap severity"):
        SimulationGap.create(
            gap_id="gap-3d",
            component_reference="C1",
            required_behavior="Mechanical assembly visualization",
            missing_model="Ordinary 3D mechanical model",
            impact="Mechanical-only projection is incomplete",
            severity=GapSeverity.NON_BLOCKING,
            blocking=True,
            recommended_resolution="Register an approved 3D model if mechanical review requires it",
        )
