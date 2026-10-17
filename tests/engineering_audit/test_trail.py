from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_audit import (
    AuditEvent,
    AuditEventType,
    AuditTrail,
)
from embedded_copilot.engineering_review import ReviewDecision

from ._factories import NOW, audit_event, audit_trail, review_record


def test_append_event_returns_new_trail_version_without_mutation() -> None:
    created = audit_event()
    trail = audit_trail(created)
    decision = audit_event(
        event_type=AuditEventType.REVIEW_APPROVED,
        timestamp=created.timestamp + timedelta(minutes=10),
    )
    updated = trail.append_event(decision)
    assert trail.events == (created,)
    assert updated.events == (created, decision)
    assert updated.fingerprint != trail.fingerprint


def test_identical_event_retry_is_idempotent() -> None:
    event = audit_event()
    trail = audit_trail(event)
    assert trail.append_event(event) == trail


def test_same_event_id_with_different_content_conflicts() -> None:
    created = audit_event()
    trail = audit_trail(created)
    changed = AuditEvent.create(
        **{
            **created.model_dump(mode="python"),
            "reason": "Different immutable content.",
        }
    )
    with pytest.raises((ValueError, ValidationError)):
        trail.append_event(changed)


@pytest.mark.parametrize("field", ("audit_id", "project_id"))
def test_append_rejects_cross_trail_identity(field: str) -> None:
    created = audit_event()
    trail = audit_trail(created)
    next_event = audit_event(
        event_type=AuditEventType.REVIEW_APPROVED,
        timestamp=created.timestamp + timedelta(minutes=10),
    )
    changed = next_event.model_copy(deep=True)
    object.__setattr__(changed, field, "different-identity")
    with pytest.raises((ValueError, ValidationError)):
        trail.append_event(changed)


def test_append_rejects_timestamp_regression() -> None:
    created = audit_event(timestamp=NOW + timedelta(hours=1))
    trail = audit_trail(created)
    older = audit_event(
        event_type=AuditEventType.REVIEW_APPROVED,
        timestamp=NOW,
    )
    with pytest.raises((ValueError, ValidationError)):
        trail.append_event(older)


def test_trail_rejects_duplicate_review_created_event() -> None:
    created = audit_event()
    duplicate = audit_event(event_id="event-other-created")
    with pytest.raises((ValueError, ValidationError)):
        audit_trail(created).append_event(duplicate)


def test_trail_rejects_multiple_decisions_for_same_review() -> None:
    record = review_record(decision=ReviewDecision.APPROVE)
    created = audit_event(record)
    approved = audit_event(
        record,
        event_type=AuditEventType.REVIEW_APPROVED,
        timestamp=record.decision.decided_at,
    )
    trail = audit_trail(created, approved)
    rejected = audit_event(
        record,
        event_type=AuditEventType.REVIEW_REJECTED,
        event_id="event-second-decision",
        timestamp=record.decision.decided_at + timedelta(minutes=1),
    )
    with pytest.raises((ValueError, ValidationError)):
        trail.append_event(rejected)


def test_trail_requires_created_at_equal_first_event_timestamp() -> None:
    event = audit_event()
    with pytest.raises((ValueError, ValidationError)):
        AuditTrail.create(
            audit_id=event.audit_id,
            project_id=event.project_id,
            events=(event,),
            created_at=event.timestamp + timedelta(seconds=1),
        )


def test_trail_events_are_tuple_only() -> None:
    event = audit_event()
    with pytest.raises((TypeError, ValidationError)):
        AuditTrail.create(
            audit_id=event.audit_id,
            project_id=event.project_id,
            events=[event],
            created_at=event.timestamp,
        )
