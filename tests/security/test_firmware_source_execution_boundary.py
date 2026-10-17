from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2] / "src" / "embedded_copilot"
GENERATION = ROOT / "engineering_copilot" / "firmware_source_generation.py"
BRIDGE = ROOT / "firmware_source_execution.py"
ADAPTER = ROOT / "execution_worker" / "firmware_source_write.py"


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports(path: Path) -> set[str]:
    values: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            values.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            values.add(node.module)
    return values


def _called_names(path: Path) -> set[str]:
    values: set[str] = set()
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name):
            values.add(target.id)
        elif isinstance(target, ast.Attribute):
            values.add(target.attr)
    return values


def test_deterministic_generation_has_no_runtime_or_side_effect_dependencies() -> None:
    imports = _imports(GENERATION)
    assert not any(
        token in name.casefold()
        for name in imports
        for token in (
            "agent",
            "runtime",
            "executor",
            "gateway",
            "workspace",
            "filesystem",
            "subprocess",
            "openai",
            "anthropic",
            "langchain",
            "langgraph",
        )
    )
    assert _called_names(GENERATION).isdisjoint(
        {"open", "write", "write_text", "write_bytes", "run", "Popen", "system"}
    )


def test_bridge_and_adapter_only_use_canonical_worker_and_workspace_boundary() -> None:
    bridge_imports = _imports(BRIDGE)
    adapter_imports = _imports(ADAPTER)
    combined = bridge_imports | adapter_imports
    assert not any(
        name.startswith(prefix)
        for name in combined
        for prefix in (
            "embedded_copilot.execution.",
            "embedded_copilot.tool_runtime",
            "embedded_copilot.workspace_runtime",
            "subprocess",
            "shutil",
            "openai",
            "anthropic",
            "langchain",
            "langgraph",
        )
    )
    assert _called_names(BRIDGE).isdisjoint(
        {"open", "write", "write_text", "write_bytes", "run", "Popen", "system"}
    )
    assert _called_names(ADAPTER).isdisjoint(
        {"open", "write", "write_text", "write_bytes", "run", "Popen", "system"}
    )
    adapter_source = ADAPTER.read_text(encoding="utf-8")
    assert "WorkspaceApplyService" in adapter_source
    assert ".apply(" in adapter_source


def test_source_write_is_the_only_new_canonical_operation_and_has_no_execution_alias() -> None:
    contracts = (ROOT / "execution_worker" / "contracts.py").read_text(encoding="utf-8")
    assert contracts.count('FIRMWARE_SOURCE_WRITE = "FIRMWARE_SOURCE_WRITE"') == 1
    assert "class FirmwareSourceWriteOperation" in contracts
    assert "shell" not in ast.dump(_tree(BRIDGE)).casefold()
