from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path("src/embedded_copilot/engineering_governance")
LEGACY_PACKAGE = Path("src/embedded_copilot/knowledge_governance")


def _trees():
    return tuple(
        (path, ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(PACKAGE.rglob("*.py"))
    )


def test_governance_has_no_external_or_execution_dependencies() -> None:
    forbidden = {
        "engineering_memory",
        "knowledge_graph",
        "agents",
        "langgraph",
        "workflow",
        "runtime",
        "subprocess",
        "socket",
        "requests",
        "urllib",
        "httpx",
        "openai",
        "anthropic",
        "boto3",
        "os",
        "pathlib",
    }
    for path, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = tuple(alias.name.casefold() for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = (node.module.casefold(),)
            else:
                continue
            assert not any(
                token in module for module in modules for token in forbidden
            ), path


def test_governance_has_no_mutation_or_automatic_approval_calls() -> None:
    forbidden = {
        "write",
        "update",
        "delete",
        "execute",
        "invoke",
        "run",
        "flash",
        "build",
        "mutate",
        "add_node",
        "add_relation",
    }
    for path, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else node.func.id
                    if isinstance(node.func, ast.Name)
                    else None
                )
                assert name not in forbidden, path


def test_governance_exposes_only_candidate_review_approved_and_sink_boundaries() -> (
    None
):
    forbidden_names = {"MemoryAgent", "GraphAgent", "ExecutionAgent", "Workflow"}
    for path, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert node.name not in forbidden_names, path
                assert not any(
                    token in node.name.casefold()
                    for token in ("agent", "runtime", "workflow")
                ), path


def test_existing_governance_keeps_its_runtime_and_mutation_boundaries() -> None:
    forbidden_imports = {
        "agents",
        "langgraph",
        "runtime",
        "workflow",
        "engineering_memory",
        "build",
        "flash",
        "device",
        "tool",
        "openai",
        "subprocess",
        "socket",
        "requests",
        "urllib",
    }
    forbidden_calls = {
        "write",
        "update",
        "delete",
        "promote",
        "execute",
        "run",
        "invoke",
        "create_record",
        "create_candidate",
        "apply_human_approval",
    }
    for path in LEGACY_PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = tuple(alias.name.casefold() for alias in node.names)
                assert not any(
                    token in module for module in modules for token in forbidden_imports
                ), path
            elif isinstance(node, ast.ImportFrom) and node.module:
                module = node.module.casefold()
                assert not any(token in module for token in forbidden_imports), path
            elif isinstance(node, ast.Call):
                name = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else node.func.id
                    if isinstance(node.func, ast.Name)
                    else None
                )
                assert name not in forbidden_calls, path
