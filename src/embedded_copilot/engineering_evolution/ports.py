from __future__ import annotations

from typing import Protocol, runtime_checkable

from embedded_copilot.engineering_governance import ApprovedKnowledge

from .models import (
    EvolutionApplicationRecord,
    EvolutionApplyRequest,
    EvolutionReviewRequest,
    EvolutionSinkReceipt,
    KnowledgeEvolutionProposal,
)


@runtime_checkable
class ApprovedKnowledgeSourcePort(Protocol):
    def get_approved_knowledge(
        self, project_id: str, knowledge_id: str
    ) -> ApprovedKnowledge | None: ...


@runtime_checkable
class EvolutionProposalStorePort(Protocol):
    def create_proposal(
        self, proposal: KnowledgeEvolutionProposal
    ) -> KnowledgeEvolutionProposal: ...

    def get_proposal(
        self, project_id: str, proposal_id: str
    ) -> KnowledgeEvolutionProposal | None: ...

    def apply_review(
        self,
        proposal: KnowledgeEvolutionProposal,
        review: EvolutionReviewRequest,
    ) -> KnowledgeEvolutionProposal: ...

    def get_review(
        self, project_id: str, proposal_id: str
    ) -> EvolutionReviewRequest | None: ...

    def apply_application(
        self,
        proposal: KnowledgeEvolutionProposal,
        record: EvolutionApplicationRecord,
    ) -> KnowledgeEvolutionProposal: ...

    def get_application(
        self, project_id: str, proposal_id: str
    ) -> EvolutionApplicationRecord | None: ...


@runtime_checkable
class ControlledEvolutionSinkPort(Protocol):
    def apply(
        self,
        proposal: KnowledgeEvolutionProposal,
        approved_review: EvolutionReviewRequest,
        apply_request: EvolutionApplyRequest,
    ) -> EvolutionSinkReceipt: ...


__all__ = (
    "ApprovedKnowledgeSourcePort",
    "ControlledEvolutionSinkPort",
    "EvolutionProposalStorePort",
)
