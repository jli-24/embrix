from __future__ import annotations

import ast
from pathlib import Path

from embedded_copilot.debug.agent import DebugAgent as FoundationDebugAgent
from embedded_copilot.firmware.agent import FirmwareAgent as FoundationFirmwareAgent
from embedded_copilot.hardware.agent import HardwareAgent
from embedded_copilot.pcb.agent import PCBAgent
from embedded_copilot.services.analysis import AnalysisService
from embedded_copilot.services.canonical_runtime import (
    CANONICAL_RUNTIME_AGENT_NAMES,
    CANONICAL_RUNTIME_AGENT_TYPES,
    CANONICAL_RUNTIME_ROUTER,
    build_canonical_runtime,
)
import embedded_copilot.services.canonical_runtime as canonical_runtime
from embedded_copilot.services.config import Settings
from embedded_copilot.services.legacy_runtime import (
    LEGACY_RUNTIME_AGENT_TYPES,
    build_legacy_runtime,
)
from embedded_copilot.supervisor.agent import SupervisorAgent
from embedded_copilot.services.runtime import (
    build_analysis_service,
    build_runtime,
)


CANONICAL_RUNTIME_MODULE = (
    Path(__file__).parents[2]
    / "src"
    / "embedded_copilot"
    / "services"
    / "canonical_runtime.py"
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def test_canonical_runtime_does_not_import_legacy_composition() -> None:
    imported = _imported_modules(CANONICAL_RUNTIME_MODULE)

    assert "embedded_copilot.supervisor.agent" not in imported
    assert "embedded_copilot.hardware.agent" not in imported
    assert "embedded_copilot.pcb.agent" not in imported
    assert "embedded_copilot.integration" not in imported
    assert "embedded_copilot.services.analysis" not in imported
    assert "embedded_copilot.services.legacy_runtime" not in imported
    assert "embedded_copilot.memory_automation" not in imported
    assert "embedded_copilot.engineering_memory" not in imported
    assert "embedded_copilot.multimodal_input" not in imported


def test_canonical_runtime_whitelist_is_explicit() -> None:
    assert CANONICAL_RUNTIME_AGENT_NAMES == (
        "supervisor",
        "knowledge",
        "firmware",
        "debug",
    )
    assert tuple(agent.__name__ for agent in CANONICAL_RUNTIME_AGENT_TYPES) == (
        "KnowledgeAgent",
        "FirmwareAgent",
        "DebugAgent",
    )
    assert CANONICAL_RUNTIME_ROUTER.__name__ == "supervisor_node"


def test_runtime_is_only_a_compatibility_facade() -> None:
    assert build_runtime is build_canonical_runtime
    assert build_runtime.__module__ == (
        "embedded_copilot.services.canonical_runtime"
    )


def test_legacy_runtime_whitelist_is_separate() -> None:
    assert LEGACY_RUNTIME_AGENT_TYPES == (
        SupervisorAgent,
        FoundationFirmwareAgent,
        FoundationDebugAgent,
        HardwareAgent,
        PCBAgent,
    )


def test_legacy_runtime_is_explicit_and_keeps_old_import_compatible() -> None:
    assert build_analysis_service is build_legacy_runtime
    assert build_legacy_runtime.__module__ == (
        "embedded_copilot.services.legacy_runtime"
    )
    assert AnalysisService.__name__ == "AnalysisService"


def test_legacy_runtime_composes_existing_analysis_service() -> None:
    service = build_legacy_runtime(Settings(_env_file=None))

    assert isinstance(service, AnalysisService)


def test_ollama_production_embedding_branch_does_not_use_hash_embedding(monkeypatch) -> None:
    class StubOllamaEmbedding:
        identity = "ollama|embeddinggemma|ollama-embed-v1|3"

        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def probe(self):
            return object()

    monkeypatch.setattr(canonical_runtime, "OllamaEmbeddingAdapter", StubOllamaEmbedding)

    embedding = canonical_runtime._build_embedding(
        Settings(
            _env_file=None,
            embedding_provider="ollama",
            embedding_model="embeddinggemma",
        )
    )

    assert isinstance(embedding, StubOllamaEmbedding)
    assert embedding.kwargs["model"] == "embeddinggemma"
