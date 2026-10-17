from __future__ import annotations

import json
import socket
import ssl

import httpx
import pytest
from pydantic import SecretStr

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.model_gateway import (
    ModelMessage,
    ModelPolicy,
    ModelRequest,
    create_model_provider,
)
from embedded_copilot.model_gateway._transport_metadata import ProviderExecutionResult
from embedded_copilot.model_gateway.adapters.protocol_http import (
    ProtocolHTTPProviderAdapter,
)
from embedded_copilot.model_gateway.exceptions import (
    ModelProviderHTTPError,
    ModelProviderNetworkError,
    ModelProviderRateLimited,
)
from embedded_copilot.model_gateway.profiles import ProtocolFamily


def _request(model: str, *, provider: str = "anthropic") -> ModelRequest:
    context = EngineeringContextSnapshot.create(
        project_id="project-a",
        task_description="request",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.0,
        provenance=(),
        verification_status="UNVERIFIED",
    )
    return ModelRequest(
        request_id="request-1",
        task="request",
        engineering_context=context,
        system_instruction="Use evidence only.",
        model_policy=ModelPolicy(primary_provider=provider, model_name=model),
    )


def test_anthropic_protocol_adapter_uses_injected_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/messages")
        assert request.headers["x-api-key"] == "test-secret"
        return httpx.Response(
            200,
            json={
                "id": "msg-1",
                "model": "claude-test",
                "content": [{"type": "text", "text": "answer"}],
                "usage": {"input_tokens": 2, "output_tokens": 3},
            },
        )

    adapter = ProtocolHTTPProviderAdapter(
        provider="anthropic",
        base_url="https://api.anthropic.com/v1",
        protocol_family=ProtocolFamily.ANTHROPIC_MESSAGES,
        api_key="test-secret",
        http_transport=httpx.MockTransport(handler),
    )
    response = adapter.generate(_request("claude-test"))
    assert response.provider == "anthropic"
    assert response.model_name == "claude-test"
    assert response.provider_id == "anthropic"
    assert response.endpoint_id == "endpoint-anthropic"
    assert response.provider_response_id == "msg-1"
    assert response.content == "answer"
    assert response.usage is not None and response.usage.total_tokens == 5


def test_protocol_http_exposes_real_transport_metadata() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            headers={"x-request-id": "provider-request-201"},
            json={
                "id": "body-response-201",
                "model": "claude-test",
                "content": [{"type": "text", "text": "answer"}],
            },
        )

    adapter = ProtocolHTTPProviderAdapter(
        provider="anthropic",
        base_url="https://api.anthropic.com/v1",
        protocol_family=ProtocolFamily.ANTHROPIC_MESSAGES,
        api_key="test-secret",
        http_transport=httpx.MockTransport(handler),
    )

    execution = adapter._generate_with_metadata(_request("claude-test"))

    assert isinstance(execution, ProviderExecutionResult)
    assert execution.metadata is not None
    assert execution.metadata.http_status == 201
    assert execution.metadata.provider_request_id == "provider-request-201"
    assert execution.metadata.latency_ms is not None
    assert execution.metadata.latency_ms >= 0.0
    assert execution.response.latency_ms == execution.metadata.latency_ms


def test_protocol_http_does_not_fabricate_provider_request_id() -> None:
    adapter = ProtocolHTTPProviderAdapter(
        provider="anthropic",
        base_url="https://api.anthropic.com/v1",
        protocol_family=ProtocolFamily.ANTHROPIC_MESSAGES,
        api_key="test-secret",
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "model": "claude-test",
                    "content": [{"type": "text", "text": "answer"}],
                },
            )
        ),
    )

    execution = adapter._generate_with_metadata(_request("claude-test"))

    assert execution.metadata is not None
    assert execution.metadata.http_status == 200
    assert execution.metadata.provider_request_id is None


def test_factory_supports_google_protocol_without_provider_specific_client() -> None:
    adapter = create_model_provider(
        environment={
            "CHAT_MODEL_PROVIDER": "google",
            "GOOGLE_API_KEY": "test-secret",
            "GOOGLE_MODEL": "gemini-test",
        },
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "modelVersion": "gemini-test",
                    "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
                },
            )
        ),
    )
    request = _request("gemini-test", provider="google")
    response = adapter.generate(request)
    assert response.provider == "google"
    assert response.content == "ok"


def test_openai_chat_payload_preserves_canonical_messages_tools_and_schema() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.read().decode("utf-8")))
        return httpx.Response(
            200,
            json={"id": "chat-1", "model": "gpt-test", "choices": [{"message": {"content": "ok"}}]},
        )

    adapter = ProtocolHTTPProviderAdapter(
        provider="openai-compatible",
        endpoint_id="endpoint-relay",
        base_url="https://relay.example/v1",
        protocol_family=ProtocolFamily.OPENAI_CHAT,
        api_key="test-secret",
        http_transport=httpx.MockTransport(handler),
    )
    material = _request("gpt-test", provider="openai-compatible").model_dump(mode="python")
    material.update(
        {
            "messages": (ModelMessage(role="user", content="hello"),),
            "tool_definitions": (("build", "Build the project"),),
            "structured_output_schema": (("status", "string"),),
            "fingerprint": "sha256:" + "0" * 64,
        }
    )
    response = adapter.generate(ModelRequest.model_validate(material))
    assert response.content == "ok"
    assert captured["messages"] == [{"role": "user", "content": "hello"}]
    assert captured["tools"] == [{"type": "function", "function": {"name": "build", "description": "Build the project"}}]
    assert captured["response_format"] == {"type": "json_object"}


def test_protocol_http_preserves_http_status_for_provider_error() -> None:
    adapter = ProtocolHTTPProviderAdapter(
        provider="zhipu",
        endpoint_id="endpoint-zhipu",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        protocol_family=ProtocolFamily.OPENAI_CHAT,
        api_key=SecretStr("test-secret"),
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(400, json={"error": "redacted"})
        ),
    )

    with pytest.raises(ModelProviderHTTPError) as raised:
        adapter.generate(_request("glm-4.7-flash", provider="zhipu"))

    assert raised.value.safe_http_status == 400


def test_protocol_http_preserves_connect_error_category() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("redacted", request=request)

    adapter = ProtocolHTTPProviderAdapter(
        provider="zhipu",
        endpoint_id="endpoint-zhipu",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        protocol_family=ProtocolFamily.OPENAI_CHAT,
        api_key=SecretStr("test-secret"),
        http_transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ModelProviderNetworkError) as raised:
        adapter.generate(_request("glm-4.7-flash", provider="zhipu"))

    assert raised.value.safe_transport_error_code == "CONNECT_ERROR"
    assert raised.value.safe_transport_stage == "TCP"


@pytest.mark.parametrize(
    ("cause", "expected_stage"),
    [(socket.gaierror(-2, "redacted"), "DNS"), (ssl.SSLError("redacted"), "TLS")],
)
def test_protocol_http_distinguishes_safe_connect_stages(
    cause: BaseException,
    expected_stage: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        error = httpx.ConnectError("redacted", request=request)
        error.__cause__ = cause
        raise error

    adapter = ProtocolHTTPProviderAdapter(
        provider="zhipu",
        endpoint_id="endpoint-zhipu",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        protocol_family=ProtocolFamily.OPENAI_CHAT,
        api_key=SecretStr("test-secret"),
        http_transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ModelProviderNetworkError) as raised:
        adapter.generate(_request("glm-4.7-flash", provider="zhipu"))

    assert raised.value.safe_transport_stage == expected_stage


def test_protocol_http_preserves_rate_limit_status() -> None:
    adapter = ProtocolHTTPProviderAdapter(
        provider="zhipu",
        endpoint_id="endpoint-zhipu",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        protocol_family=ProtocolFamily.OPENAI_CHAT,
        api_key=SecretStr("test-secret"),
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(429, json={"error": "redacted"})
        ),
    )

    with pytest.raises(ModelProviderRateLimited) as raised:
        adapter.generate(_request("glm-4.7-flash", provider="zhipu"))

    assert raised.value.safe_http_status == 429


@pytest.mark.parametrize("asynchronous", [False, True])
def test_responses_transport_serializes_policy_output_cap(asynchronous: bool) -> None:
    import asyncio

    sent: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        sent.append(payload)
        assert request.url.path.endswith("/responses")
        assert payload["model"] == "offline-response-model"
        assert payload["max_output_tokens"] == 17
        return httpx.Response(
            200,
            json={
                "id": "response-offline",
                "model": "offline-response-model",
                "output_text": "answer",
                "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
            },
        )

    transport = httpx.MockTransport(handler)
    adapter = ProtocolHTTPProviderAdapter(
        provider="offline-provider",
        base_url="https://offline.invalid/v1",
        protocol_family=ProtocolFamily.OPENAI_RESPONSES,
        api_key="offline-test-placeholder",
        http_transport=transport,
        async_transport=transport,
    )
    request = _request("offline-response-model", provider="offline-provider")
    request = request.model_copy(
        update={"model_policy": ModelPolicy(
            primary_provider="offline-provider", model_name="offline-response-model",
            max_output_tokens=17,
        ), "max_tokens": 17, "fingerprint": "sha256:" + "0" * 64}
    )
    response = asyncio.run(adapter.agenerate(request)) if asynchronous else adapter.generate(request)
    assert response.content == "answer"
    assert len(sent) == 1
