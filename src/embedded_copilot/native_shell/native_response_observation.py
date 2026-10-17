"""Delivery-independent, content-free native WebView response observations.

The sink is deliberately local, append-only, default-off and isolated from the
frontend and HTTP diagnostics.  Raw response data is used only for in-memory
route classification and is never serialized.
"""

from __future__ import annotations

import ipaddress
import json
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit


NATIVE_RESPONSE_DIAGNOSTIC_ENVIRONMENT_VARIABLE = "EMBRIX_NATIVE_WEBVIEW_RESPONSE_DIAGNOSTIC"
NATIVE_OBSERVATION_DELIVERY = "LOCAL_DIRECT_FILE_APPEND"
_OUTPUT_DIRECTORY = "embrix-p0-1g"
_OUTPUT_FILENAME_PREFIX = "native-webview-response"

_ROUTE_PATHS = {
    "/api/v1/copilot/desktop/system/bootstrap/exchange": "SESSION_EXCHANGE",
    "/api/v1/copilot/projects/locations": "PROJECT_LOCATIONS",
    "/api/v1/copilot/desktop/system/provider": "PROVIDER",
}

NATIVE_RESPONSE_ALLOWED_FIELDS = frozenset(
    {
        "timestamp",
        "runtime_pid",
        "native_response_sequence",
        "route",
        "route_match",
        "http_status",
        "host_class",
        "listener_port_match",
        "classification_result",
        "safe_exception_class",
    }
)


@dataclass(frozen=True, slots=True)
class NativeResponseClassification:
    route: str
    route_match: bool
    host_class: str
    listener_port_match: bool | str
    classification_result: str


def native_response_diagnostic_enabled() -> bool:
    return os.getenv(NATIVE_RESPONSE_DIAGNOSTIC_ENVIRONMENT_VARIABLE) == "1"


def native_response_output_path(*, runtime_pid: int | None = None) -> Path:
    pid = runtime_pid if type(runtime_pid) is int and runtime_pid > 0 else os.getpid()
    return Path(tempfile.gettempdir()) / _OUTPUT_DIRECTORY / f"{_OUTPUT_FILENAME_PREFIX}-{pid}.jsonl"


def listener_port_from_origin(origin: object) -> int | None:
    if not isinstance(origin, str) or not origin:
        return None
    try:
        parsed = urlsplit(origin)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    return port if type(port) is int and 1 <= port <= 65535 else None


def _host_class(host: str | None) -> str:
    if not host:
        return "UNKNOWN"
    if host.casefold() == "localhost":
        return "LOOPBACK"
    try:
        return "LOOPBACK" if ipaddress.ip_address(host).is_loopback else "NON_LOOPBACK"
    except ValueError:
        return "NON_LOOPBACK"


def _listener_port_match(
    *,
    host_class: str,
    response_port: int | None,
    listener_port: int | None,
) -> bool | str:
    if host_class == "UNKNOWN" or listener_port is None or response_port is None:
        return "UNKNOWN"
    return host_class == "LOOPBACK" and response_port == listener_port


def classify_native_response_url(
    url: object,
    *,
    listener_port: int | None,
) -> NativeResponseClassification:
    if not isinstance(url, str) or not url:
        return NativeResponseClassification("OTHER", False, "UNKNOWN", "UNKNOWN", "MALFORMED")
    try:
        parsed = urlsplit(url)
        response_port = parsed.port
    except (TypeError, ValueError):
        return NativeResponseClassification("OTHER", False, "UNKNOWN", "UNKNOWN", "MALFORMED")

    host_class = _host_class(parsed.hostname)
    port_match = _listener_port_match(
        host_class=host_class,
        response_port=response_port,
        listener_port=listener_port,
    )
    route = _ROUTE_PATHS.get(parsed.path)
    if route is not None and host_class == "LOOPBACK" and port_match is True:
        return NativeResponseClassification(route, True, host_class, port_match, "MATCHED")
    return NativeResponseClassification("OTHER", False, host_class, port_match, "IGNORED")


def _safe_status(value: object) -> int | None:
    return value if type(value) is int and 100 <= value <= 599 else None


def _safe_exception_class(error: BaseException) -> str:
    if isinstance(error, AttributeError):
        return "ATTRIBUTE_ERROR"
    if isinstance(error, TypeError):
        return "TYPE_ERROR"
    if isinstance(error, KeyError):
        return "KEY_ERROR"
    if isinstance(error, ValueError):
        return "VALUE_ERROR"
    return "UNKNOWN"


def _append_json_line(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n")
        stream.flush()


class NativeWebViewResponseObservationSink:
    """Append tiny safe observations without becoming a runtime dependency."""

    def __init__(
        self,
        *,
        listener_port: int | None,
        output_path: Path | None = None,
        runtime_pid: int | None = None,
    ) -> None:
        self._listener_port = listener_port if type(listener_port) is int and 1 <= listener_port <= 65535 else None
        self._runtime_pid = runtime_pid if type(runtime_pid) is int and runtime_pid > 0 else os.getpid()
        self._output_path = output_path or native_response_output_path(runtime_pid=self._runtime_pid)
        self._lock = threading.Lock()
        self._sequence = 0
        self._drop_count = 0

    @property
    def drop_count(self) -> int:
        with self._lock:
            return self._drop_count

    def _payload(
        self,
        *,
        sequence: int,
        classification: NativeResponseClassification,
        status: int | None,
        safe_exception_class: str,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "runtime_pid": self._runtime_pid,
            "native_response_sequence": sequence,
            "route": classification.route,
            "route_match": classification.route_match,
            "host_class": classification.host_class,
            "listener_port_match": classification.listener_port_match,
            "classification_result": classification.classification_result,
            "safe_exception_class": safe_exception_class,
        }
        if status is not None:
            payload["http_status"] = status
        return payload

    def observe(self, response: object) -> None:
        if not native_response_diagnostic_enabled():
            return
        with self._lock:
            self._sequence += 1
            sequence = self._sequence
            try:
                url = getattr(response, "url", None)
                status = _safe_status(getattr(response, "status_code", None))
                classification = classify_native_response_url(url, listener_port=self._listener_port)
                if status is None and classification.classification_result != "ERROR":
                    classification = NativeResponseClassification(
                        classification.route,
                        classification.route_match,
                        classification.host_class,
                        classification.listener_port_match,
                        "MALFORMED",
                    )
                payload = self._payload(
                    sequence=sequence,
                    classification=classification,
                    status=status,
                    safe_exception_class="NONE",
                )
            except Exception as error:  # noqa: BLE001 - callback evidence is optional
                payload = self._payload(
                    sequence=sequence,
                    classification=NativeResponseClassification(
                        "OTHER",
                        False,
                        "UNKNOWN",
                        "UNKNOWN",
                        "ERROR",
                    ),
                    status=None,
                    safe_exception_class=_safe_exception_class(error),
                )
            if set(payload) - NATIVE_RESPONSE_ALLOWED_FIELDS:
                self._drop_count += 1
                return
            try:
                _append_json_line(self._output_path, payload)
            except (OSError, TypeError, ValueError):
                self._drop_count += 1


__all__ = (
    "NATIVE_OBSERVATION_DELIVERY",
    "NATIVE_RESPONSE_ALLOWED_FIELDS",
    "NATIVE_RESPONSE_DIAGNOSTIC_ENVIRONMENT_VARIABLE",
    "NativeResponseClassification",
    "NativeWebViewResponseObservationSink",
    "classify_native_response_url",
    "listener_port_from_origin",
    "native_response_diagnostic_enabled",
    "native_response_output_path",
)
