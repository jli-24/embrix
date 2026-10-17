from __future__ import annotations

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot import (
    HardwareDesignSpecification,
    SimulationDesignIntent,
    SimulationEvidenceType,
    SimulationScenario,
    SimulationScenarioType,
    SimulationSpecification,
    SimulationStatus,
    generate_engineering_workflow,
    generate_firmware_spec,
    generate_hardware_design_spec,
    generate_simulation_spec,
)
from embedded_copilot.engineering_copilot.common import ZERO

from .test_workflow import _approved_sources


def _sources():
    plan, architecture = _approved_sources()
    workflow = generate_engineering_workflow(architecture, plan)
    hardware = generate_hardware_design_spec(architecture, workflow)
    firmware = generate_firmware_spec(hardware, workflow)
    return hardware, firmware, workflow


def _with_interface_type(hardware: HardwareDesignSpecification, interface_type: str) -> HardwareDesignSpecification:
    data = hardware.model_dump(mode="python")
    interface = dict(data["interfaces"][0])
    interface["interface_type"] = interface_type
    interface["fingerprint"] = ZERO
    data["interfaces"] = [interface, *data["interfaces"][1:]]
    data["fingerprint"] = ZERO
    return HardwareDesignSpecification.model_validate(data, strict=False)


def test_simulation_contract_is_strict_frozen_tuple_and_round_trips() -> None:
    hardware, firmware, workflow = _sources()
    spec = generate_simulation_spec(hardware, firmware, workflow)

    assert spec.status is SimulationStatus.PLANNED
    assert spec.design_intent in set(SimulationDesignIntent)
    assert type(spec.targets) is tuple
    assert type(spec.models) is tuple
    assert all(type(model.interfaces) is tuple for model in spec.models)
    assert all(type(scenario.related_models) is tuple for scenario in spec.scenarios)
    assert SimulationSpecification.model_validate_json(spec.model_dump_json()) == spec

    approved = spec.model_dump(mode="python")
    approved["status"] = "APPROVED"
    approved["fingerprint"] = ZERO
    assert SimulationSpecification.model_validate(approved, strict=False).status is SimulationStatus.APPROVED

    with pytest.raises((TypeError, ValidationError)):
        spec.title = "tampered"

    extra = spec.model_dump(mode="python")
    extra["unexpected"] = "forbidden"
    extra["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="unexpected|extra"):
        SimulationSpecification.model_validate(extra, strict=False)

    tampered = spec.model_dump(mode="python")
    tampered["models"][0]["name"] = "tampered"
    tampered["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="fingerprint"):
        SimulationSpecification.model_validate(tampered, strict=False)


def test_simulation_projection_is_source_driven_and_does_not_fabricate_runtime_facts() -> None:
    hardware, firmware, workflow = _sources()
    spec = generate_simulation_spec(hardware, firmware, workflow)

    model_types = {model.model_type.value for model in spec.models}
    assert {"CONTROLLER", "SENSOR", "ACTUATOR"} <= model_types
    forbidden = (
        "STM32F103", "TB6612", "MPU6050", "PWM", "TIM", "ADC", "I2C",
        "FreeRTOS", "QEMU", "Renode", "Wokwi", "SimulationResult",
    )
    encoded = spec.model_dump_json()
    assert all(value not in encoded for value in forbidden)
    assert all(item.evidence_type is SimulationEvidenceType.UNKNOWN for item in spec.evidence_requirements)
    assert all("Pending" in item.description for item in spec.evidence_requirements)
    assert all(model.model_id in spec.targets for model in spec.models)
    assert spec.design_intent is SimulationDesignIntent.UNKNOWN


def test_explicit_interface_and_firmware_verification_generate_scenario_and_evidence() -> None:
    original_hardware, _, workflow = _sources()
    hardware = _with_interface_type(original_hardware, "CONTROL")
    firmware = generate_firmware_spec(hardware, workflow)
    spec = generate_simulation_spec(hardware, firmware, workflow)

    assert spec.scenarios
    assert any(item.scenario_type is SimulationScenarioType.CONTROL_TEST for item in spec.scenarios)
    assert len(spec.evidence_requirements) == len(spec.scenarios)
    assert all(item.scenario_id in {scenario.scenario_id for scenario in spec.scenarios} for item in spec.evidence_requirements)
    assert spec.design_intent is SimulationDesignIntent.CONTROL_VALIDATION
    model_ids = {model.model_id for model in spec.models}
    assert all(set(scenario.related_models) <= model_ids for scenario in spec.scenarios)


def test_unknown_interface_does_not_create_scenario_or_evidence() -> None:
    original_hardware, _, workflow = _sources()
    data = original_hardware.model_dump(mode="python")
    interfaces = []
    for raw in data["interfaces"]:
        item = dict(raw)
        item["interface_type"] = "UNKNOWN"
        item["fingerprint"] = ZERO
        interfaces.append(item)
    data["interfaces"] = interfaces
    data["fingerprint"] = ZERO
    hardware = HardwareDesignSpecification.model_validate(data, strict=False)
    firmware = generate_firmware_spec(hardware, workflow)
    spec = generate_simulation_spec(hardware, firmware, workflow)

    assert spec.scenarios == ()
    assert spec.evidence_requirements == ()
    assert spec.design_intent is SimulationDesignIntent.UNKNOWN


def test_simulation_projection_rejects_invalid_or_direct_sources() -> None:
    hardware, firmware, workflow = _sources()

    with pytest.raises(TypeError, match="HardwareDesignSpecification"):
        generate_simulation_spec(object(), firmware, workflow)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="FirmwareSpecification"):
        generate_simulation_spec(hardware, object(), workflow)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="EngineeringWorkflow"):
        generate_simulation_spec(hardware, firmware, object())  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        generate_simulation_spec(object(), object(), object())  # type: ignore[arg-type]

    tampered_hardware = hardware.model_copy(update={"fingerprint": "sha256:" + "b" * 64})
    with pytest.raises(ValidationError, match="fingerprint"):
        generate_simulation_spec(tampered_hardware, firmware, workflow)

    tampered_firmware = firmware.model_copy(update={"project_id": "project-foreign", "fingerprint": ZERO})
    with pytest.raises((ValidationError, ValueError), match="project|fingerprint|identity"):
        generate_simulation_spec(hardware, tampered_firmware, workflow)


def test_simulation_contract_rejects_duplicate_ids_and_invalid_references() -> None:
    hardware, firmware, workflow = _sources()
    spec = generate_simulation_spec(hardware, firmware, workflow)

    duplicate = spec.model_dump(mode="python")
    duplicate["models"] = list(duplicate["models"])
    duplicate["models"].append(dict(duplicate["models"][0]))
    duplicate["models"][-1]["model_id"] = duplicate["models"][0]["model_id"]
    duplicate["models"][-1]["fingerprint"] = ZERO
    duplicate["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="unique"):
        SimulationSpecification.model_validate(duplicate, strict=False)

    invalid_ref = spec.model_dump(mode="python")
    invalid_ref["targets"] = ["model-missing"]
    invalid_ref["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="target"):
        SimulationSpecification.model_validate(invalid_ref, strict=False)

    invalid_scenario = spec.model_dump(mode="python")
    invalid_scenario["scenarios"] = list(invalid_scenario["scenarios"])
    if invalid_scenario["scenarios"]:
        invalid_scenario["scenarios"][0]["related_models"] = ["model-missing"]
        invalid_scenario["scenarios"][0]["fingerprint"] = ZERO
        invalid_scenario["fingerprint"] = ZERO
        with pytest.raises(ValidationError, match="model reference"):
            SimulationSpecification.model_validate(invalid_scenario, strict=False)

    unsafe_source = spec.model_dump(mode="python")
    unsafe_source["models"] = list(unsafe_source["models"])
    unsafe_source["models"][0]["source_reference"] = "https://example.invalid/model"
    unsafe_source["models"][0]["fingerprint"] = ZERO
    unsafe_source["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="source_reference"):
        SimulationSpecification.model_validate(unsafe_source, strict=False)


def test_simulation_scenario_does_not_store_execution_result() -> None:
    _, _, _ = _sources()
    assert "result_status" not in SimulationScenario.model_fields
    assert "execution_result" not in SimulationScenario.model_fields
