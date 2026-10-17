from __future__ import annotations

from datetime import datetime

from embedded_copilot.engineering_context import (
    ContextConflict,
    EngineeringContextSnapshot,
)
from embedded_copilot.engineering_qa import EngineeringAnswer

from .conflict import ConflictService
from .contracts import (
    ApprovalRecord,
    ConflictResolution,
    ConflictReview,
    EvidenceAudit,
    ReviewDecisionResult,
    ReviewTask,
)
from .ports import KnowledgeGovernancePort, ReviewStorePort
from .review import ReviewService


class KnowledgeGovernanceService(KnowledgeGovernancePort):
    __slots__ = ("_conflicts", "_reviews")

    def __init__(self, *, store: ReviewStorePort) -> None:
        self._reviews = ReviewService(store=store)
        self._conflicts = ConflictService(store=store)

    def create_review(self, answer: EngineeringAnswer, snapshot: EngineeringContextSnapshot, *, review_id: str, created_at: datetime) -> ReviewTask:
        return self._reviews.create_review(answer, snapshot, review_id=review_id, created_at=created_at)

    def get_review(self, review_id: str) -> ReviewTask | None:
        return self._reviews.get_review(review_id)

    def submit_review(self, approval: ApprovalRecord) -> ReviewDecisionResult:
        return self._reviews.submit_decision(approval)

    def get_audit(self, review_id: str) -> tuple[EvidenceAudit, ...]:
        return self._reviews.get_audits(review_id)

    def resolve_conflict(self, conflict: ContextConflict, *, resolution: ConflictResolution, reviewer_identity: str, resolved_at: datetime) -> ConflictReview:
        return self._conflicts.resolve_context_conflict(conflict, resolution=resolution, reviewer_identity=reviewer_identity, resolved_at=resolved_at)


__all__ = ("KnowledgeGovernanceService",)
