from __future__ import annotations

import copy

from .contracts import (
    validate_application_record,
    validate_apply_request,
    validate_project_id,
    validate_proposal,
    validate_proposal_id,
    validate_proposal_request,
    validate_receipt,
    validate_review,
    validate_source_knowledge,
)
from .exceptions import (
    ApprovedKnowledgeNotFound,
    EngineeringEvolutionError,
    EvolutionConflict,
    EvolutionProposalNotFound,
    EvolutionRejected,
    EvolutionUnavailable,
)
from .models import (
    EvolutionApplicationRecord,
    EvolutionApplyRequest,
    EvolutionProposalRequest,
    EvolutionProposalStatus,
    EvolutionReviewDecision,
    EvolutionReviewRequest,
    KnowledgeEvolutionProposal,
    proposal_id,
)
from .ports import (
    ApprovedKnowledgeSourcePort,
    ControlledEvolutionSinkPort,
    EvolutionProposalStorePort,
)


class KnowledgeEvolutionService:
    """Create, review, and hand off immutable knowledge evolution proposals."""

    __slots__ = ("_sink", "_source", "_store")

    def __init__(
        self,
        *,
        source: ApprovedKnowledgeSourcePort,
        store: EvolutionProposalStorePort,
        sink: ControlledEvolutionSinkPort,
    ) -> None:
        if not isinstance(source, ApprovedKnowledgeSourcePort):
            raise TypeError("approved knowledge source is invalid")
        if not isinstance(store, EvolutionProposalStorePort):
            raise TypeError("evolution proposal store is invalid")
        if not isinstance(sink, ControlledEvolutionSinkPort):
            raise TypeError("controlled evolution sink is invalid")
        self._source = source
        self._store = store
        self._sink = sink

    def create_proposal(
        self, request: EvolutionProposalRequest
    ) -> KnowledgeEvolutionProposal:
        try:
            checked_request = validate_proposal_request(request)
        except Exception as error:
            raise EvolutionRejected() from error

        try:
            source_value = self._source.get_approved_knowledge(
                copy.deepcopy(checked_request.project_id),
                copy.deepcopy(checked_request.knowledge_id),
            )
        except Exception as error:
            raise EvolutionUnavailable() from error
        if source_value is None:
            raise ApprovedKnowledgeNotFound()
        try:
            knowledge = validate_source_knowledge(source_value)
        except Exception as error:
            raise EvolutionUnavailable() from error
        if (
            knowledge.project_id != checked_request.project_id
            or knowledge.knowledge_id != checked_request.knowledge_id
            or knowledge.fingerprint != checked_request.knowledge_fingerprint
        ):
            raise EvolutionRejected()

        proposal = KnowledgeEvolutionProposal.create(
            proposal_id=proposal_id(
                knowledge.project_id,
                knowledge.knowledge_id,
                knowledge.fingerprint,
                checked_request.target,
            ),
            request_id=checked_request.request_id,
            project_id=knowledge.project_id,
            source_knowledge_id=knowledge.knowledge_id,
            source_knowledge_fingerprint=knowledge.fingerprint,
            source_candidate_id=knowledge.candidate_id,
            source_approved_by=knowledge.approved_by,
            target=checked_request.target,
            claim=knowledge.claim,
            evidence_refs=knowledge.evidence_refs,
            created_by=checked_request.created_by,
            created_at=checked_request.created_at,
            status=EvolutionProposalStatus.CREATED,
        )
        try:
            stored = validate_proposal(
                self._store.create_proposal(copy.deepcopy(proposal))
            )
        except EvolutionConflict:
            raise
        except Exception as error:
            raise EvolutionUnavailable() from error
        if stored != proposal:
            raise EvolutionUnavailable()
        return stored

    def get_proposal(
        self, project_id: str, proposal_id_value: str
    ) -> KnowledgeEvolutionProposal | None:
        try:
            project = validate_project_id(project_id)
            checked_id = validate_proposal_id(proposal_id_value)
        except Exception as error:
            raise EvolutionRejected() from error
        try:
            value = self._store.get_proposal(
                copy.deepcopy(project), copy.deepcopy(checked_id)
            )
            if value is None:
                return None
            checked = validate_proposal(value)
        except Exception as error:
            raise EvolutionUnavailable() from error
        if checked.project_id != project or checked.proposal_id != checked_id:
            raise EvolutionUnavailable()
        return checked

    def review_proposal(
        self, request: EvolutionReviewRequest
    ) -> KnowledgeEvolutionProposal:
        try:
            checked_request = validate_review(request)
        except Exception as error:
            raise EvolutionRejected() from error
        proposal = self.get_proposal(
            checked_request.project_id, checked_request.proposal_id
        )
        if proposal is None:
            raise EvolutionProposalNotFound()
        if proposal.status is not EvolutionProposalStatus.CREATED:
            prior = self.get_review(proposal.project_id, proposal.proposal_id)
            if prior == checked_request and proposal.status is _review_status(
                checked_request.decision
            ):
                return proposal
            raise EvolutionConflict()
        if (
            checked_request.proposal_fingerprint != proposal.fingerprint
            or checked_request.reviewed_at < proposal.created_at
        ):
            raise EvolutionRejected()

        updated = proposal.with_status(_review_status(checked_request.decision))
        try:
            stored = validate_proposal(
                self._store.apply_review(
                    copy.deepcopy(updated), copy.deepcopy(checked_request)
                )
            )
        except EvolutionConflict:
            raise
        except Exception as error:
            raise EvolutionUnavailable() from error
        if stored != updated:
            raise EvolutionUnavailable()
        return stored

    def get_review(
        self, project_id: str, proposal_id_value: str
    ) -> EvolutionReviewRequest | None:
        try:
            project = validate_project_id(project_id)
            checked_id = validate_proposal_id(proposal_id_value)
        except Exception as error:
            raise EvolutionRejected() from error
        try:
            value = self._store.get_review(
                copy.deepcopy(project), copy.deepcopy(checked_id)
            )
            if value is None:
                return None
            checked = validate_review(value)
        except Exception as error:
            raise EvolutionUnavailable() from error
        if checked.project_id != project or checked.proposal_id != checked_id:
            raise EvolutionUnavailable()
        return checked

    def apply_proposal(
        self, request: EvolutionApplyRequest
    ) -> KnowledgeEvolutionProposal:
        try:
            checked_request = validate_apply_request(request)
        except Exception as error:
            raise EvolutionRejected() from error
        proposal = self.get_proposal(
            checked_request.project_id, checked_request.proposal_id
        )
        if proposal is None:
            raise EvolutionProposalNotFound()
        if proposal.status is EvolutionProposalStatus.APPLIED:
            prior = self.get_application(proposal.project_id, proposal.proposal_id)
            if prior is not None and prior.request == checked_request:
                return proposal
            raise EvolutionConflict()
        if proposal.status is not EvolutionProposalStatus.APPROVED:
            raise EvolutionRejected()

        review = self.get_review(proposal.project_id, proposal.proposal_id)
        if review is None:
            raise EvolutionRejected()
        if (
            review.decision is not EvolutionReviewDecision.APPROVE
            or checked_request.proposal_fingerprint != proposal.fingerprint
            or checked_request.review_fingerprint != review.fingerprint
            or checked_request.applied_at < review.reviewed_at
        ):
            raise EvolutionRejected()

        try:
            receipt = validate_receipt(
                self._sink.apply(
                    copy.deepcopy(proposal),
                    copy.deepcopy(review),
                    copy.deepcopy(checked_request),
                )
            )
        except EngineeringEvolutionError:
            raise
        except Exception as error:
            raise EvolutionUnavailable() from error
        if (
            receipt.application_id != checked_request.application_id
            or receipt.project_id != proposal.project_id
            or receipt.proposal_id != proposal.proposal_id
            or receipt.proposal_fingerprint != proposal.fingerprint
            or receipt.review_fingerprint != review.fingerprint
            or receipt.target is not proposal.target
            or receipt.accepted_at < checked_request.applied_at
        ):
            raise EvolutionUnavailable()

        record = EvolutionApplicationRecord.create(
            request=checked_request,
            receipt=receipt,
        )
        updated = proposal.with_status(EvolutionProposalStatus.APPLIED)
        try:
            stored = validate_proposal(
                self._store.apply_application(
                    copy.deepcopy(updated), copy.deepcopy(record)
                )
            )
        except EvolutionConflict:
            raise
        except Exception as error:
            raise EvolutionUnavailable() from error
        if stored != updated:
            raise EvolutionUnavailable()
        return stored

    def get_application(
        self, project_id: str, proposal_id_value: str
    ) -> EvolutionApplicationRecord | None:
        try:
            project = validate_project_id(project_id)
            checked_id = validate_proposal_id(proposal_id_value)
        except Exception as error:
            raise EvolutionRejected() from error
        try:
            value = self._store.get_application(
                copy.deepcopy(project), copy.deepcopy(checked_id)
            )
            if value is None:
                return None
            checked = validate_application_record(value)
        except Exception as error:
            raise EvolutionUnavailable() from error
        if (
            checked.request.project_id != project
            or checked.request.proposal_id != checked_id
        ):
            raise EvolutionUnavailable()
        return checked


def _review_status(decision: EvolutionReviewDecision) -> EvolutionProposalStatus:
    return {
        EvolutionReviewDecision.APPROVE: EvolutionProposalStatus.APPROVED,
        EvolutionReviewDecision.REJECT: EvolutionProposalStatus.REJECTED,
    }[decision]


__all__ = ("KnowledgeEvolutionService",)
