import ast
from pathlib import Path

PACKAGE = Path(__file__).parents[2] / "src" / "embedded_copilot" / "engineering_desktop"


def test_desktop_core_has_no_external_or_high_impact_imports():
    forbidden = {
        "os",
        "pathlib",
        "subprocess",
        "socket",
        "requests",
        "httpx",
        "sqlite3",
        "execution",
        "repair",
        "hardware",
        "workflow",
        "langgraph",
        "agent_runtime",
    }
    for path in PACKAGE.rglob("*.py"):
        if path.parts[-2] == "adapters":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports |= {
            (node.module or "").split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert not (imports & forbidden), path


def test_desktop_ports_expose_projection_not_router_or_settings_mutation():
    source = (PACKAGE / "ports.py").read_text(encoding="utf-8")
    assert "invoke(" not in source
    assert "set_" not in source
    assert "save_settings" not in source
    assert "answer(" in source
