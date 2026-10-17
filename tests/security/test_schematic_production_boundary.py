import ast
from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_schematic_generation_service_is_side_effect_free() -> None:
    path = ROOT / "src" / "embedded_copilot" / "engineering_copilot" / "schematic_production.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)}
    imports.update(node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module)
    assert not any(name == "subprocess" or name.startswith("os") or name.startswith("pathlib") for name in imports)
    forbidden_calls = {"write_text", "write_bytes", "run", "Popen", "system"}
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in forbidden_calls for node in ast.walk(tree))


def test_schematic_side_effects_are_only_in_existing_worker_and_workspace_adapter() -> None:
    source = (ROOT / "src" / "embedded_copilot" / "schematic_execution.py").read_text(encoding="utf-8")
    assert "WorkspaceApplyService" in source
    assert "ExecutionOperationService" in source
    assert "ExecutionWorker" not in source
    assert "subprocess" not in source
