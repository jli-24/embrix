from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.knowledge_governance import (
    ApprovalRecord,
    ReviewDecision,
    ReviewStatus,
    ReviewTask,
)

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
NAIVE_NOW = NOW.replace(tzinfo=None)


def _review() -> ReviewTask:
    return ReviewTask.create(
        review_id="review-1", project_id="project-1", source_type="ENGINEERING_ANSWER",
        source_id="answer-1", content_fingerprint="sha256:" + "a" * 64,
        evidence_refs=("evidence-1",), status=ReviewStatus.PENDING, created_at=NOW,
    )


def test_governance_contracts_are_frozen_strict_tuple_only_and_fingerprinted() -> None:
    review = _review()
    assert review.fingerprint.startswith("sha256:")
    assert "content" not in ReviewTask.model_fields
    with pytest.raises((TypeError, ValidationError)):
        review.status = ReviewStatus.APPROVED
    with pytest.raises(ValidationError):
        ReviewTask.model_validate({**review.model_dump(), "source_id": "answer-2"})
    with pytest.raises((TypeError, ValidationError)):
        ReviewTask.create(
            review_id="review-2", project_id="project-1", source_type="ENGINEERING_ANSWER",
            source_id="answer-1", content_fingerprint="sha256:" + "a" * 64,
            evidence_refs=["evidence-1"], status=ReviewStatus.PENDING, created_at=NOW,
        )


def test_approval_record_binds_review_fingerprint_and_utc_timestamp() -> None:
    approval = ApprovalRecord.create(
        approval_id="approval-1", review_id="review-1", reviewer_identity="reviewer-1",
        decision=ReviewDecision.APPROVED, reason="Evidence reviewed.", timestamp=NOW,
        input_fingerprint=_review().fingerprint,
    )
    assert approval.decision is ReviewDecision.APPROVED
    with pytest.raises((ValueError, ValidationError)):
        ApprovalRecord.create(
            approval_id="approval-2", review_id="review-1", reviewer_identity="reviewer-1",
            decision=ReviewDecision.APPROVED, reason="Evidence reviewed.",
            timestamp=NAIVE_NOW, input_fingerprint=_review().fingerprint,
        )
