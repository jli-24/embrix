from datetime import UTC, datetime

import pytest

from embedded_copilot.engineering_eda import (
    EDAApprovalRequired,
    EDAArtifactReference,
    EDAArtifactType,
    EDAChangeSet,
    EDAEvidenceReference,
    EDAProposalType,
    EDAReviewDecisionType,
    EDARisk,
    EDASide,
    EngineeringEDAService,
    FakeEDAApplyPort,
    FakeEDAArtifactSource,
    FakeEDAAuditSink,
    FakeEDAChangeSetSource,
    FakeEDAProposalPort,
    FakeEDAReviewPort,
    FakeEDARevisionWorkspace,
    FakeEDAValidationPort,
    FixedEDAClock,
    MoveFootprintChange,
    PCBDesignProposal,
)

NOW = datetime(2026, 8, 10, tzinfo=UTC)
FP = "sha256:" + "1" * 64


def artifact() -> EDAArtifactReference:
    return EDAArtifactReference(
        project_id="project-1",
        artifact_id="board-1",
        artifact_type=EDAArtifactType.BOARD,
        revision="r1",
        format_version="20240108",
        basename="main.kicad_pcb",
        byte_size=1024,
        source_reference="artifact://board-1",
        content_fingerprint=FP,
        captured_at=NOW,
    )


def proposal() -> PCBDesignProposal:
    evidence = EDAEvidenceReference(
        evidence_id="evidence-1",
        project_id="project-1",
        source_id="finding-1",
        source_fingerprint=FP,
        locator="artifact://board-1/C12",
    )
    return PCBDesignProposal(
        proposal_id="proposal-1",
        project_id="project-1",
        analysis_fingerprint=FP,
        proposal_type=EDAProposalType.LAYOUT_REVIEW,
        target_artifacts=(artifact(),),
        change_intention=("Review C12 placement near the supply pin",),
        risk=EDARisk.MEDIUM,
        evidence=(evidence,),
        created_at=NOW,
    )


def changes(value: PCBDesignProposal) -> EDAChangeSet:
    return EDAChangeSet(
        change_set_id="change-1",
        project_id="project-1",
        proposal_fingerprint=value.fingerprint,
        target_artifact=artifact(),
        before_fingerprint=FP,
        changes=(
            MoveFootprintChange(
                reference="C12",
                x_mm=1.0,
                y_mm=2.0,
                rotation_deg=0.0,
                side=EDASide.FRONT,
            ),
        ),
        authored_by="engineer-1",
        authored_at=NOW,
    )


def service():
    clock = FixedEDAClock(NOW)
    item = proposal()
    change = changes(item)
    reviews = FakeEDAReviewPort(clock)
    source = FakeEDAArtifactSource((artifact(),))
    apply_port = FakeEDAApplyPort(clock)
    runtime = EngineeringEDAService(
        artifacts=source,
        proposals=FakeEDAProposalPort(),
        changes=FakeEDAChangeSetSource((change,)),
        reviews=reviews,
        workspace=FakeEDARevisionWorkspace(),
        apply_port=apply_port,
        validation=FakeEDAValidationPort(clock),
        audit=FakeEDAAuditSink(),
        clock=clock,
    )
    return runtime, reviews, apply_port, item


def test_apply_requires_approved_human_review_and_is_staging_only() -> None:
    runtime, reviews, apply_port, item = service()
    record = runtime.submit_review(item, "change-1")
    with pytest.raises(EDAApprovalRequired):
        runtime.authorize_apply("change-1", record.request.review_id)
    reviews.decide(
        record.request.review_id, EDAReviewDecisionType.APPROVE, "reviewer-1"
    )
    request = runtime.authorize_apply("change-1", record.request.review_id)
    receipt = runtime.apply_to_staging(request)
    assert receipt.status.value == "STAGED"
    assert receipt.after_artifact.revision == request.staging_revision
    assert receipt.after_artifact.content_fingerprint != artifact().content_fingerprint
    assert apply_port.calls == 1
    assert runtime.apply_to_staging(request) == receipt
    assert apply_port.calls == 1


def test_service_exposes_no_decide_or_promote_api() -> None:
    runtime, _, _, _ = service()
    assert not hasattr(runtime, "decide")
    assert not hasattr(runtime, "promote")
