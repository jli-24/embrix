from __future__ import annotations

from datetime import timedelta

import pytest

from embedded_copilot.engineering_audit import (
    AuditConflict,
    AuditEvent,
    AuditEventType,
    AuditWriteFailed,
)
from embedded_copilot.engineering_audit.adapters import (
    FakeAuditClockAdapter,
    FakeAuditStoreAdapter,
    FakeEvidenceSourceAdapter,
    FakeReviewRecordSourceAdapter,
)
from embedded_copilot.engineering_review import ReviewDecision

from ._factories import NOW, audit_event, evidence_bundle, review_record


def test_review_source_returns_revalidated_deep_copy() -> None:
    record = review_record()
    source = FakeReviewRecordSourceAdapter((record,))
    loaded = source.get_review_record(record.review_request.review_id)
    assert loaded == record
    assert loaded is not record
    assert loaded.review_request is not record.review_request


def test_review_source_rejects_conflicting_identity() -> None:
    first = review_record()
    second = review_record(decision=ReviewDecision.REJECT)
    with pytest.raises(AuditConflict):
        FakeReviewRecordSourceAdapter((first, second))


def test_evidence_source_returns_exact_bound_bundle() -> None:
    record = review_record()
    bundle = evidence_bundle(record)
    source = FakeEvidenceSourceAdapter((bundle,))
    loaded = source.get_evidence(
        bundle.project_id,
        bundle.source_type,
        bundle.source_id,
        bundle.source_fingerprint,
    )
    assert loaded == bundle
    assert loaded is not bundle


def test_evidence_source_returns_none_for_missing_identity() -> None:
    source = FakeEvidenceSourceAdapter(())
    assert source.get_evidence(
        "project-1", "VALIDATION", "finding-1", "sha256:" + "a" * 64
    ) is None


def test_store_creates_new_immutable_version_per_event() -> None:
    store = FakeAuditStoreAdapter()
    created = audit_event()
    first = store.append(created)
    decided = audit_event(
        event_type=AuditEventType.REVIEW_APPROVED,
        timestamp=created.timestamp + timedelta(minutes=10),
    )
    second = store.append(decided)
    assert len(first.events) == 1
    assert len(second.events) == 2
    assert len(store.get_versions(created.audit_id)) == 2


def test_store_identical_append_is_idempotent_without_new_version() -> None:
    store = FakeAuditStoreAdapter()
    event = audit_event()
    first = store.append(event)
    second = store.append(event)
    assert second == first
    assert len(store.get_versions(event.audit_id)) == 1


def test_store_same_event_id_different_content_conflicts() -> None:
    store = FakeAuditStoreAdapter()
    event = audit_event()
    store.append(event)
    changed = AuditEvent.create(
        **{**event.model_dump(mode="python"), "reason": "Changed content."}
    )
    with pytest.raises(AuditConflict):
        store.append(changed)


def test_store_failure_injection_is_deterministic() -> None:
    store = FakeAuditStoreAdapter(fail_on_attempts=(1,))
    with pytest.raises(AuditWriteFailed):
        store.append(audit_event())
    assert store.attempt_count == 1


def test_store_get_trail_returns_deep_copy() -> None:
    store = FakeAuditStoreAdapter()
    event = audit_event()
    saved = store.append(event)
    loaded = store.get_trail(event.audit_id)
    assert loaded == saved
    assert loaded is not saved
    assert loaded.events[0] is not saved.events[0]


def test_fixed_clock_has_no_time_dependency() -> None:
    clock = FakeAuditClockAdapter(NOW)
    assert clock.now() == NOW
    assert clock.now() == NOW


def test_fixed_clock_requires_utc() -> None:
    with pytest.raises((TypeError, ValueError)):
        FakeAuditClockAdapter(NOW.replace(tzinfo=None))
