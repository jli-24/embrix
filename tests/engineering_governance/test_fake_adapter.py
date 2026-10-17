from __future__ import annotations

from datetime import UTC, datetime

import pytest

from embedded_copilot.engineering_governance import (
    ApprovedKnowledge,
    GovernanceConflict,
    KnowledgeCandidate,
    ReviewDecision,
    ReviewRequest,
)
from embedded_copilot.engineering_governance.adapters import (
    FakeApprovedKnowledgeSink,
    FakeKnowledgeGovernanceStore,
)


def candidate() -> KnowledgeCandidate:
    return KnowledgeCandidate.create(
        candidate_id="candidate-fake-1",
        project_id="project-1",
        claim="STM32 component observed",
        source_type="multimodal",
        evidence_refs=("vision:obs001",),
        confidence=0.8,
        status="PENDING",
    )


def test_fake_store_submit_get_and_review_are_copy_safe() -> None:
    store = FakeKnowledgeGovernanceStore()
    value = candidate()
    stored = store.submit_candidate(value)
    assert stored == value
    assert store.get_candidate(value.candidate_id) == value

    request = ReviewRequest.create(
        review_id="review-fake-1",
        candidate_id=value.candidate_id,
        candidate_fingerprint=value.fingerprint,
        reviewer="reviewer-1",
        decision=ReviewDecision.APPROVE,
        comment="checked",
        reviewed_at=datetime(2026, 8, 9, 12, 0, tzinfo=UTC),
    )
    approved = value.with_status("APPROVED")
    result = store.apply_review(approved, request)
    assert result.status == "APPROVED"
    assert store.get_review(value.candidate_id) == request


def test_fake_store_rejects_review_result_with_changed_candidate_content() -> None:
    store = FakeKnowledgeGovernanceStore()
    value = store.submit_candidate(candidate())
    request = ReviewRequest.create(
        review_id="review-fake-tampered",
        candidate_id=value.candidate_id,
        candidate_fingerprint=value.fingerprint,
        reviewer="reviewer-1",
        decision=ReviewDecision.APPROVE,
        comment="checked",
        reviewed_at=datetime(2026, 8, 9, 12, 0, tzinfo=UTC),
    )
    tampered = KnowledgeCandidate.create(
        **{
            **value.model_dump(mode="python"),
            "claim": "UART interface detected",
            "status": "APPROVED",
        }
    )
    with pytest.raises(GovernanceConflict):
        store.apply_review(tampered, request)


def test_fake_sink_publish_is_idempotent_and_rejects_conflicting_identity() -> None:
    sink = FakeApprovedKnowledgeSink()
    value = ApprovedKnowledge.create(
        knowledge_id="knowledge-1",
        candidate_id="candidate-1",
        project_id="project-1",
        claim="STM32 component observed",
        evidence_refs=("vision:obs001",),
        approved_by="reviewer-1",
    )
    assert sink.publish(value) == value
    assert sink.publish(value) == value
    conflicting = ApprovedKnowledge.create(
        **{
            **value.model_dump(mode="python"),
            "claim": "UART interface detected",
        }
    )
    with pytest.raises(GovernanceConflict):
        sink.publish(conflicting)
