from __future__ import annotations

import copy
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import field_validator, model_validator

from embedded_copilot.engineering_copilot.common import (
    Fingerprinted,
    finalize,
    ident,
    text,
    tuple_values,
)


class EngineeringTaskStage(StrEnum):
    REQUIREMENT = "REQUIREMENT"
    HARDWARE = "HARDWARE"
    FIRMWARE = "FIRMWARE"
    DEBUG = "DEBUG"
    VALIDATION = "VALIDATION"
    COMPLETE = "COMPLETE"


class EngineeringTaskStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"


class EngineeringFailureClass(StrEnum):
    NONE = "NONE"
    INPUT = "INPUT"
    ENGINEERING = "ENGINEERING"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    APPROVAL = "APPROVAL"


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise TypeError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _strings(
    value: object,
    field: str,
    *,
    maximum: int = 512,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    values = tuple_values(value, field)
    checked = tuple(text(item, field, maximum) for item in values)
    if not allow_empty and not checked:
        raise ValueError(f"{field} must not be empty")
    if len(checked) != len(set(checked)):
        raise ValueError(f"{field} must be unique")
    return checked


class EngineeringTaskRequest(Fingerprinted):
    task_id: str
    tenant_id: str
    project_id: str
    objective: str
    constraints: tuple[str, ...] = ()
    created_at: datetime

    @field_validator("task_id", "tenant_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator("objective", mode="before")
    @classmethod
    def _objective(cls, value: object) -> str:
        return text(value, "objective", 1024)

    @field_validator("constraints", mode="before")
    @classmethod
    def _constraints(cls, value: object) -> tuple[str, ...]:
        return _strings(value, "constraints", maximum=256)

    @field_validator("created_at", mode="before")
    @classmethod
    def _created_at(cls, value: object) -> datetime:
        return _utc(value, "created_at")


class EngineeringPlanStep(Fingerprinted):
    step_id: str
    task_id: str
    tenant_id: str
    project_id: str
    stage: EngineeringTaskStage
    objective: str
    dependencies: tuple[str, ...]
    required_context: tuple[str, ...]
    status: EngineeringTaskStatus = EngineeringTaskStatus.PENDING
    round_index: int = 0

    @field_validator(
        "step_id", "task_id", "tenant_id", "project_id", mode="before"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator("objective", mode="before")
    @classmethod
    def _objective(cls, value: object) -> str:
        return text(value, "objective", 512)

    @field_validator("dependencies", mode="before")
    @classmethod
    def _dependencies(cls, value: object) -> tuple[str, ...]:
        checked = _strings(value, "dependencies", maximum=128)
        return tuple(ident(item, "dependency") for item in checked)

    @field_validator("required_context", mode="before")
    @classmethod
    def _required_context(cls, value: object) -> tuple[str, ...]:
        return _strings(value, "required_context", maximum=128)

    @field_validator("round_index", mode="before")
    @classmethod
    def _round_index(cls, value: object) -> int:
        if type(value) is not int or not 0 <= value <= 3:
            raise ValueError("round_index is invalid")
        return value

    @model_validator(mode="after")
    def _pending_only(self) -> EngineeringPlanStep:
        if self.status is not EngineeringTaskStatus.PENDING:
            raise ValueError("planned step status must be PENDING")
        if self.stage in {
            EngineeringTaskStage.DEBUG,
            EngineeringTaskStage.VALIDATION,
        }:
            if self.stage is EngineeringTaskStage.DEBUG and self.round_index == 0:
                raise ValueError("debug step requires a positive round index")
        elif self.round_index != 0:
            raise ValueError("only debug or validation steps may have a round index")
        return self

    @classmethod
    def create(cls, **values: object) -> EngineeringPlanStep:
        return finalize(cls, values)


def _expected_stages(max_debug_rounds: int) -> tuple[tuple[EngineeringTaskStage, int], ...]:
    values: list[tuple[EngineeringTaskStage, int]] = [
        (EngineeringTaskStage.REQUIREMENT, 0),
        (EngineeringTaskStage.HARDWARE, 0),
        (EngineeringTaskStage.FIRMWARE, 0),
        (EngineeringTaskStage.VALIDATION, 0),
    ]
    for round_index in range(1, max_debug_rounds + 1):
        values.extend(
            (
                (EngineeringTaskStage.DEBUG, round_index),
                (EngineeringTaskStage.VALIDATION, round_index),
            )
        )
    values.append((EngineeringTaskStage.COMPLETE, 0))
    return tuple(values)


class EngineeringPlan(Fingerprinted):
    task_id: str
    tenant_id: str
    project_id: str
    steps: tuple[EngineeringPlanStep, ...]
    created_at: datetime
    max_debug_rounds: int

    @field_validator("task_id", "tenant_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator("steps", mode="before")
    @classmethod
    def _steps(cls, value: object) -> tuple[EngineeringPlanStep, ...]:
        values = tuple_values(value, "steps")
        return tuple(
            EngineeringPlanStep.model_validate(
                copy.deepcopy(
                    item.model_dump(mode="python")
                    if type(item) is EngineeringPlanStep
                    else item
                )
            )
            for item in values
        )

    @field_validator("created_at", mode="before")
    @classmethod
    def _created_at(cls, value: object) -> datetime:
        return _utc(value, "created_at")

    @field_validator("max_debug_rounds", mode="before")
    @classmethod
    def _debug_bound(cls, value: object) -> int:
        if type(value) is not int or not 0 <= value <= 3:
            raise ValueError("max_debug_rounds is invalid")
        return value

    @model_validator(mode="after")
    def _validate_plan(self) -> EngineeringPlan:
        expected = _expected_stages(self.max_debug_rounds)
        actual = tuple((step.stage, step.round_index) for step in self.steps)
        if actual != expected:
            raise ValueError("engineering plan stage order is invalid")
        ids = tuple(step.step_id for step in self.steps)
        if len(ids) != len(set(ids)):
            raise ValueError("engineering plan step identities must be unique")
        for index, step in enumerate(self.steps):
            if (
                step.task_id != self.task_id
                or step.tenant_id != self.tenant_id
                or step.project_id != self.project_id
            ):
                raise ValueError("engineering plan identity binding mismatch")
            expected_dependencies = () if index == 0 else (self.steps[index - 1].step_id,)
            if step.dependencies != expected_dependencies:
                raise ValueError("engineering plan dependency is invalid")
        return self

    @classmethod
    def create(cls, **values: object) -> EngineeringPlan:
        return finalize(cls, values)


class EngineeringStepResult(Fingerprinted):
    step_id: str
    task_id: str
    tenant_id: str
    project_id: str
    stage: EngineeringTaskStage
    status: EngineeringTaskStatus
    failure_class: EngineeringFailureClass
    reason_code: str
    summary: str
    evidence_references: tuple[str, ...] = ()
    trace_reference: str

    @field_validator(
        "step_id", "task_id", "tenant_id", "project_id", "reason_code", mode="before"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator("summary", mode="before")
    @classmethod
    def _summary(cls, value: object) -> str:
        return text(value, "summary", 1024)

    @field_validator("evidence_references", mode="before")
    @classmethod
    def _evidence(cls, value: object) -> tuple[str, ...]:
        return _strings(value, "evidence_references", maximum=256)

    @field_validator("trace_reference", mode="before")
    @classmethod
    def _trace(cls, value: object) -> str:
        return text(value, "trace_reference", 256)

    @model_validator(mode="after")
    def _outcome(self) -> EngineeringStepResult:
        if self.status is EngineeringTaskStatus.COMPLETED:
            if self.failure_class is not EngineeringFailureClass.NONE:
                raise ValueError("completed step cannot carry failure")
        elif self.status is EngineeringTaskStatus.AWAITING_APPROVAL:
            if self.failure_class is not EngineeringFailureClass.APPROVAL:
                raise ValueError("approval step requires approval failure class")
        elif self.status is EngineeringTaskStatus.FAILED:
            if self.failure_class is EngineeringFailureClass.NONE:
                raise ValueError("failed step requires failure class")
        else:
            raise ValueError("step result must be terminal")
        return self

    @classmethod
    def create(cls, **values: object) -> EngineeringStepResult:
        return finalize(cls, values)


class EngineeringTaskResult(Fingerprinted):
    result_id: str
    task_id: str
    tenant_id: str
    project_id: str
    plan_fingerprint: str
    step_results: tuple[EngineeringStepResult, ...]
    validation_summary: str
    memory_references: tuple[str, ...]
    status: EngineeringTaskStatus
    debug_rounds_used: int

    @field_validator(
        "result_id", "task_id", "tenant_id", "project_id", mode="before"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator("plan_fingerprint", mode="before")
    @classmethod
    def _plan_fingerprint(cls, value: object) -> str:
        from embedded_copilot.engineering_copilot.common import fp

        return fp(value, "plan_fingerprint")

    @field_validator("step_results", mode="before")
    @classmethod
    def _step_results(cls, value: object) -> tuple[EngineeringStepResult, ...]:
        values = tuple_values(value, "step_results")
        return tuple(
            EngineeringStepResult.model_validate(
                copy.deepcopy(
                    item.model_dump(mode="python")
                    if type(item) is EngineeringStepResult
                    else item
                )
            )
            for item in values
        )

    @field_validator("validation_summary", mode="before")
    @classmethod
    def _validation_summary(cls, value: object) -> str:
        return text(value, "validation_summary", 1024)

    @field_validator("memory_references", mode="before")
    @classmethod
    def _memory_references(cls, value: object) -> tuple[str, ...]:
        return _strings(value, "memory_references", maximum=256)

    @field_validator("debug_rounds_used", mode="before")
    @classmethod
    def _debug_rounds_used(cls, value: object) -> int:
        if type(value) is not int or not 0 <= value <= 3:
            raise ValueError("debug_rounds_used is invalid")
        return value

    @model_validator(mode="after")
    def _binding(self) -> EngineeringTaskResult:
        if not self.step_results:
            raise ValueError("task result requires step results")
        if any(
            result.task_id != self.task_id
            or result.tenant_id != self.tenant_id
            or result.project_id != self.project_id
            for result in self.step_results
        ):
            raise ValueError("task result identity binding mismatch")
        if self.status not in {
            EngineeringTaskStatus.COMPLETED,
            EngineeringTaskStatus.FAILED,
            EngineeringTaskStatus.AWAITING_APPROVAL,
        }:
            raise ValueError("task result must be terminal")
        return self

    @classmethod
    def create(cls, **values: object) -> EngineeringTaskResult:
        return finalize(cls, values)


__all__ = (
    "EngineeringFailureClass",
    "EngineeringPlan",
    "EngineeringPlanStep",
    "EngineeringStepResult",
    "EngineeringTaskRequest",
    "EngineeringTaskResult",
    "EngineeringTaskStage",
    "EngineeringTaskStatus",
)
