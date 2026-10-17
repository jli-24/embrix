from __future__ import annotations

import asyncio

import httpx
import pytest

from embedded_copilot.model_gateway import (
    ModelGatewayUnavailable,
    ModelProviderRateLimited,
    ModelProviderTimeout,
    ModelProviderUnauthorized,
)
from embedded_copilot.model_runtime.integration import (
    ProductionModelSettings,
    ProductionProvider,
    create_production_model_binding,
    create_production_provider,
)

from .fixtures import model_request


def _response(content: str = "Grounded answer.") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 2,
                "completion_tokens": 3,
                "total_tokens": 5,
            },
        },
    )


@pytest.mark.parametrize(
    ("provider", "key", "url"),
    (
        ("openai", "OPENAI_API_KEY", "https://api.openai.com/v1/chat/completions"),
        (
            "deepseek",
            "DEEPSEEK_API_KEY",
            "https://api.deepseek.com/v1/chat/completions",
        ),
        (
            "qwen",
            "QWEN_API_KEY",
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        ),
    ),
)
def test_factory_builds_existing_cloud_adapter_chain(
    provider: str, key: str, url: str
) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["Authorization"]
        return _response()

    settings = ProductionModelSettings.from_mapping(
        {
            "MODEL_PROVIDER": provider,
            "MODEL_NAME": "model-test",
            key: "production-secret",
        }
    )
    binding = create_production_model_binding(
        settings, http_transport=httpx.MockTransport(handler)
    )
    response = binding.gateway.generate(model_request(binding.model_policy))
    assert response.provider == provider
    assert response.content == "Grounded answer."
    assert seen == {
        "url": url,
        "authorization": "Bearer production-secret",
    }
    assert not hasattr(binding, "api_key")
    assert binding.provider is ProductionProvider(provider)


@pytest.mark.parametrize(
    ("provider", "key", "model", "url"),
    (
        (
            "anthropic",
            "ANTHROPIC_API_KEY",
            "claude-test",
            "https://api.anthropic.com/v1/messages",
        ),
        (
            "google",
            "GOOGLE_API_KEY",
            "gemini-test",
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent",
        ),
        ("xai", "XAI_API_KEY", "grok-test", "https://api.x.ai/v1/chat/completions"),
        (
            "zhipu",
            "ZHIPU_API_KEY",
            "glm-test",
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        ),
        (
            "alibaba",
            "ALIBABA_API_KEY",
            "qwen-test",
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        ),
        (
            "moonshot",
            "MOONSHOT_API_KEY",
            "kimi-test",
            "https://api.moonshot.cn/v1/chat/completions",
        ),
        (
            "minimax",
            "MINIMAX_API_KEY",
            "minimax-test",
            "https://api.minimax.example/v1/chat/completions",
        ),
    ),
)
def test_factory_uses_canonical_protocol_adapter_for_extended_clouds(
    provider: str, key: str, model: str, url: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == url
        if provider == "anthropic":
            return httpx.Response(
                200,
                json={
                    "id": "msg-test",
                    "model": model,
                    "content": [{"type": "text", "text": "Grounded answer."}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 2, "output_tokens": 3},
                },
            )
        if provider == "google":
            return httpx.Response(
                200,
                json={
                    "modelVersion": model,
                    "candidates": [
                        {
                            "content": {"parts": [{"text": "Grounded answer."}]},
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 2,
                        "candidatesTokenCount": 3,
                        "totalTokenCount": 5,
                    },
                },
            )
        return _response()

    values = {
        "MODEL_PROVIDER": provider,
        "MODEL_NAME": model,
        key: "production-secret",
    }
    if provider == "minimax":
        values["MINIMAX_BASE_URL"] = "https://api.minimax.example/v1"
    settings = ProductionModelSettings.from_mapping(values)
    binding = create_production_model_binding(
        settings,
        http_transport=httpx.MockTransport(handler),
    )
    response = binding.gateway.generate(model_request(binding.model_policy))

    assert response.content == "Grounded answer."
    assert response.provider == provider
    assert response.endpoint_id == f"endpoint-{provider}"


def test_retry_wrapper_retries_transient_error_with_bounded_backoff() -> None:
    calls = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429) if calls == 1 else _response()

    settings = ProductionModelSettings.from_mapping(
        {
            "MODEL_PROVIDER": "openai",
            "MODEL_NAME": "gpt-test",
            "OPENAI_API_KEY": "secret",
            "MODEL_MAX_RETRIES": "1",
            "MODEL_RETRY_BACKOFF_SECONDS": "0.2",
        }
    )
    provider = create_production_provider(
        settings,
        http_transport=httpx.MockTransport(handler),
        sleep=delays.append,
    )
    assert provider.generate(model_request(settings.to_model_policy())).content
    assert calls == 2
    assert delays == [0.2]


def test_authentication_error_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401)

    settings = ProductionModelSettings.from_mapping(
        {
            "MODEL_PROVIDER": "openai",
            "MODEL_NAME": "gpt-test",
            "OPENAI_API_KEY": "secret",
            "MODEL_MAX_RETRIES": "3",
        }
    )
    provider = create_production_provider(
        settings, http_transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ModelProviderUnauthorized):
        provider.generate(model_request(settings.to_model_policy()))
    assert calls == 1


def test_retry_exhaustion_is_sanitized_by_gateway() -> None:
    settings = ProductionModelSettings.from_mapping(
        {
            "MODEL_PROVIDER": "openai",
            "MODEL_NAME": "gpt-test",
            "OPENAI_API_KEY": "secret",
            "MODEL_MAX_RETRIES": "1",
        }
    )
    binding = create_production_model_binding(
        settings,
        http_transport=httpx.MockTransport(lambda request: httpx.Response(429)),
    )
    with pytest.raises(ModelGatewayUnavailable) as raised:
        binding.gateway.generate(model_request(binding.model_policy))
    assert "secret" not in str(raised.value)


def test_async_retry_and_cancellation_semantics() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429) if calls == 1 else _response("Async answer.")

    settings = ProductionModelSettings.from_mapping(
        {
            "MODEL_PROVIDER": "openai",
            "MODEL_NAME": "gpt-test",
            "OPENAI_API_KEY": "secret",
            "MODEL_MAX_RETRIES": "1",
        }
    )
    provider = create_production_provider(
        settings,
        async_transport=httpx.MockTransport(handler),
        asleep=lambda delay: asyncio.sleep(0),
    )
    result = asyncio.run(provider.agenerate(model_request(settings.to_model_policy())))
    assert result.content == "Async answer."
    assert calls == 2


def test_provider_keeps_typed_rate_limit_when_retry_is_disabled() -> None:
    settings = ProductionModelSettings.from_mapping(
        {
            "MODEL_PROVIDER": "openai",
            "MODEL_NAME": "gpt-test",
            "OPENAI_API_KEY": "secret",
            "MODEL_MAX_RETRIES": "0",
        }
    )
    provider = create_production_provider(
        settings,
        http_transport=httpx.MockTransport(lambda request: httpx.Response(429)),
    )
    with pytest.raises(ModelProviderRateLimited):
        provider.generate(model_request(settings.to_model_policy()))


def test_transport_timeout_is_mapped_and_retried_only_to_configured_limit() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("sensitive upstream detail", request=request)

    settings = ProductionModelSettings.from_mapping(
        {
            "MODEL_PROVIDER": "openai",
            "MODEL_NAME": "gpt-test",
            "OPENAI_API_KEY": "secret",
            "MODEL_MAX_RETRIES": "1",
            "MODEL_RETRY_BACKOFF_SECONDS": "0.0",
        }
    )
    provider = create_production_provider(
        settings, http_transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ModelProviderTimeout) as raised:
        provider.generate(model_request(settings.to_model_policy()))
    assert calls == 2
    assert "sensitive" not in str(raised.value)


def test_async_cancellation_is_never_retried_or_mapped() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise asyncio.CancelledError

    settings = ProductionModelSettings.from_mapping(
        {
            "MODEL_PROVIDER": "openai",
            "MODEL_NAME": "gpt-test",
            "OPENAI_API_KEY": "secret",
            "MODEL_MAX_RETRIES": "3",
        }
    )
    provider = create_production_provider(
        settings, async_transport=httpx.MockTransport(handler)
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(provider.agenerate(model_request(settings.to_model_policy())))
    assert calls == 1
