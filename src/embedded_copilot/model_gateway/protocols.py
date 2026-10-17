"""Protocol-family adapters that normalize provider payloads before the gateway."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from pydantic import field_validator

from .contracts import (
    ModelGatewayContract,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    safe_text,
)
from .profiles import ProtocolFamily


class ProtocolNormalizedResponse(ModelGatewayContract):
    provider_id: str
    endpoint_id: str
    model_name: str
    actual_model: str | None = None
    content: str
    tool_calls: tuple[tuple[str, str], ...] = ()
    usage: ModelUsage | None = None
    finish_reason: str = "STOP"
    provider_response_id: str | None = None

    @field_validator("provider_id", "endpoint_id", "model_name", "finish_reason", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return safe_text(value, field=info.field_name)

    @field_validator("actual_model", "provider_response_id", mode="before")
    @classmethod
    def _optional_text(cls, value: object, info) -> str | None:
        return None if value is None else safe_text(value, field=info.field_name)

    @field_validator("content", mode="before")
    @classmethod
    def _content(cls, value: object) -> str:
        return safe_text(value, field="content", maximum=16384, allow_newlines=True)

    @field_validator("tool_calls", mode="before")
    @classmethod
    def _tools(cls, value: object) -> tuple[tuple[str, str], ...]:
        if type(value) is not tuple:
            raise TypeError("tool_calls must be a tuple")
        return tuple(
            (safe_text(item[0], field="tool_call_name"), safe_text(item[1], field="tool_call_id"))
            for item in value
            if type(item) is tuple and len(item) == 2
        )

    def to_model_response(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content=self.content,
            model_name=self.model_name,
            provider=self.provider_id,
            usage=self.usage,
            finish_reason=self.finish_reason,
            provenance=request.engineering_context.provenance,
            request_id=request.request_id,
            provider_id=self.provider_id,
            endpoint_id=self.endpoint_id,
            requested_model=request.model_policy.model_name,
            selected_model=request.model_policy.model_name,
            actual_model=self.actual_model,
            remote_model_id=self.actual_model or self.model_name,
            tool_calls=self.tool_calls,
            provider_response_id=self.provider_response_id,
        )


@runtime_checkable
class ProtocolAdapter(Protocol):
    family: ProtocolFamily

    def normalize(
        self,
        payload: Mapping[str, object],
        *,
        provider_id: str,
        endpoint_id: str,
        requested_model: str,
    ) -> ProtocolNormalizedResponse: ...


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("provider response is invalid")
    return value


def _first(value: object) -> Mapping[str, object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
        raise ValueError("provider response is invalid")
    return _mapping(value[0])


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        parts: list[str] = []
        for item in value:
            if isinstance(item, Mapping) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def _usage(value: object, *, input_key: str, output_key: str, total_key: str = "total_tokens") -> ModelUsage | None:
    if not isinstance(value, Mapping):
        return None
    raw_input = value.get(input_key)
    raw_output = value.get(output_key)
    if type(raw_input) is not int or type(raw_output) is not int or raw_input < 0 or raw_output < 0:
        return None
    raw_total = value.get(total_key)
    total = raw_total if type(raw_total) is int and raw_total >= 0 else raw_input + raw_output
    if total != raw_input + raw_output:
        total = raw_input + raw_output
    return ModelUsage(input_tokens=raw_input, output_tokens=raw_output, total_tokens=total, usage_source="PROVIDER_REPORTED")


class OpenAIResponsesProtocolAdapter:
    family = ProtocolFamily.OPENAI_RESPONSES

    def normalize(self, payload: Mapping[str, object], *, provider_id: str, endpoint_id: str, requested_model: str) -> ProtocolNormalizedResponse:
        body = _mapping(payload)
        content = _text(body.get("output_text"))
        if not content and isinstance(body.get("output"), Sequence):
            content = _text(body.get("output"))
        return ProtocolNormalizedResponse(
            provider_id=provider_id,
            endpoint_id=endpoint_id,
            model_name=requested_model,
            actual_model=body.get("model") if isinstance(body.get("model"), str) else None,
            content=content,
            usage=_usage(body.get("usage"), input_key="input_tokens", output_key="output_tokens"),
            finish_reason=str(body.get("status", "STOP")),
            provider_response_id=body.get("id") if isinstance(body.get("id"), str) else None,
        )


class OpenAIChatProtocolAdapter:
    family = ProtocolFamily.OPENAI_CHAT

    def normalize(self, payload: Mapping[str, object], *, provider_id: str, endpoint_id: str, requested_model: str) -> ProtocolNormalizedResponse:
        body = _mapping(payload)
        choice = _first(body.get("choices"))
        message = _mapping(choice.get("message"))
        content = _text(message.get("content"))
        tool_calls: list[tuple[str, str]] = []
        raw_tools = message.get("tool_calls")
        if isinstance(raw_tools, Sequence) and not isinstance(raw_tools, (str, bytes)):
            for item in raw_tools:
                if isinstance(item, Mapping):
                    call_id = item.get("id")
                    function = item.get("function")
                    name = function.get("name") if isinstance(function, Mapping) else None
                    if isinstance(name, str) and isinstance(call_id, str):
                        tool_calls.append((name, call_id))
        return ProtocolNormalizedResponse(
            provider_id=provider_id,
            endpoint_id=endpoint_id,
            model_name=requested_model,
            actual_model=body.get("model") if isinstance(body.get("model"), str) else None,
            content=content or "tool call",
            tool_calls=tuple(tool_calls),
            usage=_usage(body.get("usage"), input_key="prompt_tokens", output_key="completion_tokens"),
            finish_reason=str(choice.get("finish_reason", "STOP")),
            provider_response_id=body.get("id") if isinstance(body.get("id"), str) else None,
        )


class AnthropicMessagesProtocolAdapter:
    family = ProtocolFamily.ANTHROPIC_MESSAGES

    def normalize(self, payload: Mapping[str, object], *, provider_id: str, endpoint_id: str, requested_model: str) -> ProtocolNormalizedResponse:
        body = _mapping(payload)
        content = _text(body.get("content"))
        return ProtocolNormalizedResponse(
            provider_id=provider_id,
            endpoint_id=endpoint_id,
            model_name=requested_model,
            actual_model=body.get("model") if isinstance(body.get("model"), str) else None,
            content=content,
            usage=_usage(body.get("usage"), input_key="input_tokens", output_key="output_tokens"),
            finish_reason=str(body.get("stop_reason", "STOP")),
            provider_response_id=body.get("id") if isinstance(body.get("id"), str) else None,
        )


class GoogleGenAIProtocolAdapter:
    family = ProtocolFamily.GOOGLE_GENAI

    def normalize(self, payload: Mapping[str, object], *, provider_id: str, endpoint_id: str, requested_model: str) -> ProtocolNormalizedResponse:
        body = _mapping(payload)
        candidate = _first(body.get("candidates"))
        content = _text(_mapping(candidate.get("content")).get("parts"))
        usage_data = body.get("usageMetadata")
        usage = _usage(usage_data, input_key="promptTokenCount", output_key="candidatesTokenCount", total_key="totalTokenCount")
        return ProtocolNormalizedResponse(
            provider_id=provider_id,
            endpoint_id=endpoint_id,
            model_name=requested_model,
            actual_model=body.get("modelVersion") if isinstance(body.get("modelVersion"), str) else None,
            content=content,
            usage=usage,
            finish_reason=str(candidate.get("finishReason", "STOP")),
        )


class LocalOpenAIProtocolAdapter(OpenAIChatProtocolAdapter):
    family = ProtocolFamily.LOCAL_OPENAI_COMPATIBLE


__all__ = (
    "AnthropicMessagesProtocolAdapter",
    "GoogleGenAIProtocolAdapter",
    "LocalOpenAIProtocolAdapter",
    "OpenAIChatProtocolAdapter",
    "OpenAIResponsesProtocolAdapter",
    "ProtocolAdapter",
    "ProtocolNormalizedResponse",
)
