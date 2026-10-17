from __future__ import annotations

import copy
from datetime import datetime
from enum import StrEnum

from pydantic import field_validator, model_validator

from .models import (
    ContextContract,
    canonical_fingerprint,
    fingerprint,
    identifier,
    tuple_only,
)

_AUTO = "sha256:" + "0" * 64


class ContextEvidenceType(StrEnum):
    EDA_VALIDATION = "EDA_VALIDATION"
    HARDWARE_OBSERVATION = "HARDWARE_OBSERVATION"
    OPTIMIZATION_DECISION = "OPTIMIZATION_DECISION"


class ContextOverlayEvidence(ContextContract):
    evidence_id: str
    project_id: str
    evidence_type: ContextEvidenceType
    source_id: str
    source_fingerprint: str
    summary: str
    observed_at: datetime
    fingerprint: str = _AUTO

    @field_validator("evidence_id", "project_id", "source_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("summary", mode="before")
    @classmethod
    def _summary(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("summary is invalid")
        checked = value.strip()
        if not checked or len(checked) > 512 or "\n" in checked or "\r" in checked:
            raise ValueError("summary is unsafe")
        return checked

    @field_validator("source_fingerprint", "fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("observed_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError("observed_at is invalid")
        return value

    @model_validator(mode="after")
    def _bind(self):
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("context overlay evidence fingerprint mismatch")
        return self


class EngineeringContextOverlay(ContextContract):
    overlay_id: str
    project_id: str
    base_context_fingerprint: str | None
    evidence: tuple[ContextOverlayEvidence, ...]
    created_at: datetime
    fingerprint: str = _AUTO

    @field_validator("overlay_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("base_context_fingerprint", mode="before")
    @classmethod
    def _base_fp(cls, value: object) -> str | None:
        return (
            None
            if value is None
            else fingerprint(value, field="base_context_fingerprint")
        )

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence(cls, value: object) -> tuple[ContextOverlayEvidence, ...]:
        items = tuple(
            ContextOverlayEvidence.model_validate(copy.deepcopy(item))
            for item in tuple_only(value, field="evidence")
        )
        if not items or len({item.evidence_id for item in items}) != len(items):
            raise ValueError("context overlay evidence must be non-empty and unique")
        return items

    @field_validator("created_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError("created_at is invalid")
        return value

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return fingerprint(value)

    @model_validator(mode="after")
    def _bind(self):
        if any(item.project_id != self.project_id for item in self.evidence):
            raise ValueError("context overlay project mismatch")
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("context overlay fingerprint mismatch")
        return self


__all__ = (
    "ContextEvidenceType",
    "ContextOverlayEvidence",
    "EngineeringContextOverlay",
)
