from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_evolution import (
    EvolutionApplicationRecord,
    EvolutionApplyRequest,
    EvolutionProposalRequest,
    EvolutionProposalStatus,
    EvolutionReviewDecision,
    EvolutionReviewRequest,
    EvolutionSinkReceipt,
    EvolutionTarget,
    KnowledgeEvolutionProposal,
)

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def proposal_request(**overrides: object) -> EvolutionProposalRequest:
    values = {
        "request_id": "evolution-request-1",
        "project_id": "project-1",
        "knowledge_id": "knowledge-1",
        "knowledge_fingerprint": "sha256:" + "a" * 64,
        "target": EvolutionTarget.MEMORY,
        "created_by": "engineer-1",
        "created_at": NOW,
    }
    values.update(overrides)
    return EvolutionProposalRequest.create(**values)


def proposal(**overrides: object) -> KnowledgeEvolutionProposal:
    values = {
        "proposal_id": "evolution-proposal-1",
        "request_id": "evolution-request-1",
        "project_id": "project-1",
        "source_knowledge_id": "knowledge-1",
        "source_knowledge_fingerprint": "sha256:" + "a" * 64,
        "source_candidate_id": "candidate-1",
        "source_approved_by": "reviewer-1",
        "target": EvolutionTarget.MEMORY,
        "claim": "GPIO12 is reserved",
        "evidence_refs": ("context:evidence-1",),
        "created_by": "engineer-1",
        "created_at": NOW,
        "status": EvolutionProposalStatus.CREATED,
    }
    values.update(overrides)
    return KnowledgeEvolutionProposal.create(**values)


def approved_review(value: KnowledgeEvolutionProposal) -> EvolutionReviewRequest:
    return EvolutionReviewRequest.create(
        review_id="evolution-review-1",
        project_id=value.project_id,
        proposal_id=value.proposal_id,
        proposal_fingerprint=value.fingerprint,
        reviewer="reviewer-2",
        decision=EvolutionReviewDecision.APPROVE,
        comment="Approved for controlled handoff.",
        reviewed_at=NOW + timedelta(minutes=1),
    )


def apply_request(
    value: KnowledgeEvolutionProposal, review: EvolutionReviewRequest
) -> EvolutionApplyRequest:
    return EvolutionApplyRequest.create(
        application_id="evolution-application-1",
        project_id=value.project_id,
        proposal_id=value.proposal_id,
        proposal_fingerprint=value.fingerprint,
        review_fingerprint=review.fingerprint,
        applied_by="operator-1",
        applied_at=NOW + timedelta(minutes=2),
    )


def test_contracts_are_strict_frozen_nfc_tuple_only_and_fingerprinted() -> None:
    request = proposal_request()
    value = proposal(claim="Cafe\u0301 component observed")
    assert value.claim == "Caf\u00e9 component observed"
    assert request.fingerprint.startswith("sha256:")
    with pytest.raises((TypeError, ValidationError)):
        request.project_id = "changed"
    with pytest.raises(ValidationError):
        EvolutionProposalRequest.model_validate(
            {**request.model_dump(mode="python"), "extra": "forbidden"}
        )
    with pytest.raises((TypeError, ValidationError)):
        proposal(evidence_refs=["context:evidence-1"])


@pytest.mark.parametrize("target", tuple(EvolutionTarget))
def test_proposal_supports_all_targets_and_has_stable_content_identity(
    target: EvolutionTarget,
) -> None:
    created = proposal(target=target)
    approved = created.with_status(EvolutionProposalStatus.APPROVED)
    assert approved.status is EvolutionProposalStatus.APPROVED
    assert approved.fingerprint == created.fingerprint

    with pytest.raises(ValidationError):
        KnowledgeEvolutionProposal.model_validate(
            {**created.model_dump(mode="python"), "claim": "changed claim"}
        )


def test_review_apply_and_receipt_bind_exact_proposal_and_approval() -> None:
    value = proposal()
    review = approved_review(value)
    application = apply_request(value, review)
    receipt = EvolutionSinkReceipt.create(
        receipt_id="evolution-receipt-1",
        application_id=application.application_id,
        project_id=value.project_id,
        proposal_id=value.proposal_id,
        proposal_fingerprint=value.fingerprint,
        review_fingerprint=review.fingerprint,
        target=value.target,
        accepted_at=application.applied_at,
    )
    record = EvolutionApplicationRecord.create(
        request=application,
        receipt=receipt,
    )
    assert record.request.review_fingerprint == review.fingerprint
    assert record.receipt.target is EvolutionTarget.MEMORY
    assert record.fingerprint.startswith("sha256:")

    with pytest.raises((ValueError, ValidationError)):
        EvolutionApplicationRecord.create(
            request=application,
            receipt=EvolutionSinkReceipt.create(
                **{
                    **receipt.model_dump(mode="python"),
                    "proposal_id": "evolution-proposal-other",
                }
            ),
        )


def test_contracts_reject_non_utc_time_and_sensitive_text() -> None:
    with pytest.raises((ValueError, ValidationError)):
        proposal_request(created_at=NOW.replace(tzinfo=None))
    with pytest.raises((ValueError, ValidationError)):
        proposal(claim="api_key=secret")
