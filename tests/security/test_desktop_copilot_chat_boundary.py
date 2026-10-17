from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = (
    Path(__file__).parents[2] / "src" / "embedded_copilot" / "desktop_copilot_chat"
)

FORBIDDEN_IMPORT_PARTS = {
    "agent",
    "build",
    "capability_router",
    "execution",
    "filesystem",
    "flash",
    "git",
    "hardware",
    "httpx",
    "langgraph",
    "network",
    "os",
    "pathlib",
    "promotion",
    "repair",
    "requests",
    "shell",
    "socket",
    "subprocess",
    "tool",
    "workflow",
}

FORBIDDEN_CALLS = {
    "apply",
    "build",
    "execute",
    "flash",
    "invoke",
    "promote",
    "repair",
}


def test_chat_context_has_no_external_or_agent_dependencies() -> None:
    files = tuple(PACKAGE.rglob("*.py"))
    assert files
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = (node.module,)
            else:
                modules = ()
            for module in modules:
                assert not set(module.casefold().split(".")).intersection(
                    FORBIDDEN_IMPORT_PARTS
                ), (path, module)


def test_chat_context_exposes_no_engineering_control_calls() -> None:
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.name.casefold() not in FORBIDDEN_CALLS, (path, node.name)
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    name = node.func.attr.casefold()
                elif isinstance(node.func, ast.Name):
                    name = node.func.id.casefold()
                else:
                    name = ""
                assert name not in FORBIDDEN_CALLS, (path, name)
