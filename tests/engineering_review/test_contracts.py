from __future__ import annotations

import copy
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_review import (
    HumanDecision,
    ReviewDecision,
    ReviewRecord,
    ReviewRequest,
    ReviewStatus,
    ReviewTargetReference,
    ReviewTargetType,
)

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def target_reference(
    target_type: ReviewTargetType = ReviewTargetType.KNOWLEDGE,
) -> ReviewTargetReference:
    target_id = {
        ReviewTargetType.KNOWLEDGE: "knowledge-1",
        ReviewTargetType.VALIDATION: "finding-1",
            ReviewTargetType.OPTIMIZATION: "proposal-optimization-1",
            ReviewTargetType.EVOLUTION: "proposal-evolution-1",
            ReviewTargetType.ENGINEERING_EVIDENCE: "bundle-1",
        }[target_type]
    return ReviewTargetReference.create(
        target_type=target_type,
        target_id=target_id,
        target_fingerprint="sha256:" + "a" * 64,
    )


def review_request(**overrides: object) -> ReviewRequest:
    values: dict[str, object] = {
        "review_id": "review-1",
        "project_id": "project-1",
        "target_reference": target_reference(),
        "requester": "engineer-1",
        "created_at": NOW,
    }
    values.update(overrides)
    return ReviewRequest.create(**values)


def human_decision(**overrides: object) -> HumanDecision:
    values: dict[str, object] = {
        "decision_id": "decision-1",
        "review_id": "review-1",
        "project_id": "project-1",
        "decision": ReviewDecision.APPROVE,
        "reviewer": "reviewer-1",
        "comment": "Approved for the next engineering step.",
        "decided_at": NOW,
    }
    values.update(overrides)
    return HumanDecision.create(**values)


def test_contracts_are_frozen_strict_and_forbid_extra_fields() -> None:
    value = review_request()
    with pytest.raises((TypeError, ValidationError)):
        value.project_id = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ReviewRequest.model_validate(
            {**value.model_dump(mode="python"), "unexpected": "value"}
        )
    with pytest.raises(ValidationError):
        ReviewTargetReference.model_validate(
            {
                **target_reference().model_dump(mode="python"),
                "target_type": "KNOWLEDGE",
            }
        )


@pytest.mark.parametrize("target_type", tuple(ReviewTargetType))
def test_target_reference_binds_all_supported_target_types(
    target_type: ReviewTargetType,
) -> None:
    reference = target_reference(target_type)
    assert reference.target_type is target_type
    assert reference.target_fingerprint.startswith("sha256:")
    assert set(reference.model_dump(mode="python")) == {
        "target_type",
        "target_id",
        "target_fingerprint",
    }


def test_text_is_nfc_normalized_trimmed_and_sensitive_text_is_rejected() -> None:
    decision = human_decision(
        reviewer=" reviewer-1 ", comment="  Cafe\u0301 review completed.  "
    )
    assert decision.reviewer == "reviewer-1"
    assert decision.comment == "Caf\u00e9 review completed."

    with pytest.raises((ValueError, ValidationError)):
        human_decision(comment="access_token=secret")
    with pytest.raises((ValueError, ValidationError)):
        human_decision(comment="hidden reasoning: private")


def test_timestamps_must_be_utc() -> None:
    with pytest.raises((ValueError, ValidationError)):
        review_request(created_at=NOW.replace(tzinfo=None))
    with pytest.raises((ValueError, ValidationError)):
        human_decision(decided_at=NOW.replace(tzinfo=None))


def test_fingerprints_are_deterministic_and_reject_tampering() -> None:
    first = review_request()
    second = review_request()
    assert first == second
    assert first.fingerprint.startswith("sha256:")

    with pytest.raises(ValidationError):
        ReviewRequest.model_validate(
            {**first.model_dump(mode="python"), "fingerprint": "sha256:" + "f" * 64}
        )
    decision = human_decision()
    with pytest.raises(ValidationError):
        HumanDecision.model_validate(
            {
                **decision.model_dump(mode="python"),
                "fingerprint": "sha256:" + "f" * 64,
            }
        )


def test_nested_contracts_are_revalidated_and_deep_copied() -> None:
    reference = target_reference()
    request = review_request(target_reference=reference)
    decision = human_decision()
    record = ReviewRecord.create(review_request=request, decision=decision)

    assert request.target_reference == reference
    assert request.target_reference is not reference
    assert record.review_request == request
    assert record.review_request is not request
    assert record.decision == decision
    assert record.decision is not decision
    assert copy.deepcopy(record) == record


def test_record_binds_request_decision_status_and_complete_fingerprint() -> None:
    request = review_request()
    decision = human_decision()
    record = ReviewRecord.create(review_request=request, decision=decision)
    repeated = ReviewRecord.create(review_request=request, decision=decision)

    assert record == repeated
    assert record.status is ReviewStatus.DECIDED
    assert record.fingerprint.startswith("sha256:")
    with pytest.raises((ValueError, ValidationError)):
        ReviewRecord.create(
            review_request=request,
            decision=human_decision(project_id="project-other"),
        )
    with pytest.raises(ValidationError):
        ReviewRecord.model_validate(
            {**record.model_dump(mode="python"), "status": ReviewStatus.CLOSED}
        )
