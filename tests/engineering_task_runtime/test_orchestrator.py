from __future__ import annotations

from collections import deque
from datetime import UTC, datetime

import pytest

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.engineering_memory.foundation import (
    FoundationEngineeringMemoryRecord,
    FoundationMemoryType,
    ProjectMemoryPayload,
)
from embedded_copilot.engineering_memory.foundation_repository import (
    InMemoryEngineeringMemoryRepository,
)
from embedded_copilot.engineering_memory.foundation_service import (
    EngineeringMemoryService,
)
from embedded_copilot.engineering_task_runtime.exceptions import (
    EngineeringTaskRejected,
)
from embedded_copilot.engineering_task_runtime.models import (
    EngineeringFailureClass,
    EngineeringStepResult,
    EngineeringTaskRequest,
    EngineeringTaskStage,
    EngineeringTaskStatus,
)
from embedded_copilot.engineering_task_runtime.orchestrator import (
    EngineeringTaskOrchestrator,
)
from embedded_copilot.engineering_task_runtime.planner import EngineeringTaskPlanner

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def _request() -> EngineeringTaskRequest:
    return EngineeringTaskRequest(
        task_id="task-1",
        tenant_id="tenant-1",
        project_id="project-1",
        objective="Design an STM32 motor controller",
        constraints=("Use CAN",),
        created_at=NOW,
    )


def _context(*, project_id: str = "project-1") -> EngineeringContextSnapshot:
    return EngineeringContextSnapshot.create(
        project_id=project_id,
        tenant_id="tenant-1",
        task_description="Design an STM32 motor controller",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.0,
        provenance=(),
        verification_status="UNVERIFIED",
    )


class _ContextProvider:
    def __init__(self, snapshot: EngineeringContextSnapshot | None = None) -> None:
        self.snapshot = _context() if snapshot is None else snapshot
        self.calls = 0

    def get_context(self, request):
        self.calls += 1
        return self.snapshot


def _memory_service() -> EngineeringMemoryService:
    service = EngineeringMemoryService(InMemoryEngineeringMemoryRepository())
    service.save_memory(
        FoundationEngineeringMemoryRecord.create(
            memory_id="project-memory-1",
            tenant_id="tenant-1",
            project_id="project-1",
            memory_type=FoundationMemoryType.PROJECT,
            payload=ProjectMemoryPayload(
                project_id="project-1",
                project_type="motor_controller",
                hardware_target="STM32F407",
                requirement_summary="CAN motor control",
                architecture_summary="FreeRTOS task architecture",
            ),
            created_at=NOW,
            source_references=("memory:project-1",),
        )
    )
    return service


def _step_result(
    step,
    *,
    status: EngineeringTaskStatus = EngineeringTaskStatus.COMPLETED,
    failure_class: EngineeringFailureClass = EngineeringFailureClass.NONE,
    reason_code: str = "STEP_COMPLETED",
    summary: str = "engineering step completed",
) -> EngineeringStepResult:
    return EngineeringStepResult.create(
        step_id=step.step_id,
        task_id=step.task_id,
        tenant_id=step.tenant_id,
        project_id=step.project_id,
        stage=step.stage,
        status=status,
        failure_class=failure_class,
        reason_code=reason_code,
        summary=summary,
        evidence_references=(f"evidence:{step.step_id}",),
        trace_reference=f"trace:{step.step_id}",
    )


class _RoleExecutor:
    def __init__(
        self,
        *,
        validation_outcomes: tuple[EngineeringFailureClass, ...] = (
            EngineeringFailureClass.NONE,
        ),
        fail_stage: EngineeringTaskStage | None = None,
    ) -> None:
        self.validation_outcomes = deque(validation_outcomes)
        self.fail_stage = fail_stage
        self.calls: list[EngineeringTaskStage] = []
        self.memory_seen: list[tuple] = []

    def execute_step(
        self, step, request, engineering_context, memory_context, prior_results
    ):
        self.calls.append(step.stage)
        self.memory_seen.append(memory_context)
        if step.stage is self.fail_stage:
            return _step_result(
                step,
                status=EngineeringTaskStatus.FAILED,
                failure_class=EngineeringFailureClass.ENGINEERING,
                reason_code=f"{step.stage.value}_REJECTED",
                summary=f"{step.stage.value.lower()} engineering failed",
            )
        if step.stage is EngineeringTaskStage.VALIDATION:
            outcome = self.validation_outcomes.popleft()
            if outcome is not EngineeringFailureClass.NONE:
                return _step_result(
                    step,
                    status=EngineeringTaskStatus.FAILED,
                    failure_class=outcome,
                    reason_code=f"VALIDATION_{outcome.value}",
                    summary=f"validation {outcome.value.lower()} failure",
                )
            return _step_result(
                step,
                reason_code="VALIDATION_PASS",
                summary="validation passed",
            )
        return _step_result(step)


def _orchestrator(role, *, rounds: int = 2, context=None):
    return EngineeringTaskOrchestrator(
        planner=EngineeringTaskPlanner(max_debug_rounds=rounds),
        role_executor=role,
        context_provider=_ContextProvider(context),
        memory_service=_memory_service(),
    )


def test_happy_path_runs_requirement_hardware_firmware_validation() -> None:
    role = _RoleExecutor()
    result = _orchestrator(role).run(_request())

    assert role.calls == [
        EngineeringTaskStage.REQUIREMENT,
        EngineeringTaskStage.HARDWARE,
        EngineeringTaskStage.FIRMWARE,
        EngineeringTaskStage.VALIDATION,
    ]
    assert result.status is EngineeringTaskStatus.COMPLETED
    assert result.step_results[-1].stage is EngineeringTaskStage.COMPLETE
    assert result.validation_summary == "validation passed"
    assert result.debug_rounds_used == 0


def test_validation_failure_runs_debug_then_validation_pass() -> None:
    role = _RoleExecutor(
        validation_outcomes=(
            EngineeringFailureClass.ENGINEERING,
            EngineeringFailureClass.NONE,
        )
    )
    result = _orchestrator(role).run(_request())

    assert role.calls == [
        EngineeringTaskStage.REQUIREMENT,
        EngineeringTaskStage.HARDWARE,
        EngineeringTaskStage.FIRMWARE,
        EngineeringTaskStage.VALIDATION,
        EngineeringTaskStage.DEBUG,
        EngineeringTaskStage.VALIDATION,
    ]
    assert result.status is EngineeringTaskStatus.COMPLETED
    assert result.debug_rounds_used == 1


def test_engineering_debug_budget_exhaustion_is_bounded() -> None:
    role = _RoleExecutor(
        validation_outcomes=(EngineeringFailureClass.ENGINEERING,) * 3
    )
    result = _orchestrator(role, rounds=2).run(_request())

    assert role.calls.count(EngineeringTaskStage.DEBUG) == 2
    assert role.calls.count(EngineeringTaskStage.VALIDATION) == 3
    assert result.status is EngineeringTaskStatus.FAILED
    assert result.debug_rounds_used == 2
    assert result.step_results[-1].reason_code == "VALIDATION_ENGINEERING"


def test_infrastructure_failure_is_not_treated_as_engineering_retry() -> None:
    role = _RoleExecutor(
        validation_outcomes=(EngineeringFailureClass.INFRASTRUCTURE,)
    )
    result = _orchestrator(role).run(_request())

    assert EngineeringTaskStage.DEBUG not in role.calls
    assert result.status is EngineeringTaskStatus.FAILED
    assert result.debug_rounds_used == 0
    assert result.step_results[-1].failure_class is EngineeringFailureClass.INFRASTRUCTURE


def test_step_failure_stops_all_dependent_steps() -> None:
    role = _RoleExecutor(fail_stage=EngineeringTaskStage.HARDWARE)
    result = _orchestrator(role).run(_request())

    assert role.calls == [
        EngineeringTaskStage.REQUIREMENT,
        EngineeringTaskStage.HARDWARE,
    ]
    assert result.status is EngineeringTaskStatus.FAILED
    assert result.validation_summary == "validation not reached"


def test_candidate_memory_is_passed_to_every_executed_role() -> None:
    role = _RoleExecutor()
    _orchestrator(role).run(_request())

    assert role.memory_seen
    assert all(len(memory) == 1 for memory in role.memory_seen)
    evidence = role.memory_seen[0][0]
    assert evidence.trust_level.value == "CANDIDATE"
    assert evidence.evidence_status.value == "CANDIDATE"
    assert evidence.verification_status.value == "REQUIRES_REVIEW"


def test_cross_project_context_is_rejected_before_role_execution() -> None:
    role = _RoleExecutor()
    orchestrator = _orchestrator(role, context=_context(project_id="project-2"))

    with pytest.raises(EngineeringTaskRejected):
        orchestrator.run(_request())
    assert role.calls == []


def test_cross_project_role_result_is_rejected() -> None:
    class CrossProjectRole(_RoleExecutor):
        def execute_step(self, step, *args):
            result = _step_result(step)
            material = result.model_dump(mode="python")
            material.pop("fingerprint")
            material["project_id"] = "project-2"
            return EngineeringStepResult.create(**material)

    with pytest.raises(EngineeringTaskRejected):
        _orchestrator(CrossProjectRole()).run(_request())
