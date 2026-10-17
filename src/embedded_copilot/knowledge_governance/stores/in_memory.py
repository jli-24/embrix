from __future__ import annotations

import copy

from ..contracts import (
    ConflictReview,
    EvidenceAudit,
    ReviewDecisionResult,
    ReviewStatus,
    ReviewTask,
)
from ..exceptions import GovernanceConflictError


class InMemoryReviewStore:
    __slots__ = ("_audits", "_conflicts", "_decisions", "_reviews")

    def __init__(self) -> None:
        self._reviews: dict[str, ReviewTask] = {}
        self._audits: dict[str, tuple[EvidenceAudit, ...]] = {}
        self._decisions: dict[str, ReviewDecisionResult] = {}
        self._conflicts: dict[tuple[str, str], ConflictReview] = {}

    def create_review(self, review: ReviewTask, audits: tuple[EvidenceAudit, ...]) -> ReviewTask:
        checked = ReviewTask.model_validate(copy.deepcopy(review.model_dump(mode="python")))
        checked_audits = tuple(EvidenceAudit.model_validate(copy.deepcopy(item.model_dump(mode="python"))) for item in audits)
        if any(item.review_id != checked.review_id or item.project_id != checked.project_id for item in checked_audits):
            raise GovernanceConflictError()
        existing = self._reviews.get(checked.review_id)
        if existing is not None:
            if existing != checked or self._audits.get(checked.review_id) != checked_audits:
                raise GovernanceConflictError()
            return ReviewTask.model_validate(copy.deepcopy(existing.model_dump(mode="python")))
        self._reviews[checked.review_id] = checked
        self._audits[checked.review_id] = checked_audits
        return ReviewTask.model_validate(copy.deepcopy(checked.model_dump(mode="python")))

    def get_review(self, review_id: str) -> ReviewTask | None:
        value = self._reviews.get(review_id)
        return None if value is None else ReviewTask.model_validate(copy.deepcopy(value.model_dump(mode="python")))

    def apply_decision(self, result: ReviewDecisionResult) -> ReviewDecisionResult:
        checked = ReviewDecisionResult.model_validate(copy.deepcopy(result.model_dump(mode="python")))
        existing = self._reviews.get(checked.review.review_id)
        if existing is None or existing.status is not ReviewStatus.PENDING or checked.review.status is ReviewStatus.PENDING:
            raise GovernanceConflictError()
        if checked.approval.review_id != existing.review_id or checked.approval.input_fingerprint != existing.fingerprint:
            raise GovernanceConflictError()
        if checked.approved_candidate is not None and checked.review.status is not ReviewStatus.APPROVED:
            raise GovernanceConflictError()
        if checked.approved_candidate is None and checked.review.status is ReviewStatus.APPROVED:
            raise GovernanceConflictError()
        self._reviews[checked.review.review_id] = checked.review
        self._decisions[checked.review.review_id] = checked
        return ReviewDecisionResult.model_validate(copy.deepcopy(checked.model_dump(mode="python")))

    def get_audits(self, review_id: str) -> tuple[EvidenceAudit, ...]:
        return tuple(EvidenceAudit.model_validate(copy.deepcopy(item.model_dump(mode="python"))) for item in self._audits.get(review_id, ()))

    def record_conflict(self, review: ConflictReview) -> ConflictReview:
        checked = ConflictReview.model_validate(copy.deepcopy(review.model_dump(mode="python")))
        key = (checked.project_id, checked.conflict_id)
        existing = self._conflicts.get(key)
        if existing is not None:
            if existing != checked:
                raise GovernanceConflictError()
            return ConflictReview.model_validate(copy.deepcopy(existing.model_dump(mode="python")))
        self._conflicts[key] = checked
        return ConflictReview.model_validate(copy.deepcopy(checked.model_dump(mode="python")))


__all__ = ("InMemoryReviewStore",)
