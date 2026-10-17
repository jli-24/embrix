from __future__ import annotations

import copy

import pytest

from embedded_copilot.application import (
    ApplicationAuditConflict,
    ApplicationAuditEvent,
    ApplicationAuditEventType,
    ApplicationErrorCode,
    ApplicationRoute,
    ApplicationRunStatus,
    FakeApplicationAuditSink,
)

from .fixtures import NOW, request


def _event() -> ApplicationAuditEvent:
    return ApplicationAuditEvent.create(
        event_type=ApplicationAuditEventType.QUESTION_REJECTED,
        request=request(),
        route=ApplicationRoute.UNKNOWN,
        status=ApplicationRunStatus.REJECTED,
        context_fingerprint=None,
        answer_fingerprint=None,
        model_info_fingerprint=None,
        evidence_refs=(),
        error_code=ApplicationErrorCode.CLARIFICATION_REQUIRED,
        timestamp=NOW,
    )


def test_audit_sink_is_append_only_idempotent_and_deep_copied() -> None:
    sink = FakeApplicationAuditSink()
    event = _event()
    assert sink.append(event) == event
    assert sink.append(copy.deepcopy(event)) == event
    fetched = sink.get(event.event_id)
    assert fetched == event
    dumped = fetched.model_dump(mode="python")
    dumped["request_id"] = "changed"
    assert sink.get(event.event_id).request_id == "request-1"
    assert sink.events == (event,)


def test_audit_sink_rejects_same_identity_with_different_content() -> None:
    sink = FakeApplicationAuditSink()
    event = _event()
    sink.append(event)
    material = event.model_dump(mode="python")
    material["trace_id"] = "trace-2"
    material.pop("fingerprint")
    conflicting = ApplicationAuditEvent(**material)
    with pytest.raises(ApplicationAuditConflict):
        sink.append(conflicting)
