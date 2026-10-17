from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _text(value: object, *, field: str, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{field} is invalid")
    if any(token in value.casefold() for token in ("secret", "credential", "api_key", "token")):
        raise ValueError(f"{field} contains sensitive content")
    return value.strip()


def _optional_text(value: object, *, field: str, maximum: int = 512) -> str | None:
    if value is None:
        return None
    return _text(value, field=field, maximum=maximum)


class ProjectionUnavailable(RuntimeError):
    """A safe, stable projection dependency failure."""


class ProjectionBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DesktopProjectionIdentity(ProjectionBase):
    tenant_id: str
    requester_id: str
    project_id: str
    project_fingerprint: str

    @field_validator("tenant_id", "requester_id", "project_id", "project_fingerprint")
    @classmethod
    def validate_identity(cls, value: object, info) -> str:
        return _text(value, field=info.field_name, maximum=160)


class DesktopProjectProjection(ProjectionBase):
    project_id: str
    tenant_id: str
    name: str
    relative_path: str
    status: str
    fingerprint: str
    initialization_status: str


class DesktopSafeItem(ProjectionBase):
    reference_id: str
    kind: str
    status: str | None = None
    summary: str | None = None
    fingerprint: str | None = None
    source_references: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("reference_id", "kind")
    @classmethod
    def validate_required_text(cls, value: object, info) -> str:
        return _text(value, field=info.field_name)

    @field_validator("status", "summary", "fingerprint")
    @classmethod
    def validate_optional_text(cls, value: object, info) -> str | None:
        return _optional_text(value, field=info.field_name)


class DesktopEvidenceProjection(ProjectionBase):
    evidence_id: str
    source_type: str
    subject: str
    summary: str | None = None
    source_reference: str | None = None
    fingerprint: str
    trust_level: str | None = None
    verification_status: str
    confidence: str | None = None
    observed_at: datetime | None = None
    limitations: tuple[str, ...] = ()


class DesktopBoundedPage(ProjectionBase):
    items: tuple[DesktopSafeItem, ...] = ()
    next_cursor: str | None = None
    limit: int = Field(ge=1, le=100)
    total_available: int | None = Field(default=None, ge=0)


class DesktopEvidencePage(ProjectionBase):
    items: tuple[DesktopEvidenceProjection, ...] = ()
    next_cursor: str | None = None
    limit: int = Field(ge=1, le=100)
    total_available: int | None = Field(default=None, ge=0)


class DesktopWorkflowSummary(ProjectionBase):
    status: Literal[
        "NO_PROJECT",
        "EVIDENCE_COLLECTED",
        "VALIDATION_RECORDED",
        "VALIDATION_FAILED",
        "FINDING_CREATED",
        "WAIT_REVIEW",
        "APPROVED",
        "CHANGE_PREVIEW_READY",
        "APPLY_APPROVAL_REQUIRED",
        "APPLIED",
        "EXECUTION_UNAVAILABLE",
        "UNAVAILABLE",
    ]
    current_reference: str | None = None
    stages: tuple[str, ...] = ()
    source_references: tuple[str, ...] = ()


class DesktopCapabilityProjection(ProjectionBase):
    capability: str
    status: Literal["AVAILABLE", "DEGRADED", "UNAVAILABLE", "UNVERIFIED"]
    reason_code: str
    detail: str


class DesktopFactReference(ProjectionBase):
    domain: Literal["REQUIREMENT", "ARCHITECTURE", "CIRCUIT", "SCHEMATIC", "PCB", "FIRMWARE", "EVIDENCE"]
    owner: str
    project_id: str
    conversation_id: str | None = None
    object_id: str | None = None
    revision: str | None = None
    fingerprint: str | None = None
    source_reference: str | None = None
    source_fingerprint: str | None = None
    currentness: Literal["CURRENT", "STALE", "BLOCKED", "UNAVAILABLE", "UNKNOWN"]
    reason_code: str

    @field_validator("owner", "project_id", "reason_code")
    @classmethod
    def validate_required_fact_text(cls, value: object, info) -> str:
        return _text(value, field=info.field_name, maximum=256)

    @field_validator("conversation_id", "object_id", "revision", "fingerprint", "source_reference", "source_fingerprint")
    @classmethod
    def validate_optional_fact_text(cls, value: object, info) -> str | None:
        return _optional_text(value, field=info.field_name, maximum=512)


class DesktopWorkspaceProjection(ProjectionBase):
    schema_version: Literal["v6.7"] = "v6.7"
    identity: DesktopProjectionIdentity
    project: DesktopProjectProjection
    context: DesktopSafeItem | None = None
    conversations: DesktopBoundedPage
    evidence: DesktopEvidencePage
    memory: DesktopBoundedPage
    knowledge: DesktopBoundedPage
    reviews: DesktopBoundedPage
    validations: DesktopBoundedPage
    findings: DesktopBoundedPage
    proposals: DesktopBoundedPage
    changes: DesktopBoundedPage
    operations: DesktopBoundedPage
    audit: DesktopBoundedPage
    capabilities: tuple[DesktopCapabilityProjection, ...]
    authoritative_facts: tuple[DesktopFactReference, ...] = ()
    workflow_summary: DesktopWorkflowSummary
    generated_at: datetime
    projection_fingerprint: str


class WorkbenchResourceNode(ProjectionBase):
    resource_id: str
    parent_id: str | None = None
    name: str
    relative_path: str
    node_type: Literal["DIRECTORY", "FILE"]
    category: str
    language: str | None = None
    source_fingerprint: str | None = None

    @field_validator("resource_id", "name", "category")
    @classmethod
    def validate_text(cls, value: object, info) -> str:
        return _text(value, field=info.field_name, maximum=256)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: object) -> str:
        result = _text(value, field="relative_path", maximum=1024).replace("\\", "/")
        if result.startswith("/") or ":" in result or any(part == ".." for part in result.split("/")):
            raise ValueError("relative_path is invalid")
        return result


class WorkbenchFinding(ProjectionBase):
    finding_id: str
    finding_type: str
    severity: str
    subject: str
    summary: str
    evidence_ids: tuple[str, ...] = ()
    validation_id: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    limitations: tuple[str, ...] = ()
    target_identity: str | None = None
    target_fingerprint: str | None = None
    finding_fingerprint: str | None = None

    @field_validator("finding_id", "finding_type", "severity", "subject", "summary")
    @classmethod
    def validate_required_text(cls, value: object, info) -> str:
        return _text(value, field=info.field_name, maximum=512)


class WorkbenchChange(ProjectionBase):
    file_reference: str | None = None
    symbol: str | None = None
    logical_change: str

    @field_validator("file_reference", "symbol", "logical_change")
    @classmethod
    def validate_change_text(cls, value: object, info) -> str | None:
        if value is None and info.field_name != "logical_change":
            return None
        return _text(value, field=info.field_name, maximum=512)


class WorkbenchRepairAlternative(ProjectionBase):
    proposal_id: str
    proposal_type: str
    subject: str
    summary: str
    rationale: str | None = None
    finding_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    proposed_changes: tuple[WorkbenchChange, ...] = ()
    risk: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    limitations: tuple[str, ...] = ()
    review_status: str | None = None

    @field_validator("proposal_id", "proposal_type", "subject", "summary")
    @classmethod
    def validate_required_text(cls, value: object, info) -> str:
        return _text(value, field=info.field_name, maximum=512)


class WorkbenchProjection(ProjectionBase):
    schema_version: Literal["workbench-v1"] = "workbench-v1"
    project_id: str
    project_fingerprint: str
    resources: tuple[WorkbenchResourceNode, ...] = ()
    findings: tuple[WorkbenchFinding, ...] = ()
    repair_alternatives: tuple[WorkbenchRepairAlternative, ...] = ()
    truncated: bool = False
    generated_at: datetime
    projection_fingerprint: str
