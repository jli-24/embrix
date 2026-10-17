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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+_\-]{0,127}$")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+_\-]{0,255}$")
_FP = re.compile(r"^sha256:[a-f0-9]{64}$")
_FORBIDDEN = re.compile(
    r"(?:raw\s+(?:conversation|source|log)|prompt|cot|chain.of.thought|token|secret|"
    r"credential|provider\s+payload|stdout|stderr|command\s+string|(?<![A-Za-z])[A-Za-z]:[\\/]|\\\\|file://)",
    re.IGNORECASE,
)


class V63Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


def _text(value: object, *, field: str, maximum: int = 2048) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    value = unicodedata.normalize("NFC", value).strip()
    if not value or len(value) > maximum or "\x00" in value or "\n" in value or "\r" in value or _FORBIDDEN.search(value):
        raise ValueError(f"{field} is unsafe")
    return value


def _id(value: object, *, field: str) -> str:
    value = _text(value, field=field, maximum=128)
    if not _ID.fullmatch(value):
        raise ValueError(f"{field} is invalid")
    return value


def _ref(value: object, *, field: str) -> str:
    value = _text(value, field=field, maximum=256)
    if not _REF.fullmatch(value):
        raise ValueError(f"{field} is invalid")
    return value


def _fp(value: object, *, field: str = "fingerprint") -> str:
    if not isinstance(value, str) or _FP.fullmatch(value.strip()) is None:
        raise ValueError(f"{field} is invalid")
    return value.strip()


def _tuple(value: object, *, field: str) -> tuple[object, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{field} must be a tuple")
    return copy.deepcopy(value)


def _utc(value: object, *, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"{field} is invalid") from error
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be UTC")
    return value.astimezone(UTC)


def _enum(value: object, enum_type: type[StrEnum], *, field: str) -> StrEnum:
    if isinstance(value, enum_type):
        return value
    if isinstance(value, str):
        try:
            return enum_type(value)
        except ValueError as error:
            raise ValueError(f"{field} is invalid") from error
    raise TypeError(f"{field} is invalid")


def canonical_fingerprint(value: BaseModel, *, exclude: set[str] | frozenset[str] = frozenset()) -> str:
    payload = value.model_dump(mode="json", exclude=exclude)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _with_fingerprint(
    cls: type[BaseModel],
    values: dict[str, object],
    field: str,
    *,
    exclude: set[str] | frozenset[str] = frozenset(),
) -> BaseModel:
    material = dict(values)
    material[field] = "sha256:" + "0" * 64
    provisional = cls.model_construct(**material)
    material[field] = canonical_fingerprint(provisional, exclude={field, *exclude})
    return cls.model_validate(material)


class MemoryType(StrEnum):
    DECISION = "DECISION"
    CONSTRAINT = "CONSTRAINT"
    FAILURE = "FAILURE"
    SOLUTION = "SOLUTION"
    VALIDATION = "VALIDATION"
    OPTIMIZATION = "OPTIMIZATION"


class MemoryStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    WAIT_REVIEW = "WAIT_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    REVOKED = "REVOKED"


class KnowledgeStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    WAIT_REVIEW = "WAIT_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ACTIVE = "ACTIVE"
    STALE = "STALE"
    REVOKED = "REVOKED"


class TrustLevel(StrEnum):
    OBSERVATION = "OBSERVATION"
    INFERENCE = "INFERENCE"
    CANDIDATE = "CANDIDATE"
    VERIFIED_FACT = "VERIFIED_FACT"


class ReviewTargetType(StrEnum):
    MEMORY_CANDIDATE = "MEMORY_CANDIDATE"
    KNOWLEDGE_CANDIDATE = "KNOWLEDGE_CANDIDATE"
    KNOWLEDGE_EVOLUTION_PROPOSAL = "KNOWLEDGE_EVOLUTION_PROPOSAL"


class ReviewStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    STALE = "STALE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class EvolutionTarget(StrEnum):
    MEMORY = "MEMORY"
    RETRIEVAL = "RETRIEVAL"
    GRAPH = "GRAPH"


class EvolutionStatus(StrEnum):
    CREATED = "CREATED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    APPLIED = "APPLIED"


class Freshness(StrEnum):
    CURRENT = "CURRENT"
    HISTORICAL = "HISTORICAL"
    STALE = "STALE"


class EvidenceIdentity(V63Contract):
    evidence_id: str
    tenant_id: str
    project_id: str
    source_reference: str
    source_fingerprint: str
    evidence_fingerprint: str

    @field_validator("evidence_id", "tenant_id", "project_id", mode="before")
    @classmethod
    def ids(cls, value: object, info) -> str:
        return _id(value, field=info.field_name)

    @field_validator("source_reference", mode="before")
    @classmethod
    def reference(cls, value: object) -> str:
        return _ref(value, field="source_reference")

    @field_validator("source_fingerprint", "evidence_fingerprint", mode="before")
    @classmethod
    def fingerprints(cls, value: object, info) -> str:
        return _fp(value, field=info.field_name)


class StructuredCandidateInput(V63Contract):
    tenant_id: str
    requester_id: str
    project_id: str
    memory_type: MemoryType
    subject: str
    statement: str
    evidence: tuple[EvidenceIdentity, ...]
    confidence: float = Field(ge=0.0, le=1.0)
    limitations: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()

    @field_validator("tenant_id", "requester_id", "project_id", mode="before")
    @classmethod
    def ids(cls, value: object, info) -> str:
        return _id(value, field=info.field_name)

    @field_validator("memory_type", mode="before")
    @classmethod
    def memory_enum(cls, value: object) -> MemoryType:
        return _enum(value, MemoryType, field="memory_type")  # type: ignore[return-value]

    @field_validator("subject", "statement", mode="before")
    @classmethod
    def text(cls, value: object, info) -> str:
        return _text(value, field=info.field_name)

    @field_validator("evidence", "limitations", "provenance", mode="before")
    @classmethod
    def tuples(cls, value: object, info) -> tuple[object, ...]:
        return _tuple(value, field=info.field_name)

    @field_validator("limitations", "provenance")
    @classmethod
    def safe_items(cls, value: tuple[str, ...], info) -> tuple[str, ...]:
        return tuple(_text(item, field=info.field_name, maximum=512) for item in value)

    @model_validator(mode="after")
    def bind_evidence(self) -> StructuredCandidateInput:
        if not self.evidence or any(item.project_id != self.project_id or item.tenant_id != self.tenant_id for item in self.evidence):
            raise ValueError("candidate evidence binding is invalid")
        if len({item.evidence_id for item in self.evidence}) != len(self.evidence):
            raise ValueError("candidate evidence ids must be unique")
        return self


class MemoryCandidate(V63Contract):
    memory_candidate_id: str
    tenant_id: str
    requester_id: str
    project_id: str
    memory_type: MemoryType
    subject: str
    statement: str
    evidence_ids: tuple[str, ...]
    source_references: tuple[str, ...]
    source_fingerprints: tuple[str, ...]
    confidence: float = Field(ge=0.0, le=1.0)
    limitations: tuple[str, ...] = ()
    candidate_fingerprint: str
    status: MemoryStatus
    created_at: datetime
    provenance: tuple[str, ...] = ()

    @field_validator("memory_candidate_id", "tenant_id", "requester_id", "project_id", mode="before")
    @classmethod
    def ids(cls, value: object, info) -> str:
        return _id(value, field=info.field_name)

    @field_validator("memory_type", mode="before")
    @classmethod
    def memory_enum(cls, value: object) -> MemoryType:
        return _enum(value, MemoryType, field="memory_type")  # type: ignore[return-value]

    @field_validator("status", mode="before")
    @classmethod
    def status_enum(cls, value: object) -> MemoryStatus:
        return _enum(value, MemoryStatus, field="status")  # type: ignore[return-value]

    @field_validator("subject", "statement", mode="before")
    @classmethod
    def text(cls, value: object, info) -> str:
        return _text(value, field=info.field_name)

    @field_validator("evidence_ids", "source_references", "source_fingerprints", "limitations", "provenance", mode="before")
    @classmethod
    def collections(cls, value: object, info) -> tuple[object, ...]:
        return _tuple(value, field=info.field_name)

    @field_validator("evidence_ids")
    @classmethod
    def evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        checked = tuple(_id(item, field="evidence_id") for item in value)
        if not checked or len(set(checked)) != len(checked):
            raise ValueError("evidence_ids are invalid")
        return tuple(sorted(checked))

    @field_validator("source_references")
    @classmethod
    def references(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        checked = tuple(_ref(item, field="source_reference") for item in value)
        if not checked:
            raise ValueError("source_references are invalid")
        return tuple(checked)

    @field_validator("source_fingerprints")
    @classmethod
    def source_fps(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        checked = tuple(_fp(item, field="source_fingerprint") for item in value)
        if not checked:
            raise ValueError("source_fingerprints are invalid")
        return tuple(checked)

    @field_validator("limitations", "provenance")
    @classmethod
    def safe_items(cls, value: tuple[str, ...], info) -> tuple[str, ...]:
        return tuple(_text(item, field=info.field_name, maximum=512) for item in value)

    @field_validator("candidate_fingerprint", mode="before")
    @classmethod
    def candidate_fp(cls, value: object) -> str:
        return _fp(value, field="candidate_fingerprint")

    @field_validator("created_at", mode="before")
    @classmethod
    def created(cls, value: object) -> datetime:
        return _utc(value, field="created_at")

    @classmethod
    def create(cls, **values: object) -> MemoryCandidate:
        values = {**values, "status": MemoryStatus.CANDIDATE}
        return _with_fingerprint(cls, values, "candidate_fingerprint", exclude={"status", "created_at"})

    def with_status(self, status: MemoryStatus) -> MemoryCandidate:
        return self.model_copy(update={"status": MemoryStatus(status)})

    @model_validator(mode="after")
    def verify(self) -> MemoryCandidate:
        if self.candidate_fingerprint != canonical_fingerprint(self, exclude={"candidate_fingerprint", "status", "created_at"}):
            raise ValueError("memory candidate fingerprint mismatch")
        if len(self.evidence_ids) != len(self.source_fingerprints) or len(self.source_references) != len(self.source_fingerprints):
            raise ValueError("memory candidate source binding mismatch")
        return self


class EngineeringMemory(V63Contract):
    memory_id: str
    tenant_id: str
    project_id: str
    memory_type: MemoryType
    subject: str
    statement: str
    evidence_ids: tuple[str, ...]
    source_references: tuple[str, ...]
    source_fingerprints: tuple[str, ...]
    confidence: float = Field(ge=0.0, le=1.0)
    limitations: tuple[str, ...] = ()
    review_id: str
    approved_by: str
    approved_at: datetime
    status: MemoryStatus
    memory_fingerprint: str
    created_at: datetime
    updated_at: datetime
    provenance: tuple[str, ...] = ()

    @field_validator("memory_id", "tenant_id", "project_id", "review_id", "approved_by", mode="before")
    @classmethod
    def ids(cls, value: object, info) -> str:
        return _id(value, field=info.field_name)

    @field_validator("memory_type", mode="before")
    @classmethod
    def memory_enum(cls, value: object) -> MemoryType:
        return _enum(value, MemoryType, field="memory_type")  # type: ignore[return-value]

    @field_validator("status", mode="before")
    @classmethod
    def status_enum(cls, value: object) -> MemoryStatus:
        return _enum(value, MemoryStatus, field="status")  # type: ignore[return-value]

    @field_validator("subject", "statement", mode="before")
    @classmethod
    def text(cls, value: object, info) -> str:
        return _text(value, field=info.field_name)

    @field_validator("evidence_ids", "source_references", "source_fingerprints", "limitations", "provenance", mode="before")
    @classmethod
    def collections(cls, value: object, info) -> tuple[object, ...]:
        return _tuple(value, field=info.field_name)

    @field_validator("evidence_ids")
    @classmethod
    def evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(_id(item, field="evidence_id") for item in value))

    @field_validator("source_references")
    @classmethod
    def references(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(_ref(item, field="source_reference") for item in value))

    @field_validator("source_fingerprints")
    @classmethod
    def source_fps(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(_fp(item, field="source_fingerprint") for item in value))

    @field_validator("limitations", "provenance")
    @classmethod
    def safe_items(cls, value: tuple[str, ...], info) -> tuple[str, ...]:
        return tuple(_text(item, field=info.field_name, maximum=512) for item in value)

    @field_validator("memory_fingerprint", mode="before")
    @classmethod
    def memory_fp(cls, value: object) -> str:
        return _fp(value, field="memory_fingerprint")

    @field_validator("approved_at", "created_at", "updated_at", mode="before")
    @classmethod
    def timestamps(cls, value: object, info) -> datetime:
        return _utc(value, field=info.field_name)

    @model_validator(mode="after")
    def verify(self) -> EngineeringMemory:
        if self.memory_fingerprint != canonical_fingerprint(self, exclude={"memory_fingerprint", "status", "updated_at"}):
            raise ValueError("memory fingerprint mismatch")
        if self.status not in {MemoryStatus.APPROVED, MemoryStatus.ACTIVE, MemoryStatus.STALE, MemoryStatus.REVOKED}:
            raise ValueError("engineering memory status is invalid")
        return self


class KnowledgeCandidate(V63Contract):
    knowledge_candidate_id: str
    tenant_id: str
    requester_id: str
    project_id: str
    knowledge_type: str
    subject: str
    predicate: str
    value: str
    evidence_ids: tuple[str, ...]
    source_references: tuple[str, ...]
    source_fingerprints: tuple[str, ...]
    trust_level: TrustLevel
    confidence: float = Field(ge=0.0, le=1.0)
    limitations: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()
    candidate_fingerprint: str
    status: KnowledgeStatus
    created_at: datetime

    @field_validator("knowledge_candidate_id", "tenant_id", "requester_id", "project_id", mode="before")
    @classmethod
    def ids(cls, value: object, info) -> str:
        return _id(value, field=info.field_name)

    @field_validator("trust_level", mode="before")
    @classmethod
    def trust_enum(cls, value: object) -> TrustLevel:
        return _enum(value, TrustLevel, field="trust_level")  # type: ignore[return-value]

    @field_validator("status", mode="before")
    @classmethod
    def status_enum(cls, value: object) -> KnowledgeStatus:
        return _enum(value, KnowledgeStatus, field="status")  # type: ignore[return-value]

    @field_validator("knowledge_type", "subject", "predicate", "value", mode="before")
    @classmethod
    def text(cls, value: object, info) -> str:
        return _text(value, field=info.field_name)

    @field_validator("evidence_ids", "source_references", "source_fingerprints", "limitations", "provenance", mode="before")
    @classmethod
    def collections(cls, value: object, info) -> tuple[object, ...]:
        return _tuple(value, field=info.field_name)

    @field_validator("evidence_ids")
    @classmethod
    def evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        checked = tuple(sorted(_id(item, field="evidence_id") for item in value))
        if not checked:
            raise ValueError("knowledge evidence is required")
        return checked

    @field_validator("source_references")
    @classmethod
    def refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(_ref(item, field="source_reference") for item in value))

    @field_validator("source_fingerprints")
    @classmethod
    def fps(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(_fp(item, field="source_fingerprint") for item in value))

    @field_validator("limitations", "provenance")
    @classmethod
    def safe_items(cls, value: tuple[str, ...], info) -> tuple[str, ...]:
        return tuple(_text(item, field=info.field_name, maximum=512) for item in value)

    @field_validator("candidate_fingerprint", mode="before")
    @classmethod
    def fp(cls, value: object) -> str:
        return _fp(value, field="candidate_fingerprint")

    @field_validator("created_at", mode="before")
    @classmethod
    def timestamp(cls, value: object) -> datetime:
        return _utc(value, field="created_at")

    @classmethod
    def create(cls, **values: object) -> KnowledgeCandidate:
        if values.get("trust_level") is TrustLevel.VERIFIED_FACT or values.get("trust_level") == TrustLevel.VERIFIED_FACT:
            raise ValueError("candidate cannot be VERIFIED_FACT")
        values = {**values, "status": KnowledgeStatus.CANDIDATE}
        return _with_fingerprint(cls, values, "candidate_fingerprint", exclude={"status", "created_at"})

    @model_validator(mode="after")
    def verify(self) -> KnowledgeCandidate:
        if self.candidate_fingerprint != canonical_fingerprint(self, exclude={"candidate_fingerprint", "status", "created_at"}):
            raise ValueError("knowledge candidate fingerprint mismatch")
        if self.trust_level is TrustLevel.VERIFIED_FACT:
            raise ValueError("candidate cannot be VERIFIED_FACT")
        return self


class ApprovedKnowledge(V63Contract):
    knowledge_id: str
    tenant_id: str
    project_id: str
    scope_identity: str
    subject: str
    predicate: str
    value: str
    evidence_ids: tuple[str, ...]
    source_references: tuple[str, ...]
    source_fingerprints: tuple[str, ...]
    verification_status: Literal["VERIFIED"] = "VERIFIED"
    review_id: str
    approved_by: str
    approved_at: datetime
    knowledge_fingerprint: str
    status: KnowledgeStatus
    created_at: datetime
    updated_at: datetime
    provenance: tuple[str, ...] = ()

    @field_validator("knowledge_id", "tenant_id", "project_id", "scope_identity", "review_id", "approved_by", mode="before")
    @classmethod
    def ids(cls, value: object, info) -> str:
        return _id(value, field=info.field_name)

    @field_validator("status", mode="before")
    @classmethod
    def status_enum(cls, value: object) -> KnowledgeStatus:
        return _enum(value, KnowledgeStatus, field="status")  # type: ignore[return-value]

    @field_validator("subject", "predicate", "value", mode="before")
    @classmethod
    def text(cls, value: object, info) -> str:
        return _text(value, field=info.field_name)

    @field_validator("evidence_ids", "source_references", "source_fingerprints", "provenance", mode="before")
    @classmethod
    def collections(cls, value: object, info) -> tuple[object, ...]:
        return _tuple(value, field=info.field_name)

    @field_validator("evidence_ids")
    @classmethod
    def evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(_id(item, field="evidence_id") for item in value))

    @field_validator("source_references")
    @classmethod
    def refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(_ref(item, field="source_reference") for item in value))

    @field_validator("source_fingerprints")
    @classmethod
    def fps(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(_fp(item, field="source_fingerprint") for item in value))

    @field_validator("provenance")
    @classmethod
    def safe_items(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_text(item, field="provenance", maximum=512) for item in value)

    @field_validator("knowledge_fingerprint", mode="before")
    @classmethod
    def fp(cls, value: object) -> str:
        return _fp(value, field="knowledge_fingerprint")

    @field_validator("approved_at", "created_at", "updated_at", mode="before")
    @classmethod
    def timestamps(cls, value: object, info) -> datetime:
        return _utc(value, field=info.field_name)

    @model_validator(mode="after")
    def verify(self) -> ApprovedKnowledge:
        if self.knowledge_fingerprint != canonical_fingerprint(self, exclude={"knowledge_fingerprint", "status", "updated_at"}):
            raise ValueError("knowledge fingerprint mismatch")
        if self.status not in {KnowledgeStatus.APPROVED, KnowledgeStatus.ACTIVE, KnowledgeStatus.STALE, KnowledgeStatus.REVOKED}:
            raise ValueError("approved knowledge status is invalid")
        return self


class ReviewItem(V63Contract):
    review_id: str
    tenant_id: str
    project_id: str
    requester_id: str
    target_type: ReviewTargetType
    target_id: str
    target_fingerprint: str
    evidence_fingerprints: tuple[str, ...] = ()
    status: ReviewStatus = ReviewStatus.PENDING
    reviewer_id: str | None = None
    decision: Literal["APPROVE", "REJECT"] | None = None
    created_at: datetime
    decided_at: datetime | None = None
    fingerprint: str

    @field_validator("review_id", "tenant_id", "project_id", "requester_id", "target_id", mode="before")
    @classmethod
    def ids(cls, value: object, info) -> str:
        return _id(value, field=info.field_name)

    @field_validator("target_type", mode="before")
    @classmethod
    def target_enum(cls, value: object) -> ReviewTargetType:
        return _enum(value, ReviewTargetType, field="target_type")  # type: ignore[return-value]

    @field_validator("status", mode="before")
    @classmethod
    def status_enum(cls, value: object) -> ReviewStatus:
        return _enum(value, ReviewStatus, field="status")  # type: ignore[return-value]

    @field_validator("target_fingerprint", mode="before")
    @classmethod
    def target_fp(cls, value: object) -> str:
        return _fp(value, field="target_fingerprint")

    @field_validator("evidence_fingerprints", mode="before")
    @classmethod
    def evidence_fps(cls, value: object) -> tuple[str, ...]:
        return tuple(sorted(_fp(item, field="evidence_fingerprint") for item in _tuple(value, field="evidence_fingerprints")))

    @field_validator("reviewer_id", mode="before")
    @classmethod
    def reviewer(cls, value: object) -> str | None:
        return None if value is None else _id(value, field="reviewer_id")

    @field_validator("created_at", "decided_at", mode="before")
    @classmethod
    def timestamps(cls, value: object, info) -> datetime | None:
        return None if value is None else _utc(value, field=info.field_name)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def fp(cls, value: object) -> str:
        return _fp(value)

    @classmethod
    def create(cls, **values: object) -> ReviewItem:
        return _with_fingerprint(cls, values, "fingerprint")

    @model_validator(mode="after")
    def verify(self) -> ReviewItem:
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("review fingerprint mismatch")
        if self.status is ReviewStatus.PENDING and (self.decision is not None or self.reviewer_id is not None):
            raise ValueError("pending review decision is invalid")
        if self.status in {ReviewStatus.APPROVED, ReviewStatus.REJECTED} and (self.decision is None or self.reviewer_id is None or self.decided_at is None):
            raise ValueError("decided review binding is invalid")
        return self


class KnowledgeEvolutionProposal(V63Contract):
    proposal_id: str
    tenant_id: str
    project_id: str
    source_knowledge_id: str
    source_knowledge_fingerprint: str
    target: EvolutionTarget
    statement: str
    evidence_ids: tuple[str, ...]
    created_by: str
    created_at: datetime
    status: EvolutionStatus
    proposal_fingerprint: str
    review_id: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None

    @field_validator("proposal_id", "tenant_id", "project_id", "source_knowledge_id", "created_by", mode="before")
    @classmethod
    def ids(cls, value: object, info) -> str:
        return _id(value, field=info.field_name)

    @field_validator("target", mode="before")
    @classmethod
    def target_enum(cls, value: object) -> EvolutionTarget:
        return _enum(value, EvolutionTarget, field="target")  # type: ignore[return-value]

    @field_validator("status", mode="before")
    @classmethod
    def status_enum(cls, value: object) -> EvolutionStatus:
        return _enum(value, EvolutionStatus, field="status")  # type: ignore[return-value]

    @field_validator("source_knowledge_fingerprint", "proposal_fingerprint", mode="before")
    @classmethod
    def fps(cls, value: object, info) -> str:
        return _fp(value, field=info.field_name)

    @field_validator("statement", mode="before")
    @classmethod
    def statement(cls, value: object) -> str:
        return _text(value, field="statement")

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def evidence(cls, value: object) -> tuple[str, ...]:
        return tuple(sorted(_id(item, field="evidence_id") for item in _tuple(value, field="evidence_ids")))

    @field_validator("created_at", "approved_at", mode="before")
    @classmethod
    def timestamps(cls, value: object, info) -> datetime | None:
        return None if value is None else _utc(value, field=info.field_name)

    @field_validator("review_id", "approved_by", mode="before")
    @classmethod
    def optional_ids(cls, value: object, info) -> str | None:
        return None if value is None else _id(value, field=info.field_name)

    @classmethod
    def create(cls, **values: object) -> KnowledgeEvolutionProposal:
        values = {**values, "status": EvolutionStatus.CREATED}
        return _with_fingerprint(
            cls,
            values,
            "proposal_fingerprint",
            exclude={"status", "review_id", "approved_by", "approved_at"},
        )

    @model_validator(mode="after")
    def verify(self) -> KnowledgeEvolutionProposal:
        if self.proposal_fingerprint != canonical_fingerprint(self, exclude={"proposal_fingerprint", "status", "review_id", "approved_by", "approved_at"}):
            raise ValueError("proposal fingerprint mismatch")
        return self


class EvolutionApplyRequest(V63Contract):
    request_id: str
    tenant_id: str
    project_id: str
    proposal_id: str
    proposal_fingerprint: str
    review_id: str
    requested_by: str
    requested_at: datetime

    @field_validator("request_id", "tenant_id", "project_id", "proposal_id", "review_id", "requested_by", mode="before")
    @classmethod
    def ids(cls, value: object, info) -> str:
        return _id(value, field=info.field_name)

    @field_validator("proposal_fingerprint", mode="before")
    @classmethod
    def fp(cls, value: object) -> str:
        return _fp(value, field="proposal_fingerprint")

    @field_validator("requested_at", mode="before")
    @classmethod
    def timestamp(cls, value: object) -> datetime:
        return _utc(value, field="requested_at")


class MemoryProjection(V63Contract):
    memory_id: str
    tenant_id: str
    project_id: str
    memory_type: MemoryType
    subject: str
    statement: str
    status: MemoryStatus
    confidence: float
    evidence_count: int = Field(ge=0)
    review_id: str
    freshness: Freshness
    source_references: tuple[str, ...]
    fingerprint: str


class KnowledgeProjection(V63Contract):
    knowledge_id: str
    tenant_id: str
    project_id: str
    subject: str
    predicate: str
    value: str
    status: KnowledgeStatus
    verification_status: str
    evidence_count: int = Field(ge=0)
    review_id: str
    source_references: tuple[str, ...]
    fingerprint: str


class V63AuditEvent(V63Contract):
    event_id: str
    event_type: str
    tenant_id: str
    project_id: str
    object_id: str
    fingerprint: str
    evidence_ids: tuple[str, ...] = ()
    from_status: str | None = None
    to_status: str | None = None
    timestamp: datetime

    @field_validator("event_id", "event_type", "tenant_id", "project_id", "object_id", mode="before")
    @classmethod
    def ids(cls, value: object, info) -> str:
        return _id(value, field=info.field_name)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def fp(cls, value: object) -> str:
        return _fp(value)

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def evidence(cls, value: object) -> tuple[str, ...]:
        return tuple(sorted(_id(item, field="evidence_id") for item in _tuple(value, field="evidence_ids")))

    @field_validator("from_status", "to_status", mode="before")
    @classmethod
    def optional_status(cls, value: object, info) -> str | None:
        return None if value is None else _id(value, field=info.field_name)

    @field_validator("timestamp", mode="before")
    @classmethod
    def timestamp(cls, value: object) -> datetime:
        return _utc(value, field="timestamp")


__all__ = [name for name in globals() if not name.startswith("_")]
