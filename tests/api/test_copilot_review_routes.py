from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from embedded_copilot.api.copilot_review_routes import router


HEADERS = {
    "X-Copilot-Requester": "engineer-1",
    "X-Environment-Tenant": "tenant-1",
}
FP = "sha256:" + "a" * 64


class _ReviewPort:
    def __init__(self) -> None:
        self.request = None
        self.decision = None

    def create_review(self, *, tenant_id: str, requester: str, request):
        assert tenant_id == "tenant-1"
        assert requester == "engineer-1"
        self.request = request
        return request

    def get_review(self, *, tenant_id: str, project_id: str, review_id: str):
        assert tenant_id == "tenant-1"
        if self.request is None or self.request.review_id != review_id:
            return None
        return self.decision or self.request

    def decide(self, *, tenant_id: str, decision):
        assert tenant_id == "tenant-1"
        assert self.request is not None
        assert decision.project_id == self.request.project_id
        self.decision = type(
            "Record",
            (),
            {
                "review_request": self.request,
                "decision": decision,
                "status": type("Status", (), {"value": "DECIDED"})(),
            },
        )()
        return self.decision


def _app(port=None) -> FastAPI:
    app = FastAPI()
    app.state.copilot_review_port = port

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-review"
        return await call_next(request)

    app.include_router(router)
    return app


def test_review_lifecycle_is_identity_bound_and_does_not_execute() -> None:
    port = _ReviewPort()
    with TestClient(_app(port)) as client:
        created = client.post(
            "/api/v1/copilot/reviews",
            headers=HEADERS,
            json={
                "project_id": "project-1",
                "target_type": "KNOWLEDGE",
                "target_id": "knowledge-1",
                "target_fingerprint": FP,
            },
        )
        review_id = created.json()["review_id"]
        decided = client.post(
            f"/api/v1/copilot/reviews/{review_id}/decision",
            headers=HEADERS,
            json={
                "project_id": "project-1",
                "decision": "APPROVE",
                "reviewer": "reviewer-1",
                "comment": "Reviewed evidence.",
                "decided_at": "2026-08-13T12:00:00+00:00",
            },
        )

    assert created.status_code == 200
    assert created.json()["status"] == "OPEN"
    assert decided.status_code == 200
    assert decided.json()["status"] == "DECIDED"


def test_review_requires_port_and_identity() -> None:
    with TestClient(_app()) as client:
        unavailable = client.post(
            "/api/v1/copilot/reviews",
            headers=HEADERS,
            json={
                "project_id": "project-1",
                "target_type": "KNOWLEDGE",
                "target_id": "knowledge-1",
                "target_fingerprint": FP,
            },
        )
        missing_identity = client.post(
            "/api/v1/copilot/reviews",
            headers={"X-Environment-Tenant": "tenant-1"},
            json={
                "project_id": "project-1",
                "target_type": "KNOWLEDGE",
                "target_id": "knowledge-1",
                "target_fingerprint": FP,
            },
        )

    assert unavailable.status_code == 503
    assert unavailable.json()["error"] == "REVIEW_UNAVAILABLE"
    assert missing_identity.status_code == 401
