from datetime import timedelta

import pytest

from embedded_copilot.engineering_optimization import (
    EngineeringOptimizationService,
    OptimizationApprovalRequired,
    OptimizationAuditUnavailable,
    OptimizationReviewDecision,
    OptimizationReviewRecord,
    OptimizationRisk,
)
from embedded_copilot.engineering_optimization.adapters import (
    FakeOptimizationAnalysisPort,
    FakeOptimizationAuditPort,
    FakeOptimizationContextProjectionPort,
    FakeOptimizationDecisionPort,
    FakeOptimizationEvidencePort,
    FakeOptimizationMemoryPort,
    FakeOptimizationReviewPort,
    FixedClock,
)

from .test_contracts import NOW, request


def service(records=()):
    clock = FixedClock(NOW)
    value = request()
    reviews = FakeOptimizationReviewPort(records)
    return (
        EngineeringOptimizationService(
            evidence=FakeOptimizationEvidencePort(value.evidence_refs),
            analysis=FakeOptimizationAnalysisPort(clock),
            reviews=reviews,
            decisions=FakeOptimizationDecisionPort(),
            context=FakeOptimizationContextProjectionPort(),
            memory=FakeOptimizationMemoryPort(),
            audit=FakeOptimizationAuditPort(),
            clock=clock,
        ),
        value,
        reviews,
    )


def test_approved_decision_only_projects_and_creates_review_required_candidate():
    runtime, value, reviews = service()
    analysis = runtime.analyze(value)
    proposal = runtime.create_proposal(
        analysis,
        analysis.findings[0].finding_id,
        recommendation="Review lower-power configuration.",
        risk=OptimizationRisk.LOW,
        expected_impact="Potential power reduction.",
    )
    review = runtime.submit_review(proposal, "reviewer-1")
    reviews._records[review.review_id] = OptimizationReviewRecord(
        review_id=review.review_id,
        proposal_id=proposal.proposal_id,
        proposal_fingerprint=proposal.fingerprint,
        project_id=proposal.project_id,
        requested_reviewer="reviewer-1",
        reviewer="reviewer-1",
        decision=OptimizationReviewDecision.APPROVE,
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    decision = runtime.record_decision(proposal, review.review_id)
    overlay = runtime.project_context(proposal, decision)
    candidates = runtime.create_memory_candidate(overlay)
    assert overlay.status == "PROJECTED"
    assert candidates[0].review_status == "REVIEW_REQUIRED"
    assert proposal.analysis_fingerprint == analysis.fingerprint
    assert proposal.provenance_fingerprint == analysis.fingerprint


def test_rejected_review_has_no_decision_reference():
    runtime, value, reviews = service()
    analysis = runtime.analyze(value)
    proposal = runtime.create_proposal(
        analysis,
        analysis.findings[0].finding_id,
        recommendation="Review lower-power configuration.",
        risk=OptimizationRisk.LOW,
        expected_impact="Potential power reduction.",
    )
    review = runtime.submit_review(proposal, "reviewer-1")
    reviews._records[review.review_id] = OptimizationReviewRecord(
        review_id=review.review_id,
        proposal_id=proposal.proposal_id,
        proposal_fingerprint=proposal.fingerprint,
        project_id=proposal.project_id,
        requested_reviewer="reviewer-1",
        reviewer="reviewer-1",
        decision=OptimizationReviewDecision.REJECT,
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    assert runtime.record_decision(proposal, review.review_id) is None


def test_expired_review_is_rejected():
    runtime, value, reviews = service()
    analysis = runtime.analyze(value)
    proposal = runtime.create_proposal(
        analysis,
        analysis.findings[0].finding_id,
        recommendation="Review lower-power configuration.",
        risk=OptimizationRisk.LOW,
        expected_impact="Potential power reduction.",
    )
    review = runtime.submit_review(proposal, "reviewer-1")
    reviews._records[review.review_id] = OptimizationReviewRecord(
        review_id=review.review_id,
        proposal_id=proposal.proposal_id,
        proposal_fingerprint=proposal.fingerprint,
        project_id=proposal.project_id,
        requested_reviewer="reviewer-1",
        reviewer="reviewer-1",
        decision=OptimizationReviewDecision.APPROVE,
        created_at=NOW - timedelta(minutes=1),
        expires_at=NOW - timedelta(seconds=1),
    )
    with pytest.raises(OptimizationApprovalRequired):
        runtime.record_decision(proposal, review.review_id)


def test_audit_failure_is_fail_closed_for_decision_projection_and_memory() -> None:
    runtime, value, reviews = service()
    audit = runtime._audit
    analysis = runtime.analyze(value)
    proposal = runtime.create_proposal(
        analysis,
        analysis.findings[0].finding_id,
        recommendation="Review lower-power configuration.",
        risk=OptimizationRisk.LOW,
        expected_impact="Potential power reduction.",
    )
    review = runtime.submit_review(proposal, "reviewer-1")
    reviews._records[review.review_id] = OptimizationReviewRecord(
        review_id=review.review_id,
        proposal_id=proposal.proposal_id,
        proposal_fingerprint=proposal.fingerprint,
        project_id=proposal.project_id,
        requested_reviewer="reviewer-1",
        reviewer="reviewer-1",
        decision=OptimizationReviewDecision.APPROVE,
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )
    audit.fail = True
    with pytest.raises(OptimizationAuditUnavailable):
        runtime.record_decision(proposal, review.review_id)
    audit.fail = False
    decision = runtime.record_decision(proposal, review.review_id)
    audit.fail = True
    with pytest.raises(OptimizationAuditUnavailable):
        runtime.project_context(proposal, decision)
    audit.fail = False
    overlay = runtime.project_context(proposal, decision)
    audit.fail = True
    with pytest.raises(OptimizationAuditUnavailable):
        runtime.create_memory_candidate(overlay)
