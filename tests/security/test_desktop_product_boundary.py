import ast
from pathlib import Path

PACKAGE = (
    Path(__file__).parents[2] / "src" / "embedded_copilot" / "desktop_product_runtime"
)


def test_product_core_has_no_external_or_engineering_control_imports() -> None:
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
        "agent_runtime",
        "langgraph",
    }
    for path in PACKAGE.rglob("*.py"):
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
        assert not imports & forbidden, path


def test_product_service_has_no_high_impact_operation_surface() -> None:
    source = (PACKAGE / "service.py").read_text(encoding="utf-8")
    for forbidden in (".build(", ".flash(", ".apply(", ".promote(", ".invoke("):
        assert forbidden not in source
