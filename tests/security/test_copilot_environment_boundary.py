import ast
from pathlib import Path

PACKAGE = Path(__file__).parents[2] / "src" / "embedded_copilot" / "copilot_environment"

FORBIDDEN_IMPORT_PARTS = {
    "database",
    "execution",
    "filesystem",
    "git",
    "hardware",
    "httpx",
    "langgraph",
    "network",
    "os",
    "pathlib",
    "requests",
    "socket",
    "subprocess",
    "workflow",
}

FORBIDDEN_CALLS = {
    "apply",
    "approve",
    "build",
    "deploy",
    "execute",
    "flash",
    "install",
    "invoke",
    "promote",
    "repair",
    "upgrade",
}


def test_environment_has_no_external_or_execution_dependencies() -> None:
    files = tuple(PACKAGE.rglob("*.py"))
    assert files
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = (node.module,)
            else:
                modules = ()
            for module in modules:
                assert not set(module.casefold().split(".")).intersection(
                    FORBIDDEN_IMPORT_PARTS
                ), (path, module)


def test_environment_exposes_no_domain_mutation_surface() -> None:
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.name.casefold() not in FORBIDDEN_CALLS, (path, node.name)
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    name = node.func.attr.casefold()
                elif isinstance(node.func, ast.Name):
                    name = node.func.id.casefold()
                else:
                    name = ""
                assert name not in FORBIDDEN_CALLS, (path, name)
