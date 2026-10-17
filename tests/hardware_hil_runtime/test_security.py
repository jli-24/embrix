import ast
from pathlib import Path

ROOT = Path(__file__).parents[2] / "src" / "embedded_copilot" / "hardware_hil_runtime"


def test_core_has_no_direct_external_or_execution_control_dependencies():
    forbidden = {
        "subprocess",
        "os",
        "socket",
        "requests",
        "httpx",
        "git",
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


def test_router_boundary_text_has_no_control_methods():
    source = (
        Path(__file__).parents[2]
        / "src"
        / "embedded_copilot"
        / "agent_runtime"
        / "bindings.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    binding = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "HardwareHILTestCapabilityBinding"
    )
    called = {
        node.func.attr
        for node in ast.walk(binding)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not called & {
        "authorize",
        "execute",
        "collect_observation",
        "build",
        "flash",
    }
