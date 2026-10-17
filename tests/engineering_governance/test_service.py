from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from embedded_copilot.engineering_governance import (
    CandidateStatus,
    GovernanceRejected,
    GovernanceUnavailable,
    KnowledgeCandidate,
    KnowledgeGovernanceService,
    ReviewDecision,
    ReviewRequest,
)
from embedded_copilot.engineering_governance.adapters import (
    FakeApprovedKnowledgeSink,
    FakeKnowledgeGovernanceStore,
)

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def candidate() -> KnowledgeCandidate:
    return KnowledgeCandidate.create(
        candidate_id="candidate-service-1",
        project_id="project-1",
        claim="UART interface detected",
        source_type="multimodal",
        evidence_refs=("vision:obs001",),
        confidence=0.85,
        status="PENDING",
    )


def request(
    value: KnowledgeCandidate,
    decision: ReviewDecision,
    suffix: str = "1",
) -> ReviewRequest:
    return ReviewRequest.create(
        review_id=f"review-{suffix}",
        candidate_id=value.candidate_id,
        candidate_fingerprint=value.fingerprint,
        reviewer="reviewer-1",
        decision=decision,
        comment="Human evidence review.",
        reviewed_at=NOW + timedelta(seconds=int(suffix)),
    )


def service() -> tuple[
    KnowledgeGovernanceService,
    FakeKnowledgeGovernanceStore,
    FakeApprovedKnowledgeSink,
]:
    store = FakeKnowledgeGovernanceStore()
    sink = FakeApprovedKnowledgeSink()
    return KnowledgeGovernanceService(store=store, sink=sink), store, sink


def test_submit_review_and_approve_publishes_only_after_human_review() -> None:
    governance, _, sink = service()
    pending = governance.submit_candidate(candidate())
    assert pending.status is CandidateStatus.PENDING
    with pytest.raises(GovernanceRejected):
        governance.create_approved_knowledge(pending.candidate_id)

    approved = governance.review_candidate(request(pending, ReviewDecision.APPROVE))
    assert approved.status is CandidateStatus.APPROVED
    assert approved.fingerprint == pending.fingerprint
    knowledge = governance.create_approved_knowledge(approved.candidate_id)
    assert knowledge.approved_by == "reviewer-1"
    assert sink.get(knowledge.knowledge_id) == knowledge


@pytest.mark.parametrize(
    ("decision", "status"),
    (
        (ReviewDecision.REJECT, CandidateStatus.REJECTED),
        (ReviewDecision.REQUEST_CHANGE, CandidateStatus.PENDING),
    ),
)
def test_review_decisions_are_explicit(
    decision: ReviewDecision, status: CandidateStatus
) -> None:
    governance, _, _ = service()
    pending = governance.submit_candidate(candidate())
    result = governance.review_candidate(request(pending, decision, "2"))
    assert result.status is status
    with pytest.raises(GovernanceRejected):
        governance.create_approved_knowledge(result.candidate_id)


def test_approved_knowledge_requires_matching_candidate_fingerprint() -> None:
    class TamperingStore(FakeKnowledgeGovernanceStore):
        replacement: KnowledgeCandidate | None = None

        def get_candidate(self, candidate_id: str) -> KnowledgeCandidate | None:
            if self.replacement is not None:
                return self.replacement
            return super().get_candidate(candidate_id)

    store = TamperingStore()
    governance = KnowledgeGovernanceService(
        store=store,
        sink=FakeApprovedKnowledgeSink(),
    )
    pending = governance.submit_candidate(candidate())
    governance.review_candidate(request(pending, ReviewDecision.APPROVE))

    tampered = KnowledgeCandidate.create(
        **{
            **pending.model_dump(mode="python"),
            "claim": "SPI interface detected",
            "status": "APPROVED",
        }
    )
    assert tampered.fingerprint != pending.fingerprint
    store.replacement = tampered
    with pytest.raises(GovernanceRejected):
        governance.create_approved_knowledge(pending.candidate_id)


def test_review_candidate_rejects_fingerprint_replay_and_non_pending_transition() -> (
    None
):
    governance, _, _ = service()
    pending = governance.submit_candidate(candidate())
    changed = KnowledgeCandidate.create(
        **{
            **pending.model_dump(mode="python"),
            "claim": "SPI interface detected",
        }
    )
    forged = request(
        changed,
        ReviewDecision.APPROVE,
        "3",
    )
    with pytest.raises(GovernanceRejected):
        governance.review_candidate(forged)
    approved = governance.review_candidate(
        request(pending, ReviewDecision.APPROVE, "4")
    )
    with pytest.raises(GovernanceRejected):
        governance.review_candidate(request(approved, ReviewDecision.REJECT, "5"))


def test_sink_failure_is_unavailable_and_never_reported_as_success() -> None:
    class FailingSink:
        def publish(self, knowledge):
            raise RuntimeError("unavailable")

    governance = KnowledgeGovernanceService(
        store=FakeKnowledgeGovernanceStore(),
        sink=FailingSink(),
    )
    pending = governance.submit_candidate(candidate())
    approved = governance.review_candidate(
        request(pending, ReviewDecision.APPROVE, "6")
    )
    with pytest.raises(GovernanceUnavailable):
        governance.create_approved_knowledge(approved.candidate_id)
