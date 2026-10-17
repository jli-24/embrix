from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta

from embedded_copilot.engineering.simulation_task_role_adapter import (
    SimulationTaskRoleAdapter,
)
from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.engineering_memory.foundation_repository import (
    InMemoryEngineeringMemoryRepository,
)
from embedded_copilot.engineering_memory.foundation_service import (
    EngineeringMemoryService,
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
from embedded_copilot.execution_worker.contracts import ArtifactIdentity
from embedded_copilot.simulation_runtime.contracts import (
    SimulationAssertion,
    SimulationAssertionKind,
    SimulationAssertionResult,
    SimulationBackend,
    SimulationExecutionBinding,
    SimulationFailureClass,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
FP = "sha256:" + "7" * 64


def task_request() -> EngineeringTaskRequest:
    return EngineeringTaskRequest(
        task_id="task-1",
        tenant_id="tenant-1",
        project_id="project-1",
        objective="Validate an STM32 UART boot scenario",
        constraints=("Use bounded simulation",),
        created_at=NOW,
    )


def simulation_request() -> SimulationRequest:
    artifact = ArtifactIdentity(
        artifact_id="artifact-1",
        tenant_id="tenant-1",
        project_id="project-1",
        target_id="target-1",
        workspace_fingerprint=FP,
        build_operation_id="build-1",
        toolchain_id="CMAKE_NINJA_BUILD",
        toolchain_version="3",
        build_configuration="DEBUG",
        artifact_type="ELF",
        artifact_sha256="sha256:" + "8" * 64,
        size_bytes=256,
        created_at=NOW,
    )
    return SimulationRequest.create(
        simulation_id="simulation-1",
        tenant_id="tenant-1",
        project_id="project-1",
        backend=SimulationBackend.FAKE,
        firmware_artifact=artifact,
        platform_descriptor="stm32-platform",
        scenario="uart-boot",
        assertions=(
            SimulationAssertion.create(
                assertion_id="uart-ready",
                kind=SimulationAssertionKind.UART_CONTAINS,
                target="uart0",
                expected="READY",
            ),
        ),
        timeout_seconds=30,
        execution_binding=SimulationExecutionBinding.create(
            requester_id="engineer-1",
            target_id="target-1",
            target_fingerprint=FP,
            workspace_snapshot_id="snapshot-1",
            workspace_fingerprint=FP,
            proposal_id="proposal-1",
            proposal_fingerprint=FP,
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=10),
            nonce="nonce-1",
        ),
    )


def simulation_result(
    status: SimulationStatus,
    failure_class: SimulationFailureClass,
    reason_code: str,
) -> SimulationResult:
    assertion_results = ()
    if status in {
        SimulationStatus.PASS,
        SimulationStatus.FAIL,
        SimulationStatus.TIMEOUT,
    }:
        assertion_results = (
            SimulationAssertionResult.create(
                assertion_id="uart-ready",
                kind=SimulationAssertionKind.UART_CONTAINS,
                passed=failure_class is SimulationFailureClass.NONE,
                observed_summary="bounded UART observation",
                evidence_reference="simulation-evidence:uart-ready",
            ),
        )
    return SimulationResult.create(
        simulation_id="simulation-1",
        operation_id="operation-1",
        tenant_id="tenant-1",
        project_id="project-1",
        backend=SimulationBackend.FAKE,
        status=status,
        failure_class=failure_class,
        reason_code=reason_code,
        started_at=NOW,
        finished_at=NOW,
        assertion_results=assertion_results,
        artifact_references=("artifact:artifact-1",),
        telemetry_references=("telemetry:simulation-1:uart0",),
        diagnostic_summary="bounded fake simulation outcome",
        request_fingerprint=simulation_request().fingerprint,
    )


class Context:
    def get_context(self, _request):
        return EngineeringContextSnapshot.create(
            project_id="project-1",
            tenant_id="tenant-1",
            task_description="Validate an STM32 UART boot scenario",
            memory_evidence=(),
            graph_evidence=(),
            knowledge_evidence=(),
            multimodal_evidence=(),
            conflicts=(),
            confidence=0.0,
            provenance=(),
            verification_status="UNVERIFIED",
        )


class BaseRoles:
    def __init__(self) -> None:
        self.calls: list[EngineeringTaskStage] = []

    def execute_step(self, step, request, context, memory, prior):
        self.calls.append(step.stage)
        return EngineeringStepResult.create(
            step_id=step.step_id,
            task_id=step.task_id,
            tenant_id=step.tenant_id,
            project_id=step.project_id,
            stage=step.stage,
            status=EngineeringTaskStatus.COMPLETED,
            failure_class=EngineeringFailureClass.NONE,
            reason_code=f"{step.stage.value}_COMPLETED",
            summary=f"{step.stage.value.lower()} completed",
            evidence_references=(f"evidence:{step.step_id}",),
            trace_reference=f"trace:{step.step_id}",
        )


class RequestFactory:
    def create(self, step, request, prior_results):
        return simulation_request()


class SimulationOutcomes:
    def __init__(self, *results: SimulationResult) -> None:
        self.results = deque(results)

    def evaluate(self, request):
        return self.results.popleft()


def orchestrator(*results: SimulationResult):
    base = BaseRoles()
    roles = SimulationTaskRoleAdapter(
        delegate=base,
        request_factory=RequestFactory(),
        simulation=SimulationOutcomes(*results),
    )
    runtime = EngineeringTaskOrchestrator(
        planner=EngineeringTaskPlanner(max_debug_rounds=2),
        role_executor=roles,
        context_provider=Context(),
        memory_service=EngineeringMemoryService(InMemoryEngineeringMemoryRepository()),
    )
    return runtime, base


def test_simulation_pass_completes_existing_validation_stage() -> None:
    runtime, base = orchestrator(
        simulation_result(
            SimulationStatus.PASS,
            SimulationFailureClass.NONE,
            "SIMULATION_ASSERTIONS_PASSED",
        )
    )

    result = runtime.run(task_request())

    assert result.status is EngineeringTaskStatus.COMPLETED
    assert result.debug_rounds_used == 0
    assert base.calls.count(EngineeringTaskStage.VALIDATION) == 1


def test_engineering_failure_runs_debug_then_simulation_retry() -> None:
    runtime, base = orchestrator(
        simulation_result(
            SimulationStatus.FAIL,
            SimulationFailureClass.ENGINEERING,
            "EXPECTED_UART_NOT_OBSERVED",
        ),
        simulation_result(
            SimulationStatus.PASS,
            SimulationFailureClass.NONE,
            "SIMULATION_ASSERTIONS_PASSED",
        ),
    )

    result = runtime.run(task_request())

    assert result.status is EngineeringTaskStatus.COMPLETED
    assert result.debug_rounds_used == 1
    assert base.calls.count(EngineeringTaskStage.DEBUG) == 1
    assert base.calls.count(EngineeringTaskStage.VALIDATION) == 2


def test_engineering_timeout_consumes_debug_budget() -> None:
    runtime, base = orchestrator(
        simulation_result(
            SimulationStatus.TIMEOUT,
            SimulationFailureClass.ENGINEERING,
            "FIRMWARE_BOOT_DEADLINE_EXCEEDED",
        ),
        simulation_result(
            SimulationStatus.PASS,
            SimulationFailureClass.NONE,
            "SIMULATION_ASSERTIONS_PASSED",
        ),
    )

    result = runtime.run(task_request())

    assert result.status is EngineeringTaskStatus.COMPLETED
    assert result.debug_rounds_used == 1
    assert base.calls.count(EngineeringTaskStage.DEBUG) == 1


def test_infrastructure_timeout_does_not_consume_debug_budget() -> None:
    runtime, base = orchestrator(
        simulation_result(
            SimulationStatus.TIMEOUT,
            SimulationFailureClass.INFRASTRUCTURE,
            "PROCESS_CONTROL_TIMEOUT",
        )
    )

    result = runtime.run(task_request())

    assert result.status is EngineeringTaskStatus.FAILED
    assert result.debug_rounds_used == 0
    assert EngineeringTaskStage.DEBUG not in base.calls
    assert (
        result.step_results[-1].failure_class is EngineeringFailureClass.INFRASTRUCTURE
    )


def test_cancelled_uses_compatibility_projection_without_debug() -> None:
    runtime, base = orchestrator(
        simulation_result(
            SimulationStatus.CANCELLED,
            SimulationFailureClass.CANCELLED,
            "SIMULATION_CANCELLED",
        )
    )

    result = runtime.run(task_request())

    assert result.status is EngineeringTaskStatus.FAILED
    assert result.debug_rounds_used == 0
    assert EngineeringTaskStage.DEBUG not in base.calls
    assert result.step_results[-1].reason_code == "SIMULATION_CANCELLED"
