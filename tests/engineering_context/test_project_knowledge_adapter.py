from __future__ import annotations

from pathlib import Path

import pytest

from embedded_copilot.engineering_context.adapters import (
    ProjectKnowledgeEvidenceAdapter,
)
from embedded_copilot.engineering_context import (
    EngineeringContextRequest,
    EngineeringContextService,
    ReadOnlyProjectionSource,
)
from embedded_copilot.engineering_context.adapters.external_knowledge import (
    ExternalKnowledgeEvidenceAdapter,
)
from embedded_copilot.engineering_context.exceptions import (
    EngineeringContextExternalEvidenceUnavailable,
    EngineeringContextUnavailable,
)
from embedded_copilot.knowledge.source import KnowledgeEvidence


def _snapshot(root: Path, *, content: str = "# I2C\n\nACK and pull-up evidence.") -> None:
    (root / "firmware").mkdir(parents=True)
    (root / "firmware" / "i2c.md").write_text(content, encoding="utf-8")


def test_project_snapshot_is_project_scoped_and_returns_knowledge_evidence(tmp_path: Path):
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    _snapshot(project_a)
    _snapshot(project_b, content="# UART\n\nUART evidence.")

    roots = {"project-a": project_a, "project-b": project_b}
    adapter = ProjectKnowledgeEvidenceAdapter(roots.__getitem__)

    a = adapter.list_evidence("project-a", "I2C", ())
    b = adapter.list_evidence("project-b", "I2C", ())

    assert len(a) == 1
    assert type(a[0]) is KnowledgeEvidence
    assert a[0].source_id == "firmware:i2c"
    assert b == ()


def test_context_service_projects_snapshot_knowledge_evidence(tmp_path: Path):
    root = tmp_path / "project"
    _snapshot(root)
    adapter = ProjectKnowledgeEvidenceAdapter(lambda project_id: root)
    service = EngineeringContextService(
        source_port=ReadOnlyProjectionSource(knowledge_port=adapter)
    )

    snapshot = service.get_context(
        EngineeringContextRequest(project_id="project-1", task="I2C", constraints=())
    )

    assert snapshot is not None
    assert len(snapshot.knowledge_evidence) == 1
    evidence = snapshot.knowledge_evidence[0]
    assert evidence.project_id == "project-1"
    assert evidence.source_reference == "knowledge:firmware:i2c"
    assert evidence.source_fingerprint.startswith("sha256:")


class _ExternalEvidence:
    def __init__(self, values: tuple[object, ...] = (), error: Exception | None = None) -> None:
        self.values = values
        self.error = error
        self.calls = 0

    def list_evidence(self, project_id: str, task: str, constraints: tuple[str, ...]):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.values


def test_external_evidence_is_only_requested_for_the_explicit_context_flag(tmp_path: Path):
    root = tmp_path / "project"
    _snapshot(root)
    external = _ExternalEvidence()
    service = EngineeringContextService(
        source_port=ReadOnlyProjectionSource(
            knowledge_port=ProjectKnowledgeEvidenceAdapter(lambda _: root),
            external_knowledge_port=external,
        )
    )

    service.get_context(
        EngineeringContextRequest(project_id="project-1", task="I2C", constraints=())
    )

    assert external.calls == 0


def test_requested_external_search_failure_makes_context_unavailable(tmp_path: Path):
    root = tmp_path / "project"
    _snapshot(root)
    external = _ExternalEvidence(error=RuntimeError("network unavailable"))
    service = EngineeringContextService(
        source_port=ReadOnlyProjectionSource(
            knowledge_port=ProjectKnowledgeEvidenceAdapter(lambda _: root),
            external_knowledge_port=external,
        )
    )

    with pytest.raises(EngineeringContextUnavailable):
        service.get_context(
            EngineeringContextRequest(
                project_id="project-1",
                task="I2C",
                constraints=(),
                external_search=True,
            )
        )

    assert external.calls == 1


def test_requested_external_search_without_a_provider_fails_closed(tmp_path: Path):
    root = tmp_path / "project"
    _snapshot(root)
    service = EngineeringContextService(
        source_port=ReadOnlyProjectionSource(
            knowledge_port=ProjectKnowledgeEvidenceAdapter(lambda _: root),
        )
    )

    with pytest.raises(EngineeringContextUnavailable):
        service.get_context(
            EngineeringContextRequest(
                project_id="project-1",
                task="I2C",
                constraints=(),
                external_search=True,
            )
        )


def test_empty_snapshot_returns_no_evidence(tmp_path: Path):
    root = tmp_path / "empty"
    root.mkdir()
    adapter = ProjectKnowledgeEvidenceAdapter(lambda project_id: root)

    assert adapter.list_evidence("project-1", "I2C", ()) == ()


def test_unsupported_extensions_and_metadata_are_not_evidence(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "firmware").mkdir()
    (root / "firmware" / "i2c.c").write_text("I2C source", encoding="utf-8")
    (root / "firmware" / "i2c.pdf").write_bytes(b"not parsed")
    (root / "documents").mkdir()
    (root / "documents" / "claim.md").write_text(
        "# Must not be loaded", encoding="utf-8"
    )
    adapter = ProjectKnowledgeEvidenceAdapter(lambda project_id: root)

    assert adapter.list_evidence("project-1", "I2C", ()) == ()


def test_compile_commands_manifest_does_not_invalidate_project_snapshot(
    tmp_path: Path,
):
    root = tmp_path / "project"
    _snapshot(root)
    (root / "firmware" / "compile_commands.json").write_text(
        "[]", encoding="utf-8"
    )
    adapter = ProjectKnowledgeEvidenceAdapter(lambda project_id: root)

    evidence = adapter.list_evidence("project-1", "I2C", ())

    assert len(evidence) == 1
    assert evidence[0].source_id == "firmware:i2c"


def test_filename_match_without_content_match_is_not_evidence(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "firmware").mkdir()
    (root / "firmware" / "i2c.md").write_text(
        "# UART\n\nUART-only content.", encoding="utf-8"
    )
    adapter = ProjectKnowledgeEvidenceAdapter(lambda project_id: root)

    assert adapter.list_evidence("project-1", "I2C", ()) == ()


def test_invalid_snapshot_root_fails_closed():
    adapter = ProjectKnowledgeEvidenceAdapter(lambda project_id: "not-a-path")

    with pytest.raises(TypeError):
        adapter.list_evidence("project-1", "I2C", ())


def test_project_catalog_resolver_requires_ready_and_contained_project(tmp_path: Path):
    from embedded_copilot.project_catalog import (
        ProjectCatalogRejected,
        ProjectCatalogRuntime,
    )
    from tests.application.test_desktop_composition import _project

    project_root = tmp_path / "projects"
    project_root.mkdir()
    catalog = ProjectCatalogRuntime(
        project_root=project_root,
        state_database=tmp_path / "state" / "desktop.sqlite3",
        metadata_directory=tmp_path / "metadata",
        initialize_async=False,
    )
    project = _project(catalog)

    root = catalog.knowledge_snapshot_root(project.project_id)
    assert root.is_dir()

    with pytest.raises(ProjectCatalogRejected):
        catalog.knowledge_snapshot_root("project-missing")
