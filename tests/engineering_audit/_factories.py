from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from embedded_copilot.engineering_audit import (
    AuditDecision,
    AuditEvent,
    AuditEventType,
    AuditSourceType,
    AuditTrail,
    EvidenceBundle,
    EvidenceCoverageStatus,
    ReviewRecordReference,
    project_audit_id,
)
from embedded_copilot.engineering_review import (
    HumanDecision,
    ReviewDecision,
    ReviewRecord,
    ReviewRequest,
    ReviewTargetReference,
    ReviewTargetType,
)

NOW = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)


def digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def review_record(
    index: int = 1,
    *,
    decision: ReviewDecision = ReviewDecision.APPROVE,
    project_id: str = "project-1",
    target_id: str = "finding-1",
    target_type: ReviewTargetType = ReviewTargetType.VALIDATION,
    created_at: datetime | None = None,
) -> ReviewRecord:
    created = created_at or NOW + timedelta(hours=index - 1)
    request = ReviewRequest.create(
        review_id=f"review-{index}",
        project_id=project_id,
        target_reference=ReviewTargetReference.create(
            target_type=target_type,
            target_id=target_id,
            target_fingerprint=digest(f"{target_type.value}:{target_id}"),
        ),
        requester=f"requester-{index}",
        created_at=created,
    )
    human = HumanDecision.create(
        decision_id=f"decision-{index}",
        review_id=request.review_id,
        project_id=project_id,
        decision=decision,
        reviewer=f"reviewer-{index}",
        comment={
            ReviewDecision.APPROVE: "Approved by the human reviewer.",
            ReviewDecision.REJECT: "Rejected by the human reviewer.",
            ReviewDecision.REQUEST_CHANGE: "Changes requested by the human reviewer.",
        }[decision],
        decided_at=created + timedelta(minutes=10),
    )
    return ReviewRecord.create(review_request=request, decision=human)


def review_reference(record: ReviewRecord | None = None) -> ReviewRecordReference:
    value = record or review_record()
    return ReviewRecordReference.create(
        review_id=value.review_request.review_id,
        fingerprint=value.fingerprint,
    )


def evidence_bundle(
    record: ReviewRecord | None = None,
    *,
    status: EvidenceCoverageStatus = EvidenceCoverageStatus.COMPLETE,
    evidence_refs: tuple[str, ...] | None = None,
) -> EvidenceBundle:
    value = record or review_record()
    target = value.review_request.target_reference
    refs = (
        ("evidence://datasheet/power-rule",)
        if evidence_refs is None and status is EvidenceCoverageStatus.COMPLETE
        else () if evidence_refs is None else evidence_refs
    )
    return EvidenceBundle.create(
        project_id=value.review_request.project_id,
        source_type=AuditSourceType(target.target_type.value),
        source_id=target.target_id,
        source_fingerprint=target.target_fingerprint,
        evidence_refs=refs,
        status=status,
    )


def audit_event(
    record: ReviewRecord | None = None,
    *,
    event_type: AuditEventType = AuditEventType.REVIEW_CREATED,
    event_id: str | None = None,
    timestamp: datetime | None = None,
    reason: str | None = None,
    evidence_status: EvidenceCoverageStatus = EvidenceCoverageStatus.COMPLETE,
) -> AuditEvent:
    value = record or review_record()
    request = value.review_request
    target = request.target_reference
    decision_by_type = {
        AuditEventType.REVIEW_CREATED: AuditDecision.PENDING,
        AuditEventType.REVIEW_APPROVED: AuditDecision.APPROVE,
        AuditEventType.REVIEW_REJECTED: AuditDecision.REJECT,
        AuditEventType.REVIEW_DEFERRED: AuditDecision.REQUEST_CHANGE,
        AuditEventType.RISK_IDENTIFIED: AuditDecision.RISK_IDENTIFIED,
        AuditEventType.RISK_REMAINED: AuditDecision.RISK_REMAINED,
    }
    default_timestamp = (
        request.created_at
        if event_type is AuditEventType.REVIEW_CREATED
        else value.decision.decided_at
    )
    default_actor = (
        request.requester
        if event_type is AuditEventType.REVIEW_CREATED
        else value.decision.reviewer
    )
    default_reason = (
        "Review request created."
        if event_type is AuditEventType.REVIEW_CREATED
        else value.decision.comment
    )
    suffix = event_type.value.lower().replace("_", "-")
    refs = (
        f"review://{request.review_id}/{value.fingerprint}",
        (
            f"target://{target.target_type.value.lower()}/{target.target_id}/"
            f"{target.target_fingerprint}"
        ),
    )
    if evidence_status is EvidenceCoverageStatus.COMPLETE:
        refs += ("evidence://datasheet/power-rule",)
    return AuditEvent.create(
        event_id=event_id or f"event-{request.review_id}-{suffix}",
        audit_id=project_audit_id(request.project_id),
        project_id=request.project_id,
        event_type=event_type,
        source_type=AuditSourceType(target.target_type.value),
        source_id=target.target_id,
        review_reference=review_reference(value),
        actor_id=default_actor,
        decision=decision_by_type[event_type],
        reason=reason or default_reason,
        evidence_refs=refs,
        evidence_status=evidence_status,
        timestamp=timestamp or default_timestamp,
    )


def audit_trail(*events: AuditEvent) -> AuditTrail:
    values = events or (audit_event(),)
    return AuditTrail.create(
        audit_id=values[0].audit_id,
        project_id=values[0].project_id,
        events=tuple(values),
        created_at=values[0].timestamp,
    )
