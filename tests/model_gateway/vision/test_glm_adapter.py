from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.model_gateway.vision import VisionRequest
from embedded_copilot.model_gateway.vision.adapters import GLMVisionProviderAdapter
from embedded_copilot.model_gateway.vision.exceptions import (
    VisionAuthenticationError,
    VisionMalformedResponseError,
    VisionRateLimitError,
    VisionTimeoutError,
    VisionUnavailableError,
)


def _request() -> VisionRequest:
    return VisionRequest(
        request_id="vision-request-1",
        prompt="inspect board components",
        image_reference="artifact://image/001",
        context=EngineeringContextSnapshot.create(
            project_id="project-1",
            task_description="inspect board",
            memory_evidence=(),
            graph_evidence=(),
            knowledge_evidence=(),
            multimodal_evidence=(),
            conflicts=(),
            confidence=0.5,
            provenance=(),
            verification_status="UNVERIFIED",
        ),
        metadata=(("source", "camera-1"),),
    )


def _content(**overrides: object) -> str:
    observation: dict[str, object] = {
        "observation_id": "observation-glm-1",
        "objects": ["microcontroller"],
        "components": ["STM32"],
        "interfaces": ["UART"],
        "issues": ["possible_missing_decoupling"],
        "confidence": 0.85,
        "evidence": ["visible-label"],
    }
    observation.update(overrides)
    return json.dumps(observation)


def _response(content: str | None = None, **overrides: object) -> httpx.Response:
    choice: dict[str, object] = {
        "message": {"content": _content() if content is None else content},
        "finish_reason": "stop",
    }
    choice.update(overrides)
    return httpx.Response(200, json={"choices": [choice]})


def _adapter(
    handler: httpx.MockTransport | None = None,
    **overrides: object,
) -> GLMVisionProviderAdapter:
    return GLMVisionProviderAdapter(
        api_key="test-key",
        http_transport=handler,
        async_transport=handler,
        **overrides,
    )


def test_glm_adapter_maps_a_strict_vision_response_and_fixed_payload() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return _response()

    response = _adapter(httpx.MockTransport(handler)).generate(_request())

    assert (response.provider, response.model) == ("bigmodel", "glm-4.6v-flash")
    assert response.metadata == ()
    assert response.observation.components == ("STM32",)
    assert seen["url"] == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["model"] == "glm-4.6v-flash"
    assert "inspect board components" in body["messages"][1]["content"][0]["text"]
    assert body["messages"][1]["content"][1]["image_url"]["url"] == "artifact://image/001"
    assert "project-1" in body["messages"][1]["content"][0]["text"]
    assert "test-key" not in json.dumps(body)
    assert "api_key" not in json.dumps(body)
    assert "authorization" not in json.dumps(body).casefold()
    headers = seen["headers"]
    assert isinstance(headers, dict)
    assert headers["authorization"] == "Bearer test-key"


def test_glm_adapter_supports_async_and_model_binding_from_configuration() -> None:
    transport = httpx.MockTransport(lambda request: _response())
    adapter = _adapter(transport, model="glm-custom")

    response = asyncio.run(adapter.agenerate(_request()))

    assert (adapter.provider, adapter.model) == ("bigmodel", "glm-custom")
    assert (response.provider, response.model) == ("bigmodel", "glm-custom")
    with pytest.raises(AttributeError):
        adapter.model = "spoofed"  # type: ignore[misc]


def test_glm_adapter_ignores_spoofed_provider_and_model_from_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "provider": "spoofed",
                "model": "spoofed",
                "choices": [
                    {
                        "message": {"content": _content()},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    response = _adapter(httpx.MockTransport(handler)).generate(_request())

    assert (response.provider, response.model) == ("bigmodel", "glm-4.6v-flash")


@pytest.mark.parametrize("status", (401, 403))
def test_glm_adapter_maps_authentication_failures_without_secret_leak(
    status: int,
) -> None:
    adapter = _adapter(httpx.MockTransport(lambda request: httpx.Response(status)))

    with pytest.raises(VisionAuthenticationError) as raised:
        adapter.generate(_request())

    assert "test-key" not in str(raised.value)


def test_glm_adapter_maps_rate_limit_timeout_and_unavailable_failures() -> None:
    rate_limited = _adapter(
        httpx.MockTransport(lambda request: httpx.Response(429, json={"error": "x"}))
    )
    unavailable = _adapter(httpx.MockTransport(lambda request: httpx.Response(500)))

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("ignored", request=request)

    timed_out = _adapter(httpx.MockTransport(timeout))

    with pytest.raises(VisionRateLimitError):
        rate_limited.generate(_request())
    with pytest.raises(VisionUnavailableError):
        unavailable.generate(_request())
    with pytest.raises(VisionTimeoutError):
        timed_out.generate(_request())


@pytest.mark.parametrize(
    "response",
    (
        _response(content="not json"),
        _response(content=_content(objects=[1])),
        _response(content=_content(confidence=1)),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]}),
    ),
)
def test_glm_adapter_rejects_malformed_provider_output(response: httpx.Response) -> None:
    adapter = _adapter(httpx.MockTransport(lambda request: response))

    with pytest.raises(VisionMalformedResponseError):
        adapter.generate(_request())


def test_glm_adapter_uses_environment_only_for_allowed_configuration() -> None:
    transport = httpx.MockTransport(lambda request: _response())
    adapter = GLMVisionProviderAdapter(
        environment={"BIGMODEL_API_KEY": "test-key", "GLM_VISION_MODEL": "glm-env"},
        http_transport=transport,
        async_transport=transport,
    )

    assert adapter.model == "glm-env"
    assert adapter.generate(_request()).model == "glm-env"
    with pytest.raises(ValueError):
        GLMVisionProviderAdapter(environment={})


def test_glm_adapter_propagates_async_cancellation() -> None:
    class _CancelledTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            del request
            raise asyncio.CancelledError()

    adapter = GLMVisionProviderAdapter(
        api_key="test-key",
        async_transport=_CancelledTransport(),
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(adapter.agenerate(_request()))
