from __future__ import annotations

import ast
from pathlib import Path


def test_implementation_service_has_no_agent_runtime_or_execution_dependency() -> None:
    source_path = Path("src/embedded_copilot/engineering_copilot/implementation_service.py")
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
            "agent", "runtime", "executor", "gateway", "compiler", "build", "flash",
            "subprocess", "requests", "httpx", "socket", "filesystem", "file", "shell", "git",
            "kicad",
        )
    )
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not called & {
        "open", "execute", "invoke", "run", "system", "popen", "write", "delete",
        "compile", "flash", "start", "build",
    }


def test_implementation_service_is_downstream_planning_only() -> None:
    source = Path("src/embedded_copilot/engineering_copilot/implementation_service.py").read_text(encoding="utf-8")
    assert "ImplementationAgent" not in source
    assert "ImplementationRuntime" not in source
    assert "DesignTruth" not in source
    assert "SourceFile" not in source
    assert "CodeGenerator" not in source
    assert "Build" not in source
    assert "Flash" not in source
    assert "ExpectedArtifact" in source
    assert "ImplementationSpecification" in source
