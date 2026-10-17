from __future__ import annotations

import hashlib

from .contracts import (
    validate_evidence_bundle,
    validate_report,
    validate_review_record,
    validate_review_reference,
    validate_risk_request,
    validate_trail,
)
from .exceptions import (
    AuditConflict,
    AuditCreationError,
    AuditUnavailable,
    AuditWriteFailed,
    ComplianceReportError,
    InvalidAuditEvent,
    ReviewRecordNotFound,
)
from .models import (
    AuditDecision,
    AuditEvent,
    AuditEventType,
    AuditSourceType,
    AuditTrail,
    EvidenceCoverageStatus,
    ReviewRecordReference,
    RiskAuditRequest,
    project_audit_id,
    utc_timestamp,
)
from .ports import (
    AuditClockPort,
    AuditStorePort,
    EvidenceSourcePort,
    ReportGeneratorPort,
    ReviewRecordSourcePort,
)

_REVIEW_DECISION = {
    "APPROVE": (AuditEventType.REVIEW_APPROVED, AuditDecision.APPROVE),
    "REJECT": (AuditEventType.REVIEW_REJECTED, AuditDecision.REJECT),
    "REQUEST_CHANGE": (
        AuditEventType.REVIEW_DEFERRED,
        AuditDecision.REQUEST_CHANGE,
    ),
}


def _event_id(review_fingerprint: str, event_type: AuditEventType) -> str:
    material = f"{review_fingerprint}|{event_type.value}".encode()
    return "event-" + hashlib.sha256(material).hexdigest()[:48]


def _risk_event_id(request_fingerprint: str) -> str:
    return "event-" + hashlib.sha256(request_fingerprint.encode("utf-8")).hexdigest()[:48]


class AuditService:
    def __init__(
        self,
        *,
        source: ReviewRecordSourcePort,
        evidence_source: EvidenceSourcePort,
        store: AuditStorePort,
    ) -> None:
        self._source = source
        self._evidence_source = evidence_source
        self._store = store

    def _load_record(self, reference: ReviewRecordReference):
        try:
            raw = self._source.get_review_record(reference.review_id)
        except Exception as exc:
            raise AuditUnavailable("review record source is unavailable") from exc
        if raw is None:
            raise ReviewRecordNotFound("review record was not found")
        try:
            record = validate_review_record(raw)
        except Exception as exc:
            raise AuditCreationError("review record failed revalidation") from exc
        if (
            record.review_request.review_id != reference.review_id
            or record.fingerprint != reference.fingerprint
        ):
            raise AuditCreationError("review record reference mismatch")
        return record

    def _evidence(
        self,
        *,
        project_id: str,
        source_type: AuditSourceType,
        source_id: str,
        source_fingerprint: str,
    ) -> tuple[tuple[str, ...], EvidenceCoverageStatus]:
        try:
            raw = self._evidence_source.get_evidence(
                project_id, source_type, source_id, source_fingerprint
            )
        except Exception as exc:
            raise AuditUnavailable("evidence source is unavailable") from exc
        if raw is None:
            return (), EvidenceCoverageStatus.MISSING
        try:
            bundle = validate_evidence_bundle(raw)
        except Exception as exc:
            raise AuditCreationError("evidence bundle failed revalidation") from exc
        if (
            bundle.project_id != project_id
            or bundle.source_type is not source_type
            or bundle.source_id != source_id
            or bundle.source_fingerprint != source_fingerprint
        ):
            raise AuditCreationError("evidence bundle identity mismatch")
        return bundle.evidence_refs, bundle.status

    @staticmethod
    def _provenance(record) -> tuple[str, str]:
        request = record.review_request
        target = request.target_reference
        return (
            f"review://{request.review_id}/{record.fingerprint}",
            f"target://{target.target_type.value.lower()}/{target.target_id}/{target.target_fingerprint}",
        )

    def _append(self, event: AuditEvent) -> AuditTrail:
        try:
            trail = self._store.append(event)
        except (AuditConflict, AuditWriteFailed):
            raise
        except Exception as exc:
            raise AuditWriteFailed("audit store append failed") from exc
        try:
            checked = validate_trail(trail)
        except Exception as exc:
            raise AuditWriteFailed("audit store returned an invalid trail") from exc
        if (
            checked.audit_id != event.audit_id
            or checked.project_id != event.project_id
            or not any(saved == event for saved in checked.events)
        ):
            raise AuditWriteFailed("audit store returned a mismatched trail")
        return checked

    def ingest_review(self, reference: ReviewRecordReference) -> AuditTrail:
        try:
            checked_reference = validate_review_reference(reference)
        except Exception as exc:
            raise AuditCreationError("review reference is invalid") from exc
        record = self._load_record(checked_reference)
        request = record.review_request
        target = request.target_reference
        source_type = AuditSourceType(target.target_type.value)
        engineering_refs, evidence_status = self._evidence(
            project_id=request.project_id,
            source_type=source_type,
            source_id=target.target_id,
            source_fingerprint=target.target_fingerprint,
        )
        evidence_refs = tuple(sorted((*self._provenance(record), *engineering_refs)))
        audit_id = project_audit_id(request.project_id)
        created = AuditEvent.create(
            event_id=_event_id(record.fingerprint, AuditEventType.REVIEW_CREATED),
            audit_id=audit_id,
            project_id=request.project_id,
            event_type=AuditEventType.REVIEW_CREATED,
            source_type=source_type,
            source_id=target.target_id,
            review_reference=checked_reference,
            actor_id=request.requester,
            decision=AuditDecision.PENDING,
            reason="Review request created.",
            evidence_refs=evidence_refs,
            evidence_status=evidence_status,
            timestamp=request.created_at,
        )
        decision_value = record.decision.decision.value
        if decision_value not in _REVIEW_DECISION:
            raise InvalidAuditEvent("unknown review decision")
        event_type, audit_decision = _REVIEW_DECISION[decision_value]
        decided = AuditEvent.create(
            event_id=_event_id(record.fingerprint, event_type),
            audit_id=audit_id,
            project_id=request.project_id,
            event_type=event_type,
            source_type=source_type,
            source_id=target.target_id,
            review_reference=checked_reference,
            actor_id=record.decision.reviewer,
            decision=audit_decision,
            reason=record.decision.comment,
            evidence_refs=evidence_refs,
            evidence_status=evidence_status,
            timestamp=record.decision.decided_at,
        )
        self._append(created)
        return self._append(decided)

    def append_risk(self, request: RiskAuditRequest) -> AuditTrail:
        try:
            checked_request = validate_risk_request(request)
        except Exception as exc:
            raise InvalidAuditEvent("risk audit request is invalid") from exc
        record = self._load_record(checked_request.review_reference)
        review = record.review_request
        target = review.target_reference
        source_type = AuditSourceType(target.target_type.value)
        if checked_request.timestamp < record.decision.decided_at:
            raise AuditCreationError("risk timestamp precedes the review decision")
        audit_id = project_audit_id(review.project_id)
        try:
            raw_trail = self._store.get_trail(audit_id)
        except Exception as exc:
            raise AuditUnavailable("audit store is unavailable") from exc
        if raw_trail is None:
            raise AuditCreationError("risk event requires ingested review history")
        try:
            trail = validate_trail(raw_trail)
        except Exception as exc:
            raise AuditCreationError("stored audit trail failed revalidation") from exc
        linked = tuple(
            event
            for event in trail.events
            if event.review_reference == checked_request.review_reference
        )
        if not any(event.event_type is AuditEventType.REVIEW_CREATED for event in linked) or not any(
            event.event_type
            in {
                AuditEventType.REVIEW_APPROVED,
                AuditEventType.REVIEW_REJECTED,
                AuditEventType.REVIEW_DEFERRED,
            }
            for event in linked
        ):
            raise AuditCreationError("risk event requires linked review events")
        same_target = tuple(
            event
            for event in trail.events
            if event.source_type is source_type and event.source_id == target.target_id
        )
        if checked_request.event_type is AuditEventType.RISK_REMAINED:
            if not any(
                event.event_type is AuditEventType.RISK_IDENTIFIED
                for event in same_target
            ):
                raise AuditCreationError("remaining risk requires prior identification")
            cycles = {
                event.review_reference.review_id
                for event in same_target
                if event.event_type
                in {
                    AuditEventType.REVIEW_APPROVED,
                    AuditEventType.REVIEW_REJECTED,
                    AuditEventType.REVIEW_DEFERRED,
                }
            }
            if len(cycles) < 2:
                raise AuditCreationError("remaining risk requires two review cycles")

        engineering_refs, evidence_status = self._evidence(
            project_id=review.project_id,
            source_type=source_type,
            source_id=target.target_id,
            source_fingerprint=target.target_fingerprint,
        )
        event = AuditEvent.create(
            event_id=_risk_event_id(checked_request.fingerprint),
            audit_id=audit_id,
            project_id=review.project_id,
            event_type=checked_request.event_type,
            source_type=source_type,
            source_id=target.target_id,
            review_reference=checked_request.review_reference,
            actor_id=checked_request.actor_id,
            decision=AuditDecision(checked_request.event_type.value),
            reason=checked_request.reason,
            evidence_refs=tuple(
                sorted((*self._provenance(record), *engineering_refs))
            ),
            evidence_status=evidence_status,
            timestamp=checked_request.timestamp,
        )
        return self._append(event)


class ComplianceReportService:
    def __init__(
        self, *, generator: ReportGeneratorPort, clock: AuditClockPort
    ) -> None:
        self._generator = generator
        self._clock = clock

    def generate(self, trail: AuditTrail):
        try:
            checked_trail = validate_trail(trail)
            generated_at = utc_timestamp(self._clock.now(), field="generated_at")
            report = validate_report(
                self._generator.generate(checked_trail, generated_at)
            )
        except Exception as exc:
            raise ComplianceReportError("compliance report generation failed") from exc
        if (
            report.audit_id != checked_trail.audit_id
            or report.project_id != checked_trail.project_id
            or report.audit_trail_fingerprint != checked_trail.fingerprint
            or report.generated_at != generated_at
        ):
            raise ComplianceReportError("compliance report binding mismatch")
        return report

