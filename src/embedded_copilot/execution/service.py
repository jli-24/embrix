from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from .contracts import ExecutionCapability, ExecutionRequest, ExecutionResult
from .exceptions import ExecutionRejected, ExecutionUnavailable
from .integration_contracts import (
    BuildRequest,
    BuildResult,
    DebugObservation,
    DebugRequest,
    ExecutionStatus,
    FlashRequest,
    FlashResult,
)
from .ports import BuildPort, DebugPort, FlashPort


class ExecutionService:
    def __init__(
        self,
        *,
        build_port: BuildPort | None = None,
        flash_port: FlashPort | None = None,
        debug_port: DebugPort | None = None,
    ):
        self._build, self._flash, self._debug = build_port, flash_port, debug_port

    def _validate(self, request):
        envelope = (
            request.request
            if isinstance(request, (BuildRequest, FlashRequest, DebugRequest))
            else request
        )
        req = ExecutionRequest.model_validate(envelope.model_dump(mode="python"))
        if req.approval is not None:
            expiry = datetime.fromisoformat(req.approval.expires_at)
            if expiry <= datetime.now(UTC):
                raise ValueError("approval expired")
        return req

    def build(self, request):
        req = self._validate(request)
        if req.capability != ExecutionCapability.BUILD or self._build is None:
            if isinstance(request, BuildRequest):
                return BuildResult.create(
                    request_id=req.request_id,
                    status=ExecutionStatus.UNAVAILABLE,
                    exit_code=None,
                    artifact=None,
                    logs_summary="build unavailable",
                )
            return ExecutionResult.create(
                request_id=req.request_id,
                capability=ExecutionCapability.BUILD,
                status="UNAVAILABLE",
                summary="build unavailable",
            )
        if isinstance(request, BuildRequest):
            checked = BuildRequest.model_validate(request.model_dump(mode="python"))
            return self._dispatch_build(self._build.build, checked)
        return self._dispatch(self._build.build, req, ExecutionCapability.BUILD)

    def flash(self, request):
        req = self._validate(request)
        if req.capability != ExecutionCapability.FLASH or self._flash is None:
            if isinstance(request, FlashRequest):
                return FlashResult.create(
                    request_id=req.request_id,
                    status=ExecutionStatus.UNAVAILABLE,
                    exit_code=None,
                    firmware_fingerprint=request.artifact.firmware_fingerprint,
                    logs_summary="flash unavailable",
                )
            return ExecutionResult.create(
                request_id=req.request_id,
                capability=ExecutionCapability.FLASH,
                status="UNAVAILABLE",
                summary="flash unavailable",
            )
        if isinstance(request, FlashRequest):
            checked = FlashRequest.model_validate(request.model_dump(mode="python"))
            return self._dispatch_flash(self._flash.flash, checked)
        return self._dispatch(self._flash.flash, req, ExecutionCapability.FLASH)

    def collect_logs(self, request):
        req = self._validate(request)
        if req.capability != ExecutionCapability.DEBUG_LOGS or self._debug is None:
            if isinstance(request, DebugRequest):
                raise ExecutionUnavailable("debug logs unavailable")
            return ExecutionResult.create(
                request_id=req.request_id,
                capability=ExecutionCapability.DEBUG_LOGS,
                status="UNAVAILABLE",
                summary="debug logs unavailable",
            )
        if isinstance(request, DebugRequest):
            checked = DebugRequest.model_validate(request.model_dump(mode="python"))
            return self._dispatch_debug(self._debug.collect_logs, checked)
        return self._dispatch(
            self._debug.collect_logs, req, ExecutionCapability.DEBUG_LOGS
        )

    @staticmethod
    def _dispatch_build(call, request: BuildRequest) -> BuildResult:
        try:
            value = call(request)
            result = BuildResult.model_validate(value.model_dump(mode="python"))
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return BuildResult.create(
                request_id=request.request.request_id,
                status=ExecutionStatus.TIMEOUT,
                exit_code=None,
                artifact=None,
                logs_summary="build timed out",
            )
        except (TypeError, ValueError, AttributeError):
            raise ExecutionRejected("build response rejected") from None
        if result.request_id != request.request.request_id:
            raise ExecutionRejected("build response rejected")
        return result

    @staticmethod
    def _dispatch_flash(call, request: FlashRequest) -> FlashResult:
        try:
            value = call(request)
            result = FlashResult.model_validate(value.model_dump(mode="python"))
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return FlashResult.create(
                request_id=request.request.request_id,
                status=ExecutionStatus.TIMEOUT,
                exit_code=None,
                firmware_fingerprint=request.artifact.firmware_fingerprint,
                logs_summary="flash timed out",
            )
        except (TypeError, ValueError, AttributeError):
            raise ExecutionRejected("flash response rejected") from None
        if result.request_id != request.request.request_id:
            raise ExecutionRejected("flash response rejected")
        return result

    @staticmethod
    def _dispatch_debug(call, request: DebugRequest) -> DebugObservation:
        try:
            value = call(request)
            result = DebugObservation.model_validate(value.model_dump(mode="python"))
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise ExecutionUnavailable("debug logs unavailable") from None
        except (TypeError, ValueError, AttributeError):
            raise ExecutionRejected("debug response rejected") from None
        if result.request_id != request.request.request_id:
            raise ExecutionRejected("debug response rejected")
        return result

    @staticmethod
    def _dispatch(
        call, request: ExecutionRequest, capability: ExecutionCapability
    ) -> ExecutionResult:
        try:
            value = call(request)
            result = ExecutionResult.model_validate(value.model_dump(mode="python"))
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise ExecutionUnavailable("execution unavailable") from None
        except (TypeError, ValueError, AttributeError):
            raise ExecutionRejected("execution response rejected") from None
        if result.request_id != request.request_id or result.capability != capability:
            raise ExecutionRejected("execution response rejected")
        return result


__all__ = ("ExecutionService",)
