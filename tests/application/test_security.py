from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2] / "src" / "embedded_copilot" / "application"

FORBIDDEN_IMPORTS = (
    "embedded_copilot.api",
    "embedded_copilot.execution",
    "embedded_copilot.engineering_repair",
    "embedded_copilot.workspace_runtime",
    "embedded_copilot.hardware",
    "embedded_copilot.device_runtime",
    "embedded_copilot.tool_runtime",
    "embedded_copilot.tools",
    "embedded_copilot.agents.workflow",
    "langgraph",
    "pathlib",
    "os",
    "subprocess",
    "socket",
    "httpx",
    "requests",
    "serial",
    "git",
)
FORBIDDEN_CALLS = {
    "open",
    "eval",
    "exec",
    "compile",
    "system",
    "popen",
    "approve",
    "apply",
    "build",
    "flash",
    "execute",
    "write_text",
    "write_bytes",
    "unlink",
    "remove",
    "delete",
}


def _trees():
    assert ROOT.is_dir()
    for path in ROOT.glob("**/*.py"):
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def _forbidden_import(name: str) -> bool:
    return any(
        name == forbidden or name.startswith(f"{forbidden}.")
        for forbidden in FORBIDDEN_IMPORTS
    )


def test_execution_import_rule_keeps_legacy_and_worker_packages_distinct() -> None:
    assert _forbidden_import("embedded_copilot.execution")
    assert _forbidden_import("embedded_copilot.execution.service")
    assert not _forbidden_import("embedded_copilot.execution_worker.contracts")


def test_application_has_no_legacy_execution_infrastructure_or_workflow_dependencies() -> None:
    for path, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name.casefold() for alias in node.names]
                if isinstance(node, ast.ImportFrom) and node.module:
                    names.append(node.module.casefold())
                assert not any(_forbidden_import(name) for name in names), path
            if isinstance(node, ast.Call):
                called = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else node.func.id
                    if isinstance(node.func, ast.Name)
                    else ""
                )
                assert called.casefold() not in FORBIDDEN_CALLS, path


def test_only_supervisor_classifier_is_imported_from_agents() -> None:
    for path, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "embedded_copilot.agents"
            ):
                assert node.module == "embedded_copilot.agents.supervisor", path
                assert {alias.name for alias in node.names} <= {"classify_intent"}, path


def test_application_audit_does_not_store_raw_or_sensitive_payload_fields() -> None:
    tree = ast.parse((ROOT / "models.py").read_text(encoding="utf-8"))
    audit = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ApplicationAuditEvent"
    )
    fields = {
        node.target.id.casefold()
        for node in audit.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert not fields.intersection(
        {
            "question",
            "answer",
            "prompt",
            "raw_payload",
            "credential",
            "api_key",
            "path",
            "command",
        }
    )
