from __future__ import annotations

import copy
import hashlib

from .models import (
    EngineeringPlan,
    EngineeringPlanStep,
    EngineeringTaskRequest,
    EngineeringTaskStage,
)

MAX_DEBUG_ROUNDS = 3

_OBJECTIVES = {
    EngineeringTaskStage.REQUIREMENT: "Project the request as a non-authoritative proposed requirement",
    EngineeringTaskStage.HARDWARE: "Produce a hardware engineering proposal",
    EngineeringTaskStage.FIRMWARE: "Produce a firmware engineering proposal",
    EngineeringTaskStage.VALIDATION: "Validate referenced engineering outputs",
    EngineeringTaskStage.DEBUG: "Analyze the preceding engineering validation failure",
    EngineeringTaskStage.COMPLETE: "Assemble the bounded engineering task result",
}

_CONTEXT = {
    EngineeringTaskStage.REQUIREMENT: ("request", "authoritative_context"),
    EngineeringTaskStage.HARDWARE: ("engineering_context", "candidate_memory"),
    EngineeringTaskStage.FIRMWARE: (
        "hardware_result",
        "engineering_context",
        "candidate_memory",
    ),
    EngineeringTaskStage.VALIDATION: (
        "engineering_results",
        "authoritative_evidence",
    ),
    EngineeringTaskStage.DEBUG: (
        "validation_failure",
        "engineering_context",
        "candidate_memory",
    ),
    EngineeringTaskStage.COMPLETE: ("validation_result",),
}


def _step_id(task_id: str, index: int, stage: EngineeringTaskStage) -> str:
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:12]
    return f"step-{index:02d}-{stage.value.lower()}-{digest}"


class EngineeringTaskPlanner:
    """Small deterministic planner for one bounded engineering workflow."""

    __slots__ = ("_max_debug_rounds",)

    def __init__(self, *, max_debug_rounds: int = MAX_DEBUG_ROUNDS) -> None:
        if type(max_debug_rounds) is not int or not 0 <= max_debug_rounds <= MAX_DEBUG_ROUNDS:
            raise ValueError("max_debug_rounds is invalid")
        self._max_debug_rounds = max_debug_rounds

    def plan(self, request: EngineeringTaskRequest) -> EngineeringPlan:
        if type(request) is not EngineeringTaskRequest:
            raise TypeError("engineering task request is invalid")
        checked = EngineeringTaskRequest.model_validate(
            copy.deepcopy(request.model_dump(mode="python"))
        )
        stages: list[tuple[EngineeringTaskStage, int]] = [
            (EngineeringTaskStage.REQUIREMENT, 0),
            (EngineeringTaskStage.HARDWARE, 0),
            (EngineeringTaskStage.FIRMWARE, 0),
            (EngineeringTaskStage.VALIDATION, 0),
        ]
        for round_index in range(1, self._max_debug_rounds + 1):
            stages.extend(
                (
                    (EngineeringTaskStage.DEBUG, round_index),
                    (EngineeringTaskStage.VALIDATION, round_index),
                )
            )
        stages.append((EngineeringTaskStage.COMPLETE, 0))

        steps: list[EngineeringPlanStep] = []
        for index, (stage, round_index) in enumerate(stages, start=1):
            step_id = _step_id(checked.task_id, index, stage)
            steps.append(
                EngineeringPlanStep.create(
                    step_id=step_id,
                    task_id=checked.task_id,
                    tenant_id=checked.tenant_id,
                    project_id=checked.project_id,
                    stage=stage,
                    objective=_OBJECTIVES[stage],
                    dependencies=() if not steps else (steps[-1].step_id,),
                    required_context=_CONTEXT[stage],
                    round_index=round_index,
                )
            )
        return EngineeringPlan.create(
            task_id=checked.task_id,
            tenant_id=checked.tenant_id,
            project_id=checked.project_id,
            steps=tuple(steps),
            created_at=checked.created_at,
            max_debug_rounds=self._max_debug_rounds,
        )


__all__ = ("MAX_DEBUG_ROUNDS", "EngineeringTaskPlanner")
