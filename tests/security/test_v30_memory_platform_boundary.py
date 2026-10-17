from __future__ import annotations

import ast
import inspect
from pathlib import Path

from embedded_copilot.engineering_memory import EngineeringMemoryRetrievalPort
from embedded_copilot.knowledge_writer import KnowledgeWriterPort

ROOT = Path(__file__).parents[2]
SRC = ROOT / "src" / "embedded_copilot"
MEMORY_PACKAGES = (
    SRC / "engineering_memory",
    SRC / "memory_automation",
    SRC / "conversation_memory",
    SRC / "knowledge_evolution",
    SRC / "engineering_context",
)
RUNTIME_FILES = (
    SRC / "services" / "canonical_runtime.py",
    SRC / "agents",
)
FORBIDDEN_IMPORT_PREFIXES = (
    "embedded_copilot.agents",
    "embedded_copilot.autonomous_loop",
    "embedded_copilot.build",
    "embedded_copilot.device",
    "embedded_copilot.flash",
    "embedded_copilot.services",
    "embedded_copilot.tool_adapter",
    "embedded_copilot.tools",
    "langgraph",
)
FORBIDDEN_ROOTS = {
    "aiohttp",
    "anthropic",
    "chromadb",
    "httpx",
    "openai",
    "requests",
    "socket",
    "sqlite3",
    "subprocess",
    "urllib",
    "websockets",
}
FORBIDDEN_MEMORY_MUTATIONS = {
    "apply_human_approval",
    "create_candidate",
    "delete",
    "promote",
    "update",
    "write",
}


def _files(target: Path) -> tuple[Path, ...]:
    return (target,) if target.is_file() else tuple(target.rglob("*.py"))


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports(tree: ast.AST) -> tuple[str, ...]:
    values: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            values.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            values.append(node.module)
    return tuple(values)


def test_memory_platform_has_no_agent_workflow_or_runtime_dependencies() -> None:
    for path in (item for target in MEMORY_PACKAGES for item in _files(target)):
        for module in _imports(_tree(path)):
            root = module.split(".", 1)[0]
            # The v7.2.2 persistence adapter is the sole, explicit SQLite
            # boundary.  It remains outside Runtime/Agent access and is
            # covered by its own fail-closed migration tests.
            if path.name == "foundation_sqlite.py" and root in {
                "sqlite3",
                "pathlib",
                "threading",
            }:
                continue
            assert root not in FORBIDDEN_ROOTS, (path, module)
            assert not any(
                module == prefix or module.startswith(f"{prefix}.")
                for prefix in FORBIDDEN_IMPORT_PREFIXES
            ), (path, module)


def test_runtime_has_no_memory_mutation_edge() -> None:
    for path in (item for target in RUNTIME_FILES for item in _files(target)):
        tree = _tree(path)
        for module in _imports(tree):
            assert not module.startswith("embedded_copilot.engineering_memory"), (
                path,
                module,
            )
            assert not module.startswith("embedded_copilot.memory_automation"), (
                path,
                module,
            )
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr not in FORBIDDEN_MEMORY_MUTATIONS, (path, node.attr)


def test_retrieval_and_writer_ports_are_non_mutating_projection_boundaries() -> None:
    retrieval_methods = {
        name
        for name, value in EngineeringMemoryRetrievalPort.__dict__.items()
        if callable(value) and not name.startswith("_")
    }
    assert retrieval_methods == {"query"}
    assert tuple(
        inspect.signature(EngineeringMemoryRetrievalPort.query).parameters
    ) == ("self", "request")

    writer_methods = {
        name
        for name, value in KnowledgeWriterPort.__dict__.items()
        if callable(value) and not name.startswith("_")
    }
    assert writer_methods == {
        "write",
        "write_approved_memory",
        "write_approved_projection",
        "write_approved_graph_projection",
    }


def test_knowledge_writer_canonical_surface_excludes_candidate_helper() -> None:
    contracts = (SRC / "knowledge_writer" / "contracts.py").read_text(
        encoding="utf-8"
    )
    writer = (SRC / "knowledge_writer" / "writer.py").read_text(encoding="utf-8")
    exports = (SRC / "knowledge_writer" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "MemoryCandidate" not in contracts
    assert "artifact_from_candidate" not in contracts
    assert "MemoryCandidate" not in writer
    assert "artifact_from_candidate" not in writer
    assert '"artifact_from_candidate"' not in exports
