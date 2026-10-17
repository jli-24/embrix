from __future__ import annotations

import inspect

import pytest

from embedded_copilot.engineering_repair import (
    ApprovalRejected,
    ArtifactNotFound,
    AuditWriteFailed,
    RepairResultStatus,
    RepairReviewDecision,
    RepairReviewDecisionType,
    RepairService,
    RepairVerificationStatus,
    ReviewNotFound,
    TargetDrift,
)
from embedded_copilot.engineering_repair.adapters import (
    FakeArtifactSourceAdapter,
)

from ._factories import (
    FP_B,
    NOW,
    complete_to_apply,
    environment,
    record_approval,
    request,
    state,
)


@pytest.mark.parametrize(
    ("verification_status", "result_status"),
    (
        (RepairVerificationStatus.PASS, RepairResultStatus.VERIFIED),
        (RepairVerificationStatus.FAIL, RepairResultStatus.FAILED),
        (RepairVerificationStatus.ESCALATE, RepairResultStatus.ESCALATED),
    ),
)
def test_complete_offline_workflow_maps_verification_status(
    verification_status: RepairVerificationStatus,
    result_status: RepairResultStatus,
) -> None:
    service, source, artifact, _artifacts, reviews, audit = environment(
        verification_status=verification_status
    )
    preparation, _review, _apply_request, receipt = complete_to_apply(
        service, source, artifact, reviews
    )
    result = service.verify(preparation, receipt)
    assert result.status is result_status
    assert result.patch_reference == preparation.patch.as_reference()
    assert len(audit.get_events("project-1")) == 6


def test_prepare_binds_existing_failure_proposal_target_and_evidence() -> None:
    service, source, artifact, _artifacts, _reviews, _audit = environment()
    preparation = service.prepare(request(source, artifact))
    assert preparation.plan.analysis_reference.failure_fingerprint == source.fingerprint
    assert preparation.plan.existing_proposal_reference.failure_id == source.failure_id
    assert (
        preparation.plan.existing_proposal.fingerprint
        == preparation.plan.existing_proposal_reference.proposal_fingerprint
    )
    assert preparation.patch.before_state == artifact
    assert preparation.patch.evidence_refs == preparation.request.evidence_refs


def test_missing_target_is_rejected() -> None:
    service, source, artifact, _artifacts, _reviews, _audit = environment()
    service._artifact_source = FakeArtifactSourceAdapter()
    with pytest.raises(ArtifactNotFound):
        service.prepare(request(source, artifact))


def test_target_drift_during_review_is_rejected() -> None:
    service, source, artifact, artifacts, reviews, _audit = environment()
    preparation = service.prepare(request(source, artifact))
    review = service.submit_review(preparation)
    record_approval(reviews, review)
    artifacts.put_state(state(content=FP_B, revision="revision-drift"))
    with pytest.raises(TargetDrift):
        service.authorize_apply(preparation.patch, review.review_id)


@pytest.mark.parametrize(
    "decision_type",
    (
        RepairReviewDecisionType.REJECT,
        RepairReviewDecisionType.REQUEST_CHANGE,
    ),
)
def test_non_approve_human_decisions_cannot_authorize(
    decision_type: RepairReviewDecisionType,
) -> None:
    service, source, artifact, _artifacts, reviews, _audit = environment()
    preparation = service.prepare(request(source, artifact))
    review = service.submit_review(preparation)
    reviews.record_human_decision(
        RepairReviewDecision.create(
            decision_id="decision-non-approve",
            review_id=review.review_id,
            project_id=review.project_id,
            patch_fingerprint=review.patch_reference.patch_fingerprint,
            decision=decision_type,
            reviewer="reviewer-1",
            comment="Repair needs additional human work",
            decided_at=NOW,
        )
    )
    with pytest.raises(ApprovalRejected):
        service.authorize_apply(preparation.patch, review.review_id)


def test_missing_human_decision_cannot_authorize() -> None:
    service, source, artifact, _artifacts, _reviews, _audit = environment()
    preparation = service.prepare(request(source, artifact))
    review = service.submit_review(preparation)
    with pytest.raises(ReviewNotFound):
        service.authorize_apply(preparation.patch, review.review_id)


def test_fake_apply_does_not_change_artifact_source() -> None:
    service, source, artifact, artifacts, reviews, _audit = environment()
    preparation, _review, _apply_request, receipt = complete_to_apply(
        service, source, artifact, reviews
    )
    assert receipt.status.value == "ACCEPTED"
    assert artifacts.get_state("project-1", "artifact-1") == artifact
    assert preparation.patch.after_state != artifact


def test_identical_phase_retries_are_deterministic_and_idempotent() -> None:
    service, source, artifact, _artifacts, reviews, audit = environment()
    first = service.prepare(request(source, artifact))
    second = service.prepare(request(source, artifact))
    assert first == second
    review_1 = service.submit_review(first)
    review_2 = service.submit_review(second)
    assert review_1 == review_2
    record_approval(reviews, review_1)
    apply_1 = service.authorize_apply(first.patch, review_1.review_id)
    apply_2 = service.authorize_apply(first.patch, review_1.review_id)
    assert apply_1 == apply_2
    receipt_1 = service.apply(first.patch, apply_1)
    receipt_2 = service.apply(first.patch, apply_2)
    assert receipt_1 == receipt_2
    assert len(audit.get_events("project-1")) == 4


def test_partial_audit_failure_can_be_completed_by_identical_retry() -> None:
    service, source, artifact, _artifacts, _reviews, audit = environment()
    audit.fail_next_append = True
    with pytest.raises(AuditWriteFailed):
        service.prepare(request(source, artifact))
    recovered = service.prepare(request(source, artifact))
    assert recovered.request.request_id == "repair-request-1"
    assert len(audit.get_events("project-1")) == 1


def test_service_has_no_human_decision_api() -> None:
    public = {
        name
        for name, value in inspect.getmembers(RepairService, inspect.isfunction)
        if not name.startswith("_")
    }
    assert public == {
        "apply",
        "authorize_apply",
        "prepare",
        "submit_review",
        "verify",
    }
    assert "approve" not in public
    assert "decide" not in public


def test_apply_rejects_cross_patch_request() -> None:
    service, source, artifact, _artifacts, reviews, _audit = environment()
    preparation = service.prepare(request(source, artifact))
    review = service.submit_review(preparation)
    record_approval(reviews, review)
    apply_request = service.authorize_apply(preparation.patch, review.review_id)
    other = type(preparation.patch).create(
        **{
            **preparation.patch.model_dump(
                mode="python", exclude={"fingerprint", "patch_id"}
            ),
            "patch_id": "patch-other",
        }
    )
    with pytest.raises(ApprovalRejected):
        service.apply(other, apply_request)
