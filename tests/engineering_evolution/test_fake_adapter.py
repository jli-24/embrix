from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from embedded_copilot.engineering_evolution import (
    EvolutionApplicationRecord,
    EvolutionApplyRequest,
    EvolutionConflict,
    EvolutionProposalStatus,
    EvolutionReviewDecision,
    EvolutionReviewRequest,
    EvolutionTarget,
    KnowledgeEvolutionProposal,
)
from embedded_copilot.engineering_evolution.adapters import (
    FakeApprovedKnowledgeSource,
    FakeControlledEvolutionSink,
    FakeEvolutionProposalStore,
)
from embedded_copilot.engineering_governance import ApprovedKnowledge

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def knowledge() -> ApprovedKnowledge:
    return ApprovedKnowledge.create(
        knowledge_id="knowledge-fake-1",
        candidate_id="candidate-fake-1",
        project_id="project-1",
        claim="SPI interface detected",
        evidence_refs=("context:evidence-1",),
        approved_by="reviewer-1",
    )


def proposal() -> KnowledgeEvolutionProposal:
    value = knowledge()
    return KnowledgeEvolutionProposal.create(
        proposal_id="evolution-fake-1",
        request_id="request-fake-1",
        project_id=value.project_id,
        source_knowledge_id=value.knowledge_id,
        source_knowledge_fingerprint=value.fingerprint,
        source_candidate_id=value.candidate_id,
        source_approved_by=value.approved_by,
        target=EvolutionTarget.GRAPH,
        claim=value.claim,
        evidence_refs=value.evidence_refs,
        created_by="engineer-1",
        created_at=NOW,
        status=EvolutionProposalStatus.CREATED,
    )


def test_fake_source_and_store_are_copy_safe_and_conflict_aware() -> None:
    source = FakeApprovedKnowledgeSource((knowledge(),))
    fetched = source.get_approved_knowledge("project-1", "knowledge-fake-1")
    assert fetched == knowledge()

    store = FakeEvolutionProposalStore()
    value = proposal()
    assert store.create_proposal(value) == value
    assert store.create_proposal(value) == value
    conflicting = KnowledgeEvolutionProposal.create(
        **{**value.model_dump(mode="python"), "created_by": "engineer-2"}
    )
    with pytest.raises(EvolutionConflict):
        store.create_proposal(conflicting)


def test_fake_store_and_sink_preserve_review_and_application_audit() -> None:
    store = FakeEvolutionProposalStore()
    sink = FakeControlledEvolutionSink()
    created = store.create_proposal(proposal())
    review = EvolutionReviewRequest.create(
        review_id="review-fake-1",
        project_id=created.project_id,
        proposal_id=created.proposal_id,
        proposal_fingerprint=created.fingerprint,
        reviewer="reviewer-2",
        decision=EvolutionReviewDecision.APPROVE,
        comment="approved",
        reviewed_at=NOW + timedelta(minutes=1),
    )
    approved = store.apply_review(
        created.with_status(EvolutionProposalStatus.APPROVED), review
    )
    request = EvolutionApplyRequest.create(
        application_id="application-fake-1",
        project_id=approved.project_id,
        proposal_id=approved.proposal_id,
        proposal_fingerprint=approved.fingerprint,
        review_fingerprint=review.fingerprint,
        applied_by="operator-1",
        applied_at=NOW + timedelta(minutes=2),
    )
    receipt = sink.apply(approved, review, request)
    record = EvolutionApplicationRecord.create(request=request, receipt=receipt)
    applied = store.apply_application(
        approved.with_status(EvolutionProposalStatus.APPLIED), record
    )
    assert applied.status is EvolutionProposalStatus.APPLIED
    assert store.get_review(applied.project_id, applied.proposal_id) == review
    assert store.get_application(applied.project_id, applied.proposal_id) == record
