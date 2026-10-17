from __future__ import annotations

import copy
from datetime import timedelta

from .audit import append_eda_audit
from .contracts import deterministic_id
from .exceptions import (
    EDAApprovalRequired,
    EDAArtifactDrift,
    EDARejected,
    EDAUnavailable,
)
from .models import (
    EDAAnalysisRequest,
    EDAAnalysisResult,
    EDAApplyReceipt,
    EDAApplyRequest,
    EDAApplyStatus,
    EDAArtifactReference,
    EDAAuditEventType,
    EDAChangeSet,
    EDAReviewRecord,
    EDAReviewRequest,
    EDAValidationKind,
    EDAValidationReport,
    PCBDesignProposal,
    approval_from_record,
)
from .ports import (
    EDAApplyPort,
    EDAArtifactSourcePort,
    EDAAuditSinkPort,
    EDAChangeSetSourcePort,
    EDAClockPort,
    EDAProposalPort,
    EDAReviewPort,
    EDARevisionWorkspacePort,
    EDAValidationPort,
)


class EngineeringEDAService:
    __slots__ = (
        "_apply",
        "_artifacts",
        "_audit",
        "_changes",
        "_clock",
        "_proposals",
        "_reviews",
        "_validation",
        "_workspace",
    )

    def __init__(
        self,
        *,
        artifacts: EDAArtifactSourcePort,
        proposals: EDAProposalPort,
        changes: EDAChangeSetSourcePort,
        reviews: EDAReviewPort,
        workspace: EDARevisionWorkspacePort,
        apply_port: EDAApplyPort,
        validation: EDAValidationPort,
        audit: EDAAuditSinkPort,
        clock: EDAClockPort,
    ) -> None:
        dependencies = (
            (artifacts, EDAArtifactSourcePort),
            (proposals, EDAProposalPort),
            (changes, EDAChangeSetSourcePort),
            (reviews, EDAReviewPort),
            (workspace, EDARevisionWorkspacePort),
            (apply_port, EDAApplyPort),
            (validation, EDAValidationPort),
            (audit, EDAAuditSinkPort),
            (clock, EDAClockPort),
        )
        if any(not isinstance(value, port) for value, port in dependencies):
            raise TypeError("EDA service dependency is invalid")
        self._artifacts = artifacts
        self._proposals = proposals
        self._changes = changes
        self._reviews = reviews
        self._workspace = workspace
        self._apply = apply_port
        self._validation = validation
        self._audit = audit
        self._clock = clock

    @staticmethod
    def _checked(value, model, field: str):
        if type(value) is not model:
            raise EDARejected(f"{field} is invalid")
        try:
            return model.model_validate(copy.deepcopy(value.model_dump(mode="python")))
        except Exception as error:
            raise EDARejected(f"{field} is invalid") from error

    def _current(self, artifact: EDAArtifactReference) -> EDAArtifactReference:
        try:
            raw = self._artifacts.get_artifact(
                artifact.project_id, artifact.artifact_id
            )
            if raw is None:
                raise EDAUnavailable("EDA artifact unavailable")
            current = self._checked(raw, EDAArtifactReference, "artifact")
        except EDAUnavailable:
            raise
        except Exception as error:
            raise EDAUnavailable("EDA artifact unavailable") from error
        if current != artifact:
            raise EDAArtifactDrift("EDA artifact drift")
        return current

    def analyze(self, request: EDAAnalysisRequest) -> EDAAnalysisResult:
        checked = self._checked(request, EDAAnalysisRequest, "analysis request")
        project = self._current(checked.project_artifact)
        reports: list[EDAValidationReport] = []
        for source in checked.pcb_artifacts:
            raw_current = self._artifacts.get_artifact(
                checked.project_id, source.artifact_id
            )
            if raw_current is None:
                raise EDAArtifactDrift("PCB artifact drift")
            current = self._checked(raw_current, EDAArtifactReference, "PCB artifact")
            if (
                current.project_id != source.project_id
                or current.artifact_id != source.artifact_id
                or current.content_fingerprint != source.content_fingerprint
            ):
                raise EDAArtifactDrift("PCB artifact drift")
        board = next(
            item
            for item in checked.pcb_artifacts
            if item.artifact_type.value == "BOARD"
        )
        schematic = next(
            item
            for item in checked.pcb_artifacts
            if item.artifact_type.value == "SCHEMATIC"
        )
        for source, kind in (
            (board, EDAValidationKind.DRC),
            (schematic, EDAValidationKind.ERC),
        ):
            adapted = self._artifacts.get_artifact(
                checked.project_id, source.artifact_id
            )
            if adapted is None:
                raise EDAUnavailable("EDA validation artifact unavailable")
            adapted = self._checked(
                adapted, EDAArtifactReference, "validation artifact"
            )
            try:
                report = self._validation.validate(adapted, kind)
                reports.append(
                    self._checked(report, EDAValidationReport, "validation report")
                )
            except EDARejected:
                raise
            except Exception as error:
                raise EDAUnavailable("EDA validation unavailable") from error
        result = EDAAnalysisResult(
            analysis_id=deterministic_id("eda-analysis", checked.fingerprint),
            project_id=checked.project_id,
            request_fingerprint=checked.fingerprint,
            pcb_analysis_fingerprint=checked.pcb_analysis.fingerprint,
            project_artifact_fingerprint=project.content_fingerprint,
            validation_reports=tuple(reports),
            completed_at=self._clock.now(),
        )
        append_eda_audit(
            sink=self._audit,
            clock=self._clock,
            event_type=EDAAuditEventType.ANALYSIS_COMPLETED,
            project_id=checked.project_id,
            operation_fingerprint=checked.fingerprint,
            artifact_fingerprint=project.content_fingerprint,
            result_fingerprint=result.fingerprint,
            timestamp=result.completed_at,
        )
        return result

    def propose(self, analysis: EDAAnalysisResult) -> tuple[PCBDesignProposal, ...]:
        checked = self._checked(analysis, EDAAnalysisResult, "analysis")
        try:
            proposals = tuple(
                self._checked(item, PCBDesignProposal, "proposal")
                for item in self._proposals.propose(copy.deepcopy(checked))
            )
        except EDARejected:
            raise
        except Exception as error:
            raise EDAUnavailable("EDA proposal unavailable") from error
        if any(
            item.project_id != checked.project_id
            or item.analysis_fingerprint != checked.fingerprint
            for item in proposals
        ):
            raise EDARejected("EDA proposal binding mismatch")
        for item in proposals:
            append_eda_audit(
                sink=self._audit,
                clock=self._clock,
                event_type=EDAAuditEventType.PROPOSAL_CREATED,
                project_id=checked.project_id,
                operation_fingerprint=item.fingerprint,
                result_fingerprint=item.fingerprint,
                timestamp=item.created_at,
            )
        return proposals

    def submit_review(
        self, proposal: PCBDesignProposal, change_set_id: str
    ) -> EDAReviewRecord:
        checked_proposal = self._checked(proposal, PCBDesignProposal, "proposal")
        raw = self._changes.get_change_set(change_set_id)
        if raw is None:
            raise EDAUnavailable("EDA change set unavailable")
        changes = self._checked(raw, EDAChangeSet, "change set")
        if (
            changes.project_id != checked_proposal.project_id
            or changes.proposal_fingerprint != checked_proposal.fingerprint
        ):
            raise EDARejected("change set proposal mismatch")
        request = EDAReviewRequest(
            review_id=deterministic_id("eda-review", changes.fingerprint),
            project_id=changes.project_id,
            change_set_fingerprint=changes.fingerprint,
            proposal_fingerprint=checked_proposal.fingerprint,
            requester=changes.authored_by,
            requested_at=self._clock.now(),
        )
        record = self._checked(
            self._reviews.submit(request), EDAReviewRecord, "review record"
        )
        if record.request != request:
            raise EDARejected("review record mismatch")
        append_eda_audit(
            sink=self._audit,
            clock=self._clock,
            event_type=EDAAuditEventType.REVIEW_SUBMITTED,
            project_id=request.project_id,
            operation_fingerprint=request.fingerprint,
            result_fingerprint=record.fingerprint,
            timestamp=request.requested_at,
        )
        return record

    def authorize_apply(
        self,
        change_set_id: str,
        review_id: str,
        *,
        maximum_age: timedelta = timedelta(days=7),
    ) -> EDAApplyRequest:
        raw_change = self._changes.get_change_set(change_set_id)
        raw_review = self._reviews.get_record(review_id)
        if raw_change is None or raw_review is None:
            raise EDAApprovalRequired("EDA approval unavailable")
        changes = self._checked(raw_change, EDAChangeSet, "change set")
        record = self._checked(raw_review, EDAReviewRecord, "review record")
        try:
            approval = approval_from_record(
                record, maximum_age=maximum_age, now=self._clock.now()
            )
        except ValueError as error:
            raise EDAApprovalRequired("EDA approval required") from error
        if approval.change_set_fingerprint != changes.fingerprint:
            raise EDAApprovalRequired("EDA approval mismatch")
        self._current(changes.target_artifact)
        staging_revision = deterministic_id("staging", changes.fingerprint)
        return EDAApplyRequest(
            apply_id=deterministic_id(
                "eda-apply", changes.fingerprint, approval.fingerprint
            ),
            project_id=changes.project_id,
            change_set=changes,
            approval=approval,
            before_fingerprint=changes.before_fingerprint,
            staging_revision=staging_revision,
            requested_at=self._clock.now(),
        )

    def apply_to_staging(self, request: EDAApplyRequest) -> EDAApplyReceipt:
        checked = self._checked(request, EDAApplyRequest, "apply request")
        self._current(checked.change_set.target_artifact)
        current_record = self._reviews.get_record(checked.approval.review_id)
        if (
            current_record is None
            or current_record.fingerprint != checked.approval.review_record_fingerprint
        ):
            raise EDAApprovalRequired("EDA approval changed")
        latest_change = self._changes.get_change_set(checked.change_set.change_set_id)
        if latest_change is None or latest_change != checked.change_set:
            raise EDAArtifactDrift("EDA change set drift")
        staging = self._checked(
            self._workspace.create_staging(
                checked.change_set.target_artifact, checked.staging_revision
            ),
            EDAArtifactReference,
            "staging artifact",
        )
        if (
            staging.revision != checked.staging_revision
            or staging.content_fingerprint != checked.before_fingerprint
        ):
            raise EDARejected("staging isolation mismatch")
        receipt = self._checked(
            self._apply.apply(checked, staging), EDAApplyReceipt, "apply receipt"
        )
        if (
            receipt.apply_request_fingerprint != checked.fingerprint
            or receipt.status is not EDAApplyStatus.STAGED
        ):
            raise EDARejected("apply receipt mismatch")
        validation = self._checked(
            self._validation.validate(
                copy.deepcopy(receipt.after_artifact), EDAValidationKind.DRC
            ),
            EDAValidationReport,
            "staging validation report",
        )
        if (
            validation.artifact != receipt.after_artifact
            or validation.validation_kind is not EDAValidationKind.DRC
        ):
            raise EDARejected("staging validation mismatch")
        append_eda_audit(
            sink=self._audit,
            clock=self._clock,
            event_type=EDAAuditEventType.VALIDATION_COMPLETED,
            project_id=checked.project_id,
            operation_fingerprint=validation.fingerprint,
            artifact_fingerprint=receipt.after_artifact.content_fingerprint,
            result_fingerprint=validation.fingerprint,
            timestamp=validation.validated_at,
        )
        append_eda_audit(
            sink=self._audit,
            clock=self._clock,
            event_type=EDAAuditEventType.APPLY_STAGED,
            project_id=checked.project_id,
            operation_fingerprint=checked.fingerprint,
            artifact_fingerprint=receipt.after_artifact.content_fingerprint,
            result_fingerprint=receipt.fingerprint,
            timestamp=receipt.staged_at,
        )
        return receipt

    def validate(
        self, artifact: EDAArtifactReference, kind: EDAValidationKind
    ) -> EDAValidationReport:
        checked = self._checked(artifact, EDAArtifactReference, "artifact")
        report = self._checked(
            self._validation.validate(copy.deepcopy(checked), kind),
            EDAValidationReport,
            "validation report",
        )
        if report.artifact != checked or report.validation_kind is not kind:
            raise EDARejected("validation report mismatch")
        append_eda_audit(
            sink=self._audit,
            clock=self._clock,
            event_type=EDAAuditEventType.VALIDATION_COMPLETED,
            project_id=checked.project_id,
            operation_fingerprint=report.fingerprint,
            artifact_fingerprint=checked.content_fingerprint,
            result_fingerprint=report.fingerprint,
            timestamp=report.validated_at,
        )
        return report


__all__ = ("EngineeringEDAService",)
