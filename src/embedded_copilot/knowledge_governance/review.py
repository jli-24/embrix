from __future__ import annotations

import copy
from datetime import datetime

from pydantic import ValidationError

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.engineering_qa import EngineeringAnswer

from .approval import approved_candidate, reviewed_task
from .audit import build_evidence_audits
from .contracts import (
    ApprovalRecord,
    ReviewDecisionResult,
    ReviewSourceType,
    ReviewStatus,
    ReviewTask,
    content_fingerprint,
)
from .exceptions import GovernanceConflictError, GovernanceRejected
from .ports import ReviewStorePort


class ReviewService:
    __slots__ = ("_store",)

    def __init__(self, *, store: ReviewStorePort) -> None:
        if not isinstance(store, ReviewStorePort):
            raise TypeError("review store port is invalid")
        self._store = store

    @staticmethod
    def _answer(value: object) -> EngineeringAnswer:
        if type(value) is not EngineeringAnswer:
            raise GovernanceRejected()
        try:
            return EngineeringAnswer.model_validate(copy.deepcopy(value.model_dump(mode="python")))
        except (TypeError, ValueError, ValidationError) as error:
            raise GovernanceRejected() from error

    @staticmethod
    def _snapshot(value: object) -> EngineeringContextSnapshot:
        if type(value) is not EngineeringContextSnapshot:
            raise GovernanceRejected()
        try:
            return EngineeringContextSnapshot.model_validate(copy.deepcopy(value.model_dump(mode="python")))
        except (TypeError, ValueError, ValidationError) as error:
            raise GovernanceRejected() from error

    def create_review(
        self,
        answer: EngineeringAnswer,
        snapshot: EngineeringContextSnapshot,
        *,
        review_id: str,
        created_at: datetime,
    ) -> ReviewTask:
        checked_answer = self._answer(answer)
        checked_snapshot = self._snapshot(snapshot)
        review = ReviewTask.create(
            review_id=review_id,
            project_id=checked_snapshot.project_id,
            source_type=ReviewSourceType.ENGINEERING_ANSWER,
            source_id=checked_answer.answer_id,
            content_fingerprint=content_fingerprint(checked_answer.content),
            evidence_refs=checked_answer.evidence_refs,
            status=ReviewStatus.PENDING,
            created_at=created_at,
        )
        audits = build_evidence_audits(checked_answer, checked_snapshot, review)
        try:
            return self._store.create_review(review, audits)
        except GovernanceRejected:
            raise
        except GovernanceConflictError:
            raise
        except Exception as error:
            raise GovernanceRejected() from error

    def get_review(self, review_id: str) -> ReviewTask | None:
        try:
            value = self._store.get_review(review_id)
            if value is None:
                return None
            return ReviewTask.model_validate(copy.deepcopy(value.model_dump(mode="python")))
        except (TypeError, ValueError, ValidationError) as error:
            raise GovernanceRejected() from error

    def submit_decision(self, approval: ApprovalRecord) -> ReviewDecisionResult:
        if type(approval) is not ApprovalRecord:
            raise GovernanceRejected()
        try:
            checked_approval = ApprovalRecord.model_validate(copy.deepcopy(approval.model_dump(mode="python")))
        except (TypeError, ValueError, ValidationError) as error:
            raise GovernanceRejected() from error
        review = self.get_review(checked_approval.review_id)
        if review is None:
            raise GovernanceRejected()
        if review.status is not ReviewStatus.PENDING:
            raise GovernanceConflictError()
        try:
            updated = reviewed_task(review, checked_approval)
            candidate = approved_candidate(review, checked_approval)
            result = ReviewDecisionResult.create(review=updated, approval=checked_approval, approved_candidate=candidate)
            return self._store.apply_decision(result)
        except GovernanceRejected:
            raise
        except GovernanceConflictError:
            raise
        except (TypeError, ValueError, ValidationError) as error:
            raise GovernanceRejected() from error

    def get_audits(self, review_id: str):
        try:
            return tuple(self._store.get_audits(review_id))
        except Exception as error:
            raise GovernanceRejected() from error


__all__ = ("ReviewService",)
