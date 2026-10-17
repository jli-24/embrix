from __future__ import annotations

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot import (
    CapabilityApprovalStatus,
    CapabilityEvaluation,
    CapabilityRiskLevel,
    EngineeringCapability,
    ExecutionIntentStatus,
    ExecutionIntentType,
    ImplementationIntent,
    ImplementationTask,
    ImplementationTaskType,
    evaluate_capabilities,
    generate_execution_intent,
    generate_implementation_spec,
)
from embedded_copilot.engineering_copilot.common import ZERO

from .test_implementation_planning_layer import _with_explicit_sources


def _spec():
    return generate_implementation_spec(*_with_explicit_sources())


def test_capability_contract_is_strict_frozen_tuple_and_round_trips() -> None:
    evaluation = evaluate_capabilities(_spec())

    assert type(evaluation.capabilities) is tuple
    assert type(evaluation.bindings) is tuple
    assert type(evaluation.approval_requirements) is tuple
    assert all(type(item.source_refs) is tuple for item in evaluation.capabilities)
    assert CapabilityEvaluation.model_validate_json(evaluation.model_dump_json()) == evaluation

    with pytest.raises((TypeError, ValidationError)):
        evaluation.project_id = "project-other"

    extra = evaluation.model_dump(mode="python")
    extra["unexpected"] = "forbidden"
    extra["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="unexpected|extra"):
        CapabilityEvaluation.model_validate(extra, strict=False)

    tampered = evaluation.model_dump(mode="python")
    tampered["capabilities"][0]["description"] = "tampered capability"
    tampered["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="fingerprint"):
        CapabilityEvaluation.model_validate(tampered, strict=False)

    with pytest.raises(ValidationError, match="approval"):
        EngineeringCapability.create(
            capability_id="PROJECT_BUILD",
            name="Project Build",
            category="FIRMWARE",
            description="High risk capability",
            risk_level="HIGH",
            requires_approval=False,
            status="PLANNED",
            source_refs=("implementation-task-1",),
        )

    missing_approval = evaluation.model_dump(mode="python")
    missing_approval["approval_requirements"] = ()
    missing_approval["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="no approval requirement"):
        CapabilityEvaluation.model_validate(missing_approval, strict=False)


def test_capability_evaluation_is_deterministic_and_task_bound() -> None:
    spec = _spec()
    first = evaluate_capabilities(spec)
    second = evaluate_capabilities(spec)
    intents = tuple(generate_execution_intent(task) for task in spec.tasks)

    assert first == second
    assert len(first.bindings) == len(spec.tasks)
    assert tuple(intent.source_task_refs[0] for intent in intents) == tuple(
        task.task_id for task in spec.tasks
    )
    assert all(intent.status is ExecutionIntentStatus.PLANNED for intent in intents)
    assert {intent.intent_type for intent in intents} <= {
        ExecutionIntentType.CREATE_ARTIFACT,
        ExecutionIntentType.GENERATE_SOURCE,
    }
    assert {
        capability.capability_id for capability in first.capabilities
    } == {"ARTIFACT_CREATION", "FIRMWARE_CODE_GENERATION"}
    assert all(
        requirement.approval_status is CapabilityApprovalStatus.PENDING
        and requirement.risk_level in {
            CapabilityRiskLevel.HIGH,
            CapabilityRiskLevel.CRITICAL,
        }
        for requirement in first.approval_requirements
    )
    assert len(first.approval_requirements) == sum(
        "FIRMWARE_CODE_GENERATION" in intent.required_capabilities
        for intent in intents
    )


def test_capability_mapping_uses_structured_task_fields_not_free_text() -> None:
    task = _spec().tasks[0]
    original = generate_execution_intent(task)
    renamed = ImplementationTask.create(
        **{
            **task.model_dump(mode="python"),
            "title": "Motor PID STM32 PCB KiCad HAL",
            "description": "Arbitrary free text must not select a capability",
            "fingerprint": ZERO,
        }
    )

    projected = generate_execution_intent(renamed)
    assert projected.intent_type is original.intent_type
    assert projected.required_capabilities == original.required_capabilities

    invalid = ImplementationTask.create(
        **{
            **task.model_dump(mode="python"),
            "task_type": ImplementationTaskType.DOCUMENTATION,
            "implementation_intent": ImplementationIntent.CONTROL_IMPLEMENTATION,
            "fingerprint": ZERO,
        }
    )
    with pytest.raises(ValueError, match="mapping|combination"):
        generate_execution_intent(invalid)


def test_capability_evaluation_rejects_unknown_capability_binding() -> None:
    evaluation = evaluate_capabilities(_spec())
    payload = evaluation.model_dump(mode="python")
    payload["bindings"] = [
        *payload["bindings"],
        {
            "binding_id": "binding-unknown",
            "implementation_task_id": evaluation.bindings[0].implementation_task_id,
            "capability_id": "UNKNOWN_CAPABILITY",
            "required": True,
            "reason": "Explicit unknown capability test",
            "fingerprint": ZERO,
        },
    ]
    payload["fingerprint"] = ZERO

    with pytest.raises(ValidationError, match="unknown capability"):
        CapabilityEvaluation.model_validate(payload, strict=False)
