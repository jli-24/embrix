from __future__ import annotations

import ast
import inspect
from pathlib import Path

from embedded_copilot.engineering_repair import (
    PatchProposal,
    RepairAuditSinkPort,
    RepairService,
)

ROOT = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "embedded_copilot"
    / "engineering_repair"
)

FORBIDDEN_IMPORTS = {
    "agents",
    "execution",
    "verification_loop",
    "hardware",
    "langgraph",
    "langchain",
    "openai",
    "anthropic",
    "subprocess",
    "socket",
    "requests",
    "httpx",
    "urllib",
    "pathlib",
    "os",
    "git",
    "sqlite3",
}

FORBIDDEN_CALLS = {
    "open",
    "exec",
    "eval",
    "compile",
    "__import__",
    "system",
    "popen",
    "run",
    "remove",
    "unlink",
    "write_text",
    "write_bytes",
}


def _trees():
    for path in sorted(ROOT.rglob("*.py")):
        yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_repair_package_has_no_forbidden_runtime_dependencies() -> None:
    violations: list[str] = []
    for path, tree in _trees():
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = (node.module,)
            for module in modules:
                segments = set(module.split("."))
                blocked = segments & FORBIDDEN_IMPORTS
                if blocked:
                    violations.append(f"{path.name}:{node.lineno}:{sorted(blocked)}")
    assert violations == []


def test_repair_package_has_no_file_shell_network_or_mutation_calls() -> None:
    violations: list[str] = []
    for path, tree in _trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if (
                name
                and name.lower() in FORBIDDEN_CALLS
                and not (
                    name == "compile" and isinstance(node.func, ast.Attribute)
                )
            ):
                violations.append(f"{path.name}:{node.lineno}:{name}")
    assert violations == []


def test_patch_contract_cannot_carry_executable_repair_content() -> None:
    forbidden = {
        "source",
        "source_code",
        "code",
        "diff",
        "unified_diff",
        "path",
        "file_path",
        "command",
        "operations",
        "payload",
        "executor",
        "stdout",
        "stderr",
        "credential",
    }
    assert forbidden.isdisjoint(PatchProposal.model_fields)


def test_service_does_not_own_human_decision_or_automatic_repair_api() -> None:
    public = {
        name
        for name, value in inspect.getmembers(RepairService, inspect.isfunction)
        if not name.startswith("_")
    }
    assert public == {
        "apply",
        "authorize_apply",
        "prepare",
        "submit_review",
        "verify",
    }
    assert {"approve", "decide", "auto_repair", "retry_repair"}.isdisjoint(public)


def test_repair_audit_store_surface_is_append_only() -> None:
    public = {
        name
        for name, value in inspect.getmembers(
            RepairAuditSinkPort, inspect.isfunction
        )
        if not name.startswith("_")
    }
    assert public == {"append"}
