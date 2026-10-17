from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_governance import (
    CandidateStatus,
    KnowledgeCandidate,
    ReviewDecision,
    ReviewRequest,
)

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def candidate(**overrides: object) -> KnowledgeCandidate:
    values = {
        "candidate_id": "candidate-1",
        "project_id": "project-1",
        "claim": "STM32 component observed",
        "source_type": "multimodal",
        "evidence_refs": ("vision:observation-1",),
        "confidence": 0.85,
        "status": CandidateStatus.PENDING,
    }
    values.update(overrides)
    return KnowledgeCandidate.create(**values)


def review(value: KnowledgeCandidate) -> ReviewRequest:
    return ReviewRequest.create(
        review_id="review-1",
        candidate_id=value.candidate_id,
        candidate_fingerprint=value.fingerprint,
        reviewer="reviewer-1",
        decision=ReviewDecision.APPROVE,
        comment="Evidence checked.",
        reviewed_at=NOW,
    )


def test_candidate_is_strict_frozen_nfc_tuple_only_and_stable() -> None:
    value = candidate(claim="Cafe\u0301 component observed")
    assert value.claim == "Café component observed"
    assert value.fingerprint == candidate(claim="Café component observed").fingerprint
    with pytest.raises((TypeError, ValidationError)):
        value.claim = "changed"
    with pytest.raises(ValidationError):
        KnowledgeCandidate.model_validate({**value.model_dump(), "extra": "x"})
    with pytest.raises((TypeError, ValidationError)):
        candidate(evidence_refs=["vision:observation-1"])


@pytest.mark.parametrize(
    "claim",
    (
        "api_key=secret",
        "C:\\private\\firmware.c",
        "PCB design is correct",
        "System is optimized",
        "Automatically fixed",
    ),
)
def test_candidate_rejects_sensitive_or_inferred_claims(claim: str) -> None:
    with pytest.raises((ValueError, ValidationError)):
        candidate(claim=claim)


def test_review_binds_candidate_fingerprint_and_utc_time() -> None:
    value = candidate()
    request = review(value)
    assert request.candidate_fingerprint == value.fingerprint
    assert request.fingerprint.startswith("sha256:")
    with pytest.raises((ValueError, ValidationError)):
        ReviewRequest.create(
            review_id="review-2",
            candidate_id=value.candidate_id,
            candidate_fingerprint=value.fingerprint,
            reviewer="reviewer-1",
            decision="APPROVE",
            comment="Evidence checked.",
            reviewed_at=NOW.replace(tzinfo=None),
        )


def test_status_transition_does_not_change_content_fingerprint() -> None:
    value = candidate()
    approved = value.with_status(CandidateStatus.APPROVED)
    assert approved.status is CandidateStatus.APPROVED
    assert approved.fingerprint == value.fingerprint

    with pytest.raises(ValidationError):
        KnowledgeCandidate.model_validate(
            {**value.model_dump(mode="python"), "claim": "UART interface detected"}
        )


@pytest.mark.parametrize("value", (-0.1, 1.1, float("nan"), 1))
def test_candidate_rejects_invalid_confidence(value: object) -> None:
    with pytest.raises((ValueError, ValidationError)):
        candidate(confidence=value)
