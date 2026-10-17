"""C2-I route boundaries; all project data lives under pytest's temporary root."""

from __future__ import annotations

import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from embedded_copilot.api.copilot_execution_routes import router as execution_router
from embedded_copilot.api.main import create_app
from embedded_copilot.api.project_catalog_routes import router as catalog_router
from embedded_copilot.core.config import Settings
from embedded_copilot.execution_worker.contracts import OperationType, PCBWriteOperation
from embedded_copilot.project_catalog import (
    ProjectCatalogRuntime,
    ProjectCreationRequest,
    ProjectType,
)
from tests.project_catalog.test_c2i_native_move_runtime import _catalog as _c2i_catalog

OWNER = {"X-Copilot-Requester": "owner-1", "X-Environment-Tenant": "tenant-1"}
OTHER = {"X-Copilot-Requester": "other-1", "X-Environment-Tenant": "tenant-1"}
FP = "sha256:" + "1" * 64
UUID = "01234567-89ab-4cde-8f01-23456789abcd"


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 9, 30, 8, 0, tzinfo=UTC)


def _app(tmp_path: Path) -> tuple[FastAPI, str, Path]:
    root = tmp_path / "projects"
    root.mkdir()
    database = tmp_path / "catalog.sqlite3"
    catalog = ProjectCatalogRuntime(
        project_root=root, state_database=database, clock=Clock(), initialize_async=False
    )
    preview = catalog.preview(
        ProjectCreationRequest(name="Board", relative_path="board", project_type=ProjectType.STM32),
        tenant_id="tenant-1", requester="owner-1",
    )
    project = catalog.create(
        preview.preview_id, preview.fingerprint, confirmed_at=Clock().now(),
        tenant_id="tenant-1", requester="owner-1",
    )
    app = FastAPI()
    app.state.project_catalog_runtime = catalog

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-c2i"
        return await call_next(request)

    app.include_router(catalog_router)
    app.include_router(execution_router)
    return app, project.project_id, database


def _candidate_body() -> dict[str, object]:
    return {
        "import_revision_id": "eda-import-selected",
        "import_revision_fingerprint": FP,
        "workspace_target_id": "project-root",
        "target_native_uuid": UUID,
        "after": {"x_mm": 11.0, "y_mm": 20.0},
    }


def _candidate_count(database: Path) -> int:
    with sqlite3.connect(database) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='project_eda_native_move_candidates'"
        ).fetchone()
        if table is None:
            return 0
        return connection.execute("SELECT count(*) FROM project_eda_native_move_candidates").fetchone()[0]


def test_c2i_anonymous_and_non_owner_rejected_before_source_read_or_candidate_write(
    tmp_path: Path,
) -> None:
    app, project_id, database = _app(tmp_path)
    catalog = app.state.project_catalog_runtime
    reads: list[str] = []

    def forbidden_scan(*args, **kwargs):
        reads.append("source_scan")
        raise AssertionError("source read preceded Owner authorization")

    catalog._artifact_intelligence.scan_eda_import_directory = forbidden_scan
    url = f"/api/v1/copilot/projects/{project_id}/eda-native/footprint-move-candidates"
    with TestClient(app) as client:
        assert client.post(url, json=_candidate_body()).status_code == 401
        assert client.post(url, headers=OTHER, json=_candidate_body()).status_code == 403
    assert reads == []
    assert _candidate_count(database) == 0


def test_c2i_candidate_request_rejects_client_authoritative_fields(tmp_path: Path) -> None:
    app, project_id, database = _app(tmp_path)
    url = f"/api/v1/copilot/projects/{project_id}/eda-native/footprint-move-candidates"
    forged = (
        {"objects": [{"native_id": UUID}]},
        {"source_set_fingerprint": FP},
        {"manifest": []},
        {"before": {"x_mm": 10.0, "y_mm": 20.0}},
        {"operation_type": "KICAD_PCB_WRITE"},
        {"verified": True},
    )
    with TestClient(app) as client:
        for fields in forged:
            response = client.post(url, headers=OWNER, json={**_candidate_body(), **fields})
            assert response.status_code == 422, fields
    assert _candidate_count(database) == 0


def test_c2i_old_pcb_write_route_cannot_create_native_move(tmp_path: Path) -> None:
    app, project_id, database = _app(tmp_path)
    calls: list[str] = []

    def forbidden_legacy_bridge(*args, **kwargs):
        calls.append("legacy_bridge")
        raise AssertionError("C2 request entered legacy PCB production")

    app.state.project_catalog_runtime.decide_pcb_production = forbidden_legacy_bridge
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/copilot/projects/{project_id}/conversations/conversation-1/pcb-production/decision",
            headers=OWNER,
            json={**_candidate_body(), "operation_type": "KICAD_PCB_WRITE"},
        )
    assert response.status_code == 422
    assert calls == []
    try:
        PCBWriteOperation.model_validate({**_candidate_body(), "operation_type": "KICAD_PCB_WRITE"})
    except (TypeError, ValueError):
        pass
    else:
        raise AssertionError("legacy whole-board operation accepted native move request")
    assert _candidate_count(database) == 0


def test_c2i_generic_approval_route_never_enqueues_native_move(tmp_path: Path) -> None:
    app, project_id, database = _app(tmp_path)
    calls: list[str] = []

    class Port:
        def get_operation(self, **kwargs):
            calls.append("read_operation")
            return SimpleNamespace(operation_type=OperationType.KICAD_NATIVE_FOOTPRINT_MOVE)

        def issue_approval_for_id(self, **kwargs):
            calls.append("issue_approval")
            raise AssertionError("native move reached generic approval")

        def enqueue_by_id(self, **kwargs):
            calls.append("enqueue")
            raise AssertionError("native move reached generic queue")

    app.state.execution_operation_port = Port()
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/copilot/operations/native-move-1/approval",
            params={"project_id": project_id}, headers=OWNER,
            json={
                "reviewer_id": "owner-1", "nonce": "nonce-1",
                "expires_at": "2026-10-01T08:00:00+00:00",
            },
        )
    assert response.status_code == 503
    assert response.json()["error"] == "NATIVE_MOVE_UNAVAILABLE"
    assert calls == ["read_operation"]
    assert _candidate_count(database) == 0


def test_c2i_candidate_projection_contains_reviewable_source_and_exact_diff(tmp_path: Path) -> None:
    catalog, project_id, _, revision = _c2i_catalog(tmp_path)
    app = FastAPI()
    app.state.project_catalog_runtime = catalog

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-c2i"
        return await call_next(request)

    app.include_router(catalog_router)
    body = {
        **_candidate_body(),
        "import_revision_id": revision["import_revision_id"],
        "import_revision_fingerprint": revision["import_revision_fingerprint"],
    }
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/copilot/projects/{project_id}/eda-native/footprint-move-candidates",
            headers=OWNER, json=body,
        )
    assert response.status_code == 200, response.text
    candidate = response.json()
    assert candidate["project_id"] == project_id and candidate["backend"] == "KICAD"
    assert candidate["source_set_fingerprint"] == revision["source_set_fingerprint"]
    assert candidate["manifest_fingerprint"].startswith("sha256:")
    assert candidate["parser_ruleset_id"] == "kicad-pcb-footprint-20240108-r2"
    assert candidate["supported_subset_id"] == "kicad-footprint-placement-r1"
    assert [{key: item[key] for key in original} for item, original in zip(candidate["manifest"], revision["manifest"])] == revision["manifest"]
    assert candidate["issues"] == revision["issues"]
    assert candidate["workspace_target_id"] == "project-root"
    assert candidate["pcb_sha256"] == "sha256:" + revision["manifest"][0]["sha256"]
    assert candidate["target_native_uuid"] == UUID
    assert candidate["rotation_degrees"] == 90.0
    assert len(candidate["edits"]) == 2
    assert all(int(edit["end"]) > int(edit["start"]) for edit in candidate["edits"])
    assert candidate["can_request_operation_approval"] is False
    path = f"/api/v1/copilot/projects/{project_id}/eda-native/footprint-move-candidates/{candidate['candidate_id']}"
    with TestClient(app) as client:
        review_response = client.post(
            path + "/mapping-review", headers=OWNER,
            json={"candidate_fingerprint": candidate["candidate_fingerprint"],
                  "decision": "ACCEPT_MAPPING", "design_intent": "Move selected footprint x by 1 mm."},
        )
        assert review_response.status_code == 200
        review = review_response.json()
        assert review["candidate_fingerprint"] == candidate["candidate_fingerprint"]
        detail = client.get(
            f"/api/v1/copilot/projects/{project_id}/eda-native/mapping-reviews/{review['mapping_review_id']}",
            params={"fingerprint": review["mapping_review_fingerprint"]}, headers=OWNER,
        )
        assert detail.status_code == 200 and detail.json() == review
        blocked = client.post(
            path + "/operations", headers=OWNER,
            json={"candidate_fingerprint": candidate["candidate_fingerprint"],
                  "mapping_review_id": review["mapping_review_id"],
                  "mapping_review_fingerprint": review["mapping_review_fingerprint"]},
        )
    assert blocked.status_code == 422
    assert blocked.json()["error"] == "PRESERVATION_UNPROVEN"


def test_c2i_production_composition_keeps_native_move_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import embedded_copilot.api.main as main
    from embedded_copilot.execution_worker.registry import TrustedToolDiscovery

    attempted_launches: list[str] = []

    def no_launch(*_args, **_kwargs):
        attempted_launches.append("external-tool")
        raise AssertionError("create_app test must never launch an external executable")

    monkeypatch.setattr(main, "discover_kicad_cli", lambda **_kwargs: None)
    monkeypatch.setattr(main, "probe_kicad_cli_version", no_launch)
    monkeypatch.setattr(main, "probe_trusted_tool_version", no_launch)
    monkeypatch.setattr(main, "discover_kicad_footprint_root", no_launch)
    monkeypatch.setattr(main, "discover_kicad_symbol_root", no_launch)
    monkeypatch.setattr(main, "discover_kicad_pcb_drc_command", no_launch)
    monkeypatch.setattr(main, "discover_kicad_pcb_parse_command", no_launch)
    monkeypatch.setattr(TrustedToolDiscovery, "_which", staticmethod(lambda _name: None))
    monkeypatch.setattr(subprocess, "run", no_launch)
    monkeypatch.setattr(subprocess, "Popen", no_launch)
    for name in ("ESP_IDF_BUILD", "CMAKE_NINJA_BUILD", "ARM_NONE_EABI_GCC"):
        monkeypatch.delenv("EMBRIX_TOOLCHAIN_" + name, raising=False)
    catalog, project_id, _, revision = _c2i_catalog(tmp_path)
    app = create_app(
        settings=Settings(
            environment="test", project_root=tmp_path / "projects",
            desktop_state_database=tmp_path / "desktop.sqlite3",
            desktop_project_metadata_directory=tmp_path / "metadata", _env_file=None,
        ),
        project_catalog_runtime=catalog,
    )
    assert f"/api/v1/copilot/projects/{{project_id}}/eda-native/footprint-move-candidates" in app.openapi()["paths"]
    with TestClient(app) as client:
        assert app.state.execution_operation_port._native_move_authorizer is None
        candidate = client.post(
            f"/api/v1/copilot/projects/{project_id}/eda-native/footprint-move-candidates", headers=OWNER,
            json={**_candidate_body(), "import_revision_id": revision["import_revision_id"],
                  "import_revision_fingerprint": revision["import_revision_fingerprint"]},
        )
    assert candidate.status_code == 200
    assert candidate.json()["can_request_operation_approval"] is False
    assert attempted_launches == []
