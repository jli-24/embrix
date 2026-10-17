from __future__ import annotations

import copy
import hashlib
from collections.abc import Iterable
from datetime import UTC, datetime

from embedded_copilot.engineering_review import ReviewRecord
from embedded_copilot.failure_analysis import FailureAnalysis
from embedded_copilot.repair_proposal import (
    RepairProposal,
    RepairTargetType,
    proposal_id,
)

from ..contracts import (
    validate_apply_request,
    validate_artifact_state,
    validate_build_request,
    validate_failure_analysis,
    validate_legacy_review_record,
    validate_repair_event,
    validate_repair_plan,
    validate_repair_request,
    validate_review_record,
    validate_review_request,
    validate_verification_request,
)
from ..exceptions import RepairConflict, RepairUnavailable
from ..models import (
    ApplyReceipt,
    ApplyRequest,
    ApplyStatus,
    ArtifactStateReference,
    ChangeSetReference,
    EvidenceReference,
    PatchProposal,
    RepairBuildReceipt,
    RepairBuildRequest,
    RepairBuildStatus,
    RepairEvent,
    RepairPlan,
    RepairRequest,
    RepairReviewDecision,
    RepairReviewRecord,
    RepairReviewRequest,
    RepairReviewStatus,
    RepairVerificationReceipt,
    RepairVerificationRequest,
    RepairVerificationStatus,
    deterministic_id,
)


def _sha(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _copy_upstream(value, model):
    return model.model_validate(copy.deepcopy(value.model_dump(mode="python")))


class FixedRepairClock:
    def __init__(self, value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("fixed repair clock requires UTC")
        self._value = value.astimezone(UTC)

    def now(self) -> datetime:
        return copy.deepcopy(self._value)


class FakeRepairAnalysisAdapter:
    def __init__(self, analyses: Iterable[FailureAnalysis] = ()) -> None:
        self._analyses: dict[str, FailureAnalysis] = {}
        self.fail_next = False
        for value in analyses:
            checked = validate_failure_analysis(value)
            self._analyses[checked.failure_id] = checked

    def analyze(self, request: RepairRequest) -> FailureAnalysis:
        checked = validate_repair_request(request)
        if self.fail_next:
            self.fail_next = False
            raise RepairUnavailable("injected analysis failure")
        value = self._analyses.get(checked.failure_reference.failure_id)
        if value is None:
            raise RepairUnavailable("failure analysis not found")
        return _copy_upstream(value, FailureAnalysis)


class FakeRepairProposalAdapter:
    def __init__(
        self,
        *,
        target_type: RepairTargetType = RepairTargetType.CONFIGURATION_CHANGE,
        change_summary: str = "Adjust the referenced artifact state",
    ) -> None:
        self._target_type = target_type
        self._change_summary = change_summary
        self._values: dict[str, RepairProposal] = {}
        self.fail_next = False

    def propose(
        self, analysis: FailureAnalysis, target: ArtifactStateReference
    ) -> RepairProposal:
        checked_analysis = validate_failure_analysis(analysis)
        checked_target = validate_artifact_state(target)
        if self.fail_next:
            self.fail_next = False
            raise RepairUnavailable("injected proposal failure")
        value = RepairProposal.create(
            proposal_id=proposal_id(
                checked_analysis.failure_id,
                checked_analysis.fingerprint,
                self._target_type,
            ),
            project_id=checked_analysis.project_id,
            failure_id=checked_analysis.failure_id,
            failure_analysis_fingerprint=checked_analysis.fingerprint,
            context_fingerprint=checked_analysis.context_fingerprint,
            target_type=self._target_type,
            change_summary=self._change_summary,
            affected_component=checked_target.artifact_id,
            evidence_refs=checked_analysis.evidence_refs,
            confidence=checked_analysis.confidence,
        )
        previous = self._values.get(value.proposal_id)
        if previous is not None and previous.fingerprint != value.fingerprint:
            raise RepairConflict("proposal identity conflict")
        self._values[value.proposal_id] = value
        return _copy_upstream(value, RepairProposal)


class FakePatchGenerationAdapter:
    def __init__(self) -> None:
        self._values: dict[str, PatchProposal] = {}
        self.fail_next = False

    def generate(
        self, plan: RepairPlan, target_state: ArtifactStateReference
    ) -> PatchProposal:
        checked_plan = validate_repair_plan(plan)
        checked_target = validate_artifact_state(target_state)
        if self.fail_next:
            self.fail_next = False
            raise RepairUnavailable("injected patch generation failure")
        patch_id = deterministic_id(
            "patch-proposal", checked_plan.fingerprint, checked_target.fingerprint
        )
        after_content = _sha(
            f"{checked_target.content_fingerprint}:{checked_plan.fingerprint}"
        )
        after_state = ArtifactStateReference.create(
            project_id=checked_target.project_id,
            artifact_id=checked_target.artifact_id,
            revision_id=deterministic_id("repair-revision", after_content),
            content_fingerprint=after_content,
        )
        change_set = ChangeSetReference.create(
            change_set_id=deterministic_id("change-set", checked_plan.fingerprint),
            change_set_fingerprint=_sha(
                f"{checked_plan.fingerprint}:{checked_target.fingerprint}:{after_content}"
            ),
        )
        value = PatchProposal.create(
            patch_id=patch_id,
            project_id=checked_plan.project_id,
            plan_reference=checked_plan.as_reference(),
            target_artifact=checked_target.artifact_id,
            before_state=checked_target,
            after_state=after_state,
            change_set_reference=change_set,
            change_description="Reference-only artifact state transition",
            reason="Implement the reviewed repair plan through an isolated adapter",
            risk=checked_plan.risk,
            evidence_refs=checked_plan.evidence_refs,
            actor=checked_plan.actor,
            created_at=checked_plan.created_at,
        )
        previous = self._values.get(patch_id)
        if previous is not None and previous.fingerprint != value.fingerprint:
            raise RepairConflict("patch identity conflict")
        self._values[patch_id] = value
        return value.detached()


class FakeArtifactSourceAdapter:
    def __init__(self, states: Iterable[ArtifactStateReference] = ()) -> None:
        self._states: dict[tuple[str, str], ArtifactStateReference] = {}
        self.fail_next = False
        for value in states:
            self.put_state(value)

    def put_state(self, state: ArtifactStateReference) -> ArtifactStateReference:
        checked = validate_artifact_state(state)
        self._states[(checked.project_id, checked.artifact_id)] = checked
        return checked.detached()

    def get_state(
        self, project_id: str, artifact_id: str
    ) -> ArtifactStateReference | None:
        if self.fail_next:
            self.fail_next = False
            raise RepairUnavailable("injected artifact source failure")
        value = self._states.get((project_id, artifact_id))
        return None if value is None else value.detached()


class FakeReviewSourceAdapter:
    def __init__(self, records: Iterable[ReviewRecord] = ()) -> None:
        self._records: dict[str, ReviewRecord] = {}
        for value in records:
            checked = validate_legacy_review_record(value)
            self._records[checked.review_request.review_id] = checked

    def get_review_record(self, review_id: str) -> ReviewRecord | None:
        value = self._records.get(review_id)
        return None if value is None else _copy_upstream(value, ReviewRecord)


class FakeRepairReviewAdapter:
    def __init__(self) -> None:
        self._requests: dict[str, RepairReviewRequest] = {}
        self._records: dict[str, RepairReviewRecord] = {}
        self.fail_next_submit = False

    def submit(self, request: RepairReviewRequest) -> RepairReviewRequest:
        checked = validate_review_request(request)
        if self.fail_next_submit:
            self.fail_next_submit = False
            raise RepairUnavailable("injected review submission failure")
        previous = self._requests.get(checked.review_id)
        if previous is not None and previous.fingerprint != checked.fingerprint:
            raise RepairConflict("review request identity conflict")
        self._requests[checked.review_id] = checked
        return checked.detached()

    def record_human_decision(
        self, decision: RepairReviewDecision
    ) -> RepairReviewRecord:
        checked_decision = RepairReviewDecision.model_validate(
            copy.deepcopy(decision.model_dump(mode="python"))
        )
        request = self._requests.get(checked_decision.review_id)
        if request is None:
            raise RepairUnavailable("review request not found")
        record = RepairReviewRecord.create(
            review_request=request,
            decision=checked_decision,
            status=RepairReviewStatus.DECIDED,
        )
        previous = self._records.get(request.review_id)
        if previous is not None and previous.fingerprint != record.fingerprint:
            raise RepairConflict("review decision identity conflict")
        self._records[request.review_id] = record
        return record.detached()

    def get_record(self, review_id: str) -> RepairReviewRecord | None:
        value = self._records.get(review_id)
        return None if value is None else validate_review_record(value)


class FakeApplyAdapter:
    def __init__(self, *, status: ApplyStatus = ApplyStatus.ACCEPTED) -> None:
        self._status = status
        self._receipts: dict[str, ApplyReceipt] = {}
        self.fail_next = False

    def apply(self, request: ApplyRequest) -> ApplyReceipt:
        checked = validate_apply_request(request)
        if self.fail_next:
            self.fail_next = False
            raise RepairUnavailable("injected fake apply failure")
        evidence = EvidenceReference.create(
            evidence_id=deterministic_id("apply-evidence", checked.apply_id),
            source="repair-apply",
            source_fingerprint=checked.fingerprint,
        )
        receipt = ApplyReceipt.create(
            apply_id=checked.apply_id,
            project_id=checked.project_id,
            patch_reference=checked.patch_reference,
            approval_reference=checked.approval_reference,
            status=self._status,
            evidence_refs=(evidence,),
            accepted_at=checked.requested_at,
        )
        previous = self._receipts.get(checked.apply_id)
        if previous is not None and previous.fingerprint != receipt.fingerprint:
            raise RepairConflict("apply receipt identity conflict")
        self._receipts[checked.apply_id] = receipt
        return receipt.detached()


class FakeRepairBuildAdapter:
    def __init__(
        self, *, status: RepairBuildStatus = RepairBuildStatus.SUCCEEDED
    ) -> None:
        self._status = status
        self._receipts: dict[str, RepairBuildReceipt] = {}
        self.fail_next = False

    def build(self, request: RepairBuildRequest) -> RepairBuildReceipt:
        checked = validate_build_request(request)
        if self.fail_next:
            self.fail_next = False
            raise RepairUnavailable("injected fake build failure")
        evidence = EvidenceReference.create(
            evidence_id=deterministic_id("build-evidence", checked.build_id),
            source="repair-build",
            source_fingerprint=checked.fingerprint,
        )
        receipt = RepairBuildReceipt.create(
            build_id=checked.build_id,
            project_id=checked.project_id,
            target_reference=checked.target_reference,
            apply_reference=checked.apply_reference,
            status=self._status,
            evidence_refs=(evidence,),
            completed_at=checked.requested_at,
        )
        previous = self._receipts.get(checked.build_id)
        if previous is not None and previous.fingerprint != receipt.fingerprint:
            raise RepairConflict("build receipt identity conflict")
        self._receipts[checked.build_id] = receipt
        return receipt.detached()


class FakeVerificationAdapter:
    def __init__(
        self,
        *,
        status: RepairVerificationStatus = RepairVerificationStatus.PASS,
    ) -> None:
        self._status = status
        self._receipts: dict[str, RepairVerificationReceipt] = {}
        self.fail_next = False

    def verify(
        self, request: RepairVerificationRequest
    ) -> RepairVerificationReceipt:
        checked = validate_verification_request(request)
        if self.fail_next:
            self.fail_next = False
            raise RepairUnavailable("injected fake verification failure")
        evidence = EvidenceReference.create(
            evidence_id=deterministic_id(
                "verification-evidence", checked.verification_id
            ),
            source="repair-verification",
            source_fingerprint=checked.fingerprint,
        )
        receipt = RepairVerificationReceipt.create(
            verification_id=checked.verification_id,
            project_id=checked.project_id,
            target_reference=checked.target_reference,
            status=self._status,
            evidence_refs=(evidence,),
            summary="Offline verification projection completed",
            verified_at=checked.requested_at,
        )
        previous = self._receipts.get(checked.verification_id)
        if previous is not None and previous.fingerprint != receipt.fingerprint:
            raise RepairConflict("verification receipt identity conflict")
        self._receipts[checked.verification_id] = receipt
        return receipt.detached()


class FakeRepairAuditSinkAdapter:
    def __init__(self) -> None:
        self._events: dict[str, RepairEvent] = {}
        self._ordered_ids: list[str] = []
        self._latest_by_project: dict[str, datetime] = {}
        self.fail_next_append = False

    def append(self, event: RepairEvent) -> RepairEvent:
        checked = validate_repair_event(event)
        if self.fail_next_append:
            self.fail_next_append = False
            raise RepairUnavailable("injected repair audit failure")
        previous = self._events.get(checked.event_id)
        if previous is not None:
            if previous.fingerprint != checked.fingerprint:
                raise RepairConflict("repair event identity conflict")
            return previous.detached()
        latest = self._latest_by_project.get(checked.project_id)
        if latest is not None and checked.occurred_at < latest:
            raise RepairConflict("repair audit timestamp moved backwards")
        self._events[checked.event_id] = checked
        self._ordered_ids.append(checked.event_id)
        self._latest_by_project[checked.project_id] = checked.occurred_at
        return checked.detached()

    def get_events(self, project_id: str) -> tuple[RepairEvent, ...]:
        return tuple(
            self._events[event_id].detached()
            for event_id in self._ordered_ids
            if self._events[event_id].project_id == project_id
        )


__all__ = (
    "FakeApplyAdapter",
    "FakeArtifactSourceAdapter",
    "FakePatchGenerationAdapter",
    "FakeRepairAnalysisAdapter",
    "FakeRepairAuditSinkAdapter",
    "FakeRepairBuildAdapter",
    "FakeRepairProposalAdapter",
    "FakeRepairReviewAdapter",
    "FakeReviewSourceAdapter",
    "FakeVerificationAdapter",
    "FixedRepairClock",
)
