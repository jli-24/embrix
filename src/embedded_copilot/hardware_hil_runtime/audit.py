from __future__ import annotations

import copy

from .contracts import deterministic_id
from .exceptions import HILAuditUnavailable
from .models import HILAuditEvent, HILAuditEventType
from .ports import ClockPort, HardwareAuditPort


def append_hil_audit(
    *,
    sink: HardwareAuditPort,
    clock: ClockPort,
    event_type: HILAuditEventType,
    project_id: str,
    operation_fingerprint: str,
    result_fingerprint: str | None,
) -> HILAuditEvent:
    event = HILAuditEvent(
        event_id=deterministic_id("hil-event", operation_fingerprint, event_type.value),
        event_type=event_type,
        project_id=project_id,
        operation_fingerprint=operation_fingerprint,
        result_fingerprint=result_fingerprint,
        timestamp=clock.now(),
    )
    try:
        existing = sink.get(event.event_id)
        if existing is not None:
            checked_existing = HILAuditEvent.model_validate(
                copy.deepcopy(existing.model_dump(mode="python"))
            )
            if (
                checked_existing.event_type is not event.event_type
                or checked_existing.project_id != event.project_id
                or checked_existing.operation_fingerprint != event.operation_fingerprint
                or checked_existing.result_fingerprint != event.result_fingerprint
            ):
                raise HILAuditUnavailable("HIL audit event conflict")
            return checked_existing
        raw = sink.append(copy.deepcopy(event))
        checked = HILAuditEvent.model_validate(
            copy.deepcopy(raw.model_dump(mode="python"))
        )
    except Exception as error:
        raise HILAuditUnavailable("HIL audit unavailable") from error
    if checked != event:
        raise HILAuditUnavailable("HIL audit was altered")
    return checked
