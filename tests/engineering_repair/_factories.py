from __future__ import annotations

from datetime import UTC, datetime

from embedded_copilot.engineering_repair import (
    ArtifactStateReference,
    EvidenceReference,
    FailureAnalysisReference,
    RepairRequest,
    RepairReviewDecision,
    RepairReviewDecisionType,
    RepairService,
)
from embedded_copilot.engineering_repair.adapters import (
    FakeApplyAdapter,
    FakeArtifactSourceAdapter,
    FakePatchGenerationAdapter,
    FakeRepairAnalysisAdapter,
    FakeRepairAuditSinkAdapter,
    FakeRepairBuildAdapter,
    FakeRepairProposalAdapter,
    FakeRepairReviewAdapter,
    FakeVerificationAdapter,
    FixedRepairClock,
)
from embedded_copilot.failure_analysis import FailureAnalysis, FailureCategory

NOW = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
FP_A = "sha256:" + "a" * 64
FP_B = "sha256:" + "b" * 64


def analysis() -> FailureAnalysis:
    return FailureAnalysis.create(
        failure_id="failure-1",
        project_id="project-1",
        target_id="artifact-1",
        category=FailureCategory.BUILD_FAILED,
        observation_fingerprint=FP_A,
        context_fingerprint=FP_B,
        root_cause_candidate="Build evidence requires a controlled repair.",
        evidence_refs=("evidence-1",),
        confidence=0.85,
        provenance=(),
        verification_status="VERIFIED",
    )


def state(*, content: str = FP_A, revision: str = "revision-1") -> ArtifactStateReference:
    return ArtifactStateReference.create(
        project_id="project-1",
        artifact_id="artifact-1",
        revision_id=revision,
        content_fingerprint=content,
    )


def evidence(value: FailureAnalysis | None = None) -> EvidenceReference:
    source = value or analysis()
    return EvidenceReference.create(
        evidence_id="evidence-1",
        source="failure-analysis",
        source_fingerprint=source.fingerprint,
    )


def request(
    value: FailureAnalysis | None = None,
    target: ArtifactStateReference | None = None,
) -> RepairRequest:
    source = value or analysis()
    artifact = target or state()
    return RepairRequest.create(
        request_id="repair-request-1",
        project_id="project-1",
        failure_reference=FailureAnalysisReference.create(
            failure_id=source.failure_id,
            project_id=source.project_id,
            target_id=source.target_id,
            failure_fingerprint=source.fingerprint,
        ),
        target_reference=artifact,
        evidence_refs=(evidence(source),),
        requester="engineer-1",
        reason="Repair the observed build failure under human control",
        requested_at=NOW,
    )


def environment(*, verification_status=None):
    source = analysis()
    artifact = state()
    artifacts = FakeArtifactSourceAdapter((artifact,))
    reviews = FakeRepairReviewAdapter()
    audit = FakeRepairAuditSinkAdapter()
    verification = (
        FakeVerificationAdapter()
        if verification_status is None
        else FakeVerificationAdapter(status=verification_status)
    )
    service = RepairService(
        analysis_port=FakeRepairAnalysisAdapter((source,)),
        proposal_port=FakeRepairProposalAdapter(),
        patch_port=FakePatchGenerationAdapter(),
        artifact_source=artifacts,
        repair_review_port=reviews,
        apply_port=FakeApplyAdapter(),
        build_port=FakeRepairBuildAdapter(),
        verification_port=verification,
        audit_sink=audit,
        clock=FixedRepairClock(NOW),
    )
    return service, source, artifact, artifacts, reviews, audit


def record_approval(reviews: FakeRepairReviewAdapter, review_request):
    decision = RepairReviewDecision.create(
        decision_id="repair-decision-1",
        review_id=review_request.review_id,
        project_id=review_request.project_id,
        patch_fingerprint=review_request.patch_reference.patch_fingerprint,
        decision=RepairReviewDecisionType.APPROVE,
        reviewer="reviewer-1",
        comment="Approved for the isolated fake repair boundary",
        decided_at=NOW,
    )
    return reviews.record_human_decision(decision)


def complete_to_apply(service, source, artifact, reviews):
    preparation = service.prepare(request(source, artifact))
    review_request = service.submit_review(preparation)
    record_approval(reviews, review_request)
    apply_request = service.authorize_apply(preparation.patch, review_request.review_id)
    receipt = service.apply(preparation.patch, apply_request)
    return preparation, review_request, apply_request, receipt
