from __future__ import annotations

from datetime import UTC, datetime, timedelta

from embedded_copilot.engineering_review.adapters.sqlite import SQLiteReviewStore
from embedded_copilot.engineering_review.models import (
    HumanDecision,
    ReviewDecision,
    ReviewRequest,
    ReviewTargetReference,
    ReviewTargetType,
)


FP = "sha256:" + "a" * 64


def _request() -> ReviewRequest:
    return ReviewRequest.create(
        review_id="review-1",
        project_id="project-1",
        target_reference=ReviewTargetReference.create(
            target_type=ReviewTargetType.KNOWLEDGE,
            target_id="knowledge-1",
            target_fingerprint=FP,
        ),
        requester="engineer-1",
        created_at=datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
    )


def test_sqlite_review_store_round_trips_and_is_tenant_bound(tmp_path) -> None:
    database = tmp_path / "reviews.sqlite3"
    request = _request()
    tenant_a = SQLiteReviewStore(database, tenant_id="tenant-a")
    tenant_b = SQLiteReviewStore(database, tenant_id="tenant-b")

    tenant_a.save_review_request(request)

    assert tenant_a.get_review_request("review-1") == request
    assert tenant_b.get_review_request("review-1") is None


def test_sqlite_review_store_persists_decision_and_rejects_conflict(tmp_path) -> None:
    store = SQLiteReviewStore(tmp_path / "reviews.sqlite3", tenant_id="tenant-a")
    request = _request()
    store.save_review_request(request)
    decision = HumanDecision.create(
        decision_id="decision-1",
        review_id="review-1",
        project_id="project-1",
        decision=ReviewDecision.APPROVE,
        reviewer="reviewer-1",
        comment="Evidence reviewed.",
        decided_at=request.created_at + timedelta(minutes=1),
    )

    store.save_decision(decision)

    assert store.get_decision("review-1") == decision
