from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from embedded_copilot.engineering_context import (
    ContextConflict,
    EngineeringContextSnapshot,
)
from embedded_copilot.engineering_qa import EngineeringAnswer

from .contracts import (
    ApprovalRecord,
    ConflictResolution,
    ConflictReview,
    EvidenceAudit,
    ReviewDecisionResult,
    ReviewTask,
)


@runtime_checkable
class ReviewStorePort(Protocol):
    def create_review(self, review: ReviewTask, audits: tuple[EvidenceAudit, ...]) -> ReviewTask: ...

    def get_review(self, review_id: str) -> ReviewTask | None: ...

    def apply_decision(self, result: ReviewDecisionResult) -> ReviewDecisionResult: ...

    def get_audits(self, review_id: str) -> tuple[EvidenceAudit, ...]: ...

    def record_conflict(self, review: ConflictReview) -> ConflictReview: ...


@runtime_checkable
class KnowledgeGovernancePort(Protocol):
    def create_review(self, answer: EngineeringAnswer, snapshot: EngineeringContextSnapshot, *, review_id: str, created_at: datetime) -> ReviewTask: ...

    def get_review(self, review_id: str) -> ReviewTask | None: ...

    def submit_review(self, approval: ApprovalRecord) -> ReviewDecisionResult: ...

    def get_audit(self, review_id: str) -> tuple[EvidenceAudit, ...]: ...

    def resolve_conflict(self, conflict: ContextConflict, *, resolution: ConflictResolution, reviewer_identity: str, resolved_at: datetime) -> ConflictReview: ...


__all__ = ("KnowledgeGovernancePort", "ReviewStorePort")
