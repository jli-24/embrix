from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_AUTO = "sha256:" + "0" * 64
_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,159}$")


class ResearchContract(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
        validate_default=True,
    )


def _text(value: object, *, field: str, maximum: int = 1024) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    checked = value.strip()
    if (
        not checked
        or len(checked) > maximum
        or any(char in checked for char in ("\x00", "\r", "\n"))
        or re.search(r"(?:api[_ -]?key|access[_ -]?token|bearer|password|credential|secret)\s*[:=]", checked, re.IGNORECASE)
    ):
        raise ValueError(f"{field} is invalid")
    return checked


def _identifier(value: object, *, field: str) -> str:
    checked = _text(value, field=field, maximum=160)
    if _IDENTIFIER.fullmatch(checked) is None:
        raise ValueError(f"{field} is invalid")
    return checked


def _url(value: object, *, field: str = "url") -> str:
    checked = _text(value, field=field, maximum=2048)
    parsed = urlsplit(checked)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{field} is invalid")
    return parsed.geturl()


def _tuple(value: object, *, field: str) -> tuple[object, ...]:
    if type(value) not in {tuple, list}:
        raise TypeError(f"{field} must be a tuple or list")
    return tuple(value)


def _fingerprint(value: BaseModel, *, exclude: set[str] | None = None) -> str:
    excluded = set() if exclude is None else exclude
    payload = json.dumps(
        value.model_dump(mode="json", exclude={"fingerprint", *excluded}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _validate_fingerprint(value: object, *, field: str = "fingerprint") -> str:
    if value == _AUTO:
        return _AUTO
    if not isinstance(value, str) or _FINGERPRINT.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


class ResearchSourceType(StrEnum):
    GITHUB = "GITHUB"
    OSHWHUB = "OSHWHUB"
    VENDOR_REFERENCE = "VENDOR_REFERENCE"


class SourceCapability(StrEnum):
    SEARCH = "SEARCH"
    FETCH_METADATA = "FETCH_METADATA"
    FETCH_ARTIFACT_INDEX = "FETCH_ARTIFACT_INDEX"


class SourceCapabilityStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    LIMITED = "LIMITED"
    UNAVAILABLE = "UNAVAILABLE"


class ResearchStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    NO_RELEVANT_REFERENCE = "NO_RELEVANT_REFERENCE"
    UNAVAILABLE = "UNAVAILABLE"


class SourceResultStatus(StrEnum):
    SUCCESS = "SUCCESS"
    COMPLETE = "SUCCESS"  # legacy spelling retained for existing callers
    NO_RESULT = "NO_RESULT"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    ERROR = "ERROR"


class ResearchResultClassification(StrEnum):
    DIRECT_MATCH = "DIRECT_MATCH"
    ANALOGOUS_MATCH = "ANALOGOUS_MATCH"
    SUBSYSTEM_MATCH = "SUBSYSTEM_MATCH"
    OFFICIAL_REFERENCE_ONLY = "OFFICIAL_REFERENCE_ONLY"
    NO_RELEVANT_REFERENCE = "NO_RELEVANT_REFERENCE"


class FeatureDeltaKind(StrEnum):
    MATCHED = "MATCHED"
    REFERENCE_EXTRA = "REFERENCE_EXTRA"
    USER_ONLY = "USER_ONLY"
    DIFFERENT = "DIFFERENT"
    UNSPECIFIED = "UNSPECIFIED"


class FeatureEvidenceState(StrEnum):
    PRESENT = "PRESENT"
    INFERRED = "INFERRED"


class ReferenceProjectDecisionAction(StrEnum):
    REFERENCE = "REFERENCE"
    PARTIAL_REFERENCE = "PARTIAL_REFERENCE"
    EXCLUDE = "EXCLUDE"
    DEFER = "DEFER"


class FeatureDecisionAction(StrEnum):
    ADD = "ADD"
    REMOVE = "REMOVE"
    MODIFY = "MODIFY"
    KEEP = "KEEP"
    REJECT = "REJECT"
    DEFER = "DEFER"


class RequirementChangeAction(StrEnum):
    ADD = "ADD"
    REMOVE = "REMOVE"
    MODIFY = "MODIFY"
    KEEP = "KEEP"
    REJECT = "REJECT"
    DEFER = "DEFER"


class RequirementChangeStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    STALE = "STALE"


class ArtifactState(StrEnum):
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"
    UNVERIFIED = "UNVERIFIED"


class LicenseState(StrEnum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    NOT_DECLARED = "NOT_DECLARED"


class ReferenceType(StrEnum):
    FULL_PROJECT = "FULL_PROJECT"
    HARDWARE_REFERENCE = "HARDWARE_REFERENCE"
    FIRMWARE_REFERENCE = "FIRMWARE_REFERENCE"
    VENDOR_REFERENCE = "VENDOR_REFERENCE"
    PARTIAL_REFERENCE = "PARTIAL_REFERENCE"


class FactProvenance(StrEnum):
    README = "README"
    SCHEMATIC = "SCHEMATIC"
    PCB = "PCB"
    BOM = "BOM"
    FIRMWARE = "FIRMWARE"
    DOCUMENTATION = "DOCUMENTATION"
    IMAGE = "IMAGE"
    INFERRED_REFERENCE_ANALYSIS = "INFERRED_REFERENCE_ANALYSIS"


class RequirementSuggestionStatus(StrEnum):
    USER_REQUIRED = "USER_REQUIRED"
    REFERENCE_SUGGESTED = "REFERENCE_SUGGESTED"
    SUPPRESSED_BY_EXPLICIT_USER_REQUIREMENT = "SUPPRESSED_BY_EXPLICIT_USER_REQUIREMENT"
    USER_APPROVED = "USER_APPROVED"
    USER_REJECTED = "USER_REJECTED"
    DEFERRED = "DEFERRED"


class RequirementDecisionAction(StrEnum):
    PENDING = "PENDING"
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    MODIFY = "MODIFY"
    DEFER = "DEFER"
    SUPPRESSED = "SUPPRESSED"


class InitialRequirementBrief(ResearchContract):
    project_id: str
    conversation_id: str
    turn_id: str
    product_type: str | None = None
    explicit_features: tuple[str, ...] = ()
    explicit_constraints: tuple[str, ...] = ()
    preferred_platform: str | None = None
    known_interfaces: tuple[str, ...] = ()
    known_power_requirements: tuple[str, ...] = ()
    known_physical_constraints: tuple[str, ...] = ()
    user_goals: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    fingerprint: str = _AUTO

    @field_validator("project_id", "conversation_id", "turn_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("product_type", "preferred_platform", mode="before")
    @classmethod
    def _optional_text(cls, value: object, info) -> str | None:
        return None if value is None else _text(value, field=info.field_name, maximum=256)

    @field_validator(
        "explicit_features",
        "explicit_constraints",
        "known_interfaces",
        "known_power_requirements",
        "known_physical_constraints",
        "user_goals",
        "open_questions",
        mode="before",
    )
    @classmethod
    def _text_tuple(cls, value: object, info) -> tuple[str, ...]:
        return tuple(_text(item, field=info.field_name, maximum=1024) for item in _tuple(value, field=info.field_name))

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return _validate_fingerprint(value)

    @model_validator(mode="after")
    def _verify(self) -> InitialRequirementBrief:
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", _fingerprint(self))
        elif self.fingerprint != _fingerprint(self):
            raise ValueError("initial requirement brief fingerprint mismatch")
        return self


class ResearchQuery(ResearchContract):
    source_type: ResearchSourceType
    query: str

    @field_validator("query", mode="before")
    @classmethod
    def _query(cls, value: object) -> str:
        return _text(value, field="research query", maximum=512)


class ArtifactCompleteness(ResearchContract):
    schematic: ArtifactState = ArtifactState.UNKNOWN
    pcb: ArtifactState = ArtifactState.UNKNOWN
    bom: ArtifactState = ArtifactState.UNKNOWN
    firmware: ArtifactState = ArtifactState.UNKNOWN
    documentation: ArtifactState = ArtifactState.UNKNOWN

    @classmethod
    def from_mapping(cls, value: object) -> ArtifactCompleteness:
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise TypeError("artifact completeness is invalid")
        material: dict[str, object] = {}
        for field in ("schematic", "pcb", "bom", "firmware", "documentation"):
            raw = value.get(field, ArtifactState.UNKNOWN)
            material[field] = raw if isinstance(raw, ArtifactState) else ArtifactState(str(raw).strip().upper())
        return cls(**material)


class ReferenceFact(ResearchContract):
    fact_id: str
    category: str
    value: str
    artifact: FactProvenance
    location: str
    fingerprint: str = _AUTO

    @field_validator("fact_id", mode="before")
    @classmethod
    def _fact_id(cls, value: object) -> str:
        return _identifier(value, field="fact_id")

    @field_validator("category", "value", "location", mode="before")
    @classmethod
    def _fact_text(cls, value: object, info) -> str:
        return _text(value, field=info.field_name, maximum=512)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return _validate_fingerprint(value)

    @model_validator(mode="after")
    def _verify(self) -> ReferenceFact:
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", _fingerprint(self))
        elif self.fingerprint != _fingerprint(self):
            raise ValueError("reference fact fingerprint mismatch")
        return self


class ReferenceProjectCandidate(ResearchContract):
    source_type: ResearchSourceType
    project_identity: str
    url: str
    owner_or_author: str
    repository_name: str | None = None
    revision: str | None = None
    license_state: LicenseState = LicenseState.NOT_DECLARED
    license_name: str | None = None
    title: str
    description: str
    artifacts: ArtifactCompleteness = Field(default_factory=ArtifactCompleteness)
    extracted_facts: tuple[ReferenceFact, ...] = ()
    official: bool = False

    @field_validator("project_identity", "owner_or_author", mode="before")
    @classmethod
    def _identities(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("url", mode="before")
    @classmethod
    def _reference_url(cls, value: object) -> str:
        return _url(value, field="reference url")

    @field_validator("repository_name", "revision", "license_name", mode="before")
    @classmethod
    def _optional_values(cls, value: object, info) -> str | None:
        return None if value is None else _text(value, field=info.field_name, maximum=256)

    @field_validator("title", "description", mode="before")
    @classmethod
    def _descriptions(cls, value: object, info) -> str:
        return _text(value, field=info.field_name, maximum=2048 if info.field_name == "description" else 256)

    @field_validator("artifacts", mode="before")
    @classmethod
    def _artifacts(cls, value: object) -> ArtifactCompleteness:
        return value if isinstance(value, ArtifactCompleteness) else ArtifactCompleteness.from_mapping(value)

    @field_validator("extracted_facts", mode="before")
    @classmethod
    def _facts(cls, value: object) -> tuple[ReferenceFact, ...]:
        return tuple(
            item if isinstance(item, ReferenceFact) else ReferenceFact.model_validate(item)
            for item in _tuple(value, field="extracted_facts")
        )

    @model_validator(mode="after")
    def _verify(self) -> ReferenceProjectCandidate:
        if self.license_state is LicenseState.KNOWN and not self.license_name:
            raise ValueError("known license requires a license name")
        if self.license_state is not LicenseState.KNOWN and self.license_name is not None:
            raise ValueError("unknown license cannot have a license name")
        if len({fact.fact_id for fact in self.extracted_facts}) != len(self.extracted_facts):
            raise ValueError("reference facts must be unique")
        return self


class RelevanceAssessment(ResearchContract):
    product_type_overlap: bool
    platform_overlap: bool
    feature_overlap: tuple[str, ...] = ()
    hardware_artifact_overlap: bool = False
    firmware_overlap: bool = False
    rationale: tuple[str, ...] = ()

    @field_validator("feature_overlap", "rationale", mode="before")
    @classmethod
    def _values(cls, value: object, info) -> tuple[str, ...]:
        return tuple(_text(item, field=info.field_name, maximum=256) for item in _tuple(value, field=info.field_name))


class ReferenceProjectEvidence(ResearchContract):
    source_type: ResearchSourceType
    source_identity: str
    project_identity: str
    url: str
    revision: str | None = None
    license_state: LicenseState
    license_name: str | None = None
    title: str
    description: str
    reference_type: ReferenceType
    artifacts: ArtifactCompleteness
    extracted_facts: tuple[ReferenceFact, ...] = ()
    relevance: RelevanceAssessment
    provenance: tuple[str, ...] = ()
    classification: ResearchResultClassification = ResearchResultClassification.ANALOGOUS_MATCH
    fingerprint: str = _AUTO

    @field_validator("source_identity", "project_identity", mode="before")
    @classmethod
    def _identities(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("url", mode="before")
    @classmethod
    def _url(cls, value: object) -> str:
        return _url(value)

    @field_validator("revision", "license_name", mode="before")
    @classmethod
    def _optional(cls, value: object, info) -> str | None:
        return None if value is None else _text(value, field=info.field_name, maximum=256)

    @field_validator("title", "description", mode="before")
    @classmethod
    def _text_fields(cls, value: object, info) -> str:
        return _text(value, field=info.field_name, maximum=2048 if info.field_name == "description" else 256)

    @field_validator("extracted_facts", mode="before")
    @classmethod
    def _facts(cls, value: object) -> tuple[ReferenceFact, ...]:
        return tuple(
            item if isinstance(item, ReferenceFact) else ReferenceFact.model_validate(item)
            for item in _tuple(value, field="extracted_facts")
        )

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value: object) -> tuple[str, ...]:
        return tuple(
            _text(item, field="provenance", maximum=512)
            for item in _tuple(value, field="provenance")
        )

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return _validate_fingerprint(value)

    @model_validator(mode="after")
    def _verify(self) -> ReferenceProjectEvidence:
        if self.license_state is LicenseState.KNOWN and not self.license_name:
            raise ValueError("known license requires a license name")
        if self.license_state is not LicenseState.KNOWN and self.license_name is not None:
            raise ValueError("unknown license cannot have a license name")
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", _fingerprint(self))
        elif self.fingerprint != _fingerprint(self):
            raise ValueError("reference project evidence fingerprint mismatch")
        return self


class ReferenceFeatureDelta(ResearchContract):
    delta_id: str
    project_id: str
    conversation_id: str
    project_identity: str
    research_fingerprint: str
    reference_fingerprint: str
    feature: str
    delta: FeatureDeltaKind
    evidence_state: FeatureEvidenceState
    evidence_refs: tuple[str, ...] = ()
    description: str
    fingerprint: str = _AUTO

    @field_validator("delta_id", "project_id", "conversation_id", "project_identity", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("research_fingerprint", "reference_fingerprint", "fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return _validate_fingerprint(value, field=info.field_name)

    @field_validator("feature", "description", mode="before")
    @classmethod
    def _text_fields(cls, value: object, info) -> str:
        return _text(value, field=info.field_name, maximum=1024)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _refs(cls, value: object) -> tuple[str, ...]:
        return tuple(_identifier(item, field="evidence_ref") for item in _tuple(value, field="evidence_refs"))

    @model_validator(mode="after")
    def _verify(self) -> ReferenceFeatureDelta:
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", _fingerprint(self))
        elif self.fingerprint != _fingerprint(self):
            raise ValueError("reference feature delta fingerprint mismatch")
        return self


class ReferenceProjectReview(ResearchContract):
    project_id: str
    conversation_id: str
    project_identity: str
    research_fingerprint: str
    reference_fingerprint: str
    title: str
    description: str
    feature_deltas: tuple[ReferenceFeatureDelta, ...] = ()
    decision: ReferenceProjectDecisionAction = ReferenceProjectDecisionAction.DEFER
    selected_features: tuple[str, ...] = ()
    fingerprint: str = _AUTO

    @field_validator("project_id", "conversation_id", "project_identity", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("research_fingerprint", "reference_fingerprint", "fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return _validate_fingerprint(value, field=info.field_name)

    @field_validator("title", "description", mode="before")
    @classmethod
    def _text_fields(cls, value: object, info) -> str:
        return _text(value, field=info.field_name, maximum=2048 if info.field_name == "description" else 256)

    @field_validator("feature_deltas", mode="before")
    @classmethod
    def _deltas(cls, value: object) -> tuple[ReferenceFeatureDelta, ...]:
        return tuple(
            item if isinstance(item, ReferenceFeatureDelta)
            else ReferenceFeatureDelta.model_validate(item, strict=False)
            for item in _tuple(value, field="feature_deltas")
        )

    @field_validator("selected_features", mode="before")
    @classmethod
    def _selected(cls, value: object) -> tuple[str, ...]:
        return tuple(_text(item, field="selected_feature", maximum=256) for item in _tuple(value, field="selected_features"))

    @model_validator(mode="after")
    def _verify(self) -> ReferenceProjectReview:
        if any(
            item.project_id != self.project_id
            or item.conversation_id != self.conversation_id
            or item.project_identity != self.project_identity
            or item.research_fingerprint != self.research_fingerprint
            or item.reference_fingerprint != self.reference_fingerprint
            for item in self.feature_deltas
        ):
            raise ValueError("reference feature delta binding mismatch")
        if self.decision is ReferenceProjectDecisionAction.PARTIAL_REFERENCE and not self.selected_features:
            raise ValueError("partial reference requires selected features")
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", _fingerprint(self))
        elif self.fingerprint != _fingerprint(self):
            raise ValueError("reference project review fingerprint mismatch")
        return self


class ReferenceProjectDecision(ResearchContract):
    decision_id: str
    project_id: str
    conversation_id: str
    project_identity: str
    research_fingerprint: str
    reference_fingerprint: str
    decision: ReferenceProjectDecisionAction
    decided_by: str
    decided_at: datetime
    selected_features: tuple[str, ...] = ()
    fingerprint: str = _AUTO

    @field_validator("decision_id", "project_id", "conversation_id", "project_identity", "decided_by", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("research_fingerprint", "reference_fingerprint", "fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return _validate_fingerprint(value, field=info.field_name)

    @field_validator("selected_features", mode="before")
    @classmethod
    def _selected(cls, value: object) -> tuple[str, ...]:
        return tuple(_text(item, field="selected_feature", maximum=256) for item in _tuple(value, field="selected_features"))

    @model_validator(mode="after")
    def _verify(self) -> ReferenceProjectDecision:
        if self.decision is ReferenceProjectDecisionAction.PARTIAL_REFERENCE and not self.selected_features:
            raise ValueError("partial reference requires selected features")
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", _fingerprint(self))
        elif self.fingerprint != _fingerprint(self):
            raise ValueError("reference project decision fingerprint mismatch")
        return self


class FeatureDecision(ResearchContract):
    decision_id: str
    project_id: str
    conversation_id: str
    project_identity: str
    feature: str
    research_fingerprint: str
    reference_fingerprint: str
    action: FeatureDecisionAction
    value: str | None = None
    decided_by: str
    decided_at: datetime
    fingerprint: str = _AUTO

    @field_validator("decision_id", "project_id", "conversation_id", "project_identity", "decided_by", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("feature", mode="before")
    @classmethod
    def _feature(cls, value: object) -> str:
        return _text(value, field="feature", maximum=256)

    @field_validator("research_fingerprint", "reference_fingerprint", "fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return _validate_fingerprint(value, field=info.field_name)

    @field_validator("value", mode="before")
    @classmethod
    def _value(cls, value: object) -> str | None:
        return None if value is None else _text(value, field="feature value", maximum=1024)

    @model_validator(mode="after")
    def _verify(self) -> FeatureDecision:
        if self.action is FeatureDecisionAction.MODIFY and self.value is None:
            raise ValueError("feature modify decision requires value")
        if self.action is not FeatureDecisionAction.MODIFY and self.value is not None:
            raise ValueError("only feature modify decision may contain value")
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", _fingerprint(self))
        elif self.fingerprint != _fingerprint(self):
            raise ValueError("feature decision fingerprint mismatch")
        return self


class SourceCapabilityReport(ResearchContract):
    capability: SourceCapability
    status: SourceCapabilityStatus


class SourceResearchReport(ResearchContract):
    source_type: ResearchSourceType
    status: SourceResultStatus
    capabilities: tuple[SourceCapabilityReport, ...]
    queries: tuple[str, ...] = ()
    candidate_count: int = Field(default=0, ge=0)
    error_code: str | None = None

    @field_validator("queries", mode="before")
    @classmethod
    def _queries(cls, value: object) -> tuple[str, ...]:
        return tuple(_text(item, field="source query", maximum=512) for item in _tuple(value, field="queries"))

    @field_validator("error_code", mode="before")
    @classmethod
    def _error(cls, value: object) -> str | None:
        return None if value is None else _identifier(value, field="error_code")


class SourceResearchResponse(ResearchContract):
    source_type: ResearchSourceType
    status: SourceResultStatus
    candidates: tuple[ReferenceProjectCandidate, ...] = ()
    error_code: str | None = None

    @field_validator("candidates", mode="before")
    @classmethod
    def _candidates(cls, value: object) -> tuple[ReferenceProjectCandidate, ...]:
        return tuple(
            item if isinstance(item, ReferenceProjectCandidate) else ReferenceProjectCandidate.model_validate(item)
            for item in _tuple(value, field="candidates")
        )

    @field_validator("error_code", mode="before")
    @classmethod
    def _error(cls, value: object) -> str | None:
        return None if value is None else _identifier(value, field="error_code")


class ReferenceProjectEvidencePack(ResearchContract):
    project_id: str
    conversation_id: str
    brief_fingerprint: str
    queries: tuple[ResearchQuery, ...]
    projects: tuple[ReferenceProjectEvidence, ...] = ()
    source_reports: tuple[SourceResearchReport, ...]
    status: ResearchStatus
    retrieved_at: datetime
    fingerprint: str = _AUTO

    @field_validator("project_id", "conversation_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("brief_fingerprint", "fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return _validate_fingerprint(value, field=info.field_name)

    @field_validator("queries", "projects", "source_reports", mode="before")
    @classmethod
    def _collections(cls, value: object, info) -> tuple[object, ...]:
        return tuple(_tuple(value, field=info.field_name))

    @model_validator(mode="after")
    def _verify(self) -> ReferenceProjectEvidencePack:
        if len({item.project_identity for item in self.projects}) != len(self.projects):
            raise ValueError("reference project identities must be unique")
        if len({item.source_type for item in self.source_reports}) != len(self.source_reports):
            raise ValueError("source reports must be unique")
        if any(item.source_type not in {query.source_type for query in self.queries} for item in self.source_reports):
            raise ValueError("source report query binding is invalid")
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", _fingerprint(self, exclude={"retrieved_at"}))
        elif self.fingerprint != _fingerprint(self, exclude={"retrieved_at"}):
            raise ValueError("evidence pack fingerprint mismatch")
        return self


class RequirementSuggestion(ResearchContract):
    suggestion_id: str
    project_id: str
    conversation_id: str
    category: str
    suggested_requirement: str
    requirement_value: str
    reason: str
    source_project_refs: tuple[str, ...]
    supporting_evidence_refs: tuple[str, ...]
    current_requirement_status: RequirementSuggestionStatus
    user_decision: RequirementDecisionAction = RequirementDecisionAction.PENDING
    question_to_user: str | None = None
    fingerprint: str = _AUTO

    @field_validator("suggestion_id", "project_id", "conversation_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("category", "suggested_requirement", "requirement_value", "reason", mode="before")
    @classmethod
    def _suggestion_text(cls, value: object, info) -> str:
        return _text(value, field=info.field_name, maximum=2048)

    @field_validator("source_project_refs", "supporting_evidence_refs", mode="before")
    @classmethod
    def _refs(cls, value: object, info) -> tuple[str, ...]:
        return tuple(_identifier(item, field=info.field_name) for item in _tuple(value, field=info.field_name))

    @field_validator("question_to_user", mode="before")
    @classmethod
    def _question(cls, value: object) -> str | None:
        return None if value is None else _text(value, field="question_to_user", maximum=1024)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return _validate_fingerprint(value)

    @model_validator(mode="after")
    def _verify(self) -> RequirementSuggestion:
        if not self.source_project_refs or not self.supporting_evidence_refs:
            raise ValueError("requirement suggestion must have evidence")
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", _fingerprint(self))
        elif self.fingerprint != _fingerprint(self):
            raise ValueError("requirement suggestion fingerprint mismatch")
        return self


class RequirementSuggestionDecision(ResearchContract):
    suggestion_id: str
    project_id: str
    conversation_id: str
    action: RequirementDecisionAction
    decided_by: str
    decided_at: datetime
    modified_requirement: str | None = None
    fingerprint: str = _AUTO

    @field_validator("suggestion_id", "project_id", "conversation_id", "decided_by", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("modified_requirement", mode="before")
    @classmethod
    def _modified(cls, value: object) -> str | None:
        return None if value is None else _text(value, field="modified_requirement", maximum=2048)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return _validate_fingerprint(value)

    @model_validator(mode="after")
    def _verify(self) -> RequirementSuggestionDecision:
        if self.action is RequirementDecisionAction.MODIFY and self.modified_requirement is None:
            raise ValueError("modify decision requires modified requirement")
        if self.action is not RequirementDecisionAction.MODIFY and self.modified_requirement is not None:
            raise ValueError("only modify decision may contain modified requirement")
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", _fingerprint(self))
        elif self.fingerprint != _fingerprint(self):
            raise ValueError("requirement decision fingerprint mismatch")
        return self


class RequirementDecision(ResearchContract):
    decision_id: str
    project_id: str
    conversation_id: str
    action: RequirementChangeAction
    target: str
    category: str
    value: str | None = None
    current_requirement_id: str | None = None
    evidence_refs: tuple[str, ...] = ()
    source: str
    fingerprint: str = _AUTO

    @field_validator("decision_id", "project_id", "conversation_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("target", "category", "source", mode="before")
    @classmethod
    def _text_fields(cls, value: object, info) -> str:
        return _text(value, field=info.field_name, maximum=256)

    @field_validator("value", mode="before")
    @classmethod
    def _value(cls, value: object) -> str | None:
        return None if value is None else _text(value, field="decision value", maximum=1024)

    @field_validator("current_requirement_id", mode="before")
    @classmethod
    def _current(cls, value: object) -> str | None:
        return None if value is None else _identifier(value, field="current_requirement_id")

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _refs(cls, value: object) -> tuple[str, ...]:
        return tuple(_identifier(item, field="evidence_ref") for item in _tuple(value, field="evidence_refs"))

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return _validate_fingerprint(value)

    @model_validator(mode="after")
    def _verify(self) -> RequirementDecision:
        if self.action in {RequirementChangeAction.ADD, RequirementChangeAction.MODIFY} and self.value is None:
            raise ValueError("add or modify decision requires value")
        if self.action is RequirementChangeAction.REMOVE and self.current_requirement_id is None:
            raise ValueError("remove decision requires current requirement identity")
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", _fingerprint(self))
        elif self.fingerprint != _fingerprint(self):
            raise ValueError("requirement decision fingerprint mismatch")
        return self


class RequirementChangeProposal(ResearchContract):
    proposal_id: str
    project_id: str
    conversation_id: str
    base_requirement_fingerprint: str
    research_fingerprint: str | None = None
    reference_fingerprints: tuple[str, ...] = ()
    decisions: tuple[RequirementDecision, ...]
    summary: str
    status: RequirementChangeStatus = RequirementChangeStatus.PENDING
    created_by: str
    created_at: datetime
    fingerprint: str = _AUTO

    @field_validator("proposal_id", "project_id", "conversation_id", "created_by", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("base_requirement_fingerprint", "fingerprint", mode="before")
    @classmethod
    def _base_fp(cls, value: object, info) -> str:
        return _validate_fingerprint(value, field=info.field_name)

    @field_validator("research_fingerprint", mode="before")
    @classmethod
    def _research_fp(cls, value: object) -> str | None:
        return None if value is None else _validate_fingerprint(value, field="research_fingerprint")

    @field_validator("reference_fingerprints", mode="before")
    @classmethod
    def _reference_fps(cls, value: object) -> tuple[str, ...]:
        return tuple(_validate_fingerprint(item, field="reference_fingerprint") for item in _tuple(value, field="reference_fingerprints"))

    @field_validator("decisions", mode="before")
    @classmethod
    def _decisions(cls, value: object) -> tuple[RequirementDecision, ...]:
        return tuple(
            item if isinstance(item, RequirementDecision)
            else RequirementDecision.model_validate(item, strict=False)
            for item in _tuple(value, field="decisions")
        )

    @field_validator("summary", mode="before")
    @classmethod
    def _summary(cls, value: object) -> str:
        return _text(value, field="proposal summary", maximum=4096)

    @model_validator(mode="after")
    def _verify(self) -> RequirementChangeProposal:
        if not self.decisions:
            raise ValueError("requirement change proposal must contain decisions")
        if any(item.project_id != self.project_id or item.conversation_id != self.conversation_id for item in self.decisions):
            raise ValueError("requirement decision identity mismatch")
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", _fingerprint(self))
        elif self.fingerprint != _fingerprint(self):
            raise ValueError("requirement change proposal fingerprint mismatch")
        return self


class RequirementFinalConfirmation(ResearchContract):
    confirmation_id: str
    proposal_id: str
    project_id: str
    conversation_id: str
    proposal_fingerprint: str
    confirmed: bool
    confirmed_by: str
    confirmed_at: datetime
    fingerprint: str = _AUTO

    @field_validator("confirmation_id", "proposal_id", "project_id", "conversation_id", "confirmed_by", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("proposal_fingerprint", "fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return _validate_fingerprint(value, field=info.field_name)

    @model_validator(mode="after")
    def _verify(self) -> RequirementFinalConfirmation:
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", _fingerprint(self))
        elif self.fingerprint != _fingerprint(self):
            raise ValueError("requirement final confirmation fingerprint mismatch")
        return self


class ResearchIntentRefinement(ResearchContract):
    refinement_id: str
    project_id: str
    conversation_id: str
    product_positioning: str
    negative_constraints: tuple[str, ...] = ()
    created_by: str
    created_at: datetime
    fingerprint: str = _AUTO

    @field_validator("refinement_id", "project_id", "conversation_id", "created_by", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("product_positioning", mode="before")
    @classmethod
    def _positioning(cls, value: object) -> str:
        return _text(value, field="product positioning", maximum=1024)

    @field_validator("negative_constraints", mode="before")
    @classmethod
    def _constraints(cls, value: object) -> tuple[str, ...]:
        return tuple(_text(item, field="negative constraint", maximum=256) for item in _tuple(value, field="negative_constraints"))

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return _validate_fingerprint(value)

    @model_validator(mode="after")
    def _verify(self) -> ResearchIntentRefinement:
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", _fingerprint(self))
        elif self.fingerprint != _fingerprint(self):
            raise ValueError("research intent refinement fingerprint mismatch")
        return self


class EngineeringResearchSnapshot(ResearchContract):
    project_id: str
    conversation_id: str
    brief: InitialRequirementBrief
    evidence_pack: ReferenceProjectEvidencePack
    suggestions: tuple[RequirementSuggestion, ...] = ()
    classification: ResearchResultClassification = ResearchResultClassification.NO_RELEVANT_REFERENCE
    reference_reviews: tuple[ReferenceProjectReview, ...] = ()
    feature_decisions: tuple[FeatureDecision, ...] = ()
    fingerprint: str = _AUTO

    @field_validator("project_id", "conversation_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return _identifier(value, field=info.field_name)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return _validate_fingerprint(value)

    @field_validator("suggestions", mode="before")
    @classmethod
    def _suggestions(cls, value: object) -> tuple[RequirementSuggestion, ...]:
        return tuple(
            item if isinstance(item, RequirementSuggestion) else RequirementSuggestion.model_validate(item)
            for item in _tuple(value, field="suggestions")
        )

    @field_validator("reference_reviews", mode="before")
    @classmethod
    def _reference_reviews(cls, value: object) -> tuple[ReferenceProjectReview, ...]:
        return tuple(
            item if isinstance(item, ReferenceProjectReview)
            else ReferenceProjectReview.model_validate(item, strict=False)
            for item in _tuple(value, field="reference_reviews")
        )

    @field_validator("feature_decisions", mode="before")
    @classmethod
    def _feature_decisions(cls, value: object) -> tuple[FeatureDecision, ...]:
        return tuple(
            item if isinstance(item, FeatureDecision)
            else FeatureDecision.model_validate(item, strict=False)
            for item in _tuple(value, field="feature_decisions")
        )

    @model_validator(mode="after")
    def _verify(self) -> EngineeringResearchSnapshot:
        if (
            self.brief.project_id != self.project_id
            or self.brief.conversation_id != self.conversation_id
            or self.evidence_pack.project_id != self.project_id
            or self.evidence_pack.conversation_id != self.conversation_id
            or any(item.project_id != self.project_id or item.conversation_id != self.conversation_id for item in self.suggestions)
        ):
            raise ValueError("research snapshot identity mismatch")
        if len({item.suggestion_id for item in self.suggestions}) != len(self.suggestions):
            raise ValueError("research suggestion ids must be unique")
        if len({item.project_identity for item in self.reference_reviews}) != len(self.reference_reviews):
            raise ValueError("reference project reviews must be unique")
        project_by_identity = {item.project_identity: item for item in self.evidence_pack.projects}
        for review in self.reference_reviews:
            project = project_by_identity.get(review.project_identity)
            if project is None or review.reference_fingerprint != project.fingerprint:
                raise ValueError("reference project review is stale or unbound")
        if any(
            item.project_id != self.project_id
            or item.conversation_id != self.conversation_id
            or item.research_fingerprint != self.evidence_pack.fingerprint
            or item.project_identity not in project_by_identity
            or item.reference_fingerprint != project_by_identity[item.project_identity].fingerprint
            for item in self.feature_decisions
        ):
            raise ValueError("feature decision binding mismatch")
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", _fingerprint(self))
        elif self.fingerprint != _fingerprint(self):
            raise ValueError("research snapshot fingerprint mismatch")
        return self


__all__ = (
    "ArtifactCompleteness",
    "ArtifactState",
    "EngineeringResearchSnapshot",
    "FactProvenance",
    "FeatureDecision",
    "FeatureDecisionAction",
    "FeatureDeltaKind",
    "FeatureEvidenceState",
    "InitialRequirementBrief",
    "LicenseState",
    "ReferenceFact",
    "ReferenceFeatureDelta",
    "ReferenceProjectCandidate",
    "ReferenceProjectDecision",
    "ReferenceProjectDecisionAction",
    "ReferenceProjectEvidence",
    "ReferenceProjectEvidencePack",
    "ReferenceProjectReview",
    "ReferenceType",
    "RelevanceAssessment",
    "RequirementChangeAction",
    "RequirementChangeProposal",
    "RequirementChangeStatus",
    "RequirementDecision",
    "RequirementDecisionAction",
    "RequirementFinalConfirmation",
    "RequirementSuggestion",
    "RequirementSuggestionDecision",
    "RequirementSuggestionStatus",
    "ResearchContract",
    "ResearchIntentRefinement",
    "ResearchQuery",
    "ResearchResultClassification",
    "ResearchSourceType",
    "ResearchStatus",
    "SourceCapability",
    "SourceCapabilityReport",
    "SourceCapabilityStatus",
    "SourceResearchReport",
    "SourceResearchResponse",
    "SourceResultStatus",
)
