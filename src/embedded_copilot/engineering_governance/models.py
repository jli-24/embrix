from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+-]{0,127}$")
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+-]{0,255}$")
_FINGERPRINT = re.compile(r"^sha256:[a-f0-9]{64}$")
_SECRET = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|bearer|password|credential|secret|token)\s*[:=]"
    r"|raw\s+(?:llm\s+)?output|hidden\s+reasoning|chain\s+of\s+thought|"
    r"(?:[A-Za-z]:[\\/]|\\\\|file://|\x00|\r|\n)",
    re.IGNORECASE,
)
_FORBIDDEN_CLAIM = re.compile(
    r"^(?:pcb design is correct|system is optimized|automatically fixed)$",
    re.IGNORECASE,
)


class GovernanceContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class CandidateStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class CandidateSourceType(StrEnum):
    MEMORY = "memory"
    GRAPH = "graph"
    RETRIEVAL = "retrieval"
    VISION = "vision"
    MULTIMODAL = "multimodal"


class ReviewDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_CHANGE = "REQUEST_CHANGE"


ModelT = TypeVar("ModelT", bound=BaseModel)


def _safe_text(value: object, *, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = unicodedata.normalize("NFC", value).strip()
    if not checked or len(checked) > maximum or _SECRET.search(checked):
        raise ValueError(f"{field} is unsafe")
    return checked


def identifier(value: object, *, field: str) -> str:
    checked = _safe_text(value, field=field, maximum=128)
    if _IDENTIFIER.fullmatch(checked) is None:
        raise ValueError(f"{field} is invalid")
    return checked


def reference(value: object, *, field: str) -> str:
    checked = _safe_text(value, field=field, maximum=256)
    if _REFERENCE.fullmatch(checked) is None:
        raise ValueError(f"{field} is invalid")
    return checked


def tuple_only(value: object, *, field: str) -> tuple[object, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{field} must be a tuple")
    return copy.deepcopy(value)


def fingerprint(value: object, *, field: str = "fingerprint") -> str:
    if not isinstance(value, str) or _FINGERPRINT.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def confidence(value: object) -> float:
    if type(value) is not float or not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError("confidence is invalid")
    return value


def utc_timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must be UTC")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be UTC")
    return value.astimezone(UTC)


def canonical_fingerprint(
    value: BaseModel, *, exclude: set[str] | frozenset[str] = frozenset()
) -> str:
    payload = value.model_dump(mode="json", exclude=exclude)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _with_fingerprint(cls: type[ModelT], values: dict[str, object]) -> ModelT:
    provisional = cls.model_construct(**{**values, "fingerprint": "sha256:" + "0" * 64})
    return cls.model_validate(
        {
            **values,
            "fingerprint": canonical_fingerprint(provisional, exclude={"fingerprint"}),
        }
    )


def _candidate_fingerprint(value: BaseModel) -> str:
    return canonical_fingerprint(value, exclude={"fingerprint", "status"})


def _candidate_claim(value: object) -> str:
    checked = _safe_text(value, field="claim", maximum=2048)
    if _FORBIDDEN_CLAIM.fullmatch(checked) is not None:
        raise ValueError("claim is not evidence based")
    return checked


def _evidence_refs(value: object) -> tuple[str, ...]:
    refs = tuple(
        reference(item, field="evidence_ref")
        for item in tuple_only(value, field="evidence_refs")
    )
    if not refs or len(refs) != len(set(refs)):
        raise ValueError("evidence refs must be non-empty and unique")
    return tuple(sorted(refs))


class KnowledgeCandidate(GovernanceContract):
    candidate_id: str
    project_id: str
    claim: str
    source_type: CandidateSourceType
    evidence_refs: tuple[str, ...]
    confidence: float
    status: CandidateStatus
    fingerprint: str

    @field_validator("candidate_id", "project_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("claim", mode="before")
    @classmethod
    def validate_claim(cls, value: object) -> str:
        return _candidate_claim(value)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def validate_evidence_refs(cls, value: object) -> tuple[str, ...]:
        return _evidence_refs(value)

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_confidence(cls, value: object) -> float:
        return confidence(value)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @classmethod
    def create(cls, **values: object) -> KnowledgeCandidate:
        material = dict(values)
        material["candidate_id"] = identifier(
            material["candidate_id"], field="candidate_id"
        )
        material["project_id"] = identifier(material["project_id"], field="project_id")
        material["claim"] = _candidate_claim(material["claim"])
        material["source_type"] = CandidateSourceType(material["source_type"])
        material["evidence_refs"] = _evidence_refs(material["evidence_refs"])
        material["confidence"] = confidence(material["confidence"])
        material["status"] = CandidateStatus(material["status"])
        material["fingerprint"] = cls.content_fingerprint(material)
        return cls.model_validate(material)

    def with_status(self, status: CandidateStatus) -> KnowledgeCandidate:
        values = {
            **self.model_dump(mode="python"),
            "status": CandidateStatus(status),
            "fingerprint": self.fingerprint,
        }
        return KnowledgeCandidate.model_validate(values)

    @classmethod
    def content_fingerprint(cls, values: dict[str, object]) -> str:
        provisional = cls.model_construct(
            **{**values, "fingerprint": "sha256:" + "0" * 64}
        )
        return _candidate_fingerprint(provisional)

    @model_validator(mode="after")
    def verify(self) -> KnowledgeCandidate:
        if self.fingerprint != _candidate_fingerprint(self):
            raise ValueError("candidate fingerprint mismatch")
        return self


class ReviewRequest(GovernanceContract):
    review_id: str
    candidate_id: str
    candidate_fingerprint: str
    reviewer: str
    decision: ReviewDecision
    comment: str
    reviewed_at: datetime
    fingerprint: str

    @field_validator("review_id", "candidate_id", "reviewer", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("candidate_fingerprint", "fingerprint", mode="before")
    @classmethod
    def validate_fingerprints(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("comment", mode="before")
    @classmethod
    def validate_comment(cls, value: object) -> str:
        return _safe_text(value, field="comment", maximum=2048)

    @field_validator("reviewed_at", mode="before")
    @classmethod
    def validate_reviewed_at(cls, value: object) -> datetime:
        return utc_timestamp(value, field="reviewed_at")

    @classmethod
    def create(cls, **values: object) -> ReviewRequest:
        material = dict(values)
        for field in ("review_id", "candidate_id", "reviewer"):
            material[field] = identifier(material[field], field=field)
        material["candidate_fingerprint"] = fingerprint(
            material["candidate_fingerprint"], field="candidate_fingerprint"
        )
        material["decision"] = ReviewDecision(material["decision"])
        material["comment"] = _safe_text(
            material["comment"], field="comment", maximum=2048
        )
        material["reviewed_at"] = utc_timestamp(
            material["reviewed_at"], field="reviewed_at"
        )
        return _with_fingerprint(cls, material)

    @model_validator(mode="after")
    def verify(self) -> ReviewRequest:
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("review fingerprint mismatch")
        return self


class ApprovedKnowledge(GovernanceContract):
    knowledge_id: str
    candidate_id: str
    project_id: str
    claim: str
    evidence_refs: tuple[str, ...]
    approved_by: str
    fingerprint: str

    @field_validator(
        "knowledge_id", "candidate_id", "project_id", "approved_by", mode="before"
    )
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("claim", mode="before")
    @classmethod
    def validate_claim(cls, value: object) -> str:
        return _candidate_claim(value)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def validate_evidence_refs(cls, value: object) -> tuple[str, ...]:
        return _evidence_refs(value)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @classmethod
    def create(cls, **values: object) -> ApprovedKnowledge:
        material = dict(values)
        for field in ("knowledge_id", "candidate_id", "project_id", "approved_by"):
            material[field] = identifier(material[field], field=field)
        material["claim"] = _candidate_claim(material["claim"])
        material["evidence_refs"] = _evidence_refs(material["evidence_refs"])
        return _with_fingerprint(cls, material)

    @model_validator(mode="after")
    def verify(self) -> ApprovedKnowledge:
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("approved knowledge fingerprint mismatch")
        return self


__all__ = (
    "ApprovedKnowledge",
    "CandidateSourceType",
    "CandidateStatus",
    "GovernanceContract",
    "KnowledgeCandidate",
    "ReviewDecision",
    "ReviewRequest",
    "canonical_fingerprint",
    "confidence",
    "fingerprint",
    "identifier",
    "reference",
    "tuple_only",
    "utc_timestamp",
)
