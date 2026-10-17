from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from embedded_copilot.application import (
    ApplicationAuditEventType,
    ApplicationAnswerRejected,
    ApplicationAuditUnavailable,
    ApplicationDependencyContainer,
    ApplicationErrorCode,
    ApplicationRoute,
    ApplicationRouteDecision,
    ApplicationRoutingContext,
    ApplicationRunStatus,
    ApplicationRuntime,
    CanonicalSupervisorBinding,
    EngineeringQABinding,
    FakeApplicationAuditSink,
    FakeApplicationContext,
    FakeApplicationModelGateway,
    FakeApplicationSupervisor,
    FixedApplicationClock,
    ModelPolicy,
)
from embedded_copilot.debug_agent_adapter import (
    DebugAgentApplicationAdapter,
    DebugPromptAdapter,
)
from embedded_copilot.engineering_context import (
    ContextSourceType,
    ContextVerificationStatus,
    EngineeringContextEvidence,
    EngineeringContextExternalEvidenceUnavailable,
    EngineeringContextUnavailable,
)
from embedded_copilot.engineering_qa import (
    EngineeringAnswer,
    EngineeringQAService,
    EngineeringQuestion,
    ModelInfo,
)
from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.engineering_requirements import (
    RequirementContext,
    RequirementLifecycle,
    merge_requirement_context,
)
from embedded_copilot.firmware_agent_adapter import (
    FirmwareAgentApplicationAdapter,
    FirmwarePromptAdapter,
)
from embedded_copilot.model_gateway import (
    DeepSeekProviderAdapter,
    ModelGatewayService,
    ModelGatewayProviderFailure,
    ModelOpenQuestion,
    ModelRequirementProposal,
)

from .fixtures import NOW, request, snapshot


def _runtime(
    *,
    route: ApplicationRoute = ApplicationRoute.KNOWLEDGE,
    context=None,
    gateway=None,
    audit=None,
    supervisor=None,
) -> tuple[ApplicationRuntime, object, object, FakeApplicationAuditSink]:
    question = request()
    context_port = (
        FakeApplicationContext(snapshot()) if context is None else context
    )
    model_port = FakeApplicationModelGateway() if gateway is None else gateway
    audit_port = FakeApplicationAuditSink() if audit is None else audit
    agent = supervisor or FakeApplicationSupervisor(
        ApplicationRouteDecision.for_request(question, route=route)
    )
    container = ApplicationDependencyContainer(
        agent_runtime=agent,
        context_provider=context_port,
        model_gateway=model_port,
        audit_sink=audit_port,
        clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
        model_policy=ModelPolicy(
            primary_provider="fake", model_name="fake-model"
        ),
    )
    return ApplicationRuntime(container), context_port, model_port, audit_port


def test_complete_question_flow_binds_route_context_answer_and_audit() -> None:
    runtime, context, gateway, audit = _runtime()
    result = runtime.answer(request())
    assert result.status is ApplicationRunStatus.COMPLETED
    assert result.route is ApplicationRoute.KNOWLEDGE
    assert result.answer is not None
    assert result.context_fingerprint == snapshot().fingerprint
    assert result.answer.evidence_refs == ("memory-1",)
    assert result.audit.event_type is ApplicationAuditEventType.QUESTION_ANSWERED
    assert context.call_count == 1
    assert gateway.call_count == 1
    stored = audit.get(result.audit.event_id)
    assert stored is not None
    assert stored.answer_fingerprint == result.answer.fingerprint
    assert stored.question_fingerprint == request().fingerprint


def test_route_classification_uses_explicit_clarification_and_project_mode() -> None:
    ambiguous = request(question="帮我看看这个")
    binding = CanonicalSupervisorBinding(
        project_mode=lambda _project_id, _tenant_id: SimpleNamespace(
            creation_mode=SimpleNamespace(value="CREATE_NEW")
        )
    )
    assert binding.route(ambiguous).route is ApplicationRoute.CLARIFICATION_REQUIRED

    greenfield = request(question="请从零设计 STM32 启动架构")
    assert binding.route(greenfield).route is ApplicationRoute.GREENFIELD_DESIGN

    brownfield_binding = CanonicalSupervisorBinding(
        project_mode=lambda _project_id, _tenant_id: SimpleNamespace(
            creation_mode=SimpleNamespace(value="ATTACH_EXISTING")
        )
    )
    brownfield = request(question="分析当前已有工程里的 sensor_read 调用关系")
    assert brownfield_binding.route(brownfield).route is ApplicationRoute.BROWNFIELD_ANALYSIS

    document = request(question="生成需求分析报告")
    assert binding.route(document).route is ApplicationRoute.GREENFIELD_DESIGN


def test_build_language_uses_firmware_route_without_starting_execution() -> None:
    project = SimpleNamespace(creation_mode=SimpleNamespace(value="CREATE_NEW"))
    binding = CanonicalSupervisorBinding(
        project_mode=lambda _project_id, _tenant_id: project
    )

    for question in ("编译固件", "构建项目", "build 一下", "检查固件能不能编译"):
        decision = binding.route(request(question=question))
        assert decision.route is ApplicationRoute.FIRMWARE


def test_partial_requirement_context_keeps_design_request_in_discovery() -> None:
    project = SimpleNamespace(creation_mode=SimpleNamespace(value="CREATE_NEW"))
    binding = CanonicalSupervisorBinding(project_mode=lambda _project_id, _tenant_id: project)
    partial = merge_requirement_context(
        None,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        utterance="我要做一个 STM32 智能小车",
    )
    decision = binding.route(
        request(question="开始设计"),
        routing_context=ApplicationRoutingContext(
            project_creation_mode="CREATE_NEW",
            conversation_id="conversation-1",
            requirement_context=partial,
        ),
    )
    assert decision.route is ApplicationRoute.GREENFIELD_DISCOVERY

    complete = merge_requirement_context(
        None,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-2",
        utterance="我要做一个 STM32 智能小车，必须支持巡线，电池供电，直流电机",
    )
    confirmed_decision = binding.route(
        request(question="开始设计"),
        routing_context=ApplicationRoutingContext(
            project_creation_mode="CREATE_NEW",
            conversation_id="conversation-1",
            requirement_context=complete,
        ),
    )
    # A complete-looking context is not enough to enter design; the current
    # plan must also have an explicit user approval.
    assert confirmed_decision.route is ApplicationRoute.GREENFIELD_DISCOVERY


def test_create_new_platform_keyword_starts_greenfield_discovery_without_source() -> None:
    project = SimpleNamespace(creation_mode=SimpleNamespace(value="CREATE_NEW"))
    binding = CanonicalSupervisorBinding(project_mode=lambda _project_id, _tenant_id: project)
    assert binding.route(request(question="stm32")).route is ApplicationRoute.GREENFIELD_DISCOVERY
    assert binding.route(request(question="STM32F4 和 H7 哪个更适合我这个项目？")).route is ApplicationRoute.KNOWLEDGE


def test_greenfield_answer_completes_chat_request_but_keeps_partial_requirements() -> None:
    project = SimpleNamespace(creation_mode=SimpleNamespace(value="CREATE_NEW"))
    question = request(question="stm32", tenant_id="desktop-tenant")
    gateway = FakeApplicationModelGateway(content="开始收集温控器需求")
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=CanonicalSupervisorBinding(project_mode=lambda _project_id, _tenant_id: project),
            context_provider=FakeApplicationContext(None),
            model_gateway=gateway,
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
            model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
        )
    )
    result = runtime.answer(question)
    assert result.status is ApplicationRunStatus.COMPLETED
    assert result.route is ApplicationRoute.GREENFIELD_DISCOVERY
    assert result.requirement_context is not None
    assert result.requirement_context.lifecycle.value == "REQUIREMENTS_PARTIAL"
    assert any(item.category == "TARGET_PLATFORM" and item.status.value == "CONFIRMED" for item in result.requirement_context.requirements)
    assert gateway.call_count == 1


def test_greenfield_response_is_conversational_and_hides_legacy_report_labels() -> None:
    project = SimpleNamespace(creation_mode=SimpleNamespace(value="CREATE_NEW"))
    question = request(question="我要做一个 STM32 智能小车", tenant_id="desktop-tenant")
    gateway = FakeApplicationModelGateway(
        content=(
            "# 项目需求分析\n"
            "已确认需求：STM32。\n"
            "UNKNOWN\n"
            "Requirement ID: req-1\n"
            "接下来请确认传感器和通信方式。"
        )
    )
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=CanonicalSupervisorBinding(
                project_mode=lambda _project_id, _tenant_id: project
            ),
            context_provider=FakeApplicationContext(None),
            model_gateway=gateway,
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
            model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
        )
    )

    result = runtime.answer(question)

    assert result.status is ApplicationRunStatus.COMPLETED
    assert result.answer is not None
    assert "接下来请确认传感器和通信方式" in result.answer.content
    assert "项目需求分析" not in result.answer.content
    assert "已确认需求" not in result.answer.content
    assert "UNKNOWN" not in result.answer.content
    assert "Requirement ID" not in result.answer.content


def test_ready_requirement_context_asks_for_report_confirmation_before_document() -> None:
    project = SimpleNamespace(creation_mode=SimpleNamespace(value="CREATE_NEW"))
    question = request(question="继续", tenant_id="desktop-tenant")
    previous = RequirementContext(
        project_id=question.project_id,
        conversation_id="conversation-1",
        lifecycle=RequirementLifecycle.REQUIREMENTS_READY,
    )
    gateway = FakeApplicationModelGateway(
        content="# 项目需求分析\n已确认需求：STM32。"
    )
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=CanonicalSupervisorBinding(
                project_mode=lambda _project_id, _tenant_id: project
            ),
            context_provider=FakeApplicationContext(None),
            model_gateway=gateway,
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
            model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
        )
    )

    result = runtime.answer(
        question,
        routing_context=ApplicationRoutingContext(
            project_creation_mode="CREATE_NEW",
            project_type="STM32",
            conversation_id="conversation-1",
            requirement_context=previous,
        ),
    )

    assert result.status is ApplicationRunStatus.COMPLETED
    assert result.answer is not None
    assert result.answer.content == "当前项目需求已经整理完成，是否生成需求分析报告？"
    assert result.requirement_context is not None
    assert result.requirement_context.lifecycle is RequirementLifecycle.REQUIREMENTS_READY


def test_greenfield_runtime_projects_context_to_two_message_provider_payload(
    caplog,
) -> None:
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                "当前需求理解：目标平台为 STM32。已确认需求：STM32。"
                                "尚未确认信息：产品目标和硬件约束。初步工程设计方向："
                                "采用分层固件架构。模块划分：驱动、服务与应用。"
                                "后续开发计划：先补充需求，再评审架构和任务调度。"
                            )
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    provider = DeepSeekProviderAdapter(
        api_key="offline-test-key",
        http_transport=httpx.MockTransport(handler),
    )
    gateway = ModelGatewayService(providers={"deepseek": provider})
    project = SimpleNamespace(creation_mode=SimpleNamespace(value="CREATE_NEW"))
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=CanonicalSupervisorBinding(
                project_mode=lambda _project_id, _tenant_id: project
            ),
            context_provider=FakeApplicationContext(None),
            model_gateway=gateway,
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
            model_policy=ModelPolicy(
                primary_provider="deepseek", model_name="deepseek-chat"
            ),
        )
    )
    checked = request(question="STM32", tenant_id="desktop-tenant")
    routing_context = ApplicationRoutingContext(
        project_creation_mode="CREATE_NEW",
        project_type="STM32",
        conversation_id="conversation-1",
    )

    with caplog.at_level(
        logging.INFO,
        logger="embedded_copilot.model_gateway.adapters.openai_compatible",
    ):
        result = runtime.answer(checked, routing_context=routing_context)

    assert result.status is ApplicationRunStatus.COMPLETED
    assert len(payloads) == 1
    messages = payloads[0]["messages"]
    assert isinstance(messages, list)
    assert [message["role"] for message in messages] == ["system", "user"]
    user_message = messages[1]["content"]
    assert isinstance(user_message, str)
    assert "CREATE_NEW" in user_message
    assert "STM32" in user_message
    assert "RequirementContext" in user_message
    assert "Task:\nSTM32" in user_message
    assert "RequirementContext" not in caplog.text


def test_greenfield_multiline_question_reaches_offline_fake_provider() -> None:
    fixed_question = (
        "制作一个用于电竞的智能小车，生成：\n"
        "1.工程需求理解\n"
        "2.软件架构设计\n"
        "3.模块划分\n"
        "4.任务调度方案\n"
        "5.后续开发建议"
    )
    project = SimpleNamespace(creation_mode=SimpleNamespace(value="CREATE_NEW"))
    gateway = FakeApplicationModelGateway(content="开始收集智能小车需求")
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=CanonicalSupervisorBinding(
                project_mode=lambda _project_id, _tenant_id: project
            ),
            context_provider=FakeApplicationContext(None),
            model_gateway=gateway,
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
            model_policy=ModelPolicy(
                primary_provider="fake", model_name="fake-model"
            ),
        )
    )

    result = runtime.answer(
        request(question=fixed_question, tenant_id="desktop-tenant"),
        routing_context=ApplicationRoutingContext(
            project_creation_mode="CREATE_NEW",
            project_type="STM32",
            conversation_id="conversation-1",
        ),
    )

    assert result.status is ApplicationRunStatus.COMPLETED
    assert gateway.call_count == 1


def test_greenfield_application_evidence_emits_bounded_stage_telemetry(caplog) -> None:
    project = SimpleNamespace(creation_mode=SimpleNamespace(value="CREATE_NEW"))
    question = request(question="stm32", tenant_id="desktop-tenant")
    gateway = FakeApplicationModelGateway(content="开始收集 STM32 需求")
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=CanonicalSupervisorBinding(project_mode=lambda _project_id, _tenant_id: project),
            context_provider=FakeApplicationContext(None),
            model_gateway=gateway,
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
            model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
        )
    )

    with caplog.at_level("INFO", logger="embedded_copilot.application.bindings"):
        result = runtime.answer(question)

    assert result.status is ApplicationRunStatus.COMPLETED
    records = [
        record
        for record in caplog.records
        if record.getMessage() == "application_evidence_boundary_telemetry"
    ]
    assert [record.evidence_stage for record in records] == [
        "E0_EVIDENCE_BUILD_ENTER",
        "E1_REQUIREMENT_CONTEXT_BIND",
        "E2_PROJECT_CONTEXT_BIND",
        "E3_EVIDENCE_COLLECTION",
        "E4_EVIDENCE_VALIDATE",
        "E5_APPLICATION_ANSWER_COMMIT",
    ]
    assert all(record.telemetry_status == "PASS" for record in records)
    assert all(record.requirement_context_present is True for record in records)
    assert all(record.project_context_present is True for record in records)
    assert all(record.project_id_present is True for record in records)
    assert all(record.source_reference_count == 0 for record in records)
    assert all(record.evidence_count == 0 for record in records)
    assert all(record.answer_text_length == len("开始收集 STM32 需求") for record in records)
    assert all(record.validation_error_code == "-" for record in records)


def test_create_new_application_runtime_boundary_telemetry_is_redacted(caplog) -> None:
    project = SimpleNamespace(creation_mode=SimpleNamespace(value="CREATE_NEW"))
    question = request(question="STM32", tenant_id="desktop-tenant")
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=CanonicalSupervisorBinding(
                project_mode=lambda _project_id, _tenant_id: project
            ),
            context_provider=FakeApplicationContext(None),
            model_gateway=FakeApplicationModelGateway(content="工程分析"),
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
            model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
        )
    )
    routing_context = ApplicationRoutingContext(
        project_creation_mode="CREATE_NEW",
        project_type="STM32",
        conversation_id="conversation-1",
    )

    with caplog.at_level("INFO", logger="embedded_copilot.runtime_diagnostics"):
        result = runtime.answer(question, routing_context=routing_context)

    assert result.status is ApplicationRunStatus.COMPLETED
    records = [
        record
        for record in caplog.records
        if record.getMessage() == "application_runtime_boundary_telemetry"
    ]
    stages = [record.application_runtime_stage for record in records]
    assert stages == [
        "A0_REQUEST_ENTER",
        "A1_CONVERSATION_LOAD",
        "A2_PROJECT_CONTEXT_BIND",
        "A3_REQUIREMENT_CONTEXT_BIND",
        "A4_TASK_MODE_RESOLVE",
        "A5_ANSWER_BINDING_CREATE",
        "A6_PROMPT_REQUEST_BUILD",
        "A7_PROMPT_BUILDER_ENTER",
    ]
    assert all(record.telemetry_status == "PASS" for record in records)
    assert all(record.exception_type == "-" for record in records)
    assert all(record.reason_code == "-" for record in records)
    assert all("question" not in record.__dict__ for record in records)
    assert all("project" not in record.__dict__ for record in records)


def test_engineering_qa_binding_reports_resolved_service_dependencies(caplog) -> None:
    project = SimpleNamespace(creation_mode=SimpleNamespace(value="CREATE_NEW"))
    question = request(question="STM32", tenant_id="desktop-tenant")
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=CanonicalSupervisorBinding(
                project_mode=lambda _project_id, _tenant_id: project
            ),
            context_provider=FakeApplicationContext(None),
            model_gateway=FakeApplicationModelGateway(content="工程分析"),
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
            model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
        )
    )

    with caplog.at_level("INFO", logger="embedded_copilot.runtime_diagnostics"):
        result = runtime.answer(
            question,
            routing_context=ApplicationRoutingContext(
                project_creation_mode="CREATE_NEW",
                project_type="STM32",
                conversation_id="conversation-1",
            ),
        )

    assert result.status is ApplicationRunStatus.COMPLETED
    record = next(
        item
        for item in caplog.records
        if item.getMessage() == "binding_engineering_qa_boundary_telemetry"
        and item.binding_engineering_qa_stage
        == "B2_ENGINEERING_QA_SERVICE_RESOLVE_END"
        and item.telemetry_status == "PASS"
    )
    assert record.dependency_container_resolved is True


class _GreenfieldContextUnavailable:
    def __init__(self) -> None:
        self.call_count = 0

    def get_context(self, _request):
        self.call_count += 1
        raise EngineeringContextUnavailable()


def test_greenfield_context_unavailable_becomes_partial_context_before_provider() -> None:
    project = SimpleNamespace(creation_mode=SimpleNamespace(value="CREATE_NEW"))
    question = request(question="stm32", tenant_id="desktop-tenant")
    context = _GreenfieldContextUnavailable()
    gateway = FakeApplicationModelGateway(content="开始收集 STM32 需求")
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=CanonicalSupervisorBinding(
                project_mode=lambda _project_id, _tenant_id: project
            ),
            context_provider=context,
            model_gateway=gateway,
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
            model_policy=ModelPolicy(
                primary_provider="fake", model_name="fake-model"
            ),
        )
    )

    result = runtime.answer(question)

    assert result.status is ApplicationRunStatus.COMPLETED
    assert result.route is ApplicationRoute.GREENFIELD_DISCOVERY
    assert result.context is not None
    assert result.requirement_context is not None
    assert result.requirement_context.lifecycle.value == "REQUIREMENTS_PARTIAL"
    assert any(
        item.category == "TARGET_PLATFORM"
        and item.status.value == "CONFIRMED"
        for item in result.requirement_context.requirements
    )
    assert any(
        item.category == "PRODUCT_GOAL"
        and item.status.value == "UNKNOWN"
        for item in result.requirement_context.standard_fields
    )
    assert result.gateway_invocation_count == 1
    assert gateway.call_count == 1


def test_greenfield_provider_metadata_stays_proposed_and_open() -> None:
    project = SimpleNamespace(creation_mode=SimpleNamespace(value="CREATE_NEW"))
    question = request(question="stm32", tenant_id="desktop-tenant")
    gateway = FakeApplicationModelGateway(
        content="建议评估 FreeRTOS，并补充产品目标。",
        proposals=(
            ModelRequirementProposal(category="RTOS", value="FreeRTOS"),
        ),
        questions=(
            ModelOpenQuestion(
                category="PRODUCT_GOAL",
                question="这个项目要实现什么产品目标？",
            ),
        ),
    )
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=CanonicalSupervisorBinding(
                project_mode=lambda _project_id, _tenant_id: project
            ),
            context_provider=FakeApplicationContext(None),
            model_gateway=gateway,
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
            model_policy=ModelPolicy(
                primary_provider="fake", model_name="fake-model"
            ),
        )
    )

    result = runtime.answer(question)

    assert result.status is ApplicationRunStatus.COMPLETED
    assert result.requirement_context is not None
    assert any(
        item.category == "RTOS"
        and item.normalized_value == "FreeRTOS"
        and item.status.value == "PROPOSED"
        for item in result.requirement_context.proposed_items
    )
    assert not any(
        item.category == "RTOS" and item.status.value == "CONFIRMED"
        for item in result.requirement_context.requirements
    )
    assert any(
        item.category == "PRODUCT_GOAL"
        for item in result.requirement_context.open_questions
    )
    assert result.gateway_invocation_count == 1
    assert result.provider_response_present is True
    assert result.answer_validation_stage == "COMPLETE"
    assert result.proposed_requirements_count == 1
    assert result.open_questions_count == 1


def test_greenfield_unclassified_qa_validation_has_distinct_safe_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = SimpleNamespace(creation_mode=SimpleNamespace(value="CREATE_NEW"))
    question = request(question="stm32", tenant_id="desktop-tenant")

    def fail_validation(_service: object, _question: object) -> object:
        raise ValueError("diagnostic detail must not cross the application boundary")

    monkeypatch.setattr(EngineeringQAService, "answer", fail_validation)
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=CanonicalSupervisorBinding(
                project_mode=lambda _project_id, _tenant_id: project
            ),
            context_provider=FakeApplicationContext(None),
            model_gateway=FakeApplicationModelGateway(),
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
            model_policy=ModelPolicy(
                primary_provider="fake", model_name="fake-model"
            ),
        )
    )

    result = runtime.answer(question)

    assert result.status is ApplicationRunStatus.FAILED
    assert result.error_code is ApplicationErrorCode.ANSWER_CONTRACT_MISMATCH
    assert result.answer_validation_stage == "ENGINEERING_QA_VALIDATION"
    assert result.answer_text_present is False


def test_application_runtime_preserves_original_exception_metadata(monkeypatch, caplog) -> None:
    question = request()

    def fail_binding(*_args, **_kwargs):
        raise RuntimeError("private diagnostic detail must not cross the boundary")

    monkeypatch.setattr(EngineeringQABinding, "answer", fail_binding)
    runtime, _context, _gateway, _audit = _runtime()

    with caplog.at_level("INFO", logger="embedded_copilot.runtime_diagnostics"):
        result = runtime.answer(question)

    assert result.status is ApplicationRunStatus.FAILED
    assert result.error_code is ApplicationErrorCode.APPLICATION_UNAVAILABLE
    assert result.original_exception_type == "RuntimeError"
    assert result.original_exception_hash == hashlib.sha256(
        b"builtins.RuntimeError"
    ).hexdigest()
    assert result.exception_layer == "APPLICATIONRUNTIME"
    assert result.exception_stage == "X3_ENGINEERING_QA_EXCEPTION_CAUGHT"
    assert result.traceback_fingerprint is not None
    assert len(result.traceback_fingerprint) == 64
    assert result.gateway_invocation_count == 0
    assert result.provider_response_present is False

    record = next(
        item
        for item in caplog.records
        if item.getMessage() == "application_runtime_outer_boundary_telemetry"
        and item.stage == "X4_OUTER_EXCEPTION_MAPPING"
    )
    assert record.original_exception_type == "RuntimeError"
    assert record.original_exception_hash == result.original_exception_hash
    assert record.exception_layer == result.exception_layer
    assert record.exception_stage == result.exception_stage
    assert record.traceback_fingerprint == result.traceback_fingerprint
    assert record.reason_code == "APPLICATION_UNAVAILABLE"
    assert "private diagnostic detail" not in json.dumps(record.__dict__)


def test_greenfield_answer_revalidation_has_distinct_safe_stage(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
) -> None:
    project = SimpleNamespace(creation_mode=SimpleNamespace(value="CREATE_NEW"))
    question = request(question="stm32", tenant_id="desktop-tenant")

    def malformed_answer(_service: object, _question: object) -> object:
        return SimpleNamespace(
            content="valid text that must remain redacted",
            requirement_proposals=(),
            open_questions=(),
        )

    monkeypatch.setattr(EngineeringQAService, "answer", malformed_answer)
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=CanonicalSupervisorBinding(
                project_mode=lambda _project_id, _tenant_id: project
            ),
            context_provider=FakeApplicationContext(None),
            model_gateway=FakeApplicationModelGateway(),
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
            model_policy=ModelPolicy(
                primary_provider="fake", model_name="fake-model"
            ),
        )
    )

    with caplog.at_level("INFO", logger="embedded_copilot.application.bindings"):
        result = runtime.answer(question)

    assert result.status is ApplicationRunStatus.FAILED
    assert result.error_code is ApplicationErrorCode.ANSWER_CONTRACT_MISMATCH
    assert result.answer_validation_stage == "APPLICATION_ANSWER_REVALIDATION"
    assert result.answer_text_present is True
    records = [
        record
        for record in caplog.records
        if record.getMessage() == "application_evidence_boundary_telemetry"
    ]
    assert [record.evidence_stage for record in records] == [
        "E0_EVIDENCE_BUILD_ENTER",
        "E4_EVIDENCE_VALIDATE",
        "E1_REQUIREMENT_CONTEXT_BIND",
        "E2_PROJECT_CONTEXT_BIND",
        "E3_EVIDENCE_COLLECTION",
        "E5_APPLICATION_ANSWER_COMMIT",
    ]
    assert records[1].telemetry_status == "FAIL"
    assert records[1].validation_error_code == "ANSWER_CONTRACT_MISMATCH"
    assert all(record.project_id_present is True for record in records)
    assert all(record.source_reference_count == 0 for record in records)
    assert all(record.answer_text_length == len("valid text that must remain redacted") for record in records)


def test_clarification_runtime_stops_before_context_and_model_gateway() -> None:
    ambiguous = request(question="帮我分析一下")
    context = FakeApplicationContext(None)
    gateway = FakeApplicationModelGateway()
    container = ApplicationDependencyContainer(
        agent_runtime=CanonicalSupervisorBinding(),
        context_provider=context,
        model_gateway=gateway,
        audit_sink=FakeApplicationAuditSink(),
        clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
        model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
    )

    result = ApplicationRuntime(container).answer(ambiguous)

    assert result.route is ApplicationRoute.CLARIFICATION_REQUIRED
    assert result.status is ApplicationRunStatus.REJECTED
    assert result.error_code is ApplicationErrorCode.CLARIFICATION_REQUIRED
    assert context.call_count == 0
    assert gateway.call_count == 0


class _ProviderNotConfiguredGateway:
    is_configured = False

    def generate(self, _request):
        raise AssertionError("unconfigured provider must not be invoked")

    async def agenerate(self, _request):
        raise AssertionError("unconfigured provider must not be invoked")


def test_provider_unavailable_retains_context_and_does_not_invoke_gateway() -> None:
    question = request(question="What requirement constrains the sensor interface?")
    runtime, context, gateway, _ = _runtime(
        context=FakeApplicationContext(snapshot(task=question.question)),
        gateway=_ProviderNotConfiguredGateway(),
        supervisor=FakeApplicationSupervisor(
            ApplicationRouteDecision.for_request(question, route=ApplicationRoute.GREENFIELD)
        ),
    )

    result = runtime.answer(question)

    assert result.status is ApplicationRunStatus.FAILED
    assert result.route is ApplicationRoute.GREENFIELD
    assert result.error_code is ApplicationErrorCode.PROVIDER_NOT_CONFIGURED
    assert result.context is not None
    assert result.context_fingerprint == result.context.fingerprint
    assert context.call_count == 1
    assert getattr(gateway, "call_count", 0) == 0


def test_canonical_supervisor_routes_knowledge_into_the_complete_flow() -> None:
    question = "How is ESP32 GPIO12 configured?"
    runtime, context, gateway, _ = _runtime(
        context=FakeApplicationContext(snapshot(task=question)),
        supervisor=CanonicalSupervisorBinding(),
    )
    result = runtime.answer(request(question=question))
    assert result.status is ApplicationRunStatus.COMPLETED
    assert result.route is ApplicationRoute.KNOWLEDGE
    assert context.call_count == 1
    assert gateway.call_count == 1


def test_create_new_routes_only_design_requests_to_greenfield() -> None:
    project = SimpleNamespace(creation_mode=SimpleNamespace(value="CREATE_NEW"))
    binding = CanonicalSupervisorBinding(
        project_mode=lambda _project_id, _tenant_id: project
    )

    design = request(
        question="请为当前 qdwq 的 STM32 新建工程设计系统需求与启动架构，不依赖现有源码。"
    )
    source_lookup = request(question="sensor_read 在哪里实现？")

    assert binding.route(design).route is ApplicationRoute.GREENFIELD_DESIGN
    assert binding.route(source_lookup).route is ApplicationRoute.FIRMWARE


def test_create_new_design_uses_greenfield_context_and_completes_once() -> None:
    question = request(
        question="请为当前 qdwq 的 STM32 新建工程设计系统需求与启动架构，不依赖现有源码。"
    )
    project = SimpleNamespace(creation_mode=SimpleNamespace(value="CREATE_NEW"))
    gateway = FakeApplicationModelGateway(content="greenfield design answer")
    context = FakeApplicationContext(snapshot(task=question.question))
    container = ApplicationDependencyContainer(
        agent_runtime=CanonicalSupervisorBinding(
            project_mode=lambda _project_id, _tenant_id: project
        ),
        context_provider=context,
        model_gateway=gateway,
        audit_sink=FakeApplicationAuditSink(),
        clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
        model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
    )

    result = ApplicationRuntime(container).answer(question)

    # P0 keeps the first turn in discovery until a generated plan is approved.
    assert result.route is ApplicationRoute.GREENFIELD_DISCOVERY
    assert result.status is ApplicationRunStatus.COMPLETED
    assert result.answer is not None
    assert result.answer.content == "greenfield design answer"
    assert result.context is not None
    assert result.context.fingerprint
    assert gateway.call_count == 1


def test_create_new_empty_project_valid_design_answer_passes_without_source_references() -> None:
    question = request(
        question="请从零设计 STM32 工程的系统需求、启动架构与实施计划。"
    )
    project = SimpleNamespace(creation_mode=SimpleNamespace(value="CREATE_NEW"))
    gateway = FakeApplicationModelGateway(content="STM32 系统设计与实施计划已形成。")
    container = ApplicationDependencyContainer(
        agent_runtime=CanonicalSupervisorBinding(
            project_mode=lambda _project_id, _tenant_id: project
        ),
        context_provider=FakeApplicationContext(None),
        model_gateway=gateway,
        audit_sink=FakeApplicationAuditSink(),
        clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
        model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
    )

    result = ApplicationRuntime(container).answer(question)

    assert result.status is ApplicationRunStatus.COMPLETED
    assert result.answer is not None
    assert result.answer.evidence_refs == ()
    assert result.requirement_context is not None
    assert result.requirement_context.requirements
    assert gateway.call_count == 1


def test_create_new_empty_answer_fails_closed_without_retry() -> None:
    question = request(
        question="请从零设计 STM32 工程的系统需求、启动架构与实施计划。"
    )
    project = SimpleNamespace(creation_mode=SimpleNamespace(value="CREATE_NEW"))
    gateway = FakeApplicationModelGateway(content="")
    container = ApplicationDependencyContainer(
        agent_runtime=CanonicalSupervisorBinding(
            project_mode=lambda _project_id, _tenant_id: project
        ),
        context_provider=FakeApplicationContext(None),
        model_gateway=gateway,
        audit_sink=FakeApplicationAuditSink(),
        clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
        model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
    )

    result = ApplicationRuntime(container).answer(question)

    assert result.status is ApplicationRunStatus.FAILED
    assert result.error_code in {
        ApplicationErrorCode.ANSWER_EMPTY,
        ApplicationErrorCode.ANSWER_CONTRACT_MISMATCH,
    }
    assert result.gateway_invocation_count <= 1
    assert gateway.call_count == 1


def test_brownfield_without_source_references_is_rejected_by_evidence_policy() -> None:
    question = EngineeringQuestion(
        question_id="request-1",
        project_id="project-1",
        question="分析已有工程中的 STM32 启动路径",
    )
    empty_snapshot = EngineeringContextSnapshot.create(
        project_id="project-1",
        task_description=question.question,
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.0,
        provenance=(),
        verification_status=ContextVerificationStatus.UNVERIFIED,
    )
    answer = EngineeringAnswer(
        question_id=question.question_id,
        content="已有工程启动路径分析。",
        confidence=0.0,
        evidence_refs=(),
        model_info=ModelInfo(
            provider="fake",
            model_name="fake-model",
            finish_reason="stop",
        ),
    )

    with pytest.raises(ApplicationAnswerRejected) as error:
        EngineeringQABinding._evidence(question, empty_snapshot, answer)

    assert error.value.validation_stage == "APPLICATION_SOURCE_REFERENCE_REQUIRED"
    assert error.value.reason_code == "ANSWER_CONTRACT_MISMATCH"


def test_create_new_source_lookup_without_evidence_fails_closed_before_gateway() -> None:
    question = request(question="sensor_read 在哪里实现？")
    project = SimpleNamespace(creation_mode=SimpleNamespace(value="CREATE_NEW"))
    gateway = FakeApplicationModelGateway()
    context = FakeApplicationContext(None)
    container = ApplicationDependencyContainer(
        agent_runtime=CanonicalSupervisorBinding(
            project_mode=lambda _project_id, _tenant_id: project
        ),
        context_provider=context,
        model_gateway=gateway,
        audit_sink=FakeApplicationAuditSink(),
        clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
        model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
        firmware_agent=FirmwareAgentApplicationAdapter(),
        firmware_prompt=FirmwarePromptAdapter(),
    )

    result = ApplicationRuntime(container).answer(question)

    assert result.route is ApplicationRoute.FIRMWARE
    assert result.status is ApplicationRunStatus.FAILED
    assert result.error_code is ApplicationErrorCode.INSUFFICIENT_EVIDENCE
    assert gateway.call_count == 0


def test_attach_existing_missing_symbol_keeps_evidence_gate_fail_closed() -> None:
    question = request(question="请定位不存在的 sensor_read symbol")
    project = SimpleNamespace(creation_mode=SimpleNamespace(value="ATTACH_EXISTING"))
    gateway = FakeApplicationModelGateway()
    context = FakeApplicationContext(None)
    container = ApplicationDependencyContainer(
        agent_runtime=CanonicalSupervisorBinding(
            project_mode=lambda _project_id, _tenant_id: project
        ),
        context_provider=context,
        model_gateway=gateway,
        audit_sink=FakeApplicationAuditSink(),
        clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
        model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
        firmware_agent=FirmwareAgentApplicationAdapter(),
        firmware_prompt=FirmwarePromptAdapter(),
    )

    result = ApplicationRuntime(container).answer(question)

    assert result.route is ApplicationRoute.BROWNFIELD_ANALYSIS
    assert result.status is ApplicationRunStatus.FAILED
    assert result.error_code is ApplicationErrorCode.INSUFFICIENT_EVIDENCE
    assert gateway.call_count == 0


def test_firmware_route_without_source_evidence_fails_before_model_gateway() -> None:
    question = "Explain this STM32 HAL firmware driver"
    _runtime_instance, context, gateway, _ = _runtime(
        route=ApplicationRoute.FIRMWARE,
        context=FakeApplicationContext(snapshot(task=question)),
        gateway=FakeApplicationModelGateway(content="grounded firmware answer"),
    )
    # Rebuild the container with the production specialist adapter while
    # retaining the deterministic test dependencies.
    container = ApplicationDependencyContainer(
        agent_runtime=FakeApplicationSupervisor(
            ApplicationRouteDecision.for_request(
                request(question=question), route=ApplicationRoute.FIRMWARE
            )
        ),
        context_provider=context,
        model_gateway=gateway,
        audit_sink=FakeApplicationAuditSink(),
        clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
        model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
        firmware_agent=FirmwareAgentApplicationAdapter(),
        firmware_prompt=FirmwarePromptAdapter(),
    )
    result = ApplicationRuntime(container).answer(request(question=question))
    assert result.status is ApplicationRunStatus.FAILED
    assert result.route is ApplicationRoute.FIRMWARE
    assert result.error_code is ApplicationErrorCode.INSUFFICIENT_EVIDENCE
    assert context.call_count == 1
    assert gateway.call_count == 0


def test_firmware_route_without_evidence_does_not_call_agent_or_model() -> None:
    question = "Explain this STM32 HAL firmware driver"
    checked_request = request(question=question)
    gateway = FakeApplicationModelGateway()
    container = ApplicationDependencyContainer(
        agent_runtime=FakeApplicationSupervisor(
            ApplicationRouteDecision.for_request(
                checked_request, route=ApplicationRoute.FIRMWARE
            )
        ),
        context_provider=FakeApplicationContext(None),
        model_gateway=gateway,
        audit_sink=FakeApplicationAuditSink(),
        clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
        model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
        firmware_agent=FirmwareAgentApplicationAdapter(),
        firmware_prompt=FirmwarePromptAdapter(),
    )
    result = ApplicationRuntime(container).answer(checked_request)
    assert result.status is ApplicationRunStatus.FAILED
    assert result.error_code is ApplicationErrorCode.INSUFFICIENT_EVIDENCE
    assert gateway.call_count == 0


def test_debug_route_executes_existing_debug_agent_before_model_gateway(caplog) -> None:
    question = "Analyze HAL_TIMEOUT from the STM32 I2C transaction"
    checked_request = request(question=question)
    gateway = FakeApplicationModelGateway(content="grounded debug answer")
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=FakeApplicationSupervisor(
                ApplicationRouteDecision.for_request(checked_request, route=ApplicationRoute.DEBUG)
            ),
            context_provider=FakeApplicationContext(None),
            model_gateway=gateway,
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
            model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
            debug_agent=DebugAgentApplicationAdapter(),
            debug_prompt=DebugPromptAdapter(),
        )
    )
    with caplog.at_level("INFO"):
        result = runtime.answer(checked_request)
    assert result.status is ApplicationRunStatus.COMPLETED
    assert result.route is ApplicationRoute.DEBUG
    assert result.answer is not None
    assert result.answer.content == "grounded debug answer"
    assert gateway.call_count == 1
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "debug_agent_execution" in messages
    assert "debug_model_gateway_completed" in messages
    assert "HAL_TIMEOUT" not in messages


class _ExternalSearchUnavailableContext:
    def __init__(self) -> None:
        self.call_count = 0

    def get_context(self, _request):
        self.call_count += 1
        raise EngineeringContextExternalEvidenceUnavailable()


def test_explicit_external_search_unavailable_does_not_call_model() -> None:
    gateway = FakeApplicationModelGateway()
    runtime, context, _, _ = _runtime(
        context=_ExternalSearchUnavailableContext(),
        gateway=gateway,
        supervisor=FakeApplicationSupervisor(
            ApplicationRouteDecision.for_request(
                request(external_search=True), route=ApplicationRoute.KNOWLEDGE
            )
        ),
    )

    result = runtime.answer(request(external_search=True))

    assert result.status is ApplicationRunStatus.FAILED
    assert result.error_code is ApplicationErrorCode.EXTERNAL_EVIDENCE_UNAVAILABLE
    assert context.call_count == 1
    assert gateway.call_count == 0


def test_async_firmware_route_without_source_evidence_fails_before_model_gateway() -> None:
    question = "Explain this STM32 HAL firmware driver"
    checked_request = request(question=question)
    gateway = FakeApplicationModelGateway(content="async grounded firmware answer")
    container = ApplicationDependencyContainer(
        agent_runtime=FakeApplicationSupervisor(
            ApplicationRouteDecision.for_request(
                checked_request, route=ApplicationRoute.FIRMWARE
            )
        ),
        context_provider=FakeApplicationContext(snapshot(task=question)),
        model_gateway=gateway,
        audit_sink=FakeApplicationAuditSink(),
        clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
        model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
        firmware_agent=FirmwareAgentApplicationAdapter(),
        firmware_prompt=FirmwarePromptAdapter(),
    )
    result = asyncio.run(ApplicationRuntime(container).aanswer(checked_request))
    assert result.status is ApplicationRunStatus.FAILED
    assert result.route is ApplicationRoute.FIRMWARE
    assert result.error_code is ApplicationErrorCode.INSUFFICIENT_EVIDENCE
    assert gateway.call_count == 0


class _FirmwareResult:
    def __init__(self, evidence: tuple[EngineeringContextEvidence, ...]) -> None:
        self.facts = ("observed firmware source",)
        self.evidence = tuple(item.evidence_id for item in evidence)
        self.analysis = ("observed firmware call",)
        self.confidence = 1.0
        self.limitations = ()
        self.context_evidence = evidence


class _FirmwareAgent:
    def __init__(self, result: _FirmwareResult | Exception) -> None:
        self._result = result
        self.calls = 0

    def analyze(self, _request, _context):
        self.calls += 1
        if isinstance(self._result, Exception):
            raise self._result
        return self._result

    async def aanalyze(self, request, context):
        return self.analyze(request, context)


def test_firmware_source_evidence_is_gated_after_agent_execution() -> None:
    question = "I2C在哪里初始化？"
    checked_request = request(question=question)
    evidence = EngineeringContextEvidence.create(
        evidence_id="firmware-i2c-init",
        project_id="project-1",
        source_type=ContextSourceType.FIRMWARE_SOURCE,
        subject="MX_I2C1_Init",
        summary="observed function MX_I2C1_Init at line 42",
        fact_key="line",
        fact_value="42",
        source_reference="Core/Src/i2c.c",
            source_fingerprint="sha256:" + "f" * 64,
            confidence=1.0,
            verification_status=ContextVerificationStatus.VERIFIED,
            provenance=("Core/Src/i2c.c", "sha256:" + "f" * 64),
        )
    agent = _FirmwareAgent(_FirmwareResult((evidence,)))
    gateway = FakeApplicationModelGateway(content="grounded firmware answer")
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=FakeApplicationSupervisor(
                ApplicationRouteDecision.for_request(checked_request, route=ApplicationRoute.FIRMWARE)
            ),
            context_provider=FakeApplicationContext(None),
            model_gateway=gateway,
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
            model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
            firmware_agent=agent,
            firmware_prompt=FirmwarePromptAdapter(),
        )
    )
    result = runtime.answer(checked_request)
    assert result.status is ApplicationRunStatus.COMPLETED
    assert agent.calls == 1
    assert gateway.call_count == 1
    assert result.answer is not None
    assert result.answer.evidence_refs == ("firmware-i2c-init",)


def test_firmware_agent_empty_source_evidence_fails_before_model_gateway() -> None:
    checked_request = request(question="I2C在哪里初始化？")
    agent = _FirmwareAgent(_FirmwareResult(()))
    gateway = FakeApplicationModelGateway()
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=FakeApplicationSupervisor(
                ApplicationRouteDecision.for_request(checked_request, route=ApplicationRoute.FIRMWARE)
            ),
            context_provider=FakeApplicationContext(None),
            model_gateway=gateway,
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
            model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
            firmware_agent=agent,
            firmware_prompt=FirmwarePromptAdapter(),
        )
    )
    result = runtime.answer(checked_request)
    assert result.status is ApplicationRunStatus.FAILED
    assert result.error_code is ApplicationErrorCode.INSUFFICIENT_EVIDENCE
    assert agent.calls == 1
    assert gateway.call_count == 0


def test_firmware_evidence_without_provenance_fails_closed_before_model_gateway() -> None:
    checked_request = request(question="I2C在哪里初始化？")
    evidence = EngineeringContextEvidence.create(
        evidence_id="firmware-incomplete-provenance",
        project_id="project-1",
        source_type=ContextSourceType.FIRMWARE_SOURCE,
        subject="MX_I2C1_Init",
        summary="observed function MX_I2C1_Init at line 42",
        fact_key="line",
        fact_value="42",
        source_reference="Core/Src/main.c",
        source_fingerprint="sha256:" + "a" * 64,
        confidence=1.0,
        verification_status=ContextVerificationStatus.VERIFIED,
    )
    agent = _FirmwareAgent(_FirmwareResult((evidence,)))
    gateway = FakeApplicationModelGateway()
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=FakeApplicationSupervisor(
                ApplicationRouteDecision.for_request(checked_request, route=ApplicationRoute.FIRMWARE)
            ),
            context_provider=FakeApplicationContext(None),
            model_gateway=gateway,
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
            model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
            firmware_agent=agent,
            firmware_prompt=FirmwarePromptAdapter(),
        )
    )

    result = runtime.answer(checked_request)

    assert result.status is ApplicationRunStatus.FAILED
    assert result.error_code is ApplicationErrorCode.INSUFFICIENT_EVIDENCE
    assert agent.calls == 1
    assert gateway.call_count == 0


def test_sync_and_async_have_equivalent_bound_results() -> None:
    sync_runtime, *_ = _runtime()
    async_runtime, *_ = _runtime()
    sync = sync_runtime.answer(request())
    async_result = asyncio.run(async_runtime.aanswer(request()))
    assert sync.model_dump(mode="python") == async_result.model_dump(mode="python")


@pytest.mark.parametrize(
    ("message", "route", "code"),
    [
        (
            "Generate ESP-IDF firmware code",
            ApplicationRoute.FIRMWARE,
            ApplicationErrorCode.UNSUPPORTED_ROUTE,
        ),
        (
            "Analyze this Guru Meditation Error",
            ApplicationRoute.DEBUG_ANALYSIS,
            ApplicationErrorCode.UNSUPPORTED_ROUTE,
        ),
        (
            "Hello",
            ApplicationRoute.CLARIFICATION_REQUIRED,
            ApplicationErrorCode.CLARIFICATION_REQUIRED,
        ),
    ],
)
def test_canonical_supervisor_rejects_non_knowledge_without_downstream_calls(
    message: str, route: ApplicationRoute, code: ApplicationErrorCode
) -> None:
    context = FakeApplicationContext(snapshot())
    gateway = FakeApplicationModelGateway()
    runtime, _, _, audit = _runtime(
        context=context,
        gateway=gateway,
        supervisor=CanonicalSupervisorBinding(),
    )
    result = runtime.answer(request(question=message))
    assert result.route is route
    assert result.status is ApplicationRunStatus.REJECTED
    assert result.error_code is code
    assert result.answer is None
    assert context.call_count == 0
    assert gateway.call_count == 0
    assert audit.get(result.audit.event_id).event_type is ApplicationAuditEventType.QUESTION_REJECTED


@pytest.mark.parametrize(
    ("context_value", "code"),
    [
        (None, ApplicationErrorCode.INSUFFICIENT_EVIDENCE),
        (snapshot(project_id="project-2"), ApplicationErrorCode.CONTEXT_UNAVAILABLE),
        (
            snapshot(task="Different task"),
            ApplicationErrorCode.CONTEXT_UNAVAILABLE,
        ),
    ],
)
def test_context_failures_are_audited_without_model_call(context_value, code) -> None:
    gateway = FakeApplicationModelGateway()
    runtime, _, _, audit = _runtime(
        context=FakeApplicationContext(context_value), gateway=gateway
    )
    result = runtime.answer(request())
    assert result.status is ApplicationRunStatus.FAILED
    assert result.error_code is code
    assert result.answer is None
    assert gateway.call_count == 0
    assert audit.get(result.audit.event_id).event_type is ApplicationAuditEventType.QUESTION_FAILED


def test_model_failure_is_sanitized_and_audited() -> None:
    gateway = FakeApplicationModelGateway(failure=RuntimeError("PRIVATE_TOKEN"))
    runtime, _, _, audit = _runtime(gateway=gateway)
    result = runtime.answer(request())
    assert result.status is ApplicationRunStatus.FAILED
    assert result.error_code is ApplicationErrorCode.ANALYSIS_RUNTIME_FAILED
    assert result.gateway_invocation_count == 1
    assert result.provider_response_present is False
    assert result.answer_validation_stage == "PROVIDER_TRANSPORT_OR_PARSE"
    assert "PRIVATE" not in str(result)
    assert audit.get(result.audit.event_id).error_code is ApplicationErrorCode.ANALYSIS_RUNTIME_FAILED


def test_provider_parse_failure_is_classified_without_claiming_a_response() -> None:
    gateway = FakeApplicationModelGateway(
        failure=ModelGatewayProviderFailure(
            "PROVIDER_PARSE_FAILED",
            provider_response_present=True,
            validation_stage="PROVIDER_PARSE",
        )
    )
    runtime, _, _, audit = _runtime(gateway=gateway)

    result = runtime.answer(request())

    assert result.status is ApplicationRunStatus.FAILED
    assert result.error_code is ApplicationErrorCode.PROVIDER_PARSE_FAILED
    assert result.gateway_invocation_count == 1
    assert result.provider_response_present is True
    assert result.answer_validation_stage == "PROVIDER_PARSE"
    assert result.answer_rejection_reason is None
    assert audit.get(result.audit.event_id).error_code is ApplicationErrorCode.PROVIDER_PARSE_FAILED


def test_audit_failure_prevents_result_release() -> None:
    runtime, *_ = _runtime(audit=FakeApplicationAuditSink(fail_writes=True))
    with pytest.raises(ApplicationAuditUnavailable):
        runtime.answer(request())


def test_identical_runtime_retry_reuses_the_existing_audit_event() -> None:
    runtime, _, gateway, audit = _runtime()
    first = runtime.answer(request())
    second = runtime.answer(request())
    assert second == first
    assert gateway.call_count == 2
    assert audit.events == (audit.get(first.audit.event_id),)


def test_malformed_model_response_is_failed_and_audited() -> None:
    class _MalformedGateway:
        def generate(self, request):
            return object()

        async def agenerate(self, request):
            return object()

    runtime, _, _, audit = _runtime(gateway=_MalformedGateway())
    result = runtime.answer(request())
    assert result.status is ApplicationRunStatus.FAILED
    assert result.error_code is ApplicationErrorCode.ANSWER_SCHEMA_INVALID
    assert result.gateway_invocation_count == 1
    assert result.provider_response_present is True
    assert result.answer_validation_stage == "MODEL_RESPONSE_NORMALIZATION"
    assert result.answer_rejection_reason == "ANSWER_SCHEMA_INVALID"
    assert audit.get(result.audit.event_id).event_type is ApplicationAuditEventType.QUESTION_FAILED


def test_tampered_audit_read_prevents_result_release() -> None:
    class _TamperedAudit(FakeApplicationAuditSink):
        def get(self, event_id):
            value = super().get(event_id)
            if value is None:
                return None
            return object()

    audit = _TamperedAudit()
    runtime, *_ = _runtime(audit=audit)
    runtime.answer(request())
    with pytest.raises(ApplicationAuditUnavailable):
        runtime.answer(request())


def test_async_cancellation_propagates_without_audit() -> None:
    gateway = FakeApplicationModelGateway(cancel_async=True)
    runtime, _, _, audit = _runtime(gateway=gateway)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime.aanswer(request()))
    assert audit.events == ()
