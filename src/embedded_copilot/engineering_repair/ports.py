from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from embedded_copilot.engineering_review import ReviewRecord
from embedded_copilot.failure_analysis import FailureAnalysis
from embedded_copilot.repair_proposal import RepairProposal

from .models import (
    ApplyReceipt,
    ApplyRequest,
    ArtifactStateReference,
    PatchProposal,
    RepairBuildReceipt,
    RepairBuildRequest,
    RepairEvent,
    RepairPlan,
    RepairRequest,
    RepairReviewRecord,
    RepairReviewRequest,
    RepairVerificationReceipt,
    RepairVerificationRequest,
)


@runtime_checkable
class RepairAnalysisPort(Protocol):
    def analyze(self, request: RepairRequest) -> FailureAnalysis: ...


@runtime_checkable
class RepairProposalPort(Protocol):
    def propose(
        self, analysis: FailureAnalysis, target: ArtifactStateReference
    ) -> RepairProposal: ...


@runtime_checkable
class PatchGenerationPort(Protocol):
    def generate(
        self, plan: RepairPlan, target_state: ArtifactStateReference
    ) -> PatchProposal: ...


@runtime_checkable
class ArtifactSourcePort(Protocol):
    def get_state(
        self, project_id: str, artifact_id: str
    ) -> ArtifactStateReference | None: ...


@runtime_checkable
class ReviewSourcePort(Protocol):
    def get_review_record(self, review_id: str) -> ReviewRecord | None: ...


@runtime_checkable
class RepairReviewPort(Protocol):
    def submit(self, request: RepairReviewRequest) -> RepairReviewRequest: ...

    def get_record(self, review_id: str) -> RepairReviewRecord | None: ...


@runtime_checkable
class ApplyPort(Protocol):
    def apply(self, request: ApplyRequest) -> ApplyReceipt: ...


@runtime_checkable
class RepairBuildPort(Protocol):
    def build(self, request: RepairBuildRequest) -> RepairBuildReceipt: ...


@runtime_checkable
class VerificationPort(Protocol):
    def verify(
        self, request: RepairVerificationRequest
    ) -> RepairVerificationReceipt: ...


@runtime_checkable
class RepairAuditSinkPort(Protocol):
    def append(self, event: RepairEvent) -> RepairEvent: ...


@runtime_checkable
class RepairClockPort(Protocol):
    def now(self) -> datetime: ...


__all__ = (
    "ApplyPort",
    "ArtifactSourcePort",
    "PatchGenerationPort",
    "RepairAnalysisPort",
    "RepairAuditSinkPort",
    "RepairBuildPort",
    "RepairClockPort",
    "RepairProposalPort",
    "RepairReviewPort",
    "ReviewSourcePort",
    "VerificationPort",
)
