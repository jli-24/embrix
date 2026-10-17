from __future__ import annotations

from enum import StrEnum

from pydantic import field_validator

from embedded_copilot.desktop_copilot_chat.contracts import (
    FingerprintedChatContract,
    identifier,
    safe_text,
)


class CapabilityStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"
    UNVERIFIED = "UNVERIFIED"


class CapabilityStatusView(FingerprintedChatContract):
    """Safe, read-only capability projection for the Project-first Desktop."""

    capability: str
    status: CapabilityStatus
    reason_code: str
    detail: str
    provider_configured: str | None = None
    runtime_status: str | None = None
    model_status: str | None = None

    @field_validator("capability", "reason_code", mode="before")
    @classmethod
    def _identifiers(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("detail", mode="before")
    @classmethod
    def _detail(cls, value: object) -> str:
        return safe_text(value, field="detail", maximum=256)


__all__ = ("CapabilityStatus", "CapabilityStatusView")
