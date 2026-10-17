from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from embedded_copilot.application import ApplicationQuestionRequest
from embedded_copilot.application.desktop_composition import (
    build_project_first_chat_runtime,
)
from embedded_copilot.desktop_copilot_chat import (
    AnalysisTraceStage,
    DesktopChatRequest,
)
from embedded_copilot.engineering_requirements import (
    RequirementContext,
    RequirementItem,
    RequirementItemStatus,
    RequirementLifecycle,
    RequirementProvenance,
)
from embedded_copilot.model_gateway import (
    ModelGatewayService,
    ModelProviderSettings,
    create_model_provider,
)
from embedded_copilot.multimodal.context import ProcessLocalAttachmentBindingRepository
from embedded_copilot.project_catalog import (
    ProjectCatalogRuntime,
    ProjectCreationMode,
    ProjectCreationRequest,
    ProjectLocationReference,
    ProjectType,
)


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
TENANT = "tenant-1"
REQUESTER = "engineer-1"
SECRET = "phase2-test-secret"


def _catalog(tmp_path: Path) -> ProjectCatalogRuntime:
    (tmp_path / "projects").mkdir()
    return ProjectCatalogRuntime(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "state" / "desktop.sqlite3",
        metadata_directory=tmp_path / "metadata",
        initialize_async=False,
    )


def _project(catalog: ProjectCatalogRuntime):
    location = catalog.locations(tenant_id=TENANT, requester=REQUESTER)[0]
    preview = catalog.preview(
        ProjectCreationRequest(
            name="STM32 Smart Car",
            project_type=ProjectType.STM32,
            creation_mode=ProjectCreationMode.CREATE_NEW,
            parent_location=ProjectLocationReference(
                location_id=location.location_id,
                fingerprint=location.fingerprint,
            ),
        ),
        tenant_id=TENANT,
        requester=REQUESTER,
    )
    return catalog.create(
        preview.preview_id,
        preview.fingerprint,
        confirmed_at=preview.created_at,
        tenant_id=TENANT,
        requester=REQUESTER,
    )


def _gateway(payloads: list[dict[str, object]]) -> ModelGatewayService:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        payloads.append(payload)
        user_content = payload["messages"][-1]["content"]
        if "OLED" in user_content:
            content = (
                "可以在 STM32F103、STM32CubeMX 工程中增加 OLED 速度显示，"
                "并沿用 PID Motor Control 的控制数据。"
            )
        elif "STM32F103" in user_content:
            content = (
                "当前项目基于 STM32F103，可以从电机驱动和控制架构开始设计，"
                "再结合 STM32CubeMX 配置 PID Motor Control。"
            )
        elif "STM32 智能小车" in user_content:
            content = (
                "明白了，你要做一个 STM32 智能小车。"
                "接下来需要确认电机驱动、电源和通信方式，再开始设计控制架构。"
            )
        else:
            content = "你好，我可以协助梳理嵌入式工程设计。"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": content},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    settings = ModelProviderSettings.from_environment(
        {
            "MODEL_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": SECRET,
            "DEEPSEEK_MODEL": "deepseek-chat",
        }
    )
    provider = create_model_provider(
        settings=settings,
        http_transport=httpx.MockTransport(handler),
    )
    return ModelGatewayService(providers={settings.provider: provider})


def _runtime(tmp_path: Path, payloads: list[dict[str, object]]):
    catalog = _catalog(tmp_path)
    project = _project(catalog)
    settings = ModelProviderSettings.from_environment(
        {
            "MODEL_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": SECRET,
            "DEEPSEEK_MODEL": "deepseek-chat",
        }
    )
    runtime = build_project_first_chat_runtime(
        database=tmp_path / "state" / "desktop.sqlite3",
        project_catalog=catalog,
        approved_memory_store=None,
        environment=None,
        attachments=ProcessLocalAttachmentBindingRepository(),
        provider_binding=_gateway(payloads),
        provider_policy=settings.to_model_policy(),
    )
    assert runtime is not None
    catalog.set_chat_runtime(runtime)
    runtime.set_conversation_port(catalog)
    session = catalog.create_conversation(
        project.project_id,
        tenant_id=TENANT,
        requester=REQUESTER,
    )
    return catalog, project, runtime, session


def _live_runtime(tmp_path: Path):
    catalog = _catalog(tmp_path)
    project = _project(catalog)
    runtime = build_project_first_chat_runtime(
        database=tmp_path / "state" / "desktop.sqlite3",
        project_catalog=catalog,
        approved_memory_store=None,
        environment=None,
        attachments=ProcessLocalAttachmentBindingRepository(),
    )
    assert runtime is not None
    catalog.set_chat_runtime(runtime)
    runtime.set_conversation_port(catalog)
    session = catalog.create_conversation(
        project.project_id,
        tenant_id=TENANT,
        requester=REQUESTER,
    )
    return catalog, project, runtime, session


def _request(runtime, session, project, question: str, request_id: str) -> DesktopChatRequest:
    return DesktopChatRequest(
        session_id=session.session_id,
        project_reference=runtime.project(
            session.session_id,
            requester=REQUESTER,
            tenant_id=TENANT,
        ),
        application_question=ApplicationQuestionRequest(
            request_id=request_id,
            trace_id=f"trace-{request_id}",
            project_id=project.project_id,
            requester=REQUESTER,
            question=question,
            constraints=(),
            requested_at=NOW,
        ),
        context_references=(),
        requested_at=NOW,
    )


def _seed_context(catalog, session, project) -> RequirementContext:
    context = RequirementContext(
        project_id=project.project_id,
        conversation_id=session.conversation.conversation_id,
        requirements=tuple(
            RequirementItem(
                requirement_id=f"seed-{index}",
                project_id=project.project_id,
                conversation_id=session.conversation.conversation_id,
                category=category,
                normalized_value=value,
                status=RequirementItemStatus.CONFIRMED,
                source_turn_id="seed-turn",
                provenance=RequirementProvenance.PROJECT_METADATA,
            )
            for index, (category, value) in enumerate(
                (
                    ("HARDWARE", "STM32F103"),
                    ("TOOLCHAIN", "STM32CubeMX"),
                    ("FUNCTION", "PID Motor Control"),
                ),
                start=1,
            )
        ),
        lifecycle=RequirementLifecycle.DESIGN_READY,
    )
    catalog.begin_turn(
        session.session_id,
        request_id="seed-request",
        trace_id="seed-trace",
        question="seed engineering context",
        created_at=NOW,
    )
    catalog.complete_turn(
        session.session_id,
        "seed-request",
        type(
            "SeedAnswer",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "answer_text": "seeded",
                    "evidence_details": (),
                    "recommendations": (),
                }
            },
        )(),
        NOW,
        route="GREENFIELD_DISCOVERY",
        trace_id="seed-trace",
        analysis_state="DESIGN_READY",
        analysis_reason_code="ANSWER_COMPLETED",
        requirement_context=context,
    )
    return context


def _reconstructed_requirement_projection(payload: dict[str, object]) -> str:
    content = payload["messages"][-1]["content"]
    parts = re.findall(
        r"RequirementContext part \d+/\d+: (.*?) \[end part\]",
        content,
    )
    return "".join(parts)


def test_desktop_chat_uses_official_gateway_and_preserves_exploration_policy(
    tmp_path: Path,
) -> None:
    payloads: list[dict[str, object]] = []
    _, project, runtime, session = _runtime(tmp_path, payloads)

    hello = runtime.submit(
        _request(runtime, session, project, "你好", "hello-request"),
        requester=REQUESTER,
        tenant_id=TENANT,
    )
    exploration = runtime.submit(
        _request(
            runtime,
            session,
            project,
            "我要做一个 STM32 智能小车",
            "exploration-request",
        ),
        requester=REQUESTER,
        tenant_id=TENANT,
    )

    assert hello.answer_text.startswith("你好")
    assert "STM32 智能小车" in exploration.answer_text
    assert "接下来" in exploration.answer_text
    assert all(
        marker not in exploration.answer_text
        for marker in (
            "CONFIRMED",
            "UNKNOWN",
            "PROPOSED",
            "OPEN_QUESTION",
            "Requirement ID",
            "需求状态",
        )
    )
    assert len(payloads) == 2
    assert all(payload["messages"][0]["role"] == "system" for payload in payloads)
    assert all("Engineering context:" in payload["messages"][-1]["content"] for payload in payloads)

    for request_id in ("hello-request", "exploration-request"):
        gateway_trace = next(
            event
            for event in runtime._traces.read(f"trace-{request_id}")
            if event.stage is AnalysisTraceStage.MODEL_GATEWAY
        )
        assert gateway_trace.service_identity == "ModelGatewayService"
        assert gateway_trace.gate_result == "PASS"
        assert gateway_trace.gateway_invocation_count == 1


def test_engineering_context_is_injected_across_desktop_turns_without_reasking(
    tmp_path: Path,
) -> None:
    payloads: list[dict[str, object]] = []
    catalog, project, runtime, session = _runtime(tmp_path, payloads)
    context = _seed_context(catalog, session, project)

    design = runtime.submit(
        _request(runtime, session, project, "下一步怎么设计", "design-request"),
        requester=REQUESTER,
        tenant_id=TENANT,
    )
    oled = runtime.submit(
        _request(runtime, session, project, "增加 OLED 显示速度", "oled-request"),
        requester=REQUESTER,
        tenant_id=TENANT,
    )

    assert "STM32F103" in design.answer_text
    assert "STM32CubeMX" in design.answer_text
    assert "PID Motor Control" in design.answer_text
    assert "OLED" in oled.answer_text
    assert "你是什么项目" not in oled.answer_text
    assert "项目是什么" not in oled.answer_text
    assert len(payloads) == 2
    for value in ("STM32F103", "STM32CubeMX", "PID Motor Control"):
        assert value in _reconstructed_requirement_projection(payloads[0])
        assert value in _reconstructed_requirement_projection(payloads[1])
    stored = catalog.conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id=TENANT,
        requester=REQUESTER,
    )
    assert stored.requirement_context is not None
    assert stored.requirement_context.fingerprint != context.fingerprint
    assert stored.requirement_context.lifecycle is RequirementLifecycle.REQUIREMENTS_REFINING


def test_phase2_telemetry_does_not_log_secret_context_or_response(
    tmp_path: Path,
    caplog,
) -> None:
    payloads: list[dict[str, object]] = []
    _, project, runtime, session = _runtime(tmp_path, payloads)
    private_prompt = "这是不应进入日志的唯一问题内容-phase2"
    with caplog.at_level(logging.INFO):
        answer = runtime.submit(
            _request(runtime, session, project, private_prompt, "security-request"),
            requester=REQUESTER,
            tenant_id=TENANT,
        )

    assert answer.answer_text
    logged = caplog.text
    assert SECRET not in logged
    assert "Engineering context:" not in logged
    assert answer.answer_text not in logged
    assert private_prompt not in logged
    traces = runtime._traces.read("trace-security-request")
    serialized = " ".join(str(event.model_dump(mode="json")) for event in traces)
    assert SECRET not in serialized
    assert answer.answer_text not in serialized
    assert "answer_text" not in serialized


def test_desktop_chat_real_provider_is_explicitly_opt_in(tmp_path: Path) -> None:
    if os.getenv("RUN_EMBRIX_PHASE2_LIVE") != "1":
        pytest.skip("set RUN_EMBRIX_PHASE2_LIVE=1 to run the real Provider check")

    catalog, project, runtime, session = _live_runtime(tmp_path)
    hello = runtime.submit(
        _request(runtime, session, project, "你好", "live-hello-request"),
        requester=REQUESTER,
        tenant_id=TENANT,
    )
    exploration = runtime.submit(
        _request(
            runtime,
            session,
            project,
            "我要做一个 STM32 智能小车",
            "live-exploration-request",
        ),
        requester=REQUESTER,
        tenant_id=TENANT,
    )

    assert hello.answer_text.strip()
    assert exploration.answer_text.strip()
    assert all(
        marker not in exploration.answer_text
        for marker in (
            "CONFIRMED",
            "UNKNOWN",
            "PROPOSED",
            "OPEN_QUESTION",
            "Requirement ID",
            "需求状态",
        )
    )

    _seed_context(catalog, session, project)
    design = runtime.submit(
        _request(runtime, session, project, "下一步怎么设计", "live-design-request"),
        requester=REQUESTER,
        tenant_id=TENANT,
    )
    oled = runtime.submit(
        _request(
            runtime,
            session,
            project,
            "增加 OLED 显示速度",
            "live-oled-request",
        ),
        requester=REQUESTER,
        tenant_id=TENANT,
    )

    assert design.answer_text.strip()
    assert any(
        value in design.answer_text
        for value in ("STM32F103", "STM32CubeMX", "PID Motor Control", "PID")
    )
    assert oled.answer_text.strip()
    assert "OLED" in oled.answer_text.upper()
    assert "你是什么项目" not in oled.answer_text
    assert "项目是什么" not in oled.answer_text

    for request_id in (
        "live-hello-request",
        "live-exploration-request",
        "live-design-request",
        "live-oled-request",
    ):
        gateway_trace = next(
            event
            for event in runtime._traces.read(f"trace-{request_id}")
            if event.stage is AnalysisTraceStage.MODEL_GATEWAY
        )
        assert gateway_trace.service_identity == "ModelGatewayService"
        assert gateway_trace.gate_result == "PASS"
        assert gateway_trace.gateway_invocation_count == 1
