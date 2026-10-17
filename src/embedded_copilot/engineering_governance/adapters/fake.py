from __future__ import annotations

import copy

from ..contracts import (
    validate_approved_knowledge,
    validate_candidate,
    validate_review,
)
from ..exceptions import GovernanceConflict
from ..models import (
    ApprovedKnowledge,
    CandidateStatus,
    KnowledgeCandidate,
    ReviewDecision,
    ReviewRequest,
)
from ..ports import ApprovedKnowledgeSinkPort, KnowledgeGovernanceStorePort


class FakeKnowledgeGovernanceStore(KnowledgeGovernanceStorePort):
    """Deterministic in-memory store for offline contract tests."""

    def __init__(self) -> None:
        self._candidates: dict[str, KnowledgeCandidate] = {}
        self._reviews: dict[str, ReviewRequest] = {}

    def submit_candidate(self, candidate: KnowledgeCandidate) -> KnowledgeCandidate:
        checked = validate_candidate(candidate)
        if checked.status is not CandidateStatus.PENDING:
            raise GovernanceConflict()
        existing = self._candidates.get(checked.candidate_id)
        if existing is not None and existing != checked:
            raise GovernanceConflict()
        self._candidates[checked.candidate_id] = checked
        return validate_candidate(copy.deepcopy(checked))

    def get_candidate(self, candidate_id: str) -> KnowledgeCandidate | None:
        value = self._candidates.get(candidate_id)
        return None if value is None else validate_candidate(copy.deepcopy(value))

    def apply_review(
        self, candidate: KnowledgeCandidate, request: ReviewRequest
    ) -> KnowledgeCandidate:
        checked_candidate = validate_candidate(candidate)
        checked_request = validate_review(request)
        existing = self._candidates.get(checked_candidate.candidate_id)
        if (
            existing is None
            or existing.status is not CandidateStatus.PENDING
            or checked_request.candidate_id != existing.candidate_id
            or checked_request.candidate_fingerprint != existing.fingerprint
            or checked_candidate.fingerprint != existing.fingerprint
        ):
            raise GovernanceConflict()
        expected_status = {
            ReviewDecision.APPROVE: CandidateStatus.APPROVED,
            ReviewDecision.REJECT: CandidateStatus.REJECTED,
            ReviewDecision.REQUEST_CHANGE: CandidateStatus.PENDING,
        }[checked_request.decision]
        if checked_candidate.status is not expected_status:
            raise GovernanceConflict()
        prior_request = self._reviews.get(checked_candidate.candidate_id)
        if (
            prior_request is not None
            and prior_request.review_id != checked_request.review_id
        ):
            raise GovernanceConflict()
        self._candidates[checked_candidate.candidate_id] = checked_candidate
        self._reviews[checked_candidate.candidate_id] = checked_request
        return validate_candidate(copy.deepcopy(checked_candidate))

    def get_review(self, candidate_id: str) -> ReviewRequest | None:
        value = self._reviews.get(candidate_id)
        return None if value is None else validate_review(copy.deepcopy(value))


class FakeApprovedKnowledgeSink(ApprovedKnowledgeSinkPort):
    """In-memory, deterministic and idempotent approved knowledge sink."""

    def __init__(self) -> None:
        self._values: dict[str, ApprovedKnowledge] = {}

    def publish(self, knowledge: ApprovedKnowledge) -> ApprovedKnowledge:
        checked = validate_approved_knowledge(knowledge)
        existing = self._values.get(checked.knowledge_id)
        if existing is not None and existing != checked:
            raise GovernanceConflict()
        self._values[checked.knowledge_id] = checked
        return validate_approved_knowledge(copy.deepcopy(checked))

    def get(self, knowledge_id: str) -> ApprovedKnowledge | None:
        value = self._values.get(knowledge_id)
        return (
            None if value is None else validate_approved_knowledge(copy.deepcopy(value))
        )


__all__ = ("FakeApprovedKnowledgeSink", "FakeKnowledgeGovernanceStore")
