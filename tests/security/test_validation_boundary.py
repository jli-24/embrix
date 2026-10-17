from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path("src/embedded_copilot/engineering_validation")


def _trees() -> tuple[tuple[Path, ast.AST], ...]:
    return tuple(
        (path, ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(PACKAGE.rglob("*.py"))
    )


def test_validation_has_no_external_mutation_or_execution_dependencies() -> None:
    forbidden = {
        "engineering_memory",
        "knowledge_graph",
        "knowledge_evolution",
        "engineering_governance",
        "model_gateway",
        "execution",
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
        "shlex",
        "sys",
        "tempfile",
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


def test_validation_only_reads_engineering_context_snapshot_contract() -> None:
    imports: list[tuple[Path, str, set[str]]] = []
    for path, tree in _trees():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and node.module.startswith("embedded_copilot.engineering_context")
            ):
                imports.append(
                    (path, node.module, {alias.name for alias in node.names})
                )

    assert imports
    for path, module, names in imports:
        assert module == "embedded_copilot.engineering_context.contracts", path
        assert names <= {"EngineeringContextSnapshot"}, path


def test_validation_exposes_no_file_shell_build_flash_or_repair_calls() -> None:
    forbidden_calls = {
        "open",
        "write",
        "write_text",
        "write_bytes",
        "unlink",
        "mkdir",
        "remove",
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
        "repair",
        "apply_fix",
    }
    for path, tree in _trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id if isinstance(node.func, ast.Name) else None
            )
            assert name not in forbidden_calls, path


def test_validation_has_no_agent_runtime_workflow_or_human_decision_api() -> None:
    forbidden_class_tokens = (
        "agent",
        "runtime",
        "workflow",
        "execution",
        "repair",
        "decision",
    )
    forbidden_methods = {
        "confirm",
        "confirm_finding",
        "reject_finding",
        "decide",
        "apply_decision",
        "approve",
    }
    for path, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                assert not any(
                    token in node.name.casefold() for token in forbidden_class_tokens
                ), path
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.name not in forbidden_methods, path


def test_validation_models_do_not_expose_content_or_automatic_fix_fields() -> None:
    forbidden_fields = {
        "content",
        "binary",
        "raw_log",
        "raw_model_output",
        "recommendation",
        "suggested_change",
        "repair",
        "fix",
        "command",
    }
    for path, tree in _trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.AnnAssign) or not isinstance(
                node.target, ast.Name
            ):
                continue
            assert node.target.id not in forbidden_fields, path
