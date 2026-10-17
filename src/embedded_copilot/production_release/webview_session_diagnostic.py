"""Opt-in, content-free diagnostics for the Desktop WebView session boundary.

This module is deliberately independent from the Test Connection diagnostic.
It observes only fixed request/response and identity states.  It never accepts
or serializes cookie values, session tokens, bootstrap nonces, headers, bodies,
response payloads, or exception messages.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
import re
import secrets
import tempfile
import threading
from urllib.parse import urlsplit
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DIAGNOSTIC_ENVIRONMENT_VARIABLE = "EMBRIX_WEBVIEW_SESSION_DIAGNOSTIC"
DIAGNOSTIC_BUILD_ID = "P0-1G15H2"
FRONTEND_DIAGNOSTIC_BUILD_ID = "P0-1G15H2-FE"
DIAGNOSTIC_ORIGIN_HEADER = "X-Embrix-Diagnostic-Origin"
DIAGNOSTIC_ORIGIN_VALUE = "webview"
DESKTOP_SESSION_COOKIE = "EmbeddedCopilotSession"

_OUTPUT_DIRECTORY = "embrix-p0-1g"
_OUTPUT_FILENAME = "webview-session-diagnostic.jsonl"
_SESSION_EXCHANGE_PATH = "/api/v1/copilot/desktop/system/bootstrap/exchange"
_PROTECTED_ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/v1/copilot/desktop/system/provider"): "PROVIDER_PROJECTION",
    ("GET", "/api/v1/copilot/desktop/system/health"): "SYSTEM_HEALTH",
    ("GET", "/api/v1/copilot/capabilities"): "CAPABILITIES",
    ("GET", "/api/v1/copilot/projects/locations"): "PROJECT_LOCATIONS",
}
_NETWORK_ROUTE_PATHS: dict[str, str] = {
    "/api/v1/copilot/desktop/system/provider": "PROVIDER",
    "/api/v1/copilot/projects/locations": "PROJECT_LOCATIONS",
}
WEBVIEW_NETWORK_ROUTES = frozenset(_NETWORK_ROUTE_PATHS.values())
WEBVIEW_NETWORK_METHODS = frozenset(
    {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "OTHER"}
)
WEBVIEW_NETWORK_CORRELATION_STRATEGY = "ROUTE_LEVEL_ONLY"

ALLOWED_EVENT_FIELDS = frozenset(
    {
        "timestamp",
        "pid",
        "build_id",
        "stage",
        "result",
        "route",
        "request_id",
        "cookie_present",
        "identity_result",
        "http_status",
        "safe_error_class",
        "origin_marker",
        "fetch_attempt_id",
        "event_sequence",
        "fetch_attempt_started_at",
        "fetch_attempt_finished_at",
        "fetch_attempt_duration_ms",
        "project_locations_attempt_started_at",
        "project_locations_attempt_finished_at",
        "project_locations_attempt_duration_ms",
        "frontend_fetch_resolved",
        "frontend_http_status",
        "final_result",
        "request_sequence",
        "method",
        "failure_stage",
        "projection_processing_result",
        "projection_state",
        "credential_state",
        "provider_present",
        "fetcher_identity",
        "production_fetcher_identity",
        "frontend_origin_class",
        "provider_target_origin_class",
        "frontend_provider_same_origin",
        "provider_request_url_strategy",
        "request_credentials_mode",
        "document_ready_state_at_fetch",
        "navigation_state_at_fetch",
        "document_generation_at_fetch",
        "document_generation_at_rejection",
        "navigation_started_during_fetch",
        "document_context_changed_during_fetch",
        "abort_signal_attached_to_provider_fetch",
        "abort_trigger_observed",
        "abort_trigger_source",
        "fetch_rejection_error_class",
        "fetch_rejection_error_name",
        "fetch_rejection_message_category",
        "fetch_failure_kind",
        "fetch_failure_stage",
        "response_callback_invoked",
        "response_callback_stage",
        "response_callback_safe_exception_class",
        "route_match",
        "classification_result",
        "backend_response_status",
        "asgi_final_body_send_completed",
        "asgi_send_failure_safe_class",
        "more_body_final",
        "backend_origin_header_class",
        "render_generation",
        "method_class",
        "content_type_class",
        "content_length_present",
        "transfer_encoding_present",
        "content_encoding_present",
        "connection_close_present",
        "response_body_mode",
        "middleware_chain_class",
        "cors_header_presence",
        "corp_policy_category",
        "cache_control_presence",
    }
)

DIAGNOSTIC_STAGES = frozenset(
    {
        "WEBVIEW_SESSION_EXCHANGE_REQUEST_RECEIVED",
        "WEBVIEW_SESSION_EXCHANGE_ROUTE_ENTERED",
        "WEBVIEW_SESSION_CREATED",
        "WEBVIEW_SESSION_COOKIE_SET",
        "WEBVIEW_SESSION_EXCHANGE_RESPONSE",
        "WEBVIEW_FRONTEND_PROVIDER_FETCH_BEGIN",
        "WEBVIEW_FRONTEND_PROVIDER_FETCHER_CALL_BEGIN",
        "WEBVIEW_FRONTEND_PROVIDER_FETCHER_CALL_RESOLVED",
        "WEBVIEW_FRONTEND_PROVIDER_FETCHER_CALL_REJECTED",
        "WEBVIEW_FRONTEND_PROVIDER_FETCH_PROMISE_RESOLVED",
        "WEBVIEW_FRONTEND_PROVIDER_FETCH_PROMISE_REJECTED",
        "WEBVIEW_FRONTEND_PROVIDER_RESPONSE_RECEIVED",
        "WEBVIEW_FRONTEND_PROVIDER_JSON_PARSE_SUCCESS",
        "WEBVIEW_FRONTEND_PROVIDER_JSON_PARSE_FAILURE",
        "WEBVIEW_FRONTEND_PROVIDER_PROJECTION_PROCESSING_SUCCESS",
        "WEBVIEW_FRONTEND_PROVIDER_PROJECTION_PROCESSING_FAILURE",
        "WEBVIEW_FRONTEND_PROVIDER_PROJECTION_VALIDATE_SUCCESS",
        "WEBVIEW_FRONTEND_PROVIDER_PROJECTION_VALIDATE_FAILURE",
        "WEBVIEW_FRONTEND_PROVIDER_STATE_COMMIT",
        "WEBVIEW_FRONTEND_PROVIDER_RENDER_STATE",
        "WEBVIEW_FRONTEND_PROVIDER_FETCH_SUCCESS",
        "WEBVIEW_FRONTEND_PROVIDER_FETCH_FAILURE",
        "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_BEGIN",
        "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_PROMISE_RESOLVED",
        "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_SUCCESS",
        "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_FAILURE",
        "WEBVIEW_FRONTEND_READINESS_PASS",
        "WEBVIEW_NETWORK_RESPONSE_CALLBACK",
        "WEBVIEW_RESPONSE_OBSERVATION",
        "WEBVIEW_PROVIDER_RESPONSE_SEND_BEGIN",
        "WEBVIEW_PROVIDER_RESPONSE_SEND_COMPLETE",
        "WEBVIEW_PROVIDER_RESPONSE_SEND_FAILURE",
        "WEBVIEW_PROJECT_LOCATIONS_RESPONSE_SEND_BEGIN",
        "WEBVIEW_PROJECT_LOCATIONS_RESPONSE_SEND_COMPLETE",
        "WEBVIEW_PROJECT_LOCATIONS_RESPONSE_SEND_FAILURE",
        "WEBVIEW_SESSION_EXCHANGE_RESPONSE_SEND_BEGIN",
        "WEBVIEW_SESSION_EXCHANGE_RESPONSE_SEND_COMPLETE",
        "WEBVIEW_SESSION_EXCHANGE_RESPONSE_SEND_FAILURE",
        "WEBVIEW_NETWORK_REQUESTED",
        "WEBVIEW_NETWORK_RESPONSE_RECEIVED",
        "WEBVIEW_PROTECTED_REQUEST_RECEIVED",
        "WEBVIEW_DESKTOP_IDENTITY_BEGIN",
        "WEBVIEW_DESKTOP_IDENTITY_PASS",
        "WEBVIEW_DESKTOP_IDENTITY_MISSING",
        "WEBVIEW_DESKTOP_IDENTITY_EXPIRED",
        "WEBVIEW_DESKTOP_IDENTITY_UNKNOWN",
        "WEBVIEW_DESKTOP_IDENTITY_INVALID",
        "WEBVIEW_PROTECTED_RESPONSE",
    }
)

DIAGNOSTIC_ROUTES = frozenset(
    {
        "SESSION_EXCHANGE",
        "PROVIDER_PROJECTION",
        "SYSTEM_HEALTH",
        "CAPABILITIES",
        "PROJECT_LOCATIONS",
        "PROVIDER",
        "OTHER",
    }
)

DIAGNOSTIC_RESULTS = frozenset(
    {
        "RECEIVED",
        "ENTERED",
        "SUCCESS",
        "FAILURE",
        "EMITTED",
        "AUTH_REJECTED",
        "NOT_APPLICABLE",
    }
)

IDENTITY_RESULTS = frozenset({"PASS", "MISSING", "EXPIRED", "UNKNOWN", "INVALID"})
SAFE_ERROR_CLASSES = frozenset(
    {
        "NONE",
        "AUTH_REQUIRED",
        "SESSION_EXPIRED",
        "SESSION_UNKNOWN",
        "HTTP_ERROR",
        "HTTP_STATUS_ERROR",
        "NETWORK_ERROR",
        "FETCH_REJECTED",
        "NETWORK_TRANSPORT_ERROR",
        "UNKNOWN_FETCH_REJECTION",
        "FRONTEND_FETCH_ERROR",
        "DIAGNOSTIC_IO_ERROR",
        "JSON_PARSE_ERROR",
        "PROJECTION_PROCESSING_ERROR",
        "PROJECTION_VALIDATION_ERROR",
        "REQUEST_ABORTED",
        "STATE_PROCESSING_ERROR",
        "DIAGNOSTIC_DELIVERY_ERROR",
        "UNKNOWN",
    }
)
ORIGIN_MARKERS = frozenset({"WEBVIEW", "UNATTRIBUTED"})
PROJECTION_STATES = frozenset(
    {"NULL", "LOADING", "RECEIVED", "VALIDATED", "CONFIGURED", "UNCONFIGURED", "UNAVAILABLE", "ERROR"}
)
CREDENTIAL_STATES = frozenset({"CONFIGURED", "UNCONFIGURED", "UNKNOWN"})
FETCHER_IDENTITIES = frozenset(
    {
        "GLOBAL_BROWSER_FETCH",
        "GLOBAL_THIS_FETCH",
        "WINDOW_FETCH",
        "CUSTOM_WRAPPER",
        "INJECTED_FETCHER",
        "OTHER",
        "UNKNOWN",
    }
)
ORIGIN_CLASSES = frozenset({"LOOPBACK_HTTP", "LOCALHOST_HTTP", "FILE", "CUSTOM_SCHEME", "HTTPS_REMOTE", "OTHER", "UNKNOWN"})
TARGET_ORIGIN_CLASSES = frozenset({"SAME_ORIGIN", "LOOPBACK_CROSS_ORIGIN", "LOCALHOST_CROSS_ORIGIN", "CUSTOM_TO_LOOPBACK", "OTHER", "UNKNOWN"})
URL_STRATEGIES = frozenset({"RELATIVE_SAME_ORIGIN", "ABSOLUTE_CURRENT_LOOPBACK", "ABSOLUTE_DIFFERENT_LOOPBACK", "CUSTOM_BACKEND_ORIGIN", "OTHER"})
CREDENTIALS_MODES = frozenset({"SAME_ORIGIN", "INCLUDE", "OMIT", "DEFAULT", "UNKNOWN"})
READY_STATES = frozenset({"LOADING", "INTERACTIVE", "COMPLETE", "UNKNOWN"})
NAVIGATION_STATES = frozenset({"STABLE", "NAVIGATING", "UNKNOWN"})
ABORT_SIGNAL_STATES = frozenset({"NONE", "ATTACHED", "UNKNOWN"})
ABORT_SOURCES = frozenset({"NONE", "COMPONENT_CLEANUP", "NAVIGATION", "SESSION_BOOTSTRAP", "TIMEOUT", "OTHER", "UNKNOWN"})
REJECTION_ERROR_CLASSES = frozenset({"ABORT_ERROR", "TYPE_ERROR", "DOM_EXCEPTION", "OTHER", "UNKNOWN"})
REJECTION_ERROR_NAMES = frozenset({"TypeError", "AbortError", "NetworkError", "SecurityError", "InvalidStateError", "NotAllowedError", "Other", "Unknown"})
REJECTION_MESSAGE_CATEGORIES = frozenset({"FAILED_TO_FETCH", "LOAD_FAILED", "NETWORK_ERROR", "ILLEGAL_INVOCATION", "BLOCKED_BY_CLIENT_OR_POLICY", "CONNECTION_REFUSED", "CONNECTION_RESET", "OTHER_SANITIZED", "EMPTY", "UNKNOWN"})
FETCH_FAILURE_KINDS = frozenset({"PROMISE_REJECTION", "HTTP_STATUS", "JSON_PARSE", "RESPONSE_BODY", "ABORT", "OTHER", "UNKNOWN"})
FETCH_FAILURE_STAGES = frozenset({"FETCH_CALL", "HTTP_STATUS", "RESPONSE_BODY", "PROJECTION_PROCESSING", "STATE_PROCESSING", "UNKNOWN"})
RESPONSE_CALLBACK_STAGES = frozenset({"RESPONSE_EVENT_PARSE", "RESPONSE_ROUTE_CLASSIFICATION", "DIAGNOSTIC_EMIT"})
RESPONSE_CALLBACK_EXCEPTION_CLASSES = frozenset({"NONE", "ATTRIBUTE_ERROR", "TYPE_ERROR", "KEY_ERROR", "VALUE_ERROR", "UNKNOWN"})
RESPONSE_CLASSIFICATION_RESULTS = frozenset({"MATCHED", "IGNORED", "MALFORMED", "ERROR"})
CONTENT_TYPE_CLASSES = frozenset({"JSON", "HTML", "TEXT", "BINARY", "OTHER", "ABSENT", "UNKNOWN"})
RESPONSE_BODY_MODES = frozenset({"NORMAL", "STREAMING", "EMPTY", "UNKNOWN"})
MIDDLEWARE_CHAIN_CLASSES = frozenset({"PUBLIC_SESSION_EXCHANGE", "PROTECTED_DESKTOP_IDENTITY", "UNKNOWN"})
CORP_POLICY_CATEGORIES = frozenset({"SAME_ORIGIN", "SAME_SITE", "CROSS_ORIGIN", "OTHER", "ABSENT", "UNKNOWN"})

_PROVIDER_ATTEMPT_BEGIN_STAGE = "WEBVIEW_FRONTEND_PROVIDER_FETCH_BEGIN"
_PROVIDER_ATTEMPT_TERMINAL_STAGES = frozenset(
    {
        "WEBVIEW_FRONTEND_PROVIDER_FETCH_FAILURE",
        "WEBVIEW_FRONTEND_PROVIDER_FETCH_SUCCESS",
        "WEBVIEW_FRONTEND_PROVIDER_FETCH_PROMISE_RESOLVED",
    }
)
_EXACT_REJECTION_ERROR_NAMES = frozenset(
    {"TypeError", "AbortError", "NetworkError", "SecurityError", "InvalidStateError", "NotAllowedError"}
)
BACKEND_ORIGIN_HEADER_CLASSES = frozenset({"SAME_ORIGIN", "LOOPBACK_SAME_PORT", "LOOPBACK_OTHER_PORT", "LOCALHOST", "FILE_OR_NULL", "CUSTOM_ORIGIN", "NO_ORIGIN", "OTHER", "UNKNOWN"})
FAILURE_STAGES = frozenset(
    {"FETCH", "HTTP_STATUS", "JSON_PARSE", "PROJECTION_PROCESSING", "STATE_PROCESSING", "UNKNOWN"}
)
PROJECTION_PROCESSING_RESULTS = frozenset({"SUCCESS", "FAILURE", "NOT_APPLICABLE"})

_REQUEST_ID_PATTERN = re.compile(
    r"^(?:webview-session-[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)
_DIAGNOSTIC_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "webview_session_diagnostic_request_id",
    default=None,
)
_WRITE_LOCK = threading.Lock()


def enabled() -> bool:
    """Return whether this diagnostic sink was explicitly enabled."""

    return os.environ.get(DIAGNOSTIC_ENVIRONMENT_VARIABLE) == "1"


def new_request_id() -> str:
    """Create a bounded correlation id unrelated to any session material."""

    return "webview-session-" + secrets.token_hex(16)


def _valid_request_id(value: object) -> str | None:
    return value if isinstance(value, str) and _REQUEST_ID_PATTERN.fullmatch(value) else None


@contextlib.contextmanager
def request_scope(request_id: str | None = None) -> Iterator[str]:
    """Bind an existing safe correlation id or create a diagnostic id."""

    value = _valid_request_id(request_id) or new_request_id()
    token = _REQUEST_ID.set(value)
    try:
        yield value
    finally:
        _REQUEST_ID.reset(token)


def request_id_for_scope(scope: Mapping[str, Any]) -> str | None:
    state = scope.get("state")
    if isinstance(state, Mapping):
        value = _valid_request_id(state.get("webview_session_diagnostic_request_id"))
        if value is not None:
            return value
    return _valid_request_id(_REQUEST_ID.get())


def diagnostic_output_path() -> Path:
    return Path(tempfile.gettempdir()) / _OUTPUT_DIRECTORY / _OUTPUT_FILENAME


def _valid_diagnostic_timestamp(value: object) -> bool:
    return isinstance(value, str) and len(value) == 24 and _DIAGNOSTIC_TIMESTAMP_PATTERN.fullmatch(value) is not None


def _parse_diagnostic_timestamp(value: object) -> datetime | None:
    if not _valid_diagnostic_timestamp(value):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _valid_diagnostic_status(value: object) -> bool:
    return value == "UNKNOWN" or (type(value) is int and 100 <= value <= 599)


def normalize_webview_network_method(value: object) -> str:
    """Return only the fixed HTTP method enum used by network diagnostics."""

    try:
        candidate = value if isinstance(value, str) else str(value)
    except Exception:  # noqa: BLE001 - untrusted WebView event value
        return "OTHER"
    candidate = candidate.upper()
    return candidate if candidate in WEBVIEW_NETWORK_METHODS else "OTHER"


def classify_webview_network_route(url: object, *, method: object | None = None) -> str | None:
    """Classify the two approved routes without retaining URL components."""

    try:
        raw_url = url if isinstance(url, str) else str(url)
        path = urlsplit(raw_url).path
    except Exception:  # noqa: BLE001 - malformed event URL is ignored
        return None
    route = _NETWORK_ROUTE_PATHS.get(path)
    if route is None:
        return None
    if method is not None and normalize_webview_network_method(method) != "GET":
        return None
    return route


def provider_diagnostic_window(
    events: Iterable[Mapping[str, Any]],
    fetch_attempt_id: str,
) -> tuple[str, str] | None:
    """Return the bounded timestamp window for one provider fetch attempt."""

    event_list = tuple(events)
    rows: list[tuple[datetime, str, str]] = []
    for event in event_list:
        if event.get("fetch_attempt_id") != fetch_attempt_id:
            continue
        timestamp = event.get("timestamp")
        stage = event.get("stage")
        if not isinstance(timestamp, str) or not isinstance(stage, str):
            continue
        try:
            parsed = datetime.fromisoformat(timestamp)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            else:
                parsed = parsed.astimezone(UTC)
        except ValueError:
            continue
        rows.append((parsed, timestamp, stage))
    # Prefer the terminal event's self-contained bounds so a dropped begin
    # marker cannot erase the only reliable attempt window.
    terminal_events: list[tuple[datetime, Mapping[str, Any]]] = []
    for event in event_list:
        if event.get("fetch_attempt_id") != fetch_attempt_id or event.get("stage") not in _PROVIDER_ATTEMPT_TERMINAL_STAGES:
            continue
        started_at = _parse_diagnostic_timestamp(event.get("fetch_attempt_started_at"))
        finished_at = _parse_diagnostic_timestamp(event.get("fetch_attempt_finished_at"))
        if started_at is not None and finished_at is not None and finished_at >= started_at:
            terminal_events.append((finished_at, event))
    if terminal_events:
        _, terminal = min(terminal_events, key=lambda row: row[0])
        return str(terminal["fetch_attempt_started_at"]), str(terminal["fetch_attempt_finished_at"])

    # Keep the legacy reconstruction as a compatibility path for old logs.
    starts = [row for row in rows if row[2] == _PROVIDER_ATTEMPT_BEGIN_STAGE]
    if not starts:
        return None
    start = min(starts, key=lambda row: row[0])
    terminals = [row for row in rows if row[2] in _PROVIDER_ATTEMPT_TERMINAL_STAGES and row[0] >= start[0]]
    if not terminals:
        return None
    end = min(terminals, key=lambda row: row[0])
    return start[1], end[1]


def provider_diagnostic_window_evidence(
    events: Iterable[Mapping[str, Any]],
    fetch_attempt_id: str,
) -> dict[str, object]:
    """Summarize only fixed provider route evidence inside one attempt window.

    The attempt count is derived from frontend begin markers, response count from
    observed provider response markers, and the concurrency value counts extra
    same-route request markers in the bounded interval. Missing fire-and-forget
    markers remain unknown to callers; this helper never turns absence into a
    negative execution claim.
    """

    event_list = tuple(events)
    window = provider_diagnostic_window(event_list, fetch_attempt_id)
    result: dict[str, object] = {
        "diagnostic_window_start": window[0] if window else None,
        "diagnostic_window_end": window[1] if window else None,
        "provider_fetch_attempt_count_in_window": 0,
        "provider_matching_request_count_in_window": 0,
        "provider_matching_response_count_in_window": 0,
        "provider_backend_response_count_in_window": 0,
        "provider_response_count_in_window": 0,
        "provider_concurrent_same_route_requests": None,
    }
    if window is None:
        return result
    try:
        window_start = datetime.fromisoformat(window[0])
        window_end = datetime.fromisoformat(window[1])
        if window_start.tzinfo is None:
            window_start = window_start.replace(tzinfo=UTC)
        else:
            window_start = window_start.astimezone(UTC)
        if window_end.tzinfo is None:
            window_end = window_end.replace(tzinfo=UTC)
        else:
            window_end = window_end.astimezone(UTC)
    except ValueError:
        return result

    in_window: list[tuple[datetime, Mapping[str, Any]]] = []
    for event in event_list:
        timestamp = event.get("timestamp")
        if not isinstance(timestamp, str):
            continue
        try:
            parsed = datetime.fromisoformat(timestamp)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            else:
                parsed = parsed.astimezone(UTC)
        except ValueError:
            continue
        if window_start <= parsed <= window_end:
            in_window.append((parsed, event))

    attempt_markers = [
        event
        for _, event in in_window
        if event.get("fetch_attempt_id") == fetch_attempt_id
        and event.get("stage") in _PROVIDER_ATTEMPT_TERMINAL_STAGES
    ]
    request_markers = [
        event
        for _, event in in_window
        if event.get("stage") == "WEBVIEW_NETWORK_REQUESTED"
        and event.get("route") == "PROVIDER"
    ]
    response_markers = [
        event
        for _, event in in_window
        if (
            event.get("stage") == "WEBVIEW_NETWORK_RESPONSE_RECEIVED"
            or (
                event.get("stage") == "WEBVIEW_RESPONSE_OBSERVATION"
                and event.get("route") == "PROVIDER"
                and event.get("route_match") is True
                and event.get("classification_result") == "MATCHED"
            )
        )
        and event.get("route") == "PROVIDER"
    ]
    backend_response_markers = [
        event
        for _, event in in_window
        if (
            event.get("stage") == "WEBVIEW_PROTECTED_RESPONSE"
            and event.get("route") == "PROVIDER_PROJECTION"
        )
        or (
            event.get("stage") in {"WEBVIEW_PROVIDER_RESPONSE_SEND_COMPLETE", "WEBVIEW_PROVIDER_RESPONSE_SEND_FAILURE"}
            and event.get("route") == "PROVIDER"
        )
    ]
    result["provider_fetch_attempt_count_in_window"] = len(attempt_markers)
    result["provider_matching_request_count_in_window"] = len(request_markers)
    result["provider_matching_response_count_in_window"] = len(response_markers)
    result["provider_backend_response_count_in_window"] = len(backend_response_markers)
    result["provider_response_count_in_window"] = len(response_markers)
    if request_markers:
        result["provider_concurrent_same_route_requests"] = max(0, len(request_markers) - 1)
    return result


def project_locations_response_control_valid(
    *,
    webview_request_observed: bool,
    webview_response_observed: bool,
    webview_response_status: int | None,
    backend_request_observed: bool,
    backend_http_status: int | None,
    frontend_fetch_resolved: bool,
    result: str,
) -> bool:
    """Require full Project Locations frontend, WebView and backend success."""

    return (
        webview_request_observed
        and webview_response_observed
        and webview_response_status == 200
        and backend_request_observed
        and backend_http_status == 200
        and frontend_fetch_resolved
        and result == "SUCCESS"
    )


def evaluate_provider_response_resolution_gate(
    *,
    control_group_valid: bool,
    provider_webview_request_observed: bool,
    provider_backend_request_observed: bool,
    provider_backend_http_status: int | None,
    provider_backend_identity_result: str | None,
    provider_webview_response_observed: bool,
    provider_webview_response_status: int | None,
    provider_fetch_attempt_count_in_window: int,
    provider_response_count_in_window: int,
    provider_concurrent_same_route_requests: int,
    fetch_failure_kind: str | None,
    fetch_failure_stage: str | None,
    fetch_rejection_error_name: str | None,
    fetch_rejection_message_category: str | None,
) -> dict[str, object]:
    """Evaluate route-level evidence without claiming a request/response pair."""

    dispatch_confirmed = provider_webview_request_observed or provider_backend_request_observed
    if not control_group_valid:
        classification = "RESPONSE_OBSERVATION_NOT_PROVEN"
    elif (
        provider_fetch_attempt_count_in_window != 1
        or provider_response_count_in_window != 1
        or provider_concurrent_same_route_requests != 0
    ):
        classification = "RESPONSE_CORRELATION_AMBIGUOUS"
    elif (
        not dispatch_confirmed
        or provider_backend_http_status != 200
        or provider_backend_identity_result != "PASS"
    ):
        classification = "MECHANISM_UNCONFIRMED"
    elif not provider_webview_response_observed or provider_webview_response_status != 200:
        classification = "PROVIDER_RESPONSE_NOT_PROVEN"
    elif (
        fetch_failure_kind == "PROMISE_REJECTION"
        and fetch_failure_stage == "FETCH_CALL"
        and fetch_rejection_error_name in _EXACT_REJECTION_ERROR_NAMES
        and fetch_rejection_message_category in REJECTION_MESSAGE_CATEGORIES - {"UNKNOWN"}
    ):
        classification = "BACKEND_RESPONSE_TO_JAVASCRIPT_FETCH_RESOLUTION_BOUNDARY"
    else:
        classification = "MECHANISM_UNCONFIRMED"
    return {
        "provider_network_dispatch_confirmed": dispatch_confirmed,
        "provider_response_correlation_mode": "ROUTE_LEVEL_ONLY",
        "mechanism_classification": classification,
    }


def origin_marker(request: Any) -> str:
    """Map only the fixed diagnostic marker; never infer origin from User-Agent."""

    try:
        value = request.headers.get(DIAGNOSTIC_ORIGIN_HEADER)
    except (AttributeError, TypeError):
        value = None
    return "WEBVIEW" if value == DIAGNOSTIC_ORIGIN_VALUE else "UNATTRIBUTED"


def backend_origin_header_class(request: Any) -> str:
    """Classify Origin without retaining its value or treating loopback names as equal."""

    try:
        value = request.headers.get("origin")
        if not value:
            return "NO_ORIGIN"
        parsed = urlsplit(value)
        if parsed.scheme == "file" or value == "null":
            return "FILE_OR_NULL"
        request_url = request.url
        request_scheme = str(request_url.scheme)
        request_host = str(request_url.hostname or "")
        request_port = request_url.port
        parsed_port = parsed.port
        if parsed_port is None:
            parsed_port = 443 if parsed.scheme == "https" else 80 if parsed.scheme == "http" else None
        if request_port is None:
            request_port = 443 if request_scheme == "https" else 80 if request_scheme == "http" else None
        if parsed.scheme == request_scheme and parsed.hostname == request_host and parsed_port == request_port:
            return "SAME_ORIGIN"
        if parsed.hostname in {"127.0.0.1", "localhost"} and request_host in {"127.0.0.1", "localhost"}:
            return "LOOPBACK_SAME_PORT" if parsed_port == request_port else "LOOPBACK_OTHER_PORT"
        if parsed.hostname == "localhost":
            return "LOCALHOST"
        if parsed.scheme in {"http", "https"}:
            return "CUSTOM_ORIGIN"
        return "OTHER"
    except (AttributeError, TypeError, ValueError):
        return "UNKNOWN"


def cookie_present(request: Any) -> bool:
    """Return only whether the session cookie name is present."""

    try:
        return DESKTOP_SESSION_COOKIE in request.cookies
    except (AttributeError, TypeError):
        return False


def identity_result_for_code(code: object) -> str:
    """Map current production session rejection codes without changing them."""

    if code == "DESKTOP_IDENTITY_REQUIRED":
        return "MISSING"
    if code == "DESKTOP_SESSION_EXPIRED":
        return "EXPIRED"
    if code == "DESKTOP_IDENTITY_INVALID":
        return "UNKNOWN"
    return "INVALID"


def safe_error_class(error: BaseException | None = None, *, code: object = None) -> str | None:
    """Map only fixed categories; never include an exception message."""

    if code == "DESKTOP_BOOTSTRAP_REJECTED":
        return "AUTH_REQUIRED"
    if code == "DESKTOP_IDENTITY_REQUIRED":
        return "AUTH_REQUIRED"
    if code == "DESKTOP_SESSION_EXPIRED":
        return "SESSION_EXPIRED"
    if code == "DESKTOP_IDENTITY_INVALID":
        return "SESSION_UNKNOWN"
    if error is None:
        return None
    if isinstance(error, (TypeError, ValueError)):
        return "HTTP_ERROR"
    return "UNKNOWN"


def _status_result(status: int | None) -> str:
    if status is not None and 200 <= status < 300:
        return "SUCCESS"
    if status in {401, 403}:
        return "AUTH_REJECTED"
    return "FAILURE"


def _safe_asgi_response_metadata(message: Mapping[str, Any]) -> dict[str, object]:
    raw_headers = message.get("headers")
    if not isinstance(raw_headers, (list, tuple)):
        return {
            "content_type_class": "UNKNOWN",
            "content_length_present": False,
            "transfer_encoding_present": False,
            "content_encoding_present": False,
            "connection_close_present": False,
            "cors_header_presence": False,
            "corp_policy_category": "UNKNOWN",
            "cache_control_presence": False,
        }

    names: set[bytes] = set()
    content_type = "ABSENT"
    corp_policy = "ABSENT"
    connection_close = False
    for item in raw_headers:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        raw_name, raw_value = item
        if not isinstance(raw_name, bytes) or not isinstance(raw_value, bytes):
            continue
        name = raw_name.lower()
        names.add(name)
        if name == b"content-type":
            value = raw_value.strip().lower()
            if value.startswith(b"application/json"):
                content_type = "JSON"
            elif value.startswith(b"text/html"):
                content_type = "HTML"
            elif value.startswith(b"text/"):
                content_type = "TEXT"
            elif value.startswith((b"application/octet-stream", b"image/", b"audio/", b"video/")):
                content_type = "BINARY"
            else:
                content_type = "OTHER"
        elif name == b"connection":
            connection_close = raw_value.strip().lower() == b"close"
        elif name == b"cross-origin-resource-policy":
            value = raw_value.strip().lower()
            corp_policy = {
                b"same-origin": "SAME_ORIGIN",
                b"same-site": "SAME_SITE",
                b"cross-origin": "CROSS_ORIGIN",
            }.get(value, "OTHER")

    return {
        "content_type_class": content_type,
        "content_length_present": b"content-length" in names,
        "transfer_encoding_present": b"transfer-encoding" in names,
        "content_encoding_present": b"content-encoding" in names,
        "connection_close_present": connection_close,
        "cors_header_presence": any(name.startswith(b"access-control-") for name in names),
        "corp_policy_category": corp_policy,
        "cache_control_presence": b"cache-control" in names,
    }


def _safe_response_body_mode(message: Mapping[str, Any], *, streamed: bool) -> str:
    if streamed:
        return "STREAMING"
    body = message.get("body")
    if isinstance(body, (bytes, bytearray, memoryview)):
        return "EMPTY" if len(body) == 0 else "NORMAL"
    return "UNKNOWN"


def emit(
    stage: str,
    *,
    result: str,
    route: str | None = None,
    request_id: str | None = None,
    cookie_present_value: bool | None = None,
    identity_result: str | None = None,
    http_status: int | None = None,
    error: BaseException | None = None,
    error_code: object = None,
    safe_error_class_value: str | None = None,
    fetch_attempt_id: str | None = None,
    event_sequence: int | None = None,
    fetch_attempt_started_at: str | None = None,
    fetch_attempt_finished_at: str | None = None,
    fetch_attempt_duration_ms: int | None = None,
    project_locations_attempt_started_at: str | None = None,
    project_locations_attempt_finished_at: str | None = None,
    project_locations_attempt_duration_ms: int | None = None,
    frontend_fetch_resolved: bool | None = None,
    frontend_http_status: int | str | None = None,
    final_result: str | None = None,
    request_sequence: int | None = None,
    method: str | None = None,
    failure_stage: str | None = None,
    projection_processing_result: str | None = None,
    projection_state: str | None = None,
    credential_state: str | None = None,
    provider_present: bool | None = None,
    fetcher_identity: str | None = None,
    production_fetcher_identity: str | None = None,
    frontend_origin_class: str | None = None,
    provider_target_origin_class: str | None = None,
    frontend_provider_same_origin: bool | None = None,
    provider_request_url_strategy: str | None = None,
    request_credentials_mode: str | None = None,
    document_ready_state_at_fetch: str | None = None,
    navigation_state_at_fetch: str | None = None,
    document_generation_at_fetch: int | None = None,
    document_generation_at_rejection: int | None = None,
    navigation_started_during_fetch: bool | None = None,
    document_context_changed_during_fetch: bool | None = None,
    abort_signal_attached_to_provider_fetch: str | None = None,
    abort_trigger_observed: bool | None = None,
    abort_trigger_source: str | None = None,
    fetch_rejection_error_class: str | None = None,
    fetch_rejection_error_name: str | None = None,
    fetch_rejection_message_category: str | None = None,
    fetch_failure_kind: str | None = None,
    fetch_failure_stage: str | None = None,
    response_callback_invoked: bool | None = None,
    response_callback_stage: str | None = None,
    response_callback_safe_exception_class: str | None = None,
    route_match: bool | None = None,
    classification_result: str | None = None,
    backend_response_status: int | str | None = None,
    asgi_final_body_send_completed: bool | str | None = None,
    asgi_send_failure_safe_class: str | None = None,
    more_body_final: bool | str | None = None,
    backend_origin_header_class: str | None = None,
    render_generation: int | None = None,
    method_class: str | None = None,
    content_type_class: str | None = None,
    content_length_present: bool | None = None,
    transfer_encoding_present: bool | None = None,
    content_encoding_present: bool | None = None,
    connection_close_present: bool | None = None,
    response_body_mode: str | None = None,
    middleware_chain_class: str | None = None,
    cors_header_presence: bool | None = None,
    corp_policy_category: str | None = None,
    cache_control_presence: bool | None = None,
    build_id_value: str | None = None,
    origin: str = "UNATTRIBUTED",
) -> None:
    """Append one fixed-schema event, silently and safely."""

    if not enabled():
        return
    if stage not in DIAGNOSTIC_STAGES or result not in DIAGNOSTIC_RESULTS:
        return
    if build_id_value not in {None, DIAGNOSTIC_BUILD_ID, FRONTEND_DIAGNOSTIC_BUILD_ID}:
        return
    if route is not None and route not in DIAGNOSTIC_ROUTES:
        return
    if identity_result is not None and identity_result not in IDENTITY_RESULTS:
        return
    if origin not in ORIGIN_MARKERS:
        return
    if method is not None and (not isinstance(method, str) or method not in WEBVIEW_NETWORK_METHODS):
        return
    if cookie_present_value is not None and type(cookie_present_value) is not bool:
        return
    if http_status is not None and (type(http_status) is not int or not 100 <= http_status <= 599):
        return
    if projection_state is not None and projection_state not in PROJECTION_STATES:
        return
    if credential_state is not None and credential_state not in CREDENTIAL_STATES:
        return
    if provider_present is not None and type(provider_present) is not bool:
        return
    if fetcher_identity is not None and fetcher_identity not in FETCHER_IDENTITIES:
        return
    if production_fetcher_identity is not None and production_fetcher_identity not in FETCHER_IDENTITIES:
        return
    if frontend_origin_class is not None and frontend_origin_class not in ORIGIN_CLASSES:
        return
    if provider_target_origin_class is not None and provider_target_origin_class not in TARGET_ORIGIN_CLASSES:
        return
    if frontend_provider_same_origin is not None and type(frontend_provider_same_origin) is not bool:
        return
    if provider_request_url_strategy is not None and provider_request_url_strategy not in URL_STRATEGIES:
        return
    if request_credentials_mode is not None and request_credentials_mode not in CREDENTIALS_MODES:
        return
    if document_ready_state_at_fetch is not None and document_ready_state_at_fetch not in READY_STATES:
        return
    if navigation_state_at_fetch is not None and navigation_state_at_fetch not in NAVIGATION_STATES:
        return
    if document_generation_at_fetch is not None and (type(document_generation_at_fetch) is not int or not 0 <= document_generation_at_fetch <= 1_000_000):
        return
    if document_generation_at_rejection is not None and (type(document_generation_at_rejection) is not int or not 0 <= document_generation_at_rejection <= 1_000_000):
        return
    if navigation_started_during_fetch is not None and type(navigation_started_during_fetch) is not bool:
        return
    if document_context_changed_during_fetch is not None and type(document_context_changed_during_fetch) is not bool:
        return
    if abort_signal_attached_to_provider_fetch is not None and abort_signal_attached_to_provider_fetch not in ABORT_SIGNAL_STATES:
        return
    if abort_trigger_observed is not None and type(abort_trigger_observed) is not bool:
        return
    if abort_trigger_source is not None and abort_trigger_source not in ABORT_SOURCES:
        return
    if fetch_rejection_error_class is not None and fetch_rejection_error_class not in REJECTION_ERROR_CLASSES:
        return
    if fetch_rejection_error_name is not None and fetch_rejection_error_name not in REJECTION_ERROR_NAMES:
        return
    if fetch_rejection_message_category is not None and fetch_rejection_message_category not in REJECTION_MESSAGE_CATEGORIES:
        return
    if fetch_failure_kind is not None and fetch_failure_kind not in FETCH_FAILURE_KINDS:
        return
    if fetch_failure_stage is not None and fetch_failure_stage not in FETCH_FAILURE_STAGES:
        return
    if response_callback_invoked is not None and type(response_callback_invoked) is not bool:
        return
    if response_callback_stage is not None and response_callback_stage not in RESPONSE_CALLBACK_STAGES:
        return
    if response_callback_safe_exception_class is not None and response_callback_safe_exception_class not in RESPONSE_CALLBACK_EXCEPTION_CLASSES:
        return
    if route_match is not None and type(route_match) is not bool:
        return
    if classification_result is not None and classification_result not in RESPONSE_CLASSIFICATION_RESULTS:
        return
    if backend_response_status is not None and not _valid_diagnostic_status(backend_response_status):
        return
    if asgi_final_body_send_completed is not None and asgi_final_body_send_completed not in {True, False, "UNKNOWN"}:
        return
    if asgi_send_failure_safe_class is not None and asgi_send_failure_safe_class not in SAFE_ERROR_CLASSES:
        return
    if more_body_final is not None and more_body_final not in {True, False, "UNKNOWN"}:
        return
    if backend_origin_header_class is not None and backend_origin_header_class not in BACKEND_ORIGIN_HEADER_CLASSES:
        return
    if method_class is not None and method_class not in WEBVIEW_NETWORK_METHODS:
        return
    if content_type_class is not None and content_type_class not in CONTENT_TYPE_CLASSES:
        return
    for flag in (
        content_length_present,
        transfer_encoding_present,
        content_encoding_present,
        connection_close_present,
        cors_header_presence,
        cache_control_presence,
    ):
        if flag is not None and type(flag) is not bool:
            return
    if response_body_mode is not None and response_body_mode not in RESPONSE_BODY_MODES:
        return
    if middleware_chain_class is not None and middleware_chain_class not in MIDDLEWARE_CHAIN_CLASSES:
        return
    if corp_policy_category is not None and corp_policy_category not in CORP_POLICY_CATEGORIES:
        return
    if safe_error_class_value is not None and safe_error_class_value not in SAFE_ERROR_CLASSES:
        return
    if fetch_attempt_id is not None and not re.fullmatch(r"^provider-fetch-[1-9][0-9]{0,8}$", fetch_attempt_id):
        return
    if event_sequence is not None and (type(event_sequence) is not int or not 1 <= event_sequence <= 1_000_000):
        return
    for timestamp in (
        fetch_attempt_started_at,
        fetch_attempt_finished_at,
        project_locations_attempt_started_at,
        project_locations_attempt_finished_at,
    ):
        if timestamp is not None and not _valid_diagnostic_timestamp(timestamp):
            return
    for duration in (fetch_attempt_duration_ms, project_locations_attempt_duration_ms):
        if duration is not None and (type(duration) is not int or not 0 <= duration <= 86_400_000):
            return
    if frontend_fetch_resolved is not None and type(frontend_fetch_resolved) is not bool:
        return
    if frontend_http_status is not None and not _valid_diagnostic_status(frontend_http_status):
        return
    if final_result is not None and final_result not in {"SUCCESS", "FAILURE"}:
        return
    if request_sequence is not None and (type(request_sequence) is not int or not 1 <= request_sequence <= 1_000_000):
        return
    if failure_stage is not None and failure_stage not in FAILURE_STAGES:
        return
    if projection_processing_result is not None and projection_processing_result not in PROJECTION_PROCESSING_RESULTS:
        return
    if render_generation is not None and (type(render_generation) is not int or not 0 <= render_generation <= 1_000_000):
        return
    safe_id = _valid_request_id(request_id) or _valid_request_id(_REQUEST_ID.get())
    if safe_id is None:
        safe_id = new_request_id()
    payload: dict[str, object] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "pid": os.getpid(),
        "build_id": build_id_value or DIAGNOSTIC_BUILD_ID,
        "stage": stage,
        "result": result,
        "request_id": safe_id,
        "origin_marker": origin,
    }
    if route is not None:
        payload["route"] = route
    if cookie_present_value is not None:
        payload["cookie_present"] = cookie_present_value
    if identity_result is not None:
        payload["identity_result"] = identity_result
    if http_status is not None:
        payload["http_status"] = http_status
    if fetch_attempt_id is not None:
        payload["fetch_attempt_id"] = fetch_attempt_id
    if event_sequence is not None:
        payload["event_sequence"] = event_sequence
    if fetch_attempt_started_at is not None:
        payload["fetch_attempt_started_at"] = fetch_attempt_started_at
    if fetch_attempt_finished_at is not None:
        payload["fetch_attempt_finished_at"] = fetch_attempt_finished_at
    if fetch_attempt_duration_ms is not None:
        payload["fetch_attempt_duration_ms"] = fetch_attempt_duration_ms
    if project_locations_attempt_started_at is not None:
        payload["project_locations_attempt_started_at"] = project_locations_attempt_started_at
    if project_locations_attempt_finished_at is not None:
        payload["project_locations_attempt_finished_at"] = project_locations_attempt_finished_at
    if project_locations_attempt_duration_ms is not None:
        payload["project_locations_attempt_duration_ms"] = project_locations_attempt_duration_ms
    if frontend_fetch_resolved is not None:
        payload["frontend_fetch_resolved"] = frontend_fetch_resolved
    if frontend_http_status is not None:
        payload["frontend_http_status"] = frontend_http_status
    if final_result is not None:
        payload["final_result"] = final_result
    if request_sequence is not None:
        payload["request_sequence"] = request_sequence
    if method is not None:
        payload["method"] = method
    if failure_stage is not None:
        payload["failure_stage"] = failure_stage
    if projection_processing_result is not None:
        payload["projection_processing_result"] = projection_processing_result
    if projection_state is not None:
        payload["projection_state"] = projection_state
    if credential_state is not None:
        payload["credential_state"] = credential_state
    if provider_present is not None:
        payload["provider_present"] = provider_present
    if fetcher_identity is not None:
        payload["fetcher_identity"] = fetcher_identity
    if production_fetcher_identity is not None:
        payload["production_fetcher_identity"] = production_fetcher_identity
    if frontend_origin_class is not None:
        payload["frontend_origin_class"] = frontend_origin_class
    if provider_target_origin_class is not None:
        payload["provider_target_origin_class"] = provider_target_origin_class
    if frontend_provider_same_origin is not None:
        payload["frontend_provider_same_origin"] = frontend_provider_same_origin
    if provider_request_url_strategy is not None:
        payload["provider_request_url_strategy"] = provider_request_url_strategy
    if request_credentials_mode is not None:
        payload["request_credentials_mode"] = request_credentials_mode
    if document_ready_state_at_fetch is not None:
        payload["document_ready_state_at_fetch"] = document_ready_state_at_fetch
    if navigation_state_at_fetch is not None:
        payload["navigation_state_at_fetch"] = navigation_state_at_fetch
    if document_generation_at_fetch is not None:
        payload["document_generation_at_fetch"] = document_generation_at_fetch
    if document_generation_at_rejection is not None:
        payload["document_generation_at_rejection"] = document_generation_at_rejection
    if navigation_started_during_fetch is not None:
        payload["navigation_started_during_fetch"] = navigation_started_during_fetch
    if document_context_changed_during_fetch is not None:
        payload["document_context_changed_during_fetch"] = document_context_changed_during_fetch
    if abort_signal_attached_to_provider_fetch is not None:
        payload["abort_signal_attached_to_provider_fetch"] = abort_signal_attached_to_provider_fetch
    if abort_trigger_observed is not None:
        payload["abort_trigger_observed"] = abort_trigger_observed
    if abort_trigger_source is not None:
        payload["abort_trigger_source"] = abort_trigger_source
    if fetch_rejection_error_class is not None:
        payload["fetch_rejection_error_class"] = fetch_rejection_error_class
    if fetch_rejection_error_name is not None:
        payload["fetch_rejection_error_name"] = fetch_rejection_error_name
    if fetch_rejection_message_category is not None:
        payload["fetch_rejection_message_category"] = fetch_rejection_message_category
    if fetch_failure_kind is not None:
        payload["fetch_failure_kind"] = fetch_failure_kind
    if fetch_failure_stage is not None:
        payload["fetch_failure_stage"] = fetch_failure_stage
    if response_callback_invoked is not None:
        payload["response_callback_invoked"] = response_callback_invoked
    if response_callback_stage is not None:
        payload["response_callback_stage"] = response_callback_stage
    if response_callback_safe_exception_class is not None:
        payload["response_callback_safe_exception_class"] = response_callback_safe_exception_class
    if route_match is not None:
        payload["route_match"] = route_match
    if classification_result is not None:
        payload["classification_result"] = classification_result
    if backend_response_status is not None:
        payload["backend_response_status"] = backend_response_status
    if asgi_final_body_send_completed is not None:
        payload["asgi_final_body_send_completed"] = asgi_final_body_send_completed
    if asgi_send_failure_safe_class is not None:
        payload["asgi_send_failure_safe_class"] = asgi_send_failure_safe_class
    if more_body_final is not None:
        payload["more_body_final"] = more_body_final
    if backend_origin_header_class is not None:
        payload["backend_origin_header_class"] = backend_origin_header_class
    if render_generation is not None:
        payload["render_generation"] = render_generation
    if method_class is not None:
        payload["method_class"] = method_class
    if content_type_class is not None:
        payload["content_type_class"] = content_type_class
    if content_length_present is not None:
        payload["content_length_present"] = content_length_present
    if transfer_encoding_present is not None:
        payload["transfer_encoding_present"] = transfer_encoding_present
    if content_encoding_present is not None:
        payload["content_encoding_present"] = content_encoding_present
    if connection_close_present is not None:
        payload["connection_close_present"] = connection_close_present
    if response_body_mode is not None:
        payload["response_body_mode"] = response_body_mode
    if middleware_chain_class is not None:
        payload["middleware_chain_class"] = middleware_chain_class
    if cors_header_presence is not None:
        payload["cors_header_presence"] = cors_header_presence
    if corp_policy_category is not None:
        payload["corp_policy_category"] = corp_policy_category
    if cache_control_presence is not None:
        payload["cache_control_presence"] = cache_control_presence
    mapped_error = safe_error_class_value or safe_error_class(error, code=error_code)
    if mapped_error is not None:
        payload["safe_error_class"] = mapped_error
    if set(payload) - ALLOWED_EVENT_FIELDS:
        return
    try:
        if not _WRITE_LOCK.acquire(blocking=False):
            return
        try:
            output = diagnostic_output_path()
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n")
                stream.flush()
        finally:
            _WRITE_LOCK.release()
    except (OSError, TypeError, ValueError):
        # Diagnostics must never change startup, auth, routing or response behavior.
        return


class WebViewSessionDiagnosticMiddleware:
    """Observe only the exchange and four fixed protected GET boundaries."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self._app = app

    async def __call__(self, scope: Mapping[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        method = str(scope.get("method", "")).upper()
        path = str(scope.get("path", ""))
        route = _PROTECTED_ROUTES.get((method, path))
        is_exchange = method == "POST" and path == _SESSION_EXCHANGE_PATH
        if route is None and not is_exchange:
            await self._app(scope, receive, send)
            return

        from starlette.requests import Request

        request = Request(scope, receive)
        request_id = new_request_id()
        state = scope.get("state")
        if isinstance(state, dict):
            state["webview_session_diagnostic_request_id"] = request_id
        marker = origin_marker(request)
        response_reported = False
        response_send_started = False
        response_send_completed = False
        response_send_failure_reported = False
        response_status: int | None = None
        response_metadata: dict[str, object] = _safe_asgi_response_metadata({})
        response_body_streaming = False
        is_provider_response = route == "PROVIDER_PROJECTION"
        is_project_locations_response = route == "PROJECT_LOCATIONS"
        observes_final_send = is_exchange or is_provider_response or is_project_locations_response
        final_send_route = (
            "SESSION_EXCHANGE"
            if is_exchange
            else "PROVIDER"
            if is_provider_response
            else "PROJECT_LOCATIONS"
        )
        final_send_stage_prefix = (
            "WEBVIEW_SESSION_EXCHANGE_RESPONSE_SEND"
            if is_exchange
            else "WEBVIEW_PROVIDER_RESPONSE_SEND"
            if is_provider_response
            else "WEBVIEW_PROJECT_LOCATIONS_RESPONSE_SEND"
        )
        method_class = normalize_webview_network_method(method)
        middleware_chain_class = (
            "PUBLIC_SESSION_EXCHANGE" if is_exchange else "PROTECTED_DESKTOP_IDENTITY"
        )

        if route is not None:
            emit(
                "WEBVIEW_PROTECTED_REQUEST_RECEIVED",
                result="RECEIVED",
                route=route,
                request_id=request_id,
                cookie_present_value=cookie_present(request),
                origin=marker,
            )
        elif is_exchange:
            emit(
                "WEBVIEW_SESSION_EXCHANGE_REQUEST_RECEIVED",
                result="RECEIVED",
                route="SESSION_EXCHANGE",
                request_id=request_id,
                origin=marker,
            )

        async def send_observed(message: Mapping[str, Any]) -> None:
            nonlocal response_reported, response_send_started, response_send_completed, response_send_failure_reported, response_status, response_metadata, response_body_streaming
            message_type = message.get("type")
            if observes_final_send and message_type == "http.response.start" and not response_send_started:
                status = message.get("status")
                response_status = status if type(status) is int and 100 <= status <= 599 else None
                response_metadata = _safe_asgi_response_metadata(message)
                emit(
                    f"{final_send_stage_prefix}_BEGIN",
                    result="ENTERED",
                    route=final_send_route,
                    request_id=request_id,
                    backend_response_status=response_status or "UNKNOWN",
                    asgi_final_body_send_completed="UNKNOWN",
                    asgi_send_failure_safe_class="NONE",
                    more_body_final="UNKNOWN",
                    method_class=method_class,
                    response_body_mode="UNKNOWN",
                    middleware_chain_class=middleware_chain_class,
                    **response_metadata,
                    origin=marker,
                )
                response_send_started = True
            if message.get("type") == "http.response.start" and not response_reported:
                status = message.get("status")
                safe_status = status if type(status) is int and 100 <= status <= 599 else None
                try:
                    await send(message)
                except Exception as error:
                    if observes_final_send and not response_send_failure_reported:
                        emit(
                            f"{final_send_stage_prefix}_FAILURE",
                            result="FAILURE",
                            route=final_send_route,
                            request_id=request_id,
                            safe_error_class_value="HTTP_ERROR",
                            backend_response_status=response_status or "UNKNOWN",
                            asgi_final_body_send_completed=False,
                            asgi_send_failure_safe_class="HTTP_ERROR",
                            more_body_final="UNKNOWN",
                            method_class=method_class,
                            response_body_mode="UNKNOWN",
                            middleware_chain_class=middleware_chain_class,
                            **response_metadata,
                            origin=marker,
                        )
                        response_send_failure_reported = True
                    raise
                if route is not None:
                    emit(
                        "WEBVIEW_PROTECTED_RESPONSE",
                        result=_status_result(safe_status),
                        route=route,
                        request_id=request_id,
                        http_status=safe_status,
                        origin=marker,
                    )
                elif is_exchange:
                    emit(
                        "WEBVIEW_SESSION_EXCHANGE_RESPONSE",
                        result=_status_result(safe_status),
                        route="SESSION_EXCHANGE",
                        request_id=request_id,
                        http_status=safe_status,
                        origin=marker,
                    )
                response_reported = True
                return
            if message_type == "http.response.body" and message.get("more_body", False):
                response_body_streaming = True
            try:
                await send(message)
            except Exception:
                if observes_final_send and not response_send_failure_reported:
                    emit(
                        f"{final_send_stage_prefix}_FAILURE",
                        result="FAILURE",
                        route=final_send_route,
                        request_id=request_id,
                        safe_error_class_value="HTTP_ERROR",
                        backend_response_status=response_status or "UNKNOWN",
                        asgi_final_body_send_completed=False,
                        asgi_send_failure_safe_class="HTTP_ERROR",
                        more_body_final=(False if message_type == "http.response.body" and message.get("more_body") is False else "UNKNOWN"),
                        method_class=method_class,
                        response_body_mode=(
                            _safe_response_body_mode(message, streamed=response_body_streaming)
                            if message_type == "http.response.body"
                            else "UNKNOWN"
                        ),
                        middleware_chain_class=middleware_chain_class,
                        **response_metadata,
                        origin=marker,
                    )
                    response_send_failure_reported = True
                raise
            if observes_final_send and message_type == "http.response.body" and not message.get("more_body", False) and not response_send_completed:
                emit(
                    f"{final_send_stage_prefix}_COMPLETE",
                    result="SUCCESS",
                    route=final_send_route,
                    request_id=request_id,
                    backend_response_status=response_status or "UNKNOWN",
                    asgi_final_body_send_completed=True,
                    asgi_send_failure_safe_class="NONE",
                    more_body_final=False,
                    method_class=method_class,
                    response_body_mode=_safe_response_body_mode(
                        message,
                        streamed=response_body_streaming,
                    ),
                    middleware_chain_class=middleware_chain_class,
                    **response_metadata,
                    origin=marker,
                )
                response_send_completed = True

        with request_scope(request_id):
            try:
                await self._app(scope, receive, send_observed)
            except Exception as error:
                if observes_final_send and response_send_started and not response_send_completed and not response_send_failure_reported:
                    emit(
                        f"{final_send_stage_prefix}_FAILURE",
                        result="FAILURE",
                        route=final_send_route,
                        request_id=request_id,
                        safe_error_class_value="HTTP_ERROR",
                        backend_response_status=response_status or "UNKNOWN",
                        asgi_final_body_send_completed=False,
                        asgi_send_failure_safe_class="HTTP_ERROR",
                        more_body_final="UNKNOWN",
                        method_class=method_class,
                        response_body_mode="UNKNOWN",
                        middleware_chain_class=middleware_chain_class,
                        **response_metadata,
                        origin=marker,
                    )
                    response_send_failure_reported = True
                if not response_reported:
                    if route is not None:
                        emit(
                            "WEBVIEW_PROTECTED_RESPONSE",
                            result="FAILURE",
                            route=route,
                            request_id=request_id,
                            error=error,
                            origin=marker,
                        )
                    elif is_exchange:
                        emit(
                            "WEBVIEW_SESSION_EXCHANGE_RESPONSE",
                            result="FAILURE",
                            route="SESSION_EXCHANGE",
                            request_id=request_id,
                            error=error,
                            origin=marker,
                        )
                raise


__all__ = (
    "ALLOWED_EVENT_FIELDS",
    "DESKTOP_SESSION_COOKIE",
    "DIAGNOSTIC_BUILD_ID",
    "DIAGNOSTIC_ENVIRONMENT_VARIABLE",
    "DIAGNOSTIC_ORIGIN_HEADER",
    "DIAGNOSTIC_ORIGIN_VALUE",
    "DIAGNOSTIC_RESULTS",
    "DIAGNOSTIC_ROUTES",
    "DIAGNOSTIC_STAGES",
    "IDENTITY_RESULTS",
    "FRONTEND_DIAGNOSTIC_BUILD_ID",
    "PROJECTION_STATES",
    "CREDENTIAL_STATES",
    "FAILURE_STAGES",
    "PROJECTION_PROCESSING_RESULTS",
    "SAFE_ERROR_CLASSES",
    "FETCH_FAILURE_KINDS",
    "FETCH_FAILURE_STAGES",
    "REJECTION_ERROR_NAMES",
    "REJECTION_MESSAGE_CATEGORIES",
    "RESPONSE_CALLBACK_STAGES",
    "RESPONSE_CALLBACK_EXCEPTION_CLASSES",
    "RESPONSE_CLASSIFICATION_RESULTS",
    "WEBVIEW_NETWORK_CORRELATION_STRATEGY",
    "WEBVIEW_NETWORK_METHODS",
    "WEBVIEW_NETWORK_ROUTES",
    "WebViewSessionDiagnosticMiddleware",
    "classify_webview_network_route",
    "evaluate_provider_response_resolution_gate",
    "cookie_present",
    "diagnostic_output_path",
    "emit",
    "enabled",
    "identity_result_for_code",
    "new_request_id",
    "normalize_webview_network_method",
    "origin_marker",
    "project_locations_response_control_valid",
    "provider_diagnostic_window",
    "provider_diagnostic_window_evidence",
    "request_id_for_scope",
    "request_scope",
    "safe_error_class",
)
