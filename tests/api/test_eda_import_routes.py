from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from embedded_copilot.api.main import create_app
from embedded_copilot.api.project_catalog_routes import router
from embedded_copilot.core.config import Settings
from embedded_copilot.project_catalog import (
    ProjectCatalogRuntime,
    ProjectCreationRequest,
    ProjectType,
)

OWNER = {"X-Copilot-Requester": "owner-1", "X-Environment-Tenant": "tenant-1"}
OTHER = {"X-Copilot-Requester": "other-1", "X-Environment-Tenant": "tenant-1"}


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 9, 30, 8, 0, tzinfo=UTC)


def _app(tmp_path: Path) -> tuple[FastAPI, str]:
    root = tmp_path / "projects"
    root.mkdir()
    service = ProjectCatalogRuntime(
        project_root=root, state_database=tmp_path / "catalog.sqlite3", clock=Clock(), initialize_async=False
    )
    candidate = service.preview(
        ProjectCreationRequest(name="Board", relative_path="board", project_type=ProjectType.STM32),
        tenant_id="tenant-1", requester="owner-1",
    )
    project = service.create(
        candidate.preview_id, candidate.fingerprint, confirmed_at=Clock().now(), tenant_id="tenant-1", requester="owner-1"
    )
    (root / "board" / "board.eprj3").write_bytes(b"opaque")
    app = FastAPI()
    app.state.project_catalog_runtime = service

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-import"
        return await call_next(request)

    app.include_router(router)
    return app, project.project_id


def test_owner_preview_commit_list_detail_and_strict_input(tmp_path: Path) -> None:
    app, project_id = _app(tmp_path)
    path = f"/api/v1/copilot/projects/{project_id}"
    body = {"backend": "JLCEDA_PRO", "native_entry_relative_path": "board.eprj3", "selected_native_entries": [],
            "expected_head_revision_id": None, "expected_head_revision_fingerprint": None}
    with TestClient(app) as client:
        assert client.post(path + "/eda-import-previews", headers=OWNER, json={**body, "objects": [{"fake": True}]}).status_code == 422
        assert client.post(path + "/eda-import-previews", headers=OWNER, json={**body, "source_set_fingerprint": "sha256:" + "0" * 64}).status_code == 422
        preview_response = client.post(path + "/eda-import-previews", headers=OWNER, json=body)
        assert preview_response.status_code == 200
        preview = preview_response.json()
        assert preview["verified"] is False and preview["can_modify"] is False
        assert preview["native_format_versions"]["board.eprj3"] == "UNKNOWN"
        assert client.post(path + "/eda-imports", headers=OWNER, json={"preview_id": preview["preview_id"],
            "preview_fingerprint": preview["preview_fingerprint"], "objects": []}).status_code == 422
        committed = client.post(path + "/eda-imports", headers=OWNER,
            json={"preview_id": preview["preview_id"], "preview_fingerprint": preview["preview_fingerprint"]})
        assert committed.status_code == 200
        revision = committed.json()
        assert revision["source_currentness"] == "CURRENT"
        listed = client.get(path + "/eda-imports", headers=OWNER,
            params={"backend": "JLCEDA_PRO", "native_entry_relative_path": "board.eprj3"})
        assert listed.status_code == 200
        assert listed.json()["items"][0]["import_revision_id"] == revision["import_revision_id"]
        detail = client.get(path + "/eda-imports/" + revision["import_revision_id"], headers=OWNER,
            params={"fingerprint": revision["import_revision_fingerprint"]})
        assert detail.status_code == 200
        assert detail.json()["can_modify"] is False


def test_identity_rejection_precedes_candidate_and_file_access(tmp_path: Path) -> None:
    app, project_id = _app(tmp_path)
    service = app.state.project_catalog_runtime
    calls: list[str] = []

    def forbidden_scan(*args, **kwargs):
        calls.append("scan")
        raise AssertionError("source read before Owner check")

    service._artifact_intelligence.scan_eda_import_directory = forbidden_scan
    path = f"/api/v1/copilot/projects/{project_id}/eda-import-previews"
    body = {"backend": "JLCEDA_PRO", "native_entry_relative_path": "board.eprj3", "selected_native_entries": [],
            "expected_head_revision_id": None, "expected_head_revision_fingerprint": None}
    with TestClient(app) as client:
        assert client.post(path, json=body).status_code == 401
        assert client.post(path, headers=OTHER, json=body).status_code == 403
    assert calls == []


def test_production_desktop_composition_exposes_c1_routes(tmp_path: Path) -> None:
    from embedded_copilot.execution_worker.service import ExecutionOperationService
    from embedded_copilot.execution_worker.store import SQLiteExecutionStore

    calls = []

    class OfflineExecution(ExecutionOperationService):
        def create_operation(self, *args, **kwargs):
            calls.append("create_operation")
            raise AssertionError("read-only import must not create execution operations")

        def enqueue(self, *args, **kwargs):
            calls.append("enqueue")
            raise AssertionError("read-only import must not enqueue execution")

        def enqueue_by_id(self, *args, **kwargs):
            calls.append("enqueue_by_id")
            raise AssertionError("read-only import must not enqueue execution")

    app, project_id = _app(tmp_path)
    execution = OfflineExecution(store=SQLiteExecutionStore(tmp_path / "offline-execution.sqlite3"))
    production = create_app(
        settings=Settings(environment="test", project_root=tmp_path / "projects",
            desktop_state_database=str(tmp_path / "desktop.sqlite3"), _env_file=None),
        project_catalog_runtime=app.state.project_catalog_runtime,
        execution_operation_port=execution,
    )
    paths = set(production.openapi()["paths"])
    assert "/api/v1/copilot/projects/{project_id}/eda-import-previews" in paths
    assert "/api/v1/copilot/projects/{project_id}/eda-imports" in paths
    assert "/api/v1/copilot/projects/{project_id}/eda-imports/{import_revision_id}" in paths
    with TestClient(production) as client:
        preview = client.post(
            f"/api/v1/copilot/projects/{project_id}/eda-import-previews", headers=OWNER,
            json={"backend": "JLCEDA_PRO", "native_entry_relative_path": "board.eprj3",
                  "selected_native_entries": [], "expected_head_revision_id": None,
                  "expected_head_revision_fingerprint": None},
        )
    assert preview.status_code == 200
    assert preview.json()["can_modify"] is False
    assert calls == []
