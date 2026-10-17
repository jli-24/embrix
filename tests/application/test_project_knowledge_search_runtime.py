from __future__ import annotations

import chromadb

from embedded_copilot.api.copilot_knowledge_models import (
    CopilotKnowledgeSource,
)
from embedded_copilot.application.project_knowledge_search import (
    ProjectKnowledgeSearchRuntime,
)
from embedded_copilot.rag.embedding import HashEmbedding


def _runtime(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "docs").mkdir()
    (root / "docs" / "i2c.md").write_text(
        "# I2C\nESP32-S3 I2C1 uses SDA and SCL for controller transfers.",
        encoding="utf-8",
    )
    (root / "docs" / "other.md").write_text(
        "# UART\nUART observation is separate from I2C.",
        encoding="utf-8",
    )
    runtime = ProjectKnowledgeSearchRuntime(
        project_roots={("tenant-1", "project-1"): root},
        chroma_client=chromadb.EphemeralClient(),
        embedding=HashEmbedding(dimension=64),
    )
    return runtime, root


def test_project_search_is_project_and_tenant_bound_and_idempotent(tmp_path) -> None:
    runtime, _ = _runtime(tmp_path)

    first = runtime.refresh(tenant_id="tenant-1", project_id="project-1")
    second = runtime.refresh(tenant_id="tenant-1", project_id="project-1")
    results = runtime.search(
        tenant_id="tenant-1",
        project_id="project-1",
        query="ESP32-S3 I2C",
        sources=(CopilotKnowledgeSource.LOCAL,),
        top_k=4,
    )

    assert first.inserted == 2
    assert second.unchanged == 2
    assert results
    assert all(item.metadata["project_id"] == "project-1" for item in results)
    assert all(item.metadata["tenant_id"] == "tenant-1" for item in results)
    assert all("/" not in item.metadata.get("absolute_path", "") for item in results)


def test_project_search_rejects_unknown_project_and_empty_results(tmp_path) -> None:
    runtime, _ = _runtime(tmp_path)

    runtime.refresh(tenant_id="tenant-1", project_id="project-1")
    assert runtime.search(
        tenant_id="tenant-1",
        project_id="project-1",
        query="zzzzzzzzzzzzzz",
        sources=(),
        top_k=4,
    ) == ()

    try:
        runtime.search(
            tenant_id="tenant-2",
            project_id="project-1",
            query="I2C",
            sources=(),
            top_k=4,
        )
    except ValueError as error:
        assert str(error) == "project knowledge identity mismatch"
    else:
        raise AssertionError("cross-tenant search must fail closed")
