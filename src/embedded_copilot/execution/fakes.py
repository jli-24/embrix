from __future__ import annotations

from .contracts import ExecutionCapability, ExecutionResult
from .exceptions import ExecutionUnavailable
from .integration_contracts import (
    BuildRequest,
    BuildResult,
    DebugRequest,
    ExecutionStatus,
    FlashRequest,
    FlashResult,
)


class InMemoryExecutionAdapter:
    """Deterministic adapter used only for offline contract tests."""

    def __init__(self, *, available: bool = True) -> None:
        self._available = bool(available)

    def _result(self, request, capability: ExecutionCapability) -> ExecutionResult:
        return ExecutionResult.create(
            request_id=request.request_id,
            capability=capability,
            status="SUCCEEDED" if self._available else "UNAVAILABLE",
            summary=(
                "offline simulated result"
                if self._available
                else "offline capability unavailable"
            ),
        )

    def build(self, request):
        if isinstance(request, BuildRequest):
            return BuildResult.create(
                request_id=request.request.request_id,
                status=ExecutionStatus.UNAVAILABLE,
                exit_code=None,
                artifact=None,
                logs_summary="offline capability unavailable",
            )
        return self._result(request, ExecutionCapability.BUILD)

    def flash(self, request):
        if isinstance(request, FlashRequest):
            return FlashResult.create(
                request_id=request.request.request_id,
                status=ExecutionStatus.UNAVAILABLE,
                exit_code=None,
                firmware_fingerprint=request.artifact.firmware_fingerprint,
                logs_summary="offline capability unavailable",
            )
        return self._result(request, ExecutionCapability.FLASH)

    def collect_logs(self, request):
        if isinstance(request, DebugRequest):
            raise ExecutionUnavailable("debug logs unavailable")
        return self._result(request, ExecutionCapability.DEBUG_LOGS)


class FakeBuildAdapter:
    def build(self, request):
        if isinstance(request, BuildRequest):
            return BuildResult.create(
                request_id=request.request.request_id,
                status=ExecutionStatus.UNAVAILABLE,
                exit_code=None,
                artifact=None,
                logs_summary="offline build adapter",
            )
        return ExecutionResult.create(
            request_id=request.request_id,
            capability=ExecutionCapability.BUILD,
            status="UNAVAILABLE",
            summary="offline build adapter",
        )


class FakeFlashAdapter:
    def flash(self, request):
        if isinstance(request, FlashRequest):
            return FlashResult.create(
                request_id=request.request.request_id,
                status=ExecutionStatus.UNAVAILABLE,
                exit_code=None,
                firmware_fingerprint=request.artifact.firmware_fingerprint,
                logs_summary="offline flash adapter",
            )
        return ExecutionResult.create(
            request_id=request.request_id,
            capability=ExecutionCapability.FLASH,
            status="UNAVAILABLE",
            summary="offline flash adapter",
        )


class FakeDebugAdapter:
    def collect_logs(self, request):
        if isinstance(request, DebugRequest):
            raise ExecutionUnavailable("debug logs unavailable")
        return ExecutionResult.create(
            request_id=request.request_id,
            capability=ExecutionCapability.DEBUG_LOGS,
            status="UNAVAILABLE",
            summary="offline debug observation adapter",
        )


__all__ = (
    "FakeBuildAdapter",
    "FakeDebugAdapter",
    "FakeFlashAdapter",
    "InMemoryExecutionAdapter",
)
