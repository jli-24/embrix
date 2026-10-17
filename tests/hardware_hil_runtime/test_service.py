from datetime import timedelta

import pytest

from embedded_copilot.hardware_hil_runtime import (
    HardwareApprovalDecision,
    HardwareApprovalRecord,
    HardwareApprovalScope,
    HardwareHILRuntimeService,
    HardwareObservationRequest,
    HILApprovalRequired,
    HILExecutionStatus,
)
from embedded_copilot.hardware_hil_runtime.adapters import (
    FakeApprovalPort,
    FakeAuditPort,
    FakeContextProjectionPort,
    FakeExecutionPort,
    FakeFirmwarePort,
    FakeMeasurementPort,
    FakeMemoryCandidatePort,
    FakeObservationSink,
    FakeProposalSource,
    FakeTargetPort,
    FixedClock,
)

from .test_contracts import NOW, proposal_request


def build_service(*, approved: bool = True):
    source_request = proposal_request()
    clock = FixedClock(NOW)
    proposal_source = FakeProposalSource((source_request.source_proposal,))
    target_port = FakeTargetPort((source_request.target,))
    firmware_port = FakeFirmwarePort((source_request.firmware,))
    scope = HardwareApprovalScope(
        project_id="project-1",
        target_fingerprint=source_request.target.fingerprint,
        firmware_fingerprint=source_request.firmware.firmware_fingerprint,
        fixture_fingerprint=source_request.fixture.fingerprint,
        test_type=source_request.test_type,
        observation_types=source_request.expected_observations,
        expires_at=NOW + timedelta(hours=1),
    )
    record = HardwareApprovalRecord(
        approval_id="hil-approval-placeholder",
        proposal_id="placeholder",
        project_id="project-1",
        requested_reviewer="reviewer-1",
        reviewer="reviewer-1",
        decision=HardwareApprovalDecision.APPROVE
        if approved
        else HardwareApprovalDecision.REJECT,
        approved_at=NOW,
        scope=scope,
    )
    approvals = FakeApprovalPort((record,))
    return (
        HardwareHILRuntimeService(
            targets=target_port,
            firmware=firmware_port,
            proposals=proposal_source,
            approvals=approvals,
            execution=FakeExecutionPort(clock),
            measurements=FakeMeasurementPort(clock, content="boot observed"),
            observations=FakeObservationSink(),
            context=FakeContextProjectionPort(),
            memory=FakeMemoryCandidatePort(),
            audit=FakeAuditPort(),
            clock=clock,
        ),
        source_request,
        approvals,
        clock,
    )


def test_full_governed_hil_flow():
    service, request, approvals, _ = build_service()
    proposal = service.create_test_proposal(request)
    review = service.submit_review(proposal, "reviewer-1")
    scope = HardwareApprovalScope(
        project_id=proposal.project_id,
        target_fingerprint=proposal.target.fingerprint,
        firmware_fingerprint=proposal.firmware.firmware_fingerprint,
        fixture_fingerprint=proposal.fixture.fingerprint,
        test_type=proposal.test_type,
        observation_types=proposal.expected_observations,
        expires_at=NOW + timedelta(hours=1),
    )
    approvals._records[review.approval_id] = HardwareApprovalRecord(
        approval_id=review.approval_id,
        proposal_id=proposal.proposal_id,
        project_id=proposal.project_id,
        requested_reviewer="reviewer-1",
        reviewer="reviewer-1",
        decision=HardwareApprovalDecision.APPROVE,
        approved_at=NOW,
        scope=scope,
    )
    execution = service.authorize(proposal, review.approval_id)
    receipt = service.execute(execution)
    assert receipt.status is HILExecutionStatus.COMPLETED
    observation = service.collect_observation(
        HardwareObservationRequest(
            request_id="observe-1",
            execution=receipt,
            observation_type=proposal.expected_observations[0],
            requested_at=NOW,
        ),
        proposal,
    )
    report = service.create_report(
        proposal, approvals.get_record(review.approval_id), receipt, (observation,)
    )
    assert report.overlay.status.value == "PROJECTED"
    assert report.memory_candidates[0].review_status == "REVIEW_REQUIRED"


def test_rejected_or_expired_approval_cannot_authorize():
    service, request, _approvals, _ = build_service(approved=False)
    proposal = service.create_test_proposal(request)
    review = service.submit_review(proposal, "reviewer-1")
    with pytest.raises(HILApprovalRequired):
        service.authorize(proposal, review.approval_id)
