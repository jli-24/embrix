from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import unicodedata
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/-]{0,127}$")
_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+-]{0,255}$")
_FINGERPRINT = re.compile(r"^sha256:[a-f0-9]{64}$")
_UNSAFE = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\|file://|\x00|\r|\n|"
    r"credential|password|secret|token|provider|runtime|prompt|cot|"
    r"stdout|stderr|raw\s+log)",
    re.IGNORECASE,
)
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{1,63}")
_FUSION_SOURCE_REFERENCE = re.compile(
    r"^(?:artifact|s3|workspace)://[A-Za-z0-9][A-Za-z0-9._~:/+-]{0,255}$"
)
_FUSION_SOURCE_UNSAFE = re.compile(
    r"(?:\\\\|\x00|\r|\n|credential|password|secret|token|provider|runtime|"
    r"prompt|cot|stdout|stderr|raw\s+log)",
    re.IGNORECASE,
)
_AUTO_FINGERPRINT = "sha256:" + "0" * 64


class ContextSourceType(StrEnum):
    ENGINEERING_MEMORY = "ENGINEERING_MEMORY"
    KNOWLEDGE_GRAPH = "KNOWLEDGE_GRAPH"
    KNOWLEDGE_GATEWAY = "KNOWLEDGE_GATEWAY"
    MULTIMODAL_OBSERVATION = "MULTIMODAL_OBSERVATION"
    KNOWLEDGE_EVOLUTION = "KNOWLEDGE_EVOLUTION"
    DATASHEET_METADATA = "DATASHEET_METADATA"
    # Internal source discriminator. Firmware facts travel through the existing
    # knowledge_evidence collection until a future public context-channel split.
    FIRMWARE_SOURCE = "FIRMWARE_SOURCE"
    KNOWLEDGE = "KNOWLEDGE"
    DEBUG = "DEBUG"
    HARDWARE = "HARDWARE"
    PCB = "PCB"
    REQUIREMENT = "REQUIREMENT"
    ARCHITECTURE = "ARCHITECTURE"
    VISION_OBSERVATION = "VISION_OBSERVATION"


class EvidenceTrustLevel(StrEnum):
    OBSERVATION = "OBSERVATION"
    INFERENCE = "INFERENCE"
    CANDIDATE = "CANDIDATE"
    VERIFIED_FACT = "VERIFIED_FACT"


class EvidenceVerificationStatus(StrEnum):
    OBSERVED = "OBSERVED"
    INFERRED = "INFERRED"
    CANDIDATE = "CANDIDATE"
    VERIFIED = "VERIFIED"
    UNAVAILABLE = "UNAVAILABLE"


class ContextVerificationStatus(StrEnum):
    APPROVED = "APPROVED"
    VERIFIED = "VERIFIED"
    PROJECTED = "PROJECTED"
    UNVERIFIED = "UNVERIFIED"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"


class ContextCategory(StrEnum):
    REQUIREMENT = "REQUIREMENT"
    DECISION = "DECISION"
    CONSTRAINT = "CONSTRAINT"
    HISTORICAL_PROBLEM = "HISTORICAL_PROBLEM"
    SOLUTION = "SOLUTION"
    COMPONENT = "COMPONENT"
    INTERFACE = "INTERFACE"


class ContextContract(BaseModel):
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
    checked = unicodedata.normalize("NFC", value).strip()
    if not checked or len(checked) > maximum or _UNSAFE.search(checked):
        raise ValueError(f"{field} is unsafe")
    return checked


def identifier(value: object, *, field: str) -> str:
    checked = _text(value, field=field, maximum=128)
    if not _IDENTIFIER.fullmatch(checked):
        raise ValueError(f"{field} is invalid")
    return checked


def reference(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = unicodedata.normalize("NFC", value).strip()
    if not checked or len(checked) > 256:
        raise ValueError(f"{field} is invalid")
    if "://" in checked:
        scheme, resource = checked.split("://", maxsplit=1)
        if scheme.casefold() not in {"artifact", "s3", "workspace", "https"}:
            raise ValueError(f"{field} is invalid")
        if (
            not resource
            or "://" in resource
            or _UNSAFE.search(resource)
        ):
            raise ValueError(f"{field} is unsafe")
        if scheme.casefold() == "https":
            authority = resource.split("/", maxsplit=1)[0]
            if not authority or "@" in authority:
                raise ValueError(f"{field} is unsafe")
    elif _UNSAFE.search(checked):
        raise ValueError(f"{field} is unsafe")
    if not _REFERENCE.fullmatch(checked):
        raise ValueError(f"{field} is invalid")
    return checked


def tuple_only(value: object, *, field: str) -> object:
    if type(value) is not tuple:
        raise TypeError(f"{field} must be a tuple")
    return copy.deepcopy(value)


def fingerprint(value: object, *, field: str = "fingerprint") -> str:
    if not isinstance(value, str) or not _FINGERPRINT.fullmatch(value.strip()):
        raise ValueError(f"{field} is invalid")
    return value.strip()


def _confidence(value: object, *, field: str = "confidence") -> float:
    if type(value) is not float or not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{field} is invalid")
    return value


def _optional_confidence(value: object) -> float | None:
    if value is None:
        return None
    return _confidence(value)


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


def _with_fingerprint(cls: type[BaseModel], material: dict[str, object]) -> object:
    provisional = cls.model_construct(
        **{**material, "fingerprint": "sha256:" + "0" * 64}
    )
    material["fingerprint"] = canonical_fingerprint(
        provisional, exclude={"fingerprint"}
    )
    return cls.model_validate(material)


class ContextSourceReference(ContextContract):
    source_type: ContextSourceType
    source_id: str
    source_reference: str
    source_fingerprint: str
    verification_status: ContextVerificationStatus
    confidence: float | None = None
    fingerprint: str

    @field_validator("source_id", mode="before")
    @classmethod
    def validate_source_id(cls, value: object) -> str:
        return identifier(value, field="source_id")

    @field_validator("source_reference", mode="before")
    @classmethod
    def validate_source_reference(cls, value: object) -> str:
        return reference(value, field="source_reference")

    @field_validator("source_fingerprint", "fingerprint", mode="before")
    @classmethod
    def validate_fingerprints(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_confidence(cls, value: object) -> float | None:
        return _optional_confidence(value)

    @classmethod
    def create(cls, **values: object) -> ContextSourceReference:
        material = dict(values)
        material["source_type"] = ContextSourceType(material["source_type"])
        material["source_id"] = identifier(material["source_id"], field="source_id")
        material["source_reference"] = reference(
            material["source_reference"], field="source_reference"
        )
        material["source_fingerprint"] = fingerprint(
            material["source_fingerprint"], field="source_fingerprint"
        )
        material["verification_status"] = ContextVerificationStatus(
            material["verification_status"]
        )
        material["confidence"] = _optional_confidence(material.get("confidence"))
        return _with_fingerprint(cls, material)

    @model_validator(mode="after")
    def verify(self) -> ContextSourceReference:
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("context source reference fingerprint mismatch")
        return self


class EngineeringContextEvidence(ContextContract):
    evidence_id: str
    project_id: str
    tenant_id: str = "default-tenant"
    source_type: ContextSourceType
    subject: str
    summary: str
    fact_key: str | None = None
    fact_value: str | None = None
    source_reference: str
    source_fingerprint: str
    source_domain: str | None = None
    retrieved_at: str | None = None
    document_date: str | None = None
    license_reference: str | None = None
    confidence: float | None = None
    verification_status: ContextVerificationStatus
    trust_level: EvidenceTrustLevel = EvidenceTrustLevel.OBSERVATION
    evidence_status: EvidenceVerificationStatus = EvidenceVerificationStatus.OBSERVED
    observed_at: str | None = None
    provenance: tuple[str, ...] = ()
    fingerprint: str

    @field_validator("evidence_id", "project_id", "tenant_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("subject", "summary", "fact_key", "fact_value", mode="before")
    @classmethod
    def validate_text_fields(cls, value: object, info) -> str | None:
        return None if value is None else _text(value, field=info.field_name)

    @field_validator("source_reference", mode="before")
    @classmethod
    def validate_source_reference(cls, value: object) -> str:
        return reference(value, field="source_reference")

    @field_validator(
        "source_domain", "retrieved_at", "document_date", "license_reference",
        mode="before",
    )
    @classmethod
    def validate_optional_provenance(cls, value: object, info) -> str | None:
        return None if value is None else _text(value, field=info.field_name, maximum=256)

    @field_validator("source_fingerprint", "fingerprint", mode="before")
    @classmethod
    def validate_fingerprints(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_optional_confidence(cls, value: object) -> float | None:
        return _optional_confidence(value)

    @field_validator("observed_at", mode="before")
    @classmethod
    def validate_observed_at(cls, value: object) -> str | None:
        return None if value is None else _text(value, field="observed_at", maximum=64)

    @field_validator("provenance", mode="before")
    @classmethod
    def validate_provenance(cls, value: object) -> tuple[str, ...]:
        return tuple(_text(item, field="provenance", maximum=256) for item in tuple_only(value, field="provenance"))

    @classmethod
    def create(cls, **values: object) -> EngineeringContextEvidence:
        material = dict(values)
        for field in ("evidence_id", "project_id"):
            material[field] = identifier(material[field], field=field)
        material["source_type"] = ContextSourceType(material["source_type"])
        for field in ("subject", "summary", "fact_key", "fact_value"):
            material[field] = (
                None
                if material.get(field) is None
                else _text(material[field], field=field)
            )
        if (material.get("fact_key") is None) != (material.get("fact_value") is None):
            raise ValueError("fact_key and fact_value must be provided together")
        material["source_reference"] = reference(
            material["source_reference"], field="source_reference"
        )
        for field in (
            "source_domain", "retrieved_at", "document_date", "license_reference",
        ):
            material[field] = (
                None
                if material.get(field) is None
                else _text(material[field], field=field, maximum=256)
            )
        material["source_fingerprint"] = fingerprint(
            material["source_fingerprint"], field="source_fingerprint"
        )
        material["confidence"] = _optional_confidence(material.get("confidence"))
        material["verification_status"] = ContextVerificationStatus(
            material["verification_status"]
        )
        material["trust_level"] = EvidenceTrustLevel(material.get("trust_level", EvidenceTrustLevel.OBSERVATION))
        material["evidence_status"] = EvidenceVerificationStatus(material.get("evidence_status", EvidenceVerificationStatus.OBSERVED))
        material["observed_at"] = None if material.get("observed_at") is None else _text(material["observed_at"], field="observed_at", maximum=64)
        material["provenance"] = tuple(_text(item, field="provenance", maximum=256) for item in tuple_only(material.get("provenance", ()), field="provenance"))
        return _with_fingerprint(cls, material)

    @model_validator(mode="after")
    def verify(self) -> EngineeringContextEvidence:
        if (self.fact_key is None) != (self.fact_value is None):
            raise ValueError("fact_key and fact_value must be provided together")
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("context evidence fingerprint mismatch")
        if self.source_type is ContextSourceType.VISION_OBSERVATION and self.trust_level is EvidenceTrustLevel.VERIFIED_FACT:
            raise ValueError("vision observation cannot be a verified fact")
        return self

    @property
    def evidence_fingerprint(self) -> str:
        return self.fingerprint

    def source(self) -> ContextSourceReference:
        return ContextSourceReference.create(
            source_type=self.source_type,
            source_id=self.evidence_id,
            source_reference=self.source_reference,
            source_fingerprint=self.source_fingerprint,
            verification_status=self.verification_status,
            confidence=self.confidence,
        )


class ContextConflict(ContextContract):
    conflict_id: str
    project_id: str
    source_a: str
    source_b: str
    reason: str
    resolution_required: Literal[True] = True
    fingerprint: str

    @field_validator("conflict_id", "project_id", "source_a", "source_b", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("reason", mode="before")
    @classmethod
    def validate_reason(cls, value: object) -> str:
        return _text(value, field="reason", maximum=512)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @classmethod
    def create(cls, **values: object) -> ContextConflict:
        material = dict(values)
        for field in ("project_id", "source_a", "source_b"):
            material[field] = identifier(material[field], field=field)
        material["source_a"], material["source_b"] = sorted(
            (material["source_a"], material["source_b"])
        )
        material["conflict_id"] = identifier(
            material.get("conflict_id", "conflict-" + hashlib.sha256(
                f"{material['project_id']}:{material['source_a']}:{material['source_b']}".encode()
            ).hexdigest()),
            field="conflict_id",
        )
        material["reason"] = _text(material["reason"], field="reason", maximum=512)
        material["resolution_required"] = True
        return _with_fingerprint(cls, material)

    @model_validator(mode="after")
    def verify(self) -> ContextConflict:
        if self.source_a == self.source_b:
            raise ValueError("context conflict requires two sources")
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("context conflict fingerprint mismatch")
        return self


class EngineeringContextRequest(ContextContract):
    project_id: str
    tenant_id: str = "default-tenant"
    task: str
    constraints: tuple[str, ...] = ()
    external_search: bool = False

    @field_validator("project_id", "tenant_id", mode="before")
    @classmethod
    def validate_project_id(cls, value: object) -> str:
        return identifier(value, field="project_id")

    @field_validator("task", mode="before")
    @classmethod
    def validate_task(cls, value: object) -> str:
        return _text(value, field="task", maximum=512)

    @field_validator("constraints", mode="before")
    @classmethod
    def validate_constraints(cls, value: object) -> object:
        values = tuple_only(value, field="constraints")
        return tuple(_text(item, field="constraint", maximum=256) for item in values)

    @field_validator("external_search", mode="before")
    @classmethod
    def validate_external_search(cls, value: object) -> bool:
        if type(value) is not bool:
            raise TypeError("external_search is invalid")
        return value


class EngineeringContextQuery(ContextContract):
    """Compatibility request adapter for the previous query contract."""

    project_id: str
    tenant_id: str = "default-tenant"
    query: str
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("project_id", "tenant_id", mode="before")
    @classmethod
    def validate_project_id(cls, value: object) -> str:
        return identifier(value, field="project_id")

    @field_validator("query", mode="before")
    @classmethod
    def validate_query(cls, value: object) -> str:
        return _text(value, field="query", maximum=512)

    def to_request(self) -> EngineeringContextRequest:
        return EngineeringContextRequest(project_id=self.project_id, tenant_id=self.tenant_id, task=self.query)


def _empty_fingerprint(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _derive_context_id(material: dict[str, object]) -> str:
    payload = _jsonable(dict(material))
    payload.pop("context_id", None)
    payload.pop("fingerprint", None)
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "context-" + hashlib.sha256(encoded).hexdigest()


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump(mode="python"))
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(nested) for key, nested in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(nested) for nested in value]
    return value


class EngineeringContextSnapshot(ContextContract):
    context_id: str
    project_id: str
    tenant_id: str = "default-tenant"
    task_description: str
    memory_evidence: tuple[EngineeringContextEvidence, ...]
    graph_evidence: tuple[EngineeringContextEvidence, ...]
    knowledge_evidence: tuple[EngineeringContextEvidence, ...]
    multimodal_evidence: tuple[EngineeringContextEvidence, ...]
    conflicts: tuple[ContextConflict, ...]
    confidence: float
    provenance: tuple[ContextSourceReference, ...]
    verification_status: ContextVerificationStatus
    fingerprint: str

    @field_validator("context_id", "project_id", "tenant_id", mode="before")
    @classmethod
    def validate_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("task_description", mode="before")
    @classmethod
    def validate_task(cls, value: object) -> str:
        return _text(value, field="task_description", maximum=512)

    @field_validator(
        "memory_evidence", "graph_evidence", "knowledge_evidence",
        "multimodal_evidence", "conflicts", "provenance", mode="before",
    )
    @classmethod
    def validate_collections(cls, value: object, info) -> object:
        return tuple_only(value, field=info.field_name)

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_confidence(cls, value: object) -> float:
        return _confidence(value)

    @field_validator("verification_status", mode="before")
    @classmethod
    def validate_status(cls, value: object) -> ContextVerificationStatus:
        return ContextVerificationStatus(value)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @model_validator(mode="after")
    def verify(self) -> EngineeringContextSnapshot:
        evidence = (
            *self.memory_evidence,
            *self.graph_evidence,
            *self.knowledge_evidence,
            *self.multimodal_evidence,
        )
        if any(item.project_id != self.project_id or item.tenant_id != self.tenant_id for item in evidence):
            raise ValueError("context evidence identity binding mismatch")
        if any(conflict.project_id != self.project_id for conflict in self.conflicts):
            raise ValueError("context conflict project binding mismatch")
        source_ids = tuple(source.source_id for source in self.provenance)
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("context provenance must be unique")
        expected_id = _derive_context_id(self.model_dump(mode="python"))
        if self.context_id != expected_id:
            raise ValueError("context id mismatch")
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("context snapshot fingerprint mismatch")
        return self

    @classmethod
    def create(cls, **values: object) -> EngineeringContextSnapshot:
        material = dict(values)
        material["project_id"] = identifier(material["project_id"], field="project_id")
        material["tenant_id"] = identifier(material.get("tenant_id", "default-tenant"), field="tenant_id")
        material["task_description"] = _text(
            material["task_description"], field="task_description", maximum=512
        )
        for field in (
            "memory_evidence", "graph_evidence", "knowledge_evidence",
            "multimodal_evidence",
        ):
            material[field] = tuple(
                EngineeringContextEvidence.model_validate(copy.deepcopy(item))
                for item in tuple_only(material[field], field=field)
            )
        material["conflicts"] = tuple(
            ContextConflict.model_validate(copy.deepcopy(item))
            for item in tuple_only(material["conflicts"], field="conflicts")
        )
        material["provenance"] = tuple(
            ContextSourceReference.model_validate(copy.deepcopy(item))
            for item in tuple_only(material["provenance"], field="provenance")
        )
        material["confidence"] = _confidence(material["confidence"])
        material["verification_status"] = ContextVerificationStatus(
            material["verification_status"]
        )
        material.setdefault("context_id", _derive_context_id(material))
        material["context_id"] = identifier(material["context_id"], field="context_id")
        return _with_fingerprint(cls, material)

    @property
    def sources(self) -> tuple[ContextSourceReference, ...]:
        return self.provenance


def _validated_vision_observation(value: object) -> object:
    value_type = type(value)
    if (
        value_type.__name__ != "VisionObservation"
        or value_type.__module__ != "embedded_copilot.model_gateway.vision.contracts"
    ):
        raise TypeError("vision observation is invalid")
    validator = getattr(value_type, "model_validate", None)
    dumper = getattr(value, "model_dump", None)
    if not callable(validator) or not callable(dumper):
        raise TypeError("vision observation is invalid")
    checked = validator(copy.deepcopy(dumper(mode="python")))
    if type(checked) is not value_type:
        raise TypeError("vision observation is invalid")
    return checked


class MultimodalContextEvidence(ContextContract):
    """Project-neutral, observation-only projection for Context fusion."""

    source_type: Literal["vision"] = "vision"
    observation_id: str
    objects: tuple[str, ...] = ()
    components: tuple[str, ...] = ()
    interfaces: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()
    confidence: float
    evidence: tuple[str, ...] = ()
    observation_fingerprint: str
    fingerprint: str = _AUTO_FINGERPRINT

    @field_validator("observation_id", mode="before")
    @classmethod
    def validate_observation_id(cls, value: object) -> str:
        return identifier(value, field="observation_id")

    @field_validator(
        "objects", "components", "interfaces", "issues", "evidence", mode="before"
    )
    @classmethod
    def validate_collections(cls, value: object, info) -> tuple[str, ...]:
        return tuple(
            _text(item, field=f"{info.field_name}_item", maximum=256)
            for item in tuple_only(value, field=info.field_name)
        )

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_confidence(cls, value: object) -> float:
        return _confidence(value)

    @field_validator("observation_fingerprint", "fingerprint", mode="before")
    @classmethod
    def validate_fingerprints(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @classmethod
    def from_observation(cls, observation: object) -> MultimodalContextEvidence:
        checked = _validated_vision_observation(observation)
        return cls(
            observation_id=checked.observation_id,
            objects=checked.objects,
            components=checked.components,
            interfaces=checked.interfaces,
            issues=checked.issues,
            confidence=checked.confidence,
            evidence=checked.evidence,
            observation_fingerprint=checked.fingerprint,
        )

    @model_validator(mode="after")
    def verify(self) -> MultimodalContextEvidence:
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO_FINGERPRINT:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("multimodal context evidence fingerprint mismatch")
        return self


class MultimodalContextFusionRequest(ContextContract):
    """Explicit project and source binding for one Vision observation projection."""

    project_id: str
    snapshot: EngineeringContextSnapshot
    evidence: MultimodalContextEvidence
    source_reference: str
    target_id: str | None = None
    fingerprint: str = _AUTO_FINGERPRINT

    @field_validator("project_id", mode="before")
    @classmethod
    def validate_project_id(cls, value: object) -> str:
        return identifier(value, field="project_id")

    @field_validator("snapshot", mode="before")
    @classmethod
    def validate_snapshot(cls, value: object) -> EngineeringContextSnapshot:
        if isinstance(value, EngineeringContextSnapshot):
            material = value.model_dump(mode="python")
        elif isinstance(value, dict):
            material = value
        else:
            raise TypeError("context snapshot is invalid")
        return EngineeringContextSnapshot.model_validate(copy.deepcopy(material))

    @field_validator("evidence", mode="before")
    @classmethod
    def validate_evidence(cls, value: object) -> MultimodalContextEvidence:
        if isinstance(value, MultimodalContextEvidence):
            material = value.model_dump(mode="python")
        elif isinstance(value, dict):
            material = value
        else:
            raise TypeError("multimodal context evidence is invalid")
        return MultimodalContextEvidence.model_validate(copy.deepcopy(material))

    @field_validator("source_reference", mode="before")
    @classmethod
    def validate_source_reference(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("source_reference is invalid")
        checked = unicodedata.normalize("NFC", value).strip()
        match = _FUSION_SOURCE_REFERENCE.fullmatch(checked)
        reference_value = checked.split("://", maxsplit=1)[1] if match else ""
        if (
            not checked
            or len(checked) > 256
            or match is None
            or _FUSION_SOURCE_UNSAFE.search(reference_value)
            or re.match(r"^[A-Za-z]:[\\/]", reference_value)
            or "://" in reference_value
        ):
            raise ValueError("fusion source reference is invalid")
        return checked

    @field_validator("target_id", mode="before")
    @classmethod
    def validate_target_id(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="target_id")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def validate_fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @classmethod
    def from_observation(
        cls,
        *,
        project_id: object,
        snapshot: object,
        observation: object,
        source_reference: object,
        target_id: object = None,
    ) -> MultimodalContextFusionRequest:
        return cls(
            project_id=project_id,
            snapshot=snapshot,
            evidence=MultimodalContextEvidence.from_observation(observation),
            source_reference=source_reference,
            target_id=target_id,
        )

    @model_validator(mode="after")
    def verify(self) -> MultimodalContextFusionRequest:
        if self.snapshot.project_id != self.project_id:
            raise ValueError("multimodal fusion project binding mismatch")
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == _AUTO_FINGERPRINT:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("multimodal fusion request fingerprint mismatch")
        return self


def validate_evidence(value: object) -> EngineeringContextEvidence:
    if type(value) is not EngineeringContextEvidence:
        raise TypeError("context evidence is invalid")
    return EngineeringContextEvidence.model_validate(
        copy.deepcopy(value.model_dump(mode="python"))
    )


def tokens(value: str) -> frozenset[str]:
    return frozenset(item.casefold() for item in _TOKEN.findall(value))


__all__ = (
    "ContextCategory",
    "ContextConflict",
    "ContextContract",
    "ContextSourceReference",
    "ContextSourceType",
    "ContextVerificationStatus",
    "EvidenceTrustLevel",
    "EvidenceVerificationStatus",
    "EngineeringContextEvidence",
    "EngineeringContextQuery",
    "EngineeringContextRequest",
    "EngineeringContextSnapshot",
    "MultimodalContextEvidence",
    "MultimodalContextFusionRequest",
    "canonical_fingerprint",
    "fingerprint",
    "identifier",
    "reference",
    "tokens",
    "tuple_only",
    "validate_evidence",
)
