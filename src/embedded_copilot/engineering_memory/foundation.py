from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/-]{0,127}$")
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+\-]{0,255}$")
_FINGERPRINT = re.compile(r"^sha256:[a-f0-9]{64}$")
_ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|file://)", re.IGNORECASE)
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|authorization|credential|password|secret|token)"
    r"\s*[:=]\s*\S+|bearer\s+\S+",
    re.IGNORECASE,
)
_RAW_CONTENT = re.compile(
    r"(?:^|\s)raw\s+(?:stdout|stderr|provider\s+response|prompt)\b"
    r"|(?:^|\s)(?:stdout|stderr|provider\s+response|prompt)\s*(?:[:=]|\bfrom\b)",
    re.IGNORECASE,
)
_RAW_REFERENCE = re.compile(
    r"^(?:raw[-_ ]?)?(?:stdout|stderr|provider[-_ ]?response|prompt)(?:[:/#._-]|$)",
    re.IGNORECASE,
)
_MAX_SOURCE_REFERENCES = 64


class _FoundationContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
        strict=True,
    )


class FoundationMemoryType(StrEnum):
    PROJECT = "PROJECT"
    DECISION = "DECISION"
    DEBUG = "DEBUG"
    EVIDENCE = "EVIDENCE"


def _text(value: object, *, field: str, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = unicodedata.normalize("NFC", value).strip()
    if (
        (not checked and not allow_empty)
        or len(checked) > maximum
        or _CONTROL_CHARACTER.search(checked)
        or _ABSOLUTE_PATH.search(checked)
        or _SENSITIVE_ASSIGNMENT.search(checked)
        or _RAW_CONTENT.search(checked)
    ):
        raise ValueError(f"{field} is unsafe")
    return checked


def _identifier(value: object, *, field: str) -> str:
    checked = _text(value, field=field, maximum=128)
    if not _IDENTIFIER.fullmatch(checked):
        raise ValueError(f"{field} is invalid")
    return checked


def _reference(value: object, *, field: str) -> str:
    checked = _text(value, field=field, maximum=256)
    if (
        not _REFERENCE.fullmatch(checked)
        or "@" in checked
        or _RAW_REFERENCE.search(checked)
    ):
        raise ValueError(f"{field} is invalid")
    return checked


def _fingerprint(value: object, *, field: str = "fingerprint") -> str:
    if not isinstance(value, str) or not _FINGERPRINT.fullmatch(value.strip()):
        raise ValueError(f"{field} is invalid")
    return value.strip()


def _utc(value: object, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone aware")
    return value.astimezone(UTC)


def _tuple(value: object, *, field: str) -> tuple[object, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{field} must be a tuple")
    return copy.deepcopy(value)


def _canonical_fingerprint(value: BaseModel, *, exclude: set[str] | frozenset[str]) -> str:
    encoded = json.dumps(
        value.model_dump(mode="json", exclude=exclude),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class ProjectMemoryPayload(_FoundationContract):
    project_id: str
    project_type: str
    hardware_target: str
    requirement_summary: str
    architecture_summary: str

    @field_validator("project_id", mode="before")
    @classmethod
    def validate_project_id(cls, value: object) -> str:
        return _identifier(value, field="project_id")

    @field_validator(
        "project_type",
        "hardware_target",
        "requirement_summary",
        "architecture_summary",
        mode="before",
    )
    @classmethod
    def validate_text(cls, value: object, info) -> str:
        return _text(value, field=info.field_name, maximum=2048)


class DecisionMemoryPayload(_FoundationContract):
    reason: str
    context: str
    choice: str
    timestamp: datetime

    @field_validator("reason", "context", "choice", mode="before")
    @classmethod
    def validate_text(cls, value: object, info) -> str:
        return _text(value, field=info.field_name, maximum=2048)

    @field_validator("timestamp", mode="before")
    @classmethod
    def validate_timestamp(cls, value: object) -> datetime:
        return _utc(value, field="timestamp")


class DebugMemoryPayload(_FoundationContract):
    symptom: str
    root_cause: str
    fix: str
    verification: str

    @field_validator("symptom", "root_cause", "fix", "verification", mode="before")
    @classmethod
    def validate_text(cls, value: object, info) -> str:
        return _text(value, field=info.field_name, maximum=2048)


class EvidenceResultReference(_FoundationContract):
    status: str
    source_reference: str
    fingerprint: str | None = None
    artifact_reference: str | None = None

    @field_validator("status", mode="before")
    @classmethod
    def validate_status(cls, value: object) -> str:
        checked = _text(value, field="status", maximum=128)
        if _RAW_CONTENT.search(checked):
            raise ValueError("status contains raw content")
        return checked

    @field_validator("source_reference", mode="before")
    @classmethod
    def validate_source_reference(cls, value: object) -> str:
        return _reference(value, field="source_reference")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_optional_fingerprint(cls, value: object) -> str | None:
        return None if value is None else _fingerprint(value, field="fingerprint")

    @field_validator("artifact_reference", mode="before")
    @classmethod
    def validate_artifact_reference(cls, value: object) -> str | None:
        return None if value is None else _reference(value, field="artifact_reference")


class EvidenceMemoryPayload(_FoundationContract):
    validation_result: EvidenceResultReference | None = None
    test_result: EvidenceResultReference | None = None
    execution_result: EvidenceResultReference | None = None

    @model_validator(mode="after")
    def require_result(self) -> EvidenceMemoryPayload:
        if (
            self.validation_result is None
            and self.test_result is None
            and self.execution_result is None
        ):
            raise ValueError("at least one evidence result is required")
        return self


FoundationMemoryPayload: TypeAlias = (
    ProjectMemoryPayload
    | DecisionMemoryPayload
    | DebugMemoryPayload
    | EvidenceMemoryPayload
)


class FoundationEngineeringMemoryRecord(_FoundationContract):
    memory_id: str
    tenant_id: str
    project_id: str
    memory_type: FoundationMemoryType
    payload: FoundationMemoryPayload
    created_at: datetime
    source_references: tuple[str, ...]
    status: Literal["CANDIDATE"] = "CANDIDATE"
    fingerprint: str

    @field_validator("memory_id", "tenant_id", "project_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("created_at", mode="before")
    @classmethod
    def validate_created_at(cls, value: object) -> datetime:
        return _utc(value, field="created_at")

    @field_validator("source_references", mode="before")
    @classmethod
    def validate_source_references(cls, value: object) -> tuple[str, ...]:
        values = _tuple(value, field="source_references")
        if len(values) > _MAX_SOURCE_REFERENCES:
            raise ValueError("source_references is too long")
        checked = tuple(_reference(item, field="source_reference") for item in values)
        if not checked:
            raise ValueError("source_references must not be empty")
        return tuple(sorted(set(checked)))

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_record_fingerprint(cls, value: object) -> str:
        return _fingerprint(value)

    @model_validator(mode="after")
    def verify_bindings(self) -> FoundationEngineeringMemoryRecord:
        expected_payload = {
            FoundationMemoryType.PROJECT: ProjectMemoryPayload,
            FoundationMemoryType.DECISION: DecisionMemoryPayload,
            FoundationMemoryType.DEBUG: DebugMemoryPayload,
            FoundationMemoryType.EVIDENCE: EvidenceMemoryPayload,
        }[self.memory_type]
        if type(self.payload) is not expected_payload:
            raise ValueError("memory_type and payload mismatch")
        if isinstance(self.payload, ProjectMemoryPayload) and self.payload.project_id != self.project_id:
            raise ValueError("project payload binding is invalid")
        if self.status != "CANDIDATE":
            raise ValueError("foundation memory status is invalid")
        if self.fingerprint != _canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("foundation memory fingerprint mismatch")
        return self

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.tenant_id, self.project_id, self.memory_id)

    @classmethod
    def create(cls, **values: object) -> FoundationEngineeringMemoryRecord:
        material = dict(values)
        material.setdefault("status", "CANDIDATE")
        for field in ("memory_id", "tenant_id", "project_id"):
            material[field] = _identifier(material[field], field=field)
        material["memory_type"] = FoundationMemoryType(material["memory_type"])
        material["created_at"] = _utc(material["created_at"], field="created_at")
        references = _tuple(material["source_references"], field="source_references")
        checked_references = tuple(
            _reference(item, field="source_reference") for item in references
        )
        if not checked_references:
            raise ValueError("source_references must not be empty")
        material["source_references"] = tuple(sorted(set(checked_references)))
        payload = material["payload"]
        expected_payload = {
            FoundationMemoryType.PROJECT: ProjectMemoryPayload,
            FoundationMemoryType.DECISION: DecisionMemoryPayload,
            FoundationMemoryType.DEBUG: DebugMemoryPayload,
            FoundationMemoryType.EVIDENCE: EvidenceMemoryPayload,
        }[material["memory_type"]]
        if isinstance(payload, _FoundationContract):
            payload_data = payload.model_dump(mode="python")
        else:
            payload_data = copy.deepcopy(payload)
        material["payload"] = expected_payload.model_validate(payload_data)
        provisional = cls.model_construct(
            **{**material, "fingerprint": "sha256:" + "0" * 64}
        )
        material["fingerprint"] = _canonical_fingerprint(
            provisional, exclude={"fingerprint"}
        )
        return cls.model_validate(material)


class FoundationMemoryQuery(_FoundationContract):
    tenant_id: str
    project_id: str
    query: str = ""
    memory_type: FoundationMemoryType | None = None
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("tenant_id", "project_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("query", mode="before")
    @classmethod
    def validate_query(cls, value: object) -> str:
        return _text(value, field="query", maximum=512, allow_empty=True)

    @field_validator("limit", mode="before")
    @classmethod
    def validate_limit(cls, value: object) -> int:
        if type(value) is not int:
            raise ValueError("limit is invalid")
        return value


__all__ = (
    "DebugMemoryPayload",
    "DecisionMemoryPayload",
    "EvidenceMemoryPayload",
    "EvidenceResultReference",
    "FoundationEngineeringMemoryRecord",
    "FoundationMemoryPayload",
    "FoundationMemoryQuery",
    "FoundationMemoryType",
    "ProjectMemoryPayload",
)
