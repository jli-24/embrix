"""Typed retry/fallback policy decisions and normalized provider errors."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, field_validator

from .contracts import ModelGatewayContract, identifier


class ModelErrorCode(StrEnum):
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    INVALID_REQUEST = "INVALID_REQUEST"
    CONTEXT_LIMIT_EXCEEDED = "CONTEXT_LIMIT_EXCEEDED"
    CONTENT_REJECTED = "CONTENT_REJECTED"
    TIMEOUT = "TIMEOUT"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"


class ProviderHealth(StrEnum):
    AVAILABLE = "AVAILABLE"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    AUTH_FAILED = "AUTH_FAILED"
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    UNKNOWN = "UNKNOWN"


class RetryPolicy(ModelGatewayContract):
    max_attempts: int = Field(default=2, ge=1, le=5)
    retryable_errors: tuple[ModelErrorCode, ...] = (
        ModelErrorCode.TIMEOUT,
        ModelErrorCode.RATE_LIMITED,
        ModelErrorCode.PROVIDER_UNAVAILABLE,
    )

    def should_retry(self, error: ModelErrorCode, attempt: int) -> bool:
        return error in self.retryable_errors and attempt + 1 < self.max_attempts


class FallbackTrace(ModelGatewayContract):
    requested_model: str
    selected_model: str
    actual_model: str | None = None
    fallback_from: str | None = None
    fallback_to: str | None = None
    fallback_reason: ModelErrorCode | None = None
    retry_count: int = Field(default=0, ge=0)
    fallback_used: bool = False

    @field_validator("requested_model", "selected_model", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("actual_model", "fallback_from", "fallback_to", mode="before")
    @classmethod
    def _optional_ids(cls, value: object, info) -> str | None:
        return None if value is None else identifier(value, field=info.field_name)

    def with_fallback(self, *, to_model: str, reason: ModelErrorCode) -> FallbackTrace:
        return self.model_copy(
            update={
                "fallback_from": self.selected_model,
                "fallback_to": to_model,
                "fallback_reason": reason,
                "fallback_used": True,
                "selected_model": to_model,
            }
        )


__all__ = ("FallbackTrace", "ModelErrorCode", "ProviderHealth", "RetryPolicy")
