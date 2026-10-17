from __future__ import annotations

from datetime import UTC, datetime

from embedded_copilot.agents.base import BaseAgent
from embedded_copilot.agents.types import AgentResult, AgentStatus
from embedded_copilot.engineering.task_role_adapter import (
    ExistingEngineeringRoleAdapter,
)
from embedded_copilot.engineering_context import (
    ContextSourceType,
    EngineeringContextEvidence,
    EngineeringContextSnapshot,
    EvidenceTrustLevel,
    EvidenceVerificationStatus,
)
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
from embedded_copilot.engineering_task_runtime.models import (
    EngineeringFailureClass,
    EngineeringStepResult,
    EngineeringTaskRequest,
    EngineeringTaskStage,
    EngineeringTaskStatus,
)
from embedded_copilot.engineering_task_runtime.planner import EngineeringTaskPlanner
from embedded_copilot.engineering_validation import (
    ValidationFinding,
    ValidationFindingStatus,
    ValidationRequest,
    ValidationService,
    ValidationSeverity,
)
from embedded_copilot.engineering_validation.adapters import FakeValidationStoreAdapter

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


class _RecordingAgent(BaseAgent):
    description = "test double for an existing specialist boundary"
    capabilities = ("test",)

    def __init__(self, name: str, result: AgentResult | None = None) -> None:
        self.name = name
        self.result = result
        self.tasks = []

    def run(self, task):
        self.tasks.append(task)
        return self.result or AgentResult(
            agent_name=self.name,
            status=AgentStatus.SUCCESS,
            output=f"{self.name} completed",
            metadata={"artifact_reference": f"artifact:{self.name.lower()}"},
        )


class _Rule:
    def __init__(self, findings=()) -> None:
        self.findings = findings
        self.requests: list[ValidationRequest] = []

    def validate(self, request: ValidationRequest):
        self.requests.append(request)
        return self.findings


def _request() -> EngineeringTaskRequest:
    return EngineeringTaskRequest(
        task_id="task-1",
        tenant_id="tenant-1",
        project_id="project-1",
        objective="Design an STM32 motor controller",
        constraints=("Use CAN",),
        created_at=NOW,
    )


def _context() -> EngineeringContextSnapshot:
    evidence = EngineeringContextEvidence.create(
        evidence_id="source-evidence-1",
        tenant_id="tenant-1",
        project_id="project-1",
        source_type=ContextSourceType.KNOWLEDGE_GATEWAY,
        subject="STM32 reference",
        summary="Verified STM32 design reference",
        source_reference="knowledge:stm32-reference",
        source_fingerprint="sha256:" + "a" * 64,
        confidence=0.9,
        verification_status="VERIFIED",
        trust_level=EvidenceTrustLevel.VERIFIED_FACT,
        evidence_status=EvidenceVerificationStatus.VERIFIED,
    )
    return EngineeringContextSnapshot.create(
        project_id="project-1",
        tenant_id="tenant-1",
        task_description="Design an STM32 motor controller",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(evidence,),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.9,
        provenance=(evidence.source(),),
        verification_status="VERIFIED",
    )


def _memory():
    service = EngineeringMemoryService(InMemoryEngineeringMemoryRepository())
    service.save_memory(
        FoundationEngineeringMemoryRecord.create(
            memory_id="memory-1",
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
    from embedded_copilot.engineering_context import EngineeringContextRequest

    return service.build_context(
        EngineeringContextRequest(
            tenant_id="tenant-1",
            project_id="project-1",
            task="STM32 motor controller",
        )
    )


def _prior(step, *, summary: str = "prior completed") -> EngineeringStepResult:
    return EngineeringStepResult.create(
        step_id=step.step_id,
        task_id=step.task_id,
        tenant_id=step.tenant_id,
        project_id=step.project_id,
        stage=step.stage,
        status=EngineeringTaskStatus.COMPLETED,
        failure_class=EngineeringFailureClass.NONE,
        reason_code="STEP_COMPLETED",
        summary=summary,
        evidence_references=(f"evidence:{step.step_id}",),
        trace_reference=f"trace:{step.step_id}",
    )


def _adapter(rule: _Rule):
    hardware = _RecordingAgent("HardwareAgent")
    firmware = _RecordingAgent("FirmwareAgent")
    debug = _RecordingAgent("DebugAgent")
    adapter = ExistingEngineeringRoleAdapter(
        hardware_agent=hardware,
        firmware_agent=firmware,
        debug_agent=debug,
        validation_service=ValidationService(
            rule=rule,
            store=FakeValidationStoreAdapter(),
        ),
    )
    return adapter, hardware, firmware, debug


def test_requirement_is_projected_without_manufacturing_confirmation() -> None:
    adapter, hardware, firmware, debug = _adapter(_Rule())
    step = EngineeringTaskPlanner(max_debug_rounds=0).plan(_request()).steps[0]
    result = adapter.execute_step(step, _request(), _context(), _memory(), ())

    assert result.status is EngineeringTaskStatus.COMPLETED
    assert result.reason_code == "REQUIREMENT_PROPOSED"
    assert "CONFIRMED" not in result.summary.upper()
    assert not hardware.tasks and not firmware.tasks and not debug.tasks


def test_existing_specialists_are_reused_and_receive_candidate_memory_metadata() -> None:
    adapter, hardware, firmware, debug = _adapter(_Rule())
    plan = EngineeringTaskPlanner(max_debug_rounds=1).plan(_request())
    memory = _memory()

    for stage, agent in (
        (EngineeringTaskStage.HARDWARE, hardware),
        (EngineeringTaskStage.FIRMWARE, firmware),
        (EngineeringTaskStage.DEBUG, debug),
    ):
        step = next(item for item in plan.steps if item.stage is stage)
        result = adapter.execute_step(step, _request(), _context(), memory, ())
        assert result.status is EngineeringTaskStatus.COMPLETED
        assert len(agent.tasks) == 1
        assert agent.tasks[0].metadata["engineering_memory_context"][0][
            "trust_level"
        ] == "CANDIDATE"


def test_validation_service_adapter_passes_with_authoritative_context() -> None:
    rule = _Rule()
    adapter, *_ = _adapter(rule)
    plan = EngineeringTaskPlanner(max_debug_rounds=0).plan(_request())
    firmware_step = next(
        item for item in plan.steps if item.stage is EngineeringTaskStage.FIRMWARE
    )
    validation_step = next(
        item for item in plan.steps if item.stage is EngineeringTaskStage.VALIDATION
    )

    result = adapter.execute_step(
        validation_step,
        _request(),
        _context(),
        _memory(),
        (_prior(firmware_step),),
    )

    assert result.status is EngineeringTaskStatus.COMPLETED
    assert result.reason_code == "VALIDATION_PASS"
    assert len(rule.requests) == 1
    assert rule.requests[0].knowledge_refs == (
        "knowledge://context/source-evidence-1",
    )


def test_candidate_memory_is_not_used_as_validation_authority() -> None:
    adapter, *_ = _adapter(_Rule())
    plan = EngineeringTaskPlanner(max_debug_rounds=0).plan(_request())
    validation_step = next(
        item for item in plan.steps if item.stage is EngineeringTaskStage.VALIDATION
    )
    result = adapter.execute_step(
        validation_step,
        _request(),
        None,
        _memory(),
        (),
    )

    assert result.status is EngineeringTaskStatus.FAILED
    assert result.failure_class is EngineeringFailureClass.INFRASTRUCTURE
    assert result.reason_code == "VALIDATION_AUTHORITY_UNAVAILABLE"


def test_error_and_critical_findings_are_engineering_failures() -> None:
    finding = ValidationFinding.create(
        finding_id="finding-1",
        project_id="project-1",
        rule_id="RULE-1",
        category="engineering_output",
        severity=ValidationSeverity.ERROR,
        status=ValidationFindingStatus.OPEN,
        message="engineering output requires correction",
        evidence_refs=("knowledge://context/source-evidence-1",),
    )
    adapter, *_ = _adapter(_Rule((finding,)))
    plan = EngineeringTaskPlanner(max_debug_rounds=0).plan(_request())
    validation_step = next(
        item for item in plan.steps if item.stage is EngineeringTaskStage.VALIDATION
    )
    result = adapter.execute_step(
        validation_step, _request(), _context(), _memory(), ()
    )

    assert result.status is EngineeringTaskStatus.FAILED
    assert result.failure_class is EngineeringFailureClass.ENGINEERING
    assert result.reason_code == "VALIDATION_FINDINGS"


def test_specialist_safe_failure_preserves_stage_and_error_class() -> None:
    failure = AgentResult(
        agent_name="FirmwareAgent",
        status=AgentStatus.ERROR,
        output="firmware planning failed",
        metadata={
            "stage": "planning",
            "error_type": "FirmwarePlanningError",
        },
    )
    hardware = _RecordingAgent("HardwareAgent")
    firmware = _RecordingAgent("FirmwareAgent", failure)
    debug = _RecordingAgent("DebugAgent")
    adapter = ExistingEngineeringRoleAdapter(
        hardware_agent=hardware,
        firmware_agent=firmware,
        debug_agent=debug,
        validation_service=ValidationService(
            rule=_Rule(), store=FakeValidationStoreAdapter()
        ),
    )
    step = next(
        item
        for item in EngineeringTaskPlanner(max_debug_rounds=0).plan(_request()).steps
        if item.stage is EngineeringTaskStage.FIRMWARE
    )
    result = adapter.execute_step(step, _request(), _context(), _memory(), ())

    assert result.status is EngineeringTaskStatus.FAILED
    assert result.failure_class is EngineeringFailureClass.ENGINEERING
    assert result.reason_code == "FIRMWARE_PLANNING_FIRMWAREPLANNINGERROR"
