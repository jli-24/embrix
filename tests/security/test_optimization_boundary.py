from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "embedded_copilot"
    / "engineering_optimization_loop"
)

FORBIDDEN_IMPORT_PARTS = {
    "agent",
    "autonomous",
    "execution",
    "flash",
    "http",
    "langchain",
    "langgraph",
    "memory",
    "model_gateway",
    "openai",
    "requests",
    "runtime",
    "shell",
    "subprocess",
    "tool",
    "workflow",
}

FORBIDDEN_PUBLIC_PARTS = {
    "apply",
    "approval",
    "approve",
    "build",
    "execute",
    "flash",
    "fix",
    "mutation",
    "reject",
    "repair",
    "review",
    "write",
}

FORBIDDEN_FUNCTION_PREFIXES = (
    "apply_",
    "approve_",
    "build_",
    "execute_",
    "flash_",
    "fix_",
    "reject_",
    "repair_",
    "review_",
    "write_",
)


def dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def test_optimization_loop_has_no_execution_or_human_decision_capability() -> None:
    python_files = tuple(sorted(PACKAGE.rglob("*.py")))
    assert python_files, "engineering_optimization_loop package is missing"

    for path in python_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported = (node.module or "",)
            else:
                imported = ()
            for name in imported:
                parts = {part.lower() for part in name.split(".")}
                assert not parts.intersection(FORBIDDEN_IMPORT_PARTS), (
                    path,
                    name,
                )

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lowered = node.name.lower()
                if not lowered.startswith("validate_"):
                    assert not lowered.startswith(FORBIDDEN_FUNCTION_PREFIXES), (
                        path,
                        node.name,
                    )

            if isinstance(node, ast.Call):
                called = dotted_name(node.func).lower()
                assert not any(
                    fragment in called
                    for fragment in (
                        "open",
                        "path.write",
                        "subprocess",
                        "system",
                        "popen",
                    )
                ), (path, called)


def test_public_package_does_not_export_adapters_or_decision_interfaces() -> None:
    tree = ast.parse((PACKAGE / "__init__.py").read_text(encoding="utf-8"))
    exported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    exported = {
                        item.value
                        for item in node.value.elts  # type: ignore[union-attr]
                        if isinstance(item, ast.Constant)
                        and isinstance(item.value, str)
                    }

    assert exported
    assert not any("Fake" in name or "Adapter" in name for name in exported)
    decision_terms = ("ReviewRequest", "ApprovalRequest", "ApplyRequest")
    assert not any(term in name for name in exported for term in decision_terms)
