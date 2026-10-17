from __future__ import annotations

import pytest

from embedded_copilot.application import (
    ApplicationDependencyContainer,
    ApplicationRoute,
    ApplicationRouteDecision,
    ApplicationRuntime,
    FakeApplicationAuditSink,
    FakeApplicationContext,
    FakeApplicationModelGateway,
    FakeApplicationSupervisor,
    FixedApplicationClock,
)
from embedded_copilot.debug_agent_adapter import DebugPromptAdapter
from embedded_copilot.engineering_context import (
    ContextSourceType,
    ContextVerificationStatus,
    EngineeringContextEvidence,
)
from embedded_copilot.firmware_agent_adapter import FirmwarePromptAdapter
from embedded_copilot.model_gateway import (
    BoundModelIdentity,
    EngineeringStage,
    GateStatus,
    ModelInvocationContext,
    ModelInvocationGate,
    ModelInvocationRequest,
    ModelInvocationResult,
    ModelInvocationStatus,
    ModelInvocationTraceContext,
    ModelResponse,
    ModelSelectionResult,
    ModelSelectionTaskProfile,
    SelectionMode,
)

from .fixtures import NOW, request, snapshot

IDENTITY = BoundModelIdentity(
    provider_id="fake",
    endpoint_id="endpoint-fake",
    credential_ref="credential:fake",
    model_profile_id="model-fake",
    model_id="fake-model",
)


class _Selection:
    def select_governed_model(self, task_profile, *, request_override=None):
        return ModelSelectionResult(selected_model=IDENTITY, selection_reason="STAGE_FIXED_MODEL")


class _Invocation:
    def __init__(self) -> None:
        self.calls: list[ModelInvocationRequest] = []

    def invoke(self, request: ModelInvocationRequest) -> ModelInvocationResult:
        assert "model_policy" not in type(request.payload).model_fields
        assert request.request_override is None
        self.calls.append(request)
        response = ModelResponse(
            content="governed answer",
            model_name=IDENTITY.model_id,
            provider=IDENTITY.provider_id,
            provider_id=IDENTITY.provider_id,
            endpoint_id=IDENTITY.endpoint_id,
            credential_ref=IDENTITY.credential_ref,
            request_id=request.request_id,
            provenance=request.payload.engineering_context.provenance,
        )
        return ModelInvocationResult(
            status=ModelInvocationStatus.SUCCESS,
            response=response,
            context=ModelInvocationContext(
                task_profile=ModelSelectionTaskProfile(
                    request_id=request.request_id,
                    project_id=request.project_id,
                    stage=request.stage_id,
                    task_type=request.task_type,
                ),
                policy_source="STAGE_POLICY",
                selection_mode=SelectionMode.FIXED_MODEL,
                selection_reason="STAGE_FIXED_MODEL",
                selected_model=IDENTITY,
                gates=tuple(
                    ModelInvocationGate(name=name, status=GateStatus.PASS)
                    for name in ("capability", "availability", "credential", "endpoint_health", "quota")
                ),
                trace_context=ModelInvocationTraceContext(trace_id=request.trace_context.trace_id),
            ),
        )

    async def ainvoke(self, request: ModelInvocationRequest) -> ModelInvocationResult:
        return self.invoke(request)


class _SpecialistResult:
    def __init__(self, evidence: tuple[EngineeringContextEvidence, ...]) -> None:
        self.facts = ("observed specialist source",)
        self.evidence = tuple(item.evidence_id for item in evidence)
        self.analysis = ("observed specialist path",)
        self.confidence = 1.0
        self.limitations = ()
        self.context_evidence = evidence


class _SpecialistAgent:
    def __init__(self, result: _SpecialistResult) -> None:
        self.result = result

    def analyze(self, _request, _context):
        return self.result

    async def aanalyze(self, request, context):
        return self.analyze(request, context)


def _specialist_evidence(source_type: ContextSourceType, evidence_id: str) -> EngineeringContextEvidence:
    return EngineeringContextEvidence.create(
        evidence_id=evidence_id,
        project_id="project-1",
        source_type=source_type,
        subject="specialist-source",
        summary="observed specialist source",
        fact_key="source.location",
        fact_value="source.c",
        source_reference="Core/Src/source.c",
        source_fingerprint="sha256:" + "b" * 64,
        confidence=1.0,
        verification_status=ContextVerificationStatus.VERIFIED,
        provenance=("Core/Src/source.c", "sha256:" + "b" * 64),
    )


def test_application_runtime_general_branch_uses_invocation_service():
    question = request()
    invocation = _Invocation()
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=FakeApplicationSupervisor(
                ApplicationRouteDecision.for_request(question, route=ApplicationRoute.KNOWLEDGE)
            ),
            context_provider=FakeApplicationContext(snapshot()),
            mode="GOVERNED",
            model_invocation_service=invocation,
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW),
        )
    )

    result = runtime.answer(question)

    assert result.answer is not None
    assert result.answer.content == "governed answer"
    assert len(invocation.calls) == 1
    assert invocation.calls[0].stage_id is EngineeringStage.REQUIREMENT_ANALYSIS


def test_application_runtime_firmware_branch_uses_invocation_service():
    question = request(question="I2C 初始化在哪里？")
    invocation = _Invocation()
    evidence = _specialist_evidence(ContextSourceType.FIRMWARE_SOURCE, "firmware-source-1")
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=FakeApplicationSupervisor(
                ApplicationRouteDecision.for_request(question, route=ApplicationRoute.FIRMWARE)
            ),
            context_provider=FakeApplicationContext(snapshot(task=question.question)),
            mode="GOVERNED",
            model_invocation_service=invocation,
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW),
            firmware_agent=_SpecialistAgent(_SpecialistResult((evidence,))),
            firmware_prompt=FirmwarePromptAdapter(),
        )
    )

    result = runtime.answer(question)

    assert result.answer is not None
    assert result.answer.content == "governed answer"
    assert len(invocation.calls) == 1
    assert invocation.calls[0].stage_id is EngineeringStage.FIRMWARE_DEVELOPMENT


def test_application_runtime_debug_branch_uses_invocation_service():
    question = request(question="分析 HAL_TIMEOUT 日志")
    invocation = _Invocation()
    evidence = _specialist_evidence(ContextSourceType.DEBUG, "debug-source-1")
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=FakeApplicationSupervisor(
                ApplicationRouteDecision.for_request(question, route=ApplicationRoute.DEBUG)
            ),
            context_provider=FakeApplicationContext(snapshot(task=question.question)),
            mode="GOVERNED",
            model_invocation_service=invocation,
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW),
            debug_agent=_SpecialistAgent(_SpecialistResult((evidence,))),
            debug_prompt=DebugPromptAdapter(),
        )
    )

    result = runtime.answer(question)

    assert result.answer is not None
    assert result.answer.content == "governed answer"
    assert len(invocation.calls) == 1
    assert invocation.calls[0].stage_id is EngineeringStage.DEBUGGING


def test_governed_container_requires_invocation_and_legacy_requires_policy():
    from embedded_copilot.application.exceptions import ApplicationDependencyError

    common = {
        "agent_runtime": FakeApplicationSupervisor(
            ApplicationRouteDecision.for_request(
                request(), route=ApplicationRoute.KNOWLEDGE
            )
        ),
        "context_provider": FakeApplicationContext(snapshot()),
        "audit_sink": FakeApplicationAuditSink(),
        "clock": FixedApplicationClock(NOW),
    }
    with pytest.raises(ApplicationDependencyError):
        ApplicationDependencyContainer(**common, mode="GOVERNED")
    with pytest.raises(ApplicationDependencyError):
        ApplicationDependencyContainer(**common, mode="LEGACY", model_gateway=FakeApplicationModelGateway())


def test_governed_desktop_never_constructs_policy(tmp_path, monkeypatch):
    import embedded_copilot.application.desktop_composition as desktop
    from embedded_copilot.application.desktop_composition import (
        build_project_first_chat_runtime,
    )
    from embedded_copilot.multimodal.context import (
        ProcessLocalAttachmentBindingRepository,
    )
    from tests.application.test_desktop_composition import _catalog

    def forbidden(*args, **kwargs):
        raise AssertionError("pre-selection policy construction")

    monkeypatch.setattr(desktop, "ModelPolicy", forbidden)

    common = {
        "database": tmp_path / "state" / "desktop.sqlite3",
        "project_catalog": _catalog(tmp_path),
        "approved_memory_store": None,
        "environment": None,
        "attachments": ProcessLocalAttachmentBindingRepository(),
        "governed": True,
    }
    runtime = build_project_first_chat_runtime(
        **common, model_invocation_service=_Invocation()
    )
    assert runtime is not None
    assert runtime._application._container.model_policy is None
    assert runtime._application._container.model_gateway is None
    assert build_project_first_chat_runtime(**common) is None
