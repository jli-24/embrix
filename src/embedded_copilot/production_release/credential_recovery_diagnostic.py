"""Opt-in, content-free diagnostics for target-process credential recovery."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path


DIAGNOSTIC_ENVIRONMENT_VARIABLE = "EMBRIX_CREDENTIAL_RECOVERY_DIAGNOSTIC"
DIAGNOSTIC_BUILD_ID = "P0-1G15E"
PROVIDER = "deepseek"
_OUTPUT_DIRECTORY = "embrix-p0-1g"
_OUTPUT_FILENAME = "credential-recovery-diagnostic.jsonl"

ALLOWED_EVENT_FIELDS = frozenset(
    {
        "timestamp",
        "pid",
        "diagnostic_build_id",
        "stage",
        "result",
        "provider",
        "target_kind",
        "safe_error_class",
        "safe_win32_error_code",
    }
)

ALLOWED_TARGET_KINDS = frozenset({"PRIMARY", "INTEGRITY"})
_LOOKUP_STAGES = frozenset(
    {
        "PRIMARY_CREDENTIAL_LOOKUP_BEGIN",
        "PRIMARY_CREDENTIAL_LOOKUP_PRESENT",
        "PRIMARY_CREDENTIAL_LOOKUP_NOT_FOUND",
        "PRIMARY_CREDENTIAL_LOOKUP_ACCESS_DENIED",
        "PRIMARY_CREDENTIAL_LOOKUP_ERROR",
        "INTEGRITY_TARGET_LOOKUP_BEGIN",
        "INTEGRITY_TARGET_LOOKUP_PRESENT",
        "INTEGRITY_TARGET_LOOKUP_NOT_FOUND",
        "INTEGRITY_TARGET_LOOKUP_ACCESS_DENIED",
        "INTEGRITY_TARGET_LOOKUP_ERROR",
    }
)
ALLOWED_STAGES = frozenset(
    {
        *_LOOKUP_STAGES,
        "CREDENTIAL_RECOVERY_BEGIN",
        "CREDENTIAL_RECOVERY_PRIMARY_PRESENT",
        "CREDENTIAL_RECOVERY_PRIMARY_ABSENT",
        "CREDENTIAL_RECOVERY_PRIMARY_ERROR",
        "CREDENTIAL_RECOVERY_INTEGRITY_PRESENT",
        "CREDENTIAL_RECOVERY_INTEGRITY_ABSENT",
        "CREDENTIAL_RECOVERY_INTEGRITY_ERROR",
        "CREDENTIAL_RECOVERY_BINDING_MATCH",
        "CREDENTIAL_RECOVERY_BINDING_MISMATCH",
        "CREDENTIAL_RECOVERY_PROJECTION_CONFIGURED",
        "CREDENTIAL_RECOVERY_PROJECTION_UNCONFIGURED",
        "CREDENTIAL_RECOVERY_PROJECTION_UNAVAILABLE",
        "CREDENTIAL_RECOVERY_BINDING_INVALIDATED",
        "CREDENTIAL_RECOVERY_METADATA_SANITIZE_BEGIN",
        "CREDENTIAL_RECOVERY_METADATA_SANITIZE_SUCCESS",
        "CREDENTIAL_RECOVERY_METADATA_SANITIZE_FAILURE",
    }
)
ALLOWED_RESULTS = frozenset(
    {
        "BEGIN",
        "PRESENT",
        "NOT_FOUND",
        "ACCESS_DENIED",
        "ERROR",
        "ABSENT",
        "MATCH",
        "MISMATCH",
        "CONFIGURED",
        "UNCONFIGURED",
        "UNAVAILABLE",
        "INVALIDATED",
        "SUCCESS",
        "FAILURE",
    }
)


def enabled() -> bool:
    """Return whether the probe was explicitly enabled for this process."""

    return os.environ.get(DIAGNOSTIC_ENVIRONMENT_VARIABLE) == "1"


def diagnostic_output_path() -> Path:
    """Return the independent recovery diagnostic sink."""

    return Path(tempfile.gettempdir()) / _OUTPUT_DIRECTORY / _OUTPUT_FILENAME


def safe_error_class(error: BaseException) -> str:
    """Map an exception to a fixed, content-free category."""

    if error.__class__.__name__ == "CredentialUnavailable":
        return "CREDENTIAL_STORE_UNAVAILABLE"
    if isinstance(error, PermissionError):
        return "PERMISSION_ERROR"
    return "DIAGNOSTIC_ERROR"


def safe_win32_error_code(error: BaseException) -> int | None:
    value = getattr(error, "win32_error_code", None)
    return value if type(value) is int and value >= 0 else None


def emit(
    stage: str,
    *,
    result: str,
    target_kind: str | None = None,
    error: BaseException | None = None,
    safe_code: int | None = None,
) -> None:
    """Best-effort append of one strict, content-free diagnostic event."""

    if not enabled() or stage not in ALLOWED_STAGES or result not in ALLOWED_RESULTS:
        return
    if stage in _LOOKUP_STAGES:
        if target_kind not in ALLOWED_TARGET_KINDS:
            return
    elif target_kind is not None:
        return
    if safe_code is not None and (type(safe_code) is not int or safe_code < 0):
        return

    payload: dict[str, object] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "pid": os.getpid(),
        "diagnostic_build_id": DIAGNOSTIC_BUILD_ID,
        "stage": stage,
        "result": result,
        "provider": PROVIDER,
    }
    if target_kind is not None:
        payload["target_kind"] = target_kind
    if error is not None:
        payload["safe_error_class"] = safe_error_class(error)
    error_code = safe_code
    if error_code is None and error is not None:
        error_code = globals()["safe_win32_error_code"](error)
    if error_code is not None:
        payload["safe_win32_error_code"] = error_code
    if set(payload) - ALLOWED_EVENT_FIELDS:
        return

    try:
        output = diagnostic_output_path()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n")
            stream.flush()
    except (OSError, TypeError, ValueError):
        # A probe sink can never alter startup, lookup, projection, or errors.
        return


__all__ = (
    "ALLOWED_EVENT_FIELDS",
    "ALLOWED_RESULTS",
    "ALLOWED_STAGES",
    "ALLOWED_TARGET_KINDS",
    "DIAGNOSTIC_BUILD_ID",
    "DIAGNOSTIC_ENVIRONMENT_VARIABLE",
    "PROVIDER",
    "diagnostic_output_path",
    "emit",
    "enabled",
    "safe_error_class",
    "safe_win32_error_code",
)
