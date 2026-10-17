import ast
from pathlib import Path

ROOT = (
    Path(__file__).parents[2] / "src" / "embedded_copilot" / "engineering_optimization"
)


def test_v46_optimization_has_no_external_execution_dependencies():
    forbidden = {
        "subprocess",
        "socket",
        "requests",
        "httpx",
        "langgraph",
        "execution",
        "verification",
        "engineering_repair",
    }
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert not names & forbidden, path.name


def test_router_optimization_binding_does_not_control_governance_or_execution():
    path = (
        Path(__file__).parents[2]
        / "src"
        / "embedded_copilot"
        / "agent_runtime"
        / "bindings.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.ClassDef)
        and item.name == "OptimizationCapabilityBinding"
    )
    calls = {
        item.func.attr
        for item in ast.walk(node)
        if isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute)
    }
    assert not calls & {
        "submit_review",
        "record_decision",
        "project_context",
        "create_memory_candidate",
        "apply",
        "build",
        "flash",
        "execute",
    }
