from __future__ import annotations

from datetime import UTC, datetime

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.engineering_memory.foundation import (
    DecisionMemoryPayload,
    FoundationEngineeringMemoryRecord,
    FoundationMemoryType,
)
from embedded_copilot.engineering_memory.foundation_service import (
    EngineeringMemoryService,
)
from embedded_copilot.engineering_memory.foundation_sqlite import (
    SQLiteEngineeringMemoryRepository,
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


class _Context:
    def get_context(self, request):
        return EngineeringContextSnapshot.create(
            project_id=request.project_id,
            tenant_id=request.tenant_id,
            task_description=request.task,
            memory_evidence=(),
            graph_evidence=(),
            knowledge_evidence=(),
            multimodal_evidence=(),
            conflicts=(),
            confidence=0.0,
            provenance=(),
            verification_status="UNVERIFIED",
        )


class _Role:
    def __init__(self) -> None:
        self.memory = []

    def execute_step(self, step, request, context, memory_context, prior_results):
        self.memory.append(memory_context)
        reason = (
            "VALIDATION_PASS"
            if step.stage is EngineeringTaskStage.VALIDATION
            else "STEP_COMPLETED"
        )
        return EngineeringStepResult.create(
            step_id=step.step_id,
            task_id=step.task_id,
            tenant_id=step.tenant_id,
            project_id=step.project_id,
            stage=step.stage,
            status=EngineeringTaskStatus.COMPLETED,
            failure_class=EngineeringFailureClass.NONE,
            reason_code=reason,
            summary="validation passed" if step.stage is EngineeringTaskStage.VALIDATION else "step completed",
            evidence_references=(f"evidence:{step.step_id}",),
            trace_reference=f"trace:{step.step_id}",
        )


def test_orchestrator_reads_candidate_memory_after_repository_restart(tmp_path) -> None:
    database = tmp_path / "engineering-memory.sqlite3"
    first_service = EngineeringMemoryService(
        SQLiteEngineeringMemoryRepository(database)
    )
    record = FoundationEngineeringMemoryRecord.create(
        memory_id="decision-1",
        tenant_id="tenant-1",
        project_id="project-1",
        memory_type=FoundationMemoryType.DECISION,
        payload=DecisionMemoryPayload(
            reason="CAN interoperability",
            context="motor controller",
            choice="Use CAN",
            timestamp=NOW,
        ),
        created_at=NOW,
        source_references=("decision:can",),
    )
    first_service.save_memory(record)

    restarted_service = EngineeringMemoryService(
        SQLiteEngineeringMemoryRepository(database)
    )
    role = _Role()
    result = EngineeringTaskOrchestrator(
        planner=EngineeringTaskPlanner(max_debug_rounds=1),
        role_executor=role,
        context_provider=_Context(),
        memory_service=restarted_service,
    ).run(
        EngineeringTaskRequest(
            task_id="task-1",
            tenant_id="tenant-1",
            project_id="project-1",
            objective="Design a CAN motor controller",
            constraints=(),
            created_at=NOW,
        )
    )

    assert result.status is EngineeringTaskStatus.COMPLETED
    assert result.memory_references == ("decision:can",)
    assert role.memory
    assert all(memory[0].summary.startswith("choice Use CAN") for memory in role.memory)
    assert all(memory[0].trust_level.value == "CANDIDATE" for memory in role.memory)
