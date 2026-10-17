from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path("src/embedded_copilot/engineering_context")


def _trees():
    return tuple(
        (path, ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(PACKAGE.rglob("*.py"))
    )


def test_context_modules_do_not_import_agent_runtime_or_execution_capabilities() -> None:
    forbidden = {
        "agents", "langgraph", "runtime", "workflow", "tool", "build", "flash",
        "device", "subprocess", "socket", "requests", "urllib", "openai",
        "anthropic", "sqlite3", "sqlalchemy", "embedding",
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
                token in module
                for module in modules
                for token in forbidden
            ), path


def test_context_modules_do_not_expose_mutation_or_execution_calls() -> None:
    forbidden = {
        "write", "update", "delete", "promote", "execute", "invoke", "run",
        "flash", "build", "create_node", "update_relation", "mutate",
    }
    for path, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                function_name = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else node.func.id
                    if isinstance(node.func, ast.Name)
                    else None
                )
                assert function_name not in forbidden, path


def test_context_modules_have_no_agent_or_runtime_types() -> None:
    forbidden_names = {
        "BaseAgent", "ContextAgent", "MemoryAgent", "GraphAgent", "Workflow"
    }
    for path, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert node.name not in forbidden_names, path
                assert not any(
                    token in node.name.casefold()
                    for token in ("agent", "runtime", "workflow")
                ), path
                assert not any(
                    isinstance(base, ast.Name) and base.id in forbidden_names
                    for base in node.bases
                ), path
