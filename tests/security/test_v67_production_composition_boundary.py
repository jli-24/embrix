from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from embedded_copilot.api.main import create_app
from embedded_copilot.engineering_workspace_apply import (
    ProjectCatalogWorkspaceApplyPort,
    ProjectCatalogWorkspaceTargetResolver,
    SQLiteWorkspaceApplyStore,
    WorkspaceApplyService,
)
from embedded_copilot.services.config import Settings


def test_project_catalog_resolver_is_not_a_fixture_resolver(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    catalog = type(
        "Catalog",
        (),
        {"knowledge_snapshot_root": lambda self, project_id, *, tenant_id: root},
    )()
    resolver = ProjectCatalogWorkspaceTargetResolver(catalog)

    resolved = resolver.resolve(tenant_id="tenant-a", project_id="project-a")

    assert resolved.root == root.resolve()
    assert resolved.target_id == "project-root"


def test_default_project_runtime_binds_real_v65_components_or_fails_closed(tmp_path: Path) -> None:
    project_root = tmp_path / "projects"
    project_root.mkdir()
    settings = Settings(
        _env_file=None,
        project_root=project_root,
        desktop_state_database=tmp_path / "desktop.sqlite3",
        desktop_project_metadata_directory=tmp_path / "metadata",
        environment="production",
    )

    app = create_app(settings=settings)
    with TestClient(app):
        port = app.state.workspace_apply_port
        assert isinstance(port, ProjectCatalogWorkspaceApplyPort)
        service = port._service
        assert isinstance(service, WorkspaceApplyService)
        assert isinstance(service._store, SQLiteWorkspaceApplyStore)
        assert isinstance(service._resolver, ProjectCatalogWorkspaceTargetResolver)
