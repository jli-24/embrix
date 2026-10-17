from __future__ import annotations

import copy
from datetime import datetime

from .contracts import deterministic_id
from .exceptions import HardwareAuditUnavailable
from .models import HardwareAuditEvent, HardwareAuditEventType
from .ports import HardwareAuditSinkPort, HardwareClockPort


def append_hardware_audit(
    *,
    sink: HardwareAuditSinkPort,
    clock: HardwareClockPort,
    event_type: HardwareAuditEventType,
    project_id: str,
    operation_fingerprint: str,
    target_fingerprint: str,
    result_fingerprint: str | None = None,
    error_code: str | None = None,
    timestamp: datetime | None = None,
) -> HardwareAuditEvent:
    event = HardwareAuditEvent(
        event_id=deterministic_id(
            "hardware-event", operation_fingerprint, event_type.value
        ),
        event_type=event_type,
        project_id=project_id,
        operation_fingerprint=operation_fingerprint,
        target_fingerprint=target_fingerprint,
        result_fingerprint=result_fingerprint,
        error_code=error_code,
        timestamp=clock.now() if timestamp is None else timestamp,
    )
    try:
        stored = sink.append(copy.deepcopy(event))
        checked = HardwareAuditEvent.model_validate(
            copy.deepcopy(stored.model_dump(mode="python"))
        )
    except Exception as error:
        raise HardwareAuditUnavailable("hardware audit unavailable") from error
    if checked != event:
        raise HardwareAuditUnavailable("hardware audit returned a different event")
    return checked


__all__ = ("append_hardware_audit",)
