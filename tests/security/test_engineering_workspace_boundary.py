from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2] / "src" / "embedded_copilot" / "engineering_workspace"


def test_workspace_core_has_no_execution_or_external_io_dependencies() -> None:
    forbidden = {
        "os",
        "pathlib",
        "subprocess",
        "socket",
        "httpx",
        "requests",
        "sqlite3",
        "langgraph",
        "git",
    }
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert not imports & forbidden, path.name


def test_workspace_does_not_import_existing_domain_services() -> None:
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        ]
        assert not any(
            module.startswith("embedded_copilot.")
            and not module.startswith("embedded_copilot.engineering_workspace")
            for module in imported
        ), path.name


def test_workspace_service_has_no_high_impact_api() -> None:
    path = ROOT / "service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    methods = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert not methods & {
        "apply",
        "build",
        "flash",
        "execute",
        "promote",
        "approve",
        "decide",
        "write",
    }
