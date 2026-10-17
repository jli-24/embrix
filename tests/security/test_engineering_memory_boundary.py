from __future__ import annotations

import ast
import inspect
from pathlib import Path

import embedded_copilot.engineering_memory as memory
from embedded_copilot.coding_runtime import CodingRuntime
from embedded_copilot.debug_runtime import DebugRuntime
from embedded_copilot.reasoning_runtime import ReasoningRuntime
from embedded_copilot.telemetry_runtime import TelemetryRuntime
from embedded_copilot.tool_runtime import ToolRuntime
from embedded_copilot.verification_agent import VerificationAgent
from embedded_copilot.vscode_runtime import VSCodeRuntime
from embedded_copilot.workspace_runtime import WorkspaceRuntime

PACKAGE = Path("src/embedded_copilot/engineering_memory")
ROOT_FILES = {
    "__init__.py",
    "audit.py",
    "exceptions.py",
    "facade.py",
    "factory.py",
    "fingerprint.py",
    "models.py",
    "ports.py",
    "projection.py",
    "contracts.py",
    "ranking.py",
    "retrieval.py",
    "store.py",
    "rules.py",
    "service.py",
    "foundation.py",
    "foundation_repository.py",
    "foundation_service.py",
    "foundation_sqlite.py",
}
STORE_FILES = {"__init__.py", "in_memory.py"}
FORBIDDEN_ROOTS = {
    "aiohttp",
    "anthropic",
    "asyncio",
    "chromadb",
    "fastapi",
    "httpx",
    "langgraph",
    "multiprocessing",
    "openai",
    "os",
    "pathlib",
    "random",
    "requests",
    "shutil",
    "socket",
    "sqlite3",
    "starlette",
    "streamlit",
    "subprocess",
    "sys",
    "tempfile",
    "time",
    "urllib",
    "uuid",
    "websockets",
}
FORBIDDEN_PREFIXES = (
    "embedded_copilot.agents",
    "embedded_copilot.api",
    "embedded_copilot.rag",
    "embedded_copilot.services",
    "embedded_copilot.tools",
    "embedded_copilot.workspace_runtime",
)
FORBIDDEN_CALLS = {"__import__", "compile", "eval", "exec", "open"}
FORBIDDEN_ATTRIBUTES = {
    "connect",
    "getenv",
    "now",
    "open",
    "read_bytes",
    "read_text",
    "run",
    "sleep",
    "utcnow",
    "write",
    "write_bytes",
    "write_text",
}


def _modules(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if isinstance(node, ast.ImportFrom) and node.module:
        return (node.module,)
    return ()


def test_engineering_memory_package_is_fixed_and_non_executing() -> None:
    assert {path.name for path in PACKAGE.glob("*.py")} == ROOT_FILES
    assert {path.name for path in (PACKAGE / "stores").glob("*.py")} == STORE_FILES
    for path in PACKAGE.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            for module in _modules(node):
                root = module.split(".", 1)[0]
                if root == "threading":
                    assert path.name in {"in_memory.py", "foundation_sqlite.py"}
                elif path.name == "foundation_sqlite.py" and root in {
                    "pathlib",
                    "sqlite3",
                    "threading",
                }:
                    pass
                else:
                    assert root not in FORBIDDEN_ROOTS, (path, module)
                assert not module.startswith(FORBIDDEN_PREFIXES), (path, module)
                if module.startswith("embedded_copilot.verification_agent"):
                    assert path.name == "models.py", (path, module)
                    assert module == "embedded_copilot.verification_agent"
            if isinstance(node, ast.AsyncFunctionDef):
                raise AssertionError(path)  # noqa: TRY004
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in FORBIDDEN_CALLS, path
                if isinstance(node, ast.Attribute):
                    if path.name == "foundation_sqlite.py" and node.attr == "connect":
                        continue
                    assert node.attr not in FORBIDDEN_ATTRIBUTES, (path, node.attr)
        for statement in tree.body:
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                assert not isinstance(
                    statement.value, (ast.Dict, ast.List, ast.Set)
                ), path
        lowered = source.casefold()
        for token in (
            "hard_delete",
            "default_allow",
            "create_verification_agent",
            "subprocess",
            "database",
        ):
            if path.name == "foundation_sqlite.py" and token == "database":
                continue
            assert token not in lowered, (path, token)


def test_facade_ports_and_exports_are_narrow() -> None:
    assert {
        name
        for name, value in memory.EngineeringMemory.__dict__.items()
        if callable(value) and not name.startswith("_")
    } == {"memory_port"}
    assert {
        name
        for name, value in memory.EngineeringMemoryPort.__dict__.items()
        if callable(value) and not name.startswith("_")
    } == {"execute"}
    assert tuple(
        inspect.signature(memory.EngineeringMemoryPort.execute).parameters
    ) == (
        "self",
        "request",
    )
    assert not hasattr(memory, "InMemoryEngineeringMemoryStore")
    assert not hasattr(memory, "canonical_fingerprint")
    assert not hasattr(memory, "FoundationMemoryType")
    assert not hasattr(memory, "FoundationEngineeringMemoryRecord")
    assert not hasattr(memory, "EngineeringMemoryRepositoryPort")
    assert not hasattr(memory, "InMemoryEngineeringMemoryRepository")
    assert not hasattr(memory, "SQLiteEngineeringMemoryRepository")
    assert not hasattr(memory, "migrate_foundation_schema")


def test_existing_runtime_facades_remain_read_only_and_unchanged() -> None:
    expected = (
        (CodingRuntime, {"coding_port"}),
        (DebugRuntime, {"debug_port"}),
        (TelemetryRuntime, {"telemetry_port"}),
        (ToolRuntime, {"tool_port"}),
        (WorkspaceRuntime, {"workspace_port"}),
        (VSCodeRuntime, {"vscode_port"}),
        (ReasoningRuntime, {"reasoning_port"}),
        (VerificationAgent, {"verification_port"}),
    )
    for runtime_type, methods in expected:
        assert {
            name
            for name, value in runtime_type.__dict__.items()
            if callable(value) and not name.startswith("_")
        } == methods
