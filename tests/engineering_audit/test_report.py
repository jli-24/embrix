from __future__ import annotations

from datetime import timedelta

import pytest

from embedded_copilot.engineering_audit import (
    AuditEventType,
    AuditReportStatus,
    AuditService,
    ComplianceReportError,
    ComplianceReportService,
    EngineeringAuditReport,
    EvidenceCoverageStatus,
    RiskAuditRequest,
    UnresolvedItemType,
)
from embedded_copilot.engineering_audit.adapters import (
    FakeAuditClockAdapter,
    FakeAuditStoreAdapter,
    FakeEvidenceSourceAdapter,
    FakeReportGeneratorAdapter,
    FakeReviewRecordSourceAdapter,
)
from embedded_copilot.engineering_review import ReviewDecision

from ._factories import (
    NOW,
    audit_event,
    audit_trail,
    digest,
    evidence_bundle,
    review_record,
    review_reference,
)

GENERATED = NOW + timedelta(days=1)


def generate_report(trail, *, generated_at=GENERATED):
    service = ComplianceReportService(
        generator=FakeReportGeneratorAdapter(),
        clock=FakeAuditClockAdapter(generated_at),
    )
    return service.generate(trail)


@pytest.mark.parametrize(
    ("decision", "field"),
    (
        (ReviewDecision.APPROVE, "approved"),
        (ReviewDecision.REJECT, "rejected"),
        (ReviewDecision.REQUEST_CHANGE, "deferred"),
    ),
)
def test_review_summary_counts_each_human_decision(
    decision: ReviewDecision, field: str
) -> None:
    record = review_record(decision=decision)
    source = FakeReviewRecordSourceAdapter((record,))
    evidence = FakeEvidenceSourceAdapter((evidence_bundle(record),))
    store = FakeAuditStoreAdapter()
    trail = AuditService(source=source, evidence_source=evidence, store=store).ingest_review(
        review_reference(record)
    )
    report = generate_report(trail)
    assert report.review_summary.total_reviews == 1
    assert getattr(report.review_summary, field) == 1
    assert report.review_summary.pending == 0


def test_complete_report_binds_audit_project_trail_and_clock() -> None:
    record = review_record()
    service = AuditService(
        source=FakeReviewRecordSourceAdapter((record,)),
        evidence_source=FakeEvidenceSourceAdapter((evidence_bundle(record),)),
        store=FakeAuditStoreAdapter(),
    )
    trail = service.ingest_review(review_reference(record))
    report = generate_report(trail)
    assert report.audit_id == trail.audit_id
    assert report.project_id == trail.project_id
    assert report.audit_trail_fingerprint == trail.fingerprint
    assert report.generated_at == GENERATED
    assert report.status is AuditReportStatus.COMPLETE
    assert report.fingerprint.startswith("sha256:")


def test_evidence_summary_reports_complete_coverage() -> None:
    created = audit_event()
    decided = audit_event(
        event_type=AuditEventType.REVIEW_APPROVED,
        timestamp=created.timestamp + timedelta(minutes=10),
    )
    report = generate_report(audit_trail(created, decided))
    assert report.evidence_summary.total_events == 2
    assert report.evidence_summary.covered_events == 2
    assert report.evidence_summary.missing_events == 0
    assert report.evidence_summary.coverage_ratio == 1.0
    assert report.evidence_summary.status is EvidenceCoverageStatus.COMPLETE


def test_missing_evidence_is_warning_and_audit_incomplete() -> None:
    created = audit_event(evidence_status=EvidenceCoverageStatus.MISSING)
    decided = audit_event(
        event_type=AuditEventType.REVIEW_APPROVED,
        evidence_status=EvidenceCoverageStatus.MISSING,
        timestamp=created.timestamp + timedelta(minutes=10),
    )
    report = generate_report(audit_trail(created, decided))
    assert report.evidence_summary.missing_events == 2
    assert report.evidence_summary.coverage_ratio == 0.0
    assert report.evidence_summary.status is EvidenceCoverageStatus.MISSING
    assert report.status is AuditReportStatus.AUDIT_INCOMPLETE
    assert any(
        item.item_type is UnresolvedItemType.MISSING_EVIDENCE
        for item in report.unresolved_items
    )


def test_created_without_decision_is_pending_and_incomplete() -> None:
    report = generate_report(audit_trail(audit_event()))
    assert report.review_summary.total_reviews == 1
    assert report.review_summary.pending == 1
    assert report.status is AuditReportStatus.AUDIT_INCOMPLETE
    assert any(
        item.item_type is UnresolvedItemType.PENDING_REVIEW
        for item in report.unresolved_items
    )


def two_cycle_trail(*, final_decision=ReviewDecision.REQUEST_CHANGE):
    first = review_record(decision=ReviewDecision.REJECT)
    second = review_record(
        index=2,
        decision=final_decision,
        target_id=first.review_request.target_reference.target_id,
    )
    service = AuditService(
        source=FakeReviewRecordSourceAdapter((first, second)),
        evidence_source=FakeEvidenceSourceAdapter(
            (evidence_bundle(first), evidence_bundle(second))
        ),
        store=FakeAuditStoreAdapter(),
    )
    service.ingest_review(review_reference(first))
    trail = service.ingest_review(review_reference(second))
    return first, second, service, trail


def test_repeated_unresolved_reviews_create_persistent_risk_summary() -> None:
    _, _, _, trail = two_cycle_trail()
    report = generate_report(trail)
    assert len(report.risk_summary.persistent_risks) == 1
    risk = report.risk_summary.persistent_risks[0]
    assert risk.review_cycles == 2
    assert risk.source_id == "finding-1"
    assert report.status is AuditReportStatus.AUDIT_INCOMPLETE


def test_explicit_risk_events_contribute_counts_and_reason() -> None:
    first, second, service, _ = two_cycle_trail()
    identified = RiskAuditRequest.create(
        request_id="risk-identified-1",
        review_reference=review_reference(first),
        event_type=AuditEventType.RISK_IDENTIFIED,
        actor_id="risk-reviewer-1",
        reason="Power rail risk identified.",
        timestamp=first.decision.decided_at + timedelta(minutes=1),
    )
    # Rebuild in chronological order: first review, identified, second review, remained.
    service = AuditService(
        source=FakeReviewRecordSourceAdapter((first, second)),
        evidence_source=FakeEvidenceSourceAdapter(
            (evidence_bundle(first), evidence_bundle(second))
        ),
        store=FakeAuditStoreAdapter(),
    )
    service.ingest_review(review_reference(first))
    service.append_risk(identified)
    service.ingest_review(review_reference(second))
    trail = service.append_risk(
        RiskAuditRequest.create(
            request_id="risk-remained-2",
            review_reference=review_reference(second),
            event_type=AuditEventType.RISK_REMAINED,
            actor_id="risk-reviewer-2",
            reason="Power rail risk remains unresolved.",
            timestamp=second.decision.decided_at + timedelta(minutes=1),
        )
    )
    report = generate_report(trail)
    assert report.risk_summary.identified_events == 1
    assert report.risk_summary.remained_events == 1
    assert report.risk_summary.persistent_risks[0].reason == (
        "Power rail risk remains unresolved."
    )


def test_later_approval_resolves_current_persistent_risk() -> None:
    first = review_record(decision=ReviewDecision.REJECT)
    second = review_record(
        index=2,
        decision=ReviewDecision.REQUEST_CHANGE,
        target_id="finding-1",
    )
    third = review_record(
        index=3,
        decision=ReviewDecision.APPROVE,
        target_id="finding-1",
    )
    service = AuditService(
        source=FakeReviewRecordSourceAdapter((first, second, third)),
        evidence_source=FakeEvidenceSourceAdapter(
            (
                evidence_bundle(first),
                evidence_bundle(second),
                evidence_bundle(third),
            )
        ),
        store=FakeAuditStoreAdapter(),
    )
    service.ingest_review(review_reference(first))
    service.ingest_review(review_reference(second))
    trail = service.ingest_review(review_reference(third))
    report = generate_report(trail)
    assert report.risk_summary.persistent_risks == ()
    assert not any(
        item.item_type is UnresolvedItemType.PERSISTENT_RISK
        for item in report.unresolved_items
    )


def test_report_never_uses_pass_as_compliance_status() -> None:
    report = generate_report(audit_trail(audit_event()))
    assert "PASS" not in report.model_dump_json()


def test_report_and_nested_summaries_are_frozen() -> None:
    report = generate_report(audit_trail(audit_event()))
    with pytest.raises((TypeError, ValueError)):
        report.project_id = "changed"  # type: ignore[misc]
    with pytest.raises((TypeError, ValueError)):
        report.review_summary.pending = 0  # type: ignore[misc]


def test_compliance_service_rejects_generator_trail_mismatch() -> None:
    trail = audit_trail(audit_event())
    base = FakeReportGeneratorAdapter()

    class WrongGenerator:
        def generate(self, audit_trail, generated_at):
            report = base.generate(audit_trail, generated_at)
            return EngineeringAuditReport.create(
                **{
                    **report.model_dump(mode="python"),
                    "audit_trail_fingerprint": digest("wrong-trail"),
                }
            )

    service = ComplianceReportService(
        generator=WrongGenerator(), clock=FakeAuditClockAdapter(GENERATED)
    )
    with pytest.raises(ComplianceReportError):
        service.generate(trail)


def test_compliance_service_maps_generator_failure() -> None:
    class FailingGenerator:
        def generate(self, audit_trail, generated_at):
            raise RuntimeError("generator failed")

    service = ComplianceReportService(
        generator=FailingGenerator(), clock=FakeAuditClockAdapter(GENERATED)
    )
    with pytest.raises(ComplianceReportError):
        service.generate(audit_trail(audit_event()))


def test_compliance_service_rejects_non_utc_clock_output() -> None:
    class BadClock:
        def now(self):
            return GENERATED.replace(tzinfo=None)

    service = ComplianceReportService(
        generator=FakeReportGeneratorAdapter(), clock=BadClock()
    )
    with pytest.raises(ComplianceReportError):
        service.generate(audit_trail(audit_event()))


def test_report_fingerprint_rejects_tampering() -> None:
    report = generate_report(audit_trail(audit_event()))
    with pytest.raises(ValueError):
        EngineeringAuditReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "fingerprint": "sha256:" + "f" * 64,
            }
        )

