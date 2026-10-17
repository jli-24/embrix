from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from embedded_copilot.engineering_evolution import (
    ApprovedKnowledgeNotFound,
    EvolutionApplyRequest,
    EvolutionConflict,
    EvolutionProposalRequest,
    EvolutionProposalStatus,
    EvolutionRejected,
    EvolutionReviewDecision,
    EvolutionReviewRequest,
    EvolutionSinkReceipt,
    EvolutionTarget,
    EvolutionUnavailable,
    KnowledgeEvolutionService,
)
from embedded_copilot.engineering_evolution.adapters import (
    FakeApprovedKnowledgeSource,
    FakeControlledEvolutionSink,
    FakeEvolutionProposalStore,
)
from embedded_copilot.engineering_governance import ApprovedKnowledge

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def approved_knowledge(**overrides: object) -> ApprovedKnowledge:
    values = {
        "knowledge_id": "knowledge-1",
        "candidate_id": "candidate-1",
        "project_id": "project-1",
        "claim": "GPIO12 is reserved",
        "evidence_refs": ("context:evidence-1",),
        "approved_by": "reviewer-1",
    }
    values.update(overrides)
    return ApprovedKnowledge.create(**values)


def create_request(
    knowledge: ApprovedKnowledge, target: EvolutionTarget = EvolutionTarget.MEMORY
) -> EvolutionProposalRequest:
    return EvolutionProposalRequest.create(
        request_id=f"request-{target.value.lower()}",
        project_id=knowledge.project_id,
        knowledge_id=knowledge.knowledge_id,
        knowledge_fingerprint=knowledge.fingerprint,
        target=target,
        created_by="engineer-1",
        created_at=NOW,
    )


def review_request(proposal, decision=EvolutionReviewDecision.APPROVE):
    return EvolutionReviewRequest.create(
        review_id=f"review-{decision.value.lower()}",
        project_id=proposal.project_id,
        proposal_id=proposal.proposal_id,
        proposal_fingerprint=proposal.fingerprint,
        reviewer="reviewer-2",
        decision=decision,
        comment="Human review completed.",
        reviewed_at=NOW + timedelta(minutes=1),
    )


def apply_request(proposal, review):
    return EvolutionApplyRequest.create(
        application_id="application-1",
        project_id=proposal.project_id,
        proposal_id=proposal.proposal_id,
        proposal_fingerprint=proposal.fingerprint,
        review_fingerprint=review.fingerprint,
        applied_by="operator-1",
        applied_at=NOW + timedelta(minutes=2),
    )


def service(knowledge: ApprovedKnowledge | None = None):
    value = knowledge or approved_knowledge()
    source = FakeApprovedKnowledgeSource((value,))
    store = FakeEvolutionProposalStore()
    sink = FakeControlledEvolutionSink()
    return KnowledgeEvolutionService(source=source, store=store, sink=sink), store, sink


@pytest.mark.parametrize("target", tuple(EvolutionTarget))
def test_create_review_and_explicit_apply_for_each_target(
    target: EvolutionTarget,
) -> None:
    knowledge = approved_knowledge()
    evolution, _, sink = service(knowledge)
    proposal = evolution.create_proposal(create_request(knowledge, target))
    assert proposal.status is EvolutionProposalStatus.CREATED
    assert proposal.target is target
    assert proposal.source_knowledge_fingerprint == knowledge.fingerprint

    review = review_request(proposal)
    approved = evolution.review_proposal(review)
    assert approved.status is EvolutionProposalStatus.APPROVED
    assert approved.fingerprint == proposal.fingerprint

    applied = evolution.apply_proposal(apply_request(approved, review))
    assert applied.status is EvolutionProposalStatus.APPLIED
    assert sink.call_count == 1
    assert evolution.get_review(applied.project_id, applied.proposal_id) == review
    assert (
        evolution.get_application(applied.project_id, applied.proposal_id) is not None
    )


def test_source_identity_fingerprint_and_project_are_checked() -> None:
    knowledge = approved_knowledge()
    evolution, _, _ = service(knowledge)
    with pytest.raises(ApprovedKnowledgeNotFound):
        evolution.create_proposal(
            EvolutionProposalRequest.create(
                **{
                    **create_request(knowledge).model_dump(mode="python"),
                    "knowledge_id": "knowledge-missing",
                }
            )
        )
    with pytest.raises(EvolutionRejected):
        evolution.create_proposal(
            EvolutionProposalRequest.create(
                **{
                    **create_request(knowledge).model_dump(mode="python"),
                    "knowledge_fingerprint": "sha256:" + "b" * 64,
                }
            )
        )

    class WrongProjectSource:
        def get_approved_knowledge(self, project_id, knowledge_id):
            return approved_knowledge(project_id="project-other")

    wrong_project = KnowledgeEvolutionService(
        source=WrongProjectSource(),
        store=FakeEvolutionProposalStore(),
        sink=FakeControlledEvolutionSink(),
    )
    with pytest.raises(EvolutionRejected):
        wrong_project.create_proposal(create_request(knowledge))


def test_reject_is_terminal_and_created_cannot_be_applied() -> None:
    knowledge = approved_knowledge()
    evolution, _, _ = service(knowledge)
    proposal = evolution.create_proposal(create_request(knowledge))
    premature_review = review_request(proposal)
    with pytest.raises(EvolutionRejected):
        evolution.apply_proposal(apply_request(proposal, premature_review))

    rejected_review = review_request(proposal, EvolutionReviewDecision.REJECT)
    rejected = evolution.review_proposal(rejected_review)
    assert rejected.status is EvolutionProposalStatus.REJECTED
    with pytest.raises(EvolutionConflict):
        evolution.review_proposal(review_request(rejected))
    with pytest.raises(EvolutionRejected):
        evolution.apply_proposal(apply_request(rejected, rejected_review))


def test_identical_retries_are_idempotent_and_do_not_reinvoke_sink() -> None:
    knowledge = approved_knowledge()
    evolution, _, sink = service(knowledge)
    request = create_request(knowledge)
    first = evolution.create_proposal(request)
    assert evolution.create_proposal(request) == first

    review = review_request(first)
    approved = evolution.review_proposal(review)
    assert evolution.review_proposal(review) == approved

    application = apply_request(approved, review)
    applied = evolution.apply_proposal(application)
    assert evolution.apply_proposal(application) == applied
    assert sink.call_count == 1


def test_time_order_and_stale_fingerprints_fail_closed() -> None:
    knowledge = approved_knowledge()
    evolution, _, _ = service(knowledge)
    proposal = evolution.create_proposal(create_request(knowledge))
    early = EvolutionReviewRequest.create(
        **{
            **review_request(proposal).model_dump(mode="python"),
            "reviewed_at": NOW - timedelta(seconds=1),
        }
    )
    with pytest.raises(EvolutionRejected):
        evolution.review_proposal(early)

    stale = EvolutionReviewRequest.create(
        **{
            **review_request(proposal).model_dump(mode="python"),
            "proposal_fingerprint": "sha256:" + "c" * 64,
        }
    )
    with pytest.raises(EvolutionRejected):
        evolution.review_proposal(stale)

    review = review_request(proposal)
    approved = evolution.review_proposal(review)
    stale_apply = EvolutionApplyRequest.create(
        **{
            **apply_request(approved, review).model_dump(mode="python"),
            "review_fingerprint": "sha256:" + "d" * 64,
        }
    )
    with pytest.raises(EvolutionRejected):
        evolution.apply_proposal(stale_apply)

    early_apply = EvolutionApplyRequest.create(
        **{
            **apply_request(approved, review).model_dump(mode="python"),
            "applied_at": review.reviewed_at - timedelta(seconds=1),
        }
    )
    with pytest.raises(EvolutionRejected):
        evolution.apply_proposal(early_apply)


def test_sink_failure_never_marks_proposal_applied() -> None:
    class FailingSink:
        def apply(self, proposal, approved_review, apply_request):
            raise RuntimeError("sink unavailable")

    knowledge = approved_knowledge()
    store = FakeEvolutionProposalStore()
    evolution = KnowledgeEvolutionService(
        source=FakeApprovedKnowledgeSource((knowledge,)),
        store=store,
        sink=FailingSink(),
    )
    proposal = evolution.create_proposal(create_request(knowledge))
    review = review_request(proposal)
    approved = evolution.review_proposal(review)
    with pytest.raises(EvolutionUnavailable):
        evolution.apply_proposal(apply_request(approved, review))
    assert (
        evolution.get_proposal(approved.project_id, approved.proposal_id).status
        is EvolutionProposalStatus.APPROVED
    )


def test_mismatched_sink_receipt_is_rejected_and_proposal_stays_approved() -> None:
    class MismatchedSink:
        def apply(self, proposal, approved_review, apply_request):
            return EvolutionSinkReceipt.create(
                receipt_id="receipt-mismatch",
                application_id=apply_request.application_id,
                project_id=proposal.project_id,
                proposal_id=proposal.proposal_id,
                proposal_fingerprint=proposal.fingerprint,
                review_fingerprint=approved_review.fingerprint,
                target=EvolutionTarget.GRAPH,
                accepted_at=apply_request.applied_at,
            )

    knowledge = approved_knowledge()
    store = FakeEvolutionProposalStore()
    evolution = KnowledgeEvolutionService(
        source=FakeApprovedKnowledgeSource((knowledge,)),
        store=store,
        sink=MismatchedSink(),
    )
    proposal = evolution.create_proposal(create_request(knowledge))
    review = review_request(proposal)
    approved = evolution.review_proposal(review)
    with pytest.raises(EvolutionUnavailable):
        evolution.apply_proposal(apply_request(approved, review))
    current = evolution.get_proposal(approved.project_id, approved.proposal_id)
    assert current is not None
    assert current.status is EvolutionProposalStatus.APPROVED


def test_store_failure_after_sink_receipt_never_reports_applied() -> None:
    class FailingApplicationStore(FakeEvolutionProposalStore):
        def apply_application(self, proposal, record):
            raise RuntimeError("store unavailable")

    knowledge = approved_knowledge()
    store = FailingApplicationStore()
    sink = FakeControlledEvolutionSink()
    evolution = KnowledgeEvolutionService(
        source=FakeApprovedKnowledgeSource((knowledge,)),
        store=store,
        sink=sink,
    )
    proposal = evolution.create_proposal(create_request(knowledge))
    review = review_request(proposal)
    approved = evolution.review_proposal(review)
    with pytest.raises(EvolutionUnavailable):
        evolution.apply_proposal(apply_request(approved, review))
    assert sink.call_count == 1
    current = evolution.get_proposal(approved.project_id, approved.proposal_id)
    assert current is not None
    assert current.status is EvolutionProposalStatus.APPROVED
