from __future__ import annotations

import copy

from .contracts import deterministic_id
from .exceptions import OptimizationAuditUnavailable
from .models import OptimizationAuditEvent, OptimizationAuditEventType
from .ports import ClockPort, OptimizationAuditPort


def append_optimization_audit(
    *,
    sink: OptimizationAuditPort,
    clock: ClockPort,
    event_type: OptimizationAuditEventType,
    project_id: str,
    operation_fingerprint: str,
    result_fingerprint: str | None,
) -> OptimizationAuditEvent:
    event = OptimizationAuditEvent(
        event_id=deterministic_id(
            "optimization-event", operation_fingerprint, event_type.value
        ),
        event_type=event_type,
        project_id=project_id,
        operation_fingerprint=operation_fingerprint,
        result_fingerprint=result_fingerprint,
        timestamp=clock.now(),
    )
    try:
        existing = sink.get(event.event_id)
        if existing is not None:
            checked = OptimizationAuditEvent.model_validate(
                copy.deepcopy(existing.model_dump(mode="python"))
            )
            if (
                checked.event_type is not event.event_type
                or checked.project_id != event.project_id
                or checked.operation_fingerprint != event.operation_fingerprint
                or checked.result_fingerprint != event.result_fingerprint
            ):
                raise OptimizationAuditUnavailable("optimization audit conflict")
            return checked
        checked = OptimizationAuditEvent.model_validate(
            copy.deepcopy(sink.append(copy.deepcopy(event)).model_dump(mode="python"))
        )
    except OptimizationAuditUnavailable:
        raise
    except Exception as error:
        raise OptimizationAuditUnavailable("optimization audit unavailable") from error
    if checked != event:
        raise OptimizationAuditUnavailable("optimization audit was altered")
    return checked
