from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar

from embedded_copilot.failure_analysis import FailureAnalysis
from embedded_copilot.repair_proposal import RepairProposal, RepairTargetType

from .contracts import (
    validate_apply_receipt,
    validate_apply_request,
    validate_artifact_state,
    validate_build_receipt,
    validate_existing_repair_proposal,
    validate_failure_analysis,
    validate_legacy_review_record,
    validate_patch_proposal,
    validate_preparation,
    validate_repair_request,
    validate_review_record,
    validate_review_request,
    validate_verification_receipt,
)
from .exceptions import (
    ApplyFailed,
    ApprovalRejected,
    ArtifactNotFound,
    AuditWriteFailed,
    EvidenceMissing,
    RepairRejected,
    RepairUnavailable,
    ReviewNotFound,
    TargetDrift,
)
from .models import (
    ApplyReceipt,
    ApplyRequest,
    ApplyStatus,
    ArtifactStateReference,
    ExistingRepairProposalReference,
    HumanApprovalReference,
    PatchProposal,
    RepairBuildRequest,
    RepairEvent,
    RepairEventType,
    RepairPlan,
    RepairPreparation,
    RepairRequest,
    RepairResult,
    RepairResultStatus,
    RepairReviewDecisionType,
    RepairReviewRequest,
    RepairRisk,
    RepairVerificationRequest,
    RepairVerificationStatus,
    deterministic_id,
)
from .ports import (
    ApplyPort,
    ArtifactSourcePort,
    PatchGenerationPort,
    RepairAnalysisPort,
    RepairAuditSinkPort,
    RepairBuildPort,
    RepairClockPort,
    RepairProposalPort,
    RepairReviewPort,
    ReviewSourcePort,
    VerificationPort,
)

T = TypeVar("T")


class RepairService:
    """Deterministic orchestration for offline repair governance receipts."""

    def __init__(
        self,
        *,
        analysis_port: RepairAnalysisPort,
        proposal_port: RepairProposalPort,
        patch_port: PatchGenerationPort,
        artifact_source: ArtifactSourcePort,
        repair_review_port: RepairReviewPort,
        apply_port: ApplyPort,
        build_port: RepairBuildPort,
        verification_port: VerificationPort,
        audit_sink: RepairAuditSinkPort,
        clock: RepairClockPort,
        review_source: ReviewSourcePort | None = None,
    ) -> None:
        self._analysis_port = analysis_port
        self._proposal_port = proposal_port
        self._patch_port = patch_port
        self._artifact_source = artifact_source
        self._repair_review_port = repair_review_port
        self._apply_port = apply_port
        self._build_port = build_port
        self._verification_port = verification_port
        self._audit_sink = audit_sink
        self._clock = clock
        self._review_source = review_source

    def prepare(self, request: RepairRequest) -> RepairPreparation:
        checked_request = self._checked(validate_repair_request, request)
        now = self._now(not_before=checked_request.requested_at)
        current = self._current_target(
            checked_request.project_id,
            checked_request.target_reference.artifact_id,
        )
        if current != checked_request.target_reference:
            raise TargetDrift("repair target changed before preparation")

        analysis = self._call(
            lambda: self._analysis_port.analyze(checked_request),
            "failure analysis is unavailable",
        )
        checked_analysis = self._checked(validate_failure_analysis, analysis)
        self._bind_analysis(checked_request, checked_analysis)

        if checked_request.legacy_review_reference is not None:
            self._validate_legacy_review(checked_request)

        proposal = self._call(
            lambda: self._proposal_port.propose(checked_analysis, current),
            "existing repair proposal is unavailable",
        )
        checked_proposal = self._checked(
            validate_existing_repair_proposal, proposal
        )
        self._bind_existing_proposal(checked_analysis, checked_proposal)

        proposal_reference = ExistingRepairProposalReference.create(
            proposal_id=checked_proposal.proposal_id,
            project_id=checked_proposal.project_id,
            failure_id=checked_proposal.failure_id,
            proposal_fingerprint=checked_proposal.fingerprint,
        )
        plan = RepairPlan.create(
            plan_id=deterministic_id(
                "repair-plan",
                checked_request.fingerprint,
                checked_proposal.fingerprint,
            ),
            project_id=checked_request.project_id,
            request_id=checked_request.request_id,
            request_fingerprint=checked_request.fingerprint,
            analysis_reference=checked_request.failure_reference,
            existing_proposal=checked_proposal,
            existing_proposal_reference=proposal_reference,
            risk=self._risk(checked_analysis, checked_proposal),
            recommended_changes=(checked_proposal.change_summary,),
            evidence_refs=checked_request.evidence_refs,
            actor=checked_request.requester,
            created_at=now,
        )

        generated = self._call(
            lambda: self._patch_port.generate(plan, current),
            "patch proposal generation is unavailable",
        )
        patch = self._checked(validate_patch_proposal, generated)
        self._bind_patch(plan, current, patch)
        preparation = RepairPreparation.create(
            request=checked_request,
            plan=plan,
            patch=patch,
        )
        self._append_event(
            RepairEventType.PREPARED,
            preparation.fingerprint,
            preparation.request.request_id,
            preparation.request.project_id,
            preparation.request.requester,
            now,
            preparation.request.evidence_refs,
        )
        return preparation.detached()

    def submit_review(self, preparation: RepairPreparation) -> RepairReviewRequest:
        checked = self._checked(validate_preparation, preparation)
        now = self._now(not_before=checked.patch.created_at)
        request = RepairReviewRequest.create(
            review_id=deterministic_id(
                "repair-review", checked.patch.fingerprint
            ),
            project_id=checked.request.project_id,
            patch_reference=checked.patch.as_reference(),
            requester=checked.request.requester,
            reason=checked.request.reason,
            created_at=now,
        )
        submitted = self._call(
            lambda: self._repair_review_port.submit(request),
            "repair review submission is unavailable",
        )
        checked_submitted = self._checked(validate_review_request, submitted)
        if checked_submitted != request:
            raise RepairRejected("repair review port changed the request")
        self._append_event(
            RepairEventType.REVIEW_SUBMITTED,
            request.fingerprint,
            request.review_id,
            request.project_id,
            request.requester,
            now,
            checked.patch.evidence_refs,
        )
        return checked_submitted

    def authorize_apply(
        self, patch: PatchProposal, review_id: str
    ) -> ApplyRequest:
        checked_patch = self._checked(validate_patch_proposal, patch)
        record = self._approved_record(checked_patch, review_id)
        self._assert_current_before_state(checked_patch)
        approval = HumanApprovalReference.create(
            review_id=record.review_request.review_id,
            decision_id=record.decision.decision_id,
            project_id=record.review_request.project_id,
            patch_fingerprint=checked_patch.fingerprint,
            reviewer=record.decision.reviewer,
            approved_at=record.decision.decided_at,
            review_record_fingerprint=record.fingerprint,
        )
        now = self._now(not_before=approval.approved_at)
        apply_request = ApplyRequest.create(
            apply_id=deterministic_id(
                "repair-apply", checked_patch.fingerprint, record.fingerprint
            ),
            project_id=checked_patch.project_id,
            patch_reference=checked_patch.as_reference(),
            approval_reference=approval,
            actor=approval.reviewer,
            requested_at=now,
        )
        self._append_event(
            RepairEventType.APPROVED,
            record.fingerprint,
            record.review_request.review_id,
            checked_patch.project_id,
            approval.reviewer,
            now,
            checked_patch.evidence_refs,
        )
        return apply_request

    def apply(
        self, patch: PatchProposal, apply_request: ApplyRequest
    ) -> ApplyReceipt:
        checked_patch = self._checked(validate_patch_proposal, patch)
        checked_request = self._checked(validate_apply_request, apply_request)
        if checked_request.project_id != checked_patch.project_id:
            raise ApprovalRejected("apply request project mismatch")
        if checked_request.patch_reference != checked_patch.as_reference():
            raise ApprovalRejected("apply request patch mismatch")
        expected_record = self._approved_record(
            checked_patch, checked_request.approval_reference.review_id
        )
        approval = checked_request.approval_reference
        if approval.review_record_fingerprint != expected_record.fingerprint:
            raise ApprovalRejected("approval record fingerprint mismatch")
        if approval.decision_id != expected_record.decision.decision_id:
            raise ApprovalRejected("approval decision mismatch")
        if approval.reviewer != expected_record.decision.reviewer:
            raise ApprovalRejected("approval reviewer mismatch")
        if checked_request.actor != approval.reviewer:
            raise ApprovalRejected("apply actor must be the approved reviewer")
        expected_id = deterministic_id(
            "repair-apply", checked_patch.fingerprint, expected_record.fingerprint
        )
        if checked_request.apply_id != expected_id:
            raise ApprovalRejected("apply request identity mismatch")
        self._assert_current_before_state(checked_patch)

        receipt = self._call(
            lambda: self._apply_port.apply(checked_request),
            "fake apply port is unavailable",
        )
        checked_receipt = self._checked(validate_apply_receipt, receipt)
        if checked_receipt.apply_id != checked_request.apply_id:
            raise ApplyFailed("apply receipt identity mismatch")
        if checked_receipt.project_id != checked_request.project_id:
            raise ApplyFailed("apply receipt project mismatch")
        if checked_receipt.patch_reference != checked_request.patch_reference:
            raise ApplyFailed("apply receipt patch mismatch")
        if checked_receipt.approval_reference != approval:
            raise ApplyFailed("apply receipt approval mismatch")
        if checked_receipt.status is not ApplyStatus.ACCEPTED:
            raise ApplyFailed("fake apply request was not accepted")
        if checked_receipt.accepted_at < checked_request.requested_at:
            raise ApplyFailed("apply receipt predates request")
        self._append_event(
            RepairEventType.APPLY_ACCEPTED,
            checked_receipt.fingerprint,
            checked_receipt.apply_id,
            checked_receipt.project_id,
            checked_request.actor,
            checked_receipt.accepted_at,
            checked_receipt.evidence_refs,
        )
        return checked_receipt

    def verify(
        self, preparation: RepairPreparation, apply_receipt: ApplyReceipt
    ) -> RepairResult:
        checked = self._checked(validate_preparation, preparation)
        receipt = self._checked(validate_apply_receipt, apply_receipt)
        if receipt.project_id != checked.request.project_id:
            raise RepairRejected("apply receipt project mismatch")
        if receipt.patch_reference != checked.patch.as_reference():
            raise RepairRejected("apply receipt patch mismatch")
        if receipt.status is not ApplyStatus.ACCEPTED:
            raise RepairRejected("verification requires an accepted apply receipt")

        build_requested_at = self._now(not_before=receipt.accepted_at)
        build_request = RepairBuildRequest.create(
            build_id=deterministic_id(
                "repair-build", receipt.fingerprint, checked.patch.after_state.fingerprint
            ),
            project_id=checked.request.project_id,
            target_reference=checked.patch.after_state,
            apply_reference=receipt.as_reference(),
            actor=receipt.approval_reference.reviewer,
            requested_at=build_requested_at,
        )
        build_value = self._call(
            lambda: self._build_port.build(build_request),
            "fake build port is unavailable",
        )
        build_receipt = self._checked(validate_build_receipt, build_value)
        if build_receipt.build_id != build_request.build_id:
            raise RepairRejected("build receipt identity mismatch")
        if build_receipt.project_id != build_request.project_id:
            raise RepairRejected("build receipt project mismatch")
        if build_receipt.target_reference != build_request.target_reference:
            raise RepairRejected("build receipt target mismatch")
        if build_receipt.apply_reference != build_request.apply_reference:
            raise RepairRejected("build receipt apply mismatch")
        if build_receipt.completed_at < build_request.requested_at:
            raise RepairRejected("build receipt predates request")
        self._append_event(
            RepairEventType.BUILD_COMPLETED,
            build_receipt.fingerprint,
            build_receipt.build_id,
            build_receipt.project_id,
            build_request.actor,
            build_receipt.completed_at,
            build_receipt.evidence_refs,
        )

        verification_requested_at = self._now(not_before=build_receipt.completed_at)
        verification_request = RepairVerificationRequest.create(
            verification_id=deterministic_id(
                "repair-verification",
                build_receipt.fingerprint,
                checked.request.failure_reference.fingerprint,
            ),
            project_id=checked.request.project_id,
            target_reference=checked.patch.after_state,
            prior_failure_reference=checked.request.failure_reference,
            patch_reference=checked.patch.as_reference(),
            apply_reference=receipt.as_reference(),
            build_reference=build_receipt.as_reference(),
            requested_at=verification_requested_at,
        )
        verification_value = self._call(
            lambda: self._verification_port.verify(verification_request),
            "fake verification port is unavailable",
        )
        verification_receipt = self._checked(
            validate_verification_receipt, verification_value
        )
        if verification_receipt.verification_id != verification_request.verification_id:
            raise RepairRejected("verification receipt identity mismatch")
        if verification_receipt.project_id != verification_request.project_id:
            raise RepairRejected("verification receipt project mismatch")
        if verification_receipt.target_reference != verification_request.target_reference:
            raise RepairRejected("verification receipt target mismatch")
        if verification_receipt.verified_at < verification_request.requested_at:
            raise RepairRejected("verification receipt predates request")
        self._append_event(
            RepairEventType.VERIFICATION_COMPLETED,
            verification_receipt.fingerprint,
            verification_receipt.verification_id,
            verification_receipt.project_id,
            receipt.approval_reference.reviewer,
            verification_receipt.verified_at,
            verification_receipt.evidence_refs,
        )

        status_map = {
            RepairVerificationStatus.PASS: RepairResultStatus.VERIFIED,
            RepairVerificationStatus.FAIL: RepairResultStatus.FAILED,
            RepairVerificationStatus.ESCALATE: RepairResultStatus.ESCALATED,
        }
        evidence = self._merge_evidence(
            checked.request.evidence_refs,
            receipt.evidence_refs,
            build_receipt.evidence_refs,
            verification_receipt.evidence_refs,
        )
        result = RepairResult.create(
            result_id=deterministic_id(
                "repair-result",
                checked.fingerprint,
                verification_receipt.fingerprint,
            ),
            project_id=checked.request.project_id,
            request_id=checked.request.request_id,
            request_fingerprint=checked.request.fingerprint,
            plan_reference=checked.plan.as_reference(),
            patch_reference=checked.patch.as_reference(),
            approval_reference=receipt.approval_reference,
            apply_reference=receipt.as_reference(),
            build_reference=build_receipt.as_reference(),
            verification_reference=verification_receipt.as_reference(),
            status=status_map[verification_receipt.status],
            evidence_refs=evidence,
            completed_at=verification_receipt.verified_at,
        )
        return result.detached()

    def _current_target(
        self, project_id: str, artifact_id: str
    ) -> ArtifactStateReference:
        value = self._call(
            lambda: self._artifact_source.get_state(project_id, artifact_id),
            "artifact source is unavailable",
        )
        if value is None:
            raise ArtifactNotFound("repair artifact was not found")
        checked = self._checked(validate_artifact_state, value)
        if checked.project_id != project_id or checked.artifact_id != artifact_id:
            raise RepairRejected("artifact source returned a mismatched target")
        return checked

    def _assert_current_before_state(self, patch: PatchProposal) -> None:
        current = self._current_target(patch.project_id, patch.target_artifact)
        if current != patch.before_state:
            raise TargetDrift("repair target changed during review")

    def _approved_record(self, patch: PatchProposal, review_id: str):
        try:
            value = self._repair_review_port.get_record(review_id)
        except Exception as error:
            if isinstance(error, RepairUnavailable):
                raise
            raise RepairUnavailable("repair review source is unavailable") from error
        if value is None:
            raise ReviewNotFound("repair review decision was not found")
        record = self._checked(validate_review_record, value)
        if record.review_request.review_id != review_id:
            raise ApprovalRejected("repair review identity mismatch")
        if record.review_request.project_id != patch.project_id:
            raise ApprovalRejected("repair review project mismatch")
        if record.review_request.patch_reference != patch.as_reference():
            raise ApprovalRejected("repair review patch mismatch")
        if record.decision.decision is not RepairReviewDecisionType.APPROVE:
            raise ApprovalRejected("repair review did not approve the patch")
        return record

    def _validate_legacy_review(self, request: RepairRequest) -> None:
        reference = request.legacy_review_reference
        if reference is None:
            return
        if self._review_source is None:
            raise RepairUnavailable("legacy review source is unavailable")
        value = self._call(
            lambda: self._review_source.get_review_record(reference.review_id),
            "legacy review source is unavailable",
        )
        if value is None:
            raise ReviewNotFound("legacy review evidence was not found")
        record = self._checked(validate_legacy_review_record, value)
        if record.fingerprint != reference.record_fingerprint:
            raise RepairRejected("legacy review fingerprint mismatch")
        if record.review_request.project_id != request.project_id:
            raise RepairRejected("legacy review project mismatch")

    @staticmethod
    def _bind_analysis(request: RepairRequest, analysis: FailureAnalysis) -> None:
        reference = request.failure_reference
        if analysis.failure_id != reference.failure_id:
            raise RepairRejected("failure analysis identity mismatch")
        if analysis.project_id != request.project_id:
            raise RepairRejected("failure analysis project mismatch")
        if analysis.target_id != request.target_reference.artifact_id:
            raise RepairRejected("failure analysis target mismatch")
        if analysis.fingerprint != reference.failure_fingerprint:
            raise RepairRejected("failure analysis fingerprint mismatch")
        requested = {item.evidence_id for item in request.evidence_refs}
        if requested != set(analysis.evidence_refs):
            raise EvidenceMissing("repair evidence does not match failure analysis")

    @staticmethod
    def _bind_existing_proposal(
        analysis: FailureAnalysis, proposal: RepairProposal
    ) -> None:
        if proposal.project_id != analysis.project_id:
            raise RepairRejected("existing proposal project mismatch")
        if proposal.failure_id != analysis.failure_id:
            raise RepairRejected("existing proposal failure mismatch")
        if proposal.failure_analysis_fingerprint != analysis.fingerprint:
            raise RepairRejected("existing proposal analysis fingerprint mismatch")
        if proposal.context_fingerprint != analysis.context_fingerprint:
            raise RepairRejected("existing proposal context fingerprint mismatch")
        if set(proposal.evidence_refs) != set(analysis.evidence_refs):
            raise EvidenceMissing("existing proposal evidence mismatch")

    @staticmethod
    def _bind_patch(
        plan: RepairPlan,
        target: ArtifactStateReference,
        patch: PatchProposal,
    ) -> None:
        if patch.project_id != plan.project_id:
            raise RepairRejected("patch project mismatch")
        if patch.plan_reference != plan.as_reference():
            raise RepairRejected("patch plan mismatch")
        if patch.target_artifact != target.artifact_id:
            raise RepairRejected("patch target mismatch")
        if patch.before_state != target:
            raise RepairRejected("patch before-state mismatch")
        if patch.risk is not plan.risk:
            raise RepairRejected("patch risk mismatch")
        if patch.actor != plan.actor:
            raise RepairRejected("patch actor mismatch")
        if patch.created_at < plan.created_at:
            raise RepairRejected("patch predates plan")
        if patch.evidence_refs != plan.evidence_refs:
            raise EvidenceMissing("patch evidence mismatch")

    @staticmethod
    def _risk(
        analysis: FailureAnalysis, proposal: RepairProposal
    ) -> RepairRisk:
        if proposal.target_type is RepairTargetType.HARDWARE_REVIEW:
            return RepairRisk.CRITICAL
        if analysis.confidence < 0.5:
            return RepairRisk.HIGH
        if analysis.confidence < 0.8:
            return RepairRisk.MEDIUM
        return RepairRisk.LOW

    def _now(self, *, not_before: datetime) -> datetime:
        value = self._call(self._clock.now, "repair clock is unavailable")
        if not isinstance(value, datetime):
            raise RepairUnavailable("repair clock returned an invalid timestamp")
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise RepairUnavailable("repair clock must return UTC")
        checked = value.astimezone(UTC)
        if checked < not_before:
            raise RepairRejected("repair clock moved backwards")
        return checked

    def _append_event(
        self,
        event_type: RepairEventType,
        subject_fingerprint: str,
        subject_id: str,
        project_id: str,
        actor: str,
        occurred_at: datetime,
        evidence_refs: tuple,
    ) -> None:
        event = RepairEvent.create(
            event_id=deterministic_id(
                "repair-event", event_type.value, subject_fingerprint
            ),
            event_type=event_type,
            project_id=project_id,
            subject_id=subject_id,
            subject_fingerprint=subject_fingerprint,
            actor=actor,
            occurred_at=occurred_at,
            evidence_refs=evidence_refs,
        )
        try:
            written = self._audit_sink.append(event)
            checked = RepairEvent.model_validate(written.model_dump(mode="python"))
        except Exception as error:
            if isinstance(error, AuditWriteFailed):
                raise
            raise AuditWriteFailed("repair audit append failed") from error
        if checked != event:
            raise AuditWriteFailed("repair audit sink changed the event")

    @staticmethod
    def _merge_evidence(*groups: tuple) -> tuple:
        by_id = {}
        for group in groups:
            for item in group:
                previous = by_id.get(item.evidence_id)
                if previous is not None and previous != item:
                    raise EvidenceMissing("conflicting evidence identity")
                by_id[item.evidence_id] = item
        return tuple(by_id[key] for key in sorted(by_id))

    @staticmethod
    def _checked(validator: Callable[[object], T], value: object) -> T:
        try:
            return validator(value)
        except (TypeError, ValueError) as error:
            raise RepairRejected("repair boundary validation failed") from error

    @staticmethod
    def _call(operation: Callable[[], T], message: str) -> T:
        try:
            return operation()
        except Exception as error:
            if isinstance(error, (RepairRejected, RepairUnavailable)):
                raise
            raise RepairUnavailable(message) from error


__all__ = ("RepairService",)
