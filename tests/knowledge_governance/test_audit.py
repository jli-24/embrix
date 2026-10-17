from __future__ import annotations

from datetime import UTC, datetime

import pytest

from embedded_copilot.knowledge_governance import (
    GovernanceRejected,
    InMemoryReviewStore,
    ReviewService,
)
from tests.knowledge_governance.test_review import NOW, _answer, _snapshot


def test_evidence_audit_is_bound_to_review_and_context_provenance() -> None:
    service = ReviewService(store=InMemoryReviewStore())
    review = service.create_review(_answer(), _snapshot(), review_id="review-audit", created_at=NOW)
    audit = service.get_audits(review.review_id)
    assert len(audit) == 1
    assert audit[0].source_reference == "memory:1"
    assert audit[0].verification_status == "APPROVED"
    assert audit[0].fingerprint.startswith("sha256:")


def test_mismatched_answer_evidence_fails_closed() -> None:
    service = ReviewService(store=InMemoryReviewStore())
    answer = _answer().model_copy(update={"evidence_refs": ("unknown-evidence",)})
    with pytest.raises(GovernanceRejected):
        service.create_review(answer, _snapshot(), review_id="review-bad", created_at=datetime(2026, 8, 8, 12, 0, tzinfo=UTC))
