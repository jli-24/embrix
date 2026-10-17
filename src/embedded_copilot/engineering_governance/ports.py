from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import ApprovedKnowledge, KnowledgeCandidate, ReviewRequest


@runtime_checkable
class KnowledgeGovernanceStorePort(Protocol):
    def submit_candidate(self, candidate: KnowledgeCandidate) -> KnowledgeCandidate: ...

    def get_candidate(self, candidate_id: str) -> KnowledgeCandidate | None: ...

    def apply_review(
        self, candidate: KnowledgeCandidate, request: ReviewRequest
    ) -> KnowledgeCandidate: ...

    def get_review(self, candidate_id: str) -> ReviewRequest | None: ...


@runtime_checkable
class ApprovedKnowledgeSinkPort(Protocol):
    def publish(self, knowledge: ApprovedKnowledge) -> ApprovedKnowledge: ...


@runtime_checkable
class KnowledgeReviewPort(Protocol):
    def submit_for_review(
        self, candidate: KnowledgeCandidate
    ) -> KnowledgeCandidate: ...

    def review(
        self, candidate: KnowledgeCandidate, request: ReviewRequest
    ) -> KnowledgeCandidate: ...


__all__ = (
    "ApprovedKnowledgeSinkPort",
    "KnowledgeGovernanceStorePort",
    "KnowledgeReviewPort",
)
