"""Redacted native-host/WebView handoff telemetry."""

from __future__ import annotations

import logging

LOGGER_NAME = "embedded_copilot.native_shell.handoff"
_logger = logging.getLogger(LOGGER_NAME)


def record_native_handoff(
    stage: str,
    status: str,
    *,
    exception_type: str = "-",
    reason_code: str = "-",
    http_status: int | str = "-",
    win_error: int | str = "-",
) -> None:
    """Write only fixed, non-sensitive handoff fields and flush immediately."""

    _logger.info(
        "native_handoff_telemetry",
        extra={
            "event_name": "native_handoff_telemetry",
            "native_stage": stage,
            "telemetry_status": status,
            "exception_type": exception_type,
            "reason_code": reason_code,
            "http_status": http_status,
            "win_error": win_error,
        },
    )
    for handler in tuple(_logger.handlers):
        handler.flush()


__all__ = ("LOGGER_NAME", "record_native_handoff")
