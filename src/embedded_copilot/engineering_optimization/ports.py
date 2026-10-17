from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .models import (
    EngineeringOptimizationOverlay,
    OptimizationAnalysis,
    OptimizationAuditEvent,
    OptimizationDecisionReference,
    OptimizationEvidenceReference,
    OptimizationMemoryCandidateReference,
    OptimizationProposal,
    OptimizationRequest,
    OptimizationReviewRecord,
    OptimizationReviewRequest,
)


@runtime_checkable
class OptimizationEvidencePort(Protocol):
    def get_evidence(
        self, project_id: str, evidence_id: str
    ) -> OptimizationEvidenceReference | None: ...


@runtime_checkable
class OptimizationAnalysisPort(Protocol):
    def analyze(
        self,
        request: OptimizationRequest,
        evidence: tuple[OptimizationEvidenceReference, ...],
    ) -> OptimizationAnalysis: ...


@runtime_checkable
class OptimizationReviewPort(Protocol):
    def submit(
        self, request: OptimizationReviewRequest
    ) -> OptimizationReviewRequest: ...
    def get_request(self, review_id: str) -> OptimizationReviewRequest | None: ...
    def get_record(self, review_id: str) -> OptimizationReviewRecord | None: ...


@runtime_checkable
class OptimizationDecisionPort(Protocol):
    def record(
        self, decision: OptimizationDecisionReference
    ) -> OptimizationDecisionReference: ...
    def get(self, decision_id: str) -> OptimizationDecisionReference | None: ...


@runtime_checkable
class OptimizationProposalReaderPort(Protocol):
    def get_proposal(
        self, project_id: str, proposal_id: str
    ) -> OptimizationProposal | None: ...


@runtime_checkable
class OptimizationContextProjectionPort(Protocol):
    def project(
        self, overlay: EngineeringOptimizationOverlay
    ) -> EngineeringOptimizationOverlay: ...


@runtime_checkable
class OptimizationMemoryPort(Protocol):
    def create(
        self, overlay: EngineeringOptimizationOverlay
    ) -> tuple[OptimizationMemoryCandidateReference, ...]: ...


@runtime_checkable
class OptimizationAuditPort(Protocol):
    def append(self, event: OptimizationAuditEvent) -> OptimizationAuditEvent: ...
    def get(self, event_id: str) -> OptimizationAuditEvent | None: ...


@runtime_checkable
class ClockPort(Protocol):
    def now(self) -> datetime: ...


__all__ = tuple(name for name in globals() if name.endswith("Port"))
