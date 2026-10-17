"""Native WebView2 presentation adapter.

No pywebview/pythonnet object escapes this module.  The rest of the product
receives only ``NativeShellResult`` through ``NativeDesktopHostPort``.
"""

from __future__ import annotations

import logging
import sys
import threading
import webbrowser
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from .handoff_telemetry import record_native_handoff
from .native_response_observation import (
    NativeWebViewResponseObservationSink,
    listener_port_from_origin,
)
from .picker import (
    NativeProjectRootBridge,
    NativeProjectRootPicker,
    NativeProjectRootPickerPort,
)
from .policy import NavigationAction, decide_navigation
from embedded_copilot.production_release.webview_session_diagnostic import (
    WEBVIEW_NETWORK_CORRELATION_STRATEGY,
    classify_webview_network_route,
    emit as emit_webview_network_diagnostic,
    normalize_webview_network_method,
)

logger = logging.getLogger(__name__)
_NETWORK_SEQUENCE_LOCK = threading.Lock()
_NETWORK_SEQUENCE = 0


def _native_icon_path() -> str | None:
    """Resolve the packaged/source Embrix ICO for the WinForms title bar."""

    meipass = getattr(sys, "_MEIPASS", None)
    candidates = [Path(meipass) / "brand" / "embrix-app-icon-white.ico"] if meipass else []
    candidates.append(Path(__file__).resolve().parents[3] / "frontend" / "public" / "brand" / "embrix-app-icon-white.ico")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def _window_hwnd(window: object) -> int:
    """Read the WinForms owner handle only after pywebview has created it."""

    try:
        native = getattr(window, "native", None)
        handle = getattr(native, "Handle", None)
        if handle is None:
            return 0
        to_int64 = getattr(handle, "ToInt64", None)
        if callable(to_int64):
            return int(to_int64() or 0)
        to_int32 = getattr(handle, "ToInt32", None)
        if callable(to_int32):
            return int(to_int32() or 0)
        return int(handle or 0)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return 0


def _safe_log(event_name: str, error: BaseException | None = None) -> None:
    extra: dict[str, str] = {
        "event_name": event_name,
        "trace_id": uuid4().hex,
        "error_type": "-",
    }
    if error is not None:
        extra["error_type"] = type(error).__name__
    logger.warning(event_name, extra=extra)


class NativeShellError(StrEnum):
    WEBVIEW_RUNTIME_UNAVAILABLE = "WEBVIEW_RUNTIME_UNAVAILABLE"
    WEBVIEW_START_FAILED = "WEBVIEW_START_FAILED"
    WEBVIEW_CLOSED = "WEBVIEW_CLOSED"


@dataclass(frozen=True, slots=True)
class NativeShellConfig:
    title: str = "Embrix"
    width: int = 1280
    height: int = 800
    min_width: int = 960
    min_height: int = 600
    startup_timeout_seconds: float = 15.0
    approved_external_origins: tuple[str, ...] = ()
    debug: bool = False


@dataclass(frozen=True, slots=True)
class NativeShellResult:
    status: str
    error: NativeShellError | None = None


class NativeDesktopHostPort(Protocol):
    def show(self, url: str, *, internal_origin: str, config: NativeShellConfig) -> NativeShellResult:
        """Show the product URL and block until the native window closes."""


class _PassiveWebViewNetworkObserver:
    """Observe pywebview's public network events without mutating event objects."""

    correlation_strategy = WEBVIEW_NETWORK_CORRELATION_STRATEGY

    def __init__(self, *, native_response_sink: object | None = None) -> None:
        self._lock = threading.Lock()
        self._bound_events: object | None = None
        self._request_handler = self._handle_request
        self._response_handler = self._handle_response
        self._native_response_sink = native_response_sink

    @staticmethod
    def _event_container(window: object) -> tuple[object, object, object] | None:
        try:
            events = getattr(window, "events")
            request_sent = getattr(events, "request_sent")
            response_received = getattr(events, "response_received")
        except Exception:  # noqa: BLE001 - unavailable event API is fail-closed
            return None
        return events, request_sent, response_received

    @staticmethod
    def _remove_handlers(events: object, request_handler: object, response_handler: object) -> None:
        try:
            request_sent = getattr(events, "request_sent")
            request_sent -= request_handler
        except Exception:  # noqa: BLE001 - cleanup must never affect the host
            pass
        try:
            response_received = getattr(events, "response_received")
            response_received -= response_handler
        except Exception:  # noqa: BLE001 - cleanup must never affect the host
            pass

    def bind(self, window: object) -> bool:
        event_container = self._event_container(window)
        if event_container is None:
            return False
        events, request_sent, response_received = event_container
        with self._lock:
            if self._bound_events is events:
                return True
            if self._bound_events is not None:
                self._remove_handlers(self._bound_events, self._request_handler, self._response_handler)
            try:
                request_sent += self._request_handler
                response_received += self._response_handler
            except Exception:  # noqa: BLE001 - observation is optional and fail-closed
                self._remove_handlers(events, self._request_handler, self._response_handler)
                self._bound_events = None
                return False
            self._bound_events = events
            return True

    def unbind(self, window: object | None = None) -> None:
        with self._lock:
            events = self._bound_events
            if events is None:
                return
            if window is not None:
                try:
                    if getattr(window, "events") is not events:
                        return
                except Exception:  # noqa: BLE001 - cleanup must remain best effort
                    return
            self._bound_events = None
            self._remove_handlers(events, self._request_handler, self._response_handler)

    def _allocate_sequence(self, route: str) -> int:
        del route
        global _NETWORK_SEQUENCE
        with self._lock:
            with _NETWORK_SEQUENCE_LOCK:
                _NETWORK_SEQUENCE += 1
                sequence = _NETWORK_SEQUENCE
            return sequence

    @staticmethod
    def _safe_status(value: object) -> int | None:
        return value if type(value) is int and 100 <= value <= 599 else None

    @staticmethod
    def _response_result(status: int | None) -> str:
        if status is None:
            return "RECEIVED"
        if 200 <= status < 300:
            return "SUCCESS"
        if status in {401, 403}:
            return "AUTH_REJECTED"
        return "FAILURE"

    @staticmethod
    def _emit_request(route: str, method: str, sequence: int) -> None:
        try:
            emit_webview_network_diagnostic(
                "WEBVIEW_NETWORK_REQUESTED",
                result="RECEIVED",
                route=route,
                method=method,
                request_sequence=sequence,
                origin="WEBVIEW",
            )
        except Exception:  # noqa: BLE001 - diagnostic delivery is isolated
            pass

    @staticmethod
    def _callback_exception_class(error: Exception) -> str:
        if isinstance(error, AttributeError):
            return "ATTRIBUTE_ERROR"
        if isinstance(error, TypeError):
            return "TYPE_ERROR"
        if isinstance(error, KeyError):
            return "KEY_ERROR"
        if isinstance(error, ValueError):
            return "VALUE_ERROR"
        return "UNKNOWN"

    @staticmethod
    def _emit_response_observation(
        route: str,
        status: int | None,
        *,
        classification_result: str,
        exception_class: str = "NONE",
        response_callback_stage: str = "RESPONSE_EVENT_PARSE",
    ) -> None:
        try:
            emit_webview_network_diagnostic(
                "WEBVIEW_RESPONSE_OBSERVATION",
                result="RECEIVED" if classification_result in {"MATCHED", "IGNORED"} else "FAILURE",
                route=route,
                http_status=status,
                route_match=route in {"PROVIDER", "PROJECT_LOCATIONS"},
                classification_result=classification_result,
                response_callback_invoked=True,
                response_callback_stage=response_callback_stage,
                response_callback_safe_exception_class=exception_class,
                origin="WEBVIEW",
            )
        except Exception:  # noqa: BLE001 - diagnostic delivery is isolated
            pass

    def _handle_request(self, request: object) -> None:
        try:
            route = classify_webview_network_route(
                getattr(request, "url"),
                method=getattr(request, "method"),
            )
            if route is None:
                return
            method = normalize_webview_network_method(getattr(request, "method"))
            sequence = self._allocate_sequence(route)
            self._emit_request(route, method, sequence)
        except Exception:  # noqa: BLE001 - event callback must never escape to pywebview
            pass

    def _handle_response(self, response: object) -> None:
        try:
            if self._native_response_sink is not None:
                observe = getattr(self._native_response_sink, "observe")
                observe(response)
        except Exception:  # noqa: BLE001 - native diagnostics cannot affect pywebview
            pass
        stage = "RESPONSE_EVENT_PARSE"
        try:
            route = classify_webview_network_route(getattr(response, "url"))
            stage = "RESPONSE_ROUTE_CLASSIFICATION"
            if route is None:
                self._emit_response_observation(
                    "OTHER",
                    None,
                    classification_result="IGNORED",
                    response_callback_stage=stage,
                )
                return
            stage = "RESPONSE_EVENT_PARSE"
            status = self._safe_status(getattr(response, "status_code"))
            if status is None:
                self._emit_response_observation(
                    route,
                    None,
                    classification_result="MALFORMED",
                    exception_class="UNKNOWN",
                    response_callback_stage=stage,
                )
                return
            self._emit_response_observation(route, status, classification_result="MATCHED")
        except Exception as error:  # noqa: BLE001 - event callback must never escape to pywebview
            self._emit_response_observation(
                "OTHER",
                None,
                classification_result="MALFORMED",
                response_callback_stage=stage,
                exception_class=self._callback_exception_class(error),
            )


class PyWebView2DesktopHost:
    """pywebview EdgeChromium adapter kept behind the thin host port."""

    def __init__(self, *, picker: NativeProjectRootPickerPort | None = None) -> None:
        self._picker = picker or NativeProjectRootPicker()
        self._window: object | None = None
        self.frontend_ready = threading.Event()

    def request_close(self) -> None:
        """Close this native window after the update restart response is sent."""
        window = self._window
        if window is not None:
            window.destroy()

    def show(self, url: str, *, internal_origin: str, config: NativeShellConfig) -> NativeShellResult:
        self.frontend_ready.clear()
        record_native_handoff("H4_WEBVIEW_RUNTIME_INIT", "ENTER")
        try:
            import webview
            from webview.platforms import edgechromium
        except Exception as error:  # noqa: BLE001
            record_native_handoff(
                "H4_WEBVIEW_RUNTIME_INIT",
                "FAIL",
                exception_type=type(error).__name__,
                reason_code="WEBVIEW_RUNTIME_IMPORT_FAILED",
            )
            return NativeShellResult("FAILED", NativeShellError.WEBVIEW_RUNTIME_UNAVAILABLE)
        record_native_handoff("H4_WEBVIEW_RUNTIME_INIT", "PASS")

        original_navigation = edgechromium.EdgeChrome.on_navigation_start
        original_new_window = edgechromium.EdgeChrome.on_new_window_request
        original_download = edgechromium.EdgeChrome.on_download_starting
        original_ready = edgechromium.EdgeChrome.on_webview_ready
        ready = threading.Event()
        result: list[NativeShellResult] = []
        native_response_sink = NativeWebViewResponseObservationSink(
            listener_port=listener_port_from_origin(internal_origin),
        )
        network_observer = _PassiveWebViewNetworkObserver(
            native_response_sink=native_response_sink,
        )
        window: object | None = None

        def safe_navigation(host, sender, args):
            decision = decide_navigation(
                str(args.Uri),
                internal_origin=internal_origin,
                approved_external_origins=config.approved_external_origins,
            )
            if decision.action is NavigationAction.BLOCK:
                args.Cancel = True
                return
            if decision.action is NavigationAction.OPEN_EXTERNAL:
                args.Cancel = True
                webbrowser.open(decision.url)
                return
            original_navigation(host, sender, args)

        def safe_new_window(host, sender, args):
            args.set_Handled(True)
            decision = decide_navigation(
                str(args.get_Uri()),
                internal_origin=internal_origin,
                approved_external_origins=config.approved_external_origins,
            )
            if decision.action is NavigationAction.OPEN_EXTERNAL:
                webbrowser.open(decision.url)

        def safe_download(host, sender, args):
            args.Cancel = True

        def safe_ready(host, sender, args):
            if not args.IsSuccess:
                record_native_handoff(
                    "H6_FRONTEND_LOAD",
                    "FAIL",
                    exception_type="WebView2RuntimeError",
                    reason_code="WEBVIEW_NAVIGATION_FAILED",
                )
                result.append(NativeShellResult("FAILED", NativeShellError.WEBVIEW_RUNTIME_UNAVAILABLE))
                ready.set()
                return
            original_ready(host, sender, args)
            record_native_handoff("H6_FRONTEND_LOAD", "PASS")
            self.frontend_ready.set()
            ready.set()

        edgechromium.EdgeChrome.on_navigation_start = safe_navigation
        edgechromium.EdgeChrome.on_new_window_request = safe_new_window
        edgechromium.EdgeChrome.on_download_starting = safe_download
        edgechromium.EdgeChrome.on_webview_ready = safe_ready
        webview.settings["ALLOW_FILE_URLS"] = False
        webview.settings["ALLOW_DOWNLOADS"] = False
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
        webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = config.debug

        try:
            record_native_handoff("H5_WINDOW_CREATE", "ENTER")
            window = webview.create_window(
                config.title,
                url,
                js_api=NativeProjectRootBridge(self._picker),
                width=config.width,
                height=config.height,
                min_size=(config.min_width, config.min_height),
                resizable=True,
            )
            if window is None:
                record_native_handoff("H5_WINDOW_CREATE", "FAIL", reason_code="WINDOW_CREATE_RETURNED_NONE")
                return NativeShellResult("FAILED", NativeShellError.WEBVIEW_START_FAILED)
            self._window = window
            record_native_handoff("H5_WINDOW_CREATE", "PASS")
            record_native_handoff("H7_BOOTSTRAP_URL_READ", "PASS", reason_code="URL_DELIVERED_TO_WEBVIEW")
            network_observer.bind(window)

            set_owner_provider = getattr(self._picker, "set_owner_hwnd_provider", None)
            if callable(set_owner_provider):
                set_owner_provider(lambda: _window_hwnd(window))

            def watchdog() -> None:
                if not ready.wait(config.startup_timeout_seconds):
                    result.append(NativeShellResult("FAILED", NativeShellError.WEBVIEW_START_FAILED))
                    try:
                        window.destroy()
                    except Exception as error:  # noqa: BLE001
                        _safe_log("native_shell_watchdog_close_failed", error)

            watchdog_thread = threading.Thread(target=watchdog, name="embedded-copilot-webview-watchdog", daemon=True)
            watchdog_thread.start()
            webview.start(gui="edgechromium", debug=config.debug, private_mode=True, icon=_native_icon_path())
            return result[0] if result else NativeShellResult("CLOSED", NativeShellError.WEBVIEW_CLOSED)
        except Exception as error:  # noqa: BLE001
            record_native_handoff(
                "H5_WINDOW_CREATE",
                "FAIL",
                exception_type=type(error).__name__,
                reason_code="NATIVE_WINDOW_START_FAILED",
            )
            _safe_log("native_shell_failed", error)
            return NativeShellResult("FAILED", NativeShellError.WEBVIEW_RUNTIME_UNAVAILABLE)
        finally:
            self._window = None
            network_observer.unbind(window)
            edgechromium.EdgeChrome.on_navigation_start = original_navigation
            edgechromium.EdgeChrome.on_new_window_request = original_new_window
            edgechromium.EdgeChrome.on_download_starting = original_download
            edgechromium.EdgeChrome.on_webview_ready = original_ready
