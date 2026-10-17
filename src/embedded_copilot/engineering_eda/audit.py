from __future__ import annotations

import copy
from datetime import datetime

from .contracts import deterministic_id
from .exceptions import EDAAuditUnavailable
from .models import EDAAuditEvent, EDAAuditEventType
from .ports import EDAAuditSinkPort, EDAClockPort


def append_eda_audit(
    *,
    sink: EDAAuditSinkPort,
    clock: EDAClockPort,
    event_type: EDAAuditEventType,
    project_id: str,
    operation_fingerprint: str,
    artifact_fingerprint: str | None = None,
    result_fingerprint: str | None = None,
    error_code: str | None = None,
    timestamp: datetime | None = None,
) -> EDAAuditEvent:
    event = EDAAuditEvent(
        event_id=deterministic_id("eda-event", operation_fingerprint, event_type.value),
        event_type=event_type,
        project_id=project_id,
        operation_fingerprint=operation_fingerprint,
        artifact_fingerprint=artifact_fingerprint,
        result_fingerprint=result_fingerprint,
        error_code=error_code,
        timestamp=clock.now() if timestamp is None else timestamp,
    )
    try:
        stored = sink.append(copy.deepcopy(event))
        checked = EDAAuditEvent.model_validate(
            copy.deepcopy(stored.model_dump(mode="python"))
        )
    except Exception as error:
        raise EDAAuditUnavailable("EDA audit unavailable") from error
    if checked != event:
        raise EDAAuditUnavailable("EDA audit returned a different event")
    return checked


__all__ = ("append_eda_audit",)
