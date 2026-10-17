from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    ValidationInfo,
    model_validator,
)

from embedded_copilot.repair_proposal import RepairProposal

_ZERO_FINGERPRINT = "sha256:" + "0" * 64
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
_DRIVE_PATH = re.compile(r"(?:^|\s)[A-Za-z]:[\\/]")
_SENSITIVE = re.compile(
    r"(?i)(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|passwd|"
    r"credential|private[_ -]?key|client[_ -]?secret|authorization\s*:|bearer\s+)"
)


def _nfc_text(value: object, *, field: str, maximum: int = 2048) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")  # noqa: TRY004
    result = unicodedata.normalize("NFC", value).strip()
    if not result or len(result) > maximum:
        raise ValueError(f"{field} is empty or too long")
    if any(unicodedata.category(char) == "Cc" for char in result):
        raise ValueError(f"{field} contains control characters")
    if _SENSITIVE.search(result) or "-----BEGIN " in result:
        raise ValueError(f"{field} contains sensitive text")
    return result


def _identifier(value: object) -> str:
    result = _nfc_text(value, field="identifier", maximum=128)
    if not _IDENTIFIER.fullmatch(result):
        raise ValueError("identifier is invalid")
    return result


def _fingerprint(value: object) -> str:
    if not isinstance(value, str) or not _FINGERPRINT.fullmatch(value):
        raise ValueError("fingerprint is invalid")
    return value


def _safe_text(value: object) -> str:
    result = _nfc_text(value, field="text")
    if _DRIVE_PATH.search(result) or "file://" in result.lower():
        raise ValueError("text contains a local path")
    return result


def _utc_datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("timestamp must be a datetime")  # noqa: TRY004
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("timestamp must use UTC")
    return value.astimezone(UTC)


Identifier = Annotated[str, BeforeValidator(_identifier)]
Fingerprint = Annotated[str, BeforeValidator(_fingerprint)]
SafeText = Annotated[str, BeforeValidator(_safe_text)]
UtcDatetime = Annotated[datetime, BeforeValidator(_utc_datetime)]


def _canonical(model: BaseModel) -> str:
    payload = model.model_dump(mode="json", exclude={"fingerprint"})
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def deterministic_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:32]}"


class RepairContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        revalidate_instances="always",
    )

    fingerprint: Fingerprint = _ZERO_FINGERPRINT
    _fingerprint_exclude: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def _validate_fingerprint(self, info: ValidationInfo) -> Self:
        if info.context and info.context.get("create_fingerprint"):
            return self
        expected = _canonical_with_exclusions(self)
        if self.fingerprint != expected:
            raise ValueError("fingerprint does not match canonical content")
        return self

    @classmethod
    def create(cls, **values: object) -> Self:
        material = copy.deepcopy(values)
        material.setdefault("fingerprint", _ZERO_FINGERPRINT)
        provisional = cls.model_validate(
            material, context={"create_fingerprint": True}
        )
        checked = provisional.model_dump(mode="python")
        checked["fingerprint"] = _canonical_with_exclusions(provisional)
        return cls.model_validate(copy.deepcopy(checked))

    def detached(self) -> Self:
        return type(self).model_validate(copy.deepcopy(self.model_dump(mode="python")))


def _canonical_with_exclusions(model: RepairContract) -> str:
    excluded = {"fingerprint", *model._fingerprint_exclude}
    payload = model.model_dump(mode="json", exclude=excluded)
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def revalidate(model_type: type[RepairContract], value: object) -> Any:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    return model_type.model_validate(copy.deepcopy(value))


def _unique_evidence(values: tuple[EvidenceReference, ...]) -> tuple[EvidenceReference, ...]:
    if not values:
        raise ValueError("evidence is required")
    identities = tuple(item.evidence_id for item in values)
    if len(identities) != len(set(identities)):
        raise ValueError("evidence identities must be unique")
    return tuple(sorted(values, key=lambda item: item.evidence_id))


class RepairRisk(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RepairReviewDecisionType(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_CHANGE = "REQUEST_CHANGE"


class RepairReviewStatus(StrEnum):
    DECIDED = "DECIDED"


class ApplyStatus(StrEnum):
    ACCEPTED = "ACCEPTED"
    FAILED = "FAILED"


class RepairBuildStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class RepairVerificationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    ESCALATE = "ESCALATE"


class RepairResultStatus(StrEnum):
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"


class RepairEventType(StrEnum):
    PREPARED = "PREPARED"
    REVIEW_SUBMITTED = "REVIEW_SUBMITTED"
    APPROVED = "APPROVED"
    APPLY_ACCEPTED = "APPLY_ACCEPTED"
    BUILD_COMPLETED = "BUILD_COMPLETED"
    VERIFICATION_COMPLETED = "VERIFICATION_COMPLETED"


class EvidenceReference(RepairContract):
    evidence_id: Identifier
    source: Identifier
    source_fingerprint: Fingerprint


class FailureAnalysisReference(RepairContract):
    failure_id: Identifier
    project_id: Identifier
    target_id: Identifier
    failure_fingerprint: Fingerprint


class ArtifactStateReference(RepairContract):
    project_id: Identifier
    artifact_id: Identifier
    revision_id: Identifier
    content_fingerprint: Fingerprint


class LegacyReviewReference(RepairContract):
    review_id: Identifier
    record_fingerprint: Fingerprint


class ExistingRepairProposalReference(RepairContract):
    proposal_id: Identifier
    project_id: Identifier
    failure_id: Identifier
    proposal_fingerprint: Fingerprint


class RepairRequest(RepairContract):
    request_id: Identifier
    project_id: Identifier
    failure_reference: FailureAnalysisReference
    target_reference: ArtifactStateReference
    evidence_refs: tuple[EvidenceReference, ...]
    requester: Identifier
    reason: SafeText
    requested_at: UtcDatetime
    legacy_review_reference: LegacyReviewReference | None = None

    @model_validator(mode="after")
    def _bindings(self) -> Self:
        object.__setattr__(self, "evidence_refs", _unique_evidence(self.evidence_refs))
        if self.failure_reference.project_id != self.project_id:
            raise ValueError("failure reference project mismatch")
        if self.target_reference.project_id != self.project_id:
            raise ValueError("artifact reference project mismatch")
        if self.failure_reference.target_id != self.target_reference.artifact_id:
            raise ValueError("failure target mismatch")
        return self


class RepairPlanReference(RepairContract):
    plan_id: Identifier
    plan_fingerprint: Fingerprint


class RepairPlan(RepairContract):
    plan_id: Identifier
    project_id: Identifier
    request_id: Identifier
    request_fingerprint: Fingerprint
    analysis_reference: FailureAnalysisReference
    existing_proposal: RepairProposal
    existing_proposal_reference: ExistingRepairProposalReference
    risk: RepairRisk
    recommended_changes: tuple[SafeText, ...]
    evidence_refs: tuple[EvidenceReference, ...]
    actor: Identifier
    created_at: UtcDatetime

    @model_validator(mode="after")
    def _bindings(self) -> Self:
        if not self.recommended_changes:
            raise ValueError("recommended changes are required")
        if len(set(self.recommended_changes)) != len(self.recommended_changes):
            raise ValueError("recommended changes must be unique")
        object.__setattr__(self, "evidence_refs", _unique_evidence(self.evidence_refs))
        if type(self.existing_proposal) is not RepairProposal:
            raise ValueError("existing proposal type is invalid")
        if self.analysis_reference.project_id != self.project_id:
            raise ValueError("analysis project mismatch")
        proposal = self.existing_proposal_reference
        if proposal.project_id != self.project_id:
            raise ValueError("proposal project mismatch")
        if proposal.failure_id != self.analysis_reference.failure_id:
            raise ValueError("proposal failure mismatch")
        if proposal.proposal_id != self.existing_proposal.proposal_id:
            raise ValueError("existing proposal identity mismatch")
        if proposal.proposal_fingerprint != self.existing_proposal.fingerprint:
            raise ValueError("existing proposal fingerprint mismatch")
        if self.existing_proposal.project_id != self.project_id:
            raise ValueError("existing proposal project mismatch")
        if self.existing_proposal.failure_id != self.analysis_reference.failure_id:
            raise ValueError("existing proposal failure mismatch")
        return self

    def as_reference(self) -> RepairPlanReference:
        return RepairPlanReference.create(
            plan_id=self.plan_id, plan_fingerprint=self.fingerprint
        )


class ChangeSetReference(RepairContract):
    change_set_id: Identifier
    change_set_fingerprint: Fingerprint


class PatchProposalReference(RepairContract):
    patch_id: Identifier
    patch_fingerprint: Fingerprint


class PatchProposal(RepairContract):
    patch_id: Identifier
    project_id: Identifier
    plan_reference: RepairPlanReference
    target_artifact: Identifier
    before_state: ArtifactStateReference
    after_state: ArtifactStateReference
    change_set_reference: ChangeSetReference
    change_description: SafeText
    reason: SafeText
    risk: RepairRisk
    evidence_refs: tuple[EvidenceReference, ...]
    actor: Identifier
    created_at: UtcDatetime

    @model_validator(mode="after")
    def _safe_patch(self) -> Self:
        object.__setattr__(self, "evidence_refs", _unique_evidence(self.evidence_refs))
        for state in (self.before_state, self.after_state):
            if state.project_id != self.project_id:
                raise ValueError("patch state project mismatch")
            if state.artifact_id != self.target_artifact:
                raise ValueError("patch state artifact mismatch")
        if self.before_state.content_fingerprint == self.after_state.content_fingerprint:
            raise ValueError("patch must describe a state change")
        material = f"{self.change_description}\n{self.reason}".lower()
        forbidden = (
            "diff --git",
            "--- a/",
            "+++ b/",
            "@@ -",
            "```",
            "#include <",
            "#include \"",
            "git apply",
            "patch -p",
        )
        if any(marker in material for marker in forbidden):
            raise ValueError("patch proposal contains directly applicable content")
        return self

    def as_reference(self) -> PatchProposalReference:
        return PatchProposalReference.create(
            patch_id=self.patch_id, patch_fingerprint=self.fingerprint
        )


class RepairPreparation(RepairContract):
    request: RepairRequest
    plan: RepairPlan
    patch: PatchProposal

    @model_validator(mode="after")
    def _bindings(self) -> Self:
        if self.plan.project_id != self.request.project_id:
            raise ValueError("preparation plan project mismatch")
        if self.patch.project_id != self.request.project_id:
            raise ValueError("preparation patch project mismatch")
        if self.plan.request_id != self.request.request_id:
            raise ValueError("preparation request identity mismatch")
        if self.plan.request_fingerprint != self.request.fingerprint:
            raise ValueError("preparation request fingerprint mismatch")
        if self.patch.plan_reference != self.plan.as_reference():
            raise ValueError("preparation plan binding mismatch")
        return self


class RepairReviewRequest(RepairContract):
    review_id: Identifier
    project_id: Identifier
    patch_reference: PatchProposalReference
    requester: Identifier
    reason: SafeText
    created_at: UtcDatetime


class RepairReviewDecision(RepairContract):
    decision_id: Identifier
    review_id: Identifier
    project_id: Identifier
    patch_fingerprint: Fingerprint
    decision: RepairReviewDecisionType
    reviewer: Identifier
    comment: SafeText
    decided_at: UtcDatetime


class RepairReviewRecord(RepairContract):
    review_request: RepairReviewRequest
    decision: RepairReviewDecision
    status: RepairReviewStatus

    @model_validator(mode="after")
    def _bindings(self) -> Self:
        request = self.review_request
        decision = self.decision
        if self.status is not RepairReviewStatus.DECIDED:
            raise ValueError("review record must be decided")
        if decision.review_id != request.review_id:
            raise ValueError("review decision identity mismatch")
        if decision.project_id != request.project_id:
            raise ValueError("review decision project mismatch")
        if decision.patch_fingerprint != request.patch_reference.patch_fingerprint:
            raise ValueError("review decision patch mismatch")
        if decision.decided_at < request.created_at:
            raise ValueError("review decision predates request")
        return self


class HumanApprovalReference(RepairContract):
    review_id: Identifier
    decision_id: Identifier
    project_id: Identifier
    patch_fingerprint: Fingerprint
    reviewer: Identifier
    approved_at: UtcDatetime
    review_record_fingerprint: Fingerprint


class ApplyRequest(RepairContract):
    apply_id: Identifier
    project_id: Identifier
    patch_reference: PatchProposalReference
    approval_reference: HumanApprovalReference
    actor: Identifier
    requested_at: UtcDatetime

    @model_validator(mode="after")
    def _bindings(self) -> Self:
        if self.approval_reference.project_id != self.project_id:
            raise ValueError("approval project mismatch")
        if self.approval_reference.patch_fingerprint != self.patch_reference.patch_fingerprint:
            raise ValueError("approval patch mismatch")
        if self.requested_at < self.approval_reference.approved_at:
            raise ValueError("apply request predates approval")
        return self


class ApplyReceiptReference(RepairContract):
    apply_id: Identifier
    receipt_fingerprint: Fingerprint


class ApplyReceipt(RepairContract):
    apply_id: Identifier
    project_id: Identifier
    patch_reference: PatchProposalReference
    approval_reference: HumanApprovalReference
    status: ApplyStatus
    evidence_refs: tuple[EvidenceReference, ...]
    accepted_at: UtcDatetime

    @model_validator(mode="after")
    def _evidence(self) -> Self:
        object.__setattr__(self, "evidence_refs", _unique_evidence(self.evidence_refs))
        if self.approval_reference.project_id != self.project_id:
            raise ValueError("apply receipt approval project mismatch")
        if self.approval_reference.patch_fingerprint != self.patch_reference.patch_fingerprint:
            raise ValueError("apply receipt approval patch mismatch")
        return self

    def as_reference(self) -> ApplyReceiptReference:
        return ApplyReceiptReference.create(
            apply_id=self.apply_id, receipt_fingerprint=self.fingerprint
        )


class RepairBuildRequest(RepairContract):
    build_id: Identifier
    project_id: Identifier
    target_reference: ArtifactStateReference
    apply_reference: ApplyReceiptReference
    actor: Identifier
    requested_at: UtcDatetime

    @model_validator(mode="after")
    def _project(self) -> Self:
        if self.target_reference.project_id != self.project_id:
            raise ValueError("build target project mismatch")
        return self


class BuildReceiptReference(RepairContract):
    build_id: Identifier
    receipt_fingerprint: Fingerprint


class RepairBuildReceipt(RepairContract):
    build_id: Identifier
    project_id: Identifier
    target_reference: ArtifactStateReference
    apply_reference: ApplyReceiptReference
    status: RepairBuildStatus
    evidence_refs: tuple[EvidenceReference, ...]
    completed_at: UtcDatetime

    @model_validator(mode="after")
    def _bindings(self) -> Self:
        object.__setattr__(self, "evidence_refs", _unique_evidence(self.evidence_refs))
        if self.target_reference.project_id != self.project_id:
            raise ValueError("build receipt project mismatch")
        return self

    def as_reference(self) -> BuildReceiptReference:
        return BuildReceiptReference.create(
            build_id=self.build_id, receipt_fingerprint=self.fingerprint
        )


class RepairVerificationRequest(RepairContract):
    verification_id: Identifier
    project_id: Identifier
    target_reference: ArtifactStateReference
    prior_failure_reference: FailureAnalysisReference
    patch_reference: PatchProposalReference
    apply_reference: ApplyReceiptReference
    build_reference: BuildReceiptReference
    requested_at: UtcDatetime

    @model_validator(mode="after")
    def _bindings(self) -> Self:
        if self.target_reference.project_id != self.project_id:
            raise ValueError("verification target project mismatch")
        if self.prior_failure_reference.project_id != self.project_id:
            raise ValueError("verification failure project mismatch")
        return self


class VerificationReference(RepairContract):
    verification_id: Identifier
    receipt_fingerprint: Fingerprint


class RepairVerificationReceipt(RepairContract):
    verification_id: Identifier
    project_id: Identifier
    target_reference: ArtifactStateReference
    status: RepairVerificationStatus
    evidence_refs: tuple[EvidenceReference, ...]
    summary: SafeText
    verified_at: UtcDatetime

    @model_validator(mode="after")
    def _bindings(self) -> Self:
        object.__setattr__(self, "evidence_refs", _unique_evidence(self.evidence_refs))
        if self.target_reference.project_id != self.project_id:
            raise ValueError("verification receipt project mismatch")
        return self

    def as_reference(self) -> VerificationReference:
        return VerificationReference.create(
            verification_id=self.verification_id,
            receipt_fingerprint=self.fingerprint,
        )


class RepairResult(RepairContract):
    result_id: Identifier
    project_id: Identifier
    request_id: Identifier
    request_fingerprint: Fingerprint
    plan_reference: RepairPlanReference
    patch_reference: PatchProposalReference
    approval_reference: HumanApprovalReference
    apply_reference: ApplyReceiptReference
    build_reference: BuildReceiptReference
    verification_reference: VerificationReference
    status: RepairResultStatus
    evidence_refs: tuple[EvidenceReference, ...]
    completed_at: UtcDatetime

    @model_validator(mode="after")
    def _evidence(self) -> Self:
        object.__setattr__(self, "evidence_refs", _unique_evidence(self.evidence_refs))
        if self.approval_reference.project_id != self.project_id:
            raise ValueError("result approval project mismatch")
        if self.approval_reference.patch_fingerprint != self.patch_reference.patch_fingerprint:
            raise ValueError("result approval patch mismatch")
        return self


class RepairEvent(RepairContract):
    event_id: Identifier
    event_type: RepairEventType
    project_id: Identifier
    subject_id: Identifier
    subject_fingerprint: Fingerprint
    actor: Identifier
    occurred_at: UtcDatetime
    evidence_refs: tuple[EvidenceReference, ...]

    @model_validator(mode="after")
    def _evidence(self) -> Self:
        object.__setattr__(self, "evidence_refs", _unique_evidence(self.evidence_refs))
        return self


__all__ = tuple(
    name
    for name in globals()
    if name.startswith("Repair")
    or name.endswith("Reference")
    or name in {
        "ApplyRequest",
        "ApplyReceipt",
        "ApplyStatus",
        "BuildReceiptReference",
        "ChangeSetReference",
        "EvidenceReference",
        "FailureAnalysisReference",
        "HumanApprovalReference",
        "LegacyReviewReference",
        "PatchProposal",
        "PatchProposalReference",
        "UtcDatetime",
        "deterministic_id",
        "revalidate",
    }
)
