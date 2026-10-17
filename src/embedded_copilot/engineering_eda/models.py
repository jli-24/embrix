from __future__ import annotations

import copy
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import PurePath
from typing import Literal

from pydantic import Field, field_validator, model_validator

from embedded_copilot.engineering_pcb import PCBAnalysis, PCBArtifactReference
from embedded_copilot.multimodal_engineering import EngineeringObservation

from .contracts import (
    FingerprintedEDAContract,
    deterministic_id,
    fingerprint,
    identifier,
    logical_reference,
    safe_text,
    tuple_only,
    utc_datetime,
)


def _exact(value: object, model: type, *, field: str):
    if type(value) is model:
        material = value.model_dump(mode="python")
    elif type(value) is dict:
        material = value
    else:
        raise ValueError(f"{field} is invalid")
    return model.model_validate(copy.deepcopy(material))


class EDAArtifactType(StrEnum):
    PROJECT = "PROJECT"
    SCHEMATIC = "SCHEMATIC"
    BOARD = "BOARD"
    BOM = "BOM"
    NETLIST = "NETLIST"
    STEP = "STEP"
    GLB = "GLB"


class EDAProposalType(StrEnum):
    LAYOUT_REVIEW = "LAYOUT_REVIEW"
    CONNECTIVITY_REVIEW = "CONNECTIVITY_REVIEW"
    COMPONENT_REVIEW = "COMPONENT_REVIEW"
    DESIGN_RULE_REVIEW = "DESIGN_RULE_REVIEW"


class EDARisk(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EDASide(StrEnum):
    FRONT = "FRONT"
    BACK = "BACK"


class EDAReviewDecisionType(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_CHANGE = "REQUEST_CHANGE"


class EDAReviewStatus(StrEnum):
    OPEN = "OPEN"
    DECIDED = "DECIDED"


class EDAApplyStatus(StrEnum):
    STAGED = "STAGED"


class EDAValidationKind(StrEnum):
    ERC = "ERC"
    DRC = "DRC"


class EDAValidationSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class EDAValidationStatus(StrEnum):
    PASSED = "PASSED"
    VIOLATIONS = "VIOLATIONS"
    UNAVAILABLE = "UNAVAILABLE"


class EDAAuditEventType(StrEnum):
    ANALYSIS_COMPLETED = "ANALYSIS_COMPLETED"
    PROPOSAL_CREATED = "PROPOSAL_CREATED"
    REVIEW_SUBMITTED = "REVIEW_SUBMITTED"
    APPLY_STAGED = "APPLY_STAGED"
    VALIDATION_COMPLETED = "VALIDATION_COMPLETED"
    OPERATION_FAILED = "OPERATION_FAILED"


class EDAArtifactReference(FingerprintedEDAContract):
    project_id: str
    artifact_id: str
    artifact_type: EDAArtifactType
    revision: str
    format_version: str
    basename: str
    byte_size: int = Field(gt=0, le=128 * 1024 * 1024)
    source_reference: str
    content_fingerprint: str
    captured_at: datetime

    @field_validator(
        "project_id", "artifact_id", "revision", "format_version", mode="before"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("basename", mode="before")
    @classmethod
    def _basename(cls, value: object) -> str:
        checked = safe_text(value, field="basename", maximum=128)
        if PurePath(checked).name != checked or "/" in checked or "\\" in checked:
            raise ValueError("basename is invalid")
        return checked

    @field_validator("source_reference", mode="before")
    @classmethod
    def _source(cls, value: object) -> str:
        return logical_reference(value, field="source_reference")

    @field_validator("content_fingerprint", mode="before")
    @classmethod
    def _content_fp(cls, value: object) -> str:
        return fingerprint(value, field="content_fingerprint")

    @field_validator("captured_at", mode="before")
    @classmethod
    def _captured(cls, value: object) -> datetime:
        return utc_datetime(value, field="captured_at")

    @model_validator(mode="after")
    def _extension(self):
        suffixes = {
            # Project artifacts may be native KiCad projects or JLCEDA's
            # container format.  The container is still only an EDA artifact;
            # it never becomes Engineering Truth without an adapter projection.
            EDAArtifactType.PROJECT: (".kicad_pro", ".eprj3"),
            EDAArtifactType.SCHEMATIC: ".kicad_sch",
            EDAArtifactType.BOARD: ".kicad_pcb",
            EDAArtifactType.BOM: ".csv",
            EDAArtifactType.NETLIST: ".xml",
            EDAArtifactType.STEP: (".step", ".stp"),
            EDAArtifactType.GLB: ".glb",
        }
        allowed_suffixes = suffixes[self.artifact_type]
        if isinstance(allowed_suffixes, str):
            allowed_suffixes = (allowed_suffixes,)
        if not self.basename.casefold().endswith(allowed_suffixes):
            raise ValueError("artifact type and basename mismatch")
        return self


class EDAEvidenceReference(FingerprintedEDAContract):
    evidence_id: str
    project_id: str
    source_id: str
    source_fingerprint: str
    locator: str

    @field_validator("evidence_id", "project_id", "source_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def _source_fp(cls, value: object) -> str:
        return fingerprint(value, field="source_fingerprint")

    @field_validator("locator", mode="before")
    @classmethod
    def _locator(cls, value: object) -> str:
        return logical_reference(value, field="locator")


class EDAAnalysisRequest(FingerprintedEDAContract):
    request_id: str
    project_id: str
    pcb_analysis: PCBAnalysis
    project_artifact: EDAArtifactReference
    pcb_artifacts: tuple[PCBArtifactReference, ...]
    multimodal_observation: EngineeringObservation | None = None
    requester: str
    requested_at: datetime

    @field_validator("request_id", "project_id", "requester", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("pcb_analysis", mode="before")
    @classmethod
    def _analysis(cls, value: object) -> PCBAnalysis:
        return _exact(value, PCBAnalysis, field="pcb_analysis")

    @field_validator("project_artifact", mode="before")
    @classmethod
    def _project_artifact(cls, value: object) -> EDAArtifactReference:
        return _exact(value, EDAArtifactReference, field="project_artifact")

    @field_validator("pcb_artifacts", mode="before")
    @classmethod
    def _pcb_artifacts(cls, value: object) -> tuple[PCBArtifactReference, ...]:
        return tuple(
            _exact(item, PCBArtifactReference, field="pcb_artifact")
            for item in tuple_only(value, field="pcb_artifacts")
        )

    @field_validator("multimodal_observation", mode="before")
    @classmethod
    def _observation(cls, value: object) -> EngineeringObservation | None:
        return (
            None
            if value is None
            else _exact(value, EngineeringObservation, field="multimodal_observation")
        )

    @field_validator("requested_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="requested_at")

    @model_validator(mode="after")
    def _binding(self):
        if self.project_artifact.artifact_type is not EDAArtifactType.PROJECT:
            raise ValueError("project artifact is invalid")
        if (
            self.pcb_analysis.project_id != self.project_id
            or self.project_artifact.project_id != self.project_id
        ):
            raise ValueError("EDA analysis project mismatch")
        if (
            len(self.pcb_artifacts) != 4
            or len({item.artifact_type for item in self.pcb_artifacts}) != 4
        ):
            raise ValueError("four PCB artifacts are required")
        if any(item.project_id != self.project_id for item in self.pcb_artifacts):
            raise ValueError("PCB artifact project mismatch")
        if self.multimodal_observation is not None and (
            self.multimodal_observation.artifact_reference.project_id != self.project_id
            or self.multimodal_observation.artifact_reference.captured_at
            > self.requested_at
        ):
            raise ValueError("multimodal evidence binding mismatch")
        return self


class EDAValidationFinding(FingerprintedEDAContract):
    finding_id: str
    validation_kind: EDAValidationKind
    severity: EDAValidationSeverity
    rule_id: str
    artifact_fingerprint: str
    message: str
    evidence: tuple[EDAEvidenceReference, ...]

    @field_validator("finding_id", "rule_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("artifact_fingerprint", mode="before")
    @classmethod
    def _artifact_fp(cls, value: object) -> str:
        return fingerprint(value, field="artifact_fingerprint")

    @field_validator("message", mode="before")
    @classmethod
    def _message(cls, value: object) -> str:
        return safe_text(value, field="message", maximum=512)

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence(cls, value: object) -> tuple[EDAEvidenceReference, ...]:
        return tuple(
            _exact(item, EDAEvidenceReference, field="evidence")
            for item in tuple_only(value, field="evidence")
        )


class EDAValidationReport(FingerprintedEDAContract):
    report_id: str
    project_id: str
    artifact: EDAArtifactReference
    validation_kind: EDAValidationKind
    findings: tuple[EDAValidationFinding, ...]
    status: EDAValidationStatus
    tool_version: str
    validated_at: datetime

    @field_validator("report_id", "project_id", "tool_version", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("artifact", mode="before")
    @classmethod
    def _artifact(cls, value: object) -> EDAArtifactReference:
        return _exact(value, EDAArtifactReference, field="artifact")

    @field_validator("findings", mode="before")
    @classmethod
    def _findings(cls, value: object) -> tuple[EDAValidationFinding, ...]:
        return tuple(
            _exact(item, EDAValidationFinding, field="finding")
            for item in tuple_only(value, field="findings")
        )

    @field_validator("validated_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="validated_at")

    @model_validator(mode="after")
    def _state(self):
        if self.artifact.project_id != self.project_id or any(
            item.artifact_fingerprint != self.artifact.content_fingerprint
            for item in self.findings
        ):
            raise ValueError("validation artifact mismatch")
        expected = (
            EDAValidationStatus.VIOLATIONS
            if self.findings
            else EDAValidationStatus.PASSED
        )
        if (
            self.status is not EDAValidationStatus.UNAVAILABLE
            and self.status is not expected
        ):
            raise ValueError("validation status mismatch")
        return self


class EDAAnalysisResult(FingerprintedEDAContract):
    analysis_id: str
    project_id: str
    request_fingerprint: str
    pcb_analysis_fingerprint: str
    project_artifact_fingerprint: str
    validation_reports: tuple[EDAValidationReport, ...]
    completed_at: datetime

    @field_validator("analysis_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator(
        "request_fingerprint",
        "pcb_analysis_fingerprint",
        "project_artifact_fingerprint",
        mode="before",
    )
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("validation_reports", mode="before")
    @classmethod
    def _reports(cls, value: object) -> tuple[EDAValidationReport, ...]:
        return tuple(
            _exact(item, EDAValidationReport, field="validation_report")
            for item in tuple_only(value, field="validation_reports")
        )

    @field_validator("completed_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="completed_at")


class PCBDesignProposal(FingerprintedEDAContract):
    proposal_id: str
    project_id: str
    analysis_fingerprint: str
    proposal_type: EDAProposalType
    target_artifacts: tuple[EDAArtifactReference, ...]
    change_intention: tuple[str, ...]
    risk: EDARisk
    evidence: tuple[EDAEvidenceReference, ...]
    visual_evidence_fingerprint: str | None = None
    created_at: datetime

    @field_validator("proposal_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("analysis_fingerprint", mode="before")
    @classmethod
    def _analysis_fp(cls, value: object) -> str:
        return fingerprint(value, field="analysis_fingerprint")

    @field_validator("visual_evidence_fingerprint", mode="before")
    @classmethod
    def _visual_fp(cls, value: object) -> str | None:
        return (
            None
            if value is None
            else fingerprint(value, field="visual_evidence_fingerprint")
        )

    @field_validator("target_artifacts", mode="before")
    @classmethod
    def _targets(cls, value: object) -> tuple[EDAArtifactReference, ...]:
        return tuple(
            _exact(item, EDAArtifactReference, field="target_artifact")
            for item in tuple_only(value, field="target_artifacts")
        )

    @field_validator("change_intention", mode="before")
    @classmethod
    def _intentions(cls, value: object) -> tuple[str, ...]:
        items = tuple(
            safe_text(item, field="change_intention", maximum=512)
            for item in tuple_only(value, field="change_intention")
        )
        if not items:
            raise ValueError("change intention is required")
        return items

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence(cls, value: object) -> tuple[EDAEvidenceReference, ...]:
        items = tuple(
            _exact(item, EDAEvidenceReference, field="evidence")
            for item in tuple_only(value, field="evidence")
        )
        if not items:
            raise ValueError("proposal evidence is required")
        return items

    @field_validator("created_at", mode="before")
    @classmethod
    def _created(cls, value: object) -> datetime:
        return utc_datetime(value, field="created_at")

    @model_validator(mode="after")
    def _visual_boundary(self):
        if (
            self.visual_evidence_fingerprint is not None
            and self.proposal_type is not EDAProposalType.LAYOUT_REVIEW
        ):
            raise ValueError("visual evidence only supports layout review")
        if any(
            item.project_id != self.project_id
            for item in (*self.target_artifacts, *self.evidence)
        ):
            raise ValueError("proposal project mismatch")
        return self


class MoveFootprintChange(FingerprintedEDAContract):
    operation: Literal["MOVE_FOOTPRINT"] = "MOVE_FOOTPRINT"
    reference: str
    x_mm: float = Field(ge=-10_000.0, le=10_000.0)
    y_mm: float = Field(ge=-10_000.0, le=10_000.0)
    rotation_deg: float = Field(ge=0.0, lt=360.0)
    side: EDASide

    @field_validator("reference", mode="before")
    @classmethod
    def _reference(cls, value: object) -> str:
        return identifier(value, field="reference")


class EDAChangeSet(FingerprintedEDAContract):
    change_set_id: str
    project_id: str
    proposal_fingerprint: str
    target_artifact: EDAArtifactReference
    before_fingerprint: str
    changes: tuple[MoveFootprintChange, ...]
    authored_by: str
    authored_at: datetime

    @field_validator("change_set_id", "project_id", "authored_by", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("proposal_fingerprint", "before_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("target_artifact", mode="before")
    @classmethod
    def _target(cls, value: object) -> EDAArtifactReference:
        return _exact(value, EDAArtifactReference, field="target_artifact")

    @field_validator("changes", mode="before")
    @classmethod
    def _changes(cls, value: object) -> tuple[MoveFootprintChange, ...]:
        items = tuple(
            _exact(item, MoveFootprintChange, field="change")
            for item in tuple_only(value, field="changes")
        )
        if not items or len({item.reference for item in items}) != len(items):
            raise ValueError("changes must be non-empty and unique")
        return items

    @field_validator("authored_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="authored_at")

    @model_validator(mode="after")
    def _binding(self):
        if self.target_artifact.artifact_type is not EDAArtifactType.BOARD:
            raise ValueError("only board changes are supported")
        if (
            self.target_artifact.project_id != self.project_id
            or self.target_artifact.content_fingerprint != self.before_fingerprint
        ):
            raise ValueError("change set target mismatch")
        return self


class EDAReviewRequest(FingerprintedEDAContract):
    review_id: str
    project_id: str
    change_set_fingerprint: str
    proposal_fingerprint: str
    requester: str
    requested_at: datetime

    @field_validator("review_id", "project_id", "requester", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("change_set_fingerprint", "proposal_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("requested_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="requested_at")


class EDAReviewDecision(FingerprintedEDAContract):
    decision_id: str
    review_id: str
    project_id: str
    decision: EDAReviewDecisionType
    reviewer: str
    comment: str
    decided_at: datetime

    @field_validator(
        "decision_id", "review_id", "project_id", "reviewer", mode="before"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("comment", mode="before")
    @classmethod
    def _comment(cls, value: object) -> str:
        return safe_text(value, field="comment", maximum=512)

    @field_validator("decided_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="decided_at")


class EDAReviewRecord(FingerprintedEDAContract):
    request: EDAReviewRequest
    decision: EDAReviewDecision | None = None
    status: EDAReviewStatus

    @model_validator(mode="after")
    def _state(self):
        if self.status is EDAReviewStatus.OPEN and self.decision is not None:
            raise ValueError("open review cannot have decision")
        if self.status is EDAReviewStatus.DECIDED and self.decision is None:
            raise ValueError("decided review requires decision")
        if self.decision is not None and (
            self.decision.review_id != self.request.review_id
            or self.decision.project_id != self.request.project_id
            or self.decision.decided_at < self.request.requested_at
        ):
            raise ValueError("review decision mismatch")
        return self


class EDAApprovalReference(FingerprintedEDAContract):
    review_id: str
    review_record_fingerprint: str
    change_set_fingerprint: str
    proposal_fingerprint: str
    project_id: str
    reviewer: str
    approved_at: datetime

    @field_validator("review_id", "project_id", "reviewer", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator(
        "review_record_fingerprint",
        "change_set_fingerprint",
        "proposal_fingerprint",
        mode="before",
    )
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("approved_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="approved_at")


class EDAApplyRequest(FingerprintedEDAContract):
    apply_id: str
    project_id: str
    change_set: EDAChangeSet
    approval: EDAApprovalReference
    before_fingerprint: str
    staging_revision: str
    requested_at: datetime

    @field_validator("apply_id", "project_id", "staging_revision", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("before_fingerprint", mode="before")
    @classmethod
    def _before(cls, value: object) -> str:
        return fingerprint(value, field="before_fingerprint")

    @field_validator("requested_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="requested_at")

    @model_validator(mode="after")
    def _binding(self):
        if (
            self.change_set.project_id != self.project_id
            or self.approval.project_id != self.project_id
            or self.approval.change_set_fingerprint != self.change_set.fingerprint
            or self.approval.proposal_fingerprint
            != self.change_set.proposal_fingerprint
            or self.before_fingerprint != self.change_set.before_fingerprint
            or self.requested_at < self.approval.approved_at
        ):
            raise ValueError("apply approval binding mismatch")
        return self


class EDAApplyReceipt(FingerprintedEDAContract):
    apply_id: str
    project_id: str
    apply_request_fingerprint: str
    change_set_fingerprint: str
    staging_revision: str
    before_fingerprint: str
    after_artifact: EDAArtifactReference
    status: Literal[EDAApplyStatus.STAGED] = EDAApplyStatus.STAGED
    staged_at: datetime

    @field_validator("apply_id", "project_id", "staging_revision", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator(
        "apply_request_fingerprint",
        "change_set_fingerprint",
        "before_fingerprint",
        mode="before",
    )
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("staged_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="staged_at")

    @model_validator(mode="after")
    def _state(self):
        if (
            self.after_artifact.project_id != self.project_id
            or self.after_artifact.revision != self.staging_revision
            or self.after_artifact.content_fingerprint == self.before_fingerprint
        ):
            raise ValueError("staging receipt is invalid")
        return self


class EDAAuditEvent(FingerprintedEDAContract):
    event_id: str
    event_type: EDAAuditEventType
    project_id: str
    operation_fingerprint: str
    artifact_fingerprint: str | None
    result_fingerprint: str | None
    error_code: str | None
    timestamp: datetime

    @field_validator("event_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("operation_fingerprint", mode="before")
    @classmethod
    def _operation_fp(cls, value: object) -> str:
        return fingerprint(value, field="operation_fingerprint")

    @field_validator("artifact_fingerprint", "result_fingerprint", mode="before")
    @classmethod
    def _optional_fp(cls, value: object, info) -> str | None:
        return None if value is None else fingerprint(value, field=info.field_name)

    @field_validator("error_code", mode="before")
    @classmethod
    def _error(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="error_code")

    @field_validator("timestamp", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="timestamp")

    @model_validator(mode="after")
    def _identity(self):
        expected = deterministic_id(
            "eda-event", self.operation_fingerprint, self.event_type.value
        )
        if self.event_id != expected:
            raise ValueError("EDA audit event id mismatch")
        return self


def approval_from_record(
    record: EDAReviewRecord,
    *,
    maximum_age: timedelta | None = None,
    now: datetime | None = None,
) -> EDAApprovalReference:
    checked = _exact(record, EDAReviewRecord, field="review_record")
    if (
        checked.decision is None
        or checked.decision.decision is not EDAReviewDecisionType.APPROVE
    ):
        raise ValueError("review is not approved")
    if (
        maximum_age is not None
        and now is not None
        and utc_datetime(now, field="now") - checked.decision.decided_at > maximum_age
    ):
        raise ValueError("review approval expired")
    return EDAApprovalReference(
        review_id=checked.request.review_id,
        review_record_fingerprint=checked.fingerprint,
        change_set_fingerprint=checked.request.change_set_fingerprint,
        proposal_fingerprint=checked.request.proposal_fingerprint,
        project_id=checked.request.project_id,
        reviewer=checked.decision.reviewer,
        approved_at=checked.decision.decided_at,
    )


__all__ = tuple(
    name
    for name in globals()
    if name.startswith(("EDA", "PCBDesign", "MoveFootprint"))
    or name == "approval_from_record"
)
