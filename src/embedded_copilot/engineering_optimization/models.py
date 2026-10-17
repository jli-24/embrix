from __future__ import annotations

import copy
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import field_validator, model_validator

from .contracts import (
    FingerprintedOptimizationContract,
    deterministic_id,
    fingerprint,
    identifier,
    safe_text,
    tuple_only,
    utc_datetime,
)


def _exact(value: object, model: type, *, field: str):
    if type(value) is model:
        value = value.model_dump(mode="python")
    elif type(value) is not dict:
        raise TypeError(f"{field} is invalid")
    return model.model_validate(copy.deepcopy(value))


class OptimizationEvidenceSourceType(StrEnum):
    PCB_ANALYSIS = "PCB_ANALYSIS"
    EDA_VALIDATION = "EDA_VALIDATION"
    FIRMWARE_ANALYSIS = "FIRMWARE_ANALYSIS"
    DEBUG_ANALYSIS = "DEBUG_ANALYSIS"
    HIL_OBSERVATION = "HIL_OBSERVATION"
    POWER_MEASUREMENT = "POWER_MEASUREMENT"
    PERFORMANCE_METRIC = "PERFORMANCE_METRIC"
    MEMORY_ANALYSIS = "MEMORY_ANALYSIS"


class OptimizationScope(StrEnum):
    POWER = "POWER"
    PERFORMANCE = "PERFORMANCE"
    MEMORY = "MEMORY"
    FIRMWARE = "FIRMWARE"
    PCB = "PCB"
    THERMAL = "THERMAL"
    COST = "COST"


class OptimizationRisk(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class OptimizationConfidence(StrEnum):
    PROJECTED = "PROJECTED"
    EVIDENCE_BOUND = "EVIDENCE_BOUND"


class OptimizationReviewDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_CHANGE = "REQUEST_CHANGE"


class OptimizationAuditEventType(StrEnum):
    OPTIMIZATION_CREATED = "OPTIMIZATION_CREATED"
    REVIEW_SUBMITTED = "REVIEW_SUBMITTED"
    OPTIMIZATION_APPROVED = "OPTIMIZATION_APPROVED"
    OPTIMIZATION_REJECTED = "OPTIMIZATION_REJECTED"
    CONTEXT_PROJECTED = "CONTEXT_PROJECTED"
    MEMORY_CANDIDATE_CREATED = "MEMORY_CANDIDATE_CREATED"


class OptimizationEvidenceReference(FingerprintedOptimizationContract):
    evidence_id: str
    project_id: str
    source_type: OptimizationEvidenceSourceType
    source_reference: str
    source_fingerprint: str
    created_at: datetime

    @field_validator("evidence_id", "project_id", "source_reference", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return fingerprint(value, field="source_fingerprint")

    @field_validator("created_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="created_at")


class OptimizationRequest(FingerprintedOptimizationContract):
    request_id: str
    project_id: str
    evidence_refs: tuple[OptimizationEvidenceReference, ...]
    optimization_scope: tuple[OptimizationScope, ...]
    requester: str
    created_at: datetime

    @field_validator("request_id", "project_id", "requester", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence(cls, value: object) -> tuple[OptimizationEvidenceReference, ...]:
        items = tuple(
            _exact(item, OptimizationEvidenceReference, field="evidence")
            for item in tuple_only(value, field="evidence_refs")
        )
        if not items or len({item.evidence_id for item in items}) != len(items):
            raise ValueError("optimization evidence is invalid")
        return tuple(sorted(items, key=lambda item: item.evidence_id))

    @field_validator("optimization_scope", mode="before")
    @classmethod
    def _scope(cls, value: object) -> tuple[OptimizationScope, ...]:
        items = tuple_only(value, field="optimization_scope")
        if not items or len(items) != len(set(items)):
            raise ValueError("optimization scope is invalid")
        return tuple(sorted(items, key=lambda item: item.value))

    @field_validator("created_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="created_at")

    @model_validator(mode="after")
    def _binding(self):
        if any(
            item.project_id != self.project_id or item.created_at > self.created_at
            for item in self.evidence_refs
        ):
            raise ValueError("optimization request evidence binding mismatch")
        return self


class OptimizationFinding(FingerprintedOptimizationContract):
    finding_id: str
    project_id: str
    optimization_type: OptimizationScope
    finding: str
    evidence_refs: tuple[OptimizationEvidenceReference, ...]
    confidence: OptimizationConfidence
    created_at: datetime

    @field_validator("finding_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("finding", mode="before")
    @classmethod
    def _finding(cls, value: object) -> str:
        return safe_text(value, field="finding", maximum=512)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence(cls, value: object) -> tuple[OptimizationEvidenceReference, ...]:
        return OptimizationRequest._evidence(value)

    @field_validator("created_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="created_at")

    @model_validator(mode="after")
    def _binding(self):
        if any(item.project_id != self.project_id for item in self.evidence_refs):
            raise ValueError("optimization finding evidence binding mismatch")
        return self


class OptimizationAnalysis(FingerprintedOptimizationContract):
    analysis_id: str
    project_id: str
    request_fingerprint: str
    findings: tuple[OptimizationFinding, ...]
    created_at: datetime

    @field_validator("analysis_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("request_fingerprint", mode="before")
    @classmethod
    def _request_fp(cls, value: object) -> str:
        return fingerprint(value, field="request_fingerprint")

    @field_validator("findings", mode="before")
    @classmethod
    def _findings(cls, value: object) -> tuple[OptimizationFinding, ...]:
        items = tuple(
            _exact(item, OptimizationFinding, field="finding")
            for item in tuple_only(value, field="findings")
        )
        if not items or len({item.finding_id for item in items}) != len(items):
            raise ValueError("optimization findings are invalid")
        return tuple(sorted(items, key=lambda item: item.finding_id))

    @field_validator("created_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="created_at")

    @model_validator(mode="after")
    def _binding(self):
        if any(item.project_id != self.project_id for item in self.findings):
            raise ValueError("optimization analysis project mismatch")
        return self


class OptimizationProposal(FingerprintedOptimizationContract):
    proposal_id: str
    project_id: str
    optimization_type: OptimizationScope
    evidence_refs: tuple[OptimizationEvidenceReference, ...]
    finding: str
    recommendation: str
    risk: OptimizationRisk
    expected_impact: str
    confidence: OptimizationConfidence
    analysis_fingerprint: str
    provenance_fingerprint: str
    created_at: datetime

    @field_validator("proposal_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("finding", "recommendation", "expected_impact", mode="before")
    @classmethod
    def _text(cls, value: object, info) -> str:
        return safe_text(value, field=info.field_name, maximum=768)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence(cls, value: object) -> tuple[OptimizationEvidenceReference, ...]:
        return OptimizationRequest._evidence(value)

    @field_validator("analysis_fingerprint", "provenance_fingerprint", mode="before")
    @classmethod
    def _provenance(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("created_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="created_at")

    @model_validator(mode="after")
    def _binding(self):
        if (
            any(item.project_id != self.project_id for item in self.evidence_refs)
            or self.analysis_fingerprint != self.provenance_fingerprint
        ):
            raise ValueError("optimization proposal evidence binding mismatch")
        return self


class OptimizationReviewRequest(FingerprintedOptimizationContract):
    review_id: str
    proposal: OptimizationProposal
    requested_reviewer: str
    requested_at: datetime

    @field_validator("review_id", "requested_reviewer", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("proposal", mode="before")
    @classmethod
    def _proposal(cls, value: object) -> OptimizationProposal:
        return _exact(value, OptimizationProposal, field="proposal")

    @field_validator("requested_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="requested_at")


class OptimizationReviewRecord(FingerprintedOptimizationContract):
    review_id: str
    proposal_id: str
    proposal_fingerprint: str
    project_id: str
    requested_reviewer: str
    reviewer: str
    decision: OptimizationReviewDecision
    created_at: datetime
    expires_at: datetime

    @field_validator(
        "review_id",
        "proposal_id",
        "project_id",
        "requested_reviewer",
        "reviewer",
        mode="before",
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("proposal_fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return fingerprint(value, field="proposal_fingerprint")

    @field_validator("created_at", "expires_at", mode="before")
    @classmethod
    def _time(cls, value: object, info) -> datetime:
        return utc_datetime(value, field=info.field_name)

    @model_validator(mode="after")
    def _binding(self):
        if (
            self.reviewer != self.requested_reviewer
            or self.expires_at < self.created_at
        ):
            raise ValueError("optimization review binding mismatch")
        return self


class OptimizationDecisionReference(FingerprintedOptimizationContract):
    decision_id: str
    project_id: str
    review_id: str
    proposal_id: str
    proposal_fingerprint: str
    reviewer: str
    decided_at: datetime

    @field_validator(
        "decision_id",
        "project_id",
        "review_id",
        "proposal_id",
        "reviewer",
        mode="before",
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("proposal_fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return fingerprint(value, field="proposal_fingerprint")

    @field_validator("decided_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="decided_at")


class EngineeringOptimizationOverlay(FingerprintedOptimizationContract):
    overlay_id: str
    project_id: str
    base_context_fingerprint: str | None
    decision: OptimizationDecisionReference
    proposal_fingerprint: str
    status: Literal["PROJECTED"] = "PROJECTED"
    created_at: datetime

    @field_validator("overlay_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("base_context_fingerprint", mode="before")
    @classmethod
    def _base(cls, value: object) -> str | None:
        return (
            None
            if value is None
            else fingerprint(value, field="base_context_fingerprint")
        )

    @field_validator("decision", mode="before")
    @classmethod
    def _decision(cls, value: object) -> OptimizationDecisionReference:
        return _exact(value, OptimizationDecisionReference, field="decision")

    @field_validator("proposal_fingerprint", mode="before")
    @classmethod
    def _proposal(cls, value: object) -> str:
        return fingerprint(value, field="proposal_fingerprint")

    @field_validator("created_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="created_at")

    @model_validator(mode="after")
    def _binding(self):
        if (
            self.decision.project_id != self.project_id
            or self.decision.proposal_fingerprint != self.proposal_fingerprint
        ):
            raise ValueError("optimization overlay binding mismatch")
        return self


class OptimizationMemoryCandidateReference(FingerprintedOptimizationContract):
    project_id: str
    overlay_fingerprint: str
    candidate_id: str
    candidate_fingerprint: str
    review_status: Literal["REVIEW_REQUIRED"] = "REVIEW_REQUIRED"

    @field_validator("project_id", "candidate_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("overlay_fingerprint", "candidate_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)


class OptimizationAuditEvent(FingerprintedOptimizationContract):
    event_id: str
    event_type: OptimizationAuditEventType
    project_id: str
    operation_fingerprint: str
    result_fingerprint: str | None
    timestamp: datetime

    @field_validator("event_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("operation_fingerprint", mode="before")
    @classmethod
    def _operation(cls, value: object) -> str:
        return fingerprint(value, field="operation_fingerprint")

    @field_validator("result_fingerprint", mode="before")
    @classmethod
    def _result(cls, value: object) -> str | None:
        return None if value is None else fingerprint(value, field="result_fingerprint")

    @field_validator("timestamp", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="timestamp")

    @model_validator(mode="after")
    def _event_id(self):
        if self.event_id != deterministic_id(
            "optimization-event", self.operation_fingerprint, self.event_type.value
        ):
            raise ValueError("optimization audit identity mismatch")
        return self


__all__ = tuple(
    name
    for name in globals()
    if name.startswith("Optimization") or name == "EngineeringOptimizationOverlay"
)
