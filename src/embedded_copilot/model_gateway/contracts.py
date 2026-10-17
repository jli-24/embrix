from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from embedded_copilot.engineering_context import (
    ContextSourceReference,
    EngineeringContextSnapshot,
)

_IDENTIFIER: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_FINGERPRINT: Final = re.compile(r"^sha256:[a-f0-9]{64}$")
_UNSAFE: Final = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\|file://|\x00|"
    r"(?:api[_ -]?key|access[_ -]?token|bearer|password|credential|secret|token)\s*[:=])",
    re.IGNORECASE,
)
_ALLOWED_CONTEXT_REFERENCE: Final = re.compile(
    r"(?:artifact|s3|workspace)://[A-Za-z0-9][A-Za-z0-9._~:/+-]*|"
    r"(?:graph|knowledge|memory|retrieval):[A-Za-z0-9][A-Za-z0-9._:/+-]*",
    re.IGNORECASE,
)
_FORBIDDEN_CONTEXT_URI: Final = re.compile(
    r"(?:https?|file):/{2}|data:", re.IGNORECASE
)
_SENSITIVE_CONTEXT_TEXT: Final = re.compile(
    r"\b(?:api[_ -]?key|access[_ -]?token|bearer|credential|password|secret|"
    r"token|base64)\b",
    re.IGNORECASE,
)
_UNIX_PATH: Final = re.compile(
    r"(?<![A-Za-z0-9])/(?:home|tmp|etc|var|usr|opt|private)(?:/|$)",
    re.IGNORECASE,
)
_ATTACHMENT_REFERENCE: Final = re.compile(
    r"(?:artifact|workspace)://[A-Za-z0-9][A-Za-z0-9._~:/+-]*",
    re.IGNORECASE,
)
_IMAGE_REFERENCE: Final = re.compile(
    r"(?:artifact|workspace|https)://[A-Za-z0-9][A-Za-z0-9._~:/?&=%+#-]*",
    re.IGNORECASE,
)
_IMAGE_MIME_TYPES: Final = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif"}
)
_MAX_IMAGE_BYTES: Final = 10 * 1024 * 1024
_MAX_IMAGES_PER_REQUEST: Final = 8
_AUTO_FINGERPRINT: Final = "sha256:" + "0" * 64


class ModelGatewayContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
        validate_default=True,
    )


class ProviderCapability(StrEnum):
    """Provider features reserved for future typed inference requests."""

    TEXT = "text"
    VISION = "vision"


def safe_text(
    value: object, *, field: str, maximum: int = 4096, allow_newlines: bool = False
) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = unicodedata.normalize("NFC", value).strip()
    if not checked or len(checked) > maximum or _UNSAFE.search(checked):
        raise ValueError(f"{field} is unsafe")
    if not allow_newlines and ("\n" in checked or "\r" in checked):
        raise ValueError(f"{field} is unsafe")
    return checked


def _system_instruction_text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("system_instruction is invalid")
    checked = unicodedata.normalize("NFC", value).strip()
    if not checked or len(checked) > 16384 or "\n" in checked or "\r" in checked:
        raise ValueError("system_instruction is unsafe")

    def redact_reference(match: re.Match[str]) -> str:
        reference_value = match.group(0)
        resource = (
            reference_value.split("://", maxsplit=1)[1]
            if "://" in reference_value
            else reference_value.split(":", maxsplit=1)[1]
        )
        return reference_value if _SENSITIVE_CONTEXT_TEXT.search(resource) else ""

    remainder = _ALLOWED_CONTEXT_REFERENCE.sub(redact_reference, checked)
    if (
        _UNSAFE.search(remainder)
        or _FORBIDDEN_CONTEXT_URI.search(remainder)
        or _SENSITIVE_CONTEXT_TEXT.search(remainder)
        or _UNIX_PATH.search(remainder)
    ):
        raise ValueError("system_instruction is unsafe")
    return checked


def identifier(value: object, *, field: str) -> str:
    checked = safe_text(value, field=field, maximum=128)
    if not _IDENTIFIER.fullmatch(checked):
        raise ValueError(f"{field} is invalid")
    return checked


def tuple_only(value: object, *, field: str) -> tuple[object, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{field} must be a tuple")
    return copy.deepcopy(value)


def confidence(value: object, *, field: str = "confidence") -> float:
    if type(value) is not float or not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{field} is invalid")
    return value


def fingerprint(value: object, *, field: str = "fingerprint") -> str:
    if not isinstance(value, str) or not _FINGERPRINT.fullmatch(value):
        raise ValueError(f"{field} is invalid")
    return value


def canonical_fingerprint(value: BaseModel, *, exclude: set[str]) -> str:
    payload = value.model_dump(mode="json", exclude=exclude)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class ModelPolicy(ModelGatewayContract):
    primary_provider: str
    fallback_providers: tuple[str, ...] = ()
    model_name: str
    temperature: float = 0.0
    max_output_tokens: int = Field(default=1024, ge=1, le=32768)
    service_tier: Literal["default"] | None = None
    fingerprint: str = _AUTO_FINGERPRINT

    @field_validator("primary_provider", mode="before")
    @classmethod
    def validate_primary_provider(cls, value: object) -> str:
        return identifier(value, field="primary_provider")

    @field_validator("fallback_providers", mode="before")
    @classmethod
    def validate_fallback_providers(cls, value: object) -> tuple[str, ...]:
        return tuple(
            identifier(item, field="fallback_provider")
            for item in tuple_only(value, field="fallback_providers")
        )

    @field_validator("model_name", mode="before")
    @classmethod
    def validate_model_name(cls, value: object) -> str:
        return identifier(value, field="model_name")

    @field_validator("temperature", mode="before")
    @classmethod
    def validate_temperature(cls, value: object) -> float:
        if (
            type(value) is not float
            or not math.isfinite(value)
            or not 0.0 <= value <= 2.0
        ):
            raise ValueError("temperature is invalid")
        return value

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @model_validator(mode="after")
    def verify(self) -> ModelPolicy:
        if self.primary_provider in self.fallback_providers:
            raise ValueError("fallback providers must not contain primary provider")
        if len(self.fallback_providers) != len(set(self.fallback_providers)):
            raise ValueError("fallback providers must be unique")
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO_FINGERPRINT:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("model policy fingerprint mismatch")
        return self

    @model_serializer(mode="wrap")
    def _legacy_service_tier(self, handler):
        value = handler(self)
        if self.service_tier is None:
            value.pop("service_tier", None)
        return value


class ModelRequestMetadata(ModelGatewayContract):
    """Caller-supplied correlation metadata without business or prompt history."""

    request_id: str
    trace_id: str
    created_at: datetime
    fingerprint: str = _AUTO_FINGERPRINT

    @field_validator("request_id", "trace_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("created_at", mode="before")
    @classmethod
    def validate_created_at(cls, value: object) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("created_at is invalid")
        return value.astimezone(UTC)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @model_validator(mode="after")
    def verify(self) -> ModelRequestMetadata:
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO_FINGERPRINT:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("model request metadata fingerprint mismatch")
        return self


class ModelMessage(ModelGatewayContract):
    """A bounded, provider-neutral message used by the canonical request."""

    role: str
    content: str

    @field_validator("role", mode="before")
    @classmethod
    def validate_role(cls, value: object) -> str:
        return safe_text(value, field="message_role", maximum=32)

    @field_validator("content", mode="before")
    @classmethod
    def validate_content(cls, value: object) -> str:
        return safe_text(value, field="message_content", maximum=16384, allow_newlines=True)


class ModelImageInput(ModelGatewayContract):
    """A bounded image reference carried by the canonical model request."""

    content_ref: str
    mime_type: str
    size_bytes: int | None = Field(default=None, ge=1, le=_MAX_IMAGE_BYTES)

    @field_validator("content_ref", mode="before")
    @classmethod
    def validate_content_ref(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("image content reference is invalid")
        checked = unicodedata.normalize("NFC", value).strip()
        resource = checked.split("://", 1)[1] if "://" in checked else checked
        if (
            len(checked) > 1024
            or _IMAGE_REFERENCE.fullmatch(checked) is None
            or "@" in checked
            or safe_text(resource, field="image_content_ref", maximum=1024) != resource
        ):
            raise ValueError("image content reference is invalid")
        return checked

    @field_validator("mime_type", mode="before")
    @classmethod
    def validate_mime_type(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("image mime type is invalid")
        checked = value.strip().casefold()
        if checked not in _IMAGE_MIME_TYPES:
            raise ValueError("image mime type is unsupported")
        return checked


class ModelRequest(ModelGatewayContract):
    request_id: str
    task: str
    engineering_context: EngineeringContextSnapshot
    system_instruction: str
    model_policy: ModelPolicy
    constraints: tuple[str, ...] = ()
    prompt: str | None = None
    system_prompt: str | None = None
    context: EngineeringContextSnapshot | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    metadata: ModelRequestMetadata | None = None
    project_id: str | None = None
    conversation_id: str | None = None
    endpoint_id: str | None = None
    credential_ref: str | None = None
    task_type: str | None = None
    messages: tuple[ModelMessage, ...] = ()
    attachments: tuple[str, ...] = ()
    images: tuple[ModelImageInput, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    preferred_model: str | None = None
    preferred_provider: str | None = None
    routing_strategy: str | None = None
    tool_definitions: tuple[tuple[str, str], ...] = ()
    structured_output_schema: tuple[tuple[str, str], ...] | None = None
    fingerprint: str = _AUTO_FINGERPRINT

    @field_validator("request_id", mode="before")
    @classmethod
    def validate_request_id(cls, value: object) -> str:
        return identifier(value, field="request_id")

    @field_validator("task", mode="before")
    @classmethod
    def validate_task(cls, value: object) -> str:
        return safe_text(value, field="task", maximum=1024)

    @field_validator("engineering_context", mode="before")
    @classmethod
    def validate_context(cls, value: object) -> EngineeringContextSnapshot:
        return EngineeringContextSnapshot.model_validate(copy.deepcopy(value))

    @field_validator("system_instruction", mode="before")
    @classmethod
    def validate_system_instruction(cls, value: object) -> str:
        return _system_instruction_text(value)

    @field_validator("constraints", mode="before")
    @classmethod
    def validate_constraints(cls, value: object) -> tuple[str, ...]:
        return tuple(
            safe_text(item, field="constraint", maximum=256)
            for item in tuple_only(value, field="constraints")
        )

    @field_validator("model_policy", mode="before")
    @classmethod
    def validate_policy(cls, value: object) -> ModelPolicy:
        return ModelPolicy.model_validate(copy.deepcopy(value))

    @field_validator("prompt", mode="before")
    @classmethod
    def validate_prompt(cls, value: object) -> str | None:
        return None if value is None else safe_text(value, field="prompt", maximum=1024)

    @field_validator("system_prompt", mode="before")
    @classmethod
    def validate_system_prompt(cls, value: object) -> str | None:
        return (
            None
            if value is None
            else safe_text(value, field="system_prompt", maximum=16384)
        )

    @field_validator("context", mode="before")
    @classmethod
    def validate_provider_context(
        cls, value: object
    ) -> EngineeringContextSnapshot | None:
        return (
            None
            if value is None
            else EngineeringContextSnapshot.model_validate(copy.deepcopy(value))
        )

    @field_validator("temperature", mode="before")
    @classmethod
    def validate_provider_temperature(cls, value: object) -> float | None:
        if value is None:
            return None
        if (
            type(value) is not float
            or not math.isfinite(value)
            or not 0.0 <= value <= 2.0
        ):
            raise ValueError("temperature is invalid")
        return value

    @field_validator("max_tokens", mode="before")
    @classmethod
    def validate_provider_max_tokens(cls, value: object) -> int | None:
        if value is None:
            return None
        if type(value) is not int or not 1 <= value <= 32768:
            raise ValueError("max_tokens is invalid")
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, value: object) -> ModelRequestMetadata | None:
        return (
            None
            if value is None
            else ModelRequestMetadata.model_validate(copy.deepcopy(value))
        )

    @field_validator(
        "project_id",
        "conversation_id",
        "endpoint_id",
        "preferred_model",
        "preferred_provider",
        mode="before",
    )
    @classmethod
    def validate_optional_ids(cls, value: object, info) -> str | None:
        return None if value is None else identifier(value, field=info.field_name)

    @field_validator("credential_ref", mode="before")
    @classmethod
    def validate_credential_ref(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not re.fullmatch(
            r"(?:credential|vault|secret):[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value.strip()
        ):
            raise ValueError("credential_ref is invalid")
        return value.strip()

    @field_validator("task_type", "routing_strategy", mode="before")
    @classmethod
    def validate_optional_text(cls, value: object, info) -> str | None:
        return None if value is None else safe_text(value, field=info.field_name, maximum=64)

    @field_validator("messages", mode="before")
    @classmethod
    def validate_messages(cls, value: object) -> tuple[ModelMessage, ...]:
        if type(value) is not tuple:
            raise TypeError("messages must be a tuple")
        return tuple(
            item if type(item) is ModelMessage else ModelMessage.model_validate(copy.deepcopy(item))
            for item in value
        )

    @field_validator("attachments", mode="before")
    @classmethod
    def validate_attachments(cls, value: object) -> tuple[str, ...]:
        if type(value) is not tuple:
            raise TypeError("attachments must be a tuple")
        result: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise TypeError("attachment reference is invalid")
            checked = unicodedata.normalize("NFC", item).strip()
            if (
                len(checked) > 512
                or _ATTACHMENT_REFERENCE.fullmatch(checked) is None
                or safe_text(checked.split("://", 1)[1], field="attachment", maximum=480)
                != checked.split("://", 1)[1]
            ):
                raise ValueError("attachment reference is invalid")
            result.append(checked)
        return tuple(result)

    @field_validator("images", mode="before")
    @classmethod
    def validate_images(cls, value: object) -> tuple[ModelImageInput, ...]:
        if type(value) is not tuple:
            raise TypeError("images must be a tuple")
        if len(value) > _MAX_IMAGES_PER_REQUEST:
            raise ValueError("too many images")
        return tuple(
            item
            if type(item) is ModelImageInput
            else ModelImageInput.model_validate(copy.deepcopy(item))
            for item in value
        )

    @field_validator("required_capabilities", mode="before")
    @classmethod
    def validate_required_capabilities(cls, value: object) -> tuple[str, ...]:
        if type(value) is not tuple:
            raise TypeError("required_capabilities must be a tuple")
        return tuple(safe_text(item, field="required_capability", maximum=64) for item in value)

    @field_validator("tool_definitions", "structured_output_schema", mode="before")
    @classmethod
    def validate_contract_pairs(cls, value: object, info) -> tuple[tuple[str, str], ...] | None:
        if value is None and info.field_name == "structured_output_schema":
            return None
        if type(value) is not tuple:
            raise TypeError(f"{info.field_name} must be a tuple")
        result: list[tuple[str, str]] = []
        for item in value:
            if type(item) is not tuple or len(item) != 2:
                raise TypeError(f"{info.field_name} contains an invalid entry")
            result.append(
                (
                    safe_text(item[0], field=f"{info.field_name}.name", maximum=128),
                    safe_text(item[1], field=f"{info.field_name}.value", maximum=4096, allow_newlines=True),
                )
            )
        return tuple(result)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @model_validator(mode="after")
    def verify(self) -> ModelRequest:
        if self.project_id is not None and self.project_id != self.engineering_context.project_id:
            raise ValueError("project request binding mismatch")
        if self.prompt is None:
            object.__setattr__(self, "prompt", self.task)
        elif self.prompt != self.task:
            raise ValueError("prompt must match task")
        if self.system_prompt is None:
            object.__setattr__(self, "system_prompt", self.system_instruction)
        elif self.system_prompt != self.system_instruction:
            raise ValueError("system_prompt must match system_instruction")
        if self.context is None:
            object.__setattr__(self, "context", self.engineering_context)
        elif self.context != self.engineering_context:
            raise ValueError("context must match engineering_context")
        if self.temperature is None:
            object.__setattr__(self, "temperature", self.model_policy.temperature)
        elif self.temperature != self.model_policy.temperature:
            raise ValueError("temperature must match model policy")
        if self.max_tokens is None:
            object.__setattr__(self, "max_tokens", self.model_policy.max_output_tokens)
        elif self.max_tokens != self.model_policy.max_output_tokens:
            raise ValueError("max_tokens must match model policy")
        if self.metadata is not None and self.metadata.request_id != self.request_id:
            raise ValueError("request metadata binding mismatch")
        if self.images and "VISION" not in {item.upper() for item in self.required_capabilities}:
            object.__setattr__(
                self,
                "required_capabilities",
                self.required_capabilities + ("VISION",),
            )
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO_FINGERPRINT:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("model request fingerprint mismatch")
        return self


class ModelUsage(ModelGatewayContract):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    provider_reported_cost: float | None = Field(default=None, ge=0.0)
    estimated_cost: float | None = Field(default=None, ge=0.0)
    currency: str | None = None
    billing_class: str | None = None
    usage_source: str = "UNKNOWN"
    fingerprint: str = _AUTO_FINGERPRINT

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @model_validator(mode="after")
    def verify(self) -> ModelUsage:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total token count is invalid")
        if self.cached_input_tokens is not None and self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached input token count is invalid")
        if self.reasoning_tokens is not None and self.reasoning_tokens > self.output_tokens:
            raise ValueError("reasoning token count is invalid")
        if self.currency is not None:
            safe_text(self.currency, field="currency", maximum=16)
        safe_text(self.billing_class or "UNKNOWN", field="billing_class", maximum=32)
        safe_text(self.usage_source, field="usage_source", maximum=32)
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO_FINGERPRINT:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("model usage fingerprint mismatch")
        return self


class ModelRequirementProposal(ModelGatewayContract):
    category: str
    value: str
    rationale: str | None = None

    @field_validator("category", "value", mode="before")
    @classmethod
    def validate_text_fields(cls, value: object, info) -> str:
        return safe_text(value, field=info.field_name, maximum=256)

    @field_validator("rationale", mode="before")
    @classmethod
    def validate_rationale(cls, value: object) -> str | None:
        return None if value is None else safe_text(value, field="rationale", maximum=512)


class ModelOpenQuestion(ModelGatewayContract):
    category: str
    question: str

    @field_validator("category", "question", mode="before")
    @classmethod
    def validate_text_fields(cls, value: object, info) -> str:
        return safe_text(value, field=info.field_name, maximum=1024)


class ModelRoutingRecord(ModelGatewayContract):
    """Content-free execution identity carried with a normalized response."""

    requested_model: str
    selected_model: str
    actual_model: str | None = None
    remote_model_id: str | None = None
    provider_id: str
    endpoint_id: str
    retry_count: int = Field(default=0, ge=0)
    fallback_used: bool = False
    fallback_reason: str | None = None

    @field_validator("requested_model", "selected_model", "provider_id", "endpoint_id", mode="before")
    @classmethod
    def validate_identity(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("actual_model", "remote_model_id", mode="before")
    @classmethod
    def validate_optional_identity(cls, value: object, info) -> str | None:
        return None if value is None else identifier(value, field=info.field_name)

    @field_validator("fallback_reason", mode="before")
    @classmethod
    def validate_fallback_reason(cls, value: object) -> str | None:
        return None if value is None else safe_text(value, field="fallback_reason", maximum=128)


class ModelResponse(ModelGatewayContract):
    response_id: str = ""
    content: str
    model_name: str
    provider: str
    usage: ModelUsage | None = None
    finish_reason: str = "STOP"
    confidence: float | None = None
    provenance: tuple[ContextSourceReference, ...]
    request_id: str | None = None
    provider_id: str | None = None
    endpoint_id: str | None = None
    credential_ref: str | None = None
    requested_model: str | None = None
    selected_model: str | None = None
    actual_model: str | None = None
    remote_model_id: str | None = None
    tool_calls: tuple[tuple[str, str], ...] = ()
    structured_output: str | None = None
    latency_ms: float | None = Field(default=None, ge=0.0)
    routing_record: ModelRoutingRecord | None = None
    provider_response_id: str | None = None
    requirement_proposals: tuple[ModelRequirementProposal, ...] = ()
    open_questions: tuple[ModelOpenQuestion, ...] = ()
    fingerprint: str = _AUTO_FINGERPRINT

    @field_validator("response_id", mode="before")
    @classmethod
    def validate_response_id(cls, value: object) -> str:
        return "" if value == "" else identifier(value, field="response_id")

    @field_validator("content", mode="before")
    @classmethod
    def validate_content(cls, value: object) -> str:
        return safe_text(value, field="content", maximum=16384, allow_newlines=True)

    @field_validator("model_name", "provider", "finish_reason", mode="before")
    @classmethod
    def validate_text_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator(
        "request_id",
        "provider_id",
        "endpoint_id",
        "requested_model",
        "selected_model",
        "actual_model",
        "remote_model_id",
        "provider_response_id",
        mode="before",
    )
    @classmethod
    def validate_optional_identities(cls, value: object, info) -> str | None:
        return None if value is None else identifier(value, field=info.field_name)

    @field_validator("credential_ref", mode="before")
    @classmethod
    def validate_credential_ref(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not re.fullmatch(
            r"(?:credential|vault|secret):[A-Za-z0-9][A-Za-z0-9._:-]{0,127}",
            value.strip(),
        ):
            raise ValueError("credential_ref is invalid")
        return value.strip()

    @field_validator("tool_calls", mode="before")
    @classmethod
    def validate_tool_calls(cls, value: object) -> tuple[tuple[str, str], ...]:
        if type(value) is not tuple:
            raise TypeError("tool_calls must be a tuple")
        result: list[tuple[str, str]] = []
        for item in value:
            if type(item) is not tuple or len(item) != 2:
                raise TypeError("tool_calls contains an invalid entry")
            result.append(
                (
                    safe_text(item[0], field="tool_call_name", maximum=128),
                    identifier(item[1], field="tool_call_id"),
                )
            )
        return tuple(result)

    @field_validator("routing_record", mode="before")
    @classmethod
    def validate_routing_record(cls, value: object) -> ModelRoutingRecord | None:
        return None if value is None else ModelRoutingRecord.model_validate(copy.deepcopy(value))

    @field_validator("usage", mode="before")
    @classmethod
    def validate_usage(cls, value: object) -> ModelUsage | None:
        return (
            None if value is None else ModelUsage.model_validate(copy.deepcopy(value))
        )

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_confidence(cls, value: object) -> float | None:
        return None if value is None else confidence(value)

    @field_validator("provenance", mode="before")
    @classmethod
    def validate_provenance(cls, value: object) -> tuple[ContextSourceReference, ...]:
        return tuple(
            ContextSourceReference.model_validate(copy.deepcopy(item))
            for item in tuple_only(value, field="provenance")
        )

    @field_validator("requirement_proposals", "open_questions", mode="before")
    @classmethod
    def validate_requirement_fields(cls, value: object, info):
        if type(value) is not tuple:
            raise TypeError(f"{info.field_name} must be a tuple")
        return tuple(copy.deepcopy(value))

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @model_validator(mode="after")
    def verify(self) -> ModelResponse:
        material = self.model_dump(mode="python")
        material.pop("response_id")
        material.pop("fingerprint")
        derived_id = (
            "response-"
            + hashlib.sha256(
                json.dumps(
                    material,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        )
        if not self.response_id:
            object.__setattr__(self, "response_id", derived_id)
        elif self.response_id != derived_id:
            raise ValueError("model response id mismatch")
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO_FINGERPRINT:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("model response fingerprint mismatch")
        return self


def validate_model_request(value: object) -> ModelRequest:
    if type(value) is not ModelRequest:
        raise TypeError("model request is invalid")
    material = copy.deepcopy(value.model_dump(mode="python"))
    if material.get("system_prompt") == material.get("system_instruction"):
        material["system_prompt"] = None
    return ModelRequest.model_validate(material)


def validate_model_response(value: object) -> ModelResponse:
    if type(value) is not ModelResponse:
        raise TypeError("model response is invalid")
    return ModelResponse.model_validate(copy.deepcopy(value.model_dump(mode="python")))


__all__ = (
    "ModelGatewayContract",
    "ModelImageInput",
    "ModelMessage",
    "ModelOpenQuestion",
    "ModelPolicy",
    "ModelRequest",
    "ModelRequestMetadata",
    "ModelRequirementProposal",
    "ModelResponse",
    "ModelRoutingRecord",
    "ModelUsage",
    "ProviderCapability",
    "canonical_fingerprint",
    "confidence",
    "identifier",
    "safe_text",
    "tuple_only",
    "validate_model_request",
    "validate_model_response",
)
