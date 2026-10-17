from __future__ import annotations

import copy
import hashlib

from embedded_copilot.engineering_governance import ApprovedKnowledge

from ..contracts import (
    validate_application_record,
    validate_apply_request,
    validate_proposal,
    validate_receipt,
    validate_review,
    validate_source_knowledge,
)
from ..exceptions import EvolutionConflict
from ..models import (
    EvolutionApplicationRecord,
    EvolutionApplyRequest,
    EvolutionProposalStatus,
    EvolutionReviewDecision,
    EvolutionReviewRequest,
    EvolutionSinkReceipt,
    KnowledgeEvolutionProposal,
    identifier,
)
from ..ports import (
    ApprovedKnowledgeSourcePort,
    ControlledEvolutionSinkPort,
    EvolutionProposalStorePort,
)


class FakeApprovedKnowledgeSource(ApprovedKnowledgeSourcePort):
    """Read-only, deterministic ApprovedKnowledge source for offline tests."""

    def __init__(self, values: tuple[ApprovedKnowledge, ...] = ()) -> None:
        if type(values) is not tuple:
            raise TypeError("approved knowledge values must be a tuple")
        self._values: dict[tuple[str, str], ApprovedKnowledge] = {}
        for value in values:
            checked = validate_source_knowledge(value)
            key = (checked.project_id, checked.knowledge_id)
            existing = self._values.get(key)
            if existing is not None and existing != checked:
                raise EvolutionConflict()
            self._values[key] = checked

    def get_approved_knowledge(
        self, project_id: str, knowledge_id: str
    ) -> ApprovedKnowledge | None:
        project = identifier(project_id, field="project_id")
        knowledge = identifier(knowledge_id, field="knowledge_id")
        value = self._values.get((project, knowledge))
        return (
            None if value is None else validate_source_knowledge(copy.deepcopy(value))
        )


class FakeEvolutionProposalStore(EvolutionProposalStorePort):
    """In-memory proposal lifecycle store with atomic contract checks."""

    def __init__(self) -> None:
        self._proposals: dict[tuple[str, str], KnowledgeEvolutionProposal] = {}
        self._reviews: dict[tuple[str, str], EvolutionReviewRequest] = {}
        self._applications: dict[tuple[str, str], EvolutionApplicationRecord] = {}

    def create_proposal(
        self, proposal: KnowledgeEvolutionProposal
    ) -> KnowledgeEvolutionProposal:
        checked = validate_proposal(proposal)
        if checked.status is not EvolutionProposalStatus.CREATED:
            raise EvolutionConflict()
        key = (checked.project_id, checked.proposal_id)
        existing = self._proposals.get(key)
        if existing is not None and existing != checked:
            raise EvolutionConflict()
        self._proposals[key] = checked
        return validate_proposal(copy.deepcopy(checked))

    def get_proposal(
        self, project_id: str, proposal_id: str
    ) -> KnowledgeEvolutionProposal | None:
        project = identifier(project_id, field="project_id")
        checked_id = identifier(proposal_id, field="proposal_id")
        value = self._proposals.get((project, checked_id))
        return None if value is None else validate_proposal(copy.deepcopy(value))

    def apply_review(
        self,
        proposal: KnowledgeEvolutionProposal,
        review: EvolutionReviewRequest,
    ) -> KnowledgeEvolutionProposal:
        checked_proposal = validate_proposal(proposal)
        checked_review = validate_review(review)
        key = (checked_proposal.project_id, checked_proposal.proposal_id)
        existing = self._proposals.get(key)
        expected_status = {
            EvolutionReviewDecision.APPROVE: EvolutionProposalStatus.APPROVED,
            EvolutionReviewDecision.REJECT: EvolutionProposalStatus.REJECTED,
        }[checked_review.decision]
        prior_review = self._reviews.get(key)
        if (
            existing is not None
            and existing.status is expected_status
            and existing == checked_proposal
            and prior_review == checked_review
        ):
            return validate_proposal(copy.deepcopy(existing))
        if (
            existing is None
            or existing.status is not EvolutionProposalStatus.CREATED
            or existing.fingerprint != checked_proposal.fingerprint
            or checked_proposal.status is not expected_status
            or checked_review.project_id != existing.project_id
            or checked_review.proposal_id != existing.proposal_id
            or checked_review.proposal_fingerprint != existing.fingerprint
            or checked_review.reviewed_at < existing.created_at
            or prior_review is not None
        ):
            raise EvolutionConflict()
        self._proposals[key] = checked_proposal
        self._reviews[key] = checked_review
        return validate_proposal(copy.deepcopy(checked_proposal))

    def get_review(
        self, project_id: str, proposal_id: str
    ) -> EvolutionReviewRequest | None:
        project = identifier(project_id, field="project_id")
        checked_id = identifier(proposal_id, field="proposal_id")
        value = self._reviews.get((project, checked_id))
        return None if value is None else validate_review(copy.deepcopy(value))

    def apply_application(
        self,
        proposal: KnowledgeEvolutionProposal,
        record: EvolutionApplicationRecord,
    ) -> KnowledgeEvolutionProposal:
        checked_proposal = validate_proposal(proposal)
        checked_record = validate_application_record(record)
        key = (checked_proposal.project_id, checked_proposal.proposal_id)
        existing = self._proposals.get(key)
        prior_record = self._applications.get(key)
        if (
            existing is not None
            and existing.status is EvolutionProposalStatus.APPLIED
            and existing == checked_proposal
            and prior_record == checked_record
        ):
            return validate_proposal(copy.deepcopy(existing))
        review = self._reviews.get(key)
        if (
            existing is None
            or existing.status is not EvolutionProposalStatus.APPROVED
            or existing.fingerprint != checked_proposal.fingerprint
            or checked_proposal.status is not EvolutionProposalStatus.APPLIED
            or review is None
            or review.decision is not EvolutionReviewDecision.APPROVE
            or checked_record.request.project_id != existing.project_id
            or checked_record.request.proposal_id != existing.proposal_id
            or checked_record.request.proposal_fingerprint != existing.fingerprint
            or checked_record.request.review_fingerprint != review.fingerprint
            or checked_record.request.applied_at < review.reviewed_at
            or prior_record is not None
        ):
            raise EvolutionConflict()
        self._proposals[key] = checked_proposal
        self._applications[key] = checked_record
        return validate_proposal(copy.deepcopy(checked_proposal))

    def get_application(
        self, project_id: str, proposal_id: str
    ) -> EvolutionApplicationRecord | None:
        project = identifier(project_id, field="project_id")
        checked_id = identifier(proposal_id, field="proposal_id")
        value = self._applications.get((project, checked_id))
        return (
            None if value is None else validate_application_record(copy.deepcopy(value))
        )


class FakeControlledEvolutionSink(ControlledEvolutionSinkPort):
    """Receipt-only sink; it never mutates Memory, Graph, or Retrieval systems."""

    def __init__(self) -> None:
        self._receipts: dict[str, EvolutionSinkReceipt] = {}
        self.call_count = 0

    def apply(
        self,
        proposal: KnowledgeEvolutionProposal,
        approved_review: EvolutionReviewRequest,
        apply_request: EvolutionApplyRequest,
    ) -> EvolutionSinkReceipt:
        self.call_count += 1
        checked_proposal = validate_proposal(proposal)
        checked_review = validate_review(approved_review)
        checked_request = validate_apply_request(apply_request)
        if (
            checked_proposal.status is not EvolutionProposalStatus.APPROVED
            or checked_review.decision is not EvolutionReviewDecision.APPROVE
            or checked_review.project_id != checked_proposal.project_id
            or checked_review.proposal_id != checked_proposal.proposal_id
            or checked_review.proposal_fingerprint != checked_proposal.fingerprint
            or checked_request.project_id != checked_proposal.project_id
            or checked_request.proposal_id != checked_proposal.proposal_id
            or checked_request.proposal_fingerprint != checked_proposal.fingerprint
            or checked_request.review_fingerprint != checked_review.fingerprint
            or checked_request.applied_at < checked_review.reviewed_at
        ):
            raise EvolutionConflict()
        receipt_id = (
            "receipt-"
            + hashlib.sha256(
                (
                    f"{checked_request.application_id}:"
                    f"{checked_request.fingerprint}:"
                    f"{checked_proposal.target.value}"
                ).encode()
            ).hexdigest()[:48]
        )
        receipt = EvolutionSinkReceipt.create(
            receipt_id=receipt_id,
            application_id=checked_request.application_id,
            project_id=checked_request.project_id,
            proposal_id=checked_request.proposal_id,
            proposal_fingerprint=checked_request.proposal_fingerprint,
            review_fingerprint=checked_request.review_fingerprint,
            target=checked_proposal.target,
            accepted_at=checked_request.applied_at,
        )
        existing = self._receipts.get(checked_request.application_id)
        if existing is not None and existing != receipt:
            raise EvolutionConflict()
        self._receipts[checked_request.application_id] = receipt
        return validate_receipt(copy.deepcopy(receipt))

    def get(self, application_id: str) -> EvolutionSinkReceipt | None:
        checked_id = identifier(application_id, field="application_id")
        value = self._receipts.get(checked_id)
        return None if value is None else validate_receipt(copy.deepcopy(value))


__all__ = (
    "FakeApprovedKnowledgeSource",
    "FakeControlledEvolutionSink",
    "FakeEvolutionProposalStore",
)
