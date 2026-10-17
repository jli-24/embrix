from __future__ import annotations

import ast
from pathlib import Path

from embedded_copilot.agent_runtime import (
    CapabilityAuditEvent,
    CapabilityRegistry,
    CapabilityRouter,
)

PACKAGE = Path(__file__).parents[2] / "src" / "embedded_copilot" / "agent_runtime"


def modules() -> tuple[tuple[Path, ast.AST], ...]:
    return tuple(
        (path, ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(PACKAGE.rglob("*.py"))
    )


def imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.casefold() for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.casefold())
    return names


def test_package_has_no_agent_workflow_tool_or_model_gateway_imports() -> None:
    forbidden = (
        "langgraph",
        "agents",
        "supervisor",
        "workflow",
        "model_gateway",
        "tool_runtime",
        "tool_adapter",
    )
    for path, tree in modules():
        imports = imported_names(tree)
        assert not any(token in name for token in forbidden for name in imports), path


def test_package_has_no_external_io_or_execution_imports() -> None:
    forbidden = (
        "subprocess",
        "socket",
        "requests",
        "httpx",
        "urllib",
        "pathlib",
        "shutil",
        "os",
        "execution",
        "verification",
        "hardware",
        "database",
    )
    for path, tree in modules():
        imports = imported_names(tree)
        assert not any(
            name == token or name.startswith(f"{token}.")
            for token in forbidden
            for name in imports
        ), path


def test_package_has_no_dangerous_calls_or_high_impact_methods() -> None:
    forbidden_names = {
        "open",
        "eval",
        "exec",
        "compile",
        "system",
        "popen",
        "run",
        "call",
        "check_call",
        "check_output",
    }
    forbidden_attributes = {
        "apply",
        "approve",
        "authorize_apply",
        "build",
        "flash",
        "prepare",
        "submit_review",
        "verify",
        "write_text",
        "write_bytes",
        "unlink",
        "remove",
        "delete",
        "update",
    }
    for path, tree in modules():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden_names, path
            if isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_attributes, path


def test_repair_boundary_imports_only_request_and_analysis_port() -> None:
    allowed = {"RepairRequest", "RepairAnalysisPort"}
    for path, tree in modules():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "embedded_copilot.engineering_repair"
            ):
                assert {alias.name for alias in node.names} <= allowed, path


def test_router_exposes_no_agent_loop_or_execution_api() -> None:
    public = {name for name in dir(CapabilityRouter) if not name.startswith("_")}
    assert public == {"ainvoke", "invoke"}


def test_registry_is_immutable_after_construction() -> None:
    public = {name for name in dir(CapabilityRegistry) if not name.startswith("_")}
    assert public == {"resolve"}


def test_audit_schema_contains_only_identity_metadata() -> None:
    fields = set(CapabilityAuditEvent.model_fields)
    forbidden = {
        "question",
        "answer",
        "observation",
        "proposal",
        "prompt",
        "provider_payload",
        "api_key",
        "secret",
        "path",
    }
    assert fields.isdisjoint(forbidden)
