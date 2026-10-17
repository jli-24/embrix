from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot import (
    ArchitectureDesignStatus,
    HardwareCandidateType,
    HardwareDecision,
    HardwareDecisionStatus,
    HardwareDesignPlanStatus,
    HardwareDesignSpecification,
    HardwareDomainType,
    HardwareInterfaceType,
    generate_engineering_workflow,
    generate_hardware_design_spec,
)
from embedded_copilot.engineering_copilot.common import ZERO
from embedded_copilot.engineering_copilot.contracts import ArchitectureDesign

from .test_workflow import _approved_sources

NOW = datetime(2026, 9, 3, tzinfo=UTC)


def test_hardware_contract_is_strict_frozen_tuple_and_round_trips() -> None:
    plan, architecture = _approved_sources()
    workflow = generate_engineering_workflow(architecture, plan)
    spec = generate_hardware_design_spec(architecture, workflow)

    assert spec.hardware_domains
    assert all(type(item.requirements) is tuple for item in spec.hardware_domains)
    assert HardwareDomainType.SYSTEM in {item.type for item in spec.hardware_domains}
    assert isinstance(spec.design_revision, str)
    assert spec.architecture_fingerprint == architecture.fingerprint
    assert spec.workflow_fingerprint == workflow.fingerprint
    assert HardwareDesignSpecification.model_validate_json(spec.model_dump_json()) == spec
    with pytest.raises((TypeError, ValidationError)):
        spec.title = "tampered"

    tampered = spec.model_dump(mode="python")
    tampered["hardware_domains"][0]["name"] = "tampered domain"
    with pytest.raises(ValidationError, match="fingerprint"):
        HardwareDesignSpecification.model_validate(tampered, strict=False)

    extra = spec.model_dump(mode="python")
    extra["unexpected"] = "forbidden"
    with pytest.raises(ValidationError, match="unexpected"):
        HardwareDesignSpecification.model_validate(extra, strict=False)


def test_hardware_projection_is_explicit_and_does_not_fabricate_parts_or_signals() -> None:
    plan, architecture = _approved_sources()
    workflow = generate_engineering_workflow(architecture, plan)
    spec = generate_hardware_design_spec(architecture, workflow)

    names = {domain.name for domain in spec.hardware_domains}
    assert {"System", "STM32", "Power", "Sensor/Input", "Motor/Actuator"} <= names
    assert all(
        forbidden not in spec.model_dump_json()
        for forbidden in ("STM32F103", "TB6612", "MPU6050", "TIM1_CH1", "PWM")
    )
    assert all(item.interface_type is HardwareInterfaceType.UNKNOWN for item in spec.interfaces)
    assert all(item.direction == "UNKNOWN" for item in spec.interfaces)
    assert all(item.status is HardwareDecisionStatus.OPEN for item in spec.decisions)
    assert all(item.selected_option is None for item in spec.decisions)
    assert spec.schematic_plan is not None
    assert spec.schematic_plan.status is HardwareDesignPlanStatus.PLANNED
    assert spec.pcb_plan is None


def test_hardware_decision_requires_selected_option_only_when_confirmed() -> None:
    with pytest.raises(ValidationError, match="selected_option"):
        HardwareDecision.create(
            decision_id="decision-1",
            subject="power architecture",
            options=("Battery + Buck", "Battery Direct"),
            selected_option="Battery + Buck",
            rationale="review required",
            status=HardwareDecisionStatus.OPEN,
            evidence_refs=(),
        )

    confirmed = HardwareDecision.create(
        decision_id="decision-1",
        subject="power architecture",
        options=("Battery + Buck", "Battery Direct"),
        selected_option="Battery + Buck",
        rationale="reviewed",
        status=HardwareDecisionStatus.CONFIRMED,
        evidence_refs=(),
    )
    assert confirmed.selected_option == "Battery + Buck"


def test_hardware_projection_rejects_unapproved_or_cross_project_sources() -> None:
    plan, architecture = _approved_sources()
    workflow = generate_engineering_workflow(architecture, plan)
    proposed_data = architecture.model_dump(mode="python")
    proposed_data["status"] = ArchitectureDesignStatus.PROPOSED
    proposed_data["fingerprint"] = ZERO
    proposed = ArchitectureDesign.model_validate(proposed_data, strict=False)
    with pytest.raises(ValueError, match="approved"):
        generate_hardware_design_spec(proposed, workflow)

    foreign_data = workflow.model_dump(mode="python")
    foreign_data["project_id"] = "project-foreign"
    foreign_data["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="provenance identity"):
        type(workflow).model_validate(foreign_data, strict=False)

    with pytest.raises(TypeError, match="ArchitectureDesign"):
        generate_hardware_design_spec(object(), workflow)  # type: ignore[arg-type]

    tampered_workflow = workflow.model_copy(
        update={"fingerprint": "sha256:" + "b" * 64}
    )
    with pytest.raises(ValidationError, match="fingerprint"):
        generate_hardware_design_spec(architecture, tampered_workflow)


def test_hardware_candidate_type_is_explicitly_typed() -> None:
    assert {item.value for item in HardwareCandidateType} == {
        "CAPABILITY", "DEVICE", "MODULE", "UNKNOWN"
    }
