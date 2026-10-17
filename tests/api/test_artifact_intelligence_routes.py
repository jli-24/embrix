from __future__ import annotations

import base64
import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from embedded_copilot.api.copilot_review_routes import router as review_router
from embedded_copilot.api.project_catalog_routes import router as catalog_router
from embedded_copilot.project_catalog import ProjectCatalogRuntime

HEADERS = {
    "X-Copilot-Requester": "engineer-1",
    "X-Environment-Tenant": "tenant-1",
}


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


class ReviewPort:
    def __init__(self) -> None:
        self.request = None
        self.record = None

    def create_review(self, *, tenant_id: str, requester: str, request):
        assert tenant_id == "tenant-1"
        assert requester == "engineer-1"
        self.request = request
        return request

    def get_review(self, *, tenant_id: str, project_id: str, review_id: str):
        assert tenant_id == "tenant-1"
        if self.request is None or self.request.project_id != project_id or self.request.review_id != review_id:
            return None
        return self.record or self.request

    def decide(self, *, tenant_id: str, decision):
        assert tenant_id == "tenant-1"
        self.record = type(
            "ReviewRecord",
            (),
            {
                "review_request": self.request,
                "decision": decision,
                "status": type("Status", (), {"value": "DECIDED"})(),
            },
        )()
        return self.record


def _app(tmp_path: Path, port: ReviewPort | None = None) -> FastAPI:
    root = tmp_path / "projects"
    root.mkdir()
    app = FastAPI()
    app.state.project_catalog_runtime = ProjectCatalogRuntime(
        project_root=root,
        state_database=tmp_path / "state" / "desktop.sqlite3",
        metadata_directory=tmp_path / "metadata",
        clock=Clock(),
        initialize_async=False,
    )
    app.state.copilot_review_port = port

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-artifact"
        return await call_next(request)

    app.include_router(catalog_router)
    app.include_router(review_router)
    return app


def _conversation(client: TestClient) -> tuple[str, str]:
    preview = client.post(
        "/api/v1/copilot/projects/previews",
        headers=HEADERS,
        json={"name": "Robot", "relative_path": "robot", "project_type": "STM32"},
    )
    assert preview.status_code == 200
    project = client.post(
        "/api/v1/copilot/projects",
        headers=HEADERS,
        json={
            "preview_id": preview.json()["preview_id"],
            "preview_fingerprint": preview.json()["fingerprint"],
            "confirmed_at": "2026-09-02T12:00:00Z",
        },
    )
    assert project.status_code == 202
    project_id = project.json()["project_id"]
    conversation = client.post(
        f"/api/v1/copilot/projects/{project_id}/conversations",
        headers=HEADERS,
        json={},
    )
    assert conversation.status_code == 201
    return project_id, conversation.json()["conversation"]["conversation_id"]


def _bom_zip() -> str:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("BOM.csv", "References,Value\nU1,STM32F103C8T6\n")
    return base64.b64encode(output.getvalue()).decode("ascii")


def test_candidate_evidence_is_visible_but_cannot_create_truth(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        project_id, conversation_id = _conversation(client)
        extracted = client.post(
            f"/api/v1/copilot/projects/{project_id}/conversations/{conversation_id}/artifact-intelligence",
            headers=HEADERS,
            json={"zip_base64": _bom_zip()},
        )
        repeated = client.post(
            f"/api/v1/copilot/projects/{project_id}/conversations/{conversation_id}/artifact-intelligence",
            headers=HEADERS,
            json={"zip_base64": _bom_zip()},
        )
        truth = client.post(
            f"/api/v1/copilot/projects/{project_id}/conversations/{conversation_id}/artifact-intelligence/design-truth",
            headers=HEADERS,
        )

    assert extracted.status_code == 200
    assert extracted.json()["evidence_review_status"] == "CANDIDATE"
    assert repeated.status_code == 200
    assert repeated.json()["evidence_review_status"] == "CANDIDATE"
    assert extracted.json()["available_evidence"] == ["U1 STM32F103C8T6"]
    assert "sha256:" not in extracted.text
    assert "artifact_id" not in extracted.text
    assert "path_reference" not in extracted.text
    assert truth.status_code == 422
    assert truth.json()["error"] == "EVIDENCE_REVIEW_REQUIRED"


def test_approved_evidence_review_enables_reverse_truth_only(tmp_path: Path) -> None:
    port = ReviewPort()
    with TestClient(_app(tmp_path, port)) as client:
        project_id, conversation_id = _conversation(client)
        extracted = client.post(
            f"/api/v1/copilot/projects/{project_id}/conversations/{conversation_id}/artifact-intelligence",
            headers=HEADERS,
            json={"zip_base64": _bom_zip()},
        )
        assert extracted.status_code == 200
        review_id = port.request.review_id
        repeated = client.post(
            f"/api/v1/copilot/projects/{project_id}/conversations/{conversation_id}/artifact-intelligence",
            headers=HEADERS,
            json={"zip_base64": _bom_zip()},
        )
        decided = client.post(
            f"/api/v1/copilot/reviews/{review_id}/decision",
            headers=HEADERS,
            json={
                "project_id": project_id,
                "decision": "APPROVE",
                "reviewer": "reviewer-1",
                "comment": "Evidence reviewed.",
                "decided_at": datetime.now(UTC).isoformat(),
            },
        )
        truth = client.post(
            f"/api/v1/copilot/projects/{project_id}/conversations/{conversation_id}/artifact-intelligence/design-truth",
            headers=HEADERS,
        )

    assert decided.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json()["evidence_review_status"] == "REVIEWING"
    assert truth.status_code == 200
    assert truth.json()["origin"] == "REVERSE"
    assert "组件：STM32F103C8T6" in truth.json()["confirmed_mapping"]
    assert "truth_id" not in truth.text
    assert "fingerprint" not in truth.text
