from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime
from typing import Protocol

from ...exceptions import ExecutionRejected, ExecutionUnavailable
from ...integration_contracts import (
    DebugObservation,
    DebugRequest,
    DebugSeverity,
    SerialToolConfiguration,
)
from ...ports import CancellationPort


class SerialConnectionPort(Protocol):
    def readline(self) -> bytes | str: ...

    def close(self) -> None: ...


class SerialFactoryPort(Protocol):
    def open(
        self, *, port: str, baud_rate: int, timeout_seconds: float
    ) -> SerialConnectionPort: ...


class SerialDebugAdapter:
    def __init__(
        self,
        *,
        configuration: SerialToolConfiguration,
        serial_factory: SerialFactoryPort,
        cancellation: CancellationPort | None = None,
    ) -> None:
        self._configuration = SerialToolConfiguration.model_validate(
            configuration.model_dump(mode="python")
        )
        self._factory = serial_factory
        self._cancellation = cancellation

    def collect_logs(self, request: DebugRequest) -> DebugObservation:
        checked = DebugRequest.model_validate(request.model_dump(mode="python"))
        envelope = checked.request
        if (
            envelope.project_id != self._configuration.project_id
            or envelope.target_id != self._configuration.target_id
        ):
            raise ExecutionRejected("debug target rejected")
        if envelope.cancellation_token and self._is_cancelled(
            envelope.cancellation_token
        ):
            raise asyncio.CancelledError
        try:
            connection = self._factory.open(
                port=self._configuration.serial_port,
                baud_rate=self._configuration.baud_rate,
                timeout_seconds=self._configuration.read_timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - injected transport errors are sanitized here.
            raise ExecutionUnavailable("serial unavailable") from None
        lines: list[str] = []
        started = time.monotonic()
        try:
            while len(lines) < self._configuration.max_lines:
                if time.monotonic() - started >= envelope.timeout_seconds:
                    break
                if envelope.cancellation_token and self._is_cancelled(
                    envelope.cancellation_token
                ):
                    raise asyncio.CancelledError
                value = connection.readline()
                if value in (b"", ""):
                    break
                if isinstance(value, bytes):
                    decoded = value.decode("utf-8", errors="replace")
                elif isinstance(value, str):
                    decoded = value
                else:
                    raise TypeError("serial output is invalid")
                sanitized = _sanitize_line(decoded)
                if sanitized:
                    lines.append(sanitized)
        except asyncio.CancelledError:
            raise
        except (TimeoutError, TypeError, ValueError):
            raise ExecutionRejected("serial observation rejected") from None
        finally:
            try:
                connection.close()
            except Exception:  # noqa: BLE001, S110 - cleanup cannot mask the observation result.
                pass
        if not lines:
            raise ExecutionUnavailable("serial observation unavailable")
        severity = _severity(lines)
        return DebugObservation.create(
            observation_id=f"observation-{envelope.request_id}",
            request_id=envelope.request_id,
            project_id=envelope.project_id,
            target_id=envelope.target_id,
            timestamp=datetime.now(UTC),
            source="UART",
            severity=severity,
            log_lines=tuple(lines),
            confidence=0.7,
        )

    def _is_cancelled(self, token: str) -> bool:
        return bool(self._cancellation and self._cancellation.is_cancelled(token))


_SENSITIVE = re.compile(
    r"(?:[A-Za-z]:[\\/][^\s]+|/(?:[^\s/]+/)+[^\s]+|(?:password|secret|token|api[_ -]?key|credential)\s*[:=]\s*[^\s]+)",
    re.IGNORECASE,
)


def _sanitize_line(value: str) -> str:
    value = value.replace("\x00", "").replace("\r", "").replace("\n", "")
    value = _SENSITIVE.sub("[REDACTED]", value).strip()
    return value[:256]


def _severity(lines: list[str]) -> DebugSeverity:
    combined = " ".join(lines).casefold()
    if any(word in combined for word in ("fatal", "panic", "exception", "error")):
        return DebugSeverity.ERROR
    if any(word in combined for word in ("warn", "deprecated")):
        return DebugSeverity.WARNING
    return DebugSeverity.INFO


__all__ = (
    "SerialConnectionPort",
    "SerialDebugAdapter",
    "SerialFactoryPort",
)
