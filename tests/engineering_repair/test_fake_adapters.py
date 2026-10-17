from __future__ import annotations

import pytest

from embedded_copilot.engineering_repair import (
    RepairConflict,
    RepairEvent,
    RepairEventType,
    RepairReviewDecision,
    RepairReviewDecisionType,
    RepairUnavailable,
)
from embedded_copilot.engineering_repair.adapters import (
    FakeRepairAuditSinkAdapter,
    FakeRepairReviewAdapter,
)

from ._factories import NOW, environment, evidence, request


def test_audit_sink_is_append_only_idempotent_and_deep_copied() -> None:
    sink = FakeRepairAuditSinkAdapter()
    event = RepairEvent.create(
        event_id="event-1",
        event_type=RepairEventType.PREPARED,
        project_id="project-1",
        subject_id="request-1",
        subject_fingerprint=request().fingerprint,
        actor="engineer-1",
        occurred_at=NOW,
        evidence_refs=(evidence(),),
    )
    first = sink.append(event)
    second = sink.append(event)
    assert first == second
    assert first is not second
    assert sink.get_events("project-1") == (event,)
    assert not hasattr(sink, "update")
    assert not hasattr(sink, "delete")
    assert not hasattr(sink, "overwrite")


def test_audit_sink_rejects_same_event_identity_with_different_content() -> None:
    sink = FakeRepairAuditSinkAdapter()
    first = RepairEvent.create(
        event_id="event-1",
        event_type=RepairEventType.PREPARED,
        project_id="project-1",
        subject_id="request-1",
        subject_fingerprint=request().fingerprint,
        actor="engineer-1",
        occurred_at=NOW,
        evidence_refs=(evidence(),),
    )
    second = RepairEvent.create(
        **{
            **first.model_dump(
                mode="python", exclude={"fingerprint", "subject_id"}
            ),
            "subject_id": "request-2",
        }
    )
    sink.append(first)
    with pytest.raises(RepairConflict):
        sink.append(second)


def test_review_adapter_rejects_different_second_decision() -> None:
    service, source, artifact, _artifacts, reviews, _audit = environment()
    preparation = service.prepare(request(source, artifact))
    review = service.submit_review(preparation)
    first = RepairReviewDecision.create(
        decision_id="decision-1",
        review_id=review.review_id,
        project_id=review.project_id,
        patch_fingerprint=review.patch_reference.patch_fingerprint,
        decision=RepairReviewDecisionType.APPROVE,
        reviewer="reviewer-1",
        comment="Approved",
        decided_at=NOW,
    )
    second = RepairReviewDecision.create(
        decision_id="decision-2",
        review_id=review.review_id,
        project_id=review.project_id,
        patch_fingerprint=review.patch_reference.patch_fingerprint,
        decision=RepairReviewDecisionType.REJECT,
        reviewer="reviewer-2",
        comment="Rejected",
        decided_at=NOW,
    )
    reviews.record_human_decision(first)
    with pytest.raises(RepairConflict):
        reviews.record_human_decision(second)


def test_review_adapter_requires_submitted_request() -> None:
    adapter = FakeRepairReviewAdapter()
    decision = RepairReviewDecision.create(
        decision_id="decision-1",
        review_id="review-missing",
        project_id="project-1",
        patch_fingerprint="sha256:" + "c" * 64,
        decision=RepairReviewDecisionType.APPROVE,
        reviewer="reviewer-1",
        comment="Approved",
        decided_at=NOW,
    )
    with pytest.raises(RepairUnavailable):
        adapter.record_human_decision(decision)
