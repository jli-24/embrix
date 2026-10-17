from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_task_runtime.models import (
    EngineeringPlan,
    EngineeringPlanStep,
    EngineeringTaskRequest,
    EngineeringTaskStage,
    EngineeringTaskStatus,
)
from embedded_copilot.engineering_task_runtime.planner import (
    MAX_DEBUG_ROUNDS,
    EngineeringTaskPlanner,
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def _request(**changes: object) -> EngineeringTaskRequest:
    values: dict[str, object] = {
        "task_id": "task-1",
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "objective": "Design an STM32 motor controller",
        "constraints": ("Use CAN", "Keep validation evidence"),
        "created_at": NOW,
    }
    values.update(changes)
    return EngineeringTaskRequest(**values)


def test_planner_builds_deterministic_bounded_plan() -> None:
    planner = EngineeringTaskPlanner(max_debug_rounds=2)

    first = planner.plan(_request())
    second = planner.plan(_request())

    assert first == second
    assert first.fingerprint == second.fingerprint
    assert first.max_debug_rounds == 2
    assert tuple(step.stage for step in first.steps) == (
        EngineeringTaskStage.REQUIREMENT,
        EngineeringTaskStage.HARDWARE,
        EngineeringTaskStage.FIRMWARE,
        EngineeringTaskStage.VALIDATION,
        EngineeringTaskStage.DEBUG,
        EngineeringTaskStage.VALIDATION,
        EngineeringTaskStage.DEBUG,
        EngineeringTaskStage.VALIDATION,
        EngineeringTaskStage.COMPLETE,
    )
    assert all(step.status is EngineeringTaskStatus.PENDING for step in first.steps)
    assert all(
        dependency in {prior.step_id for prior in first.steps[:index]}
        for index, step in enumerate(first.steps)
        for dependency in step.dependencies
    )


def test_debug_round_limit_is_strict_and_capped() -> None:
    assert MAX_DEBUG_ROUNDS == 3
    with pytest.raises((TypeError, ValueError)):
        EngineeringTaskPlanner(max_debug_rounds=True)
    with pytest.raises(ValueError):
        EngineeringTaskPlanner(max_debug_rounds=MAX_DEBUG_ROUNDS + 1)


def test_plan_rejects_invalid_dependency_and_stage_order() -> None:
    plan = EngineeringTaskPlanner(max_debug_rounds=1).plan(_request())
    steps = list(plan.steps)
    material = steps[1].model_dump(mode="python")
    material.pop("fingerprint")
    material["dependencies"] = ("missing-step",)
    steps[1] = EngineeringPlanStep.create(**material)
    with pytest.raises(ValidationError):
        EngineeringPlan.create(
            task_id=plan.task_id,
            tenant_id=plan.tenant_id,
            project_id=plan.project_id,
            steps=tuple(steps),
            created_at=plan.created_at,
            max_debug_rounds=plan.max_debug_rounds,
        )

    reordered = list(plan.steps)
    reordered[1], reordered[2] = reordered[2], reordered[1]
    with pytest.raises(ValidationError):
        EngineeringPlan.create(
            task_id=plan.task_id,
            tenant_id=plan.tenant_id,
            project_id=plan.project_id,
            steps=tuple(reordered),
            created_at=plan.created_at,
            max_debug_rounds=plan.max_debug_rounds,
        )


def test_plan_rejects_cross_project_or_cross_tenant_steps() -> None:
    plan = EngineeringTaskPlanner(max_debug_rounds=0).plan(_request())
    step = plan.steps[0]
    material = step.model_dump(mode="python")
    material.pop("fingerprint")
    material["project_id"] = "project-2"
    cross_project = EngineeringPlanStep.create(**material)

    with pytest.raises(ValidationError):
        EngineeringPlan.create(
            task_id=plan.task_id,
            tenant_id=plan.tenant_id,
            project_id=plan.project_id,
            steps=(cross_project, *plan.steps[1:]),
            created_at=plan.created_at,
            max_debug_rounds=plan.max_debug_rounds,
        )


def test_models_are_strict_frozen_and_fingerprints_reject_tampering() -> None:
    request = _request()
    with pytest.raises((TypeError, ValidationError)):
        request.project_id = "project-2"
    with pytest.raises(ValidationError):
        EngineeringTaskRequest(
            **request.model_dump(mode="python"),
            unexpected="field",
        )

    plan = EngineeringTaskPlanner(max_debug_rounds=1).plan(request)
    tampered = plan.model_dump(mode="python")
    tampered["fingerprint"] = "sha256:" + "a" * 64
    with pytest.raises(ValidationError):
        EngineeringPlan.model_validate(tampered)


def test_time_is_normalized_and_requirement_is_never_confirmed() -> None:
    plus_eight = datetime(2026, 8, 27, 20, 0, tzinfo=timezone(timedelta(hours=8)))
    request = _request(created_at=plus_eight)
    plan = EngineeringTaskPlanner(max_debug_rounds=0).plan(request)

    assert request.created_at == NOW
    assert plan.created_at == NOW
    requirement = plan.steps[0]
    assert requirement.stage is EngineeringTaskStage.REQUIREMENT
    assert "CONFIRMED" not in requirement.objective.upper()
    assert "CONFIRMED" not in requirement.required_context
