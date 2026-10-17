from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from embedded_copilot.api.copilot_review_routes import router as review_router
from embedded_copilot.api.copilot_validation_proposal_routes import router
from tests.engineering_validation_proposal.test_service import _service


HEADERS = {
    "X-Copilot-Requester": "engineer-1",
    "X-Environment-Tenant": "tenant-1",
}
FP = "sha256:" + "a" * 64
PROJECT_FP = "sha256:" + "b" * 64


def _app(service) -> FastAPI:
    app = FastAPI()
    app.state.engineering_validation_proposal_service = service

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-v64"
        return await call_next(request)

    app.include_router(router)
    app.include_router(review_router)
    return app


def test_validation_proposal_and_review_api_is_additive_and_read_only() -> None:
    service = _service(facts=(("firmware.sensor_address", "0x69"), ("hardware.sensor_address", "0x68")))
    with TestClient(_app(service)) as client:
        validation = client.post(
            "/api/v1/copilot/validations",
            headers=HEADERS,
            json={
                "validation_id": "validation-api-1",
                "project_id": "project-1",
                "project_fingerprint": PROJECT_FP,
                "validation_type": "CROSS_DOMAIN_CONSISTENCY",
                "target_identity": {"identity": "sensor-1", "kind": "firmware"},
                "target_fingerprint": FP,
                "evidence_ids": ["evidence-1"],
                "memory_ids": [],
                "knowledge_ids": [],
                "requested_at": "2026-08-15T12:00:00+00:00",
            },
        )
        assert validation.status_code == 200
        finding_id = validation.json()["findings"][0]["finding_id"]
        proposal = client.post(
            "/api/v1/copilot/proposals",
            headers=HEADERS,
            json={"project_id": "project-1", "finding_id": finding_id},
        )
        assert proposal.status_code == 200
        proposal_body = proposal.json()
        assert proposal_body["status"] == "DRAFT"
        assert "proposed_changes" in proposal_body
        assert "command" not in proposal_body

        review = client.post(
            "/api/v1/copilot/reviews",
            headers=HEADERS,
            json={
                "project_id": "project-1",
                "target_type": "ENGINEERING_PROPOSAL",
                "target_id": proposal_body["proposal_id"],
                "target_fingerprint": proposal_body["proposal_fingerprint"],
            },
        )
        assert review.status_code == 200
        assert review.json()["status"] == "WAIT_REVIEW"

        decision = client.post(
            f"/api/v1/copilot/reviews/{review.json()['review_id']}/decision",
            headers=HEADERS,
            json={
                "project_id": "project-1",
                "decision": "APPROVE",
                "reviewer": "reviewer-1",
                "comment": "Approve proposal for future controlled execution.",
                "decided_at": "2026-08-15T12:01:00+00:00",
            },
        )
        assert decision.status_code == 200
        assert decision.json()["status"] == "APPROVED"
        assert service.get_proposal("tenant-1", "project-1", proposal_body["proposal_id"]).status.value == "APPROVED"
