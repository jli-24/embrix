from __future__ import annotations

import ast
from pathlib import Path


def test_firmware_design_service_has_no_agent_or_execution_dependency() -> None:
    source_path = Path("src/embedded_copilot/engineering_copilot/firmware_design_service.py")
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
            "agent", "runtime", "executor", "gateway", "subprocess", "requests",
            "compiler", "flash", "kicad",
        )
    )
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not called & {"open", "execute", "invoke", "run", "system", "popen", "write", "delete"}


def test_firmware_design_service_is_not_an_agent_or_mutation_boundary() -> None:
    source = Path("src/embedded_copilot/engineering_copilot/firmware_design_service.py").read_text(encoding="utf-8")
    assert "class FirmwareAgent" not in source
    assert "class CodeGeneration" not in source
    assert "subprocess" not in source
    assert "HardwareDesignSpecification" in source
    assert "DesignTruth" not in source
