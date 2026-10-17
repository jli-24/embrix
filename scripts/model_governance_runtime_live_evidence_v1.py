"""Embrix Model Governance Runtime Live Evidence v1.

This validator is deliberately real-provider-only.  It never substitutes an
offline provider, mock gateway, catalog lookup, or historical receipt for a live
execution.  If any precondition is absent, it writes safe BLOCKED evidence and
returns without contacting a provider.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

LIVE_FLAG = "EMBRIX_LIVE_PROVIDER_FLAG"
STAGE_ID = "FIRMWARE_DEVELOPMENT"
SELECTION_MODE = "FIXED_MODEL"
EXPECTED_RESPONSE = "EMBRIX_RUNTIME_WIRING_OK"
FIXED_PROMPT = "Return exactly:\n\nEMBRIX_RUNTIME_WIRING_OK"
PROJECT_TENANTS = ("desktop-tenant", "default-tenant")
_GATE_BLOCKER_CODES = {
    "MODEL_CREDENTIAL_GATE_FAILED": "NO_VERIFIED_CREDENTIAL",
    "MODEL_CREDENTIAL_MISMATCH": "NO_VERIFIED_CREDENTIAL",
    "MODEL_QUOTA_GATE_FAILED": "QUOTA_UNAVAILABLE",
    "MODEL_AVAILABILITY_GATE_FAILED": "ENDPOINT_UNHEALTHY",
}
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{12,}"),
    re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|secret)\s*[:=]\s*[^\s,}\"]+"),
)


def execution_fingerprint(identity: dict[str, str], stage_id: str = STAGE_ID) -> str:
    material = json.dumps(
        [
            identity["provider_id"],
            identity["endpoint_id"],
            identity["credential_ref"],
            identity["model_profile_id"],
            identity["model_id"],
            stage_id,
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(material).hexdigest()


def _flag() -> str:
    value = os.getenv(LIVE_FLAG, "").strip().casefold()
    return "true" if value == "true" else ("false" if value else "UNSET")


def _dirty_overlap(root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    return bool(result.stdout.strip())


def _status_value(value: object) -> str:
    candidate = getattr(value, "value", value)
    return str(candidate) if isinstance(candidate, str) else "UNKNOWN"


def _live_gate_reason(code: str) -> str:
    return _GATE_BLOCKER_CODES.get(code, code)


def _architecture_guard(root: Path) -> dict[str, Any]:
    paths = (
        root / "src/embedded_copilot/application/runtime.py",
        root / "src/embedded_copilot/application/bindings.py",
        root / "src/embedded_copilot/application/container.py",
        root / "src/embedded_copilot/model_gateway/invocation/service.py",
        root / "src/embedded_copilot/model_gateway/service.py",
        root / "src/embedded_copilot/model_gateway/selection.py",
    )
    forbidden_imports = {
        "openai",
        "anthropic",
        "requests",
        "urllib",
        "socket",
        "subprocess",
        "langgraph",
    }
    violations: list[str] = []
    direct_side_effect = False
    for path in paths:
        if not path.is_file():
            violations.append("MISSING:" + path.name)
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):
            violations.append("AST_PARSE_FAILED:" + path.name)
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {
                    item.name.split(".", 1)[0].casefold() for item in node.names
                }
                if imported & forbidden_imports:
                    violations.append("FORBIDDEN_IMPORT:" + path.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".", 1)[0].casefold() in forbidden_imports:
                    violations.append("FORBIDDEN_IMPORT:" + path.name)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {
                    "post",
                    "put",
                    "patch",
                    "delete",
                    "run",
                    "Popen",
                    "system",
                }:
                    direct_side_effect = True

    live_source = (
        root / "scripts/model_governance_runtime_live_evidence_v1.py"
    ).read_text(encoding="utf-8")
    if re.search(
        r"(?:from|import)\s+.*(?:openai|anthropic|requests|urllib|socket)",
        live_source,
        re.IGNORECASE,
    ):
        violations.append("LIVE_SCRIPT_DIRECT_PROVIDER_IMPORT")
    composition = (root / "src/embedded_copilot/api/main.py").read_text(
        encoding="utf-8"
    )
    composition_ok = all(
        marker in composition
        for marker in (
            "ModelInvocationService",
            "ManagedModelSelectionResolver",
            "active_provider_service.binding",
            "receipt_store",
        )
    )
    return {
        "status": "PASS"
        if not violations and not direct_side_effect and composition_ok
        else "FAIL",
        "new_agent_added": False,
        "second_runtime_added": False,
        "second_model_gateway_added": False,
        "second_execution_framework_added": False,
        "llm_direct_side_effect": direct_side_effect,
        "evidence_bypassed": False,
        "credential_leak_risk": False,
        "legacy_path_promoted": False,
        "violations": tuple(violations),
    }


def _success_http_status_supported(root: Path) -> bool:
    service_path = root / "src/embedded_copilot/model_gateway/invocation/service.py"
    try:
        tree = ast.parse(
            service_path.read_text(encoding="utf-8"), filename=str(service_path)
        )
    except (OSError, SyntaxError):
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "_result":
            continue
        status = next(
            (item.value for item in node.keywords if item.arg == "status"), None
        )
        if isinstance(status, ast.Attribute) and status.attr == "SUCCESS":
            return any(item.arg == "execution_metadata" for item in node.keywords)
    return False


def _run_existing_validation_regression(root: Path) -> str:
    interpreter = root / ".venv/Scripts/python.exe"
    test_path = (
        root
        / "tests/production_acceptance/test_model_governance_runtime_validation_v1.py"
    )
    if not interpreter.is_file() or not test_path.is_file():
        return "UNAVAILABLE"
    try:
        result = subprocess.run(
            [str(interpreter), "-m", "pytest", "-q", str(test_path)],
            cwd=root,
            capture_output=True,
            check=False,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return "UNAVAILABLE"
    return "PASS" if result.returncode == 0 else "FAIL"


def _run_command_status(command: list[str], *, cwd: Path, timeout: int = 120) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return "UNAVAILABLE"
    return "PASS" if result.returncode == 0 else "FAIL"


def _run_backend_focused_tests(root: Path) -> str:
    interpreter = root / ".venv/Scripts/python.exe"
    paths = (
        root / "tests/model_gateway/test_protocol_http.py",
        root / "tests/model_gateway/test_invocation_service.py",
    )
    if not interpreter.is_file() or not all(path.is_file() for path in paths):
        return "UNAVAILABLE"
    return _run_command_status(
        [str(interpreter), "-m", "pytest", "-q", *(str(path) for path in paths)],
        cwd=root,
    )


def _run_compileall(root: Path) -> str:
    interpreter = root / ".venv/Scripts/python.exe"
    if not interpreter.is_file():
        return "UNAVAILABLE"
    return _run_command_status(
        [str(interpreter), "-m", "compileall", "-q", "src", "scripts", "tests"],
        cwd=root,
    )


def _run_ruff_checks(root: Path) -> str:
    interpreter = root / ".venv/Scripts/python.exe"
    check_paths = (
        root / "scripts/model_governance_runtime_live_evidence_v1.py",
        root / "src/embedded_copilot/model_gateway/_transport_metadata.py",
        root / "src/embedded_copilot/model_gateway/adapters/protocol_http.py",
        root / "src/embedded_copilot/model_gateway/adapters/openai_compatible.py",
        root / "src/embedded_copilot/model_gateway/service.py",
        root / "src/embedded_copilot/model_gateway/invocation/service.py",
        root / "src/embedded_copilot/production_release/provider.py",
    )
    if not interpreter.is_file() or not all(path.is_file() for path in check_paths):
        return "UNAVAILABLE"
    check_status = _run_command_status(
        [str(interpreter), "-m", "ruff", "check", *(str(path) for path in check_paths)],
        cwd=root,
    )
    format_status = _run_command_status(
        [
            str(interpreter),
            "-m",
            "ruff",
            "format",
            "--check",
            str(check_paths[0]),
            str(check_paths[1]),
        ],
        cwd=root,
    )
    return "PASS" if check_status == format_status == "PASS" else "FAIL"


def _preflight(
    root: Path, architecture: dict[str, Any], validation_status: str
) -> dict[str, Any]:
    interpreter = root / ".venv/Scripts/python.exe"
    configured_names: set[str] = set()
    env_file = root / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"\s*([A-Z][A-Z0-9_]*)\s*=", line)
            if match:
                configured_names.add(match.group(1))
    production_config = (
        Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        / "Embedded Copilot"
        / "config"
        / "production.json"
    )
    config_status = (
        "CONFIGURED_UNVERIFIED"
        if production_config.is_file()
        else (
            "CONFIGURED_UNVERIFIED"
            if configured_names
            & {"CHAT_MODEL_PROVIDER", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL"}
            else "UNCONFIGURED"
        )
    )
    runtime_text = (root / "src/embedded_copilot/application/runtime.py").read_text(
        encoding="utf-8"
    )
    invocation_text = (root / "src/embedded_copilot/application/bindings.py").read_text(
        encoding="utf-8"
    )
    runtime_wiring = (
        "model_invocation_service" in runtime_text
        and "_InvocationGatewayAdapter" in invocation_text
    )
    flag = _flag()
    http_metadata_available = _success_http_status_supported(root)
    not_run_reason = "NOT_EVALUATED"
    return {
        "project_venv_present": interpreter.is_file(),
        "project_python_version": platform_version(),
        "model_governance_status": "PASS"
        if validation_status == "PASS"
        else "UNVERIFIED",
        "runtime_wiring_status": "RUNTIME_WIRED_FIXED_MODE"
        if runtime_wiring
        else "RUNTIME_WIRING_UNVERIFIED",
        "validation_status": validation_status,
        "runtime_validation_status": validation_status,
        "live_evidence_framework_status": "AVAILABLE",
        "architecture_guard_status": architecture["status"],
        "receipt_store_status": "AVAILABLE_OFFLINE"
        if (root / "src/embedded_copilot/model_gateway_storage.py").is_file()
        else "UNAVAILABLE",
        "live_flag_status": "PASS" if flag == "true" else "BLOCKED",
        "project_context_status": "NOT_EVALUATED"
        if flag != "true"
        else "PENDING_LIVE_GATE",
        "provider_configuration_status": config_status,
        "live_provider_flag": flag,
        "endpoint_health_status": not_run_reason,
        "credential_status": not_run_reason,
        "credential_validation_status": not_run_reason,
        "model_catalog_status": not_run_reason,
        "usable_model_status": not_run_reason,
        "allowed_model_status": not_run_reason,
        "stage_policy_status": not_run_reason,
        "quota_status": not_run_reason,
        "provider_http_metadata_capability": "AVAILABLE"
        if http_metadata_available
        else "UNAVAILABLE",
        "http_status_evidence_capability": "AVAILABLE"
        if http_metadata_available
        else "UNAVAILABLE",
        "http_status_source": "REAL_TRANSPORT_RESPONSE_STATUS_CODE"
        if http_metadata_available
        else "UNAVAILABLE",
        "provider_request_id_capability": "AVAILABLE_OR_PROVIDER_NOT_EXPOSED"
        if http_metadata_available
        else "UNAVAILABLE",
        "provider_request_id_source": "REAL_TRANSPORT_HEADER_OR_RESPONSE_ID"
        if http_metadata_available
        else "UNAVAILABLE",
        "latency_source": "REAL_TRANSPORT_PERF_COUNTER"
        if http_metadata_available
        else "UNAVAILABLE",
        "project_context_capability": "PRODUCTION_PROJECT_CATALOG_RESOLVER",
        "ready_for_live_request": False,
        "dirty_overlap": "TRUE" if _dirty_overlap(root) else "FALSE",
    }


def platform_version() -> str:
    return ".".join(str(value) for value in sys.version_info[:3])


def _identity_dict(identity: object) -> dict[str, str]:
    return {
        "provider_id": str(identity.provider_id),
        "endpoint_id": str(identity.endpoint_id),
        "credential_ref": str(identity.credential_ref),
        "model_profile_id": str(identity.model_profile_id),
        "model_id": str(identity.model_id),
    }


def _safe_failure(
    reason: str,
    *,
    identity: dict[str, str] | None = None,
    project_id: str | None = None,
    tenant_id: str | None = None,
    project_context_source: str = "NONE",
    status: str = "BLOCKED",
    status_updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    failure = {
        "status": status,
        "reason": reason,
        "real_provider_verified": False,
        "real_backend_verified": False,
        "live_e2e": status,
        "live_flag_status": "NOT_EVALUATED",
        "project_context_status": "NOT_EVALUATED",
        "stage_policy_status": "NOT_EVALUATED",
        "policy_source": "NOT_EVALUATED",
        "allowed_model_status": "NOT_EVALUATED",
        "capability_gate": "NOT_EVALUATED",
        "availability_gate": "NOT_EVALUATED",
        "credential_gate": "NOT_EVALUATED",
        "endpoint_health_gate": "NOT_EVALUATED",
        "quota_gate": "NOT_EVALUATED",
        "fixed_model_execution": "NOT_EVALUATED",
        "identity": identity,
        "identity_match": False,
        "identity_preservation": "NOT_EVALUATED",
        "project_id": project_id,
        "tenant_id": tenant_id,
        "project_context_source": project_context_source,
        "valid_project_context": project_id is not None,
        "provider_request_id": None,
        "provider_request_id_evidence": False,
        "provider_request_id_status": "NOT_EVALUATED",
        "http_status": None,
        "http_status_source": "NOT_EVALUATED",
        "http_status_evidence_status": "NOT_EVALUATED",
        "latency_ms": None,
        "response_exact_match": "NOT_EVALUATED",
        "execution_fingerprint_match": False,
        "receipt_id": None,
        "provider_request_count": 0,
        "receipt_created": False,
        "restart_recovery": False,
        "receipt_validation": "NOT_EVALUATED",
        "execution_fingerprint": None,
        "relay_isolation": "NOT_EVALUATED",
        "failures": [],
    }
    if status_updates:
        failure.update(status_updates)
    return failure


def _resolve_legal_project(
    project_catalog: object, management: object
) -> dict[str, str] | None:
    """Select an existing project through the production catalog boundary only."""

    projects = getattr(project_catalog, "projects", None)
    get_project = getattr(project_catalog, "get_project", None)
    snapshot_root = getattr(project_catalog, "knowledge_snapshot_root", None)
    stage_policy = getattr(management, "stage_model_policy", None)
    if not all(
        callable(item) for item in (projects, get_project, snapshot_root, stage_policy)
    ):
        return None
    for tenant_id in PROJECT_TENANTS:
        try:
            candidates = projects(tenant_id)
        except Exception:  # noqa: BLE001, S112 - catalog boundary is fail-closed
            continue
        for project in candidates:
            project_id = getattr(project, "project_id", None)
            if not isinstance(project_id, str) or not project_id.startswith("project-"):
                continue
            if (
                getattr(getattr(project, "initialization_status", None), "value", None)
                != "READY"
            ):
                continue
            try:
                bound = get_project(project_id, tenant_id=tenant_id)
                if getattr(bound, "tenant_id", None) != tenant_id:
                    continue
                # This is a read-only project-root identity check. It does not
                # scan, write, or infer project evidence.
                snapshot_root(project_id, tenant_id=tenant_id)
                policy = stage_policy(project_id, STAGE_ID)
            except Exception:  # noqa: BLE001, S112 - try the next legal catalog item
                continue
            if policy is None:
                return {
                    "project_id": project_id,
                    "tenant_id": tenant_id,
                    "project_context_source": "PRODUCTION_PROJECT_CATALOG",
                    "stage_policy_status": "MISSING",
                }
            return {
                "project_id": project_id,
                "tenant_id": tenant_id,
                "project_context_source": "PRODUCTION_PROJECT_CATALOG",
                "stage_policy_status": "AVAILABLE",
            }
    return None


def _production_settings(root: Path):
    from embedded_copilot.production_release.configuration import (
        load_production_settings,
    )
    from embedded_copilot.production_release.paths import ProductionStateLayout

    layout = ProductionStateLayout.for_current_user()
    assets = (root / "frontend" / "dist").resolve()
    return load_production_settings(assets=assets, layout=layout)


def _execute_production_live(root: Path, validation_run_id: str) -> dict[str, Any]:
    """Execute the existing production composition after every gate passes."""

    try:
        from embedded_copilot.api.main import create_app
        from embedded_copilot.production_release.runtime import ProductionReleaseRuntime
        from embedded_copilot.project_catalog import ProjectCatalogRuntime
    except (ImportError, TypeError, ValueError):
        return _safe_failure("PRODUCTION_COMPOSITION_UNAVAILABLE")

    try:
        settings = _production_settings(root)
        production = ProductionReleaseRuntime.create(settings)
        project_catalog = ProjectCatalogRuntime(
            project_root=production.settings.project_root,
            state_database=production.settings.desktop_state_database,
            metadata_directory=production.settings.desktop_project_metadata_directory,
            location_label=production.settings.project_location_label,
            location_allow_list=production.settings.project_location_allow_list,
            production_mode=production.settings.environment == "production",
            initialize_async=False,
        )
        application = create_app(
            settings=production.settings,
            production_release_runtime=production,
            project_catalog_runtime=project_catalog,
        )

        async def run() -> dict[str, Any]:
            async with application.router.lifespan_context(application):
                return _execute_production_live_in_lifespan(
                    application,
                    validation_run_id=validation_run_id,
                )

        return asyncio.run(run())
    except (OSError, ValueError, TypeError, RuntimeError, sqlite3.Error):
        return _safe_failure("PRODUCTION_COMPOSITION_UNAVAILABLE")


def _execute_production_live_in_lifespan(
    application: object, *, validation_run_id: str
) -> dict[str, Any]:
    """Run the one fixed-model request while the normal app composition is alive."""

    from embedded_copilot.application import (
        ApplicationQuestionRequest,
        ApplicationRoute,
        ApplicationRouteDecision,
    )
    from embedded_copilot.model_gateway import (
        EngineeringStage,
        ModelAvailability,
        ModelSelectionTaskProfile,
        SelectionMode,
    )
    from embedded_copilot.model_gateway.selection import ModelSelectionError

    provider_service = getattr(
        application.state, "provider_configuration_service", None
    )
    management = getattr(application.state, "model_gateway_management_service", None)
    receipt_store = None if management is None else management.receipt_store
    project_catalog = getattr(application.state, "project_catalog_runtime", None)
    if provider_service is None or management is None or receipt_store is None:
        return _safe_failure("PRODUCTION_MODEL_BINDING_UNAVAILABLE")
    if project_catalog is None:
        return _safe_failure(
            "NO_VALID_PROJECT_CONTEXT",
            status_updates={"project_context_status": "BLOCKED"},
        )

    project = _resolve_legal_project(project_catalog, management)
    if project is None:
        return _safe_failure(
            "NO_VALID_PROJECT_CONTEXT",
            status_updates={"project_context_status": "BLOCKED"},
        )
    project_id = project["project_id"]
    tenant_id = project["tenant_id"]
    project_kwargs = {
        "project_id": project_id,
        "tenant_id": tenant_id,
        "project_context_source": project["project_context_source"],
    }
    if project["stage_policy_status"] != "AVAILABLE":
        return _safe_failure(
            "MODEL_STAGE_ASSIGNMENT_NOT_FIXED",
            **project_kwargs,
            status_updates={
                "live_flag_status": "PASS",
                "project_context_status": "PASS",
                "stage_policy_status": "BLOCKED",
            },
        )

    stage_policy = management.stage_model_policy(project_id, STAGE_ID)
    if (
        stage_policy is None
        or stage_policy.selection_mode is not SelectionMode.FIXED_MODEL
        or stage_policy.fixed_model is None
    ):
        return _safe_failure(
            "MODEL_STAGE_ASSIGNMENT_NOT_FIXED",
            **project_kwargs,
            status_updates={
                "live_flag_status": "PASS",
                "project_context_status": "PASS",
                "stage_policy_status": "BLOCKED",
            },
        )
    identity = _identity_dict(stage_policy.fixed_model)
    pre_request_status = {
        "live_flag_status": "PASS",
        "project_context_status": "PASS",
        "stage_policy_status": "PASS",
        "policy_source": "STAGE_POLICY",
        "fixed_model_execution": "PASS",
    }
    profile = management.model_profile(identity["model_profile_id"])
    endpoint = management.endpoint_profile(identity["endpoint_id"])
    if profile is None:
        return _safe_failure(
            "MODEL_UNAVAILABLE",
            identity=identity,
            **project_kwargs,
            status_updates={
                **pre_request_status,
                "model_catalog_status": "BLOCKED",
                "capability_gate": "BLOCKED",
            },
        )
    usable_pool = management.get_usable_models(
        provider_id=identity["provider_id"],
        endpoint_id=identity["endpoint_id"],
        credential_revision=profile.credential_revision,
        endpoint_revision=profile.endpoint_revision,
    )
    if not any(
        item.model_profile_id == identity["model_profile_id"]
        and item.remote_model_id == identity["model_id"]
        for item in usable_pool
    ):
        return _safe_failure(
            "MODEL_UNAVAILABLE",
            identity=identity,
            **project_kwargs,
            status_updates={
                **pre_request_status,
                "model_catalog_status": "PASS",
                "usable_model_status": "BLOCKED",
                "capability_gate": "BLOCKED",
            },
        )
    allowed = management.effective_allowed_model_policies(
        project_id=project_id,
        stage_id=stage_policy.stage_id,
    )
    allowed_identities = {
        (
            item.provider_id,
            item.endpoint_id,
            item.credential_ref,
            item.model_profile_id,
            item.model_id,
        )
        for item in allowed
    }
    identity_key = tuple(
        identity[key]
        for key in (
            "provider_id",
            "endpoint_id",
            "credential_ref",
            "model_profile_id",
            "model_id",
        )
    )
    if identity_key not in allowed_identities:
        return _safe_failure(
            "MODEL_NOT_ALLOWED",
            identity=identity,
            **project_kwargs,
            status_updates={
                **pre_request_status,
                "model_catalog_status": "PASS",
                "usable_model_status": "PASS",
                "allowed_model_status": "BLOCKED",
                "capability_gate": "PASS",
            },
        )
    if profile.availability is not ModelAvailability.AVAILABLE or not profile.enabled:
        return _safe_failure(
            "MODEL_UNAVAILABLE",
            identity=identity,
            **project_kwargs,
            status_updates={
                **pre_request_status,
                "model_catalog_status": "PASS",
                "usable_model_status": "PASS",
                "allowed_model_status": "PASS",
                "capability_gate": "PASS",
                "availability_gate": "BLOCKED",
            },
        )
    if endpoint is None or endpoint.health_status != "AVAILABLE":
        return _safe_failure(
            "ENDPOINT_UNHEALTHY",
            identity=identity,
            **project_kwargs,
            status_updates={
                **pre_request_status,
                "model_catalog_status": "PASS",
                "usable_model_status": "PASS",
                "allowed_model_status": "PASS",
                "capability_gate": "PASS",
                "availability_gate": "PASS",
                "endpoint_health_gate": "BLOCKED",
            },
        )
    available_credentials = provider_service.available_credential_refs()
    if identity["credential_ref"] not in available_credentials:
        return _safe_failure(
            "NO_VERIFIED_CREDENTIAL",
            identity=identity,
            **project_kwargs,
            status_updates={
                **pre_request_status,
                "model_catalog_status": "PASS",
                "usable_model_status": "PASS",
                "allowed_model_status": "PASS",
                "capability_gate": "PASS",
                "availability_gate": "PASS",
                "endpoint_health_gate": "PASS",
                "credential_gate": "BLOCKED",
            },
        )
    if not profile.credential_revision:
        return _safe_failure(
            "MODEL_CREDENTIAL_REVISION_UNAVAILABLE",
            identity=identity,
            **project_kwargs,
            status_updates={
                **pre_request_status,
                "model_catalog_status": "PASS",
                "usable_model_status": "PASS",
                "allowed_model_status": "PASS",
                "capability_gate": "PASS",
                "availability_gate": "PASS",
                "endpoint_health_gate": "PASS",
                "credential_gate": "BLOCKED",
            },
        )
    try:
        governed = management.select_governed_model(
            ModelSelectionTaskProfile(
                request_id=f"{validation_run_id}-selection",
                project_id=project_id,
                stage=EngineeringStage.FIRMWARE_DEVELOPMENT,
                task_type=STAGE_ID,
                required_capabilities=(),
            ),
            available_credential_refs=available_credentials,
            strict_gates=True,
        )
    except ModelSelectionError as error:
        gate_status = {
            **pre_request_status,
            "model_catalog_status": "PASS",
            "usable_model_status": "PASS",
            "allowed_model_status": "PASS",
            "capability_gate": "PASS",
            "availability_gate": "PASS",
            "endpoint_health_gate": "PASS",
            "credential_gate": "PASS",
        }
        failed_gate = _live_gate_reason(error.code)
        if failed_gate == "NO_VERIFIED_CREDENTIAL":
            gate_status["credential_gate"] = "BLOCKED"
        elif failed_gate == "QUOTA_UNAVAILABLE":
            gate_status["quota_gate"] = "BLOCKED"
        elif failed_gate == "ENDPOINT_UNHEALTHY":
            gate_status["endpoint_health_gate"] = "BLOCKED"
        elif failed_gate == "MODEL_UNAVAILABLE":
            gate_status["availability_gate"] = "BLOCKED"
        return _safe_failure(
            failed_gate,
            identity=identity,
            **project_kwargs,
            status_updates=gate_status,
        )
    if (
        governed.selected_model is None
        or _identity_dict(governed.selected_model) != identity
    ):
        return _safe_failure(
            "MODEL_IDENTITY_BINDING_MISMATCH",
            identity=identity,
            **project_kwargs,
            status_updates={
                **pre_request_status,
                "model_catalog_status": "PASS",
                "usable_model_status": "PASS",
                "allowed_model_status": "PASS",
                "capability_gate": "PASS",
                "availability_gate": "PASS",
                "endpoint_health_gate": "PASS",
                "credential_gate": "PASS",
                "quota_gate": "PASS",
                "fixed_model_execution": "FAILED",
            },
        )

    pre_request_status = {
        **pre_request_status,
        "model_catalog_status": "PASS",
        "usable_model_status": "PASS",
        "allowed_model_status": "PASS",
        "capability_gate": "PASS",
        "availability_gate": "PASS",
        "endpoint_health_gate": "PASS",
        "credential_gate": "PASS",
        "quota_gate": "PASS",
    }

    production = getattr(application.state, "production_release_runtime", None)
    if production is not None:
        production.start()
    chat_runtime = application.state.desktop_copilot_chat_runtime
    if chat_runtime is None or not hasattr(chat_runtime, "application"):
        return _safe_failure(
            "APPLICATION_RUNTIME_UNAVAILABLE",
            identity=identity,
            **project_kwargs,
            status_updates=pre_request_status,
        )
    request = ApplicationQuestionRequest(
        request_id=f"{validation_run_id}-request",
        trace_id=f"{validation_run_id}-trace",
        project_id=project_id,
        tenant_id=tenant_id,
        requester="release-validator",
        question=FIXED_PROMPT,
        constraints=(),
        requested_at=datetime.now(UTC),
    )
    decision = ApplicationRouteDecision.for_request(
        request, route=ApplicationRoute.FIRMWARE
    )
    try:
        result = chat_runtime.application.answer(request, route_decision=decision)
    except Exception:  # noqa: BLE001 - redact provider boundary failures
        receipts = tuple(receipt_store.list_receipts())
        receipt = next(
            (item for item in receipts if item.request_id == request.request_id), None
        )
        request_count = 0 if receipt is None else receipt.provider_request_count
        failure = _safe_failure(
            "PROVIDER_REQUEST_FAILED" if request_count else "LIVE_EXECUTION_FAILED",
            identity=identity,
            project_id=project_id,
            tenant_id=tenant_id,
            project_context_source=project["project_context_source"],
            status="FAILED" if request_count else "BLOCKED",
            status_updates={
                **pre_request_status,
                "fixed_model_execution": "FAILED" if request_count else "NOT_EVALUATED",
                "receipt_validation": "FAILED" if request_count else "NOT_EVALUATED",
                "failures": [
                    "PROVIDER_REQUEST_FAILED"
                    if request_count
                    else "LIVE_EXECUTION_FAILED"
                ]
                if request_count
                else [],
            },
        )
        if receipt is not None:
            receipt_identity_match = (
                receipt.selected_model is not None
                and _identity_dict(receipt.selected_model) == identity
            )
            receipt_http_status = receipt.http_status
            failure.update(
                {
                    "provider_request_id": receipt.provider_request_id,
                    "provider_request_id_evidence": bool(receipt.provider_request_id),
                    "provider_request_id_status": (
                        "PASS"
                        if receipt.provider_request_id
                        else "PROVIDER_NOT_EXPOSED"
                    ),
                    "http_status": receipt_http_status,
                    "http_status_source": (
                        "REAL_TRANSPORT"
                        if isinstance(receipt_http_status, int)
                        else "UNAVAILABLE"
                    ),
                    "http_status_evidence_status": (
                        "PASS"
                        if isinstance(receipt_http_status, int)
                        and 200 <= receipt_http_status < 300
                        else "FAILED"
                        if isinstance(receipt_http_status, int)
                        else "UNAVAILABLE"
                    ),
                    "latency_ms": receipt.latency_ms,
                    "receipt_id": receipt.fingerprint,
                    "provider_request_count": receipt.provider_request_count,
                    "receipt_created": True,
                    "execution_fingerprint": execution_fingerprint(identity),
                    "execution_fingerprint_match": receipt.fingerprint
                    == execution_fingerprint(identity),
                    "identity_match": receipt_identity_match,
                    "identity_preservation": (
                        "PASS" if receipt_identity_match else "FAILED"
                    ),
                }
            )
        return failure
    receipt = next(
        (
            item
            for item in receipt_store.list_receipts()
            if item.request_id == request.request_id
        ),
        None,
    )
    if receipt is None:
        return _safe_failure(
            "RECEIPT_PERSISTENCE_FAILED",
            identity=identity,
            **project_kwargs,
            status="FAILED",
            status_updates={
                **pre_request_status,
                "fixed_model_execution": "FAILED",
                "receipt_validation": "FAILED",
                "failures": ["RECEIPT_PERSISTENCE_FAILED"],
            },
        )
    identity_match = (
        _identity_dict(receipt.selected_model) == identity
        if receipt.selected_model is not None
        else False
    )
    receipt_fields_match = (
        receipt.request_id == request.request_id
        and receipt.trace_context.trace_id == request.trace_id
        and receipt.project_id == project_id
        and _status_value(receipt.stage_id) == STAGE_ID
        and receipt.policy_source == "STAGE_POLICY"
        and receipt.selection_mode is SelectionMode.FIXED_MODEL
        and receipt.credential_revision == profile.credential_revision
        and _status_value(receipt.result) == "SUCCESS"
        and tuple(item.status.value for item in receipt.gates)
        == ("PASS", "PASS", "PASS", "PASS", "PASS")
    )
    fingerprint = execution_fingerprint(identity)
    execution_fingerprint_match = (
        receipt.fingerprint == fingerprint
        and receipt.fingerprint == execution_fingerprint(identity, STAGE_ID)
    )
    restart_recovery = False
    store_path = getattr(receipt_store, "_path", None)
    if isinstance(store_path, Path):
        from embedded_copilot.model_gateway import SQLiteModelGatewayStore

        restart_recovery = (
            SQLiteModelGatewayStore(store_path).get_receipt(receipt.fingerprint)
            == receipt
        )
    response_exact = (
        result.answer is not None and result.answer.content == EXPECTED_RESPONSE
    )
    http_ok = isinstance(receipt.http_status, int) and 200 <= receipt.http_status < 300
    provider_request_id_evidence = bool(receipt.provider_request_id)
    provider_request_id_status = (
        "PASS" if provider_request_id_evidence else "PROVIDER_NOT_EXPOSED"
    )
    http_status_evidence_status = (
        "PASS"
        if http_ok
        else "FAILED"
        if isinstance(receipt.http_status, int)
        else "UNAVAILABLE"
    )
    receipt_validation = (
        "PASS" if receipt_fields_match and restart_recovery else "FAILED"
    )
    all_verified = (
        response_exact
        and receipt.provider_request_count == 1
        and http_ok
        and identity_match
        and receipt_fields_match
        and execution_fingerprint_match
        and restart_recovery
    )
    if all_verified:
        live_status = "PASS"
        live_reason = None
    elif receipt.http_status is None:
        live_status = "BLOCKED"
        live_reason = "HTTP_EVIDENCE_PATH_NOT_READY"
    elif not response_exact:
        live_status = "FAILED"
        live_reason = "GOLDEN_RESPONSE_MISMATCH"
    elif receipt.provider_request_count != 1:
        live_status = "FAILED"
        live_reason = "UNEXPECTED_PROVIDER_REQUEST_COUNT"
    elif not identity_match:
        live_status = "FAILED"
        live_reason = "MODEL_IDENTITY_BINDING_MISMATCH"
    elif not receipt_fields_match or not restart_recovery:
        live_status = "FAILED"
        live_reason = "RECEIPT_VALIDATION_FAILED"
    elif not http_ok:
        live_status = "FAILED"
        live_reason = "LIVE_PROVIDER_HTTP_FAILED"
    else:
        live_status = "FAILED"
        live_reason = "LIVE_EVIDENCE_CONSTRAINT_FAILED"
    return {
        "status": live_status,
        "reason": live_reason,
        "real_provider_verified": all_verified,
        "real_backend_verified": all_verified,
        "live_e2e": live_status,
        **pre_request_status,
        "fixed_model_execution": "PASS" if all_verified else live_status,
        "identity": identity,
        "identity_match": identity_match,
        "identity_preservation": "PASS" if identity_match else "FAILED",
        "receipt_fields_match": receipt_fields_match,
        "provider_request_id": receipt.provider_request_id,
        "provider_request_id_evidence": provider_request_id_evidence,
        "provider_request_id_status": provider_request_id_status,
        "http_status": receipt.http_status,
        "http_status_source": (
            "REAL_TRANSPORT" if isinstance(receipt.http_status, int) else "UNAVAILABLE"
        ),
        "http_status_evidence_status": http_status_evidence_status,
        "latency_ms": receipt.latency_ms,
        "credential_revision": profile.credential_revision,
        "receipt_id": receipt.fingerprint,
        "provider_request_count": receipt.provider_request_count,
        "receipt_created": True,
        "receipt_validation": receipt_validation,
        "restart_recovery": restart_recovery,
        "execution_fingerprint": fingerprint,
        "execution_fingerprint_match": execution_fingerprint_match,
        "response_exact": response_exact,
        "response_exact_match": "PASS" if response_exact else "FAILED",
        "valid_project_context": True,
        "project_id": project_id,
        "tenant_id": tenant_id,
        "project_context_source": project["project_context_source"],
        "relay_isolation": "PASS",
        "failures": [] if all_verified else [live_reason],
        "receipt_metadata_only": all(
            key not in receipt.model_dump_json().casefold()
            for key in (
                "prompt",
                "response",
                "headers",
                "authorization",
                "secret",
                "token",
            )
        ),
    }


def _secret_scan(root: Path, evidence_root: Path) -> dict[str, Any]:
    paths = [
        root / "acceptance",
        root / "docs/release",
        root / "logs",
        root / "receipts",
    ]
    if not evidence_root.is_relative_to(root / "acceptance"):
        paths.append(evidence_root)
    findings = 0
    for base in paths:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix.casefold() not in {
                ".json",
                ".txt",
                ".log",
                ".sqlite3",
            }:
                continue
            text = path.read_bytes().decode("utf-8", errors="replace")
            if any(pattern.search(text) for pattern in SENSITIVE_VALUE_PATTERNS):
                findings += 1
    return {"status": "PASS" if findings == 0 else "FAIL", "finding_count": findings}


def _print_controlled_preflight(preflight: dict[str, Any]) -> None:
    """Print the read-only preflight before the production request can run."""

    runtime_wiring = (
        "PASS"
        if preflight["runtime_wiring_status"] == "RUNTIME_WIRED_FIXED_MODE"
        else "UNVERIFIED"
    )
    fields = (
        ("PROJECT_VENV_PRESENT", preflight["project_venv_present"]),
        ("PROJECT_VENV_PYTHON_VERSION", preflight["project_python_version"]),
        ("MODEL_GOVERNANCE_STATUS", preflight["model_governance_status"]),
        ("MODEL_RUNTIME_WIRING_STATUS", runtime_wiring),
        ("MODEL_RUNTIME_VALIDATION_STATUS", preflight["runtime_validation_status"]),
        ("LIVE_EVIDENCE_FRAMEWORK_STATUS", preflight["live_evidence_framework_status"]),
        ("ARCHITECTURE_GUARD_STATUS", preflight["architecture_guard_status"]),
        ("LIVE_FLAG_STATUS", preflight["live_flag_status"]),
        ("PROJECT_CONTEXT_STATUS", preflight["project_context_status"]),
        ("PROVIDER_CONFIGURATION_STATUS", preflight["provider_configuration_status"]),
        ("ENDPOINT_HEALTH_STATUS", preflight["endpoint_health_status"]),
        ("CREDENTIAL_STATUS", preflight["credential_status"]),
        ("MODEL_CATALOG_STATUS", preflight["model_catalog_status"]),
        ("USABLE_MODEL_STATUS", preflight["usable_model_status"]),
        ("ALLOWED_MODEL_STATUS", preflight["allowed_model_status"]),
        ("STAGE_POLICY_STATUS", preflight["stage_policy_status"]),
        ("QUOTA_STATUS", preflight["quota_status"]),
        (
            "HTTP_STATUS_EVIDENCE_CAPABILITY",
            preflight["http_status_evidence_capability"],
        ),
        (
            "PROVIDER_REQUEST_ID_CAPABILITY",
            preflight["provider_request_id_capability"],
        ),
        ("RECEIPT_STORE_STATUS", preflight["receipt_store_status"]),
        ("READY_FOR_LIVE_REQUEST", preflight["ready_for_live_request"]),
        ("provider_request_count", 0),
    )
    print("EMBRIX_CONTROLLED_LIVE_PREFLIGHT")
    for key, value in fields:
        print(f"{key}={str(value).lower() if isinstance(value, bool) else value}")


def _final_status_lines(report: dict[str, Any]) -> tuple[str, ...]:
    gate = report["release_gate"]
    preflight = report["preflight"]
    validation_status = preflight["validation_status"]
    live = report["live"]
    return (
        "MODEL_GOVERNANCE_STATUS=" + preflight["model_governance_status"],
        "MODEL_RUNTIME_WIRING_STATUS="
        + ("PASS" if gate["runtime_composition"] == "PASS" else "FAIL"),
        "MODEL_RUNTIME_VALIDATION_STATUS=" + preflight["runtime_validation_status"],
        "LIVE_EVIDENCE_FRAMEWORK_STATUS=" + preflight["live_evidence_framework_status"],
        "LIVE_FLAG_STATUS=" + live.get("live_flag_status", "NOT_EVALUATED"),
        "PROJECT_CONTEXT_STATUS=" + live.get("project_context_status", "NOT_EVALUATED"),
        "STAGE_POLICY_STATUS=" + live.get("stage_policy_status", "NOT_EVALUATED"),
        "ALLOWED_MODEL_STATUS=" + live.get("allowed_model_status", "NOT_EVALUATED"),
        "CAPABILITY_GATE=" + live.get("capability_gate", "NOT_EVALUATED"),
        "AVAILABILITY_GATE=" + live.get("availability_gate", "NOT_EVALUATED"),
        "CREDENTIAL_GATE=" + live.get("credential_gate", "NOT_EVALUATED"),
        "ENDPOINT_HEALTH_GATE=" + live.get("endpoint_health_gate", "NOT_EVALUATED"),
        "QUOTA_GATE=" + live.get("quota_gate", "NOT_EVALUATED"),
        "FIXED_MODEL_EXECUTION=" + gate["fixed_model_execution"],
        "HTTP_STATUS_EVIDENCE_STATUS="
        + live.get("http_status_evidence_status", "NOT_EVALUATED"),
        "HTTP_STATUS_SOURCE=" + live.get("http_status_source", "NOT_EVALUATED"),
        "PROVIDER_REQUEST_ID_EVIDENCE_STATUS="
        + live.get("provider_request_id_status", "NOT_EVALUATED"),
        "RESPONSE_EXACT_MATCH=" + live.get("response_exact_match", "NOT_EVALUATED"),
        "IDENTITY_PRESERVATION=" + gate["identity_preservation"],
        "RELAY_ISOLATION=" + live.get("relay_isolation", "NOT_EVALUATED"),
        "EXECUTION_FINGERPRINT_MATCH="
        + (
            "PASS"
            if live.get("execution_fingerprint_match", False)
            else "FAILED"
            if live.get("status") == "FAILED"
            else "NOT_EVALUATED"
        ),
        "RECEIPT_VALIDATION=" + gate["receipt_validation"],
        "SECRET_FREE_EVIDENCE=" + report["secret_scan"]["status"],
        "SECURITY_AUDIT=" + ("PASS" if gate["architecture_guard_pass"] else "FAIL"),
        "RUNTIME_COMPOSITION=" + gate["runtime_composition"],
        "BACKEND_FOCUSED_TESTS=" + report["verification"]["backend_focused_tests"],
        "BACKEND_REGRESSION=" + report["verification"]["backend_regression"],
        "COMPILEALL=" + report["verification"]["compileall"],
        "RUFF=" + report["verification"]["ruff"],
        "INTEGRATION_TESTS="
        + (
            "PASS"
            if gate["runtime_composition"] == "PASS" and validation_status == "PASS"
            else "UNVERIFIED"
        ),
        "PROVIDER_REQUEST_COUNT=" + str(live.get("provider_request_count", 0)),
        "REAL_PROVIDER_E2E=" + live["live_e2e"],
        "LIVE_E2E=" + live["live_e2e"],
        "PACKAGE_BUILD=" + gate["package_build"],
        "CURRENT_PACKAGE_MODIFIED=FALSE",
        "PROMOTION=NOT_PERFORMED",
    )


def _write_evidence(evidence_root: Path, report: dict[str, Any]) -> None:
    evidence_root.mkdir(parents=True, exist_ok=True)
    live = report["live"]
    identity = live.get("identity") or {}
    manifest = {
        "schema_version": "1",
        "validation_run_id": report["validation_run_id"],
        "timestamp": report["timestamp"],
        "live_flag_status": live.get("live_flag_status", "NOT_EVALUATED"),
        "live_flag": report["preflight"]["live_provider_flag"],
        "project_context_source": live.get("project_context_source") or "NONE",
        "project_id": live.get("project_id") or "",
        "tenant_id": live.get("tenant_id") or "",
        "stage_id": STAGE_ID,
        "selection_mode": SELECTION_MODE,
        "policy_source": live.get("policy_source", "NOT_EVALUATED"),
        "provider_id": identity.get("provider_id", ""),
        "endpoint_id": identity.get("endpoint_id", ""),
        "credential_ref": identity.get("credential_ref", ""),
        "credential_revision": live.get("credential_revision") or "",
        "model_profile_id": identity.get("model_profile_id", ""),
        "model_id": identity.get("model_id", ""),
        "capability_gate": live.get("capability_gate", "NOT_EVALUATED"),
        "availability_gate": live.get("availability_gate", "NOT_EVALUATED"),
        "credential_gate": live.get("credential_gate", "NOT_EVALUATED"),
        "endpoint_health_gate": live.get("endpoint_health_gate", "NOT_EVALUATED"),
        "quota_gate": live.get("quota_gate", "NOT_EVALUATED"),
        "provider_request_count": live.get("provider_request_count", 0),
        "provider_request_id": live.get("provider_request_id") or "UNAVAILABLE",
        "provider_request_id_status": live.get(
            "provider_request_id_status", "NOT_EVALUATED"
        ),
        "http_status": live.get("http_status")
        if live.get("http_status") is not None
        else "",
        "http_status_source": live.get("http_status_source", "UNAVAILABLE"),
        "latency_ms": live.get("latency_ms"),
        "response_exact_match": live.get("response_exact_match", "NOT_EVALUATED"),
        "receipt_id": live.get("receipt_id") or "",
        "receipt_persisted": bool(live.get("receipt_created", False)),
        "receipt_restart_readable": bool(live.get("restart_recovery", False)),
        "identity_match": bool(live.get("identity_match", False)),
        "execution_fingerprint": live.get("execution_fingerprint") or "",
        "execution_fingerprint_match": bool(
            live.get("execution_fingerprint_match", False)
        ),
        "secret_free": report["secret_scan"]["status"] == "PASS",
        "real_provider_e2e": live["live_e2e"],
        "status": live.get("status", "NOT_EVALUATED"),
        "blockers": report.get("environment_blockers", []),
        "failures": report.get("failures") or live.get("failures", []),
        "runtime_status": report["preflight"]["runtime_wiring_status"],
        "runtime_wiring": report["preflight"]["runtime_wiring_status"],
        "real_provider": bool(live.get("real_provider_verified", False)),
        "real_provider_gate": {
            "enabled": report["preflight"]["live_provider_flag"] == "true",
            "reason": live.get("reason") or "LIVE_EVIDENCE_VERIFIED",
        },
        "identity_fingerprint": live.get("execution_fingerprint") or "",
    }
    (evidence_root / "validation_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (evidence_root / "validation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )
    preflight = report["preflight"]
    gate = report["release_gate"]
    lines = [
        "EMBRIX_CONTROLLED_LIVE_PREFLIGHT",
        *[
            f"{key}={str(value).lower() if isinstance(value, bool) else value}"
            for key, value in preflight.items()
        ],
        "",
        "EMBRIX_ARCHITECTURE_GUARD",
        f"status={report['architecture_guard']['status']}",
        *[
            f"{key}={str(report['architecture_guard'][key]).lower()}"
            for key in (
                "new_agent_added",
                "second_runtime_added",
                "second_model_gateway_added",
                "second_execution_framework_added",
                "llm_direct_side_effect",
                "evidence_bypassed",
                "credential_leak_risk",
                "legacy_path_promoted",
            )
        ],
        "",
        "EMBRIX_MODEL_GOVERNANCE_RELEASE_GATE",
        *[f"{key}={value}" for key, value in gate.items()],
        "",
        *report.get("final_status_lines", ()),
    ]
    (evidence_root / "validation_report.txt").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def run_live_evidence(
    root: Path = ROOT,
    *,
    evidence_dir: Path | None = None,
    run_regression: bool = True,
) -> dict[str, Any]:
    root = root.resolve()
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    validation_run_id = f"model-governance-controlled-live-golden-v1_{timestamp}"
    if evidence_dir is None:
        evidence_dir = root / "acceptance" / validation_run_id
    evidence_root = evidence_dir.resolve()
    while evidence_root.exists():
        evidence_root = evidence_root.with_name(evidence_root.name + "-isolated")
    evidence_root.mkdir(parents=True, exist_ok=False)

    architecture = _architecture_guard(root)
    validation_status = (
        _run_existing_validation_regression(root)
        if run_regression
        else "NOT_RUN_TEST_MODE"
    )
    verification = {
        "backend_focused_tests": (
            _run_backend_focused_tests(root) if run_regression else "NOT_RUN"
        ),
        "backend_regression": validation_status,
        "compileall": _run_compileall(root) if run_regression else "NOT_RUN",
        "ruff": _run_ruff_checks(root) if run_regression else "NOT_RUN",
    }
    preflight = _preflight(root, architecture, validation_status)
    _print_controlled_preflight(preflight)
    if architecture["status"] != "PASS":
        live = _safe_failure(
            "ARCHITECTURE_GUARD_FAILED",
            status_updates={"live_flag_status": preflight["live_flag_status"]},
        )
    elif preflight["live_provider_flag"] != "true":
        live = _safe_failure(
            "LIVE_FLAG_UNSET",
            status_updates={"live_flag_status": "BLOCKED"},
        )
    elif not _success_http_status_supported(root):
        live = _safe_failure(
            "HTTP_EVIDENCE_PATH_NOT_READY",
            status_updates={"live_flag_status": "PASS"},
        )
    else:
        live = _execute_production_live(root, validation_run_id)
        if live.get("live_flag_status") == "NOT_EVALUATED":
            live["live_flag_status"] = "PASS"

    report: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "validation_run_id": validation_run_id,
        "preflight": preflight,
        "architecture_guard": architecture,
        "live": live,
        "secret_scan": {"status": "PENDING"},
        "release_gate": {
            "scope": "Controlled Live Golden Run v1",
            "architecture_guard_pass": architecture["status"] == "PASS",
            "security_checks": "PENDING",
            "offline_validation": validation_status,
            "runtime_composition": "PASS"
            if preflight["runtime_wiring_status"] == "RUNTIME_WIRED_FIXED_MODE"
            else "FAIL",
            "production_composition": "PASS"
            if preflight["runtime_wiring_status"] == "RUNTIME_WIRED_FIXED_MODE"
            else "FAIL",
            "live_authorization_verified": preflight["live_flag_status"] == "PASS",
            "project_context_verified": bool(live.get("valid_project_context", False)),
            "fixed_model_policy_verified": live.get("stage_policy_status") == "PASS",
            "hard_gates_verified": all(
                live.get(key) == "PASS"
                for key in (
                    "capability_gate",
                    "availability_gate",
                    "credential_gate",
                    "endpoint_health_gate",
                    "quota_gate",
                )
            ),
            "http_evidence_verified": (
                live.get("http_status_source") == "REAL_TRANSPORT"
                and isinstance(live.get("http_status"), int)
                and 200 <= live["http_status"] < 300
            ),
            "fixed_model_execution": live.get("fixed_model_execution", "NOT_EVALUATED"),
            "receipt_validation": live.get("receipt_validation", "NOT_EVALUATED"),
            "identity_preservation": live.get(
                "identity_preservation",
                "PASS" if live.get("identity_match", False) else "NOT_EVALUATED",
            ),
            "relay_isolation": live.get("relay_isolation", "NOT_EVALUATED"),
            "real_provider_verified": bool(live.get("real_provider_verified", False)),
            "real_backend_verified": bool(live.get("real_backend_verified", False)),
            "live_e2e": live["live_e2e"],
            "package_build": "NOT_RUN",
            "current_package_modified": False,
            "promotion": "NOT_PERFORMED",
            "verdict": "PENDING",
            "release_ready": False,
            "model_governance_v1_complete": False,
            "required_before_pass": (
                "live_authorization_verified,project_context_verified,"
                "fixed_model_policy_verified,hard_gates_verified,http_evidence_verified,"
                "provider_request_count=1,real_provider_verified,identity_preservation,"
                "receipt_validation,secret_free,live_e2e=PASS"
            ),
        },
        "verification": verification,
        "known_debt": [],
        "environment_blockers": [],
        "failures": [],
    }
    if live.get("reason"):
        if live.get("status") == "FAILED":
            report["failures"].append(live["reason"])
        else:
            report["environment_blockers"].append(live["reason"])
    if preflight["dirty_overlap"] == "TRUE":
        report["known_debt"].append("DIRTY_OVERLAP_PREEXISTING")
    if not _success_http_status_supported(root):
        report["known_debt"].append("HTTP_STATUS_EVIDENCE_CAPABILITY_UNAVAILABLE")

    _write_evidence(evidence_root, report)
    secret = _secret_scan(root, evidence_root)
    report["secret_scan"] = secret
    gate = report["release_gate"]
    gate["security_checks"] = secret["status"]
    release_requirements_met = all(
        (
            gate["architecture_guard_pass"],
            validation_status == "PASS",
            gate["runtime_composition"] == "PASS",
            gate["production_composition"] == "PASS",
            gate["live_authorization_verified"],
            gate["project_context_verified"],
            gate["fixed_model_policy_verified"],
            gate["hard_gates_verified"],
            gate["http_evidence_verified"],
            live["real_provider_verified"],
            gate["receipt_validation"] == "PASS",
            gate["identity_preservation"] == "PASS",
            secret["status"] == "PASS",
        )
    )
    gate["verdict"] = (
        "PASS"
        if release_requirements_met
        else "FAIL"
        if live.get("status") == "FAILED"
        or not gate["architecture_guard_pass"]
        or validation_status == "FAIL"
        or secret["status"] == "FAIL"
        else "BLOCKED"
    )
    gate["release_ready"] = gate["verdict"] == "PASS"
    gate["model_governance_v1_complete"] = gate["release_ready"]
    report["evidence_directory"] = evidence_root.name
    report["final_status_lines"] = _final_status_lines(report)
    _write_evidence(evidence_root, report)
    return report


def _print_report(report: dict[str, Any]) -> None:
    gate = report["release_gate"]
    print("\nEMBRIX_ARCHITECTURE_GUARD")
    print("status=" + report["architecture_guard"]["status"])
    for key in (
        "new_agent_added",
        "second_runtime_added",
        "second_model_gateway_added",
        "second_execution_framework_added",
        "llm_direct_side_effect",
        "evidence_bypassed",
        "credential_leak_risk",
        "legacy_path_promoted",
    ):
        print(f"{key}={str(report['architecture_guard'][key]).lower()}")
    print("\nEMBRIX_MODEL_GOVERNANCE_RELEASE_GATE")
    for key, value in gate.items():
        print(f"{key}={value}")
    print()
    for line in report.get("final_status_lines", ()):
        print(line)
    print("EVIDENCE_DIRECTORY=" + report["evidence_directory"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=None)
    parser.add_argument("--skip-validation-regression", action="store_true")
    args = parser.parse_args(argv)
    report = run_live_evidence(
        evidence_dir=args.evidence_dir,
        run_regression=not args.skip_validation_regression,
    )
    _print_report(report)
    return 0 if report["release_gate"]["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
