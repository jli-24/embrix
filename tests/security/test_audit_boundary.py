from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path("src/embedded_copilot/engineering_audit")

FORBIDDEN_IMPORT_PARTS = {
    "agents",
    "database",
    "execution",
    "httpx",
    "langchain",
    "langgraph",
    "model_gateway",
    "openai",
    "pathlib",
    "requests",
    "runtime",
    "socket",
    "subprocess",
    "urllib",
    "workflow",
}

FORBIDDEN_API_NAMES = {
    "delete",
    "execute",
    "overwrite",
    "remove",
    "replace_events",
    "update",
    "update_event",
}


def _trees() -> tuple[tuple[Path, ast.AST], ...]:
    files = tuple(sorted(PACKAGE.rglob("*.py")))
    assert files, "engineering_audit package is missing"
    return tuple(
        (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in files
    )


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr.casefold()
    if isinstance(node.func, ast.Name):
        return node.func.id.casefold()
    return ""


def test_audit_has_no_agent_model_execution_network_or_file_dependencies() -> None:
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


def test_audit_exposes_no_delete_update_overwrite_or_remove_api() -> None:
    for path, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.name.casefold() not in FORBIDDEN_API_NAMES, (path, node.name)
            if isinstance(node, ast.Call):
                assert _call_name(node) not in FORBIDDEN_API_NAMES, path


def test_audit_store_port_only_exposes_append_and_get_trail() -> None:
    tree = ast.parse((PACKAGE / "ports.py").read_text(encoding="utf-8"))
    store = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "AuditStorePort"
    )
    methods = {
        node.name
        for node in store.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert methods == {"append", "get_trail"}


def test_only_v38_review_record_is_imported_as_external_business_contract() -> None:
    allowed = {
        "embedded_copilot.engineering_review": {"ReviewRecord"},
    }
    for path, tree in _trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if not node.module.startswith("embedded_copilot."):
                continue
            assert node.module in allowed, (path, node.module)
            assert {alias.name for alias in node.names} <= allowed[node.module]


def test_public_package_exposes_no_fake_agent_execution_or_receipt_contract() -> None:
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
    forbidden = ("Fake", "Agent", "Execution", "Executor", "Receipt")
    assert not any(part in name for name in exported for part in forbidden)

