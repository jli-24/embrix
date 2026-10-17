from __future__ import annotations

from enum import StrEnum

from pydantic import Field, field_validator

from embedded_copilot.desktop_copilot_chat.contracts import (
    FingerprintedChatContract,
    identifier,
    safe_text,
    tuple_only,
)


class EngineeringEvidenceLevel(StrEnum):
    OBSERVATION = "OBSERVATION"
    INFERENCE = "INFERENCE"
    CANDIDATE = "CANDIDATE"
    VERIFIED_FACT = "VERIFIED_FACT"
    RECOMMENDATION = "RECOMMENDATION"


class EngineeringEvidenceView(FingerprintedChatContract):
    evidence_id: str
    project_id: str
    level: EngineeringEvidenceLevel
    summary: str
    source_reference: str
    source_fingerprint: str
    confidence: float = Field(ge=0, le=1)
    needs_more_evidence: tuple[str, ...] = ()

    @field_validator("evidence_id", "project_id", "source_reference", "source_fingerprint", mode="before")
    @classmethod
    def _identifiers(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("summary", mode="before")
    @classmethod
    def _summary(cls, value: object) -> str:
        return safe_text(value, field="summary", maximum=1024)

    @field_validator("needs_more_evidence", mode="before")
    @classmethod
    def _needs_more_evidence(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, list):
            value = tuple(value)
        return tuple(safe_text(item, field="need", maximum=256) for item in tuple_only(value, field="needs_more_evidence"))


class FirmwareAnalysisRequest(FingerprintedChatContract):
    project_id: str
    question: str

    @field_validator("project_id", mode="before")
    @classmethod
    def _project_id(cls, value: object) -> str:
        return identifier(value, field="project_id")

    @field_validator("question", mode="before")
    @classmethod
    def _question(cls, value: object) -> str:
        return safe_text(value, field="question", maximum=2048)


class FirmwareAnalysisResponse(FingerprintedChatContract):
    project_id: str
    facts: tuple[str, ...]
    evidence: tuple[EngineeringEvidenceView, ...]
    recommendations: tuple[str, ...]
    confidence: float = Field(ge=0, le=1)
    limitations: tuple[str, ...]
    source_fingerprint: str

    @field_validator("project_id", "source_fingerprint", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("facts", "recommendations", "limitations", mode="before")
    @classmethod
    def _texts(cls, value: object, info) -> tuple[str, ...]:
        if isinstance(value, list):
            value = tuple(value)
        return tuple(safe_text(item, field=info.field_name, maximum=1024) for item in tuple_only(value, field=info.field_name))


class DebugAnalysisRequest(FingerprintedChatContract):
    project_id: str
    failure_reference: str
    failure_type: str
    summary: str

    @field_validator("project_id", "failure_reference", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("failure_type", "summary", mode="before")
    @classmethod
    def _text(cls, value: object, info) -> str:
        return safe_text(value, field=info.field_name, maximum=1024)


class DebugAnalysisResponse(FingerprintedChatContract):
    project_id: str
    failure_reference: str
    root_cause_candidates: tuple[EngineeringEvidenceView, ...]
    confidence: float = Field(ge=0, le=1)
    needs_more_evidence: tuple[str, ...]
    recommendations: tuple[str, ...]

    @field_validator("project_id", "failure_reference", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("needs_more_evidence", "recommendations", mode="before")
    @classmethod
    def _texts(cls, value: object, info) -> tuple[str, ...]:
        if isinstance(value, list):
            value = tuple(value)
        return tuple(safe_text(item, field=info.field_name, maximum=1024) for item in tuple_only(value, field=info.field_name))


__all__ = (
    "DebugAnalysisRequest",
    "DebugAnalysisResponse",
    "EngineeringEvidenceLevel",
    "EngineeringEvidenceView",
    "FirmwareAnalysisRequest",
    "FirmwareAnalysisResponse",
)
