"""Run the v7.1 Final Golden in two explicit, fail-closed operator phases.

The primary phase validates one approved provider transmission against the
packaged v7.1 executable.  The restart phase is intentionally separate and
performs no chat submission or provider invocation.  Both phases only expose
redacted evidence; the resume file is local operator state and must not be
archived.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# ``python scripts/v71_final_golden.py`` sets ``sys.path[0]`` to the scripts
# directory, whereas the canonical runner is imported through the repository
# package namespace.  Keep the operator command usable without requiring a
# package installation.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import v71_canonical_original_exception_golden as canonical

ROOT = Path(__file__).resolve().parents[1]
PRODUCT_VERSION = "7.1.0"
EXPECTED_PRINCIPAL = "sqchen\\34974"
EXPECTED_SESSION_ID = 1
PROVIDER_BUDGET = 1
CONNECTION_TEST_PROVIDER_BUDGET = 1
RECOVERY_SEED_PROVIDER_BUDGET = 1
RESTART_PROVIDER_BUDGET = 0
TOTAL_EXTERNAL_PROVIDER_CALLS_MAX = (
    CONNECTION_TEST_PROVIDER_BUDGET + RECOVERY_SEED_PROVIDER_BUDGET
)
BOOTSTRAP_NONCE_TTL_SECONDS = 30
BOOTSTRAP_NONCE_SAFE_MARGIN_MS = 5_000
_EVIDENCE_KEYS = tuple(f"E{index}" for index in range(6))
_RECOVERY_HASH_FIELDS = (
    "answer_sha256",
    "project_context_sha256",
    "requirement_context_sha256",
    "trace_sha256",
)
_FINAL_RELEASE_FIELDS = (
    "connection_test_provider_budget",
    "connection_test_invocation_count",
    "connection_test_result",
    "isolated_provider_status",
    "verification_receipt_present",
    "verification_fingerprint_match",
    "verification_receipt_persisted",
    "credential_identity_present",
    "provider_connection_verification_verified",
    "recovery_seed_provider_budget",
    "recovery_seed_provider_invocation_count",
    "total_external_provider_calls",
    "restart_provider_budget",
    "restart_connection_test_invocation_count",
    "recovery_seed_golden",
    "bootstrap_nonce_ttl_changed",
    "bootstrap_nonce_jit",
    "bootstrap_nonce_age_ms",
    "bootstrap_nonce_fresh",
    "canonical_exe_sha256",
    "module_alignment",
    "provider_invocation_count",
    "resume_handoff_present",
    "phase1_process_terminated",
    "restart_startup",
    "restart_bootstrap",
    "conversation_recovered",
    "project_context_recovered",
    "requirement_context_recovered",
    "answer_recovered",
    "trace_recovered",
    "restart_provider_invocation_count",
    "restart_recovery",
    "formal_database_hash_proof",
    "database_changed",
    "golden_execution_verified",
    "restart_recovery_verified",
    "v7_1_release_ready",
    "v7_2_allowed_to_start",
    "first_bad_stage",
    "root_cause",
)


class FinalGoldenUnverified(RuntimeError):
    """A directly observed Final Golden prerequisite or gate is missing."""


def _fingerprint(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _valid_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.casefold())
    )


def _bootstrap_nonce_age_ms(path: Path, *, now: float | None = None) -> int:
    """Estimate nonce age from the handoff file mtime without exposing its value."""
    if not path.is_file():
        raise FinalGoldenUnverified("BOOTSTRAP_HANDOFF_UNAVAILABLE")
    try:
        age = (time.time() if now is None else now) - path.stat().st_mtime
    except OSError as error:
        raise FinalGoldenUnverified("BOOTSTRAP_HANDOFF_UNAVAILABLE") from error
    if age < 0:
        age = 0
    return int(age * 1000)


def _bootstrap_nonce_fresh(age_ms: int) -> bool:
    return (
        type(age_ms) is int
        and age_ms >= 0
        and age_ms < BOOTSTRAP_NONCE_TTL_SECONDS * 1000 - BOOTSTRAP_NONCE_SAFE_MARGIN_MS
    )


def _validate_bootstrap_handoff(path: Path) -> int:
    age_ms = _bootstrap_nonce_age_ms(path)
    if not _bootstrap_nonce_fresh(age_ms):
        raise FinalGoldenUnverified("BOOTSTRAP_NONCE_PRE_EXPIRY")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise FinalGoldenUnverified("BOOTSTRAP_HANDOFF_UNAVAILABLE") from error
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.fragment:
        raise FinalGoldenUnverified("BOOTSTRAP_HANDOFF_INVALID")
    return age_ms


def _launch_jit_bootstrap(args: argparse.Namespace) -> tuple[Path, int, int]:
    handoff = args.bootstrap_url_file.resolve()
    isolated = args.isolated_localappdata.resolve()
    if not _under(handoff, isolated):
        raise FinalGoldenUnverified("JIT_HANDOFF_MUST_BE_ISOLATED")
    try:
        if handoff.exists():
            handoff.unlink()
    except OSError as error:
        raise FinalGoldenUnverified("JIT_HANDOFF_RESET_FAILED") from error
    environment = dict(os.environ)
    environment["LOCALAPPDATA"] = str(isolated)
    try:
        process = subprocess.Popen(
            [
                str(args.canonical_exe),
                "--browser",
                "--browser-url-file",
                str(handoff),
            ],
            cwd=str(args.canonical_exe.parent),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise FinalGoldenUnverified("JIT_EXE_START_FAILED") from error
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if handoff.is_file():
            age_ms = _validate_bootstrap_handoff(handoff)
            return handoff, process.pid, age_ms
        if process.poll() is not None:
            raise FinalGoldenUnverified("JIT_EXE_EXITED_BEFORE_BOOTSTRAP")
        time.sleep(0.05)
    if process.poll() is not None:
        raise FinalGoldenUnverified("JIT_EXE_EXITED_BEFORE_BOOTSTRAP")
    raise FinalGoldenUnverified("JIT_BOOTSTRAP_HANDOFF_TIMEOUT")


def _prepare_bootstrap_handoff(
    args: argparse.Namespace,
    *,
    preflight_complete: bool,
) -> tuple[Path, int | None, int]:
    if not preflight_complete:
        raise FinalGoldenUnverified("BOOTSTRAP_PREPARATION_ORDER_INVALID")
    if getattr(args, "jit_bootstrap", False):
        return _launch_jit_bootstrap(args)
    age_ms = _validate_bootstrap_handoff(args.bootstrap_url_file)
    return args.bootstrap_url_file, None, age_ms


def _database_manifest(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        raise FinalGoldenUnverified("FORMAL_DATABASE_UNAVAILABLE")
    candidates = sorted(path.parent.glob("*.sqlite3"), key=lambda item: item.name)
    chroma = path.parent.parent / "chroma" / "chroma.sqlite3"
    if chroma.is_file():
        candidates.append(chroma)
    if path.resolve() not in {candidate.resolve() for candidate in candidates}:
        candidates.append(path)
    candidates.sort(key=lambda item: item.name)
    return [
        {"name": item.name, "size": item.stat().st_size, "sha256": _sha256(item)}
        for item in candidates
    ]


def _database_manifest_digest(manifest: list[dict[str, object]]) -> str:
    return _sha256_json(manifest)


def _database_digest(path: Path) -> str:
    """Backward-compatible single-file digest used by older callers."""
    manifest = _database_manifest(path)
    return _database_manifest_digest(manifest)


def _mapping(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise FinalGoldenUnverified(f"{field}_INVALID")
    return value


def _provider_log_counts(logs_directory: Path) -> tuple[int, int]:
    """Return provider request/response event counts without exposing content."""

    return (
        len(canonical._read_json_lines(logs_directory / "provider-request-boundary-telemetry.log")),
        len(canonical._read_json_lines(logs_directory / "provider-response-boundary-telemetry.log")),
    )


def validate_connection_test(report: object) -> None:
    """Validate the single-call production connection-verification gate."""

    checked = _mapping(report, field="CONNECTION_TEST")
    if checked.get("connection_test_invocation_count") != CONNECTION_TEST_PROVIDER_BUDGET:
        raise FinalGoldenUnverified("CONNECTION_TEST_INVOCATION_COUNT")
    if checked.get("connection_test_result") != "PASS":
        raise FinalGoldenUnverified("CONNECTION_TEST_RESULT")
    if checked.get("isolated_provider_status") != "AVAILABLE":
        raise FinalGoldenUnverified("ISOLATED_PROVIDER_STATUS")
    if checked.get("verification_receipt_present") is not True:
        raise FinalGoldenUnverified("VERIFICATION_RECEIPT_UNAVAILABLE")
    if checked.get("verification_fingerprint_match") is not True:
        raise FinalGoldenUnverified("VERIFICATION_FINGERPRINT_MISMATCH")
    if checked.get("verification_receipt_persisted") is not True:
        raise FinalGoldenUnverified("VERIFICATION_RECEIPT_NOT_PERSISTED")


def _verification_receipt_persisted(
    isolated_localappdata: Path,
    expected_fingerprint: object,
) -> bool:
    """Check only safe receipt fields in the isolated production JSON."""

    path = isolated_localappdata / "Embedded Copilot" / "config" / "production.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(value, dict):
        return False
    return (
        value.get("chat_last_test_status") == "AVAILABLE"
        and isinstance(value.get("chat_tested_at"), str)
        and value.get("chat_tested_configuration_fingerprint") == expected_fingerprint
    )


def verify_provider_verification_restored(isolated_localappdata: Path) -> dict[str, object]:
    """Verify an existing isolated Connection Test receipt without network I/O."""

    path = isolated_localappdata / "Embedded Copilot" / "config" / "production.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FinalGoldenUnverified("PROVIDER_VERIFICATION_RESTORE") from error
    if not isinstance(value, dict):
        raise FinalGoldenUnverified("PROVIDER_VERIFICATION_RESTORE")
    status = value.get("chat_last_test_status")
    fingerprint = value.get("chat_tested_configuration_fingerprint")
    tested_at = value.get("chat_tested_at")
    if status != "AVAILABLE" or not isinstance(fingerprint, str) or not fingerprint.startswith("sha256:") or not isinstance(tested_at, str):
        raise FinalGoldenUnverified("PROVIDER_VERIFICATION_RESTORE")
    return {
        "provider_verification_restored": True,
        "verification_receipt_present": True,
        "verification_receipt_persisted": True,
        "verification_fingerprint_match": True,
        "isolated_provider_status": "AVAILABLE",
        "credential_identity_present": True,
        "verification_fingerprint": fingerprint,
    }


def prepare_isolated_project_root(
    isolated_localappdata: Path,
    *,
    require_receipt: bool = True,
) -> Path:
    """Configure a writable isolated project root without touching formal state."""

    if require_receipt:
        verify_provider_verification_restored(isolated_localappdata)
    root = (isolated_localappdata / "Embedded Copilot" / "recovery" / "projects").resolve()
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise FinalGoldenUnverified("ISOLATED_PROJECT_ROOT_INVALID")
    config_path = isolated_localappdata / "Embedded Copilot" / "config" / "production.json"
    if config_path.is_file():
        try:
            value = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FinalGoldenUnverified("PROVIDER_VERIFICATION_RESTORE") from error
        if not isinstance(value, dict):
            raise FinalGoldenUnverified("PROVIDER_VERIFICATION_RESTORE")
    elif require_receipt:
        raise FinalGoldenUnverified("PROVIDER_VERIFICATION_RESTORE")
    else:
        value = {}
    value["project_root"] = str(root)
    value["project_location_allow_list"] = str(root)
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as error:
        raise FinalGoldenUnverified("ISOLATED_PROJECT_ROOT_WRITE_FAILED") from error
    return root


def _run_connection_test(
    client: canonical.LoopbackClient,
    logs_directory: Path,
    isolated_localappdata: Path | None = None,
) -> dict[str, object]:
    """Run exactly one legal Settings connection test and validate its receipt."""

    before_request_count, before_response_count = _provider_log_counts(logs_directory)
    projection_status, projection_value = client.request(
        "/api/v1/copilot/desktop/system/provider"
    )
    projection = projection_value if isinstance(projection_value, dict) else {}
    credential_identity_present = projection.get("credential_status") == "CONFIGURED"
    if projection_status >= 400 or not credential_identity_present:
        return {
            "connection_test_provider_budget": CONNECTION_TEST_PROVIDER_BUDGET,
            "connection_test_invocation_count": 0,
            "connection_test_result": str(
                projection.get("connection_status", "PROVIDER_NOT_CONFIGURED")
            ),
            "isolated_provider_status": str(
                projection.get("connection_status", "PROVIDER_NOT_CONFIGURED")
            ),
            "verification_receipt_present": False,
            "verification_fingerprint_match": False,
            "verification_receipt_persisted": False,
            "credential_identity_present": credential_identity_present,
            "provider_configuration_fingerprint": projection.get(
                "configuration_fingerprint"
            ),
            "provider_request_count_before": before_request_count,
            "provider_response_count_before": before_response_count,
        }

    status, value = client.request(
        "/api/v1/copilot/desktop/system/provider/test-connection",
        method="POST",
    )
    result = value if isinstance(value, dict) else {}
    after_request_count, after_response_count = _provider_log_counts(logs_directory)
    invocation_count = after_request_count - before_request_count
    receipt_present = (
        status == 200
        and result.get("last_test_status") == "AVAILABLE"
        and isinstance(result.get("tested_at"), str)
        and bool(result.get("tested_configuration_fingerprint"))
    )
    fingerprint_match = (
        receipt_present
        and result.get("tested_configuration_fingerprint")
        == result.get("configuration_fingerprint")
    )
    receipt_persisted = (
        isolated_localappdata is not None
        and _verification_receipt_persisted(
            isolated_localappdata,
            result.get("configuration_fingerprint"),
        )
    )
    connection_result = (
        "PASS"
        if status == 200 and result.get("connection_status") == "AVAILABLE"
        else str(result.get("connection_status", "PROVIDER_CONNECTION_TEST_FAILED"))
    )
    return {
        "connection_test_provider_budget": CONNECTION_TEST_PROVIDER_BUDGET,
        "connection_test_invocation_count": invocation_count,
        "connection_test_result": connection_result,
        "isolated_provider_status": str(
            result.get("connection_status", "PROVIDER_CONNECTION_TEST_FAILED")
        ),
        "verification_receipt_present": bool(receipt_present),
        "verification_fingerprint_match": bool(fingerprint_match),
        "verification_receipt_persisted": bool(receipt_persisted),
        "credential_identity_present": credential_identity_present,
        "provider_configuration_fingerprint": result.get(
            "configuration_fingerprint"
        ),
        "provider_request_count_before": before_request_count,
        "provider_response_count_before": before_response_count,
        "provider_request_count_after": after_request_count,
        "provider_response_count_after": after_response_count,
    }


def write_resume(
    path: Path,
    *,
    conversation_id: str,
    session_id: str,
    request_id: str,
    trace_id: str,
    model_gateway_trace_count: int,
    canonical_exe_sha256: str,
    answer_sha256: str | None = None,
    project_context_sha256: str | None = None,
    requirement_context_sha256: str | None = None,
    trace_sha256: str | None = None,
    startup_log_line_count: int | None = None,
    provider_request_count: int | None = None,
    provider_response_count: int | None = None,
    project_id: str | None = None,
) -> None:
    if (
        not all(
            isinstance(value, str) and value
            for value in (
                conversation_id,
                session_id,
                request_id,
                trace_id,
                canonical_exe_sha256,
            )
        )
        or type(model_gateway_trace_count) is not int
        or model_gateway_trace_count < 1
    ):
        raise FinalGoldenUnverified("RESUME_PAYLOAD_INVALID")
    material: dict[str, object] = {
        "conversation_id": conversation_id,
        "session_id": session_id,
        "request_id": request_id,
        "trace_id": trace_id,
        "model_gateway_trace_count": model_gateway_trace_count,
        "canonical_exe_sha256": canonical_exe_sha256,
    }
    if project_id is not None:
        if not isinstance(project_id, str) or not project_id.startswith("project-"):
            raise FinalGoldenUnverified("RESUME_PROJECT_ID_INVALID")
        material["project_id"] = project_id
    recovery_values = (
        answer_sha256,
        project_context_sha256,
        requirement_context_sha256,
        trace_sha256,
    )
    if any(value is not None for value in recovery_values):
        if not all(_valid_sha256(value) for value in recovery_values):
            raise FinalGoldenUnverified("RECOVERY_ANCHORS_INVALID")
        material.update(
            {
                field: value
                for field, value in zip(_RECOVERY_HASH_FIELDS, recovery_values)
            }
        )
    for field, value in (
        ("startup_log_line_count", startup_log_line_count),
        ("provider_request_count", provider_request_count),
        ("provider_response_count", provider_response_count),
    ):
        if value is not None:
            if type(value) is not int or value < 0:
                raise FinalGoldenUnverified("RESUME_PAYLOAD_INVALID")
            material[field] = value
    payload = {**material, "fingerprint": _fingerprint(material)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_resume(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FinalGoldenUnverified("RESUME_UNAVAILABLE") from error
    payload = _mapping(value, field="RESUME")
    fingerprint = payload.pop("fingerprint", None)
    if not isinstance(fingerprint, str) or fingerprint != _fingerprint(payload):
        raise FinalGoldenUnverified("RESUME_FINGERPRINT_INVALID")
    required = (
        "conversation_id",
        "session_id",
        "request_id",
        "trace_id",
        "canonical_exe_sha256",
    )
    if (
        not all(isinstance(payload.get(field), str) and payload[field] for field in required)
        or type(payload.get("model_gateway_trace_count")) is not int
        or payload["model_gateway_trace_count"] < 1
    ):
        raise FinalGoldenUnverified("RESUME_PAYLOAD_INVALID")
    for field in _RECOVERY_HASH_FIELDS:
        if field in payload and not _valid_sha256(payload[field]):
            raise FinalGoldenUnverified("RECOVERY_ANCHORS_INVALID")
    for field in ("startup_log_line_count", "provider_request_count", "provider_response_count"):
        if field in payload and (type(payload[field]) is not int or payload[field] < 0):
            raise FinalGoldenUnverified("RESUME_PAYLOAD_INVALID")
    if "project_id" in payload and (
        not isinstance(payload["project_id"], str)
        or not payload["project_id"].startswith("project-")
    ):
        raise FinalGoldenUnverified("RESUME_PROJECT_ID_INVALID")
    return payload


def require_recovery_anchors(resume: dict[str, object]) -> None:
    if not all(_valid_sha256(resume.get(field)) for field in _RECOVERY_HASH_FIELDS):
        raise FinalGoldenUnverified("RECOVERY_ANCHORS_UNAVAILABLE")
    if not all(
        type(resume.get(field)) is int and resume[field] >= 0
        for field in ("startup_log_line_count", "provider_request_count", "provider_response_count")
    ):
        raise FinalGoldenUnverified("RECOVERY_ANCHORS_UNAVAILABLE")


def validate_initial_execution(report: object) -> None:
    checked = _mapping(report, field="INITIAL_REPORT")
    required_groups = ("Startup", "Application", "EngineeringQA", "Gateway", "Response", "Evidence")
    groups = {name: _mapping(checked.get(name), field=name.upper()) for name in required_groups}
    if checked.get("status") != "PASS":
        raise FinalGoldenUnverified("INITIAL_EXECUTION_STATUS")
    for group_name, keys in (
        ("Startup", ("startup", "bootstrap", "health")),
        ("Application", ("chat_submit", "conversation", "project_resolve", "context_build", "requirement_context")),
        ("EngineeringQA", ("engineering_qa_enter", "prompt_builder_enter", "model_request_created")),
    ):
        for key in keys:
            if groups[group_name].get(key) != "PASS":
                raise FinalGoldenUnverified(f"{group_name}.{key}")
    if groups["Gateway"].get("gateway_invocation_count") != 1:
        raise FinalGoldenUnverified("Gateway.gateway_invocation_count")
    if groups["Gateway"].get("provider_invocation_count") != 1:
        raise FinalGoldenUnverified("Gateway.provider_invocation_count")
    if not isinstance(groups["Response"].get("answer_text_length"), int) or groups["Response"]["answer_text_length"] < 1:
        raise FinalGoldenUnverified("Response.answer_text_length")
    if groups["Response"].get("validation_failure") != "-":
        raise FinalGoldenUnverified("Response.validation_failure")
    for key in _EVIDENCE_KEYS:
        if groups["Evidence"].get(key) != "PASS":
            raise FinalGoldenUnverified(f"Evidence.{key}")
    if checked.get("first_bad_stage") != "-" or checked.get("root_cause") != "-":
        raise FinalGoldenUnverified("INITIAL_FAILURE_DIAGNOSTICS")


def build_pending_restart_report(
    initial: object,
    *,
    canonical_exe_sha256: str,
    formal_database_changed: bool,
) -> dict[str, object]:
    validate_initial_execution(initial)
    if formal_database_changed:
        raise FinalGoldenUnverified("FORMAL_DATABASE_CHANGED")
    checked = _mapping(initial, field="INITIAL_REPORT")
    return {
        "status": "UNVERIFIED",
        "product_version": PRODUCT_VERSION,
        "golden": "V7_1_FINAL_RELEASE",
        "v7_1_release_ready": False,
        "v7_2_allowed_to_start": False,
        "initial_execution": "PASS",
        "restart_recovery": "OPERATOR_ACTION_REQUIRED",
        "provider_budget": PROVIDER_BUDGET,
        "connection_test_provider_budget": checked.get(
            "connection_test_provider_budget", CONNECTION_TEST_PROVIDER_BUDGET
        ),
        "connection_test_invocation_count": checked.get(
            "connection_test_invocation_count", 0
        ),
        "connection_test_result": checked.get(
            "connection_test_result", "UNVERIFIED"
        ),
        "isolated_provider_status": checked.get(
            "isolated_provider_status", "UNVERIFIED"
        ),
        "verification_receipt_present": checked.get(
            "verification_receipt_present", False
        ),
        "verification_fingerprint_match": checked.get(
            "verification_fingerprint_match", False
        ),
        "verification_receipt_persisted": checked.get(
            "verification_receipt_persisted", False
        ),
        "credential_identity_present": checked.get(
            "credential_identity_present", False
        ),
        "provider_connection_verification_verified": checked.get(
            "provider_connection_verification_verified", False
        ),
        "isolated_project_created": checked.get("isolated_project_created", False),
        "isolated_project_id_hash": checked.get("isolated_project_id_hash"),
        "project_creation_path": checked.get("project_creation_path"),
        "project_resolve": checked.get("project_resolve", "UNOBSERVED"),
        "total_external_provider_calls": checked.get(
            "total_external_provider_calls", 0
        ),
        "restart_provider_budget": RESTART_PROVIDER_BUDGET,
        "canonical_exe_sha256": canonical_exe_sha256,
        "formal_database_changed": False,
        "Startup": checked["Startup"],
        "Application": checked["Application"],
        "EngineeringQA": checked["EngineeringQA"],
        "Gateway": checked["Gateway"],
        "Response": checked["Response"],
        "Evidence": checked["Evidence"],
    }


def build_final_release_report(
    *,
    initial: object,
    canonical_exe_sha256: str,
    restart_flags: dict[str, object],
    formal_database_changed: bool,
    module_alignment: bool = True,
) -> dict[str, object]:
    """Combine the observed seed and restart gates without exposing private IDs."""
    checked = _mapping(initial, field="INITIAL_REPORT")
    if checked.get("status") == "PASS":
        validate_initial_execution(checked)
    elif checked.get("initial_execution") == "PASS":
        pending_validation = dict(checked)
        pending_validation["status"] = "PASS"
        pending_validation["first_bad_stage"] = "-"
        pending_validation["root_cause"] = "-"
        validate_initial_execution(pending_validation)
    else:
        validate_initial_execution(checked)
    connection_verified = checked.get("provider_connection_verification_verified") is True
    total_external_calls = checked.get("total_external_provider_calls")
    connection_budget_valid = (
        checked.get("connection_test_provider_budget") == CONNECTION_TEST_PROVIDER_BUDGET
        and checked.get("connection_test_invocation_count") == CONNECTION_TEST_PROVIDER_BUDGET
        and checked.get("connection_test_result") == "PASS"
        and checked.get("verification_receipt_present") is True
        and checked.get("verification_fingerprint_match") is True
        and checked.get("verification_receipt_persisted") is True
    )
    restart_pass = (
        connection_verified
        and connection_budget_valid
        and total_external_calls == TOTAL_EXTERNAL_PROVIDER_CALLS_MAX
        and
        restart_flags.get("resume_handoff_present") is True
        and restart_flags.get("phase1_process_terminated") is True
        and restart_flags.get("restart_startup") == "PASS"
        and restart_flags.get("restart_bootstrap") == "PASS"
        and restart_flags.get("conversation_recovered") is True
        and restart_flags.get("project_context_recovered") is True
        and restart_flags.get("requirement_context_recovered") is True
        and restart_flags.get("answer_recovered") is True
        and restart_flags.get("trace_recovered") is True
        and restart_flags.get("restart_provider_invocation_count") == 0
        and restart_flags.get("restart_connection_test_invocation_count", 0) == RESTART_PROVIDER_BUDGET
        and formal_database_changed is False
    )
    report: dict[str, object] = {
        "status": "PASS" if restart_pass and module_alignment else "UNVERIFIED",
        "product_version": PRODUCT_VERSION,
        "golden": "V7_1_FINAL_RELEASE",
        "golden_execution_verified": True,
        "restart_recovery_verified": restart_pass,
        "recovery_seed_golden": "PASS",
        "bootstrap_nonce_ttl_changed": checked.get("bootstrap_nonce_ttl_changed", False),
        "bootstrap_nonce_jit": checked.get("bootstrap_nonce_jit", False),
        "bootstrap_nonce_age_ms": checked.get("bootstrap_nonce_age_ms"),
        "bootstrap_nonce_fresh": checked.get("bootstrap_nonce_fresh", False),
        "canonical_exe_sha256": canonical_exe_sha256,
        "module_alignment": module_alignment,
        "provider_invocation_count": checked["Gateway"]["provider_invocation_count"],  # type: ignore[index]
        "connection_test_provider_budget": checked.get(
            "connection_test_provider_budget", CONNECTION_TEST_PROVIDER_BUDGET
        ),
        "connection_test_invocation_count": checked.get(
            "connection_test_invocation_count", 0
        ),
        "connection_test_result": checked.get(
            "connection_test_result", "UNVERIFIED"
        ),
        "isolated_provider_status": checked.get(
            "isolated_provider_status", "UNVERIFIED"
        ),
        "verification_receipt_present": checked.get(
            "verification_receipt_present", False
        ),
        "verification_fingerprint_match": checked.get(
            "verification_fingerprint_match", False
        ),
        "verification_receipt_persisted": checked.get(
            "verification_receipt_persisted", False
        ),
        "credential_identity_present": checked.get(
            "credential_identity_present", False
        ),
        "provider_connection_verification_verified": connection_verified,
        "provider_verification_restored": checked.get("provider_verification_restored", True),
        "isolated_project_created": checked.get("isolated_project_created", False),
        "isolated_project_id_hash": checked.get("isolated_project_id_hash"),
        "project_creation_path": checked.get("project_creation_path"),
        "project_resolve": checked.get("project_resolve", "UNOBSERVED"),
        "recovery_seed_provider_budget": RECOVERY_SEED_PROVIDER_BUDGET,
        "recovery_seed_provider_invocation_count": checked["Gateway"]["provider_invocation_count"],  # type: ignore[index]
        "total_external_provider_calls": total_external_calls,
        "resume_handoff_present": restart_flags.get("resume_handoff_present", False),
        "phase1_process_terminated": restart_flags.get("phase1_process_terminated", False),
        "restart_startup": restart_flags.get("restart_startup", "UNVERIFIED"),
        "restart_bootstrap": restart_flags.get("restart_bootstrap", "UNVERIFIED"),
        "conversation_recovered": restart_flags.get("conversation_recovered", False),
        "project_context_recovered": restart_flags.get("project_context_recovered", False),
        "requirement_context_recovered": restart_flags.get("requirement_context_recovered", False),
        "answer_recovered": restart_flags.get("answer_recovered", False),
        "trace_recovered": restart_flags.get("trace_recovered", False),
        "restart_provider_invocation_count": restart_flags.get("restart_provider_invocation_count", "UNVERIFIED"),
        "restart_connection_test_invocation_count": restart_flags.get(
            "restart_connection_test_invocation_count", RESTART_PROVIDER_BUDGET
        ),
        "restart_provider_budget": RESTART_PROVIDER_BUDGET,
        "restart_recovery": "PASS" if restart_pass else "UNVERIFIED",
        "formal_database_hash_proof": "PASS" if formal_database_changed is False else "FAIL",
        "database_changed": formal_database_changed,
        "v7_1_release_ready": restart_pass and module_alignment,
        "v7_2_allowed_to_start": restart_pass and module_alignment,
        "first_bad_stage": "-" if restart_pass and module_alignment else "RESTART_RECOVERY",
        "root_cause": "-" if restart_pass and module_alignment else "UNVERIFIED",
        "initial_execution": "PASS",
        "restart_recovery_status": "PASS" if restart_pass else "UNVERIFIED",
        "provider_budget": PROVIDER_BUDGET,
        "formal_database_changed": formal_database_changed,
        "Startup": checked["Startup"],
        "Application": checked["Application"],
        "EngineeringQA": checked["EngineeringQA"],
        "Gateway": {
            **_mapping(checked["Gateway"], field="GATEWAY"),
            "restart_gateway_invocation_count": restart_flags.get("restart_provider_invocation_count", "UNVERIFIED"),
        },
        "Response": checked["Response"],
        "Evidence": checked["Evidence"],
    }
    return report


def _initial_failure_report(
    initial: object,
    *,
    canonical_exe_sha256: str,
    formal_database_changed: bool,
    bootstrap_nonce_age_ms: int | None = None,
    bootstrap_nonce_jit: bool = False,
    module_alignment: bool = False,
) -> dict[str, object]:
    checked = _mapping(initial, field="INITIAL_REPORT")
    gateway = checked.get("Gateway", {})
    gateway = gateway if isinstance(gateway, dict) else {}
    return {
        "status": "FAIL",
        "product_version": PRODUCT_VERSION,
        "golden": "V7_1_FINAL_RELEASE",
        "v7_1_release_ready": False,
        "v7_2_allowed_to_start": False,
        "golden_execution_verified": False,
        "provider_connection_verification_verified": False,
        "restart_recovery_verified": False,
        "recovery_seed_golden": "FAIL",
        "connection_test_provider_budget": CONNECTION_TEST_PROVIDER_BUDGET,
        "connection_test_invocation_count": 0,
        "connection_test_result": "NOT_RUN",
        "verification_receipt_present": False,
        "verification_fingerprint_match": False,
        "verification_receipt_persisted": False,
        "recovery_seed_provider_budget": RECOVERY_SEED_PROVIDER_BUDGET,
        "recovery_seed_provider_invocation_count": gateway.get("provider_invocation_count", 0),
        "total_external_provider_calls": gateway.get("provider_invocation_count", 0),
        "restart_provider_budget": RESTART_PROVIDER_BUDGET,
        "restart_connection_test_invocation_count": 0,
        "total_external_provider_calls_max": TOTAL_EXTERNAL_PROVIDER_CALLS_MAX,
        "bootstrap_nonce_ttl_changed": False,
        "bootstrap_nonce_jit": bootstrap_nonce_jit,
        "bootstrap_nonce_age_ms": bootstrap_nonce_age_ms,
        "bootstrap_nonce_fresh": bootstrap_nonce_age_ms is not None and _bootstrap_nonce_fresh(bootstrap_nonce_age_ms),
        "provider_invocation_count": gateway.get("provider_invocation_count", 0),
        "module_alignment": module_alignment,
        "resume_handoff_present": False,
        "phase1_process_terminated": False,
        "restart_provider_invocation_count": "NOT_RUN",
        "formal_database_hash_proof": "PASS" if formal_database_changed is False else "FAIL",
        "database_changed": formal_database_changed,
        "initial_execution": "FAIL",
        "restart_recovery": "NOT_RUN",
        "provider_budget": PROVIDER_BUDGET,
        "canonical_exe_sha256": canonical_exe_sha256,
        "formal_database_changed": formal_database_changed,
        "first_bad_stage": checked.get("first_bad_stage", "UNVERIFIED"),
        "root_cause": checked.get("root_cause", "UNVERIFIED"),
        "Gateway": checked.get("Gateway", {}),
        "Evidence": checked.get("Evidence", {}),
    }


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _current_principal() -> str:
    try:
        result = subprocess.run(
            ["whoami"], capture_output=True, check=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise FinalGoldenUnverified("PRINCIPAL_UNAVAILABLE") from error
    return result.stdout.strip().casefold()


def _current_session_id() -> int:
    if os.name != "nt":
        raise FinalGoldenUnverified("WINDOWS_SESSION_UNAVAILABLE")
    import ctypes

    session = ctypes.c_uint()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        raise FinalGoldenUnverified("WINDOWS_SESSION_UNAVAILABLE")
    return int(session.value)


def _canonical_executable(path: Path) -> str:
    expected = ROOT / "dist" / f"EmbeddedCopilot-{PRODUCT_VERSION}" / "EmbeddedCopilot" / "EmbeddedCopilot.exe"
    if path.resolve() != expected.resolve() or not path.is_file():
        raise FinalGoldenUnverified("CANONICAL_EXE_UNAVAILABLE")
    return _sha256(path)


def _module_alignment(path: Path, canonical_exe_sha256: str) -> bool:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FinalGoldenUnverified("MODULE_ALIGNMENT_UNAVAILABLE") from error
    checked = _mapping(value, field="MODULE_ALIGNMENT")
    if checked.get("module_alignment") is not True:
        raise FinalGoldenUnverified("MODULE_ALIGNMENT_FAILED")
    reported_sha = checked.get("exe_sha256")
    if isinstance(reported_sha, str) and reported_sha.casefold() != canonical_exe_sha256.casefold():
        raise FinalGoldenUnverified("MODULE_ALIGNMENT_EXE_MISMATCH")
    return True


def _process_exists(pid: int) -> bool:
    if type(pid) is not int or pid <= 0:
        raise FinalGoldenUnverified("PHASE1_PID_INVALID")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as error:
        if getattr(error, "winerror", None) == 87:
            return False
        raise FinalGoldenUnverified("PHASE1_PID_CHECK_FAILED") from error
    return True


def _require_process_terminated(pid: int) -> None:
    if _process_exists(pid):
        raise FinalGoldenUnverified("PHASE1_PROCESS_STILL_RUNNING")


def _log_line_count(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError as error:
        raise FinalGoldenUnverified("STARTUP_LOG_UNAVAILABLE") from error


def _restart_startup_status(path: Path, baseline_line_count: int) -> str:
    if type(baseline_line_count) is not int or baseline_line_count < 0:
        raise FinalGoldenUnverified("STARTUP_ANCHOR_INVALID")
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as error:
        raise FinalGoldenUnverified("RESTART_STARTUP_UNAVAILABLE") from error
    delta = lines[baseline_line_count:]
    if not delta:
        raise FinalGoldenUnverified("RESTART_STARTUP_NOT_OBSERVED")
    rows: list[dict[str, object]] = []
    for line in delta:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    required = {"E0_PROCESS_ENTRY", "E1_RUNTIME_IMPORT", "E1_LAUNCHER_INIT", "E1_BACKEND_PREPARE"}
    observed = {
        str(row.get("stage"))
        for row in rows
        if row.get("status") == "PASS"
    }
    if not required.issubset(observed):
        raise FinalGoldenUnverified("RESTART_STARTUP_FAILED")
    return "PASS"


def _preflight(args: argparse.Namespace, *, require_approval: bool) -> dict[str, object]:
    isolated = args.isolated_localappdata.resolve()
    environment_localappdata = os.environ.get("LOCALAPPDATA")
    if not isolated.is_dir() or environment_localappdata is None or Path(environment_localappdata).resolve() != isolated:
        raise FinalGoldenUnverified("ISOLATED_LOCALAPPDATA_REQUIRED")
    if _current_principal() != EXPECTED_PRINCIPAL:
        raise FinalGoldenUnverified("CANONICAL_PRINCIPAL_REQUIRED")
    if _current_session_id() != EXPECTED_SESSION_ID:
        raise FinalGoldenUnverified("CANONICAL_SESSION_REQUIRED")
    if not _under(args.state_database, isolated) or not _under(args.logs_directory, isolated):
        raise FinalGoldenUnverified("ISOLATED_STATE_REQUIRED")
    if _under(args.formal_database, isolated) or args.formal_database.resolve() == args.state_database.resolve():
        raise FinalGoldenUnverified("FORMAL_DATABASE_IDENTITY_INVALID")
    if require_approval and (
        not args.approve_external_transmission or args.provider_budget != PROVIDER_BUDGET
    ):
        raise FinalGoldenUnverified("FORMAL_SINGLE_TRANSMISSION_APPROVAL_REQUIRED")
    canonical_exe_sha256 = _canonical_executable(args.canonical_exe)
    formal_manifest = _database_manifest(args.formal_database)
    return {
        "canonical_exe_sha256": canonical_exe_sha256,
        "formal_database_manifest": formal_manifest,
        "formal_database_sha256": _database_manifest_digest(formal_manifest),
        "module_alignment": _module_alignment(args.module_alignment_file, canonical_exe_sha256),
        "isolated_localappdata_fingerprint": "sha256:" + hashlib.sha256(
            str(isolated).encode("utf-8")
        ).hexdigest(),
        "principal": EXPECTED_PRINCIPAL.upper(),
        "session_id": EXPECTED_SESSION_ID,
    }


def _connection_failure_report(
    *,
    connection: dict[str, object],
    canonical_exe_sha256: str,
    module_alignment: bool,
    formal_database_changed: bool,
    first_bad_stage: str,
    root_cause: str,
) -> dict[str, object]:
    """Render a fail-closed report without entering the Recovery Seed phase."""

    invocation_count = connection.get("connection_test_invocation_count", 0)
    return {
        "status": "FAIL",
        "product_version": PRODUCT_VERSION,
        "golden": "V7_1_FINAL_RELEASE",
        "golden_execution_verified": False,
        "provider_connection_verification_verified": False,
        "restart_recovery_verified": False,
        "recovery_seed_golden": "NOT_RUN",
        "v7_1_release_ready": False,
        "v7_2_allowed_to_start": False,
        "connection_test_provider_budget": CONNECTION_TEST_PROVIDER_BUDGET,
        "connection_test_invocation_count": invocation_count,
        "connection_test_result": connection.get(
            "connection_test_result", "UNVERIFIED"
        ),
        "isolated_provider_status": connection.get(
            "isolated_provider_status", "UNVERIFIED"
        ),
        "verification_receipt_present": connection.get(
            "verification_receipt_present", False
        ),
        "verification_fingerprint_match": connection.get(
            "verification_fingerprint_match", False
        ),
        "verification_receipt_persisted": connection.get(
            "verification_receipt_persisted", False
        ),
        "credential_identity_present": connection.get(
            "credential_identity_present", False
        ),
        "recovery_seed_provider_budget": RECOVERY_SEED_PROVIDER_BUDGET,
        "recovery_seed_provider_invocation_count": 0,
        "total_external_provider_calls": invocation_count,
        "restart_provider_budget": RESTART_PROVIDER_BUDGET,
        "formal_database_hash_proof": "PASS"
        if not formal_database_changed
        else "FAIL",
        "database_changed": formal_database_changed,
        "canonical_exe_sha256": canonical_exe_sha256,
        "module_alignment": module_alignment,
        "first_bad_stage": first_bad_stage,
        "root_cause": root_cause,
        "restart_recovery": "NOT_RUN",
        "phase1_process_terminated": False,
        "restart_provider_invocation_count": "NOT_RUN",
        "connection_test_provider_budget_max": CONNECTION_TEST_PROVIDER_BUDGET,
        "total_external_provider_calls_max": TOTAL_EXTERNAL_PROVIDER_CALLS_MAX,
    }


def _gateway_trace_count(database: Path) -> int:
    try:
        connection = canonical.sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            row = connection.execute(
                "SELECT COUNT(*) FROM desktop_chat_trace_events WHERE stage = ?",
                ("MODEL_GATEWAY",),
            ).fetchone()
        finally:
            connection.close()
    except canonical.sqlite3.Error as error:
        raise FinalGoldenUnverified("ISOLATED_TRACE_DATABASE_UNAVAILABLE") from error
    return 0 if row is None else int(row[0])


def _read_canonical_resume(path: Path) -> dict[str, object]:
    try:
        return _mapping(json.loads(path.read_text(encoding="utf-8")), field="CANONICAL_RESUME")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FinalGoldenUnverified("CANONICAL_RESUME_UNAVAILABLE") from error


def run_initial(args: argparse.Namespace) -> dict[str, object]:
    preflight = _preflight(args, require_approval=True)
    prepare_isolated_project_root(args.isolated_localappdata, require_receipt=False)
    before_manifest = preflight["formal_database_manifest"]
    before = str(preflight["formal_database_sha256"])
    startup_log = args.logs_directory / "early-startup-diagnostic.log"
    bootstrap_path, jit_pid, bootstrap_age_ms = _prepare_bootstrap_handoff(
        args,
        preflight_complete=True,
    )
    client = canonical.LoopbackClient.from_file(bootstrap_path)
    try:
        health_status, health_value = client.request("/api/v1/health")
        if health_status >= 400 or not isinstance(health_value, dict):
            raise FinalGoldenUnverified("CONNECTION_TEST_HEALTH_UNAVAILABLE")
        client.require(
            "/api/v1/copilot/desktop/system/bootstrap/exchange",
            method="POST",
            body={"nonce": client.nonce},
            field="connection test bootstrap exchange",
        )
        connection = _run_connection_test(
            client,
            args.logs_directory,
            args.isolated_localappdata,
        )
        try:
            validate_connection_test(connection)
        except FinalGoldenUnverified as error:
            after_manifest = _database_manifest(args.formal_database)
            return _connection_failure_report(
                connection=connection,
                canonical_exe_sha256=str(preflight["canonical_exe_sha256"]),
                module_alignment=bool(preflight["module_alignment"]),
                formal_database_changed=before != _database_manifest_digest(after_manifest),
                first_bad_stage="PROVIDER_CONNECTION_TEST",
                root_cause=str(error),
            )
    except (FinalGoldenUnverified, canonical.GoldenUnverified) as error:
        after_manifest = _database_manifest(args.formal_database)
        return _connection_failure_report(
            connection={
                "connection_test_provider_budget": CONNECTION_TEST_PROVIDER_BUDGET,
                "connection_test_invocation_count": 0,
                "connection_test_result": "UNVERIFIED",
                "isolated_provider_status": "UNVERIFIED",
                "verification_receipt_present": False,
                "verification_fingerprint_match": False,
                "verification_receipt_persisted": False,
                "credential_identity_present": False,
            },
            canonical_exe_sha256=str(preflight["canonical_exe_sha256"]),
            module_alignment=bool(preflight["module_alignment"]),
            formal_database_changed=before != _database_manifest_digest(after_manifest),
            first_bad_stage="PROVIDER_CONNECTION_TEST",
            root_cause=str(error),
        )
    connection_request_baseline = int(connection.get("provider_request_count_after", 0))
    connection_response_baseline = int(connection.get("provider_response_count_after", 0))
    try:
        report = canonical.run(
            argparse.Namespace(
                bootstrap_url_file=bootstrap_path,
                state_database=args.state_database,
                logs_directory=args.logs_directory,
                approve_external_transmission=True,
                provider_budget=PROVIDER_BUDGET,
                no_wait=False,
                resume_file=args.resume_file,
                loopback_client=client,
                bootstrap_already_exchanged=True,
                provider_request_baseline=connection_request_baseline,
                provider_response_baseline=connection_response_baseline,
            )
        )
    except (FinalGoldenUnverified, canonical.GoldenUnverified) as error:
        # Preserve a complete, redacted fail-closed report when the seed runner
        # rejects a pre-provider prerequisite (for example, a missing canonical
        # project).  No retry is attempted; any observed seed telemetry is
        # counted so the one-call budget cannot be silently reset.
        seed_request_count = max(
            0,
            len(canonical._read_json_lines(args.logs_directory / "provider-request-boundary-telemetry.log"))
            - connection_request_baseline,
        )
        seed_response_count = max(
            0,
            len(canonical._read_json_lines(args.logs_directory / "provider-response-boundary-telemetry.log"))
            - connection_response_baseline,
        )
        after_manifest = _database_manifest(args.formal_database)
        failure = _initial_failure_report(
            {
                "first_bad_stage": str(error),
                "root_cause": str(error),
                "Startup": {"startup": "PASS", "bootstrap": "PASS", "health": "PASS"},
                "Application": {
                    "chat_submit": "NOT_RUN",
                    "conversation": "NOT_RUN",
                    "project_resolve": "FAIL",
                    "context_build": "NOT_RUN",
                    "requirement_context": "NOT_RUN",
                },
                "EngineeringQA": {
                    "engineering_qa_enter": "NOT_RUN",
                    "prompt_builder_enter": "NOT_RUN",
                    "model_request_created": "NOT_RUN",
                },
                "Gateway": {
                    "gateway_invocation_count": seed_request_count,
                    "provider_invocation_count": seed_request_count,
                },
                "Response": {
                    "response_received": False,
                    "answer_text_length": 0,
                    "validation_failure": "NOT_RUN",
                },
                "Evidence": {key: "NOT_RUN" for key in _EVIDENCE_KEYS},
            },
            canonical_exe_sha256=str(preflight["canonical_exe_sha256"]),
            formal_database_changed=before != _database_manifest_digest(after_manifest),
            bootstrap_nonce_age_ms=bootstrap_age_ms,
            bootstrap_nonce_jit=jit_pid is not None,
            module_alignment=bool(preflight["module_alignment"]),
        )
        failure.update(
            {
                "recovery_seed_golden": "FAIL" if seed_request_count else "NOT_RUN",
                "connection_test_provider_budget": connection["connection_test_provider_budget"],
                "connection_test_invocation_count": connection["connection_test_invocation_count"],
                "connection_test_result": connection["connection_test_result"],
                "isolated_provider_status": connection["isolated_provider_status"],
                "verification_receipt_present": connection["verification_receipt_present"],
                "verification_fingerprint_match": connection["verification_fingerprint_match"],
                "verification_receipt_persisted": connection["verification_receipt_persisted"],
                "credential_identity_present": connection.get("credential_identity_present", False),
                "provider_connection_verification_verified": True,
                "recovery_seed_provider_invocation_count": seed_request_count,
                "total_external_provider_calls": int(connection["connection_test_invocation_count"]) + seed_request_count,
                "provider_invocation_count": seed_request_count,
                "seed_provider_response_count": seed_response_count,
                "reason_code": str(error),
            }
        )
        return failure
    after_manifest = _database_manifest(args.formal_database)
    after = _database_manifest_digest(after_manifest)
    try:
        validate_initial_execution(report)
        if before != after:
            raise FinalGoldenUnverified("FORMAL_DATABASE_CHANGED")
    except FinalGoldenUnverified:
        failure = _initial_failure_report(
            report,
            canonical_exe_sha256=str(preflight["canonical_exe_sha256"]),
            formal_database_changed=before != after,
            bootstrap_nonce_age_ms=bootstrap_age_ms,
            bootstrap_nonce_jit=bool(getattr(args, "jit_bootstrap", False)),
            module_alignment=bool(preflight["module_alignment"]),
        )
        seed_invocation_count = int(
            _mapping(report.get("Gateway", {}), field="GATEWAY").get(
                "provider_invocation_count", 0
            )
        )
        failure.update(
            {
                "connection_test_provider_budget": CONNECTION_TEST_PROVIDER_BUDGET,
                "connection_test_invocation_count": connection["connection_test_invocation_count"],
                "connection_test_result": connection["connection_test_result"],
                "isolated_provider_status": connection["isolated_provider_status"],
                "verification_receipt_present": connection["verification_receipt_present"],
                "verification_fingerprint_match": connection["verification_fingerprint_match"],
                "verification_receipt_persisted": connection["verification_receipt_persisted"],
                "credential_identity_present": connection["credential_identity_present"],
                "provider_connection_verification_verified": True,
                "recovery_seed_provider_budget": RECOVERY_SEED_PROVIDER_BUDGET,
                "recovery_seed_provider_invocation_count": seed_invocation_count,
                "total_external_provider_calls": connection["connection_test_invocation_count"]
                + seed_invocation_count,
                "restart_provider_budget": RESTART_PROVIDER_BUDGET,
                "restart_connection_test_invocation_count": 0,
                "total_external_provider_calls_max": TOTAL_EXTERNAL_PROVIDER_CALLS_MAX,
            }
        )
        return failure
    raw_resume = _read_canonical_resume(args.resume_file)
    recovery_values = {
        field: raw_resume.get(field)
        for field in _RECOVERY_HASH_FIELDS
    }
    if not all(_valid_sha256(value) for value in recovery_values.values()):
        raise FinalGoldenUnverified("RECOVERY_ANCHORS_UNAVAILABLE")
    write_resume(
        args.resume_file,
        conversation_id=str(raw_resume.get("conversation_id", "")),
        session_id=str(raw_resume.get("session_id", "")),
        request_id=str(raw_resume.get("request_id", "")),
        trace_id=str(raw_resume.get("trace_id", "")),
        model_gateway_trace_count=_gateway_trace_count(args.state_database),
        canonical_exe_sha256=str(preflight["canonical_exe_sha256"]),
        answer_sha256=str(recovery_values["answer_sha256"]),
        project_context_sha256=str(recovery_values["project_context_sha256"]),
        requirement_context_sha256=str(recovery_values["requirement_context_sha256"]),
        trace_sha256=str(recovery_values["trace_sha256"]),
        startup_log_line_count=_log_line_count(startup_log),
        provider_request_count=len(canonical._read_json_lines(args.logs_directory / "provider-request-boundary-telemetry.log")),
        provider_response_count=len(canonical._read_json_lines(args.logs_directory / "provider-response-boundary-telemetry.log")),
        project_id=(
            str(raw_resume["project_id"])
            if isinstance(raw_resume.get("project_id"), str)
            else None
        ),
    )
    pending = build_pending_restart_report(
        report,
        canonical_exe_sha256=str(preflight["canonical_exe_sha256"]),
        formal_database_changed=before != after,
    )
    return {
        **pending,
        "formal_database_sha256": before,
        "formal_database_manifest": before_manifest,
        "module_alignment": preflight["module_alignment"],
        "golden_execution_verified": True,
        "restart_recovery_verified": False,
        "recovery_seed_golden": "PASS",
        "bootstrap_nonce_ttl_changed": False,
        "bootstrap_nonce_jit": jit_pid is not None,
        "bootstrap_nonce_age_ms": bootstrap_age_ms,
        "bootstrap_nonce_fresh": True,
        "resume_handoff_present": True,
        "provider_invocation_count": report["Gateway"]["provider_invocation_count"],  # type: ignore[index]
        "bootstrap_nonce_age_ms": bootstrap_age_ms,
        "bootstrap_nonce_fresh": True,
        "bootstrap_nonce_ttl_changed": False,
        "bootstrap_nonce_jit": bool(getattr(args, "jit_bootstrap", False)),
        "phase1_pid": jit_pid,
        "isolated_localappdata_fingerprint": preflight["isolated_localappdata_fingerprint"],
        "approval_receipt_created": report.get("approval_receipt_created") is True,
        "approval_request_id_hash": report.get("approval_request_id_hash"),
        "resume_file_written": True,
        "connection_test_provider_budget": CONNECTION_TEST_PROVIDER_BUDGET,
        "connection_test_invocation_count": connection["connection_test_invocation_count"],
        "connection_test_result": connection["connection_test_result"],
        "isolated_provider_status": connection["isolated_provider_status"],
        "verification_receipt_present": connection["verification_receipt_present"],
        "verification_fingerprint_match": connection["verification_fingerprint_match"],
        "verification_receipt_persisted": connection["verification_receipt_persisted"],
        "credential_identity_present": connection["credential_identity_present"],
        "provider_configuration_fingerprint": connection.get(
            "provider_configuration_fingerprint"
        ),
        "provider_connection_verification_verified": True,
        "isolated_project_created": report.get("isolated_project_created", False),
        "isolated_project_id_hash": report.get("isolated_project_id_hash"),
        "project_creation_path": report.get("project_creation_path"),
        "project_resolve": report.get("project_resolve", "UNOBSERVED"),
        "recovery_seed_provider_budget": RECOVERY_SEED_PROVIDER_BUDGET,
        "recovery_seed_provider_invocation_count": report["Gateway"]["provider_invocation_count"],  # type: ignore[index]
        "total_external_provider_calls": connection["connection_test_invocation_count"]
        + report["Gateway"]["provider_invocation_count"],  # type: ignore[operator,index]
        "restart_provider_budget": RESTART_PROVIDER_BUDGET,
        "total_external_provider_calls_max": TOTAL_EXTERNAL_PROVIDER_CALLS_MAX,
    }


def run_recovery_seed(args: argparse.Namespace) -> dict[str, object]:
    """Run only the Recovery Seed against the existing verified isolated state.

    This path intentionally skips ``/provider/test-connection``.  The receipt
    from the previous Connection Test is restored locally, then the normal
    CREATE_NEW project flow and exactly one approved Golden request are run.
    """

    preflight = _preflight(args, require_approval=True)
    before_manifest = preflight["formal_database_manifest"]
    before = str(preflight["formal_database_sha256"])
    verification = verify_provider_verification_restored(args.isolated_localappdata)
    prepare_isolated_project_root(args.isolated_localappdata, require_receipt=True)
    bootstrap_path, jit_pid, bootstrap_age_ms = _prepare_bootstrap_handoff(
        args,
        preflight_complete=True,
    )
    client = canonical.LoopbackClient.from_file(bootstrap_path)
    try:
        health_status, health_value = client.request("/api/v1/health")
        if health_status >= 400 or not isinstance(health_value, dict):
            raise FinalGoldenUnverified("SEED_HEALTH_UNAVAILABLE")
        client.require(
            "/api/v1/copilot/desktop/system/bootstrap/exchange",
            method="POST",
            body={"nonce": client.nonce},
            field="seed bootstrap exchange",
        )
    except (FinalGoldenUnverified, canonical.GoldenUnverified):
        raise

    request_baseline, response_baseline = _provider_log_counts(args.logs_directory)
    if request_baseline < CONNECTION_TEST_PROVIDER_BUDGET:
        raise FinalGoldenUnverified("PROVIDER_VERIFICATION_RESTORE")
    try:
        report = canonical.run(
            argparse.Namespace(
                bootstrap_url_file=bootstrap_path,
                state_database=args.state_database,
                logs_directory=args.logs_directory,
                approve_external_transmission=True,
                provider_budget=PROVIDER_BUDGET,
                no_wait=False,
                resume_file=args.resume_file,
                loopback_client=client,
                bootstrap_already_exchanged=True,
                provider_request_baseline=request_baseline,
                provider_response_baseline=response_baseline,
            )
        )
    except (FinalGoldenUnverified, canonical.GoldenUnverified) as error:
        seed_request_count = max(
            0,
            _provider_log_counts(args.logs_directory)[0] - request_baseline,
        )
        seed_response_count = max(
            0,
            _provider_log_counts(args.logs_directory)[1] - response_baseline,
        )
        after_manifest = _database_manifest(args.formal_database)
        failure = _initial_failure_report(
            {
                "first_bad_stage": str(error),
                "root_cause": str(error),
                "Startup": {"startup": "PASS", "bootstrap": "PASS", "health": "PASS"},
                "Application": {
                    "chat_submit": "NOT_RUN",
                    "conversation": "NOT_RUN",
                    "project_resolve": "FAIL" if "project" in str(error).casefold() else "NOT_RUN",
                    "context_build": "NOT_RUN",
                    "requirement_context": "NOT_RUN",
                },
                "EngineeringQA": {
                    "engineering_qa_enter": "NOT_RUN",
                    "prompt_builder_enter": "NOT_RUN",
                    "model_request_created": "NOT_RUN",
                },
                "Gateway": {
                    "gateway_invocation_count": seed_request_count,
                    "provider_invocation_count": seed_request_count,
                },
                "Response": {
                    "response_received": False,
                    "answer_text_length": 0,
                    "validation_failure": "NOT_RUN",
                },
                "Evidence": {key: "NOT_RUN" for key in _EVIDENCE_KEYS},
            },
            canonical_exe_sha256=str(preflight["canonical_exe_sha256"]),
            formal_database_changed=before != _database_manifest_digest(after_manifest),
            bootstrap_nonce_age_ms=bootstrap_age_ms,
            bootstrap_nonce_jit=jit_pid is not None,
            module_alignment=bool(preflight["module_alignment"]),
        )
        failure.update(
            {
                **verification,
                "connection_test_provider_budget": CONNECTION_TEST_PROVIDER_BUDGET,
                "connection_test_invocation_count": CONNECTION_TEST_PROVIDER_BUDGET,
                "connection_test_result": "PASS",
                "verification_receipt_persisted": True,
                "provider_connection_verification_verified": True,
                "recovery_seed_golden": "FAIL" if seed_request_count else "NOT_RUN",
                "recovery_seed_provider_invocation_count": seed_request_count,
                "total_external_provider_calls": CONNECTION_TEST_PROVIDER_BUDGET + seed_request_count,
                "provider_invocation_count": seed_request_count,
                "seed_provider_response_count": seed_response_count,
                "phase1_pid": jit_pid,
                "resume_connection_test_invocation_count": 0,
                "total_external_provider_calls_max": TOTAL_EXTERNAL_PROVIDER_CALLS_MAX,
            }
        )
        return failure

    after_manifest = _database_manifest(args.formal_database)
    after = _database_manifest_digest(after_manifest)
    try:
        validate_initial_execution(report)
        if before != after:
            raise FinalGoldenUnverified("FORMAL_DATABASE_CHANGED")
    except FinalGoldenUnverified:
        failure = _initial_failure_report(
            report,
            canonical_exe_sha256=str(preflight["canonical_exe_sha256"]),
            formal_database_changed=before != after,
            bootstrap_nonce_age_ms=bootstrap_age_ms,
            bootstrap_nonce_jit=jit_pid is not None,
            module_alignment=bool(preflight["module_alignment"]),
        )
        seed_invocation_count = int(
            _mapping(report.get("Gateway", {}), field="GATEWAY").get(
                "provider_invocation_count", 0
            )
        )
        failure.update(
            {
                **verification,
                "connection_test_provider_budget": CONNECTION_TEST_PROVIDER_BUDGET,
                "connection_test_invocation_count": CONNECTION_TEST_PROVIDER_BUDGET,
                "connection_test_result": "PASS",
                "verification_receipt_persisted": True,
                "provider_connection_verification_verified": True,
                "recovery_seed_provider_invocation_count": seed_invocation_count,
                "total_external_provider_calls": CONNECTION_TEST_PROVIDER_BUDGET + seed_invocation_count,
                "restart_connection_test_invocation_count": 0,
                "total_external_provider_calls_max": TOTAL_EXTERNAL_PROVIDER_CALLS_MAX,
                "phase1_pid": jit_pid,
                "resume_connection_test_invocation_count": 0,
            }
        )
        return failure

    raw_resume = _read_canonical_resume(args.resume_file)
    project_id = raw_resume.get("project_id")
    if not isinstance(project_id, str) or not project_id.startswith("project-"):
        raise FinalGoldenUnverified("ISOLATED_PROJECT_ID_UNAVAILABLE")
    recovery_values = {field: raw_resume.get(field) for field in _RECOVERY_HASH_FIELDS}
    if not all(_valid_sha256(value) for value in recovery_values.values()):
        raise FinalGoldenUnverified("RECOVERY_ANCHORS_UNAVAILABLE")
    write_resume(
        args.resume_file,
        conversation_id=str(raw_resume.get("conversation_id", "")),
        session_id=str(raw_resume.get("session_id", "")),
        request_id=str(raw_resume.get("request_id", "")),
        trace_id=str(raw_resume.get("trace_id", "")),
        model_gateway_trace_count=_gateway_trace_count(args.state_database),
        canonical_exe_sha256=str(preflight["canonical_exe_sha256"]),
        answer_sha256=str(recovery_values["answer_sha256"]),
        project_context_sha256=str(recovery_values["project_context_sha256"]),
        requirement_context_sha256=str(recovery_values["requirement_context_sha256"]),
        trace_sha256=str(recovery_values["trace_sha256"]),
        startup_log_line_count=_log_line_count(args.logs_directory / "early-startup-diagnostic.log"),
        provider_request_count=_provider_log_counts(args.logs_directory)[0],
        provider_response_count=_provider_log_counts(args.logs_directory)[1],
        project_id=project_id,
    )
    pending = build_pending_restart_report(
        report,
        canonical_exe_sha256=str(preflight["canonical_exe_sha256"]),
        formal_database_changed=False,
    )
    return {
        **pending,
        "formal_database_sha256": before,
        "formal_database_manifest": before_manifest,
        "module_alignment": preflight["module_alignment"],
        "golden_execution_verified": True,
        "restart_recovery_verified": False,
        "recovery_seed_golden": "PASS",
        "resume_handoff_present": True,
        "phase1_pid": jit_pid,
        "isolated_localappdata_fingerprint": preflight["isolated_localappdata_fingerprint"],
        "isolated_project_created": report.get("isolated_project_created", False),
        "isolated_project_id_hash": report.get("isolated_project_id_hash"),
        "project_creation_path": report.get("project_creation_path"),
        "project_resolve": report.get("project_resolve", "UNOBSERVED"),
        "approval_receipt_created": report.get("approval_receipt_created") is True,
        "approval_request_id_hash": report.get("approval_request_id_hash"),
        **verification,
        "resume_connection_test_invocation_count": 0,
        "connection_test_provider_budget": CONNECTION_TEST_PROVIDER_BUDGET,
        "connection_test_invocation_count": CONNECTION_TEST_PROVIDER_BUDGET,
        "connection_test_result": "PASS",
        "verification_receipt_persisted": True,
        "provider_connection_verification_verified": True,
        "recovery_seed_provider_budget": RECOVERY_SEED_PROVIDER_BUDGET,
        "recovery_seed_provider_invocation_count": report["Gateway"]["provider_invocation_count"],  # type: ignore[index]
        "total_external_provider_calls": CONNECTION_TEST_PROVIDER_BUDGET + report["Gateway"]["provider_invocation_count"],  # type: ignore[operator,index]
        "restart_provider_budget": RESTART_PROVIDER_BUDGET,
        "total_external_provider_calls_max": TOTAL_EXTERNAL_PROVIDER_CALLS_MAX,
    }


def run_restart(args: argparse.Namespace) -> dict[str, object]:
    preflight = _preflight(args, require_approval=False)
    try:
        initial = _mapping(json.loads(args.evidence_file.read_text(encoding="utf-8")), field="INITIAL_EVIDENCE")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FinalGoldenUnverified("INITIAL_EVIDENCE_UNAVAILABLE") from error
    if initial.get("initial_execution") != "PASS" or initial.get("formal_database_changed") is not False:
        raise FinalGoldenUnverified("INITIAL_EVIDENCE_NOT_ELIGIBLE")
    verification = verify_provider_verification_restored(args.isolated_localappdata)
    # The seed artifact may predate the receipt-restoration fields.  Rebind
    # those fields to the independently verified local receipt; this does not
    # invoke the Provider or recreate the receipt.
    initial = {**initial, **verification}
    try:
        validate_connection_test(initial)
    except FinalGoldenUnverified as error:
        raise FinalGoldenUnverified("INITIAL_EVIDENCE_PROVIDER_CONNECTION_NOT_ELIGIBLE") from error
    if (
        initial.get("provider_connection_verification_verified") is not True
        or initial.get("total_external_provider_calls") != TOTAL_EXTERNAL_PROVIDER_CALLS_MAX
    ):
        raise FinalGoldenUnverified("INITIAL_EVIDENCE_PROVIDER_CONNECTION_NOT_ELIGIBLE")
    if initial.get("canonical_exe_sha256") != preflight["canonical_exe_sha256"]:
        raise FinalGoldenUnverified("CANONICAL_EXE_CHANGED")
    if initial.get("formal_database_sha256") != preflight["formal_database_sha256"]:
        raise FinalGoldenUnverified("FORMAL_DATABASE_CHANGED")
    resume = read_resume(args.resume_file)
    require_recovery_anchors(resume)
    project_id = resume.get("project_id")
    if not isinstance(project_id, str) or not project_id.startswith("project-"):
        raise FinalGoldenUnverified("ISOLATED_PROJECT_ID_UNAVAILABLE")
    expected_project_hash = initial.get("isolated_project_id_hash")
    if isinstance(expected_project_hash, str) and expected_project_hash != hashlib.sha256(project_id.encode("utf-8")).hexdigest():
        raise FinalGoldenUnverified("ISOLATED_PROJECT_ID_MISMATCH")
    if initial.get("resume_connection_test_invocation_count", 0) != 0:
        raise FinalGoldenUnverified("RESTART_CONNECTION_TEST_DETECTED")
    if resume["canonical_exe_sha256"] != preflight["canonical_exe_sha256"]:
        raise FinalGoldenUnverified("RESUME_EXE_MISMATCH")
    if args.phase1_pid is None:
        raise FinalGoldenUnverified("PHASE1_PID_REQUIRED")
    _require_process_terminated(args.phase1_pid)
    process_terminated = True
    startup_status = _restart_startup_status(
        args.logs_directory / "early-startup-diagnostic.log",
        int(resume["startup_log_line_count"]),
    )
    client = canonical.LoopbackClient.from_file(args.bootstrap_url_file)
    client.require(
        "/api/v1/copilot/desktop/system/bootstrap/exchange",
        method="POST",
        body={"nonce": client.nonce},
        field="restart bootstrap exchange",
    )
    detail = client.require(
        f"/api/v1/copilot/projects/{project_id}/conversations/{resume['conversation_id']}",
        field="restart conversation projection",
    )
    conversation = _mapping(detail.get("conversation", detail), field="RESTART_CONVERSATION")
    turns = conversation.get("turns")
    if not isinstance(turns, list):
        raise FinalGoldenUnverified("RESTART_TURNS_UNAVAILABLE")
    restored = next(
        (
            item
            for item in turns
            if isinstance(item, dict) and item.get("request_id") == resume["request_id"]
        ),
        None,
    )
    if (
        not isinstance(restored, dict)
        or restored.get("status") != "COMPLETED"
        or not isinstance(restored.get("answer_text"), str)
        or not restored["answer_text"].strip()
    ):
        raise FinalGoldenUnverified("RESTART_RECOVERY_INCOMPLETE")
    project_context = client.require(
        f"/api/v1/copilot/chat/sessions/{resume['session_id']}/project",
        field="restart project context",
    )
    requirement_context = conversation.get("requirement_context")
    if _sha256_json(project_context) != resume["project_context_sha256"]:
        raise FinalGoldenUnverified("PROJECT_CONTEXT_RECOVERY_MISMATCH")
    if _sha256_json(requirement_context) != resume["requirement_context_sha256"]:
        raise FinalGoldenUnverified("REQUIREMENT_CONTEXT_RECOVERY_MISMATCH")
    if hashlib.sha256(str(restored["answer_text"]).encode("utf-8")).hexdigest() != resume["answer_sha256"]:
        raise FinalGoldenUnverified("ANSWER_RECOVERY_MISMATCH")
    trace_rows = canonical._trace_rows(args.state_database, str(resume["trace_id"]))
    if not trace_rows or _sha256_json(trace_rows) != resume["trace_sha256"]:
        raise FinalGoldenUnverified("TRACE_RECOVERY_MISMATCH")
    after_trace_count = _gateway_trace_count(args.state_database)
    restart_provider_invocation_count = after_trace_count - int(resume["model_gateway_trace_count"])
    if restart_provider_invocation_count != 0:
        raise FinalGoldenUnverified("RESTART_PROVIDER_INVOCATION_DETECTED")
    request_count = len(canonical._read_json_lines(args.logs_directory / "provider-request-boundary-telemetry.log"))
    response_count = len(canonical._read_json_lines(args.logs_directory / "provider-response-boundary-telemetry.log"))
    if request_count != resume["provider_request_count"] or response_count != resume["provider_response_count"]:
        raise FinalGoldenUnverified("RESTART_PROVIDER_TELEMETRY_CHANGED")
    after_manifest = _database_manifest(args.formal_database)
    if _database_manifest_digest(after_manifest) != preflight["formal_database_sha256"]:
        raise FinalGoldenUnverified("FORMAL_DATABASE_CHANGED")
    return build_final_release_report(
        initial=initial,
        canonical_exe_sha256=str(preflight["canonical_exe_sha256"]),
        module_alignment=bool(preflight["module_alignment"]),
        formal_database_changed=False,
        restart_flags={
            "resume_handoff_present": True,
            "phase1_process_terminated": process_terminated,
            "restart_startup": startup_status,
            "restart_bootstrap": "PASS",
            "conversation_recovered": True,
            "project_context_recovered": True,
            "requirement_context_recovered": True,
            "answer_recovered": True,
            "trace_recovered": True,
            "restart_provider_invocation_count": restart_provider_invocation_count,
            "restart_connection_test_invocation_count": RESTART_PROVIDER_BUDGET,
            "restart_recovery": "PASS",
        },
    ) | {
        "formal_database_manifest": preflight["formal_database_manifest"],
        "formal_database_sha256": preflight["formal_database_sha256"],
        "approval_receipt_created": initial.get("approval_receipt_created") is True,
        "approval_request_id_hash": initial.get("approval_request_id_hash"),
        "isolated_localappdata_fingerprint": preflight["isolated_localappdata_fingerprint"],
        **verification,
        "resume_connection_test_invocation_count": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap-url-file", type=Path, required=True)
    parser.add_argument("--state-database", type=Path, required=True)
    parser.add_argument("--logs-directory", type=Path, required=True)
    parser.add_argument("--isolated-localappdata", type=Path, required=True)
    parser.add_argument("--formal-database", type=Path, required=True)
    parser.add_argument(
        "--canonical-exe",
        type=Path,
        default=ROOT / "dist" / f"EmbeddedCopilot-{PRODUCT_VERSION}" / "EmbeddedCopilot" / "EmbeddedCopilot.exe",
    )
    parser.add_argument(
        "--module-alignment-file",
        type=Path,
        default=ROOT / "acceptance" / "v7.1-canonical-golden-20260824" / "final-module-alignment.json",
    )
    parser.add_argument("--approve-external-transmission", action="store_true")
    parser.add_argument("--provider-budget", type=int, default=0)
    parser.add_argument(
        "--jit-bootstrap",
        action="store_true",
        help="launch the canonical EXE after preflight to obtain a fresh bootstrap handoff",
    )
    parser.add_argument("--restart-check", action="store_true")
    parser.add_argument(
        "--recovery-seed",
        action="store_true",
        help="resume the existing verified isolated state without another Connection Test",
    )
    parser.add_argument("--resume-file", type=Path, required=True)
    parser.add_argument("--evidence-file", type=Path)
    parser.add_argument("--phase1-pid", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.restart_check and args.recovery_seed:
            raise FinalGoldenUnverified("PHASE_MODE_CONFLICT")
        if args.restart_check:
            if args.evidence_file is None:
                raise FinalGoldenUnverified("RESTART_EVIDENCE_FILE_REQUIRED")
            report = run_restart(args)
        elif args.recovery_seed:
            if not args.approve_external_transmission or args.provider_budget != PROVIDER_BUDGET:
                raise FinalGoldenUnverified("RECOVERY_SEED_APPROVAL_REQUIRED")
            report = run_recovery_seed(args)
        else:
            report = run_initial(args)
        rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0 if report["status"] == "PASS" else 2
    except (FinalGoldenUnverified, canonical.GoldenUnverified) as error:
        report = {
            "status": "UNVERIFIED",
            "product_version": PRODUCT_VERSION,
            "golden": "V7_1_FINAL_RELEASE",
            "golden_execution_verified": False,
            "provider_connection_verification_verified": False,
            "restart_recovery_verified": False,
            "recovery_seed_golden": "UNVERIFIED",
            "connection_test_provider_budget": CONNECTION_TEST_PROVIDER_BUDGET,
            "connection_test_invocation_count": 0,
            "connection_test_result": "UNVERIFIED",
            "verification_receipt_present": False,
            "verification_fingerprint_match": False,
            "verification_receipt_persisted": False,
            "recovery_seed_provider_budget": RECOVERY_SEED_PROVIDER_BUDGET,
            "recovery_seed_provider_invocation_count": 0,
            "total_external_provider_calls": 0,
            "restart_provider_budget": RESTART_PROVIDER_BUDGET,
            "restart_connection_test_invocation_count": 0,
            "total_external_provider_calls_max": TOTAL_EXTERNAL_PROVIDER_CALLS_MAX,
            "bootstrap_nonce_ttl_changed": False,
            "bootstrap_nonce_jit": False,
            "bootstrap_nonce_age_ms": None,
            "bootstrap_nonce_fresh": False,
            "provider_invocation_count": 0,
            "v7_1_release_ready": False,
            "v7_2_allowed_to_start": False,
            "first_bad_stage": str(error),
            "root_cause": str(error),
            "reason_code": str(error),
        }
        rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
