from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from embedded_copilot.engineering_context import (
    ContextSourceType,
    EngineeringContextEvidence,
    EngineeringContextSnapshot,
)
from embedded_copilot.engineering_qa import EngineeringAnswer, ModelInfo
from embedded_copilot.knowledge_governance import (
    ApprovalRecord,
    GovernanceConflictError,
    InMemoryReviewStore,
    ReviewDecision,
    ReviewService,
    ReviewStatus,
)

NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


def _snapshot() -> EngineeringContextSnapshot:
    evidence = EngineeringContextEvidence.create(
        evidence_id="evidence-1", project_id="project-1", source_type=ContextSourceType.ENGINEERING_MEMORY,
        subject="GPIO12", summary="GPIO12 is reserved", fact_key="gpio12.function", fact_value="reserved",
        source_reference="memory:1", source_fingerprint="sha256:" + "a" * 64,
        confidence=0.8, verification_status="APPROVED",
    )
    return EngineeringContextSnapshot.create(
        project_id="project-1", task_description="GPIO12", memory_evidence=(evidence,), graph_evidence=(),
        knowledge_evidence=(), multimodal_evidence=(), conflicts=(), confidence=0.8,
        provenance=(evidence.source(),), verification_status="APPROVED",
    )


def _answer() -> EngineeringAnswer:
    return EngineeringAnswer(
        question_id="question-1", content="GPIO12 must remain reserved.", confidence=0.8,
        evidence_refs=("evidence-1",), model_info=ModelInfo(provider="local", model_name="offline", finish_reason="STOP"),
    )


def test_human_review_lifecycle_returns_handoff_without_memory_write() -> None:
    service = ReviewService(store=InMemoryReviewStore())
    task = service.create_review(_answer(), _snapshot(), review_id="review-1", created_at=NOW)
    assert task.status is ReviewStatus.PENDING
    approval = ApprovalRecord.create(
        approval_id="approval-1", review_id=task.review_id, reviewer_identity="reviewer-1",
        decision=ReviewDecision.APPROVED, reason="Evidence reviewed.", timestamp=NOW + timedelta(seconds=1),
        input_fingerprint=task.fingerprint,
    )
    result = service.submit_decision(approval)
    assert result.review.status is ReviewStatus.APPROVED
    assert result.approved_candidate is not None
    assert "content" not in type(result.approved_candidate).model_fields
    assert result.approved_candidate.content_fingerprint == task.content_fingerprint
    assert service.get_review("review-1") == result.review
    with pytest.raises(GovernanceConflictError):
        service.submit_decision(approval)


@pytest.mark.parametrize(
    ("decision", "status"),
    ((ReviewDecision.REJECTED, ReviewStatus.REJECTED), (ReviewDecision.NEEDS_CHANGE, ReviewStatus.NEEDS_CHANGE)),
)
def test_nonapproval_decisions_do_not_create_candidate(decision: ReviewDecision, status: ReviewStatus) -> None:
    service = ReviewService(store=InMemoryReviewStore())
    task = service.create_review(_answer(), _snapshot(), review_id=f"review-{decision.value.lower()}", created_at=NOW)
    result = service.submit_decision(ApprovalRecord.create(
        approval_id=f"approval-{decision.value.lower()}", review_id=task.review_id, reviewer_identity="reviewer-1",
        decision=decision, reason="Human decision.", timestamp=NOW, input_fingerprint=task.fingerprint,
    ))
    assert result.review.status is status
    assert result.approved_candidate is None
