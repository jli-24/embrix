from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2] / "src" / "embedded_copilot" / "execution"
FORBIDDEN_IMPORTS = (
    "agents",
    "langgraph",
    "model_gateway",
    "engineering_memory",
    "engineering_knowledge",
    "knowledge_governance",
)
FORBIDDEN_CALLS = {"system", "invoke", "execute", "promote", "approve_and_save"}


def test_execution_integrations_use_only_injected_boundaries() -> None:
    for path in ROOT.glob("**/*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [alias.name.casefold() for alias in node.names]
                if isinstance(node, ast.ImportFrom) and node.module:
                    names.append(node.module.casefold())
                assert not any(
                    part in name for name in names for part in FORBIDDEN_IMPORTS
                ), path
            if isinstance(node, ast.Call):
                called = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else node.func.id
                    if isinstance(node.func, ast.Name)
                    else ""
                )
                assert called.casefold() not in FORBIDDEN_CALLS, path


def test_subprocess_is_non_shell_and_commands_are_fixed() -> None:
    source = (ROOT / "adapters" / "runner.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    popen = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Popen"
    )
    shell = next(keyword for keyword in popen.keywords if keyword.arg == "shell")
    assert isinstance(shell.value, ast.Constant) and shell.value.value is False
    assert "os.system" not in source
    assert "subprocess.run" not in source
    assert "os.popen" not in source
