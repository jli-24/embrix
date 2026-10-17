from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_AUTO = "sha256:" + "0" * 64
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#-]{0,127}$")


class RequirementContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class RequirementLifecycle(StrEnum):
    DISCOVERY_STARTED = "DISCOVERY_STARTED"
    REQUIREMENTS_PARTIAL = "REQUIREMENTS_PARTIAL"
    REQUIREMENTS_REFINING = "REQUIREMENTS_REFINING"
    REQUIREMENTS_READY = "REQUIREMENTS_READY"
    DESIGN_READY = "DESIGN_READY"


class RequirementItemStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    PROPOSED = "PROPOSED"
    UNKNOWN = "UNKNOWN"
    SUPERSEDED = "SUPERSEDED"
    REMOVED_BY_USER = "REMOVED_BY_USER"


class RequirementPriority(StrEnum):
    MUST = "MUST"
    SHOULD = "SHOULD"
    COULD = "COULD"
    WONT = "WONT"


class RequirementStatus(StrEnum):
    DRAFT = "DRAFT"
    ANALYZING = "ANALYZING"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    CONFIRMED = "CONFIRMED"
    ARCHIVED = "ARCHIVED"


class RequirementConflictSeverity(StrEnum):
    BLOCKING = "BLOCKING"
    NON_BLOCKING = "NON_BLOCKING"


class RequirementConflictStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class RequirementProvenance(StrEnum):
    USER_UTTERANCE = "USER_UTTERANCE"
    PROVIDER_RESPONSE = "PROVIDER_RESPONSE"
    PROJECT_METADATA = "PROJECT_METADATA"
    SYSTEM = "SYSTEM"


def _identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value.strip()):
        raise ValueError(f"{field} is invalid")
    return value.strip()


def _text(value: object, *, field: str, maximum: int = 512) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = value.strip()
    if not checked or len(checked) > maximum or "\x00" in checked or "\r" in checked or "\n" in checked:
        raise ValueError(f"{field} is invalid")
    return checked


def _fingerprint(value: BaseModel, *, exclude: set[str]) -> str:
    payload = json.dumps(
        value.model_dump(mode="json", exclude=exclude),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _payload_fingerprint(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class StandardRequirementField(RequirementContract):
    category: str
    value: str | None = None
    status: RequirementItemStatus = RequirementItemStatus.UNKNOWN
    priority: RequirementPriority = RequirementPriority.SHOULD
    source_turn_id: str | None = None
    provenance: RequirementProvenance = RequirementProvenance.SYSTEM

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, value: object) -> str:
        return _text(value, field="requirement category", maximum=96)

    @field_validator("value", mode="before")
    @classmethod
    def _value(cls, value: object) -> str | None:
        return None if value is None else _text(value, field="requirement value")

    @field_validator("source_turn_id", mode="before")
    @classmethod
    def _source_turn(cls, value: object) -> str | None:
        return None if value is None else _identifier(value, field="source_turn_id")

    @model_validator(mode="after")
    def _status_binding(self) -> StandardRequirementField:
        if self.status is RequirementItemStatus.UNKNOWN and self.value is not None:
            raise ValueError("unknown requirement field cannot have a value")
        if self.status is not RequirementItemStatus.UNKNOWN and self.value is None:
            raise ValueError("known requirement field requires a value")
        return self


class RequirementItem(RequirementContract):
    requirement_id: str
    project_id: str
    conversation_id: str
    category: str
    normalized_value: str
    status: RequirementItemStatus
    priority: RequirementPriority = RequirementPriority.SHOULD
    source_turn_id: str
    provenance: RequirementProvenance
    supersedes: str | None = None
    superseded_by: str | None = None
    fingerprint: str = _AUTO

    @field_validator("requirement_id", "project_id", "conversation_id", "source_turn_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("category", "normalized_value", mode="before")
    @classmethod
    def _text_fields(cls, value: object, info) -> str:
        return _text(value, field=info.field_name)

    @field_validator("supersedes", "superseded_by", mode="before")
    @classmethod
    def _supersedes(cls, value: object, info) -> str | None:
        return None if value is None else _identifier(value, field=info.field_name)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        if value == _AUTO:
            return _AUTO
        if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise ValueError("requirement fingerprint is invalid")
        return value

    @model_validator(mode="after")
    def _verify(self) -> RequirementItem:
        expected = _fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            legacy = self.model_dump(mode="json", exclude={"fingerprint", "priority"})
            if self.priority is not RequirementPriority.SHOULD or self.fingerprint != _payload_fingerprint(legacy):
                raise ValueError("requirement fingerprint mismatch")
        if self.status is RequirementItemStatus.SUPERSEDED and self.superseded_by is None:
            raise ValueError("superseded requirement must identify its replacement")
        if self.status is not RequirementItemStatus.SUPERSEDED and self.superseded_by is not None:
            raise ValueError("only superseded requirements may identify a replacement")
        return self


class OpenQuestion(RequirementContract):
    question_id: str
    category: str
    question: str
    source_turn_id: str
    provenance: RequirementProvenance
    fingerprint: str = _AUTO

    @field_validator("question_id", "source_turn_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("category", "question", mode="before")
    @classmethod
    def _text_fields(cls, value: object, info) -> str:
        return _text(value, field=info.field_name, maximum=1024)

    @model_validator(mode="after")
    def _verify(self) -> OpenQuestion:
        expected = _fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("open question fingerprint mismatch")
        return self


class AcceptanceCriterion(RequirementContract):
    criterion_id: str
    project_id: str
    conversation_id: str
    requirement_id: str | None = None
    given: str
    when: str
    then: str
    verification: str
    status: RequirementItemStatus = RequirementItemStatus.PROPOSED
    source_turn_id: str
    provenance: RequirementProvenance
    fingerprint: str = _AUTO

    @field_validator(
        "criterion_id", "project_id", "conversation_id", "source_turn_id", mode="before"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("requirement_id", mode="before")
    @classmethod
    def _optional_requirement_id(cls, value: object) -> str | None:
        return None if value is None else _identifier(value, field="requirement_id")

    @field_validator("given", "when", "then", "verification", mode="before")
    @classmethod
    def _text_fields(cls, value: object, info) -> str:
        return _text(value, field=info.field_name, maximum=1024)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        if value == _AUTO:
            return _AUTO
        if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise ValueError("acceptance criterion fingerprint is invalid")
        return value

    @model_validator(mode="after")
    def _verify(self) -> AcceptanceCriterion:
        expected = _fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("acceptance criterion fingerprint mismatch")
        return self


class RequirementConflict(RequirementContract):
    conflict_id: str
    project_id: str
    conversation_id: str
    requirement_ids: tuple[str, ...]
    description: str
    severity: RequirementConflictSeverity
    status: RequirementConflictStatus = RequirementConflictStatus.OPEN
    resolution: str | None = None
    source_turn_id: str
    provenance: RequirementProvenance
    fingerprint: str = _AUTO

    @field_validator(
        "conflict_id", "project_id", "conversation_id", "source_turn_id", mode="before"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("requirement_ids", mode="before")
    @classmethod
    def _requirement_ids(cls, value: object) -> tuple[str, ...]:
        if type(value) not in {tuple, list}:
            raise TypeError("requirement_ids must be a tuple or list")
        checked = tuple(_identifier(item, field="requirement_id") for item in value)
        if len(checked) < 2 or len(checked) != len(set(checked)):
            raise ValueError("conflict requirement ids are invalid")
        return checked

    @field_validator("description", mode="before")
    @classmethod
    def _description(cls, value: object) -> str:
        return _text(value, field="conflict description", maximum=1024)

    @field_validator("resolution", mode="before")
    @classmethod
    def _resolution(cls, value: object) -> str | None:
        return None if value is None else _text(value, field="conflict resolution", maximum=1024)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        if value == _AUTO:
            return _AUTO
        if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            raise ValueError("requirement conflict fingerprint is invalid")
        return value

    @model_validator(mode="after")
    def _verify(self) -> RequirementConflict:
        if self.status is RequirementConflictStatus.RESOLVED and self.resolution is None:
            raise ValueError("resolved conflict requires a resolution")
        if self.status is RequirementConflictStatus.OPEN and self.resolution is not None:
            raise ValueError("open conflict cannot have a resolution")
        expected = _fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("requirement conflict fingerprint mismatch")
        return self


class RequirementContext(RequirementContract):
    project_id: str
    conversation_id: str
    standard_fields: tuple[StandardRequirementField, ...] = ()
    requirements: tuple[RequirementItem, ...] = ()
    constraints: tuple[RequirementItem, ...] = ()
    proposed_items: tuple[RequirementItem, ...] = ()
    open_questions: tuple[OpenQuestion, ...] = ()
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = ()
    conflicts: tuple[RequirementConflict, ...] = ()
    status: RequirementStatus = RequirementStatus.DRAFT
    lifecycle: RequirementLifecycle = RequirementLifecycle.DISCOVERY_STARTED
    fingerprint: str = _AUTO

    @field_validator("project_id", "conversation_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator(
        "standard_fields",
        "requirements",
        "constraints",
        "proposed_items",
        "open_questions",
        "acceptance_criteria",
        "conflicts",
        mode="before",
    )
    @classmethod
    def _tuples(cls, value: object, info):
        if type(value) not in {tuple, list}:
            raise TypeError(f"{info.field_name} must be a tuple or list")
        return tuple(value)

    @model_validator(mode="after")
    def _verify(self) -> RequirementContext:
        ids = tuple(item.requirement_id for item in (*self.requirements, *self.constraints, *self.proposed_items))
        if len(ids) != len(set(ids)):
            raise ValueError("requirement ids must be unique")
        question_ids = tuple(item.question_id for item in self.open_questions)
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("open question ids must be unique")
        criterion_ids = tuple(item.criterion_id for item in self.acceptance_criteria)
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("acceptance criterion ids must be unique")
        conflict_ids = tuple(item.conflict_id for item in self.conflicts)
        if len(conflict_ids) != len(set(conflict_ids)):
            raise ValueError("requirement conflict ids must be unique")
        for item in (*self.requirements, *self.constraints, *self.proposed_items):
            if item.project_id != self.project_id or item.conversation_id != self.conversation_id:
                raise ValueError("requirement item identity mismatch")
        for item in (*self.acceptance_criteria, *self.conflicts):
            if item.project_id != self.project_id or item.conversation_id != self.conversation_id:
                raise ValueError("requirement analysis identity mismatch")
        expected = _fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            legacy_allowed = (
                self.status is RequirementStatus.DRAFT
                and not self.acceptance_criteria
                and not self.conflicts
                and all(
                    item.priority is RequirementPriority.SHOULD
                    for item in (*self.requirements, *self.constraints, *self.proposed_items)
                )
                and all(item.priority is RequirementPriority.SHOULD for item in self.standard_fields)
            )
            if legacy_allowed:
                legacy = self.model_dump(
                    mode="json",
                    exclude={"fingerprint", "status", "acceptance_criteria", "conflicts"},
                )
                for field in ("standard_fields", "requirements", "constraints", "proposed_items"):
                    for item in legacy[field]:
                        item.pop("priority", None)
                if self.fingerprint != _payload_fingerprint(legacy):
                    raise ValueError("requirement context fingerprint mismatch")
            else:
                raise ValueError("requirement context fingerprint mismatch")
        return self

    @property
    def effective_status(self) -> RequirementStatus:
        """Return product-facing status while keeping old lifecycle records readable."""

        if self.status is not RequirementStatus.DRAFT:
            return self.status
        if self.lifecycle is RequirementLifecycle.DESIGN_READY:
            return RequirementStatus.CONFIRMED
        if self.lifecycle is RequirementLifecycle.REQUIREMENTS_READY:
            return RequirementStatus.CONFIRMED
        if self.lifecycle is RequirementLifecycle.REQUIREMENTS_REFINING:
            return RequirementStatus.ANALYZING
        return self.status


__all__ = (
    "OpenQuestion",
    "RequirementContext",
    "RequirementContract",
    "RequirementItem",
    "RequirementItemStatus",
    "RequirementPriority",
    "RequirementStatus",
    "RequirementConflict",
    "RequirementConflictSeverity",
    "RequirementConflictStatus",
    "RequirementLifecycle",
    "RequirementProvenance",
    "StandardRequirementField",
    "AcceptanceCriterion",
)
