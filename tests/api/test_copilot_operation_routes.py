from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from embedded_copilot.api.copilot_operation_routes import router


HEADERS = {
    "X-Copilot-Requester": "engineer-1",
    "X-Environment-Tenant": "tenant-1",
}
FP = "sha256:" + "a" * 64


class _OperationPort:
    def __init__(self) -> None:
        self.value = None

    def create_operation(self, *, tenant_id: str, requester: str, request):
        assert tenant_id == "tenant-1"
        assert requester == "engineer-1"
        self.value = {
            "operation_id": "operation-1",
            "project_id": request.project_id,
            "target_id": request.target_id,
            "capability": request.capability,
            "status": "WAIT_HUMAN",
            "proposal_fingerprint": request.proposal_fingerprint,
            "reason_code": "APPROVAL_REQUIRED",
            "detail": "A human approval is required before any worker can run.",
            "approval_required": True,
        }
        return self.value

    def get_operation(self, *, tenant_id: str, project_id: str, operation_id: str):
        assert tenant_id == "tenant-1"
        if self.value is None or self.value["operation_id"] != operation_id:
            return None
        return self.value


def _app(port=None) -> FastAPI:
    app = FastAPI()
    app.state.copilot_operation_port = port

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-operation"
        return await call_next(request)

    app.include_router(router)
    return app


def test_operation_is_identity_bound_and_waits_for_human() -> None:
    port = _OperationPort()
    with TestClient(_app(port)) as client:
        response = client.post(
            "/api/v1/copilot/operations",
            headers=HEADERS,
            json={
                "project_id": "project-1",
                "target_id": "board-1",
                "capability": "FLASH",
                "proposal_fingerprint": FP,
            },
        )
        fetched = client.get(
            "/api/v1/copilot/operations/operation-1?project_id=project-1",
            headers=HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["status"] == "WAIT_HUMAN"
    assert fetched.status_code == 200
    assert fetched.json()["project_id"] == "project-1"


def test_operation_rejects_command_and_path_fields() -> None:
    with TestClient(_app(_OperationPort())) as client:
        response = client.post(
            "/api/v1/copilot/operations",
            headers=HEADERS,
            json={
                "project_id": "project-1",
                "target_id": "board-1",
                "capability": "BUILD",
                "proposal_fingerprint": FP,
                "command": "west build",
                "working_directory": "C:\\project",
            },
        )
    assert response.status_code == 422


def test_operation_is_unavailable_without_explicit_port() -> None:
    with TestClient(_app()) as client:
        response = client.post(
            "/api/v1/copilot/operations",
            headers=HEADERS,
            json={
                "project_id": "project-1",
                "target_id": "board-1",
                "capability": "FLASH",
                "proposal_fingerprint": FP,
            },
        )
    assert response.status_code == 503
    assert response.json()["error"] == "OPERATION_UNAVAILABLE"
