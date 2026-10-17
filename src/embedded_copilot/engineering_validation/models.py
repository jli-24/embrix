from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from enum import StrEnum
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from embedded_copilot.engineering_context.contracts import EngineeringContextSnapshot

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+-]{0,127}$")
_CATEGORY = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,127}$")
_FINGERPRINT = re.compile(r"^sha256:[a-f0-9]{64}$")
_ARTIFACT_REFERENCE = re.compile(
    r"^(?:artifact|workspace|s3)://[A-Za-z0-9][A-Za-z0-9._~:/+-]{0,255}$"
)
_KNOWLEDGE_REFERENCE = re.compile(
    r"^knowledge://[A-Za-z0-9][A-Za-z0-9._~:/+-]{0,255}$"
)
_UNSAFE_TEXT = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|bearer|password|credential|secret|token)"
    r"\s*[:=]?|raw\s+(?:model\s+)?output|hidden\s+reasoning|chain\s+of\s+thought|"
    r"(?:[A-Za-z]:[\\/]|\\\\|file://|https?://|data:|base64|\x00|\r|\n)",
    re.IGNORECASE,
)
_UNSAFE_REFERENCE = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|bearer|password|credential|secret|token)"
    r"\s*[:=]?|raw\s+(?:model\s+)?output|hidden\s+reasoning|chain\s+of\s+thought|"
    r"(?:\\\\|file://|https?://|data:|base64|\x00|\r|\n)",
    re.IGNORECASE,
)


class ValidationContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class ValidationSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ValidationFindingStatus(StrEnum):
    OPEN = "OPEN"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


ModelT = TypeVar("ModelT", bound=BaseModel)


def safe_text(value: object, *, field: str, maximum: int = 2048) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = unicodedata.normalize("NFC", value).strip()
    if not checked or len(checked) > maximum or _UNSAFE_TEXT.search(checked):
        raise ValueError(f"{field} is unsafe")
    return checked


def identifier(value: object, *, field: str) -> str:
    checked = safe_text(value, field=field, maximum=128)
    if _IDENTIFIER.fullmatch(checked) is None:
        raise ValueError(f"{field} is invalid")
    return checked


def category(value: object, *, field: str = "category") -> str:
    checked = safe_text(value, field=field, maximum=128)
    if _CATEGORY.fullmatch(checked) is None:
        raise ValueError(f"{field} is invalid")
    return checked


def fingerprint(value: object, *, field: str = "fingerprint") -> str:
    if not isinstance(value, str) or _FINGERPRINT.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def artifact_reference(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("source_reference is invalid")
    checked = unicodedata.normalize("NFC", value).strip()
    if (
        _ARTIFACT_REFERENCE.fullmatch(checked) is None
        or _UNSAFE_REFERENCE.search(checked) is not None
    ):
        raise ValueError("source_reference is unsafe")
    return checked


def knowledge_reference(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = unicodedata.normalize("NFC", value).strip()
    if (
        _KNOWLEDGE_REFERENCE.fullmatch(checked) is None
        or _UNSAFE_REFERENCE.search(checked) is not None
    ):
        raise ValueError(f"{field} is unsafe")
    return checked


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


def _with_fingerprint(
    cls: type[ModelT], values: dict[str, object], *, exclude: set[str]
) -> ModelT:
    material = dict(values)
    material.pop("fingerprint", None)
    provisional = cls.model_construct(
        **{**material, "fingerprint": "sha256:" + "0" * 64}
    )
    material["fingerprint"] = canonical_fingerprint(provisional, exclude=exclude)
    return cls.model_validate(material)


def _tuple_only(value: object, *, field: str) -> tuple[object, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{field} must be a tuple")
    return copy.deepcopy(value)


def _knowledge_refs(value: object, *, field: str) -> tuple[str, ...]:
    checked = tuple(
        knowledge_reference(item, field=field)
        for item in _tuple_only(value, field=field)
    )
    if not checked or len(checked) != len(set(checked)):
        raise ValueError(f"{field} must be non-empty and unique")
    return tuple(sorted(checked))


def _validation_scope(value: object) -> tuple[str, ...]:
    checked = tuple(
        category(item, field="validation_scope")
        for item in _tuple_only(value, field="validation_scope")
    )
    if not checked or len(checked) != len(set(checked)):
        raise ValueError("validation_scope must be non-empty and unique")
    return tuple(sorted(checked))


class ValidationArtifactReference(ValidationContract):
    artifact_id: str
    project_id: str
    artifact_type: str
    source_reference: str
    fingerprint: str

    @field_validator("artifact_id", "project_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("artifact_type", mode="before")
    @classmethod
    def validate_artifact_type(cls, value: object) -> str:
        return category(value, field="artifact_type")

    @field_validator("source_reference", mode="before")
    @classmethod
    def validate_source_reference(cls, value: object) -> str:
        return artifact_reference(value)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @classmethod
    def create(cls, **values: object) -> ValidationArtifactReference:
        material = dict(values)
        material["artifact_id"] = identifier(
            material["artifact_id"], field="artifact_id"
        )
        material["project_id"] = identifier(
            material["project_id"], field="project_id"
        )
        material["artifact_type"] = category(
            material["artifact_type"], field="artifact_type"
        )
        material["source_reference"] = artifact_reference(
            material["source_reference"]
        )
        return _with_fingerprint(cls, material, exclude={"fingerprint"})

    @model_validator(mode="after")
    def verify(self) -> ValidationArtifactReference:
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("artifact reference fingerprint mismatch")
        return self


class ValidationRequest(ValidationContract):
    request_id: str
    project_id: str
    artifact_reference: ValidationArtifactReference
    context_snapshot: EngineeringContextSnapshot
    knowledge_refs: tuple[str, ...]
    validation_scope: tuple[str, ...]

    @field_validator("request_id", "project_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("artifact_reference", mode="before")
    @classmethod
    def validate_artifact(cls, value: object) -> ValidationArtifactReference:
        if type(value) is ValidationArtifactReference:
            material = value.model_dump(mode="python")
        elif type(value) is dict:
            material = value
        else:
            raise TypeError("artifact_reference is invalid")
        return ValidationArtifactReference.model_validate(copy.deepcopy(material))

    @field_validator("context_snapshot", mode="before")
    @classmethod
    def validate_context(cls, value: object) -> EngineeringContextSnapshot:
        if type(value) is EngineeringContextSnapshot:
            material = value.model_dump(mode="python")
        elif type(value) is dict:
            material = value
        else:
            raise TypeError("context_snapshot is invalid")
        return EngineeringContextSnapshot.model_validate(copy.deepcopy(material))

    @field_validator("knowledge_refs", mode="before")
    @classmethod
    def validate_knowledge_refs(cls, value: object) -> tuple[str, ...]:
        return _knowledge_refs(value, field="knowledge_refs")

    @field_validator("validation_scope", mode="before")
    @classmethod
    def validate_scope(cls, value: object) -> tuple[str, ...]:
        return _validation_scope(value)

    @classmethod
    def create(cls, **values: object) -> ValidationRequest:
        material = dict(values)
        material["request_id"] = identifier(
            material["request_id"], field="request_id"
        )
        material["project_id"] = identifier(
            material["project_id"], field="project_id"
        )
        material["artifact_reference"] = cls.validate_artifact(
            material["artifact_reference"]
        )
        material["context_snapshot"] = cls.validate_context(
            material["context_snapshot"]
        )
        material["knowledge_refs"] = _knowledge_refs(
            material["knowledge_refs"], field="knowledge_refs"
        )
        material["validation_scope"] = _validation_scope(
            material["validation_scope"]
        )
        return cls.model_validate(material)

    @model_validator(mode="after")
    def verify(self) -> ValidationRequest:
        if self.artifact_reference.project_id != self.project_id:
            raise ValueError("artifact project binding mismatch")
        if self.context_snapshot.project_id != self.project_id:
            raise ValueError("context project binding mismatch")
        return self

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self)


class ValidationRule(ValidationContract):
    rule_id: str
    category: str
    description: str
    fingerprint: str

    @field_validator("rule_id", mode="before")
    @classmethod
    def validate_rule_id(cls, value: object) -> str:
        return identifier(value, field="rule_id")

    @field_validator("category", mode="before")
    @classmethod
    def validate_category(cls, value: object) -> str:
        return category(value)

    @field_validator("description", mode="before")
    @classmethod
    def validate_description(cls, value: object) -> str:
        return safe_text(value, field="description")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @classmethod
    def create(cls, **values: object) -> ValidationRule:
        material = dict(values)
        material["rule_id"] = identifier(material["rule_id"], field="rule_id")
        material["category"] = category(material["category"])
        material["description"] = safe_text(
            material["description"], field="description"
        )
        return _with_fingerprint(cls, material, exclude={"fingerprint"})

    @model_validator(mode="after")
    def verify(self) -> ValidationRule:
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("validation rule fingerprint mismatch")
        return self


class ValidationFinding(ValidationContract):
    finding_id: str
    project_id: str
    rule_id: str
    category: str
    severity: ValidationSeverity
    status: ValidationFindingStatus
    message: str
    evidence_refs: tuple[str, ...]
    fingerprint: str

    @field_validator("finding_id", "project_id", "rule_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("category", mode="before")
    @classmethod
    def validate_category(cls, value: object) -> str:
        return category(value)

    @field_validator("message", mode="before")
    @classmethod
    def validate_message(cls, value: object) -> str:
        return safe_text(value, field="message")

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def validate_evidence_refs(cls, value: object) -> tuple[str, ...]:
        return _knowledge_refs(value, field="evidence_refs")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @classmethod
    def create(cls, **values: object) -> ValidationFinding:
        material = dict(values)
        for field in ("finding_id", "project_id", "rule_id"):
            material[field] = identifier(material[field], field=field)
        material["category"] = category(material["category"])
        material["severity"] = ValidationSeverity(material["severity"])
        material["status"] = ValidationFindingStatus(material["status"])
        material["message"] = safe_text(material["message"], field="message")
        material["evidence_refs"] = _knowledge_refs(
            material["evidence_refs"], field="evidence_refs"
        )
        return _with_fingerprint(
            cls, material, exclude={"fingerprint", "status"}
        )

    @model_validator(mode="after")
    def verify(self) -> ValidationFinding:
        if self.fingerprint != canonical_fingerprint(
            self, exclude={"fingerprint", "status"}
        ):
            raise ValueError("validation finding fingerprint mismatch")
        return self


class ValidationReport(ValidationContract):
    report_id: str
    project_id: str
    findings: tuple[ValidationFinding, ...]
    summary: str
    fingerprint: str

    @field_validator("report_id", "project_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("findings", mode="before")
    @classmethod
    def validate_findings(cls, value: object) -> tuple[ValidationFinding, ...]:
        items = _tuple_only(value, field="findings")
        checked: list[ValidationFinding] = []
        for item in items:
            if type(item) is ValidationFinding:
                material = item.model_dump(mode="python")
            elif type(item) is dict:
                material = item
            else:
                raise TypeError("finding is invalid")
            checked.append(
                ValidationFinding.model_validate(copy.deepcopy(material))
            )
        ids = tuple(item.finding_id for item in checked)
        fingerprints = tuple(item.fingerprint for item in checked)
        if len(ids) != len(set(ids)) or len(fingerprints) != len(set(fingerprints)):
            raise ValueError("report findings must be unique")
        return tuple(sorted(checked, key=lambda item: item.finding_id))

    @field_validator("summary", mode="before")
    @classmethod
    def validate_summary(cls, value: object) -> str:
        return safe_text(value, field="summary", maximum=512)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @classmethod
    def create(cls, **values: object) -> ValidationReport:
        material = dict(values)
        material["report_id"] = identifier(
            material["report_id"], field="report_id"
        )
        material["project_id"] = identifier(
            material["project_id"], field="project_id"
        )
        material["findings"] = cls.validate_findings(material["findings"])
        material["summary"] = safe_text(
            material["summary"], field="summary", maximum=512
        )
        return _with_fingerprint(cls, material, exclude={"fingerprint"})

    @model_validator(mode="after")
    def verify(self) -> ValidationReport:
        if any(item.project_id != self.project_id for item in self.findings):
            raise ValueError("report finding project binding mismatch")
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("validation report fingerprint mismatch")
        return self


__all__ = (
    "ValidationArtifactReference",
    "ValidationContract",
    "ValidationFinding",
    "ValidationFindingStatus",
    "ValidationReport",
    "ValidationRequest",
    "ValidationRule",
    "ValidationSeverity",
    "canonical_fingerprint",
    "category",
    "fingerprint",
    "identifier",
)
