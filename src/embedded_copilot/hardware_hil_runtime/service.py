from __future__ import annotations

import copy

from .audit import append_hil_audit
from .contracts import deterministic_id
from .exceptions import HILApprovalRequired, HILRejected, HILUnavailable
from .models import (
    EngineeringProposalReference,
    HardwareApprovalDecision,
    HardwareApprovalRecord,
    HardwareApprovalRequest,
    HardwareHILContextOverlay,
    HardwareObservation,
    HardwareObservationRequest,
    HardwareTestProposal,
    HardwareTestProposalRequest,
    HILAuditEventType,
    HILExecutionReceipt,
    HILExecutionRequest,
    HILExecutionStatus,
    HILMemoryCandidateReference,
    HILReport,
    HILRuntimeEvidence,
)
from .ports import (
    ClockPort,
    EngineeringProposalSourcePort,
    FirmwareArtifactPort,
    HardwareApprovalPort,
    HardwareAuditPort,
    HardwareTargetPort,
    HILContextProjectionPort,
    HILExecutionPort,
    HILMemoryCandidatePort,
    MeasurementPort,
    ObservationSinkPort,
)


class HardwareHILRuntimeService:
    __slots__ = (
        "_approvals",
        "_audit",
        "_clock",
        "_context",
        "_execution",
        "_firmware",
        "_measure",
        "_memory",
        "_observations",
        "_proposals",
        "_targets",
    )

    def __init__(
        self,
        *,
        targets: HardwareTargetPort,
        firmware: FirmwareArtifactPort,
        proposals: EngineeringProposalSourcePort,
        approvals: HardwareApprovalPort,
        execution: HILExecutionPort,
        measurements: MeasurementPort,
        observations: ObservationSinkPort,
        context: HILContextProjectionPort,
        memory: HILMemoryCandidatePort,
        audit: HardwareAuditPort,
        clock: ClockPort,
    ) -> None:
        ports = (
            targets,
            firmware,
            proposals,
            approvals,
            execution,
            measurements,
            observations,
            context,
            memory,
            audit,
            clock,
        )
        interfaces = (
            HardwareTargetPort,
            FirmwareArtifactPort,
            EngineeringProposalSourcePort,
            HardwareApprovalPort,
            HILExecutionPort,
            MeasurementPort,
            ObservationSinkPort,
            HILContextProjectionPort,
            HILMemoryCandidatePort,
            HardwareAuditPort,
            ClockPort,
        )
        if any(
            not isinstance(value, port)
            for value, port in zip(ports, interfaces, strict=True)
        ):
            raise TypeError("HIL dependency is invalid")
        self._targets, self._firmware, self._proposals, self._approvals = (
            targets,
            firmware,
            proposals,
            approvals,
        )
        self._execution, self._measure, self._observations = (
            execution,
            measurements,
            observations,
        )
        self._context, self._memory, self._audit, self._clock = (
            context,
            memory,
            audit,
            clock,
        )

    @staticmethod
    def _checked(value, model, field: str):
        if type(value) is not model:
            raise HILRejected(f"{field} is invalid")
        try:
            return model.model_validate(copy.deepcopy(value.model_dump(mode="python")))
        except Exception as error:
            raise HILRejected(f"{field} is invalid") from error

    def _target(self, expected):
        try:
            value = self._targets.get_target(expected.project_id, expected.device_id)
            checked = (
                self._checked(value, type(expected), "target")
                if value is not None
                else None
            )
        except HILRejected:
            raise
        except Exception as error:
            raise HILUnavailable("HIL target unavailable") from error
        if checked != expected:
            raise HILRejected("HIL target drift")
        return checked

    def _firmware_artifact(self, expected):
        try:
            value = self._firmware.get_firmware(
                expected.project_id, expected.artifact_id
            )
            checked = (
                self._checked(value, type(expected), "firmware")
                if value is not None
                else None
            )
        except HILRejected:
            raise
        except Exception as error:
            raise HILUnavailable("HIL firmware unavailable") from error
        if checked != expected:
            raise HILRejected("HIL firmware drift")
        return checked

    def _source(
        self, expected: EngineeringProposalReference
    ) -> EngineeringProposalReference:
        try:
            value = self._proposals.get_proposal(copy.deepcopy(expected))
            checked = (
                self._checked(value, EngineeringProposalReference, "proposal source")
                if value is not None
                else None
            )
        except HILRejected:
            raise
        except Exception as error:
            raise HILUnavailable("engineering proposal unavailable") from error
        if checked != expected:
            raise HILRejected("engineering proposal drift")
        return checked

    def create_test_proposal(
        self, request: HardwareTestProposalRequest
    ) -> HardwareTestProposal:
        checked = self._checked(
            request, HardwareTestProposalRequest, "test proposal request"
        )
        self._source(checked.source_proposal)
        self._target(checked.target)
        self._firmware_artifact(checked.firmware)
        if checked.fixture.expires_at < self._clock.now():
            raise HILRejected("HIL fixture expired")
        proposal = HardwareTestProposal(
            **checked.model_dump(mode="python", exclude={"fingerprint"}),
            proposal_id=deterministic_id("hil-proposal", checked.fingerprint),
        )
        append_hil_audit(
            sink=self._audit,
            clock=self._clock,
            event_type=HILAuditEventType.TEST_PROPOSAL_CREATED,
            project_id=proposal.project_id,
            operation_fingerprint=proposal.fingerprint,
            result_fingerprint=proposal.fingerprint,
        )
        return proposal

    def submit_review(
        self, proposal: HardwareTestProposal, reviewer: str
    ) -> HardwareApprovalRequest:
        checked = self._checked(proposal, HardwareTestProposal, "test proposal")
        request = HardwareApprovalRequest(
            approval_id=deterministic_id("hil-approval", checked.fingerprint),
            proposal=checked,
            requested_reviewer=reviewer,
            requested_at=self._clock.now(),
        )
        try:
            raw = self._approvals.submit(copy.deepcopy(request))
            result = self._checked(raw, HardwareApprovalRequest, "approval request")
        except HILRejected:
            raise
        except Exception as error:
            raise HILUnavailable("HIL review unavailable") from error
        if result != request:
            raise HILRejected("HIL review request mismatch")
        append_hil_audit(
            sink=self._audit,
            clock=self._clock,
            event_type=HILAuditEventType.REVIEW_SUBMITTED,
            project_id=checked.project_id,
            operation_fingerprint=request.fingerprint,
            result_fingerprint=result.fingerprint,
        )
        return result

    def authorize(
        self, proposal: HardwareTestProposal, approval_id: str
    ) -> HILExecutionRequest:
        checked = self._checked(proposal, HardwareTestProposal, "test proposal")
        self._source(checked.source_proposal)
        self._target(checked.target)
        self._firmware_artifact(checked.firmware)
        try:
            raw = self._approvals.get_record(approval_id)
            approval = (
                self._checked(raw, HardwareApprovalRecord, "approval record")
                if raw is not None
                else None
            )
        except HILRejected:
            raise
        except Exception as error:
            raise HILUnavailable("HIL approval unavailable") from error
        if (
            approval is None
            or approval.decision is not HardwareApprovalDecision.APPROVE
        ):
            raise HILApprovalRequired("HIL approval required")
        expected_review = deterministic_id("hil-approval", checked.fingerprint)
        if (
            approval.approval_id != expected_review
            or approval.requested_reviewer != approval.reviewer
            or self._clock.now() > approval.scope.expires_at
        ):
            raise HILApprovalRequired("HIL approval invalid")
        request = HILExecutionRequest(
            execution_id=deterministic_id(
                "hil-execution", checked.fingerprint, approval.fingerprint
            ),
            proposal=checked,
            approval=approval,
            target=checked.target,
            firmware=checked.firmware,
            fixture=checked.fixture,
            authorized_at=self._clock.now(),
        )
        append_hil_audit(
            sink=self._audit,
            clock=self._clock,
            event_type=HILAuditEventType.TEST_APPROVED,
            project_id=checked.project_id,
            operation_fingerprint=request.fingerprint,
            result_fingerprint=approval.fingerprint,
        )
        return request

    def execute(self, request: HILExecutionRequest) -> HILExecutionReceipt:
        checked = self._checked(request, HILExecutionRequest, "execution request")
        if self._clock.now() > checked.approval.scope.expires_at:
            raise HILApprovalRequired("HIL approval expired")
        self._target(checked.target)
        self._firmware_artifact(checked.firmware)
        try:
            raw = self._execution.execute(copy.deepcopy(checked))
            receipt = self._checked(raw, HILExecutionReceipt, "execution receipt")
        except HILRejected:
            raise
        except Exception as error:
            append_hil_audit(
                sink=self._audit,
                clock=self._clock,
                event_type=HILAuditEventType.HIL_FAILED,
                project_id=checked.proposal.project_id,
                operation_fingerprint=checked.fingerprint,
                result_fingerprint=None,
            )
            raise HILUnavailable("HIL execution unavailable") from error
        if (
            receipt.execution_id != checked.execution_id
            or receipt.request_fingerprint != checked.fingerprint
            or receipt.project_id != checked.proposal.project_id
            or receipt.target_fingerprint != checked.target.fingerprint
            or receipt.firmware_fingerprint != checked.firmware.firmware_fingerprint
        ):
            raise HILRejected("HIL execution receipt mismatch")
        event = (
            HILAuditEventType.HIL_FAILED
            if receipt.status
            in (HILExecutionStatus.FAILED, HILExecutionStatus.CANCELLED)
            else HILAuditEventType.HIL_STARTED
        )
        append_hil_audit(
            sink=self._audit,
            clock=self._clock,
            event_type=event,
            project_id=checked.proposal.project_id,
            operation_fingerprint=checked.fingerprint,
            result_fingerprint=receipt.fingerprint,
        )
        return receipt

    def collect_observation(
        self, request: HardwareObservationRequest, proposal: HardwareTestProposal
    ) -> HardwareObservation:
        checked_request = self._checked(
            request, HardwareObservationRequest, "observation request"
        )
        checked_proposal = self._checked(
            proposal, HardwareTestProposal, "test proposal"
        )
        receipt = checked_request.execution
        if (
            receipt.status
            not in (HILExecutionStatus.STARTED, HILExecutionStatus.COMPLETED)
            or receipt.project_id != checked_proposal.project_id
            or receipt.target_fingerprint != checked_proposal.target.fingerprint
            or receipt.firmware_fingerprint
            != checked_proposal.firmware.firmware_fingerprint
            or checked_request.observation_type
            not in checked_proposal.expected_observations
        ):
            raise HILRejected("HIL observation is not authorized")
        try:
            raw = self._measure.collect(copy.deepcopy(checked_request))
            observation = self._checked(
                raw, HardwareObservation, "hardware observation"
            )
            stored = self._checked(
                self._observations.append(copy.deepcopy(observation)),
                HardwareObservation,
                "stored observation",
            )
        except HILRejected:
            raise
        except Exception as error:
            raise HILUnavailable("HIL observation unavailable") from error
        if (
            stored != observation
            or observation.request_fingerprint != checked_request.fingerprint
            or observation.project_id != checked_proposal.project_id
            or observation.target_fingerprint != receipt.target_fingerprint
            or observation.firmware_fingerprint != receipt.firmware_fingerprint
            or observation.observation_type is not checked_request.observation_type
        ):
            raise HILRejected("HIL observation mismatch")
        append_hil_audit(
            sink=self._audit,
            clock=self._clock,
            event_type=HILAuditEventType.OBSERVATION_CAPTURED,
            project_id=checked_proposal.project_id,
            operation_fingerprint=checked_request.fingerprint,
            result_fingerprint=observation.fingerprint,
        )
        return observation

    def create_report(
        self,
        proposal: HardwareTestProposal,
        approval: HardwareApprovalRecord,
        receipt: HILExecutionReceipt,
        observations: tuple[HardwareObservation, ...],
        *,
        base_context_fingerprint: str | None = None,
    ) -> HILReport:
        checked = self._checked(proposal, HardwareTestProposal, "test proposal")
        checked_approval = self._checked(
            approval, HardwareApprovalRecord, "approval record"
        )
        checked_receipt = self._checked(
            receipt, HILExecutionReceipt, "execution receipt"
        )
        checked_observations = tuple(
            self._checked(item, HardwareObservation, "observation")
            for item in observations
        )
        if (
            type(observations) is not tuple
            or not checked_observations
            or any(
                item.project_id != checked.project_id for item in checked_observations
            )
        ):
            raise HILRejected("HIL report observations invalid")
        overlay = HardwareHILContextOverlay(
            overlay_id=deterministic_id(
                "hil-overlay",
                checked_receipt.fingerprint,
                *[item.fingerprint for item in checked_observations],
            ),
            project_id=checked.project_id,
            base_context_fingerprint=base_context_fingerprint,
            evidence=tuple(
                HILRuntimeEvidence(observation=item) for item in checked_observations
            ),
            created_at=self._clock.now(),
        )
        try:
            projected = self._checked(
                self._context.project(copy.deepcopy(overlay)),
                HardwareHILContextOverlay,
                "context overlay",
            )
            candidates = self._memory.create(copy.deepcopy(projected))
            if type(candidates) is not tuple:
                raise TypeError("memory candidates are invalid")
            checked_candidates = tuple(
                self._checked(item, HILMemoryCandidateReference, "memory candidate")
                for item in candidates
            )
        except HILRejected:
            raise
        except Exception as error:
            raise HILUnavailable("HIL projection unavailable") from error
        if projected != overlay or any(
            item.project_id != checked.project_id
            or item.overlay_fingerprint != overlay.fingerprint
            or item.review_status != "REVIEW_REQUIRED"
            for item in checked_candidates
        ):
            raise HILRejected("HIL projection binding mismatch")
        return HILReport(
            proposal=checked,
            approval=checked_approval,
            receipt=checked_receipt,
            observations=checked_observations,
            overlay=overlay,
            memory_candidates=checked_candidates,
        )


__all__ = ("HardwareHILRuntimeService",)
