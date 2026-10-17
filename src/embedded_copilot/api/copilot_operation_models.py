from __future__ import annotations

from enum import StrEnum

from pydantic import field_validator

from embedded_copilot.desktop_copilot_chat.contracts import (
    FingerprintedChatContract,
    fingerprint,
    identifier,
    safe_text,
)


class CopilotOperationCapability(StrEnum):
    WORKSPACE_WRITE = "WORKSPACE_WRITE"
    BUILD = "BUILD"
    FLASH = "FLASH"
    HIL = "HIL"
    EDA_APPLY = "EDA_APPLY"
    REPAIR = "REPAIR"
    DEPLOY = "DEPLOY"
    EXECUTE = "EXECUTE"


class CopilotOperationStatus(StrEnum):
    PROPOSED = "PROPOSED"
    WAIT_HUMAN = "WAIT_HUMAN"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNAVAILABLE = "UNAVAILABLE"


class CopilotOperationCreateRequest(FingerprintedChatContract):
    """Safe operation identity; it intentionally has no command or path fields."""

    project_id: str
    target_id: str
    capability: CopilotOperationCapability
    proposal_fingerprint: str

    @field_validator("capability", mode="before")
    @classmethod
    def _capability(cls, value: object) -> CopilotOperationCapability:
        return CopilotOperationCapability(value) if isinstance(value, str) else value

    @field_validator("project_id", "target_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("proposal_fingerprint", mode="before")
    @classmethod
    def _proposal_fingerprint(cls, value: object) -> str:
        return fingerprint(value, field="proposal_fingerprint")


class CopilotOperationView(FingerprintedChatContract):
    operation_id: str
    project_id: str
    target_id: str
    capability: CopilotOperationCapability
    status: CopilotOperationStatus
    proposal_fingerprint: str
    reason_code: str
    detail: str
    approval_required: bool = True
    receipt_fingerprint: str | None = None

    @field_validator("capability", mode="before")
    @classmethod
    def _capability(cls, value: object) -> CopilotOperationCapability:
        return CopilotOperationCapability(value) if isinstance(value, str) else value

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> CopilotOperationStatus:
        return CopilotOperationStatus(value) if isinstance(value, str) else value

    @field_validator("operation_id", "project_id", "target_id", "reason_code", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("proposal_fingerprint", "receipt_fingerprint", mode="before")
    @classmethod
    def _fingerprints(cls, value: object, info) -> str | None:
        return None if value is None else fingerprint(value, field=info.field_name)

    @field_validator("detail", mode="before")
    @classmethod
    def _detail(cls, value: object) -> str:
        return safe_text(value, field="detail", maximum=256)


__all__ = (
    "CopilotOperationCapability",
    "CopilotOperationCreateRequest",
    "CopilotOperationStatus",
    "CopilotOperationView",
)
