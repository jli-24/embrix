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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/?&=+-]{0,511}$")
_FINGERPRINT = re.compile(r"^sha256:[a-f0-9]{64}$")
_SECRET = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|bearer|password|credential|secret|token)\s*[:=]"
    r"|raw\s+(?:llm\s+)?output|hidden\s+reasoning|chain\s+of\s+thought|"
    r"(?:^|\s)[A-Za-z]:[\\/]|\\\\|file://|\x00|\r|\n",
    re.IGNORECASE,
)


class AuditContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class AuditEventType(StrEnum):
    REVIEW_CREATED = "REVIEW_CREATED"
    REVIEW_APPROVED = "REVIEW_APPROVED"
    REVIEW_REJECTED = "REVIEW_REJECTED"
    REVIEW_DEFERRED = "REVIEW_DEFERRED"
    RISK_IDENTIFIED = "RISK_IDENTIFIED"
    RISK_REMAINED = "RISK_REMAINED"


class AuditSourceType(StrEnum):
    KNOWLEDGE = "KNOWLEDGE"
    VALIDATION = "VALIDATION"
    OPTIMIZATION = "OPTIMIZATION"
    EVOLUTION = "EVOLUTION"


class AuditDecision(StrEnum):
    PENDING = "PENDING"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_CHANGE = "REQUEST_CHANGE"
    RISK_IDENTIFIED = "RISK_IDENTIFIED"
    RISK_REMAINED = "RISK_REMAINED"


class EvidenceCoverageStatus(StrEnum):
    COMPLETE = "COMPLETE"
    MISSING = "MISSING"


class AuditReportStatus(StrEnum):
    COMPLETE = "COMPLETE"
    AUDIT_INCOMPLETE = "AUDIT_INCOMPLETE"


class UnresolvedItemType(StrEnum):
    PENDING_REVIEW = "PENDING_REVIEW"
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    PERSISTENT_RISK = "PERSISTENT_RISK"


ModelT = TypeVar("ModelT", bound=BaseModel)


def safe_text(value: object, *, field: str, maximum: int = 2048) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = unicodedata.normalize("NFC", value).strip()
    if not checked or len(checked) > maximum or _SECRET.search(checked):
        raise ValueError(f"{field} is unsafe")
    return checked


def identifier(value: object, *, field: str) -> str:
    checked = safe_text(value, field=field, maximum=128)
    if not checked[0].isalnum() or any(
        not (character.isalnum() or character in "._:#/+-")
        for character in checked
    ):
        raise ValueError(f"{field} is invalid")
    return checked


def reference(value: object, *, field: str) -> str:
    checked = safe_text(value, field=field, maximum=512)
    if _REFERENCE.fullmatch(checked) is None:
        raise ValueError(f"{field} is invalid")
    return checked


def fingerprint(value: object, *, field: str = "fingerprint") -> str:
    if not isinstance(value, str) or _FINGERPRINT.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def utc_timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must be UTC")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be UTC")
    return value.astimezone(UTC)


def tuple_only(value: object, *, field: str) -> tuple[object, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{field} must be a tuple")
    return copy.deepcopy(value)


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


def project_audit_id(project_id: object) -> str:
    checked = identifier(project_id, field="project_id")
    return "audit-" + hashlib.sha256(checked.encode("utf-8")).hexdigest()[:48]


def _with_fingerprint(cls: type[ModelT], values: dict[str, object]) -> ModelT:
    material = dict(values)
    material.pop("fingerprint", None)
    provisional = cls.model_construct(
        **{**material, "fingerprint": "sha256:" + "0" * 64}
    )
    material["fingerprint"] = canonical_fingerprint(
        provisional, exclude={"fingerprint"}
    )
    return cls.model_validate(material)


def _nested(value: object, cls: type[ModelT], *, field: str) -> ModelT:
    if type(value) is cls:
        material = copy.deepcopy(value.model_dump(mode="python"))
    elif type(value) is dict:
        material = copy.deepcopy(value)
    else:
        raise TypeError(f"{field} is invalid")
    return cls.model_validate(material)


def _reference_tuple(
    value: object, *, field: str, allow_empty: bool = False
) -> tuple[str, ...]:
    checked = tuple(
        reference(item, field=field) for item in tuple_only(value, field=field)
    )
    if (not allow_empty and not checked) or len(checked) != len(set(checked)):
        raise ValueError(f"{field} must be unique and non-empty")
    return tuple(sorted(checked))


class ReviewRecordReference(AuditContract):
    review_id: str
    fingerprint: str

    @field_validator("review_id", mode="before")
    @classmethod
    def validate_review_id(cls, value: object) -> str:
        return identifier(value, field="review_id")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @classmethod
    def create(cls, **values: object) -> ReviewRecordReference:
        return cls.model_validate(
            {
                "review_id": identifier(values["review_id"], field="review_id"),
                "fingerprint": fingerprint(values["fingerprint"]),
            }
        )


class EvidenceBundle(AuditContract):
    project_id: str
    source_type: AuditSourceType
    source_id: str
    source_fingerprint: str
    evidence_refs: tuple[str, ...]
    status: EvidenceCoverageStatus
    fingerprint: str

    @field_validator("project_id", "source_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("source_fingerprint", "fingerprint", mode="before")
    @classmethod
    def validate_fingerprints(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def validate_evidence_refs(cls, value: object) -> tuple[str, ...]:
        return _reference_tuple(value, field="evidence_refs", allow_empty=True)

    @classmethod
    def create(cls, **values: object) -> EvidenceBundle:
        material = dict(values)
        material["project_id"] = identifier(material["project_id"], field="project_id")
        material["source_type"] = AuditSourceType(material["source_type"])
        material["source_id"] = identifier(material["source_id"], field="source_id")
        material["source_fingerprint"] = fingerprint(
            material["source_fingerprint"], field="source_fingerprint"
        )
        material["evidence_refs"] = _reference_tuple(
            material["evidence_refs"], field="evidence_refs", allow_empty=True
        )
        material["status"] = EvidenceCoverageStatus(material["status"])
        return _with_fingerprint(cls, material)

    @model_validator(mode="after")
    def verify(self) -> EvidenceBundle:
        if (self.status is EvidenceCoverageStatus.COMPLETE) != bool(
            self.evidence_refs
        ):
            raise ValueError("evidence coverage is inconsistent")
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("evidence bundle fingerprint mismatch")
        return self


_DECISION_BY_EVENT = {
    AuditEventType.REVIEW_CREATED: AuditDecision.PENDING,
    AuditEventType.REVIEW_APPROVED: AuditDecision.APPROVE,
    AuditEventType.REVIEW_REJECTED: AuditDecision.REJECT,
    AuditEventType.REVIEW_DEFERRED: AuditDecision.REQUEST_CHANGE,
    AuditEventType.RISK_IDENTIFIED: AuditDecision.RISK_IDENTIFIED,
    AuditEventType.RISK_REMAINED: AuditDecision.RISK_REMAINED,
}


class AuditEvent(AuditContract):
    event_id: str
    audit_id: str
    project_id: str
    event_type: AuditEventType
    source_type: AuditSourceType
    source_id: str
    review_reference: ReviewRecordReference
    actor_id: str
    decision: AuditDecision
    reason: str
    evidence_refs: tuple[str, ...]
    evidence_status: EvidenceCoverageStatus
    timestamp: datetime
    fingerprint: str

    @field_validator(
        "event_id", "audit_id", "project_id", "source_id", "actor_id", mode="before"
    )
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("review_reference", mode="before")
    @classmethod
    def validate_review_reference(cls, value: object) -> ReviewRecordReference:
        return _nested(value, ReviewRecordReference, field="review_reference")

    @field_validator("reason", mode="before")
    @classmethod
    def validate_reason(cls, value: object) -> str:
        return safe_text(value, field="reason")

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def validate_evidence_refs(cls, value: object) -> tuple[str, ...]:
        return _reference_tuple(value, field="evidence_refs")

    @field_validator("timestamp", mode="before")
    @classmethod
    def validate_timestamp(cls, value: object) -> datetime:
        return utc_timestamp(value, field="timestamp")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @classmethod
    def create(cls, **values: object) -> AuditEvent:
        material = dict(values)
        for field in ("event_id", "audit_id", "project_id", "source_id", "actor_id"):
            material[field] = identifier(material[field], field=field)
        material["event_type"] = AuditEventType(material["event_type"])
        material["source_type"] = AuditSourceType(material["source_type"])
        material["review_reference"] = _nested(
            material["review_reference"], ReviewRecordReference, field="review_reference"
        )
        material["decision"] = AuditDecision(material["decision"])
        material["reason"] = safe_text(material["reason"], field="reason")
        material["evidence_refs"] = _reference_tuple(
            material["evidence_refs"], field="evidence_refs"
        )
        material["evidence_status"] = EvidenceCoverageStatus(
            material["evidence_status"]
        )
        material["timestamp"] = utc_timestamp(material["timestamp"], field="timestamp")
        return _with_fingerprint(cls, material)

    @model_validator(mode="after")
    def verify(self) -> AuditEvent:
        if self.decision is not _DECISION_BY_EVENT[self.event_type]:
            raise ValueError("audit event decision mismatch")
        if self.audit_id != project_audit_id(self.project_id):
            raise ValueError("audit event project identity mismatch")
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("audit event fingerprint mismatch")
        return self


_DECISION_EVENTS = frozenset(
    {
        AuditEventType.REVIEW_APPROVED,
        AuditEventType.REVIEW_REJECTED,
        AuditEventType.REVIEW_DEFERRED,
    }
)
_RISK_EVENTS = frozenset(
    {AuditEventType.RISK_IDENTIFIED, AuditEventType.RISK_REMAINED}
)


def _event_tuple(value: object) -> tuple[AuditEvent, ...]:
    events = tuple(
        _nested(item, AuditEvent, field="event")
        for item in tuple_only(value, field="events")
    )
    if not events:
        raise ValueError("events must be non-empty")
    return events


class AuditTrail(AuditContract):
    audit_id: str
    project_id: str
    events: tuple[AuditEvent, ...]
    created_at: datetime
    fingerprint: str

    @field_validator("audit_id", "project_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("events", mode="before")
    @classmethod
    def validate_events(cls, value: object) -> tuple[AuditEvent, ...]:
        return _event_tuple(value)

    @field_validator("created_at", mode="before")
    @classmethod
    def validate_created_at(cls, value: object) -> datetime:
        return utc_timestamp(value, field="created_at")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @classmethod
    def create(cls, **values: object) -> AuditTrail:
        material = dict(values)
        material["audit_id"] = identifier(material["audit_id"], field="audit_id")
        material["project_id"] = identifier(material["project_id"], field="project_id")
        material["events"] = _event_tuple(material["events"])
        material["created_at"] = utc_timestamp(
            material["created_at"], field="created_at"
        )
        return _with_fingerprint(cls, material)

    def append_event(self, event: AuditEvent) -> AuditTrail:
        checked = _nested(event, AuditEvent, field="event")
        for existing in self.events:
            if existing.event_id == checked.event_id:
                if existing == checked:
                    return AuditTrail.model_validate(
                        copy.deepcopy(self.model_dump(mode="python"))
                    )
                raise ValueError("audit event identity conflict")
        return AuditTrail.create(
            audit_id=self.audit_id,
            project_id=self.project_id,
            events=(*self.events, checked),
            created_at=self.created_at,
        )

    @model_validator(mode="after")
    def verify(self) -> AuditTrail:
        if self.audit_id != project_audit_id(self.project_id):
            raise ValueError("audit trail project identity mismatch")
        if self.created_at != self.events[0].timestamp:
            raise ValueError("audit trail created_at mismatch")
        if self.events[0].event_type is not AuditEventType.REVIEW_CREATED:
            raise ValueError("audit trail must start with review creation")
        ids = tuple(event.event_id for event in self.events)
        fingerprints = tuple(event.fingerprint for event in self.events)
        if len(ids) != len(set(ids)) or len(fingerprints) != len(set(fingerprints)):
            raise ValueError("audit trail events must be unique")
        created_reviews: set[str] = set()
        decided_reviews: set[str] = set()
        previous: datetime | None = None
        for event in self.events:
            if event.audit_id != self.audit_id or event.project_id != self.project_id:
                raise ValueError("audit trail event binding mismatch")
            if previous is not None and event.timestamp < previous:
                raise ValueError("audit trail timestamp regression")
            previous = event.timestamp
            review_id = event.review_reference.review_id
            if event.event_type is AuditEventType.REVIEW_CREATED:
                if review_id in created_reviews:
                    raise ValueError("duplicate review creation event")
                created_reviews.add(review_id)
            elif event.event_type in _DECISION_EVENTS:
                if review_id not in created_reviews or review_id in decided_reviews:
                    raise ValueError("review decision lifecycle mismatch")
                decided_reviews.add(review_id)
            elif event.event_type in _RISK_EVENTS and review_id not in decided_reviews:
                raise ValueError("risk event requires review decision")
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("audit trail fingerprint mismatch")
        return self


class RiskAuditRequest(AuditContract):
    request_id: str
    review_reference: ReviewRecordReference
    event_type: AuditEventType
    actor_id: str
    reason: str
    timestamp: datetime
    fingerprint: str

    @field_validator("request_id", "actor_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("review_reference", mode="before")
    @classmethod
    def validate_review_reference(cls, value: object) -> ReviewRecordReference:
        return _nested(value, ReviewRecordReference, field="review_reference")

    @field_validator("reason", mode="before")
    @classmethod
    def validate_reason(cls, value: object) -> str:
        return safe_text(value, field="reason")

    @field_validator("timestamp", mode="before")
    @classmethod
    def validate_timestamp(cls, value: object) -> datetime:
        return utc_timestamp(value, field="timestamp")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @classmethod
    def create(cls, **values: object) -> RiskAuditRequest:
        material = dict(values)
        material["request_id"] = identifier(material["request_id"], field="request_id")
        material["review_reference"] = _nested(
            material["review_reference"], ReviewRecordReference, field="review_reference"
        )
        material["event_type"] = AuditEventType(material["event_type"])
        material["actor_id"] = identifier(material["actor_id"], field="actor_id")
        material["reason"] = safe_text(material["reason"], field="reason")
        material["timestamp"] = utc_timestamp(material["timestamp"], field="timestamp")
        return _with_fingerprint(cls, material)

    @model_validator(mode="after")
    def verify(self) -> RiskAuditRequest:
        if self.event_type not in _RISK_EVENTS:
            raise ValueError("risk request event type is invalid")
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("risk request fingerprint mismatch")
        return self


class ReviewSummary(AuditContract):
    total_reviews: int = Field(ge=0)
    approved: int = Field(ge=0)
    rejected: int = Field(ge=0)
    deferred: int = Field(ge=0)
    pending: int = Field(ge=0)

    @model_validator(mode="after")
    def verify(self) -> ReviewSummary:
        if self.total_reviews != self.approved + self.rejected + self.deferred + self.pending:
            raise ValueError("review summary counts are inconsistent")
        return self


class EvidenceSummary(AuditContract):
    total_events: int = Field(ge=0)
    covered_events: int = Field(ge=0)
    missing_events: int = Field(ge=0)
    coverage_ratio: float = Field(ge=0.0, le=1.0)
    status: EvidenceCoverageStatus

    @field_validator("coverage_ratio")
    @classmethod
    def validate_ratio(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("coverage ratio must be finite")
        return value

    @model_validator(mode="after")
    def verify(self) -> EvidenceSummary:
        if self.total_events != self.covered_events + self.missing_events:
            raise ValueError("evidence summary counts are inconsistent")
        expected = 1.0 if self.total_events == 0 else self.covered_events / self.total_events
        if not math.isclose(self.coverage_ratio, expected, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("evidence coverage ratio is inconsistent")
        complete = self.missing_events == 0
        if (self.status is EvidenceCoverageStatus.COMPLETE) != complete:
            raise ValueError("evidence summary status is inconsistent")
        return self


class PersistentRisk(AuditContract):
    risk_id: str
    source_type: AuditSourceType
    source_id: str
    review_cycles: int = Field(ge=2)
    first_seen_at: datetime
    last_seen_at: datetime
    reason: str
    evidence_refs: tuple[str, ...]
    fingerprint: str

    @field_validator("risk_id", "source_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("first_seen_at", "last_seen_at", mode="before")
    @classmethod
    def validate_times(cls, value: object, info) -> datetime:
        return utc_timestamp(value, field=info.field_name)

    @field_validator("reason", mode="before")
    @classmethod
    def validate_reason(cls, value: object) -> str:
        return safe_text(value, field="reason")

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def validate_evidence_refs(cls, value: object) -> tuple[str, ...]:
        return _reference_tuple(value, field="evidence_refs")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @classmethod
    def create(cls, **values: object) -> PersistentRisk:
        material = dict(values)
        material["risk_id"] = identifier(material["risk_id"], field="risk_id")
        material["source_type"] = AuditSourceType(material["source_type"])
        material["source_id"] = identifier(material["source_id"], field="source_id")
        material["first_seen_at"] = utc_timestamp(material["first_seen_at"], field="first_seen_at")
        material["last_seen_at"] = utc_timestamp(material["last_seen_at"], field="last_seen_at")
        material["reason"] = safe_text(material["reason"], field="reason")
        material["evidence_refs"] = _reference_tuple(material["evidence_refs"], field="evidence_refs")
        return _with_fingerprint(cls, material)

    @model_validator(mode="after")
    def verify(self) -> PersistentRisk:
        if self.last_seen_at < self.first_seen_at:
            raise ValueError("persistent risk timestamp regression")
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("persistent risk fingerprint mismatch")
        return self


def _risk_tuple(value: object) -> tuple[PersistentRisk, ...]:
    risks = tuple(
        _nested(item, PersistentRisk, field="persistent_risk")
        for item in tuple_only(value, field="persistent_risks")
    )
    if len({item.risk_id for item in risks}) != len(risks):
        raise ValueError("persistent risks must be unique")
    return tuple(sorted(risks, key=lambda item: item.risk_id))


class RiskSummary(AuditContract):
    identified_events: int = Field(ge=0)
    remained_events: int = Field(ge=0)
    persistent_risks: tuple[PersistentRisk, ...]

    @field_validator("persistent_risks", mode="before")
    @classmethod
    def validate_risks(cls, value: object) -> tuple[PersistentRisk, ...]:
        return _risk_tuple(value)


class UnresolvedItem(AuditContract):
    item_id: str
    item_type: UnresolvedItemType
    source_type: AuditSourceType
    source_id: str
    reason: str
    evidence_refs: tuple[str, ...]
    fingerprint: str

    @field_validator("item_id", "source_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("reason", mode="before")
    @classmethod
    def validate_reason(cls, value: object) -> str:
        return safe_text(value, field="reason")

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def validate_evidence_refs(cls, value: object) -> tuple[str, ...]:
        return _reference_tuple(value, field="evidence_refs")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @classmethod
    def create(cls, **values: object) -> UnresolvedItem:
        material = dict(values)
        material["item_id"] = identifier(material["item_id"], field="item_id")
        material["item_type"] = UnresolvedItemType(material["item_type"])
        material["source_type"] = AuditSourceType(material["source_type"])
        material["source_id"] = identifier(material["source_id"], field="source_id")
        material["reason"] = safe_text(material["reason"], field="reason")
        material["evidence_refs"] = _reference_tuple(material["evidence_refs"], field="evidence_refs")
        return _with_fingerprint(cls, material)

    @model_validator(mode="after")
    def verify(self) -> UnresolvedItem:
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("unresolved item fingerprint mismatch")
        return self


def _unresolved_tuple(value: object) -> tuple[UnresolvedItem, ...]:
    items = tuple(
        _nested(item, UnresolvedItem, field="unresolved_item")
        for item in tuple_only(value, field="unresolved_items")
    )
    if len({item.item_id for item in items}) != len(items):
        raise ValueError("unresolved items must be unique")
    return tuple(sorted(items, key=lambda item: item.item_id))


def report_id(audit_id: object, trail_fingerprint: object, generated_at: object) -> str:
    audit = identifier(audit_id, field="audit_id")
    trail = fingerprint(trail_fingerprint, field="audit_trail_fingerprint")
    generated = utc_timestamp(generated_at, field="generated_at")
    material = f"{audit}|{trail}|{generated.isoformat()}".encode()
    return "report-" + hashlib.sha256(material).hexdigest()[:48]


class EngineeringAuditReport(AuditContract):
    report_id: str
    audit_id: str
    project_id: str
    audit_trail_fingerprint: str
    generated_at: datetime
    review_summary: ReviewSummary
    evidence_summary: EvidenceSummary
    risk_summary: RiskSummary
    unresolved_items: tuple[UnresolvedItem, ...]
    status: AuditReportStatus
    fingerprint: str

    @field_validator("report_id", "audit_id", "project_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("audit_trail_fingerprint", "fingerprint", mode="before")
    @classmethod
    def validate_fingerprints(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("generated_at", mode="before")
    @classmethod
    def validate_generated_at(cls, value: object) -> datetime:
        return utc_timestamp(value, field="generated_at")

    @field_validator("review_summary", mode="before")
    @classmethod
    def validate_review_summary(cls, value: object) -> ReviewSummary:
        return _nested(value, ReviewSummary, field="review_summary")

    @field_validator("evidence_summary", mode="before")
    @classmethod
    def validate_evidence_summary(cls, value: object) -> EvidenceSummary:
        return _nested(value, EvidenceSummary, field="evidence_summary")

    @field_validator("risk_summary", mode="before")
    @classmethod
    def validate_risk_summary(cls, value: object) -> RiskSummary:
        return _nested(value, RiskSummary, field="risk_summary")

    @field_validator("unresolved_items", mode="before")
    @classmethod
    def validate_unresolved_items(cls, value: object) -> tuple[UnresolvedItem, ...]:
        return _unresolved_tuple(value)

    @classmethod
    def create(cls, **values: object) -> EngineeringAuditReport:
        material = dict(values)
        material["audit_id"] = identifier(material["audit_id"], field="audit_id")
        material["project_id"] = identifier(material["project_id"], field="project_id")
        material["audit_trail_fingerprint"] = fingerprint(
            material["audit_trail_fingerprint"], field="audit_trail_fingerprint"
        )
        material["generated_at"] = utc_timestamp(material["generated_at"], field="generated_at")
        material["report_id"] = report_id(
            material["audit_id"], material["audit_trail_fingerprint"], material["generated_at"]
        )
        material["review_summary"] = _nested(material["review_summary"], ReviewSummary, field="review_summary")
        material["evidence_summary"] = _nested(material["evidence_summary"], EvidenceSummary, field="evidence_summary")
        material["risk_summary"] = _nested(material["risk_summary"], RiskSummary, field="risk_summary")
        material["unresolved_items"] = _unresolved_tuple(material["unresolved_items"])
        material["status"] = AuditReportStatus(material["status"])
        return _with_fingerprint(cls, material)

    @model_validator(mode="after")
    def verify(self) -> EngineeringAuditReport:
        expected_status = (
            AuditReportStatus.COMPLETE
            if not self.unresolved_items
            and self.review_summary.pending == 0
            and self.evidence_summary.status is EvidenceCoverageStatus.COMPLETE
            and not self.risk_summary.persistent_risks
            else AuditReportStatus.AUDIT_INCOMPLETE
        )
        if self.status is not expected_status:
            raise ValueError("audit report status is inconsistent")
        if self.report_id != report_id(self.audit_id, self.audit_trail_fingerprint, self.generated_at):
            raise ValueError("audit report identity mismatch")
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("audit report fingerprint mismatch")
        return self
