from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx

from embedded_copilot.application import (
    ApplicationDependencyContainer,
    ApplicationRunStatus,
    ApplicationRuntime,
    CanonicalSupervisorBinding,
    FakeApplicationAuditSink,
    FakeApplicationContext,
    FixedApplicationClock,
)
from embedded_copilot.model_runtime.integration import (
    ProductionModelSettings,
    create_production_model_binding,
)

from .fixtures import NOW, application_request, context


def test_application_runtime_reaches_real_provider_adapter_and_audit() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "GPIO12 is reserved by the board configuration."
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    settings = ProductionModelSettings.from_mapping(
        {
            "MODEL_PROVIDER": "openai",
            "MODEL_NAME": "gpt-test",
            "OPENAI_API_KEY": "production-secret",
        }
    )
    production = create_production_model_binding(
        settings, http_transport=httpx.MockTransport(handler)
    )
    audit = FakeApplicationAuditSink()
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=CanonicalSupervisorBinding(),
            context_provider=FakeApplicationContext(context()),
            model_gateway=production.gateway,
            audit_sink=audit,
            clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
            model_policy=production.model_policy,
        )
    )
    result = runtime.answer(application_request())
    assert result.status is ApplicationRunStatus.COMPLETED
    assert result.answer is not None
    assert result.answer.model_info.provider == "openai"
    assert result.answer.model_info.model_name == "gpt-test"
    assert calls == ["https://api.openai.com/v1/chat/completions"]
    assert audit.get(result.audit.event_id) is not None
    assert "production-secret" not in str(result)


def test_async_application_runtime_uses_the_same_production_binding() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "Async grounded answer."},
                        "finish_reason": "stop",
                    }
                ]
            },
        )
    )
    settings = ProductionModelSettings.from_mapping(
        {
            "MODEL_PROVIDER": "deepseek",
            "MODEL_NAME": "deepseek-chat",
            "DEEPSEEK_API_KEY": "production-secret",
        }
    )
    production = create_production_model_binding(settings, async_transport=transport)
    audit = FakeApplicationAuditSink()
    runtime = ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=CanonicalSupervisorBinding(),
            context_provider=FakeApplicationContext(context()),
            model_gateway=production.gateway,
            audit_sink=audit,
            clock=FixedApplicationClock(NOW + timedelta(seconds=1)),
            model_policy=production.model_policy,
        )
    )
    result = asyncio.run(runtime.aanswer(application_request()))
    assert result.status is ApplicationRunStatus.COMPLETED
    assert result.answer is not None
    assert result.answer.model_info.provider == "deepseek"
    assert audit.get(result.audit.event_id) is not None
