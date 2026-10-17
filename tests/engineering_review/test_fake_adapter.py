from __future__ import annotations

from datetime import UTC, datetime

import pytest

from embedded_copilot.engineering_review import (
    HumanDecision,
    ReviewConflict,
    ReviewDecision,
    ReviewRecord,
    ReviewRequest,
    ReviewTargetReference,
    ReviewTargetType,
    ReviewUnavailable,
)
from embedded_copilot.engineering_review.adapters import (
    FakeDecisionAuditSinkAdapter,
    FakeReviewStoreAdapter,
)

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def review_request(**overrides: object) -> ReviewRequest:
    values: dict[str, object] = {
        "review_id": "review-1",
        "project_id": "project-1",
        "target_reference": ReviewTargetReference.create(
            target_type=ReviewTargetType.OPTIMIZATION,
            target_id="proposal-1",
            target_fingerprint="sha256:" + "a" * 64,
        ),
        "requester": "engineer-1",
        "created_at": NOW,
    }
    values.update(overrides)
    return ReviewRequest.create(**values)


def decision(**overrides: object) -> HumanDecision:
    values: dict[str, object] = {
        "decision_id": "decision-1",
        "review_id": "review-1",
        "project_id": "project-1",
        "decision": ReviewDecision.APPROVE,
        "reviewer": "fake-user",
        "comment": "approved for next engineering step",
        "decided_at": NOW,
    }
    values.update(overrides)
    return HumanDecision.create(**values)


def test_store_save_get_idempotency_and_deep_copy_isolation() -> None:
    store = FakeReviewStoreAdapter()
    request = review_request()
    saved = store.save_review_request(request)
    repeated = store.save_review_request(request)

    assert saved == repeated == request
    assert saved is not request
    assert saved.target_reference is not request.target_reference
    loaded = store.get_review_request(request.review_id)
    assert loaded == request
    assert loaded is not saved

    human = decision()
    assert store.save_decision(human) == human
    assert store.save_decision(human) == human
    assert store.get_decision(request.review_id) == human

    record = ReviewRecord.create(review_request=request, decision=human)
    assert store.save_record(record) == record
    assert store.save_record(record) == record
    assert store.get_record(request.review_id) == record


def test_store_rejects_same_identity_with_different_content() -> None:
    store = FakeReviewStoreAdapter()
    store.save_review_request(review_request())
    with pytest.raises(ReviewConflict):
        store.save_review_request(review_request(requester="engineer-other"))

    store.save_decision(decision())
    with pytest.raises(ReviewConflict):
        store.save_decision(
            decision(
                decision_id="decision-other",
                decision=ReviewDecision.REJECT,
                comment="Rejected by fake reviewer.",
            )
        )


def test_store_rejects_unbound_decision_and_record() -> None:
    store = FakeReviewStoreAdapter()
    human = decision()
    with pytest.raises(ReviewConflict):
        store.save_decision(human)

    request = review_request()
    store.save_review_request(request)
    store.save_decision(human)
    other = decision(
        decision_id="decision-other",
        decision=ReviewDecision.REJECT,
        comment="Rejected by fake reviewer.",
    )
    with pytest.raises(ReviewConflict):
        store.save_record(ReviewRecord.create(review_request=request, decision=other))


def test_audit_sink_is_idempotent_conflict_aware_and_retryable() -> None:
    request = review_request()
    approved = decision()
    record = ReviewRecord.create(review_request=request, decision=approved)
    sink = FakeDecisionAuditSinkAdapter(failures=1)

    with pytest.raises(ReviewUnavailable):
        sink.record_decision(record)
    assert sink.get_record(request.review_id) is None

    first = sink.record_decision(record)
    second = sink.record_decision(record)
    assert first == second == record
    assert first is not record

    rejected = decision(
        decision_id="decision-other",
        decision=ReviewDecision.REJECT,
        comment="Rejected by fake reviewer.",
    )
    with pytest.raises(ReviewConflict):
        sink.record_decision(
            ReviewRecord.create(review_request=request, decision=rejected)
        )
