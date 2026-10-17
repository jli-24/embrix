from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path("src/embedded_copilot/engineering_review")

ALLOWED_TARGET_IMPORTS = {
    "embedded_copilot.engineering_governance": {"ApprovedKnowledge"},
    "embedded_copilot.engineering_validation": {"ValidationFinding"},
    "embedded_copilot.engineering_optimization_loop": {"OptimizationProposal"},
    "embedded_copilot.engineering_evolution": {"KnowledgeEvolutionProposal"},
}

FORBIDDEN_IMPORT_PARTS = {
    "agents",
    "autonomous",
    "execution",
    "httpx",
    "langchain",
    "langgraph",
    "model_gateway",
    "openai",
    "requests",
    "runtime",
    "socket",
    "subprocess",
    "tool",
    "urllib",
    "workflow",
}

FORBIDDEN_CALLS = {
    "apply",
    "build",
    "execute",
    "flash",
    "invoke",
    "open",
    "popen",
    "run",
    "system",
    "write",
    "write_bytes",
    "write_text",
}


def _trees() -> tuple[tuple[Path, ast.AST], ...]:
    files = tuple(sorted(PACKAGE.rglob("*.py")))
    assert files, "engineering_review package is missing"
    return tuple(
        (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in files
    )


def _called_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr.casefold()
    if isinstance(node.func, ast.Name):
        return node.func.id.casefold()
    return ""


def test_review_has_no_agent_model_workflow_runtime_or_execution_dependencies() -> None:
    for path, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = (node.module,)
            else:
                modules = ()
            for module in modules:
                parts = {part.casefold() for part in module.split(".")}
                assert not parts.intersection(FORBIDDEN_IMPORT_PARTS), (path, module)

            if isinstance(node, ast.Call):
                assert _called_name(node) not in FORBIDDEN_CALLS, path


def test_review_only_imports_the_four_approved_target_dtos() -> None:
    for path, tree in _trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if not node.module.startswith("embedded_copilot."):
                continue
            assert node.module in ALLOWED_TARGET_IMPORTS, (path, node.module)
            imported = {alias.name for alias in node.names}
            assert imported <= ALLOWED_TARGET_IMPORTS[node.module], (path, imported)


def test_review_exposes_no_apply_receipt_auto_decision_or_target_mutation_api() -> None:
    forbidden_name_parts = (
        "apply",
        "autoreview",
        "autodecision",
        "automaticdecision",
        "build",
        "execute",
        "flash",
        "mutatetarget",
        "updatetarget",
        "writetarget",
    )
    for path, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                compact = node.name.replace("_", "").casefold()
                assert not any(part in compact for part in forbidden_name_parts), (
                    path,
                    node.name,
                )
            if isinstance(node, ast.ClassDef):
                assert node.name != "AuditReceipt", path

