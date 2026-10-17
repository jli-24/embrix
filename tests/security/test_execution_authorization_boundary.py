from __future__ import annotations

import ast
from pathlib import Path


def test_authorization_service_has_no_execution_or_side_effect_dependency() -> None:
    source_path = Path(
        "src/embedded_copilot/engineering_copilot/execution_authorization_service.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not any(
        forbidden in name.lower()
        for name in imported
        for forbidden in (
            "agent", "runtime", "executor", "gateway", "compiler", "subprocess",
            "requests", "httpx", "socket", "filesystem", "shell", "workspace",
            "embedded_copilot.execution", "execution_worker", "simulation_runtime",
            "tool_adapter",
        )
    )
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not calls & {
        "open", "execute", "invoke", "run", "system", "popen", "write",
        "delete", "compile", "flash", "start", "build",
    }


def test_authorization_layer_is_not_an_execution_adapter() -> None:
    source = Path(
        "src/embedded_copilot/engineering_copilot/execution_authorization_service.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "ExecutionWorker", "ToolExecutionRequest", "BuildExecutor", "FlashExecutor",
        "SimulationRuntime", "WorkspaceMutation", "CapabilityAgent",
    ):
        assert forbidden not in source
    assert "ExecutionAuthorization" in source
    assert "AuthorizationDecision" in source
