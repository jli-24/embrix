"""Opt-in, content-free diagnostics for the P0-1 credential save boundary."""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
import secrets
import tempfile
from collections.abc import Awaitable, Callable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DIAGNOSTIC_ENVIRONMENT_VARIABLE = "EMBRIX_CREDENTIAL_SAVE_DIAGNOSTIC"
_OUTPUT_DIRECTORY = "embrix-p0-1g"
_OUTPUT_FILENAME = "credential-save-diagnostic.jsonl"
_CONFIGURATION_REQUEST_PATH = "/api/v1/copilot/desktop/system/provider/configuration"

ALLOWED_EVENT_FIELDS = frozenset(
    {
        "timestamp",
        "request_id",
        "stage",
        "result",
        "safe_error_class",
        "safe_win32_error_code",
        "safe_http_status",
    }
)

DIAGNOSTIC_STAGES = frozenset(
    {
        "HTTP_CONFIGURATION_REQUEST_RECEIVED",
        "CONFIGURATION_ROUTE_ENTERED",
        "CONFIGURATION_SERVICE_ENTERED",
        "CONFIGURATION_STORE_SAVE_BEGIN",
        "CONFIGURATION_STORE_SAVE_SUCCESS",
        "CONFIGURATION_STORE_SAVE_FAILURE",
        "CONFIGURATION_ROUTE_SUCCESS",
        "CONFIGURATION_ROUTE_FAILURE",
        "HTTP_CONFIGURATION_RESPONSE",
        "HTTP_ROUTE_ENTERED",
        "PROVIDER_SERVICE_ENTERED",
        "SECRET_VALIDATION_PASSED",
        "INTEGRITY_IDENTITY_BEGIN",
        "INTEGRITY_TARGET_EXISTS",
        "INTEGRITY_TARGET_CREATE_BEGIN",
        "INTEGRITY_TARGET_CREATE_SUCCESS",
        "INTEGRITY_TARGET_CREATE_FAILURE",
        "CREDENTIAL_FINGERPRINT_READY",
        "BINDING_INVALIDATE_BEGIN",
        "BINDING_INVALIDATE_SUCCESS",
        "PRIMARY_CREDENTIAL_WRITE_BEGIN",
        "PRIMARY_CREDENTIAL_WRITE_SUCCESS",
        "PRIMARY_CREDENTIAL_WRITE_FAILURE",
        "PRIMARY_CREDENTIAL_READBACK_BEGIN",
        "PRIMARY_CREDENTIAL_READBACK_SUCCESS",
        "PRIMARY_CREDENTIAL_READBACK_FAILURE",
        "SAFE_CONFIG_PERSIST_BEGIN",
        "SAFE_CONFIG_PERSIST_SUCCESS",
        "SAFE_CONFIG_PERSIST_FAILURE",
        "PROVIDER_PROJECTION_RETURNED",
        "HTTP_ROUTE_SUCCESS",
        "HTTP_ROUTE_FAILURE",
    }
)

_REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "credential_save_diagnostic_request_id",
    default=None,
)


def enabled() -> bool:
    """Return whether the local diagnostic sink was explicitly enabled."""

    return os.environ.get(DIAGNOSTIC_ENVIRONMENT_VARIABLE) == "1"


def new_request_id() -> str:
    """Create a random correlation id that is independent of user input."""

    return "credential-save-" + secrets.token_hex(16)


@contextlib.contextmanager
def request_scope(request_id: str | None = None) -> Iterator[str]:
    """Bind a safe request id for one route/service operation."""

    value = request_id or new_request_id()
    token = _REQUEST_ID.set(value)
    try:
        yield value
    finally:
        _REQUEST_ID.reset(token)


def diagnostic_output_path() -> Path:
    return Path(tempfile.gettempdir()) / _OUTPUT_DIRECTORY / _OUTPUT_FILENAME


def safe_error_class(error: BaseException) -> str:
    """Map exceptions to a fixed, content-free diagnostic category."""

    if error.__class__.__name__ == "CredentialUnavailable":
        return "CREDENTIAL_STORE_UNAVAILABLE"
    if isinstance(error, (TypeError, ValueError)):
        return "CONFIG_INVALID"
    return "DIAGNOSTIC_ERROR"


def safe_win32_error_code(error: BaseException) -> int | None:
    value = getattr(error, "win32_error_code", None)
    return value if isinstance(value, int) and value >= 0 else None


def emit(
    stage: str,
    *,
    result: str,
    error: BaseException | None = None,
    safe_http_status: int | None = None,
) -> None:
    """Append one allowlisted event without affecting normal behavior."""

    if not enabled() or stage not in DIAGNOSTIC_STAGES:
        return
    request_id = _REQUEST_ID.get()
    if request_id is None:
        return
    payload: dict[str, object] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "request_id": request_id,
        "stage": stage,
        "result": result,
    }
    if error is not None:
        payload["safe_error_class"] = safe_error_class(error)
        win32_code = safe_win32_error_code(error)
        if win32_code is not None:
            payload["safe_win32_error_code"] = win32_code
    if type(safe_http_status) is int:
        payload["safe_http_status"] = safe_http_status
    if set(payload) - ALLOWED_EVENT_FIELDS:
        return
    try:
        output = diagnostic_output_path()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n")
            stream.flush()
    except (OSError, TypeError, ValueError):
        # Diagnostics are best-effort and must never change provider behavior.
        return


class ConfigurationDispatchDiagnosticMiddleware:
    """Observe only the configuration PUT before routing and dependency resolution."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self._app = app

    async def __call__(self, scope: Mapping[str, Any], receive: Any, send: Any) -> None:
        if (
            scope.get("type") != "http"
            or scope.get("method") != "PUT"
            or scope.get("path") != _CONFIGURATION_REQUEST_PATH
        ):
            await self._app(scope, receive, send)
            return

        request_id = new_request_id()
        response_reported = False

        async def send_observed(message: Mapping[str, Any]) -> None:
            nonlocal response_reported
            if message.get("type") == "http.response.start" and not response_reported:
                status = message.get("status")
                safe_status = status if type(status) is int else None
                emit(
                    "HTTP_CONFIGURATION_RESPONSE",
                    result=(
                        "SUCCESS"
                        if safe_status is not None and 200 <= safe_status < 300
                        else "FAILURE"
                    ),
                    safe_http_status=safe_status,
                )
                response_reported = True
            await send(message)

        with request_scope(request_id):
            emit("HTTP_CONFIGURATION_REQUEST_RECEIVED", result="RECEIVED")
            try:
                await self._app(scope, receive, send_observed)
            except Exception as error:  # noqa: BLE001 - observation must preserve the original failure
                if not response_reported:
                    emit("HTTP_CONFIGURATION_RESPONSE", result="FAILURE", error=error)
                    response_reported = True
                raise
            finally:
                if not response_reported:
                    emit("HTTP_CONFIGURATION_RESPONSE", result="FAILURE")


__all__ = (
    "ALLOWED_EVENT_FIELDS",
    "ConfigurationDispatchDiagnosticMiddleware",
    "DIAGNOSTIC_ENVIRONMENT_VARIABLE",
    "DIAGNOSTIC_STAGES",
    "diagnostic_output_path",
    "emit",
    "enabled",
    "new_request_id",
    "request_scope",
    "safe_error_class",
    "safe_win32_error_code",
)
