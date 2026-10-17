from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path("src/embedded_copilot/engineering_evolution")


def _trees():
    return tuple(
        (path, ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(PACKAGE.rglob("*.py"))
    )


def test_engineering_evolution_has_no_external_or_mutation_dependencies() -> None:
    forbidden = {
        "engineering_memory",
        "engineering_knowledge",
        "knowledge_evolution",
        "engineering_context",
        "model_gateway",
        "execution",
        "agents",
        "langgraph",
        "workflow",
        "subprocess",
        "socket",
        "requests",
        "urllib",
        "httpx",
        "openai",
        "anthropic",
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


def test_engineering_evolution_exposes_no_real_sink_or_execution_calls() -> None:
    forbidden_calls = {
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
        "upsert",
        "index",
        "embed",
    }
    forbidden_classes = {"MemoryAgent", "GraphAgent", "RetrievalAgent", "Workflow"}
    for path, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else node.func.id if isinstance(node.func, ast.Name) else None
                )
                assert name not in forbidden_calls, path
            elif isinstance(node, ast.ClassDef):
                assert node.name not in forbidden_classes, path
                assert not any(
                    token in node.name.casefold()
                    for token in ("agent", "workflow", "runtime")
                ), path


def test_only_engineering_governance_contract_is_allowed_upstream() -> None:
    for path, tree in _trees():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "embedded_copilot.engineering_governance"
            ):
                imported = {alias.name for alias in node.names}
                assert imported <= {"ApprovedKnowledge"}, path
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module == "embedded_copilot.engineering_governance.contracts"
            ):
                imported = {alias.name for alias in node.names}
                assert imported <= {"validate_approved_knowledge"}, path
