"""Production v7.0 launcher owning FastAPI and the Native WebView2 window."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import socket
import sys
import tempfile
import threading
import time
import webbrowser
from collections.abc import Callable, Sequence
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4

import uvicorn

from embedded_copilot.api.main import create_app
from embedded_copilot.native_shell import (
    NativeDesktopHostPort,
    NativeShellConfig,
    NativeShellResult,
    PyWebView2DesktopHost,
    SingleInstanceGuard,
)
from embedded_copilot.native_shell.handoff_telemetry import (
    LOGGER_NAME,
    record_native_handoff,
)
from embedded_copilot.native_shell.picker import probe_picker_runtime
from embedded_copilot.production_release.build_identity import read_build_identity
from embedded_copilot.production_release.configuration import (
    ProductionConfigurationRejected,
    load_production_settings,
)
from embedded_copilot.production_release.paths import ProductionStateLayout
from embedded_copilot.production_release.runtime import ProductionReleaseRuntime
from embedded_copilot.production_release.update_switch import mark_boot_ready
from embedded_copilot.production_release.webview_session_diagnostic import (
    DIAGNOSTIC_ENVIRONMENT_VARIABLE as WEBVIEW_SESSION_DIAGNOSTIC_ENVIRONMENT_VARIABLE,
)

logger = logging.getLogger("embedded_copilot.native_launcher")

_EARLY_STARTUP_LOG_NAME = "early-startup-diagnostic.log"


def _early_startup_log_paths() -> tuple[Path, ...]:
    """Return best-effort locations for diagnostics before normal logging."""

    candidates: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Embedded Copilot" / "logs")
    candidates.append(Path(tempfile.gettempdir()) / "Embedded Copilot" / "logs")
    return tuple(candidates)


def _early_exception_hash(stage: str, error: BaseException | None) -> str:
    if error is None:
        return "-"
    exception_type = f"{type(error).__module__}.{type(error).__qualname__}"
    digest = hashlib.sha256(f"{stage}|{exception_type}".encode("utf-8")).hexdigest()
    return digest[:16]


def _early_exception_context(error: BaseException | None) -> dict[str, object]:
    """Return safe exception origin fields without recording paths or messages."""

    if error is None:
        return {
            "exception_module": "-",
            "exception_function": "-",
            "exception_line": "-",
            "traceback_hash": "-",
        }

    exception_module = type(error).__module__
    exception_function = "-"
    exception_line: int | str = "-"
    frames: list[str] = []
    traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        exception_module = str(frame.f_globals.get("__name__", exception_module))
        exception_function = frame.f_code.co_name
        exception_line = traceback.tb_lineno
        frames.append(f"{exception_module}|{exception_function}|{exception_line}")
        traceback = traceback.tb_next

    traceback_hash = (
        hashlib.sha256("\n".join(frames).encode("utf-8")).hexdigest()[:16]
        if frames
        else "-"
    )
    return {
        "exception_module": exception_module,
        "exception_function": exception_function,
        "exception_line": exception_line,
        "traceback_hash": traceback_hash,
    }


def _early_startup_event(
    stage: str,
    status: str,
    error: BaseException | None = None,
    *,
    reason_code: str = "-",
    exit_code: int | None = None,
) -> None:
    """Write a redacted startup event without becoming a startup dependency."""

    event = {
        "stage": stage,
        "status": status,
        "exception_type": type(error).__name__ if error is not None else "-",
        "exception_hash": _early_exception_hash(stage, error),
        "reason_code": reason_code,
        "exit_code": exit_code,
    }
    event.update(_early_exception_context(error))
    payload = (json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        for directory in _early_startup_log_paths():
            try:
                directory.mkdir(parents=True, exist_ok=True)
                with (directory / _EARLY_STARTUP_LOG_NAME).open("ab") as stream:
                    stream.write(payload)
                    stream.flush()
                return
            except (OSError, ValueError):
                continue
    except (OSError, ValueError):
        return


def _early_startup_failure(stage: str, error: BaseException, reason_code: str) -> None:
    _early_startup_event(stage, "FAIL", error, reason_code=reason_code, exit_code=1)


_ANSWER_CONTRACT_DIAGNOSTIC_FIELDS = (
    "event_name",
    "route",
    "evidence_count",
    "gate_result",
    "model_gateway_invocation_count",
    "provider_status",
    "provider_response_present",
    "answer_validation_stage",
    "proposed_requirements_count",
    "open_questions_count",
    "answer_text_present",
    "reason_code",
    "terminal_event",
)
_ANSWER_CONTRACT_LOGGER = "embedded_copilot.desktop_copilot_chat.runtime"
_PROJECT_CATALOG_DIAGNOSTIC_FIELDS = (
    "event_name",
    "stage",
    "status",
    "exception_type",
    "reason_code",
    "target_exists",
    "write_attempted",
    "project_root_writable",
)
_PROJECT_CATALOG_DIAGNOSTIC_LOGGER = "embedded_copilot.project_catalog.runtime"
_PROVIDER_RESPONSE_TELEMETRY_LOGGERS = (
    "embedded_copilot.model_gateway.adapters.openai_compatible",
    "embedded_copilot.engineering_qa.service",
)
_PROVIDER_REQUEST_TELEMETRY_LOGGERS = (
    "embedded_copilot.model_gateway.adapters.openai_compatible",
)
_APPLICATION_EVIDENCE_TELEMETRY_LOGGER = "embedded_copilot.application.bindings"
_SNAPSHOT_TELEMETRY_LOGGER = "embedded_copilot.production_release.migration"
_MIGRATION_TELEMETRY_LOGGER = "embedded_copilot.production_release.migration"


class _NativeHandoffTelemetryFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "event_name", None) == "native_handoff_telemetry"


class _NativeHandoffTelemetryFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return (
            f"{self.formatTime(record)} {record.levelname} native_handoff_telemetry "
            f"status={getattr(record, 'telemetry_status', '-')} "
            f"stage={getattr(record, 'native_stage', '-')} "
            f"exception_type={getattr(record, 'exception_type', '-')} "
            f"reason_code={getattr(record, 'reason_code', '-')} "
            f"http_status={getattr(record, 'http_status', '-')} "
            f"win_error={getattr(record, 'win_error', '-')}"
        )


def _configure_native_handoff_telemetry(log_directory: Path) -> None:
    log_directory.mkdir(parents=True, exist_ok=True)
    target = logging.getLogger(LOGGER_NAME)
    if any(bool(getattr(handler, "_native_handoff_telemetry", False)) for handler in target.handlers):
        return
    handler = logging.FileHandler(log_directory / "native-host-handoff-telemetry.log", encoding="utf-8")
    handler._native_handoff_telemetry = True  # type: ignore[attr-defined]
    handler.addFilter(_NativeHandoffTelemetryFilter())
    handler.setFormatter(_NativeHandoffTelemetryFormatter())
    target.addHandler(handler)
    target.setLevel(logging.INFO)


class _AnswerContractDiagnosticFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "event_name", None) == "desktop_runtime_failure_trace"


class _AnswerContractDiagnosticFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            field: getattr(record, field, None)
            for field in _ANSWER_CONTRACT_DIAGNOSTIC_FIELDS
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _configure_answer_contract_diagnostics(log_directory: Path) -> None:
    """Persist the existing answer diagnostic event without identifiers or content."""

    log_directory.mkdir(parents=True, exist_ok=True)
    target = logging.getLogger(_ANSWER_CONTRACT_LOGGER)
    if any(
        bool(getattr(handler, "_answer_contract_diagnostic", False))
        for handler in target.handlers
    ):
        return
    handler = logging.FileHandler(
        log_directory / "answer-contract-diagnostics.log", encoding="utf-8"
    )
    handler._answer_contract_diagnostic = True  # type: ignore[attr-defined]
    handler.addFilter(_AnswerContractDiagnosticFilter())
    handler.setFormatter(_AnswerContractDiagnosticFormatter())
    target.addHandler(handler)
    if target.level == logging.NOTSET or target.level > logging.INFO:
        target.setLevel(logging.INFO)


class _ProjectCatalogDiagnosticFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "event_name", None) == "project_catalog_create_diagnostic"


class _ProjectCatalogDiagnosticFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            field: getattr(record, field, None)
            for field in _PROJECT_CATALOG_DIAGNOSTIC_FIELDS
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _configure_project_catalog_diagnostics(log_directory: Path) -> None:
    """Persist only fixed, redacted Project Catalog create-stage fields."""

    log_directory.mkdir(parents=True, exist_ok=True)
    target = logging.getLogger(_PROJECT_CATALOG_DIAGNOSTIC_LOGGER)
    if any(
        bool(getattr(handler, "_project_catalog_diagnostic", False))
        for handler in target.handlers
    ):
        return
    handler = logging.FileHandler(
        log_directory / "project-catalog-create-diagnostics.log", encoding="utf-8"
    )
    handler._project_catalog_diagnostic = True  # type: ignore[attr-defined]
    handler.addFilter(_ProjectCatalogDiagnosticFilter())
    handler.setFormatter(_ProjectCatalogDiagnosticFormatter())
    target.addHandler(handler)
    target.setLevel(logging.INFO)


class _ProviderResponseTelemetryFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "event_name", None) == "provider_response_boundary_telemetry"


class _ProviderRequestTelemetryFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "event_name", None) == "provider_request_boundary_telemetry"


class _ProviderRequestTelemetryFormatter(logging.Formatter):
    _FIELDS = (
        "event_name",
        "telemetry_status",
        "request_model",
        "request_message_count",
        "system_message_present",
        "system_prompt_length",
        "user_message_present",
        "user_prompt_length",
        "context_message_present",
        "context_payload_length",
        "project_context_attached",
        "requirement_context_attached",
        "max_tokens",
        "temperature",
        "top_p",
        "response_format_present",
        "http_payload_valid",
        "messages_array_length",
        "first_message_role",
        "last_message_role",
        "task_mode",
        "project_mode",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            field: getattr(record, field)
            for field in self._FIELDS
            if hasattr(record, field)
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _provider_request_telemetry_unavailable() -> None:
    logger.warning(
        "provider_request_telemetry_unavailable",
        extra={
            "event_name": "provider_request_telemetry_unavailable",
            "telemetry_enabled": False,
            "reason_code": "TELEMETRY_LOG_FILE_CREATE_FAILED",
        },
    )


def _configure_provider_request_telemetry(log_directory: Path) -> bool:
    """Persist only fixed, redacted Provider request-boundary fields."""

    targets = [logging.getLogger(name) for name in _PROVIDER_REQUEST_TELEMETRY_LOGGERS]
    try:
        log_directory.mkdir(parents=True, exist_ok=True)
        if all(
            any(bool(getattr(handler, "_provider_request_telemetry", False)) for handler in target.handlers)
            for target in targets
        ):
            return True
        handler = logging.FileHandler(
            log_directory / "provider-request-boundary-telemetry.log", encoding="utf-8"
        )
        handler._provider_request_telemetry = True  # type: ignore[attr-defined]
        handler.addFilter(_ProviderRequestTelemetryFilter())
        handler.setFormatter(_ProviderRequestTelemetryFormatter())
        for target in targets:
            target.addHandler(handler)
            target.setLevel(logging.INFO)
        return True
    except (OSError, ValueError):
        _provider_request_telemetry_unavailable()
        return False


class _ProviderResponseTelemetryFormatter(logging.Formatter):
    _FIELDS = (
        "event_name",
        "response_stage",
        "telemetry_status",
        "provider_response_received",
        "adapter_parse_status",
        "parsed_field_names",
        "model_response_created",
        "choices_present",
        "message_present",
        "content_present",
        "content_type",
        "content_length",
        "answer_text_present",
        "answer_text_length",
        "validation_status",
        "validation_error_code",
        "reason_code",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            field: getattr(record, field)
            for field in self._FIELDS
            if hasattr(record, field)
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _provider_response_telemetry_unavailable() -> None:
    logger.warning(
        "provider_response_telemetry_unavailable",
        extra={
            "event_name": "provider_response_telemetry_unavailable",
            "telemetry_enabled": False,
            "reason_code": "TELEMETRY_LOG_FILE_CREATE_FAILED",
        },
    )


def _configure_provider_response_telemetry(log_directory: Path) -> bool:
    """Best-effort persistence for fixed, redacted response-boundary fields."""

    targets = [logging.getLogger(name) for name in _PROVIDER_RESPONSE_TELEMETRY_LOGGERS]
    try:
        log_directory.mkdir(parents=True, exist_ok=True)
        if all(
            any(bool(getattr(handler, "_provider_response_telemetry", False)) for handler in target.handlers)
            for target in targets
        ):
            return True
        handler = logging.FileHandler(
            log_directory / "provider-response-boundary-telemetry.log", encoding="utf-8"
        )
        handler._provider_response_telemetry = True  # type: ignore[attr-defined]
        handler.addFilter(_ProviderResponseTelemetryFilter())
        handler.setFormatter(_ProviderResponseTelemetryFormatter())
        for target in targets:
            target.addHandler(handler)
            target.setLevel(logging.INFO)
        return True
    except (OSError, ValueError):
        _provider_response_telemetry_unavailable()
        return False


class _ApplicationEvidenceTelemetryFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "event_name", None) == "application_evidence_boundary_telemetry"


class _ApplicationEvidenceTelemetryFormatter(logging.Formatter):
    _FIELDS = (
        "event_name",
        "evidence_stage",
        "telemetry_status",
        "requirement_context_present",
        "project_context_present",
        "project_id_present",
        "source_reference_count",
        "evidence_count",
        "answer_text_length",
        "validation_error_code",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            field: getattr(record, field)
            for field in self._FIELDS
            if hasattr(record, field)
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _configure_application_evidence_telemetry(log_directory: Path) -> bool:
    """Persist only bounded ApplicationAnswerEvidence boundary fields."""

    target = logging.getLogger(_APPLICATION_EVIDENCE_TELEMETRY_LOGGER)
    try:
        log_directory.mkdir(parents=True, exist_ok=True)
        if any(
            bool(getattr(handler, "_application_evidence_telemetry", False))
            for handler in target.handlers
        ):
            return True
        handler = logging.FileHandler(
            log_directory / "application-evidence-boundary-telemetry.log",
            encoding="utf-8",
        )
        handler._application_evidence_telemetry = True  # type: ignore[attr-defined]
        handler.addFilter(_ApplicationEvidenceTelemetryFilter())
        handler.setFormatter(_ApplicationEvidenceTelemetryFormatter())
        target.addHandler(handler)
        target.setLevel(logging.INFO)
        return True
    except (OSError, ValueError):
        logger.warning(
            "application_evidence_telemetry_unavailable",
            extra={
                "event_name": "application_evidence_telemetry_unavailable",
                "telemetry_enabled": False,
                "reason_code": "TELEMETRY_LOG_FILE_CREATE_FAILED",
            },
        )
        return False


class _SnapshotTelemetryFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "event_name", None) == "snapshot_prepare_telemetry"


class _SnapshotTelemetryFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return (
            f"{record.asctime if hasattr(record, 'asctime') else self.formatTime(record)} "
            f"{record.levelname} snapshot_prepare_telemetry "
            f"status={getattr(record, 'telemetry_status', '-')} "
            f"stage={getattr(record, 'snapshot_stage', '-')} "
            f"exception_type={getattr(record, 'exception_type', '-')} "
            f"safe_reason_code={getattr(record, 'safe_reason_code', '-')} "
            f"sqlite_error_code={getattr(record, 'sqlite_error_code', '-')} "
            f"win_error={getattr(record, 'win_error', '-')}"
        )


def _configure_snapshot_telemetry(log_directory: Path) -> None:
    """Persist only the fixed, safe snapshot-preflight telemetry fields."""

    log_directory.mkdir(parents=True, exist_ok=True)
    target = logging.getLogger(_SNAPSHOT_TELEMETRY_LOGGER)
    if any(
        bool(getattr(handler, "_snapshot_telemetry", False))
        for handler in target.handlers
    ):
        return
    handler = logging.FileHandler(
        log_directory / "migration-preflight-telemetry.log", encoding="utf-8"
    )
    handler._snapshot_telemetry = True  # type: ignore[attr-defined]
    handler.addFilter(_SnapshotTelemetryFilter())
    handler.setFormatter(_SnapshotTelemetryFormatter())
    target.addHandler(handler)
    if target.level == logging.NOTSET or target.level > logging.INFO:
        target.setLevel(logging.INFO)


class _MigrationTelemetryFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "event_name", None) == "migration_post_snapshot_telemetry"


class _MigrationTelemetryFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return (
            f"{self.formatTime(record)} {record.levelname} migration_post_snapshot_telemetry "
            f"status={getattr(record, 'telemetry_status', '-')} "
            f"stage={getattr(record, 'migration_stage', '-')} "
            f"exception_type={getattr(record, 'exception_type', '-')} "
            f"reason_code={getattr(record, 'reason_code', '-')} "
            f"sqlite_error_code={getattr(record, 'sqlite_error_code', '-')} "
            f"safe_error_category={getattr(record, 'safe_error_category', '-')}"
        )


def _configure_migration_telemetry(log_directory: Path) -> None:
    """Persist only safe post-snapshot migration boundary telemetry."""

    log_directory.mkdir(parents=True, exist_ok=True)
    target = logging.getLogger(_MIGRATION_TELEMETRY_LOGGER)
    if any(
        bool(getattr(handler, "_migration_telemetry", False))
        for handler in target.handlers
    ):
        return
    handler = logging.FileHandler(
        log_directory / "migration-post-snapshot-telemetry.log", encoding="utf-8"
    )
    handler._migration_telemetry = True  # type: ignore[attr-defined]
    handler.addFilter(_MigrationTelemetryFilter())
    handler.setFormatter(_MigrationTelemetryFormatter())
    target.addHandler(handler)
    if target.level == logging.NOTSET or target.level > logging.INFO:
        target.setLevel(logging.INFO)
# Frozen imports (FastAPI, Chroma and the application registry) can be much
# slower than source execution. Keep startup bounded, but long enough for a
# cold packaged launch on a normal developer workstation.
STARTUP_TIMEOUT_SECONDS = 60.0
SHUTDOWN_TIMEOUT_SECONDS = 10.0
_HEALTH_STATUSES = frozenset({"ok", "degraded", "unavailable"})


def _resolve_assets(*, bundled_root: Path | None, source_root: Path) -> Path:
    candidate = (bundled_root / "frontend-dist") if bundled_root is not None else (source_root / "frontend" / "dist")
    if not candidate.is_dir():
        raise RuntimeError("STATIC_ASSET_UNAVAILABLE")
    return candidate


def _assets() -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return _resolve_assets(bundled_root=Path(meipass), source_root=Path(__file__).resolve().parents[1])
    return _resolve_assets(bundled_root=None, source_root=Path(__file__).resolve().parents[1])


def _start_server_worker(server: uvicorn.Server, *, listener: socket.socket) -> threading.Thread:
    worker = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        name="embedded-copilot-loopback-server",
        daemon=False,
    )
    worker.start()
    return worker


def _wait_for_server_startup(server: uvicorn.Server, worker: threading.Thread, *, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while worker.is_alive() and not server.started:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        worker.join(timeout=min(0.05, remaining))
    return bool(server.started) and worker.is_alive()


def _listener_ready(port: int, *, timeout_seconds: float = 2.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout_seconds):
            return True
    except OSError:
        return False


def _health_endpoint_ready(port: int, *, timeout_seconds: float = 2.0) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/v1/health", timeout=timeout_seconds) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
            return isinstance(payload, dict) and payload.get("status") in _HEALTH_STATUSES
    except (OSError, TypeError, ValueError):
        return False


def _request_graceful_shutdown(server: uvicorn.Server, worker: threading.Thread, *, timeout_seconds: float = SHUTDOWN_TIMEOUT_SECONDS) -> bool:
    server.should_exit = True
    worker.join(timeout=timeout_seconds)
    return not worker.is_alive()


def _safe_error_dialog(reason_code: str) -> None:
    """Display only a fixed product message; never expose raw exception data."""

    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            "Embedded Copilot could not start.",
            f"Embedded Copilot [{reason_code}]",
            0x10,
        )
    except Exception:  # noqa: BLE001
        logger.warning("native_error_dialog_unavailable", extra={"event_name": "native_error_dialog_unavailable"})


def _safe_log(
    event_name: str,
    error: BaseException | None = None,
    *,
    stage: str = "-",
) -> None:
    extra = {
        "event_name": event_name,
        "trace_id": uuid4().hex,
        "error_type": "-",
        "stage": stage,
    }
    if error is not None:
        extra["error_type"] = type(error).__name__
    logger.warning(event_name, extra=extra)


def _configure_safe_file_logging(log_directory: Path) -> None:
    """Persist only structured, redacted launcher events for packaged runs."""

    _early_startup_event("E1_TELEMETRY_INIT", "ENTER")
    try:
        log_directory.mkdir(parents=True, exist_ok=True)
        _configure_snapshot_telemetry(log_directory)
        _configure_migration_telemetry(log_directory)
        _configure_native_handoff_telemetry(log_directory)
        path = log_directory / "launcher.log"
        if any(isinstance(handler, logging.FileHandler) for handler in logger.handlers):
            _configure_answer_contract_diagnostics(log_directory)
        else:
            handler = logging.FileHandler(path, encoding="utf-8")
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)s %(message)s "
                    "trace_id=%(trace_id)s error_type=%(error_type)s exception_type=%(exception_type)s stage=%(stage)s "
                    "reason_code=%(reason_code)s telemetry_enabled=%(telemetry_enabled)s hresult=%(hresult)s thread_id=%(thread_id)s thread_name=%(thread_name)s "
                    "apartment_state=%(apartment_state)s apartment_type=%(apartment_type)s picker_backend=%(picker_backend)s "
                    "owner_hwnd=%(owner_hwnd)s owner_present=%(owner_present)s owner_pid_match=%(owner_pid_match)s duration_ms=%(duration_ms)s "
                    "requested_apartment=%(requested_apartment)s com_init_status=%(com_init_status)s raw_hresult=%(raw_hresult)s "
                    "normalized_hresult=%(normalized_hresult)s observed_apartment=%(observed_apartment)s apartment_probe_status=%(apartment_probe_status)s",
                    defaults={
                        "trace_id": "-",
                        "error_type": "-",
                        "exception_type": "-",
                        "stage": "-",
                        "reason_code": "-",
                        "telemetry_enabled": "-",
                        "hresult": "-",
                        "thread_id": "-",
                        "thread_name": "-",
                        "apartment_state": "-",
                        "apartment_type": "-",
                        "picker_backend": "-",
                        "owner_hwnd": "-",
                        "owner_present": "-",
                        "owner_pid_match": "-",
                        "duration_ms": "-",
                        "requested_apartment": "-",
                        "com_init_status": "-",
                        "raw_hresult": "-",
                        "normalized_hresult": "-",
                        "observed_apartment": "-",
                        "apartment_probe_status": "-",
                    },
                )
            )
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            _configure_answer_contract_diagnostics(log_directory)
    except (OSError, ValueError):
        _safe_log("launcher_file_logging_unavailable")
    try:
        _configure_project_catalog_diagnostics(log_directory)
    except (OSError, ValueError):
        _safe_log("project_catalog_diagnostic_logging_unavailable")
    try:
        _configure_provider_request_telemetry(log_directory)
    except (OSError, ValueError):
        _provider_request_telemetry_unavailable()
    try:
        _configure_provider_response_telemetry(log_directory)
    except (OSError, ValueError):
        _provider_response_telemetry_unavailable()
    try:
        _configure_application_evidence_telemetry(log_directory)
    except (OSError, ValueError):
        logger.warning(
            "application_evidence_telemetry_unavailable",
            extra={
                "event_name": "application_evidence_telemetry_unavailable",
                "telemetry_enabled": False,
                "reason_code": "TELEMETRY_LOG_FILE_CREATE_FAILED",
            },
        )
    _early_startup_event("E1_TELEMETRY_INIT", "PASS")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Embedded Copilot production desktop launcher")
    parser.add_argument("--browser", action="store_true", help="explicit developer/diagnostic browser mode")
    parser.add_argument("--browser-url-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--debug", action="store_true", help="enable developer diagnostics")
    parser.add_argument("--picker-runtime-probe", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def _diagnostic_bootstrap_query(*, browser: bool) -> str:
    """Return the diagnostic query only for the native WebView handoff."""

    if browser or os.environ.get(WEBVIEW_SESSION_DIAGNOSTIC_ENVIRONMENT_VARIABLE) != "1":
        return ""
    return "?webview_session_diagnostic=1"


def run(
    argv: Sequence[str] | None = None,
    *,
    host: NativeDesktopHostPort | None = None,
    guard: SingleInstanceGuard | None = None,
    asset_resolver: Callable[[], Path] = _assets,
) -> int:
    _early_startup_event("E1_LAUNCHER_INIT", "ENTER")
    _early_startup_event("E1_ENTER", "ENTER")
    try:
        arguments = _parse_args(argv)
    except Exception as error:  # noqa: BLE001
        _early_startup_failure("E1_ENTER", error, "LAUNCHER_ARGUMENT_PARSE_FAILED")
        raise
    _early_startup_event("E1_ENTER", "PASS")
    if arguments.picker_runtime_probe:
        _early_startup_event("E1_LAUNCHER_INIT", "PASS", exit_code=0)
        return 0 if probe_picker_runtime() else 1
    _early_startup_event("E1_SINGLE_INSTANCE", "ENTER")
    try:
        instance = guard or SingleInstanceGuard(
            state_identity=ProductionStateLayout.for_current_user().root
        )
        if not instance.acquire():
            _early_startup_event(
                "E1_SINGLE_INSTANCE",
                "FAIL",
                reason_code="SINGLE_INSTANCE_HANDOFF",
                exit_code=0,
            )
            instance.signal_existing()
            return 0
        instance.serve_activation()
    except Exception as error:  # noqa: BLE001
        _early_startup_failure("E1_SINGLE_INSTANCE", error, "SINGLE_INSTANCE_INIT_FAILED")
        raise
    _early_startup_event("E1_SINGLE_INSTANCE", "PASS")
    _early_startup_event("E1_LAUNCHER_INIT", "PASS")

    listener: socket.socket | None = None
    worker: threading.Thread | None = None
    server: uvicorn.Server | None = None
    stage = "instance"
    early_stage = "E2_BEFORE_LOGGING_INIT"
    try:
        try:
            _early_startup_event("E2_BEFORE_LOGGING_INIT", "ENTER")
            early_stage = "E3_BEFORE_PRODUCTION_PREFLIGHT"
            _early_startup_event(early_stage, "ENTER")
            stage = "preflight"
            assets = asset_resolver()
            layout = ProductionStateLayout.for_current_user()
            settings = load_production_settings(assets=assets, layout=layout)
            release = ProductionReleaseRuntime.create(settings)
            early_stage = "E2_BEFORE_LOGGING_INIT"
            _early_startup_event("E1_LOGGING_INIT", "ENTER")
            try:
                _configure_safe_file_logging(release.layout.logs)
            except Exception as error:  # noqa: BLE001
                _early_startup_failure("E1_TELEMETRY_INIT", error, "TELEMETRY_INIT_FAILED")
                _early_startup_failure("E1_LOGGING_INIT", error, "LOGGING_INIT_FAILED")
                raise
            _early_startup_event("E1_LOGGING_INIT", "PASS")
            _early_startup_event("E2_BEFORE_LOGGING_INIT", "PASS")
            early_stage = "E3_BEFORE_PRODUCTION_PREFLIGHT"
            record_native_handoff("H0_NATIVE_PROCESS_START", "PASS")
            release.start()
            _early_startup_event("E3_BEFORE_PRODUCTION_PREFLIGHT", "PASS")
        except ProductionConfigurationRejected as error:
            _early_startup_failure(early_stage, error, "PRODUCTION_CONFIGURATION_REJECTED")
            _safe_log("production_preflight_failed", error, stage=stage)
            _safe_error_dialog("PROJECT_CATALOG_UNAVAILABLE")
            return 1
        except RuntimeError as error:
            reason = (
                str(error)
                if str(error) in {"STATIC_ASSET_UNAVAILABLE", "MIGRATION_FAILED"}
                else "MIGRATION_FAILED"
            )
            _early_startup_failure(early_stage, error, reason)
            _safe_log("production_preflight_failed", error, stage=stage)
            _safe_error_dialog(reason)
            return 1
        except Exception as error:  # noqa: BLE001
            _early_startup_failure(early_stage, error, "PRODUCTION_PREFLIGHT_FAILED")
            _safe_log("production_preflight_failed", error, stage=stage)
            _safe_error_dialog("MIGRATION_FAILED")
            return 1

        early_stage = "E4_AFTER_PRODUCTION_PREFLIGHT"
        _early_startup_event(early_stage, "ENTER")
        stage = "create_app"
        _early_startup_event("E1_BACKEND_PREPARE", "ENTER")
        try:
            application = create_app(settings=release.settings, production_release_runtime=release)
            native_host = host or PyWebView2DesktopHost()
            application.state.native_desktop_host = native_host
        except Exception as error:  # noqa: BLE001
            _early_startup_failure("E1_BACKEND_PREPARE", error, "BACKEND_PREPARE_FAILED")
            raise
        _early_startup_event("E1_BACKEND_PREPARE", "PASS")
        _early_startup_event(early_stage, "PASS")
        stage = "socket_create"
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        stage = "socket_options"
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        stage = "socket_bind"
        listener.bind(("127.0.0.1", 0))
        stage = "socket_listen"
        listener.listen(128)
        stage = "port_resolve"
        port = int(listener.getsockname()[1])
        stage = "uvicorn_config"
        server = uvicorn.Server(
            uvicorn.Config(
                application,
                host="127.0.0.1",
                port=port,
                log_level="error",
                access_log=False,
                lifespan="on",
                use_colors=False,
            )
        )
        stage = "backend_start"
        worker = _start_server_worker(server, listener=listener)
        if (
            not _wait_for_server_startup(server, worker, timeout_seconds=STARTUP_TIMEOUT_SECONDS)
            or not _listener_ready(port)
            or not _health_endpoint_ready(port)
        ):
            _safe_error_dialog("BACKEND_START_FAILED")
            return 1
        record_native_handoff("H1_BACKEND_READY_DETECTED", "PASS", http_status=200)
        if not instance.mark_ready(port):
            _safe_error_dialog("BACKEND_START_FAILED")
            return 1
        transaction_id = os.environ.get("EMBRIX_UPDATE_TRANSACTION_ID")
        if transaction_id:
            identity = read_build_identity()
            expected_build = os.environ.get("EMBRIX_EXPECTED_BUILD_ID")
            install_root = Path(sys.executable).resolve().parents[2]
            frontend_ready = getattr(native_host, "frontend_ready", None)
            if (identity is None or not expected_build or identity.get("build_id") != expected_build or
                    not isinstance(frontend_ready, threading.Event)):
                _safe_error_dialog("UPDATE_BOOT_IDENTITY_FAILED")
                return 1

            def confirm_native_boot() -> None:
                if frontend_ready.wait(STARTUP_TIMEOUT_SECONDS):
                    mark_boot_ready(install_root, transaction_id=transaction_id,
                                    build_id=expected_build, assets=assets,
                                    migration_statuses=release.migrations.statuses())

            threading.Thread(target=confirm_native_boot, name="embrix-update-boot-confirm", daemon=True).start()

        stage = "bootstrap"
        record_native_handoff("H2_BOOTSTRAP_URL_CREATE", "ENTER")
        nonce = release.sessions.issue_bootstrap(requester="desktop-user", tenant_id="desktop-tenant")
        diagnostic_query = _diagnostic_bootstrap_query(browser=arguments.browser)
        url = f"http://127.0.0.1:{port}/desktop/{diagnostic_query}#{nonce}"
        record_native_handoff("H2_BOOTSTRAP_URL_CREATE", "PASS")
        record_native_handoff("H3_BOOTSTRAP_FILE_WRITE", "ENTER")
        if arguments.browser:
            # Diagnostic-only handoff for the in-app browser. This is a
            # short-lived local bootstrap URL, never a log/audit field.
            Path(tempfile.gettempdir(), "embedded_copilot_v70_bootstrap_url.txt").write_text(url, encoding="utf-8")
            if arguments.browser_url_file is not None:
                arguments.browser_url_file.write_text(url, encoding="utf-8")
            # When an explicit handoff file is requested, the caller owns
            # browser navigation.  Avoid opening a second browser that could
            # consume the one-time bootstrap nonce first.
            if arguments.browser_url_file is None:
                webbrowser.open(url, new=1)
            record_native_handoff("H3_BOOTSTRAP_FILE_WRITE", "PASS", reason_code="DIAGNOSTIC_FILE_HANDOFF")
            worker.join()
            return 0

        record_native_handoff("H3_BOOTSTRAP_FILE_WRITE", "PASS", reason_code="NOT_REQUIRED_NATIVE_MODE")

        stage = "native_host"
        result: NativeShellResult = native_host.show(
            url,
            internal_origin=f"http://127.0.0.1:{port}",
            config=NativeShellConfig(debug=arguments.debug),
        )
        if result.error is not None and result.status == "FAILED":
            _safe_error_dialog(result.error.value)
            return 1
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as error:  # noqa: BLE001
        _early_startup_failure(early_stage, error, "LAUNCHER_STARTUP_FAILED")
        _safe_log("launcher_failed", error, stage=stage)
        _safe_error_dialog("BACKEND_START_FAILED")
        return 1
    finally:
        if server is not None and worker is not None and worker.is_alive():
            _request_graceful_shutdown(server, worker)
        if listener is not None:
            listener.close()
        instance.close()


if __name__ == "__main__":
    raise SystemExit(run())
