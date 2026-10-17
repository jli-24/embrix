from __future__ import annotations

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot import (
    HardwareDesignSpecification,
    SimulationSpecification,
    VerificationAcceptanceMapping,
    VerificationCase,
    VerificationCaseType,
    VerificationEvidenceType,
    VerificationSpecification,
    VerificationStatus,
    generate_engineering_workflow,
    generate_firmware_spec,
    generate_hardware_design_spec,
    generate_simulation_spec,
    generate_verification_spec,
)
from embedded_copilot.engineering_copilot.common import ZERO

from .test_workflow import _approved_sources


def _sources():
    plan, architecture = _approved_sources()
    workflow = generate_engineering_workflow(architecture, plan)
    hardware = generate_hardware_design_spec(architecture, workflow)
    firmware = generate_firmware_spec(hardware, workflow)
    simulation = generate_simulation_spec(hardware, firmware, workflow)
    return hardware, firmware, simulation, workflow


def _with_explicit_control_sources():
    hardware, _, _, workflow = _sources()
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
    scenario = dict(simulation_data["scenarios"][0])
    scenario["acceptance_refs"] = ["requirement-motor-control"]
    scenario["fingerprint"] = ZERO
    evidence = dict(simulation_data["evidence_requirements"][0])
    evidence["evidence_type"] = "WAVEFORM"
    evidence["fingerprint"] = ZERO
    simulation_data["scenarios"] = [scenario]
    simulation_data["evidence_requirements"] = [evidence]
    simulation_data["fingerprint"] = ZERO
    simulation = SimulationSpecification.model_validate(simulation_data, strict=False)
    return hardware, firmware, simulation, workflow


def test_verification_contract_is_strict_frozen_tuple_versioned_and_round_trips() -> None:
    hardware, firmware, simulation, workflow = _with_explicit_control_sources()
    spec = generate_verification_spec(hardware, firmware, simulation, workflow)

    assert spec.schema_version == "1.0"
    assert spec.status is VerificationStatus.PLANNED
    assert type(spec.cases) is tuple
    assert type(spec.evidence_requirements) is tuple
    assert type(spec.acceptance_mapping) is tuple
    assert all(type(case.expected_behavior) is tuple for case in spec.cases)
    assert VerificationSpecification.model_validate_json(spec.model_dump_json()) == spec

    with pytest.raises((TypeError, ValidationError)):
        spec.title = "tampered"

    extra = spec.model_dump(mode="python")
    extra["unexpected"] = "forbidden"
    extra["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="unexpected|extra"):
        VerificationSpecification.model_validate(extra, strict=False)

    wrong_version = spec.model_dump(mode="python")
    wrong_version["schema_version"] = "2.0"
    wrong_version["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="schema_version"):
        VerificationSpecification.model_validate(wrong_version, strict=False)

    tampered = spec.model_dump(mode="python")
    tampered["cases"][0]["title"] = "tampered"
    tampered["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="fingerprint"):
        VerificationSpecification.model_validate(tampered, strict=False)


def test_verification_projection_is_source_driven_and_has_no_runtime_result() -> None:
    hardware, firmware, simulation, workflow = _with_explicit_control_sources()
    spec = generate_verification_spec(hardware, firmware, simulation, workflow)

    assert spec.cases
    assert any(case.case_type is VerificationCaseType.CONTROL for case in spec.cases)
    assert spec.verification_intent.value == "CONTROL_VALIDATION"
    assert all(case.verification_intent is not None for case in spec.cases)
    assert any(case.verification_intent.value == "CONTROL_VALIDATION" for case in spec.cases)
    assert all(case.status is VerificationStatus.PLANNED for case in spec.cases)
    assert all(item.status is VerificationStatus.PLANNED for item in spec.cases)
    assert any(item.evidence_type is VerificationEvidenceType.WAVEFORM for item in spec.evidence_requirements)
    assert spec.acceptance_mapping
    assert all(isinstance(item, VerificationAcceptanceMapping) for item in spec.acceptance_mapping)

    forbidden = (
        "STM32F103", "TB6612", "MPU6050", "PWM", "TIM", "ADC", "PID", "CAN", "I2C",
        "PASS", "FAIL", "verification_result", "execution_result", "measurement_result",
    )
    encoded = spec.model_dump_json()
    assert all(value not in encoded for value in forbidden)
    assert "verification_result" not in VerificationCase.model_fields
    assert "execution_result" not in VerificationCase.model_fields
    assert "actual_evidence" not in type(spec.evidence_requirements[0]).model_fields


def test_verification_contract_rejects_invalid_mapping_and_service_rejects_direct_sources() -> None:
    hardware, firmware, simulation, workflow = _with_explicit_control_sources()
    spec = generate_verification_spec(hardware, firmware, simulation, workflow)

    invalid = spec.model_dump(mode="python")
    mapping = dict(invalid["acceptance_mapping"][0])
    mapping["case_id"] = "case-missing"
    mapping["fingerprint"] = ZERO
    invalid["acceptance_mapping"] = [mapping]
    invalid["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="mapping case reference"):
        VerificationSpecification.model_validate(invalid, strict=False)

    with pytest.raises(TypeError, match="HardwareDesignSpecification"):
        generate_verification_spec(object(), firmware, simulation, workflow)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="FirmwareSpecification"):
        generate_verification_spec(hardware, object(), simulation, workflow)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="SimulationSpecification"):
        generate_verification_spec(hardware, firmware, object(), workflow)  # type: ignore[arg-type]

    foreign = simulation.model_copy(update={"project_id": "project-foreign", "fingerprint": ZERO})
    with pytest.raises((ValidationError, ValueError), match="project|fingerprint|identity"):
        generate_verification_spec(hardware, firmware, foreign, workflow)
