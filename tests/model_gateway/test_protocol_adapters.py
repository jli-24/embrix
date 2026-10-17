from __future__ import annotations

from embedded_copilot.model_gateway import ModelResponse
from embedded_copilot.model_gateway.protocols import (
    AnthropicMessagesProtocolAdapter,
    GoogleGenAIProtocolAdapter,
    LocalOpenAIProtocolAdapter,
    OpenAIChatProtocolAdapter,
    OpenAIResponsesProtocolAdapter,
)


def test_openai_responses_normalization_preserves_endpoint_identity() -> None:
    result = OpenAIResponsesProtocolAdapter().normalize(
        {
            "id": "response-1",
            "model": "gpt-relay",
            "output_text": "ok",
            "usage": {"input_tokens": 10, "output_tokens": 2},
        },
        provider_id="relay",
        endpoint_id="endpoint-relay",
        requested_model="gpt-relay",
    )
    assert result.provider_id == "relay"
    assert result.endpoint_id == "endpoint-relay"
    assert result.actual_model == "gpt-relay"
    assert result.usage is not None and result.usage.total_tokens == 12


def test_model_response_projection_preserves_request_and_endpoint_identity() -> None:
    response = ModelResponse(
        content="ok",
        model_name="gpt-relay",
        provider="relay",
        provenance=(),
        request_id="request-1",
        provider_id="relay",
        endpoint_id="endpoint-relay",
        requested_model="gpt-relay",
        selected_model="model-profile-relay",
        actual_model="gpt-4o-mini",
        remote_model_id="gpt-4o-mini",
        provider_response_id="response-1",
    )
    assert response.provider_id == "relay"
    assert response.endpoint_id == "endpoint-relay"
    assert response.requested_model == "gpt-relay"
    assert response.selected_model == "model-profile-relay"
    assert response.actual_model == "gpt-4o-mini"
    assert response.remote_model_id == "gpt-4o-mini"
    assert response.provider_response_id == "response-1"


def test_protocol_family_adapters_normalize_vendor_shapes() -> None:
    chat = OpenAIChatProtocolAdapter().normalize(
        {
            "id": "chat-1",
            "model": "chat-model",
            "choices": [{"message": {"content": "chat"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
        },
        provider_id="openai",
        endpoint_id="endpoint-openai",
        requested_model="chat-model",
    )
    anthropic = AnthropicMessagesProtocolAdapter().normalize(
        {
            "id": "msg-1",
            "model": "claude-test",
            "content": [{"type": "text", "text": "message"}],
            "usage": {"input_tokens": 5, "output_tokens": 6},
        },
        provider_id="anthropic",
        endpoint_id="endpoint-anthropic",
        requested_model="claude-test",
    )
    google = GoogleGenAIProtocolAdapter().normalize(
        {
            "modelVersion": "gemini-test",
            "candidates": [{"content": {"parts": [{"text": "gemini"}]}}],
            "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 8},
        },
        provider_id="google",
        endpoint_id="endpoint-google",
        requested_model="gemini-test",
    )
    local = LocalOpenAIProtocolAdapter().normalize(
        {
            "model": "local-model",
            "choices": [{"message": {"content": "local"}}],
        },
        provider_id="local",
        endpoint_id="endpoint-local",
        requested_model="local-model",
    )
    assert (chat.content, anthropic.content, google.content, local.content) == (
        "chat",
        "message",
        "gemini",
        "local",
    )
    assert chat.usage is not None and chat.usage.total_tokens == 7
    assert anthropic.usage is not None and anthropic.usage.total_tokens == 11
    assert google.usage is not None and google.usage.total_tokens == 15
