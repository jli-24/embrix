from __future__ import annotations

import copy
import hashlib

from .contracts import (
    validate_approved_knowledge,
    validate_candidate,
    validate_candidate_id,
    validate_review,
)
from .exceptions import (
    CandidateNotFound,
    GovernanceConflict,
    GovernanceRejected,
    GovernanceUnavailable,
)
from .models import (
    ApprovedKnowledge,
    CandidateStatus,
    KnowledgeCandidate,
    ReviewDecision,
    ReviewRequest,
)
from .ports import (
    ApprovedKnowledgeSinkPort,
    KnowledgeGovernanceStorePort,
    KnowledgeReviewPort,
)


class KnowledgeReviewService(KnowledgeReviewPort):
    __slots__ = ()

    def submit_for_review(self, candidate: KnowledgeCandidate) -> KnowledgeCandidate:
        try:
            checked = validate_candidate(candidate)
        except Exception as error:
            raise GovernanceRejected() from error
        if checked.status is not CandidateStatus.PENDING:
            raise GovernanceRejected()
        return checked

    def review(
        self, candidate: KnowledgeCandidate, request: ReviewRequest
    ) -> KnowledgeCandidate:
        try:
            checked_candidate = validate_candidate(candidate)
            checked_request = validate_review(request)
        except Exception as error:
            raise GovernanceRejected() from error
        if checked_candidate.status is not CandidateStatus.PENDING:
            raise GovernanceRejected()
        if checked_request.candidate_id != checked_candidate.candidate_id:
            raise GovernanceRejected()
        if checked_request.candidate_fingerprint != checked_candidate.fingerprint:
            raise GovernanceRejected()
        next_status = {
            ReviewDecision.APPROVE: CandidateStatus.APPROVED,
            ReviewDecision.REJECT: CandidateStatus.REJECTED,
            ReviewDecision.REQUEST_CHANGE: CandidateStatus.PENDING,
        }[checked_request.decision]
        return checked_candidate.with_status(next_status)


class KnowledgeGovernanceService:
    __slots__ = ("_review", "_sink", "_store")

    def __init__(
        self,
        *,
        store: KnowledgeGovernanceStorePort,
        sink: ApprovedKnowledgeSinkPort,
    ) -> None:
        if not isinstance(store, KnowledgeGovernanceStorePort):
            raise TypeError("knowledge governance store is invalid")
        if not isinstance(sink, ApprovedKnowledgeSinkPort):
            raise TypeError("approved knowledge sink is invalid")
        self._store = store
        self._sink = sink
        self._review = KnowledgeReviewService()

    def submit_candidate(self, candidate: KnowledgeCandidate) -> KnowledgeCandidate:
        checked = self._review.submit_for_review(candidate)
        try:
            stored = validate_candidate(
                self._store.submit_candidate(copy.deepcopy(checked))
            )
            if stored != checked:
                raise GovernanceRejected()
            return stored
        except GovernanceRejected:
            raise
        except GovernanceConflict:
            raise
        except Exception as error:
            raise GovernanceUnavailable() from error

    def get_candidate(self, candidate_id: str) -> KnowledgeCandidate | None:
        try:
            checked_id = validate_candidate_id(candidate_id)
        except Exception as error:
            raise GovernanceRejected() from error
        try:
            value = self._store.get_candidate(copy.deepcopy(checked_id))
            if value is None:
                return None
            checked = validate_candidate(value)
            if checked.candidate_id != checked_id:
                raise GovernanceRejected()
            return checked
        except GovernanceRejected:
            raise
        except Exception as error:
            raise GovernanceUnavailable() from error

    def review_candidate(self, request: ReviewRequest) -> KnowledgeCandidate:
        try:
            checked_request = validate_review(request)
        except Exception as error:
            raise GovernanceRejected() from error
        candidate = self.get_candidate(checked_request.candidate_id)
        if candidate is None:
            raise CandidateNotFound()
        updated = self._review.review(candidate, checked_request)
        try:
            stored = validate_candidate(
                self._store.apply_review(
                    copy.deepcopy(updated), copy.deepcopy(checked_request)
                )
            )
            if stored != updated:
                raise GovernanceRejected()
            return stored
        except GovernanceRejected:
            raise
        except GovernanceConflict:
            raise
        except Exception as error:
            raise GovernanceUnavailable() from error

    def create_approved_knowledge(self, candidate_id: str) -> ApprovedKnowledge:
        candidate = self.get_candidate(candidate_id)
        if candidate is None:
            raise CandidateNotFound()
        if candidate.status is not CandidateStatus.APPROVED:
            raise GovernanceRejected()
        try:
            review = self._store.get_review(candidate.candidate_id)
            checked_review = None if review is None else validate_review(review)
        except Exception as error:
            raise GovernanceUnavailable() from error
        if (
            checked_review is None
            or checked_review.decision is not ReviewDecision.APPROVE
            or checked_review.candidate_id != candidate.candidate_id
            or checked_review.candidate_fingerprint != candidate.fingerprint
        ):
            raise GovernanceRejected()

        knowledge_id = (
            "knowledge-"
            + hashlib.sha256(
                f"{candidate.project_id}:{candidate.candidate_id}:{candidate.fingerprint}".encode()
            ).hexdigest()[:48]
        )
        knowledge = ApprovedKnowledge.create(
            knowledge_id=knowledge_id,
            candidate_id=candidate.candidate_id,
            project_id=candidate.project_id,
            claim=candidate.claim,
            evidence_refs=candidate.evidence_refs,
            approved_by=checked_review.reviewer,
        )
        try:
            published = validate_approved_knowledge(
                self._sink.publish(copy.deepcopy(knowledge))
            )
            if published != knowledge:
                raise GovernanceRejected()
            return published
        except GovernanceRejected:
            raise
        except GovernanceConflict:
            raise
        except Exception as error:
            raise GovernanceUnavailable() from error


__all__ = ("KnowledgeGovernanceService", "KnowledgeReviewService")
