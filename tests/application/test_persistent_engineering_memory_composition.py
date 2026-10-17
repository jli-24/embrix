from __future__ import annotations

import sqlite3
from pathlib import Path

from embedded_copilot.application.desktop_composition import (
    build_project_first_chat_runtime,
)
from embedded_copilot.desktop_copilot_chat import (
    ChatProjectReference,
)
from embedded_copilot.engineering_context import EngineeringContextService
from embedded_copilot.engineering_memory.foundation_service import (
    EngineeringMemoryContextProjectionAdapter,
)
from embedded_copilot.multimodal.context import ProcessLocalAttachmentBindingRepository


class _Catalog:
    def __init__(self, root: Path) -> None:
        self._knowledge_root = root / "knowledge"
        self._knowledge_root.mkdir(parents=True)

    def knowledge_snapshot_root(
        self, project_id: str, *, tenant_id: str | None = None
    ) -> Path:
        del project_id, tenant_id
        return self._knowledge_root

    def project(self, tenant_id: str, project_id: str) -> ChatProjectReference:
        return ChatProjectReference(
            tenant_id=tenant_id,
            project_id=project_id,
            project_name_reference="test-project",
            workspace_reference="workspace:test-project",
        )

    def references(self, tenant_id: str, project_id: str) -> tuple:
        return ()

    def get_project(self, project_id: str, *, tenant_id: str):
        return self.project(tenant_id, project_id)


def _catalog(tmp_path: Path) -> _Catalog:
    return _Catalog(tmp_path / "projects")


def _project(catalog: _Catalog) -> str:
    del catalog
    return "project-1"


def test_persistent_memory_is_explicit_composition_opt_in(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    _project(catalog)
    database = tmp_path / "state" / "desktop.sqlite3"
    database.parent.mkdir()
    runtime = build_project_first_chat_runtime(
        database=database,
        project_catalog=catalog,
        approved_memory_store=None,
        environment=None,
        attachments=ProcessLocalAttachmentBindingRepository(),
    )
    assert runtime is not None
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'engineering_memory_foundation'"
        ).fetchone() is None


def test_persistent_memory_composition_uses_sqlite_repository_when_enabled(tmp_path) -> None:
    catalog = _catalog(tmp_path)
    _project(catalog)
    database = tmp_path / "state" / "desktop.sqlite3"
    database.parent.mkdir()
    runtime = build_project_first_chat_runtime(
        database=database,
        project_catalog=catalog,
        approved_memory_store=None,
        environment=None,
        attachments=ProcessLocalAttachmentBindingRepository(),
        persistent_engineering_memory=True,
    )
    assert runtime is not None
    context_provider = runtime._application._container.context_provider
    assert isinstance(context_provider, EngineeringContextService)
    assert isinstance(context_provider._source, EngineeringMemoryContextProjectionAdapter)
    foundation_database = database.with_name("desktop-engineering-memory.sqlite3")
    with sqlite3.connect(foundation_database) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'engineering_memory_foundation'"
        ).fetchone() == ("engineering_memory_foundation",)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'engineering_memory_foundation'"
        ).fetchone() is None
