from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from embedded_copilot.api.project_catalog_routes import router
from tests.project_catalog.test_firmware_persistence import _persist_design_stack

HEADERS = {
    "X-Copilot-Requester": "engineer-1",
    "X-Environment-Tenant": "tenant-1",
}


def test_existing_conversation_api_projects_authorization(tmp_path: Path) -> None:
    service, project, session, _ = _persist_design_stack(tmp_path)
    application = FastAPI()
    application.state.project_catalog_runtime = service

    @application.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-authorization"
        return await call_next(request)

    application.include_router(router)
    with TestClient(application) as client:
        response = client.get(
            f"/api/v1/copilot/projects/{project.project_id}/conversations/"
            f"{session.conversation.conversation_id}",
            headers=HEADERS,
        )

    assert response.status_code == 200
    payload = response.json()
    authorization = payload["execution_authorization"]
    assert authorization["capability_evaluation_id"] == payload["capability_evaluation"]["evaluation_id"]
    assert len(authorization["requests"]) == len(payload["execution_intents"])
    assert {item["decision"] for item in authorization["decisions"]} == {"PENDING"}

