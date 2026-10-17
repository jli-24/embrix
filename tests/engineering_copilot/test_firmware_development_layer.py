from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot import (
    FirmwareInterfaceRole,
    FirmwareSpecification,
    FirmwareTaskStatus,
    HardwareFirmwareBinding,
    generate_engineering_workflow,
    generate_firmware_spec,
    generate_hardware_design_spec,
)
from embedded_copilot.engineering_copilot.common import ZERO

from .test_workflow import _approved_sources


def _sources():
    plan, architecture = _approved_sources()
    workflow = generate_engineering_workflow(architecture, plan)
    hardware = generate_hardware_design_spec(architecture, workflow)
    return hardware, workflow


def test_firmware_contract_is_strict_frozen_tuple_and_round_trips() -> None:
    hardware, workflow = _sources()
    spec = generate_firmware_spec(hardware, workflow)

    assert spec.project_id == hardware.project_id
    assert spec.conversation_id == workflow.conversation_id
    assert spec.hardware_design_id == hardware.hardware_spec_id
    assert spec.hardware_design_fingerprint == hardware.fingerprint
    assert spec.workflow_fingerprint == workflow.fingerprint
    assert type(spec.modules) is tuple
    assert all(type(item.dependencies) is tuple for item in spec.modules)
    assert all(item.status is FirmwareTaskStatus.PLANNED for item in spec.tasks)
    assert all(item.simulation_refs == () for item in spec.verification_requirements)
    assert FirmwareSpecification.model_validate_json(spec.model_dump_json()) == spec

    with pytest.raises((TypeError, ValidationError)):
        spec.title = "tampered"

    tampered = spec.model_dump(mode="python")
    tampered["modules"][0]["responsibility"] = "tampered"
    tampered["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="fingerprint"):
        FirmwareSpecification.model_validate(tampered, strict=False)

    extra = spec.model_dump(mode="python")
    extra["unexpected"] = "forbidden"
    extra["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="unexpected"):
        FirmwareSpecification.model_validate(extra, strict=False)


def test_firmware_projection_is_downstream_and_does_not_fabricate_implementation() -> None:
    hardware, workflow = _sources()
    spec = generate_firmware_spec(hardware, workflow)

    names = {item.name for item in spec.modules}
    assert {"Application", "Sensor Acquisition", "Motor Control"} <= names
    forbidden = ("STM32F103", "TB6612", "MPU6050", "STM32 HAL", "FreeRTOS", "PID", "PWM", "TIM")
    assert all(item not in spec.model_dump_json() for item in forbidden)
    assert all(binding.interface_role is FirmwareInterfaceRole.UNKNOWN for binding in spec.bindings)
    assert all(binding.direction == "UNKNOWN" for binding in spec.bindings)
    assert all(task.task_type.value == "DESIGN" for task in spec.tasks)
    assert all(task.status is FirmwareTaskStatus.PLANNED for task in spec.tasks)
    assert all(item.simulation_refs == () for item in spec.verification_requirements)


def test_firmware_binding_keeps_existing_design_truth_binding_compatible() -> None:
    assert HardwareFirmwareBinding is not None
    hardware, workflow = _sources()
    spec = generate_firmware_spec(hardware, workflow)
    assert all(item.hardware_interface_id in {value.interface_id for value in hardware.interfaces} for item in spec.bindings)


def test_firmware_projection_rejects_invalid_or_direct_sources() -> None:
    hardware, workflow = _sources()

    with pytest.raises(TypeError, match="HardwareDesignSpecification"):
        generate_firmware_spec(object(), workflow)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="EngineeringWorkflow"):
        generate_firmware_spec(hardware, object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="HardwareDesignSpecification"):
        generate_firmware_spec(object(), object())  # type: ignore[arg-type]

    tampered_hardware = hardware.model_copy(update={"fingerprint": "sha256:" + "b" * 64})
    with pytest.raises(ValidationError, match="fingerprint"):
        generate_firmware_spec(tampered_hardware, workflow)

    tampered_workflow = workflow.model_copy(
        update={"conversation_id": "conversation-foreign", "fingerprint": ZERO}
    )
    with pytest.raises((ValidationError, ValueError), match="fingerprint|conversation|binding"):
        generate_firmware_spec(hardware, tampered_workflow)


def test_firmware_contract_rejects_duplicate_ids_and_nonempty_simulation_refs() -> None:
    hardware, workflow = _sources()
    spec = generate_firmware_spec(hardware, workflow)

    duplicate = spec.model_dump(mode="python")
    duplicate["modules"] = list(duplicate["modules"])
    duplicate["modules"].append(dict(duplicate["modules"][0]))
    duplicate["modules"][-1]["module_id"] = duplicate["modules"][0]["module_id"]
    duplicate["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="unique"):
        FirmwareSpecification.model_validate(duplicate, strict=False)

    if spec.verification_requirements:
        simulation = spec.model_dump(mode="python")
        simulation["verification_requirements"] = list(simulation["verification_requirements"])
        simulation["verification_requirements"][0]["simulation_refs"] = ("simulation-1",)
        simulation["verification_requirements"][0]["fingerprint"] = ZERO
        simulation["fingerprint"] = ZERO
        with pytest.raises(ValidationError, match="simulation"):
            FirmwareSpecification.model_validate(simulation, strict=False)


def test_firmware_json_has_no_raw_execution_or_artifact_path() -> None:
    hardware, workflow = _sources()
    payload = json.loads(generate_firmware_spec(hardware, workflow).model_dump_json())
    encoded = json.dumps(payload, ensure_ascii=False)
    assert "source_path" not in encoded
    assert "Build Firmware" not in encoded
    assert "Flash Device" not in encoded
