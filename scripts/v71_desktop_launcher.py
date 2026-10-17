"""v7.1 launcher entrypoint with a stdlib-only earliest crash boundary."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

_EARLY_STARTUP_LOG_NAME = "early-startup-diagnostic.log"
_RUNTIME_BOUNDARY_LOG_NAME = "runtime-boundary-telemetry.log"
_RUNTIME_BOUNDARY_LOGGER = "embedded_copilot.runtime_diagnostics"
_RUNTIME_BOUNDARY_EVENTS = frozenset(
    {
        "application_runtime_boundary_telemetry",
        "application_runtime_outer_boundary_telemetry",
        "binding_engineering_qa_boundary_telemetry",
        "engineering_qa_exception_boundary_telemetry",
        "model_request_validation_boundary_telemetry",
        "prompt_request_boundary_telemetry",
    }
)
_RUNTIME_BOUNDARY_FIELDS = (
    "event_name",
    "stage",
    "status",
    "application_runtime_stage",
    "application_runtime_outer_stage",
    "binding_engineering_qa_stage",
    "engineering_qa_stage",
    "prompt_pipeline_stage",
    "telemetry_status",
    "original_exception_type",
    "original_exception_hash",
    "exception_layer",
    "exception_stage",
    "traceback_fingerprint",
    "exception_type_hash",
    "reason_code",
    "engineering_qa_service_available",
    "engineering_qa_service_instance_available",
    "prompt_module_loaded",
    "greenfield_prompt_available",
    "prompt_service_dependency_available",
    "prompt_builder_dependency_available",
    "request_builder_dependency_available",
    "model_request_schema_valid",
    "context_projection_valid",
    "constraints_valid",
    "binding_service_instance_exists",
    "service_constructor_completed",
    "service_method_exists",
    "method_signature_compatible",
    "dependency_container_resolved",
    "validation_error_count",
    "validation_errors",
)

_VALIDATION_RECEIVED_TYPES = frozenset(
    {
        "NONE",
        "BOOL",
        "INT",
        "FLOAT",
        "STR",
        "BYTES",
        "TUPLE",
        "LIST",
        "DICT",
        "SET",
        "FROZENSET",
        "MODEL",
        "OTHER",
    }
)


def _fixed_code(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    checked = value.strip().upper()
    if (
        not checked
        or len(checked) > 96
        or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in checked)
    ):
        return None
    return checked


def _redacted_validation_errors(value: object) -> list[dict[str, str]]:
    if not isinstance(value, (tuple, list)):
        return []
    sanitized: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        location_hash = item.get("location_hash")
        error_type = _fixed_code(item.get("error_type"))
        received_type = _fixed_code(item.get("received_type"))
        message_code = _fixed_code(item.get("message_code"))
        if (
            not isinstance(location_hash, str)
            or len(location_hash) != 64
            or any(character not in "0123456789abcdef" for character in location_hash)
            or error_type is None
            or received_type not in _VALIDATION_RECEIVED_TYPES
            or message_code is None
        ):
            continue
        sanitized.append(
            {
                "location_hash": location_hash,
                "error_type": error_type,
                "received_type": received_type,
                "message_code": message_code,
            }
        )
    return sanitized


class _RuntimeBoundaryTelemetryFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, "event_name", None) in _RUNTIME_BOUNDARY_EVENTS


class _RuntimeBoundaryTelemetryFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            field: getattr(record, field)
            for field in _RUNTIME_BOUNDARY_FIELDS
            if hasattr(record, field)
        }
        if getattr(record, "event_name", None) == "model_request_validation_boundary_telemetry":
            errors = _redacted_validation_errors(payload.get("validation_errors"))
            payload = {
                "event_name": "model_request_validation_boundary_telemetry",
                "validation_error_count": len(errors),
                "validation_errors": errors,
            }
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def _configure_runtime_boundary_telemetry(
    log_directory: Path | None = None,
) -> bool:
    """Persist only the existing fixed-shape, content-free v7.1 diagnostics."""

    directory = log_directory
    if directory is None:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            return False
        directory = Path(local_app_data) / "Embedded Copilot" / "logs"
    target = logging.getLogger(_RUNTIME_BOUNDARY_LOGGER)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if any(
            bool(getattr(handler, "_runtime_boundary_telemetry", False))
            for handler in target.handlers
        ):
            return True
        handler = logging.FileHandler(
            directory / _RUNTIME_BOUNDARY_LOG_NAME,
            encoding="utf-8",
        )
        handler._runtime_boundary_telemetry = True  # type: ignore[attr-defined]
        handler.addFilter(_RuntimeBoundaryTelemetryFilter())
        handler.setFormatter(_RuntimeBoundaryTelemetryFormatter())
        target.addHandler(handler)
        target.setLevel(logging.INFO)
        return True
    except (OSError, ValueError):
        return False


def _early_entry_event(
    stage: str,
    status: str,
    error: BaseException | None = None,
    *,
    reason_code: str = "-",
    exit_code: int | None = None,
) -> None:
    """Persist only fixed, redacted fields and never block launcher startup."""

    exception_type = type(error).__name__ if error is not None else "-"
    exception_hash = "-"
    if error is not None:
        type_name = f"{type(error).__module__}.{type(error).__qualname__}"
        exception_hash = hashlib.sha256(f"{stage}|{type_name}".encode("utf-8")).hexdigest()[:16]
    exception_module = type(error).__module__ if error is not None else "-"
    exception_function = "-"
    exception_line: int | str = "-"
    frames: list[str] = []
    traceback = error.__traceback__ if error is not None else None
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
    event = {
        "stage": stage,
        "status": status,
        "exception_type": exception_type,
        "exception_hash": exception_hash,
        "exception_module": exception_module,
        "exception_function": exception_function,
        "exception_line": exception_line,
        "traceback_hash": traceback_hash,
        "reason_code": reason_code,
        "exit_code": exit_code,
    }
    payload = (json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        candidates: list[Path] = []
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "Embedded Copilot" / "logs")
        candidates.append(Path(tempfile.gettempdir()) / "Embedded Copilot" / "logs")
        for directory in candidates:
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


def main() -> int:
    _early_entry_event("E0_PROCESS_ENTRY", "ENTER")
    _early_entry_event("E1_RUNTIME_IMPORT", "ENTER")
    try:
        from v70_desktop_launcher import run
    except Exception as error:  # noqa: BLE001
        _early_entry_event(
            "E1_RUNTIME_IMPORT",
            "FAIL",
            error,
            reason_code="LAUNCHER_IMPORT_FAILED",
            exit_code=1,
        )
        _early_entry_event(
            "E0_PROCESS_ENTRY",
            "FAIL",
            error,
            reason_code="LAUNCHER_IMPORT_FAILED",
            exit_code=1,
        )
        return 1

    _early_entry_event("E1_RUNTIME_IMPORT", "PASS")
    _early_entry_event("E0_PROCESS_ENTRY", "PASS")
    _configure_runtime_boundary_telemetry()
    try:
        return int(run())
    except Exception as error:  # noqa: BLE001
        _early_entry_event(
            "E1_LAUNCHER_INIT",
            "FAIL",
            error,
            reason_code="LAUNCHER_UNHANDLED_EXCEPTION",
            exit_code=1,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
