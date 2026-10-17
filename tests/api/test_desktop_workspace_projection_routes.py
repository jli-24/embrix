from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from embedded_copilot.api.desktop_identity import TrustedDesktopIdentity
from embedded_copilot.api.desktop_workspace_projection_routes import router
from embedded_copilot.desktop_workspace_projection import (
    DesktopWorkspaceProjectionService,
)
from embedded_copilot.project_catalog import ProjectCatalogForbidden


class _Catalog:
    def get_project(self, project_id: str, *, tenant_id: str):
        return SimpleNamespace(
            fingerprint="sha256:" + "1" * 64,
            model_dump=lambda mode="json": {
                "name": "Synthetic",
                "relative_path": "synthetic",
                "status": "READY",
                "initialization_status": "READY",
                "fingerprint": "sha256:" + "1" * 64,
            },
        )

    def get_project_for_requester(self, project_id: str, *, tenant_id: str, requester: str):
        if requester != "requester-a":
            raise ProjectCatalogForbidden("project requester mismatch")
        return self.get_project(project_id, tenant_id=tenant_id)

    def context(self, project_id: str, *, tenant_id: str):
        return SimpleNamespace(model_dump=lambda mode="python": {"groups": ()})

    def conversations(self, project_id: str, *, tenant_id: str, requester: str, cursor=None, limit=20):
        assert limit <= 10
        return SimpleNamespace(items=())

    def workbench_resources(self, project_id: str, *, tenant_id: str, limit: int = 500):
        return (({
            "resource_id": "resource-1",
            "parent_id": None,
            "name": "main.c",
            "relative_path": "Core/main.c",
            "node_type": "FILE",
            "category": "FIRMWARE",
            "language": "C",
            "source_fingerprint": "sha256:" + "2" * 64,
        },), False)


class _Validation:
    def list_findings(self, tenant_id: str, project_id: str, *, limit: int):
        return (SimpleNamespace(model_dump=lambda mode="python": {
            "finding_id": "finding-1",
            "finding_type": "RULE",
            "severity": "WARNING",
            "subject": "工程发现",
            "summary": "摘要",
            "evidence_ids": ["evidence-1"],
        }),)

    def list_proposals(self, tenant_id: str, project_id: str, *, limit: int):
        return ()


def _app(service: object | None, *, requester: str = "requester-a") -> FastAPI:
    app = FastAPI()
    app.state.desktop_workspace_projection = service
    app.state.desktop_identity_provider = type(
        "Provider",
        (),
        {"resolve": lambda self, request: TrustedDesktopIdentity(requester, "tenant-a", "TEST")},
    )()

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-projection"
        return await call_next(request)

    app.include_router(router)
    return app


def test_workspace_projection_is_additive_bounded_and_read_only() -> None:
    service = DesktopWorkspaceProjectionService(catalog=_Catalog(), capabilities=())
    with TestClient(_app(service)) as client:
        response = client.get(
            "/api/v1/copilot/desktop/projects/project-a/workspace?limit=10",
            headers={"X-Copilot-Requester": "requester-a", "X-Environment-Tenant": "tenant-a"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "v6.7"
    assert body["identity"] == {
        "tenant_id": "tenant-a",
        "requester_id": "requester-a",
        "project_id": "project-a",
        "project_fingerprint": "sha256:" + "1" * 64,
    }
    assert body["conversations"]["limit"] == 10
    assert "absolute_path" not in response.text
    assert "provider_payload" not in response.text
    assert "secret" not in response.text.casefold()


def test_workspace_projection_accepts_explicit_task_scope_without_claiming_completion() -> None:
    service = DesktopWorkspaceProjectionService(catalog=_Catalog(), capabilities=())
    with TestClient(_app(service)) as client:
        response = client.get("/api/v1/copilot/desktop/projects/project-a/workspace?limit=10&task_id=task-current")
    assert response.status_code == 200
    assert response.json()["workflow_summary"]["status"] == "UNAVAILABLE"


def test_workspace_projection_fails_closed_without_production_service() -> None:
    with TestClient(_app(None)) as client:
        response = client.get(
            "/api/v1/copilot/desktop/projects/project-a/workspace",
            headers={"X-Copilot-Requester": "requester-a", "X-Environment-Tenant": "tenant-a"},
        )
    assert response.status_code == 503
    assert response.json()["error"] == "DESKTOP_WORKSPACE_PROJECTION_UNAVAILABLE"


def test_workbench_projection_is_additive_and_metadata_only() -> None:
    service = DesktopWorkspaceProjectionService(catalog=_Catalog(), validation=_Validation())
    with TestClient(_app(service)) as client:
        response = client.get(
            "/api/v1/copilot/desktop/projects/project-a/workbench?limit=10",
            headers={"X-Copilot-Requester": "requester-a", "X-Environment-Tenant": "tenant-a"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "workbench-v1"
    assert body["resources"][0]["relative_path"] == "Core/main.c"
    assert body["findings"][0]["finding_id"] == "finding-1"
    assert "absolute_path" not in response.text
    assert "provider_payload" not in response.text


def test_workbench_projection_fails_closed_without_production_service() -> None:
    with TestClient(_app(None)) as client:
        response = client.get(
            "/api/v1/copilot/desktop/projects/project-a/workbench",
            headers={"X-Copilot-Requester": "requester-a", "X-Environment-Tenant": "tenant-a"},
        )
    assert response.status_code == 503
    assert response.json()["error"] == "DESKTOP_WORKBENCH_PROJECTION_UNAVAILABLE"


@pytest.mark.parametrize("projection", ["workspace", "workbench"])
def test_projection_rejects_non_owner_in_same_tenant(projection: str) -> None:
    service = DesktopWorkspaceProjectionService(catalog=_Catalog(), validation=_Validation())
    with TestClient(_app(service, requester="requester-b")) as client:
        response = client.get(f"/api/v1/copilot/desktop/projects/project-a/{projection}?limit=10")

    assert response.status_code == 403
    assert response.json()["error"] == "PROJECT_ACCESS_DENIED"


def test_workspace_projection_uses_selected_conversation_owner_read() -> None:
    class SelectedCatalog(_Catalog):
        def conversation(self, project_id: str, conversation_id: str, *, tenant_id: str, requester: str):
            if conversation_id != "conversation-owned":
                raise LookupError("conversation missing")
            return SimpleNamespace(
                project_id=project_id,
                conversation_id=conversation_id,
                requirement_context=None,
                architecture_design=None,
                schematic_planning=None,
                pcb_engineering_state=None,
                firmware_spec=None,
                turns=(),
            )

        def current_circuit_intents(self, *, tenant_id: str, requester: str, project_id: str):
            return ()

    service = DesktopWorkspaceProjectionService(catalog=SelectedCatalog())
    with TestClient(_app(service)) as client:
        response = client.get(
            "/api/v1/copilot/desktop/projects/project-a/workspace?limit=10&conversation_id=conversation-owned"
        )
        missing = client.get(
            "/api/v1/copilot/desktop/projects/project-a/workspace?limit=10&conversation_id=conversation-elsewhere"
        )

    assert response.status_code == 200
    facts = response.json()["authoritative_facts"]
    assert {item["domain"] for item in facts} == {
        "REQUIREMENT", "ARCHITECTURE", "CIRCUIT", "SCHEMATIC", "PCB", "FIRMWARE", "EVIDENCE"
    }
    assert all(item["currentness"] == "UNAVAILABLE" for item in facts)
    assert missing.status_code == 404
