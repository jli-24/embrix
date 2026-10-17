from __future__ import annotations

import asyncio

import pytest

from embedded_copilot.execution import (
    DebugRequest,
    ExecutionCapability,
    ExecutionRequest,
    SerialToolConfiguration,
)
from embedded_copilot.execution.adapters.serial.monitor import SerialDebugAdapter
from embedded_copilot.execution.exceptions import (
    ExecutionRejected,
    ExecutionUnavailable,
)


class _Connection:
    def __init__(self, values):
        self.values, self.closed = list(values), False

    def readline(self):
        return self.values.pop(0) if self.values else b""

    def close(self):
        self.closed = True


class _Factory:
    def __init__(self, connection):
        self.connection, self.calls = connection, []

    def open(self, **kwargs):
        self.calls.append(kwargs)
        return self.connection


class _Cancellation:
    def __init__(self, value=False):
        self.value = value

    def is_cancelled(self, token):
        return self.value


def _config():
    return SerialToolConfiguration.create(
        configuration_id="serial-config-1",
        project_id="project-1",
        workspace_id="workspace-1",
        target_id="target-1",
        serial_port="COM7",
    )


def _request():
    return DebugRequest.create(
        request=ExecutionRequest.create(
            request_id="debug-request-1",
            project_id="project-1",
            target_id="target-1",
            proposal_fingerprint="sha256:" + "a" * 64,
            capability=ExecutionCapability.DEBUG_LOGS,
            timeout_seconds=1.0,
            cancellation_token="cancel-1",
        )
    )


def test_serial_observation_is_sanitized_bounded_and_target_bound():
    connection = _Connection(
        [b"INFO boot C:\\private\\firmware.bin\n", b"ERROR password=secret\n", b""]
    )
    factory = _Factory(connection)
    observation = SerialDebugAdapter(
        configuration=_config(), serial_factory=factory
    ).collect_logs(_request())
    assert observation.severity.value == "ERROR"
    assert all(
        "private" not in line.casefold() and "password" not in line.casefold()
        for line in observation.log_lines
    )
    assert factory.calls == [
        {"port": "COM7", "baud_rate": 115200, "timeout_seconds": 0.1}
    ]
    assert connection.closed


def test_serial_malformed_and_unavailable_fail_closed():
    with pytest.raises(ExecutionRejected):
        SerialDebugAdapter(
            configuration=_config(), serial_factory=_Factory(_Connection([object()]))
        ).collect_logs(_request())

    class Missing:
        def open(self, **kwargs):
            raise OSError("private serial details")

    with pytest.raises(ExecutionUnavailable):
        SerialDebugAdapter(
            configuration=_config(), serial_factory=Missing()
        ).collect_logs(_request())


def test_serial_cancellation_propagates_without_observation():
    with pytest.raises(asyncio.CancelledError):
        SerialDebugAdapter(
            configuration=_config(),
            serial_factory=_Factory(_Connection([b"line"])),
            cancellation=_Cancellation(True),
        ).collect_logs(_request())
