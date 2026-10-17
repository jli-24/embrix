from __future__ import annotations

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot import (
    HardwareDesignSpecification,
    ImplementationArtifactType,
    ImplementationIntent,
    ImplementationSpecification,
    ImplementationStatus,
    ImplementationTaskType,
    VerificationSpecification,
    generate_engineering_workflow,
    generate_firmware_spec,
    generate_hardware_design_spec,
    generate_implementation_spec,
    generate_simulation_spec,
    generate_verification_spec,
)
from embedded_copilot.engineering_copilot.common import ZERO

from .test_workflow import _approved_sources


def _sources() -> tuple[
    HardwareDesignSpecification,
    object,
    object,
    VerificationSpecification,
    object,
]:
    plan, architecture = _approved_sources()
    workflow = generate_engineering_workflow(architecture, plan)
    hardware = generate_hardware_design_spec(architecture, workflow)
    firmware = generate_firmware_spec(hardware, workflow)
    simulation = generate_simulation_spec(hardware, firmware, workflow)
    verification = generate_verification_spec(hardware, firmware, simulation, workflow)
    return hardware, firmware, simulation, verification, workflow


def _with_explicit_sources():
    hardware, _, _, _, workflow = _sources()
    hardware_data = hardware.model_dump(mode="python")
    interface = dict(hardware_data["interfaces"][0])
    interface["interface_type"] = "CONTROL"
    interface["fingerprint"] = ZERO
    hardware_data["interfaces"] = [interface, *hardware_data["interfaces"][1:]]
    hardware_data["fingerprint"] = ZERO
    hardware = HardwareDesignSpecification.model_validate(hardware_data, strict=False)
    firmware = generate_firmware_spec(hardware, workflow)
    simulation = generate_simulation_spec(hardware, firmware, workflow)
    simulation_data = simulation.model_dump(mode="python")
    if simulation_data["scenarios"]:
        scenario = dict(simulation_data["scenarios"][0])
        scenario["acceptance_refs"] = ["acceptance-motor"]
        scenario["fingerprint"] = ZERO
        simulation_data["scenarios"] = [scenario, *simulation_data["scenarios"][1:]]
        simulation_data["fingerprint"] = ZERO
        simulation = type(simulation).model_validate(simulation_data, strict=False)
    verification = generate_verification_spec(hardware, firmware, simulation, workflow)
    return hardware, firmware, simulation, verification, workflow


def test_implementation_contract_is_strict_frozen_tuple_and_round_trips() -> None:
    hardware, firmware, simulation, verification, workflow = _with_explicit_sources()
    spec = generate_implementation_spec(hardware, firmware, simulation, verification, workflow)

    assert spec.status is ImplementationStatus.PLANNED
    assert type(spec.tasks) is tuple
    assert type(spec.expected_artifacts) is tuple
    assert type(spec.dependencies) is tuple
    assert all(type(task.source_refs) is tuple for task in spec.tasks)
    assert all(type(task.acceptance_refs) is tuple for task in spec.tasks)
    assert all(task.implementation_intent in set(ImplementationIntent) for task in spec.tasks)
    assert ImplementationSpecification.model_validate_json(spec.model_dump_json()) == spec

    with pytest.raises((TypeError, ValidationError)):
        spec.status = ImplementationStatus.COMPLETED

    extra = spec.model_dump(mode="python")
    extra["unexpected"] = "forbidden"
    extra["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="unexpected|extra"):
        ImplementationSpecification.model_validate(extra, strict=False)

    tampered = spec.model_dump(mode="python")
    tampered["tasks"][0]["description"] = "fabricated implementation detail"
    tampered["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="fingerprint"):
        ImplementationSpecification.model_validate(tampered, strict=False)


def test_implementation_projection_maps_sources_without_generating_code() -> None:
    hardware, firmware, simulation, verification, workflow = _with_explicit_sources()
    spec = generate_implementation_spec(hardware, firmware, simulation, verification, workflow)

    task_types = {task.task_type for task in spec.tasks}
    assert ImplementationTaskType.FIRMWARE_MODULE in task_types
    assert ImplementationTaskType.INTERFACE_IMPLEMENTATION in task_types
    assert any(task.implementation_intent is ImplementationIntent.CONTROL_IMPLEMENTATION for task in spec.tasks)
    if simulation.scenarios:
        assert ImplementationTaskType.TEST_PREPARATION in task_types
    artifact_types = {artifact.artifact_type for artifact in spec.expected_artifacts}
    assert ImplementationArtifactType.SOURCE_CODE in artifact_types
    assert all(task.status is ImplementationStatus.PLANNED for task in spec.tasks)
    assert all(artifact.artifact_id in task.expected_artifacts for task in spec.tasks for artifact in spec.expected_artifacts if artifact.artifact_id in task.expected_artifacts)
    assert any("acceptance-motor" in task.acceptance_refs for task in spec.tasks)

    encoded = spec.model_dump_json()
    forbidden = (
        "STM32F103", "TB6612", "MPU6050", "STM32 HAL", "FreeRTOS",
        "PWM", "TIM", "PID", "ADC", "CAN", "I2C", "motor_control.c",
        "Generate Code", "Build Firmware", "Flash Device",
    )
    assert all(value not in encoded for value in forbidden)


def test_implementation_contract_rejects_invalid_references_and_direct_inputs() -> None:
    hardware, firmware, simulation, verification, workflow = _sources()
    spec = generate_implementation_spec(hardware, firmware, simulation, verification, workflow)

    invalid_dependency = spec.model_dump(mode="python")
    invalid_dependency["tasks"] = list(invalid_dependency["tasks"])
    invalid_dependency["tasks"][0]["dependencies"] = ["task-missing"]
    invalid_dependency["tasks"][0]["fingerprint"] = ZERO
    invalid_dependency["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="dependency"):
        ImplementationSpecification.model_validate(invalid_dependency, strict=False)

    invalid_artifact = spec.model_dump(mode="python")
    invalid_artifact["tasks"] = list(invalid_artifact["tasks"])
    invalid_artifact["tasks"][0]["expected_artifacts"] = ["artifact-missing"]
    invalid_artifact["tasks"][0]["fingerprint"] = ZERO
    invalid_artifact["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="artifact"):
        ImplementationSpecification.model_validate(invalid_artifact, strict=False)

    case_ids = {case.case_id for case in verification.cases}
    assert set(spec.verification_refs) <= case_ids
    assert all(set(task.verification_refs) <= case_ids for task in spec.tasks)

    invalid_verification = spec.model_dump(mode="python")
    invalid_verification["tasks"] = list(invalid_verification["tasks"])
    invalid_verification["tasks"][0]["verification_refs"] = ["case-missing"]
    invalid_verification["tasks"][0]["fingerprint"] = ZERO
    invalid_verification["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="verification"):
        ImplementationSpecification.model_validate(invalid_verification, strict=False)

    with pytest.raises(TypeError, match="HardwareDesignSpecification"):
        generate_implementation_spec(object(), firmware, simulation, verification, workflow)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="FirmwareSpecification"):
        generate_implementation_spec(hardware, object(), simulation, verification, workflow)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="VerificationSpecification"):
        generate_implementation_spec(hardware, firmware, simulation, object(), workflow)  # type: ignore[arg-type]


def test_implementation_projection_rejects_source_fingerprint_and_identity_mismatch() -> None:
    hardware, firmware, simulation, verification, workflow = _sources()
    tampered = firmware.model_copy(update={"fingerprint": "sha256:" + "b" * 64})
    with pytest.raises((ValidationError, ValueError), match="fingerprint|identity|binding"):
        generate_implementation_spec(hardware, tampered, simulation, verification, workflow)

    foreign = simulation.model_copy(update={"conversation_id": "conversation-foreign", "fingerprint": ZERO})
    with pytest.raises((ValidationError, ValueError), match="conversation|identity|fingerprint"):
        generate_implementation_spec(hardware, firmware, foreign, verification, workflow)
