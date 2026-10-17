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

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+\-]{0,127}$")
_FP = re.compile(r"^sha256:[a-f0-9]{64}$")
_REFERENCE = re.compile(r"^(?:artifact|workspace|knowledge|evidence)://[A-Za-z0-9][A-Za-z0-9._~:/+\-]{0,255}$")
_RELATIVE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/+\-]{0,255}$")
_UNSAFE = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|bearer|password|credential|secret|token)\s*[:=]?|"
    r"raw\s+(?:model\s+)?output|hidden\s+reasoning|chain\s+of\s+thought|"
    r"(?:[A-Za-z]:[\\/]|\\\\|file://|data:|base64|\x00|\r|\n)",
    re.IGNORECASE,
)
ModelT = TypeVar("ModelT", bound=BaseModel)


class V64Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class ValidationType(StrEnum):
    FIRMWARE_STRUCTURE = "FIRMWARE_STRUCTURE"
    FIRMWARE_CONSTRAINT = "FIRMWARE_CONSTRAINT"
    DEBUG_EVIDENCE = "DEBUG_EVIDENCE"
    HARDWARE_CONSTRAINT = "HARDWARE_CONSTRAINT"
    PCB_CONNECTIVITY = "PCB_CONNECTIVITY"
    REQUIREMENT_COMPLIANCE = "REQUIREMENT_COMPLIANCE"
    ARCHITECTURE_CONSTRAINT = "ARCHITECTURE_CONSTRAINT"
    CROSS_DOMAIN_CONSISTENCY = "CROSS_DOMAIN_CONSISTENCY"


class ValidationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"


class FindingType(StrEnum):
    DEFECT_CANDIDATE = "DEFECT_CANDIDATE"
    CONFIGURATION_CONFLICT = "CONFIGURATION_CONFLICT"
    REQUIREMENT_VIOLATION = "REQUIREMENT_VIOLATION"
    ARCHITECTURE_RISK = "ARCHITECTURE_RISK"
    DEBUG_ROOT_CAUSE_CANDIDATE = "DEBUG_ROOT_CAUSE_CANDIDATE"
    PCB_CONNECTIVITY_CONFLICT = "PCB_CONNECTIVITY_CONFLICT"
    HARDWARE_FIRMWARE_CONFLICT = "HARDWARE_FIRMWARE_CONFLICT"
    OPTIMIZATION_OPPORTUNITY = "OPTIMIZATION_OPPORTUNITY"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class FindingSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ProposalType(StrEnum):
    REPAIR = "REPAIR"
    OPTIMIZATION = "OPTIMIZATION"
    CONFIGURATION_CHANGE = "CONFIGURATION_CHANGE"
    ARCHITECTURE_CHANGE = "ARCHITECTURE_CHANGE"
    PCB_CHANGE = "PCB_CHANGE"
    HARDWARE_REVIEW = "HARDWARE_REVIEW"


class ProposalStatus(StrEnum):
    DRAFT = "DRAFT"
    WAIT_REVIEW = "WAIT_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    STALE = "STALE"
    REVOKED = "REVOKED"
    SUPERSEDED = "SUPERSEDED"


class AuditEventType(StrEnum):
    VALIDATION_CREATED = "VALIDATION_CREATED"
    VALIDATION_COMPLETED = "VALIDATION_COMPLETED"
    FINDING_CREATED = "FINDING_CREATED"
    PROPOSAL_CREATED = "PROPOSAL_CREATED"
    PROPOSAL_SUBMITTED = "PROPOSAL_SUBMITTED"
    PROPOSAL_APPROVED = "PROPOSAL_APPROVED"
    PROPOSAL_REJECTED = "PROPOSAL_REJECTED"
    PROPOSAL_STALE = "PROPOSAL_STALE"
    PROPOSAL_REVOKED = "PROPOSAL_REVOKED"


def _text(value: object, *, field: str, maximum: int = 2048) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = unicodedata.normalize("NFC", value).strip()
    if not checked or len(checked) > maximum or _UNSAFE.search(checked):
        raise ValueError(f"{field} is unsafe")
    return checked


def safe_text(value: object, *, field: str, maximum: int = 2048) -> str:
    """Public boundary helper for API adapters; keeps unsafe text rules canonical."""
    return _text(value, field=field, maximum=maximum)


def identifier(value: object, *, field: str) -> str:
    checked = _text(value, field=field, maximum=128)
    if _ID.fullmatch(checked) is None:
        raise ValueError(f"{field} is invalid")
    return checked


def fingerprint(value: object, *, field: str = "fingerprint") -> str:
    if not isinstance(value, str) or _FP.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def utc_datetime(value: object, *, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"{field} is invalid") from error
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be UTC")
    return value.astimezone(UTC)


def _tuple(value: object, *, field: str) -> tuple[object, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{field} must be a tuple")
    return copy.deepcopy(value)


def _ids(value: object, *, field: str, allow_empty: bool = True) -> tuple[str, ...]:
    items = tuple(identifier(item, field=field) for item in _tuple(value, field=field))
    if not allow_empty and not items:
        raise ValueError(f"{field} must not be empty")
    if len(items) != len(set(items)):
        raise ValueError(f"{field} must be unique")
    return tuple(sorted(items))


def canonical_fingerprint(value: BaseModel, *, exclude: set[str] | frozenset[str] = frozenset()) -> str:
    payload = value.model_dump(mode="json", exclude=exclude)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _create(cls: type[ModelT], values: dict[str, object], *, exclude: set[str] | frozenset[str]) -> ModelT:
    material = dict(values)
    fp_name = "fingerprint" if "fingerprint" in cls.model_fields else next((name for name in ("proposal_fingerprint", "validation_fingerprint", "finding_fingerprint") if name in cls.model_fields), "fingerprint")
    material.pop(fp_name, None)
    if isinstance(material.get("target_identity"), dict):
        material["target_identity"] = TargetIdentity.model_validate(material["target_identity"])
    if isinstance(material.get("findings"), tuple):
        material["findings"] = tuple(
            item if isinstance(item, EngineeringFinding) else EngineeringFinding.model_validate(item)
            for item in material["findings"]
        )
    if isinstance(material.get("proposed_changes"), tuple):
        material["proposed_changes"] = tuple(
            item if isinstance(item, ProposalChange) else ProposalChange.model_validate(item)
            for item in material["proposed_changes"]
        )
    provisional = cls.model_construct(**{**material, fp_name: "sha256:" + "0" * 64})
    material[fp_name] = canonical_fingerprint(provisional, exclude=set(exclude) | {fp_name})
    return cls.model_validate(material)


class TargetIdentity(V64Contract):
    identity: str
    kind: str

    @field_validator("identity", "kind", mode="before")
    @classmethod
    def _fields(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)


class EvidenceRecord(V64Contract):
    evidence_id: str
    tenant_id: str
    project_id: str
    source_reference: str
    evidence_fingerprint: str
    status: str = "ACTIVE"
    summary: str = "Structured engineering evidence."
    facts: tuple[tuple[str, str], ...] = ()

    @field_validator("evidence_id", "tenant_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("source_reference", mode="before")
    @classmethod
    def _reference(cls, value: object) -> str:
        if not isinstance(value, str) or _REFERENCE.fullmatch(value) is None:
            raise ValueError("source_reference is invalid")
        return value

    @field_validator("evidence_fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return fingerprint(value, field="evidence_fingerprint")

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> str:
        return identifier(value, field="status")

    @field_validator("summary", mode="before")
    @classmethod
    def _summary(cls, value: object) -> str:
        return _text(value, field="summary", maximum=1024)

    @field_validator("facts", mode="before")
    @classmethod
    def _facts(cls, value: object) -> tuple[tuple[str, str], ...]:
        values = _tuple(value, field="facts")
        result: list[tuple[str, str]] = []
        for item in values:
            if type(item) is not tuple or len(item) != 2:
                raise TypeError("fact must be a pair")
            result.append((_text(item[0], field="fact_key", maximum=128), _text(item[1], field="fact_value", maximum=512)))
        return tuple(sorted(result))


class ApprovedContextRecord(V64Contract):
    context_id: str
    tenant_id: str
    project_id: str
    context_kind: str
    status: str
    context_fingerprint: str

    @field_validator("context_id", "tenant_id", "project_id", "context_kind", "status", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("context_fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return fingerprint(value, field="context_fingerprint")


class ValidationRequest(V64Contract):
    validation_id: str
    tenant_id: str
    project_id: str
    requester_id: str
    validation_type: ValidationType
    target_identity: TargetIdentity
    target_fingerprint: str
    project_fingerprint: str
    evidence_ids: tuple[str, ...] = ()
    memory_ids: tuple[str, ...] = ()
    knowledge_ids: tuple[str, ...] = ()
    requested_at: datetime

    @field_validator("validation_id", "tenant_id", "project_id", "requester_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("target_identity", mode="before")
    @classmethod
    def _target(cls, value: object) -> TargetIdentity:
        return TargetIdentity.model_validate(copy.deepcopy(value.model_dump(mode="python") if type(value) is TargetIdentity else value))

    @field_validator("target_fingerprint", "project_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("validation_type", mode="before")
    @classmethod
    def _validation_type(cls, value: object) -> ValidationType:
        return value if isinstance(value, ValidationType) else ValidationType(value)

    @field_validator("evidence_ids", "memory_ids", "knowledge_ids", mode="before")
    @classmethod
    def _ref_ids(cls, value: object, info) -> tuple[str, ...]:
        return _ids(value, field=info.field_name)

    @field_validator("requested_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="requested_at")

    @classmethod
    def create(cls, **values: object) -> "ValidationRequest":
        return cls.model_validate(values)

    @property
    def fingerprint(self) -> str:
        return canonical_fingerprint(self)


class FindingDraft(V64Contract):
    finding_type: FindingType
    severity: FindingSeverity
    subject: str
    summary: str
    confidence: float
    limitations: tuple[str, ...] = ()

    @field_validator("subject", "summary", mode="before")
    @classmethod
    def _text_fields(cls, value: object, info) -> str:
        return _text(value, field=info.field_name)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: object) -> float:
        if type(value) is not float or not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError("confidence is invalid")
        return value

    @field_validator("limitations", mode="before")
    @classmethod
    def _limitations(cls, value: object) -> tuple[str, ...]:
        return tuple(_text(item, field="limitation", maximum=512) for item in _tuple(value, field="limitations"))


class EngineeringFinding(V64Contract):
    finding_id: str
    tenant_id: str
    project_id: str
    finding_type: FindingType
    severity: FindingSeverity
    subject: str
    summary: str
    evidence_ids: tuple[str, ...]
    memory_ids: tuple[str, ...]
    knowledge_ids: tuple[str, ...]
    validation_id: str
    confidence: float
    limitations: tuple[str, ...]
    target_identity: TargetIdentity
    target_fingerprint: str
    finding_fingerprint: str
    created_at: datetime

    @field_validator("finding_id", "tenant_id", "project_id", "validation_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("target_identity", mode="before")
    @classmethod
    def _target(cls, value: object) -> TargetIdentity:
        return TargetIdentity.model_validate(copy.deepcopy(value.model_dump(mode="python") if type(value) is TargetIdentity else value))

    @field_validator("subject", "summary", mode="before")
    @classmethod
    def _texts(cls, value: object, info) -> str:
        return _text(value, field=info.field_name)

    @field_validator("finding_type", mode="before")
    @classmethod
    def _finding_type(cls, value: object) -> FindingType:
        return value if isinstance(value, FindingType) else FindingType(value)

    @field_validator("severity", mode="before")
    @classmethod
    def _severity(cls, value: object) -> FindingSeverity:
        return value if isinstance(value, FindingSeverity) else FindingSeverity(value)

    @field_validator("evidence_ids", "memory_ids", "knowledge_ids", "limitations", mode="before")
    @classmethod
    def _tuples(cls, value: object, info) -> tuple[str, ...]:
        if info.field_name == "limitations":
            return tuple(_text(item, field="limitation", maximum=512) for item in _tuple(value, field="limitations"))
        return _ids(value, field=info.field_name)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: object) -> float:
        if type(value) is not float or not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError("confidence is invalid")
        return value

    @field_validator("target_fingerprint", "finding_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("created_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="created_at")

    @classmethod
    def create(cls, **values: object) -> "EngineeringFinding":
        return _create(cls, values, exclude={"finding_fingerprint"})

    @model_validator(mode="after")
    def _verify(self) -> "EngineeringFinding":
        if self.finding_fingerprint != canonical_fingerprint(self, exclude={"finding_fingerprint"}):
            raise ValueError("finding fingerprint mismatch")
        return self


class ValidationResult(V64Contract):
    validation_id: str
    tenant_id: str
    project_id: str
    validation_type: ValidationType
    status: ValidationStatus
    evidence_ids: tuple[str, ...]
    memory_ids: tuple[str, ...]
    knowledge_ids: tuple[str, ...]
    findings: tuple[EngineeringFinding, ...]
    target_identity: TargetIdentity
    target_fingerprint: str
    project_fingerprint: str
    limitations: tuple[str, ...]
    validated_at: datetime
    validation_fingerprint: str

    @field_validator("validation_id", "tenant_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("target_identity", mode="before")
    @classmethod
    def _target(cls, value: object) -> TargetIdentity:
        return TargetIdentity.model_validate(copy.deepcopy(value.model_dump(mode="python") if type(value) is TargetIdentity else value))

    @field_validator("validation_type", mode="before")
    @classmethod
    def _validation_type(cls, value: object) -> ValidationType:
        return value if isinstance(value, ValidationType) else ValidationType(value)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> ValidationStatus:
        return value if isinstance(value, ValidationStatus) else ValidationStatus(value)

    @field_validator("evidence_ids", "memory_ids", "knowledge_ids", mode="before")
    @classmethod
    def _refs(cls, value: object, info) -> tuple[str, ...]:
        return _ids(value, field=info.field_name)

    @field_validator("findings", mode="before")
    @classmethod
    def _findings(cls, value: object) -> tuple[EngineeringFinding, ...]:
        items = tuple(EngineeringFinding.model_validate(copy.deepcopy(item.model_dump(mode="python") if type(item) is EngineeringFinding else item)) for item in _tuple(value, field="findings"))
        return tuple(sorted(items, key=lambda item: item.finding_id))

    @field_validator("limitations", mode="before")
    @classmethod
    def _limitations(cls, value: object) -> tuple[str, ...]:
        return tuple(_text(item, field="limitation", maximum=512) for item in _tuple(value, field="limitations"))

    @field_validator("target_fingerprint", "project_fingerprint", "validation_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("validated_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="validated_at")

    @classmethod
    def create(cls, **values: object) -> "ValidationResult":
        # The field validator canonicalizes findings by finding_id.  Apply the
        # same normalization before hashing so creation and revalidation use
        # one semantic payload when a rule returns multiple findings.
        raw_findings = values.get("findings")
        if type(raw_findings) is tuple:
            findings = tuple(
                item if isinstance(item, EngineeringFinding) else EngineeringFinding.model_validate(item)
                for item in raw_findings
            )
            values["findings"] = tuple(sorted(findings, key=lambda item: item.finding_id))
        return _create(cls, values, exclude={"validation_fingerprint", "status"})

    @model_validator(mode="after")
    def _verify(self) -> "ValidationResult":
        if any(item.project_id != self.project_id or item.tenant_id != self.tenant_id or item.validation_id != self.validation_id for item in self.findings):
            raise ValueError("finding binding mismatch")
        if self.validation_fingerprint != canonical_fingerprint(self, exclude={"validation_fingerprint", "status"}):
            raise ValueError("validation fingerprint mismatch")
        return self


class ProposalChange(V64Contract):
    change_kind: str
    file_reference: str | None = None
    symbol: str | None = None
    logical_change: str
    before_fingerprint: str | None = None
    expected_effect: str

    @field_validator("change_kind", mode="before")
    @classmethod
    def _kind(cls, value: object) -> str:
        return identifier(value, field="change_kind")

    @field_validator("file_reference", mode="before")
    @classmethod
    def _file(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or _RELATIVE_FILE.fullmatch(value.replace("\\", "/")) is None or ".." in value.replace("\\", "/").split("/"):
            raise ValueError("file_reference is invalid")
        return value.replace("\\", "/")

    @field_validator("symbol", "logical_change", "expected_effect", mode="before")
    @classmethod
    def _text_fields(cls, value: object, info) -> str | None:
        return None if value is None and info.field_name == "symbol" else _text(value, field=info.field_name)

    @field_validator("before_fingerprint", mode="before")
    @classmethod
    def _before_fp(cls, value: object) -> str | None:
        return None if value is None else fingerprint(value, field="before_fingerprint")


class OperationProposal(V64Contract):
    proposal_id: str
    tenant_id: str
    project_id: str
    proposal_type: ProposalType
    subject: str
    summary: str
    rationale: str
    finding_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    memory_ids: tuple[str, ...]
    knowledge_ids: tuple[str, ...]
    validation_ids: tuple[str, ...]
    target_identity: TargetIdentity
    target_fingerprint: str
    project_fingerprint: str
    evidence_fingerprint: str
    finding_fingerprint: str
    validation_fingerprint: str
    risk: str
    confidence: float
    limitations: tuple[str, ...]
    proposed_changes: tuple[ProposalChange, ...]
    validation_plan: tuple[str, ...]
    rollback_plan: tuple[str, ...]
    review_required: bool
    status: ProposalStatus
    proposal_fingerprint: str
    created_at: datetime

    @field_validator("proposal_id", "tenant_id", "project_id", "risk", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("target_identity", mode="before")
    @classmethod
    def _target(cls, value: object) -> TargetIdentity:
        return TargetIdentity.model_validate(copy.deepcopy(value.model_dump(mode="python") if type(value) is TargetIdentity else value))

    @field_validator("subject", "summary", "rationale", mode="before")
    @classmethod
    def _texts(cls, value: object, info) -> str:
        return _text(value, field=info.field_name)

    @field_validator("proposal_type", mode="before")
    @classmethod
    def _proposal_type(cls, value: object) -> ProposalType:
        return value if isinstance(value, ProposalType) else ProposalType(value)

    @field_validator("status", mode="before")
    @classmethod
    def _status(cls, value: object) -> ProposalStatus:
        return value if isinstance(value, ProposalStatus) else ProposalStatus(value)

    @field_validator("finding_ids", "evidence_ids", "memory_ids", "knowledge_ids", "validation_ids", mode="before")
    @classmethod
    def _refs(cls, value: object, info) -> tuple[str, ...]:
        return _ids(value, field=info.field_name, allow_empty=info.field_name in {"memory_ids", "knowledge_ids"})

    @field_validator("limitations", "validation_plan", "rollback_plan", mode="before")
    @classmethod
    def _text_tuples(cls, value: object, info) -> tuple[str, ...]:
        return tuple(_text(item, field=info.field_name, maximum=1024) for item in _tuple(value, field=info.field_name))

    @field_validator("proposed_changes", mode="before")
    @classmethod
    def _changes(cls, value: object) -> tuple[ProposalChange, ...]:
        items = tuple(ProposalChange.model_validate(copy.deepcopy(item.model_dump(mode="python") if type(item) is ProposalChange else item)) for item in _tuple(value, field="proposed_changes"))
        if not items:
            raise ValueError("proposed_changes must not be empty")
        return items

    @field_validator("target_fingerprint", "project_fingerprint", "evidence_fingerprint", "finding_fingerprint", "validation_fingerprint", "proposal_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: object) -> float:
        if type(value) is not float or not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError("confidence is invalid")
        return value

    @field_validator("created_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="created_at")

    @classmethod
    def create(cls, **values: object) -> "OperationProposal":
        return _create(cls, values, exclude={"proposal_fingerprint", "status"})

    def with_status(self, status: ProposalStatus) -> "OperationProposal":
        return self.model_copy(update={"status": ProposalStatus(status)})

    @model_validator(mode="after")
    def _verify(self) -> "OperationProposal":
        if self.proposal_fingerprint != canonical_fingerprint(self, exclude={"proposal_fingerprint", "status"}):
            raise ValueError("proposal fingerprint mismatch")
        return self


class ProposalReviewBinding(V64Contract):
    review_id: str
    tenant_id: str
    project_id: str
    proposal_id: str
    proposal_fingerprint: str
    project_fingerprint: str
    target_identity: TargetIdentity
    target_fingerprint: str
    evidence_fingerprint: str
    finding_fingerprint: str
    validation_fingerprint: str
    requester_id: str
    reviewer_id: str | None = None
    decision: str | None = None
    status: str = "PENDING"
    created_at: datetime
    decided_at: datetime | None = None
    fingerprint: str

    @field_validator("review_id", "tenant_id", "project_id", "proposal_id", "requester_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("target_identity", mode="before")
    @classmethod
    def _target(cls, value: object) -> TargetIdentity:
        return TargetIdentity.model_validate(copy.deepcopy(value.model_dump(mode="python") if type(value) is TargetIdentity else value))

    @field_validator("reviewer_id", mode="before")
    @classmethod
    def _reviewer(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="reviewer_id")

    @field_validator("proposal_fingerprint", "project_fingerprint", "target_fingerprint", "evidence_fingerprint", "finding_fingerprint", "validation_fingerprint", "fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("decision", "status", mode="before")
    @classmethod
    def _state(cls, value: object, info) -> str | None:
        return None if value is None and info.field_name == "decision" else identifier(value, field=info.field_name)

    @field_validator("created_at", "decided_at", mode="before")
    @classmethod
    def _times(cls, value: object, info) -> datetime | None:
        return None if value is None else utc_datetime(value, field=info.field_name)

    @classmethod
    def create(cls, **values: object) -> "ProposalReviewBinding":
        return _create(cls, values, exclude={"fingerprint"})

    @model_validator(mode="after")
    def _verify(self) -> "ProposalReviewBinding":
        if self.fingerprint != canonical_fingerprint(self, exclude={"fingerprint"}):
            raise ValueError("review binding fingerprint mismatch")
        return self


class StatusHistory(V64Contract):
    history_id: str
    tenant_id: str
    project_id: str
    object_type: str
    object_id: str
    from_status: str | None = None
    to_status: str
    actor_id: str
    object_fingerprint: str
    occurred_at: datetime

    @field_validator("history_id", "tenant_id", "project_id", "object_type", "object_id", "to_status", "actor_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("from_status", mode="before")
    @classmethod
    def _from(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="from_status")

    @field_validator("object_fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return fingerprint(value, field="object_fingerprint")

    @field_validator("occurred_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="occurred_at")


class AuditEvent(V64Contract):
    event_id: str
    event_type: AuditEventType
    tenant_id: str
    project_id: str
    object_type: str
    object_id: str
    object_fingerprint: str
    metadata: tuple[tuple[str, str], ...] = ()
    occurred_at: datetime

    @field_validator("event_id", "tenant_id", "project_id", "object_type", "object_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("event_type", mode="before")
    @classmethod
    def _event_type(cls, value: object) -> AuditEventType:
        return value if isinstance(value, AuditEventType) else AuditEventType(value)

    @field_validator("object_fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return fingerprint(value, field="object_fingerprint")

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata(cls, value: object) -> tuple[tuple[str, str], ...]:
        values = _tuple(value, field="metadata")
        result: list[tuple[str, str]] = []
        for item in values:
            if type(item) is not tuple or len(item) != 2:
                raise TypeError("metadata must contain pairs")
            result.append((identifier(item[0], field="metadata_key"), _text(item[1], field="metadata_value", maximum=512)))
        return tuple(sorted(result))

    @field_validator("occurred_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="occurred_at")


__all__ = tuple(name for name in globals() if name in {
    "ApprovedContextRecord", "AuditEvent", "AuditEventType", "EngineeringFinding", "EvidenceRecord", "FindingDraft", "FindingSeverity", "FindingType", "OperationProposal", "ProposalChange", "ProposalReviewBinding", "ProposalStatus", "ProposalType", "StatusHistory", "TargetIdentity", "ValidationRequest", "ValidationResult", "ValidationStatus", "ValidationType", "V64Contract", "canonical_fingerprint", "fingerprint", "identifier", "utc_datetime",
})
