from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EDA = ROOT / "src" / "embedded_copilot" / "engineering_eda"
HARDWARE = ROOT / "src" / "embedded_copilot" / "hardware_runtime"
ROUTER_BINDINGS = ROOT / "src" / "embedded_copilot" / "agent_runtime" / "bindings.py"


def _trees(root: Path):
    for path in root.rglob("*.py"):
        yield path, ast.parse(path.read_text(encoding="utf-8"))


def test_core_packages_do_not_import_agents_workflow_llm_or_external_io() -> None:
    forbidden = {
        "langgraph",
        "langchain",
        "subprocess",
        "socket",
        "requests",
        "httpx",
        "serial",
        "sqlite3",
        "git",
    }
    for root in (EDA, HARDWARE):
        for path, tree in _trees(root):
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    assert all(
                        alias.name.split(".")[0] not in forbidden
                        for alias in node.names
                    ), path
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert node.module.split(".")[0] not in forbidden, path


def test_service_layers_do_not_open_files_or_start_processes() -> None:
    for root in (EDA, HARDWARE):
        for path, tree in _trees(root):
            if "adapters" in path.parts:
                continue
            calls = {
                node.func.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            assert not calls.intersection({"open", "exec", "eval", "compile"}), path


def test_hardware_adapters_never_create_a_second_process_runner() -> None:
    for path, tree in _trees(HARDWARE / "adapters"):
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert "subprocess" not in imports, path


def test_router_bindings_cannot_apply_build_or_flash() -> None:
    tree = ast.parse(ROUTER_BINDINGS.read_text(encoding="utf-8"))
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not calls.intersection(
        {"apply", "authorize_apply", "build", "flash", "promote"}
    )


def test_services_expose_no_human_decision_or_promotion_api() -> None:
    for path in (EDA / "service.py", HARDWARE / "service.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        methods = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert not methods.intersection({"approve", "decide", "promote", "commit"}), (
            path
        )
