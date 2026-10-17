from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path("src/embedded_copilot/engineering_qa")


def test_engineering_qa_is_context_first_service_only() -> None:
    forbidden = {"agents", "langgraph", "runtime", "workflow", "memory", "graph", "workspace", "build", "flash", "device", "tool", "openai", "deepseek", "qwen", "subprocess", "socket", "requests", "urllib"}
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = node.module.casefold() if isinstance(node, ast.ImportFrom) and node.module else ""
            if isinstance(node, ast.Import):
                module = " ".join(alias.name.casefold() for alias in node.names)
            assert not any(token in module for token in forbidden), path


def test_qa_service_uses_context_provider_before_model_gateway() -> None:
    source = (PACKAGE / "service.py").read_text(encoding="utf-8")
    assert "get_context" in source
    assert source.index("get_context") < source.index("generate")
