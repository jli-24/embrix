from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import field_validator, model_validator

from embedded_copilot.engineering_copilot.common import (
    Fingerprinted,
    finalize,
    fp,
    ident,
    text,
)


class ExecutionCapability(StrEnum):
    BUILD = "BUILD"
    FLASH = "FLASH"
    DEBUG_LOGS = "DEBUG_LOGS"


class ApprovalReference(Fingerprinted):
    approval_id: str
    project_id: str
    target_id: str
    capability: ExecutionCapability
    proposal_fingerprint: str
    expires_at: str

    @field_validator("approval_id", "project_id", "target_id", mode="before")
    @classmethod
    def _ids(cls, v, info):
        return ident(v, info.field_name)

    @field_validator("proposal_fingerprint", mode="before")
    @classmethod
    def _fp(cls, v):
        return fp(v, "proposal_fingerprint")

    @field_validator("expires_at", mode="before")
    @classmethod
    def _expiry(cls, v):
        value = text(v, "expires_at", 64)
        try:
            stamp = datetime.fromisoformat(value)
            if stamp.tzinfo is None or stamp.utcoffset() is None:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            raise ValueError("expires_at must be UTC") from None
        return stamp.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @classmethod
    def create(cls, **v):
        return finalize(cls, v)


class ExecutionRequest(Fingerprinted):
    request_id: str
    project_id: str
    target_id: str
    proposal_fingerprint: str
    capability: ExecutionCapability
    approval: ApprovalReference | None = None
    timeout_seconds: float = 30.0
    cancellation_token: str | None = None

    @field_validator("request_id", "project_id", "target_id", mode="before")
    @classmethod
    def _ids(cls, v, info):
        return ident(v, info.field_name)

    @field_validator("proposal_fingerprint", mode="before")
    @classmethod
    def _fp(cls, v):
        return fp(v, "proposal_fingerprint")

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def _timeout(cls, v):
        if type(v) not in (int, float) or v <= 0 or v > 3600:
            raise ValueError("timeout is invalid")
        return float(v)

    @field_validator("cancellation_token", mode="before")
    @classmethod
    def _cancel(cls, v):
        return None if v is None else ident(v, "cancellation_token")

    @model_validator(mode="after")
    def _approval_binding(self):
        if (
            self.capability in (ExecutionCapability.BUILD, ExecutionCapability.FLASH)
            and self.approval is None
        ):
            raise ValueError("approval required")
        if self.approval is not None and (
            self.approval.project_id != self.project_id
            or self.approval.target_id != self.target_id
            or self.approval.capability != self.capability
            or self.approval.proposal_fingerprint != self.proposal_fingerprint
        ):
            raise ValueError("approval binding mismatch")
        return self

    @classmethod
    def create(cls, **v):
        return finalize(cls, v)


class ExecutionResult(Fingerprinted):
    request_id: str
    capability: ExecutionCapability
    status: str
    summary: str
    observation_refs: tuple[str, ...] = ()

    @field_validator("request_id", mode="before")
    @classmethod
    def _id(cls, v):
        return ident(v, "request_id")

    @field_validator("status", "summary", mode="before")
    @classmethod
    def _text(cls, v, info):
        return text(v, info.field_name)

    @field_validator("observation_refs", mode="before")
    @classmethod
    def _refs(cls, v):
        if type(v) is not tuple:
            raise TypeError("observation_refs must be a tuple")
        return tuple(ident(x, "observation_ref") for x in v)

    @classmethod
    def create(cls, **v):
        return finalize(cls, v)


__all__ = (
    "ApprovalReference",
    "ExecutionCapability",
    "ExecutionRequest",
    "ExecutionResult",
)
