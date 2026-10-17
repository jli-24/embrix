from __future__ import annotations

import ast
from pathlib import Path


def test_verification_service_has_no_runtime_execution_or_external_dependency() -> None:
    source_path = Path("src/embedded_copilot/engineering_copilot/verification_service.py")
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
            "agent", "runtime", "executor", "gateway", "simulation_runtime",
            "subprocess", "requests", "httpx", "socket", "compiler", "flash",
            "kicad", "filesystem", "file", "shell",
        )
    )
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not called & {
        "open", "execute", "invoke", "run", "system", "popen", "write", "delete",
        "compile", "flash", "start", "collect",
    }


def test_verification_service_is_downstream_planning_only() -> None:
    source = Path("src/embedded_copilot/engineering_copilot/verification_service.py").read_text(encoding="utf-8")
    assert "class VerificationAgent" not in source
    assert "VerificationRuntime" not in source
    assert "OperationType.TEST" not in source
    assert "DesignTruth" not in source
    assert "SimulationSpecification" in source
    assert "VerificationResult" not in source
