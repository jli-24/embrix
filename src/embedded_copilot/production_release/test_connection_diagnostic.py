"""Opt-in, content-free diagnostics for the Test Connection dispatch boundary."""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
import re
import secrets
import tempfile
from collections.abc import Awaitable, Callable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DIAGNOSTIC_ENVIRONMENT_VARIABLE = "EMBRIX_TEST_CONNECTION_DIAGNOSTIC"
DIAGNOSTIC_BUILD_ID = "P0-1G15F"
_OUTPUT_DIRECTORY = "embrix-p0-1g"
_OUTPUT_FILENAME = "test-connection-diagnostic.jsonl"
_REQUEST_PATH = "/api/v1/copilot/desktop/system/provider/test-connection"
_PROVIDER = "deepseek"
_SAFE_PROVIDER = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_TRANSPORT_ERROR_CODES = frozenset(
    {
        "PROXY_ERROR",
        "CONNECT_TIMEOUT",
        "READ_TIMEOUT",
        "WRITE_TIMEOUT",
        "POOL_TIMEOUT",
        "CONNECT_ERROR",
        "READ_ERROR",
        "WRITE_ERROR",
        "CLOSE_ERROR",
        "PROTOCOL_ERROR",
        "HTTP_TRANSPORT_ERROR",
    }
)
_SAFE_TRANSPORT_STAGES = frozenset({"DNS", "TCP", "TLS", "HTTP"})
_SAFE_PROXY_MODES = frozenset({"DIRECT_NO_ENV_PROXY"})

ALLOWED_EVENT_FIELDS = frozenset(
    {
        "timestamp",
        "request_id",
        "stage",
        "result",
        "safe_http_status",
        "safe_error_class",
        "safe_transport_error_code",
        "safe_transport_stage",
        "provider",
        "endpoint_id",
        "credential_ref",
        "protocol_family",
        "request_url",
        "proxy_mode",
    }
)

DIAGNOSTIC_STAGES = frozenset(
    {
        "HTTP_TEST_CONNECTION_REQUEST_RECEIVED",
        "TEST_CONNECTION_ROUTE_ENTERED",
        "TEST_CONNECTION_SERVICE_ENTERED",
        "TEST_CONNECTION_PROVIDER_CALL_BEGIN",
        "TEST_CONNECTION_PROVIDER_CALL_SUCCESS",
        "TEST_CONNECTION_PROVIDER_CALL_FAILURE",
        "TEST_CONNECTION_RECEIPT_PERSIST_BEGIN",
        "TEST_CONNECTION_RECEIPT_PERSIST_SUCCESS",
        "TEST_CONNECTION_RECEIPT_PERSIST_FAILURE",
        "TEST_CONNECTION_ROUTE_SUCCESS",
        "TEST_CONNECTION_ROUTE_FAILURE",
        "HTTP_TEST_CONNECTION_RESPONSE",
    }
)

_DIAGNOSTIC_RESULTS_BY_STAGE = {
    "HTTP_TEST_CONNECTION_REQUEST_RECEIVED": frozenset({"RECEIVED"}),
    "TEST_CONNECTION_ROUTE_ENTERED": frozenset({"ENTERED"}),
    "TEST_CONNECTION_SERVICE_ENTERED": frozenset({"ENTERED"}),
    "TEST_CONNECTION_PROVIDER_CALL_BEGIN": frozenset({"BEGIN"}),
    "TEST_CONNECTION_PROVIDER_CALL_SUCCESS": frozenset({"SUCCESS"}),
    "TEST_CONNECTION_PROVIDER_CALL_FAILURE": frozenset(
        {"AUTH_FAILED", "MODEL_UNAVAILABLE", "RATE_LIMITED", "CONFIG_INVALID", "NETWORK_UNAVAILABLE"}
    ),
    "TEST_CONNECTION_RECEIPT_PERSIST_BEGIN": frozenset({"BEGIN"}),
    "TEST_CONNECTION_RECEIPT_PERSIST_SUCCESS": frozenset({"SUCCESS"}),
    "TEST_CONNECTION_RECEIPT_PERSIST_FAILURE": frozenset({"FAILURE"}),
    "TEST_CONNECTION_ROUTE_SUCCESS": frozenset({"HTTP_200"}),
    "TEST_CONNECTION_ROUTE_FAILURE": frozenset({"HTTP_503"}),
    "HTTP_TEST_CONNECTION_RESPONSE": frozenset({"SUCCESS", "FAILURE"}),
}
_REQUEST_ID_PATTERN = re.compile(r"^test-connection-[0-9a-f]{32}$")

_REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "test_connection_diagnostic_request_id",
    default=None,
)


def enabled() -> bool:
    """Return whether the diagnostic sink was explicitly enabled."""

    return os.environ.get(DIAGNOSTIC_ENVIRONMENT_VARIABLE) == "1"


def new_request_id() -> str:
    """Create a correlation id independent of user input."""

    return "test-connection-" + secrets.token_hex(16)


@contextlib.contextmanager
def request_scope(request_id: str | None = None) -> Iterator[str]:
    """Bind a safe request id for one route/service operation."""

    value = request_id if isinstance(request_id, str) and _REQUEST_ID_PATTERN.fullmatch(request_id) else new_request_id()
    token = _REQUEST_ID.set(value)
    try:
        yield value
    finally:
        _REQUEST_ID.reset(token)


def diagnostic_output_path() -> Path:
    return Path(tempfile.gettempdir()) / _OUTPUT_DIRECTORY / _OUTPUT_FILENAME


def safe_error_class(error: BaseException) -> str:
    """Map exceptions to a fixed, content-free category."""

    if error.__class__.__name__ == "CredentialUnavailable":
        return "CREDENTIAL_STORE_UNAVAILABLE"
    if getattr(error, "safe_http_status", None) is not None:
        status = getattr(error, "safe_http_status", None)
        if status in {401, 403}:
            return "PROVIDER_AUTH_FAILED"
        if status == 404:
            return "PROVIDER_MODEL_UNAVAILABLE"
        if status == 429:
            return "PROVIDER_RATE_LIMITED"
        return "PROVIDER_HTTP_ERROR"
    transport_error_code = getattr(error, "safe_transport_error_code", None)
    if isinstance(transport_error_code, str) and transport_error_code in _SAFE_TRANSPORT_ERROR_CODES:
        return transport_error_code
    if isinstance(error, (TypeError, ValueError)):
        return "CONFIG_INVALID"
    return "DIAGNOSTIC_ERROR"


def _safe_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 512:
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path
    ):
        return None
    return value


def emit(
    stage: str,
    *,
    result: str,
    error: BaseException | None = None,
    safe_http_status: int | None = None,
    safe_transport_error_code: str | None = None,
    safe_transport_stage: str | None = None,
    provider: str | None = None,
    endpoint_id: str | None = None,
    credential_ref: str | None = None,
    protocol_family: str | None = None,
    request_url: str | None = None,
    proxy_mode: str | None = None,
) -> None:
    """Append one allowlisted event without affecting normal behavior."""

    if not enabled() or stage not in DIAGNOSTIC_STAGES:
        return
    if result not in _DIAGNOSTIC_RESULTS_BY_STAGE[stage]:
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
    if isinstance(provider, str) and _SAFE_PROVIDER.fullmatch(provider):
        payload["provider"] = provider
    for field, value in (
        ("endpoint_id", endpoint_id),
        ("credential_ref", credential_ref),
        ("protocol_family", protocol_family),
    ):
        if isinstance(value, str) and _SAFE_IDENTIFIER.fullmatch(value):
            payload[field] = value
    checked_url = _safe_url(request_url)
    if checked_url is not None:
        payload["request_url"] = checked_url
    if proxy_mode in _SAFE_PROXY_MODES:
        payload["proxy_mode"] = proxy_mode
    if isinstance(safe_transport_error_code, str) and safe_transport_error_code in _SAFE_TRANSPORT_ERROR_CODES:
        payload["safe_transport_error_code"] = safe_transport_error_code
    elif error is not None:
        error_code = getattr(error, "safe_transport_error_code", None)
        if isinstance(error_code, str) and error_code in _SAFE_TRANSPORT_ERROR_CODES:
            payload["safe_transport_error_code"] = error_code
    if isinstance(safe_transport_stage, str) and safe_transport_stage in _SAFE_TRANSPORT_STAGES:
        payload["safe_transport_stage"] = safe_transport_stage
    elif error is not None:
        error_stage = getattr(error, "safe_transport_stage", None)
        if isinstance(error_stage, str) and error_stage in _SAFE_TRANSPORT_STAGES:
            payload["safe_transport_stage"] = error_stage
    if error is not None and safe_http_status is None:
        safe_http_status = getattr(error, "safe_http_status", None)
    if error is not None:
        payload["safe_error_class"] = safe_error_class(error)
    if type(safe_http_status) is int and 100 <= safe_http_status <= 599:
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


class TestConnectionDispatchDiagnosticMiddleware:
    """Observe the exact request before route dependency resolution."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self._app = app

    async def __call__(self, scope: Mapping[str, Any], receive: Any, send: Any) -> None:
        if (
            scope.get("type") != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != _REQUEST_PATH
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
                    "HTTP_TEST_CONNECTION_RESPONSE",
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
            emit(
                "HTTP_TEST_CONNECTION_REQUEST_RECEIVED",
                result="RECEIVED",
            )
            try:
                await self._app(scope, receive, send_observed)
            except Exception as error:
                if not response_reported:
                    emit("HTTP_TEST_CONNECTION_RESPONSE", result="FAILURE", error=error)
                    response_reported = True
                raise
            finally:
                if not response_reported:
                    emit("HTTP_TEST_CONNECTION_RESPONSE", result="FAILURE", provider=_PROVIDER)


__all__ = (
    "ALLOWED_EVENT_FIELDS",
    "DIAGNOSTIC_BUILD_ID",
    "DIAGNOSTIC_ENVIRONMENT_VARIABLE",
    "DIAGNOSTIC_STAGES",
    "TestConnectionDispatchDiagnosticMiddleware",
    "diagnostic_output_path",
    "emit",
    "enabled",
    "new_request_id",
    "request_scope",
    "safe_error_class",
)
