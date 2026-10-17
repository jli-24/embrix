from __future__ import annotations

from datetime import timedelta

import pytest

from embedded_copilot.engineering_audit import (
    AuditCreationError,
    AuditDecision,
    AuditEventType,
    AuditService,
    AuditWriteFailed,
    EvidenceCoverageStatus,
    InvalidAuditEvent,
    ReviewRecordNotFound,
    ReviewRecordReference,
    RiskAuditRequest,
)
from embedded_copilot.engineering_audit.adapters import (
    FakeAuditStoreAdapter,
    FakeEvidenceSourceAdapter,
    FakeReviewRecordSourceAdapter,
)
from embedded_copilot.engineering_review import ReviewDecision

from ._factories import (
    digest,
    evidence_bundle,
    review_record,
    review_reference,
)


def audit_service(
    records,
    bundles=(),
    *,
    fail_on_attempts: tuple[int, ...] = (),
):
    source = FakeReviewRecordSourceAdapter(tuple(records))
    evidence = FakeEvidenceSourceAdapter(tuple(bundles))
    store = FakeAuditStoreAdapter(fail_on_attempts=fail_on_attempts)
    return AuditService(source=source, evidence_source=evidence, store=store), store


@pytest.mark.parametrize(
    ("decision", "event_type", "audit_decision"),
    (
        (
            ReviewDecision.APPROVE,
            AuditEventType.REVIEW_APPROVED,
            AuditDecision.APPROVE,
        ),
        (
            ReviewDecision.REJECT,
            AuditEventType.REVIEW_REJECTED,
            AuditDecision.REJECT,
        ),
        (
            ReviewDecision.REQUEST_CHANGE,
            AuditEventType.REVIEW_DEFERRED,
            AuditDecision.REQUEST_CHANGE,
        ),
    ),
)
def test_ingest_review_creates_created_and_decision_events(
    decision: ReviewDecision,
    event_type: AuditEventType,
    audit_decision: AuditDecision,
) -> None:
    record = review_record(decision=decision)
    service, _ = audit_service((record,), (evidence_bundle(record),))
    trail = service.ingest_review(review_reference(record))
    assert tuple(event.event_type for event in trail.events) == (
        AuditEventType.REVIEW_CREATED,
        event_type,
    )
    assert trail.events[1].decision is audit_decision
    assert trail.events[0].actor_id == record.review_request.requester
    assert trail.events[1].actor_id == record.decision.reviewer
    assert trail.events[1].reason == record.decision.comment


def test_ingest_binds_project_target_and_review_provenance() -> None:
    record = review_record()
    service, _ = audit_service((record,), (evidence_bundle(record),))
    trail = service.ingest_review(review_reference(record))
    event = trail.events[1]
    target = record.review_request.target_reference
    assert event.project_id == record.review_request.project_id
    assert event.source_type.value == target.target_type.value
    assert event.source_id == target.target_id
    assert event.review_reference == review_reference(record)
    assert any(ref.startswith("review://") for ref in event.evidence_refs)
    assert any(ref.startswith("target://") for ref in event.evidence_refs)


def test_missing_engineering_evidence_creates_incomplete_events() -> None:
    record = review_record()
    service, _ = audit_service((record,))
    trail = service.ingest_review(review_reference(record))
    assert all(
        event.evidence_status is EvidenceCoverageStatus.MISSING
        for event in trail.events
    )
    assert all(event.evidence_refs for event in trail.events)


def test_ingest_review_is_idempotent() -> None:
    record = review_record()
    service, store = audit_service((record,), (evidence_bundle(record),))
    first = service.ingest_review(review_reference(record))
    second = service.ingest_review(review_reference(record))
    assert second == first
    assert len(store.get_versions(first.audit_id)) == 2


def test_ingest_recovers_when_second_append_previously_failed() -> None:
    record = review_record()
    service, store = audit_service(
        (record,), (evidence_bundle(record),), fail_on_attempts=(2,)
    )
    with pytest.raises(AuditWriteFailed):
        service.ingest_review(review_reference(record))
    partial = store.get_trail_for_test(record.review_request.project_id)
    assert partial is not None
    assert len(partial.events) == 1
    recovered = service.ingest_review(review_reference(record))
    assert len(recovered.events) == 2


def test_missing_review_record_fails_closed() -> None:
    service, _ = audit_service(())
    reference = ReviewRecordReference.create(
        review_id="review-missing", fingerprint=digest("missing")
    )
    with pytest.raises(ReviewRecordNotFound):
        service.ingest_review(reference)


def test_review_record_fingerprint_mismatch_fails_closed() -> None:
    record = review_record()
    service, _ = audit_service((record,))
    reference = ReviewRecordReference.create(
        review_id=record.review_request.review_id,
        fingerprint=digest("wrong"),
    )
    with pytest.raises(AuditCreationError):
        service.ingest_review(reference)


def test_review_record_id_mismatch_from_source_is_unavailable() -> None:
    record = review_record()

    class WrongSource:
        def get_review_record(self, review_id):
            return review_record(index=2)

    service = AuditService(
        source=WrongSource(),
        evidence_source=FakeEvidenceSourceAdapter(()),
        store=FakeAuditStoreAdapter(),
    )
    with pytest.raises(AuditCreationError):
        service.ingest_review(review_reference(record))


def test_evidence_bundle_identity_mismatch_fails_closed() -> None:
    record = review_record()
    wrong = evidence_bundle(review_record(target_id="finding-other"))

    class WrongEvidenceSource:
        def get_evidence(self, project_id, source_type, source_id, source_fingerprint):
            return wrong

    service = AuditService(
        source=FakeReviewRecordSourceAdapter((record,)),
        evidence_source=WrongEvidenceSource(),
        store=FakeAuditStoreAdapter(),
    )
    with pytest.raises(AuditCreationError):
        service.ingest_review(review_reference(record))


def risk_request(
    record,
    event_type: AuditEventType,
    *,
    index: int = 1,
    timestamp=None,
) -> RiskAuditRequest:
    return RiskAuditRequest.create(
        request_id=f"risk-request-{index}-{event_type.value.lower()}",
        review_reference=review_reference(record),
        event_type=event_type,
        actor_id=f"risk-reviewer-{index}",
        reason="Power rail risk remains unresolved.",
        timestamp=timestamp or record.decision.decided_at + timedelta(minutes=1),
    )


def test_explicit_risk_identified_event_is_appended() -> None:
    record = review_record(decision=ReviewDecision.REQUEST_CHANGE)
    service, _ = audit_service((record,), (evidence_bundle(record),))
    service.ingest_review(review_reference(record))
    trail = service.append_risk(
        risk_request(record, AuditEventType.RISK_IDENTIFIED)
    )
    assert trail.events[-1].event_type is AuditEventType.RISK_IDENTIFIED
    assert trail.events[-1].reason == "Power rail risk remains unresolved."


def test_risk_event_requires_ingested_review_history() -> None:
    record = review_record(decision=ReviewDecision.REQUEST_CHANGE)
    service, _ = audit_service((record,), (evidence_bundle(record),))
    with pytest.raises(AuditCreationError):
        service.append_risk(
            risk_request(record, AuditEventType.RISK_IDENTIFIED)
        )


def test_risk_remained_requires_prior_identification() -> None:
    first = review_record(decision=ReviewDecision.REJECT)
    second = review_record(
        index=2,
        decision=ReviewDecision.REQUEST_CHANGE,
        target_id=first.review_request.target_reference.target_id,
    )
    service, _ = audit_service(
        (first, second),
        (evidence_bundle(first), evidence_bundle(second)),
    )
    service.ingest_review(review_reference(first))
    service.ingest_review(review_reference(second))
    with pytest.raises(AuditCreationError):
        service.append_risk(
            risk_request(second, AuditEventType.RISK_REMAINED, index=2)
        )


def test_risk_remained_requires_two_review_cycles() -> None:
    record = review_record(decision=ReviewDecision.REJECT)
    service, _ = audit_service((record,), (evidence_bundle(record),))
    service.ingest_review(review_reference(record))
    service.append_risk(risk_request(record, AuditEventType.RISK_IDENTIFIED))
    with pytest.raises(AuditCreationError):
        service.append_risk(
            risk_request(
                record,
                AuditEventType.RISK_REMAINED,
                index=2,
                timestamp=record.decision.decided_at + timedelta(minutes=2),
            )
        )


def test_risk_remained_succeeds_after_two_cycles() -> None:
    first = review_record(decision=ReviewDecision.REJECT)
    second = review_record(
        index=2,
        decision=ReviewDecision.REQUEST_CHANGE,
        target_id=first.review_request.target_reference.target_id,
    )
    service, _ = audit_service(
        (first, second),
        (evidence_bundle(first), evidence_bundle(second)),
    )
    service.ingest_review(review_reference(first))
    service.append_risk(risk_request(first, AuditEventType.RISK_IDENTIFIED))
    service.ingest_review(review_reference(second))
    trail = service.append_risk(
        risk_request(second, AuditEventType.RISK_REMAINED, index=2)
    )
    assert trail.events[-1].event_type is AuditEventType.RISK_REMAINED


def test_risk_request_timestamp_cannot_precede_review_decision() -> None:
    record = review_record(decision=ReviewDecision.REJECT)
    service, _ = audit_service((record,), (evidence_bundle(record),))
    service.ingest_review(review_reference(record))
    request = risk_request(
        record,
        AuditEventType.RISK_IDENTIFIED,
        timestamp=record.review_request.created_at,
    )
    with pytest.raises(AuditCreationError):
        service.append_risk(request)


def test_non_risk_event_request_is_invalid() -> None:
    record = review_record()
    valid = risk_request(record, AuditEventType.RISK_IDENTIFIED)
    invalid = valid.model_copy(deep=True)
    object.__setattr__(invalid, "event_type", AuditEventType.REVIEW_APPROVED)
    service, _ = audit_service((record,))
    with pytest.raises(InvalidAuditEvent):
        service.append_risk(invalid)


def test_inputs_remain_immutable_after_service_calls() -> None:
    record = review_record()
    reference = review_reference(record)
    original = reference.model_copy(deep=True)
    service, _ = audit_service((record,), (evidence_bundle(record),))
    service.ingest_review(reference)
    assert reference == original
