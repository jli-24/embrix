from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import field_validator

from embedded_copilot.desktop_copilot_chat.contracts import (
    FingerprintedChatContract,
    fingerprint,
    identifier,
    safe_text,
)


class CopilotReviewTargetType(StrEnum):
    KNOWLEDGE = "KNOWLEDGE"
    VALIDATION = "VALIDATION"
    OPTIMIZATION = "OPTIMIZATION"
    EVOLUTION = "EVOLUTION"
    MEMORY_CANDIDATE = "MEMORY_CANDIDATE"
    KNOWLEDGE_CANDIDATE = "KNOWLEDGE_CANDIDATE"
    KNOWLEDGE_EVOLUTION_PROPOSAL = "KNOWLEDGE_EVOLUTION_PROPOSAL"
    REPAIR_PROPOSAL = "REPAIR_PROPOSAL"
    OPTIMIZATION_PROPOSAL = "OPTIMIZATION_PROPOSAL"
    ENGINEERING_PROPOSAL = "ENGINEERING_PROPOSAL"
    ENGINEERING_EVIDENCE = "ENGINEERING_EVIDENCE"


class CopilotReviewDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_CHANGE = "REQUEST_CHANGE"


def _utc(value: object, *, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"{field} is invalid") from error
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


class CopilotReviewCreateRequest(FingerprintedChatContract):
    project_id: str
    target_type: CopilotReviewTargetType
    target_id: str
    target_fingerprint: str

    @field_validator("project_id", "target_id", "target_fingerprint", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        if info.field_name == "target_fingerprint":
            return fingerprint(value, field=info.field_name)
        return identifier(value, field=info.field_name)

    @field_validator("target_type", mode="before")
    @classmethod
    def _target_type(cls, value: object) -> CopilotReviewTargetType:
        return CopilotReviewTargetType(value) if isinstance(value, str) else value


class CopilotReviewDecisionRequest(FingerprintedChatContract):
    project_id: str
    decision: CopilotReviewDecision
    reviewer: str
    comment: str
    decided_at: datetime

    @field_validator("reviewer", mode="before")
    @classmethod
    def _reviewer(cls, value: object) -> str:
        return identifier(value, field="reviewer")

    @field_validator("project_id", mode="before")
    @classmethod
    def _project_id(cls, value: object) -> str:
        return identifier(value, field="project_id")

    @field_validator("decision", mode="before")
    @classmethod
    def _decision(cls, value: object) -> CopilotReviewDecision:
        return CopilotReviewDecision(value) if isinstance(value, str) else value

    @field_validator("comment", mode="before")
    @classmethod
    def _comment(cls, value: object) -> str:
        return safe_text(value, field="comment", maximum=2048)

    @field_validator("decided_at", mode="before")
    @classmethod
    def _decided_at(cls, value: object) -> datetime:
        return _utc(value, field="decided_at")


class CopilotReviewView(FingerprintedChatContract):
    review_id: str
    project_id: str
    target_type: CopilotReviewTargetType
    target_id: str
    target_fingerprint: str
    requester: str
    status: str
    created_at: datetime
    decision: CopilotReviewDecision | None = None
    reviewer: str | None = None
    decided_at: datetime | None = None

    @field_validator(
        "review_id", "project_id", "target_id", "target_fingerprint", "requester", mode="before"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        if info.field_name == "target_fingerprint":
            return fingerprint(value, field=info.field_name)
        return identifier(value, field=info.field_name)

    @field_validator("created_at", "decided_at", mode="before")
    @classmethod
    def _timestamps(cls, value: object, info) -> datetime | None:
        return None if value is None else _utc(value, field=info.field_name)


__all__ = (
    "CopilotReviewCreateRequest",
    "CopilotReviewDecision",
    "CopilotReviewDecisionRequest",
    "CopilotReviewTargetType",
    "CopilotReviewView",
)
