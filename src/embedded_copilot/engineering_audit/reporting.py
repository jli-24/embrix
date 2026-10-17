from __future__ import annotations

import hashlib
from collections import defaultdict

from .contracts import validate_generated_at, validate_trail
from .models import (
    AuditDecision,
    AuditEvent,
    AuditEventType,
    AuditReportStatus,
    EngineeringAuditReport,
    EvidenceCoverageStatus,
    EvidenceSummary,
    PersistentRisk,
    ReviewSummary,
    RiskSummary,
    UnresolvedItem,
    UnresolvedItemType,
)

_DECISION_TYPES = frozenset(
    {
        AuditEventType.REVIEW_APPROVED,
        AuditEventType.REVIEW_REJECTED,
        AuditEventType.REVIEW_DEFERRED,
    }
)


def _stable_id(prefix: str, *parts: str) -> str:
    encoded = "|".join(parts).encode("utf-8")
    return f"{prefix}-" + hashlib.sha256(encoded).hexdigest()[:48]


def _persistent_risks(events: tuple[AuditEvent, ...]) -> tuple[PersistentRisk, ...]:
    decisions: dict[tuple[object, str], list[AuditEvent]] = defaultdict(list)
    explicit_risks: dict[tuple[object, str], list[AuditEvent]] = defaultdict(list)
    for event in events:
        key = (event.source_type, event.source_id)
        if event.event_type in _DECISION_TYPES:
            decisions[key].append(event)
        elif event.event_type in {
            AuditEventType.RISK_IDENTIFIED,
            AuditEventType.RISK_REMAINED,
        }:
            explicit_risks[key].append(event)

    results: list[PersistentRisk] = []
    for key, review_events in decisions.items():
        cycles = len({event.review_reference.review_id for event in review_events})
        latest = review_events[-1]
        if cycles < 2 or latest.decision is AuditDecision.APPROVE:
            continue
        risk_events = explicit_risks.get(key, [])
        reason_event = risk_events[-1] if risk_events else latest
        evidence = tuple(
            sorted(
                {
                    reference
                    for event in (*review_events, *risk_events)
                    for reference in event.evidence_refs
                }
            )
        )
        results.append(
            PersistentRisk.create(
                risk_id=_stable_id("risk", key[0].value, key[1]),
                source_type=key[0],
                source_id=key[1],
                review_cycles=cycles,
                first_seen_at=review_events[0].timestamp,
                last_seen_at=latest.timestamp,
                reason=reason_event.reason,
                evidence_refs=evidence,
            )
        )
    return tuple(sorted(results, key=lambda item: item.risk_id))


def build_audit_report(
    trail: object, generated_at: object
) -> EngineeringAuditReport:
    checked_trail = validate_trail(trail)
    checked_time = validate_generated_at(generated_at)
    if checked_time < checked_trail.events[-1].timestamp:
        raise ValueError("report generation time precedes the audit trail")

    created = tuple(
        event
        for event in checked_trail.events
        if event.event_type is AuditEventType.REVIEW_CREATED
    )
    decisions = tuple(
        event for event in checked_trail.events if event.event_type in _DECISION_TYPES
    )
    decided_ids = {event.review_reference.review_id for event in decisions}
    approved = sum(event.event_type is AuditEventType.REVIEW_APPROVED for event in decisions)
    rejected = sum(event.event_type is AuditEventType.REVIEW_REJECTED for event in decisions)
    deferred = sum(event.event_type is AuditEventType.REVIEW_DEFERRED for event in decisions)
    pending_events = tuple(
        event
        for event in created
        if event.review_reference.review_id not in decided_ids
    )
    review_summary = ReviewSummary(
        total_reviews=len(created),
        approved=approved,
        rejected=rejected,
        deferred=deferred,
        pending=len(pending_events),
    )

    missing = tuple(
        event
        for event in checked_trail.events
        if event.evidence_status is EvidenceCoverageStatus.MISSING
    )
    total_events = len(checked_trail.events)
    covered = total_events - len(missing)
    evidence_summary = EvidenceSummary(
        total_events=total_events,
        covered_events=covered,
        missing_events=len(missing),
        coverage_ratio=covered / total_events,
        status=(
            EvidenceCoverageStatus.MISSING
            if missing
            else EvidenceCoverageStatus.COMPLETE
        ),
    )

    persistent = _persistent_risks(checked_trail.events)
    risk_summary = RiskSummary(
        identified_events=sum(
            event.event_type is AuditEventType.RISK_IDENTIFIED
            for event in checked_trail.events
        ),
        remained_events=sum(
            event.event_type is AuditEventType.RISK_REMAINED
            for event in checked_trail.events
        ),
        persistent_risks=persistent,
    )

    unresolved: list[UnresolvedItem] = []
    for event in pending_events:
        unresolved.append(
            UnresolvedItem.create(
                item_id=_stable_id("unresolved", "pending", event.event_id),
                item_type=UnresolvedItemType.PENDING_REVIEW,
                source_type=event.source_type,
                source_id=event.source_id,
                reason="Review has no recorded human decision.",
                evidence_refs=event.evidence_refs,
            )
        )
    for event in missing:
        unresolved.append(
            UnresolvedItem.create(
                item_id=_stable_id("unresolved", "evidence", event.event_id),
                item_type=UnresolvedItemType.MISSING_EVIDENCE,
                source_type=event.source_type,
                source_id=event.source_id,
                reason="Engineering evidence coverage is missing.",
                evidence_refs=event.evidence_refs,
            )
        )
    for risk in persistent:
        unresolved.append(
            UnresolvedItem.create(
                item_id=_stable_id("unresolved", "risk", risk.risk_id),
                item_type=UnresolvedItemType.PERSISTENT_RISK,
                source_type=risk.source_type,
                source_id=risk.source_id,
                reason=risk.reason,
                evidence_refs=risk.evidence_refs,
            )
        )

    status = (
        AuditReportStatus.AUDIT_INCOMPLETE
        if unresolved
        else AuditReportStatus.COMPLETE
    )
    return EngineeringAuditReport.create(
        audit_id=checked_trail.audit_id,
        project_id=checked_trail.project_id,
        audit_trail_fingerprint=checked_trail.fingerprint,
        generated_at=checked_time,
        review_summary=review_summary,
        evidence_summary=evidence_summary,
        risk_summary=risk_summary,
        unresolved_items=tuple(unresolved),
        status=status,
    )

