from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from enum import StrEnum
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from embedded_copilot.engineering_context import EngineeringContextSnapshot

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+-]{0,127}$")
_FINGERPRINT = re.compile(r"^sha256:[a-f0-9]{64}$")
_KNOWLEDGE_REFERENCE = re.compile(
    r"^knowledge://[A-Za-z0-9][A-Za-z0-9._~:/+-]{0,255}$"
)
_PROPOSAL_ID = re.compile(r"^proposal-[a-f0-9]{48}$")
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


class OptimizationContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class OptimizationTarget(StrEnum):
    HARDWARE = "HARDWARE"
    FIRMWARE = "FIRMWARE"
    SOFTWARE = "SOFTWARE"
    TESTING = "TESTING"
    POWER = "POWER"
    PERFORMANCE = "PERFORMANCE"


class OptimizationProposalStatus(StrEnum):
    CREATED = "CREATED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


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


def fingerprint(value: object, *, field: str = "fingerprint") -> str:
    if not isinstance(value, str) or _FINGERPRINT.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


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


def _tuple_only(value: object, *, field: str) -> tuple[object, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{field} must be a tuple")
    return copy.deepcopy(value)


def _nested_exact_or_mapping(value: object, cls: type[ModelT], *, field: str) -> ModelT:
    if type(value) is cls:
        material = value.model_dump(mode="python")
    elif type(value) is dict:
        material = value
    else:
        raise TypeError(f"{field} is invalid")
    return cls.model_validate(copy.deepcopy(material))


def _text_tuple(
    value: object,
    *,
    field: str,
    sort: bool,
    reference: bool = False,
) -> tuple[str, ...]:
    checked = tuple(
        knowledge_reference(item, field=field)
        if reference
        else safe_text(item, field=field)
        for item in _tuple_only(value, field=field)
    )
    if not checked or len(checked) != len(set(checked)):
        raise ValueError(f"{field} must be non-empty and unique")
    return tuple(sorted(checked)) if sort else checked


class ValidationFindingReference(OptimizationContract):
    finding_id: str
    fingerprint: str

    @field_validator("finding_id", mode="before")
    @classmethod
    def validate_finding_id(cls, value: object) -> str:
        return identifier(value, field="finding_id")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @classmethod
    def create(cls, **values: object) -> ValidationFindingReference:
        material = dict(values)
        material["finding_id"] = identifier(
            material["finding_id"], field="finding_id"
        )
        material["fingerprint"] = fingerprint(material["fingerprint"])
        return cls.model_validate(material)


class ApprovedKnowledgeReference(OptimizationContract):
    reference: str
    fingerprint: str

    @field_validator("reference", mode="before")
    @classmethod
    def validate_reference(cls, value: object) -> str:
        return knowledge_reference(value, field="reference")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @classmethod
    def create(cls, **values: object) -> ApprovedKnowledgeReference:
        material = dict(values)
        material["reference"] = knowledge_reference(
            material["reference"], field="reference"
        )
        material["fingerprint"] = fingerprint(material["fingerprint"])
        return cls.model_validate(material)

    @property
    def knowledge_id(self) -> str:
        return self.reference.removeprefix("knowledge://")


def _finding_refs(
    value: object, *, field: str, non_empty: bool = True
) -> tuple[ValidationFindingReference, ...]:
    checked = tuple(
        _nested_exact_or_mapping(item, ValidationFindingReference, field=field)
        for item in _tuple_only(value, field=field)
    )
    ids = tuple(item.finding_id for item in checked)
    fingerprints = tuple(item.fingerprint for item in checked)
    if (
        (non_empty and not checked)
        or len(ids) != len(set(ids))
        or len(fingerprints) != len(set(fingerprints))
    ):
        raise ValueError(f"{field} must be non-empty and unique")
    return tuple(sorted(checked, key=lambda item: item.finding_id))


def _knowledge_refs(value: object) -> tuple[ApprovedKnowledgeReference, ...]:
    checked = tuple(
        _nested_exact_or_mapping(
            item, ApprovedKnowledgeReference, field="knowledge_refs"
        )
        for item in _tuple_only(value, field="knowledge_refs")
    )
    references = tuple(item.reference for item in checked)
    fingerprints = tuple(item.fingerprint for item in checked)
    if len(references) != len(set(references)) or len(fingerprints) != len(
        set(fingerprints)
    ):
        raise ValueError("knowledge_refs must be unique")
    return tuple(sorted(checked, key=lambda item: item.reference))


def _optimization_scope(value: object) -> tuple[OptimizationTarget, ...]:
    items = _tuple_only(value, field="optimization_scope")
    if any(type(item) is not OptimizationTarget for item in items):
        raise TypeError("optimization_scope is invalid")
    checked = tuple(items)
    if not checked or len(checked) != len(set(checked)):
        raise ValueError("optimization_scope must be non-empty and unique")
    return tuple(sorted(checked, key=lambda item: item.value))


class OptimizationRequest(OptimizationContract):
    request_id: str
    project_id: str
    validation_report_id: str
    finding_refs: tuple[ValidationFindingReference, ...]
    context_snapshot: EngineeringContextSnapshot
    knowledge_refs: tuple[ApprovedKnowledgeReference, ...]
    optimization_scope: tuple[OptimizationTarget, ...]

    @field_validator("request_id", "project_id", "validation_report_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("finding_refs", mode="before")
    @classmethod
    def validate_finding_refs(
        cls, value: object
    ) -> tuple[ValidationFindingReference, ...]:
        return _finding_refs(value, field="finding_refs")

    @field_validator("context_snapshot", mode="before")
    @classmethod
    def validate_context(cls, value: object) -> EngineeringContextSnapshot:
        return _nested_exact_or_mapping(
            value, EngineeringContextSnapshot, field="context_snapshot"
        )

    @field_validator("knowledge_refs", mode="before")
    @classmethod
    def validate_knowledge_refs(
        cls, value: object
    ) -> tuple[ApprovedKnowledgeReference, ...]:
        return _knowledge_refs(value)

    @field_validator("optimization_scope", mode="before")
    @classmethod
    def validate_scope(cls, value: object) -> tuple[OptimizationTarget, ...]:
        return _optimization_scope(value)

    @classmethod
    def create(cls, **values: object) -> OptimizationRequest:
        material = dict(values)
        material["request_id"] = identifier(material["request_id"], field="request_id")
        material["project_id"] = identifier(material["project_id"], field="project_id")
        material["validation_report_id"] = identifier(
            material["validation_report_id"], field="validation_report_id"
        )
        material["finding_refs"] = _finding_refs(
            material["finding_refs"], field="finding_refs"
        )
        if type(material["context_snapshot"]) is not EngineeringContextSnapshot:
            raise TypeError("context_snapshot is invalid")
        material["context_snapshot"] = cls.validate_context(
            material["context_snapshot"]
        )
        material["knowledge_refs"] = _knowledge_refs(material["knowledge_refs"])
        material["optimization_scope"] = _optimization_scope(
            material["optimization_scope"]
        )
        return cls.model_validate(material)

    @model_validator(mode="after")
    def verify(self) -> OptimizationRequest:
        if self.context_snapshot.project_id != self.project_id:
            raise ValueError("context project binding mismatch")
        return self

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self)


class OptimizationProposal(OptimizationContract):
    proposal_id: str
    project_id: str
    source_finding_refs: tuple[ValidationFindingReference, ...]
    target: OptimizationTarget
    title: str
    rationale: str
    expected_effects: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    risk_notes: tuple[str, ...]
    status: OptimizationProposalStatus
    fingerprint: str

    @field_validator("proposal_id", mode="before")
    @classmethod
    def validate_proposal_id(cls, value: object) -> str:
        checked = identifier(value, field="proposal_id")
        if _PROPOSAL_ID.fullmatch(checked) is None:
            raise ValueError("proposal_id is invalid")
        return checked

    @field_validator("project_id", mode="before")
    @classmethod
    def validate_project_id(cls, value: object) -> str:
        return identifier(value, field="project_id")

    @field_validator("source_finding_refs", mode="before")
    @classmethod
    def validate_source_refs(
        cls, value: object
    ) -> tuple[ValidationFindingReference, ...]:
        return _finding_refs(value, field="source_finding_refs")

    @field_validator("title", "rationale", mode="before")
    @classmethod
    def validate_text(cls, value: object, info) -> str:
        maximum = 512 if info.field_name == "title" else 2048
        return safe_text(value, field=info.field_name, maximum=maximum)

    @field_validator("expected_effects", mode="before")
    @classmethod
    def validate_effects(cls, value: object) -> tuple[str, ...]:
        return _text_tuple(value, field="expected_effects", sort=False)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def validate_evidence(cls, value: object) -> tuple[str, ...]:
        return _text_tuple(
            value, field="evidence_refs", sort=True, reference=True
        )

    @field_validator("risk_notes", mode="before")
    @classmethod
    def validate_risks(cls, value: object) -> tuple[str, ...]:
        return _text_tuple(value, field="risk_notes", sort=False)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @classmethod
    def create(cls, **values: object) -> OptimizationProposal:
        material = dict(values)
        material.pop("proposal_id", None)
        material.pop("fingerprint", None)
        material["project_id"] = identifier(material["project_id"], field="project_id")
        material["source_finding_refs"] = _finding_refs(
            material["source_finding_refs"], field="source_finding_refs"
        )
        if type(material["target"]) is not OptimizationTarget:
            raise TypeError("target is invalid")
        material["title"] = safe_text(material["title"], field="title", maximum=512)
        material["rationale"] = safe_text(
            material["rationale"], field="rationale"
        )
        material["expected_effects"] = _text_tuple(
            material["expected_effects"], field="expected_effects", sort=False
        )
        material["evidence_refs"] = _text_tuple(
            material["evidence_refs"],
            field="evidence_refs",
            sort=True,
            reference=True,
        )
        material["risk_notes"] = _text_tuple(
            material["risk_notes"], field="risk_notes", sort=False
        )
        if type(material["status"]) is not OptimizationProposalStatus:
            raise TypeError("status is invalid")
        provisional = cls.model_construct(
            **material,
            proposal_id="proposal-" + "0" * 48,
            fingerprint="sha256:" + "0" * 64,
        )
        semantic_fingerprint = canonical_fingerprint(
            provisional,
            exclude={"fingerprint", "proposal_id", "status"},
        )
        material["fingerprint"] = semantic_fingerprint
        material["proposal_id"] = (
            "proposal-" + semantic_fingerprint.removeprefix("sha256:")[:48]
        )
        return cls.model_validate(material)

    @model_validator(mode="after")
    def verify(self) -> OptimizationProposal:
        expected_fingerprint = canonical_fingerprint(
            self,
            exclude={"fingerprint", "proposal_id", "status"},
        )
        expected_id = "proposal-" + expected_fingerprint.removeprefix("sha256:")[:48]
        if self.fingerprint != expected_fingerprint:
            raise ValueError("proposal fingerprint mismatch")
        if self.proposal_id != expected_id:
            raise ValueError("proposal identity mismatch")
        return self


__all__ = (
    "ApprovedKnowledgeReference",
    "OptimizationContract",
    "OptimizationProposal",
    "OptimizationProposalStatus",
    "OptimizationRequest",
    "OptimizationTarget",
    "ValidationFindingReference",
    "canonical_fingerprint",
    "fingerprint",
    "identifier",
)
