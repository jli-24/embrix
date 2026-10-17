from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/-]{0,127}$")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+\-]{0,255}$")
_FP = re.compile(r"^sha256:[a-f0-9]{64}$")
_UNSAFE = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\|file://|\x00|\r|\n|"
    r"(?:api[_ -]?key|access[_ -]?token|bearer|password|credential|secret|token)\s*[:=])",
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


def safe_text(value: object, *, field: str, maximum: int = 2048) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = unicodedata.normalize("NFC", value).strip()
    if not checked or len(checked) > maximum or _UNSAFE.search(checked):
        raise ValueError(f"{field} is unsafe")
    return checked


def identifier(value: object, *, field: str) -> str:
    checked = safe_text(value, field=field, maximum=128)
    if not _ID.fullmatch(checked):
        raise ValueError(f"{field} is invalid")
    return checked


def reference(value: object, *, field: str) -> str:
    checked = safe_text(value, field=field, maximum=256)
    if not _REF.fullmatch(checked):
        raise ValueError(f"{field} is invalid")
    return checked


def tuple_only(value: object, *, field: str) -> tuple[object, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{field} must be a tuple")
    return copy.deepcopy(value)


def valid_fingerprint(value: object, *, field: str = "fingerprint") -> str:
    if not isinstance(value, str) or not _FP.fullmatch(value):
        raise ValueError(f"{field} is invalid")
    return value


def utc_timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be UTC")
    return value.astimezone(UTC)


def confidence(value: object) -> float:
    if type(value) is not float or not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError("confidence is invalid")
    return value


def canonical_fingerprint(value: BaseModel, *, exclude: set[str] | frozenset[str] = frozenset()) -> str:
    payload = value.model_dump(mode="json", exclude=exclude)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def content_fingerprint(value: str) -> str:
    return "sha256:" + hashlib.sha256(unicodedata.normalize("NFC", value).strip().encode("utf-8")).hexdigest()


class ReviewSourceType(StrEnum):
    ENGINEERING_ANSWER = "ENGINEERING_ANSWER"
    CONTEXT_CONFLICT = "CONTEXT_CONFLICT"
    GRAPH_CONFLICT = "GRAPH_CONFLICT"


class ReviewStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NEEDS_CHANGE = "NEEDS_CHANGE"


class ReviewDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NEEDS_CHANGE = "NEEDS_CHANGE"


class ConflictResolution(StrEnum):
    ACCEPT_A = "ACCEPT_A"
    ACCEPT_B = "ACCEPT_B"
    KEEP_BOTH = "KEEP_BOTH"
    INSUFFICIENT_INFO = "INSUFFICIENT_INFO"


class ReviewTask(GovernanceContract):
    review_id: str
    project_id: str
    source_type: ReviewSourceType
    source_id: str
    content_fingerprint: str
    evidence_refs: tuple[str, ...]
    status: ReviewStatus
    created_at: datetime
    fingerprint: str

    @field_validator("review_id", "project_id", "source_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("content_fingerprint", "fingerprint", mode="before")
    @classmethod
    def validate_fingerprints(cls, value: object, info) -> str:
        return valid_fingerprint(value, field=info.field_name)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def validate_evidence_refs(cls, value: object) -> tuple[str, ...]:
        refs = tuple(reference(item, field="evidence_ref") for item in tuple_only(value, field="evidence_refs"))
        if len(refs) != len(set(refs)):
            raise ValueError("evidence refs must be unique")
        return tuple(sorted(refs))

    @field_validator("created_at", mode="before")
    @classmethod
    def validate_created_at(cls, value: object) -> datetime:
        return utc_timestamp(value, field="created_at")

    @classmethod
    def create(cls, **values: object) -> ReviewTask:
        material = dict(values)
        for field in ("review_id", "project_id", "source_id"):
            material[field] = identifier(material[field], field=field)
        material["source_type"] = ReviewSourceType(material["source_type"])
        material["status"] = ReviewStatus(material["status"])
        material["content_fingerprint"] = valid_fingerprint(material["content_fingerprint"], field="content_fingerprint")
        material["evidence_refs"] = tuple(sorted(reference(item, field="evidence_ref") for item in tuple_only(material["evidence_refs"], field="evidence_refs")))
        material["created_at"] = utc_timestamp(material["created_at"], field="created_at")
        provisional = cls.model_construct(**material, fingerprint="sha256:" + "0" * 64)
        return cls.model_validate({**material, "fingerprint": canonical_fingerprint(provisional, exclude={"fingerprint"})})

    @model_validator(mode="after")
    def verify(self) -> ReviewTask:
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint != expected:
            raise ValueError("review task fingerprint mismatch")
        return self


class ApprovalRecord(GovernanceContract):
    approval_id: str
    review_id: str
    reviewer_identity: str
    decision: ReviewDecision
    reason: str
    timestamp: datetime
    input_fingerprint: str
    fingerprint: str

    @field_validator("approval_id", "review_id", "reviewer_identity", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("reason", mode="before")
    @classmethod
    def validate_reason(cls, value: object) -> str:
        return safe_text(value, field="reason", maximum=1024)

    @field_validator("timestamp", mode="before")
    @classmethod
    def validate_timestamp(cls, value: object) -> datetime:
        return utc_timestamp(value, field="timestamp")

    @field_validator("input_fingerprint", "fingerprint", mode="before")
    @classmethod
    def validate_fingerprints(cls, value: object, info) -> str:
        return valid_fingerprint(value, field=info.field_name)

    @classmethod
    def create(cls, **values: object) -> ApprovalRecord:
        material = dict(values)
        for field in ("approval_id", "review_id", "reviewer_identity"):
            material[field] = identifier(material[field], field=field)
        material["decision"] = ReviewDecision(material["decision"])
        material["reason"] = safe_text(material["reason"], field="reason", maximum=1024)
        material["timestamp"] = utc_timestamp(material["timestamp"], field="timestamp")
        material["input_fingerprint"] = valid_fingerprint(material["input_fingerprint"], field="input_fingerprint")
        provisional = cls.model_construct(**material, fingerprint="sha256:" + "0" * 64)
        return cls.model_validate({**material, "fingerprint": canonical_fingerprint(provisional, exclude={"fingerprint"})})

    @model_validator(mode="after")
    def verify(self) -> ApprovalRecord:
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint != expected:
            raise ValueError("approval fingerprint mismatch")
        return self


class EvidenceAudit(GovernanceContract):
    audit_id: str
    review_id: str
    project_id: str
    evidence_id: str
    source_reference: str
    source_fingerprint: str
    confidence: float | None
    verification_status: str
    input_fingerprint: str
    fingerprint: str

    @field_validator("audit_id", "review_id", "project_id", "evidence_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("source_reference", mode="before")
    @classmethod
    def validate_source_reference(cls, value: object) -> str:
        return reference(value, field="source_reference")

    @field_validator("source_fingerprint", "input_fingerprint", "fingerprint", mode="before")
    @classmethod
    def validate_fingerprints(cls, value: object, info) -> str:
        return valid_fingerprint(value, field=info.field_name)

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_confidence(cls, value: object) -> float | None:
        return None if value is None else confidence(value)

    @field_validator("verification_status", mode="before")
    @classmethod
    def validate_status(cls, value: object) -> str:
        return identifier(value, field="verification_status")

    @classmethod
    def create(cls, **values: object) -> EvidenceAudit:
        material = dict(values)
        for field in ("audit_id", "review_id", "project_id", "evidence_id"):
            material[field] = identifier(material[field], field=field)
        material["source_reference"] = reference(material["source_reference"], field="source_reference")
        material["source_fingerprint"] = valid_fingerprint(material["source_fingerprint"], field="source_fingerprint")
        material["input_fingerprint"] = valid_fingerprint(material["input_fingerprint"], field="input_fingerprint")
        material["confidence"] = None if material.get("confidence") is None else confidence(material["confidence"])
        material["verification_status"] = identifier(material["verification_status"], field="verification_status")
        provisional = cls.model_construct(**material, fingerprint="sha256:" + "0" * 64)
        return cls.model_validate({**material, "fingerprint": canonical_fingerprint(provisional, exclude={"fingerprint"})})

    @model_validator(mode="after")
    def verify(self) -> EvidenceAudit:
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint != expected:
            raise ValueError("evidence audit fingerprint mismatch")
        return self


class ApprovedCandidateProjection(GovernanceContract):
    candidate_id: str
    review_id: str
    project_id: str
    source_type: ReviewSourceType
    source_id: str
    content_fingerprint: str
    evidence_refs: tuple[str, ...]
    approval_id: str
    approval_fingerprint: str
    status: Literal["APPROVED"] = "APPROVED"
    fingerprint: str

    @field_validator("candidate_id", "review_id", "project_id", "source_id", "approval_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("content_fingerprint", "approval_fingerprint", "fingerprint", mode="before")
    @classmethod
    def validate_fingerprints(cls, value: object, info) -> str:
        return valid_fingerprint(value, field=info.field_name)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def validate_evidence_refs(cls, value: object) -> tuple[str, ...]:
        return tuple(sorted(reference(item, field="evidence_ref") for item in tuple_only(value, field="evidence_refs")))

    @classmethod
    def create(cls, **values: object) -> ApprovedCandidateProjection:
        material = dict(values)
        for field in ("candidate_id", "review_id", "project_id", "source_id", "approval_id"):
            material[field] = identifier(material[field], field=field)
        material["source_type"] = ReviewSourceType(material["source_type"])
        material["content_fingerprint"] = valid_fingerprint(material["content_fingerprint"], field="content_fingerprint")
        material["approval_fingerprint"] = valid_fingerprint(material["approval_fingerprint"], field="approval_fingerprint")
        material["evidence_refs"] = tuple(sorted(reference(item, field="evidence_ref") for item in tuple_only(material["evidence_refs"], field="evidence_refs")))
        provisional = cls.model_construct(**material, fingerprint="sha256:" + "0" * 64)
        return cls.model_validate({**material, "fingerprint": canonical_fingerprint(provisional, exclude={"fingerprint"})})

    @model_validator(mode="after")
    def verify(self) -> ApprovedCandidateProjection:
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint != expected:
            raise ValueError("approved candidate fingerprint mismatch")
        return self


class ReviewDecisionResult(GovernanceContract):
    review: ReviewTask
    approval: ApprovalRecord
    approved_candidate: ApprovedCandidateProjection | None
    fingerprint: str

    @classmethod
    def create(cls, **values: object) -> ReviewDecisionResult:
        provisional = cls.model_construct(**values, fingerprint="sha256:" + "0" * 64)
        return cls.model_validate({**values, "fingerprint": canonical_fingerprint(provisional, exclude={"fingerprint"})})

    @model_validator(mode="after")
    def verify(self) -> ReviewDecisionResult:
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint != expected:
            raise ValueError("review decision result fingerprint mismatch")
        return self


class ConflictReview(GovernanceContract):
    conflict_id: str
    project_id: str
    source_a: str
    source_b: str
    reason: str
    resolution: ConflictResolution
    reviewer_identity: str
    resolved_at: datetime
    input_fingerprint: str
    fingerprint: str

    @field_validator("conflict_id", "project_id", "source_a", "source_b", "reviewer_identity", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("reason", mode="before")
    @classmethod
    def validate_reason(cls, value: object) -> str:
        return safe_text(value, field="reason", maximum=512)

    @field_validator("resolved_at", mode="before")
    @classmethod
    def validate_resolved_at(cls, value: object) -> datetime:
        return utc_timestamp(value, field="resolved_at")

    @field_validator("input_fingerprint", "fingerprint", mode="before")
    @classmethod
    def validate_fingerprints(cls, value: object, info) -> str:
        return valid_fingerprint(value, field=info.field_name)

    @classmethod
    def create(cls, **values: object) -> ConflictReview:
        material = dict(values)
        for field in ("conflict_id", "project_id", "source_a", "source_b", "reviewer_identity"):
            material[field] = identifier(material[field], field=field)
        material["reason"] = safe_text(material["reason"], field="reason", maximum=512)
        material["resolution"] = ConflictResolution(material["resolution"])
        material["resolved_at"] = utc_timestamp(material["resolved_at"], field="resolved_at")
        material["input_fingerprint"] = valid_fingerprint(material["input_fingerprint"], field="input_fingerprint")
        provisional = cls.model_construct(**material, fingerprint="sha256:" + "0" * 64)
        return cls.model_validate({**material, "fingerprint": canonical_fingerprint(provisional, exclude={"fingerprint"})})

    @model_validator(mode="after")
    def verify(self) -> ConflictReview:
        if self.source_a == self.source_b:
            raise ValueError("conflict requires two distinct sources")
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint != expected:
            raise ValueError("conflict review fingerprint mismatch")
        return self


__all__ = (
    "ApprovalRecord", "ApprovedCandidateProjection", "ConflictResolution", "ConflictReview",
    "EvidenceAudit", "GovernanceContract", "ReviewDecision", "ReviewDecisionResult",
    "ReviewSourceType", "ReviewStatus", "canonical_fingerprint", "content_fingerprint",
)
