from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from embedded_copilot.api.project_catalog_routes import router
from tests.project_catalog.test_schematic_verification_record import (
    _s02_generated_catalog,
)


def test_verification_api_rejects_client_pass_and_hides_artifact_path(tmp_path) -> None:
    catalog, project_id, conversation_id, selector, plan, artifact_fp, _ = _s02_generated_catalog(tmp_path)
    app = FastAPI()
    app.state.project_catalog_runtime = catalog

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-s02"
        return await call_next(request)

    app.include_router(router)
    url = f"/api/v1/copilot/projects/{project_id}/conversations/{conversation_id}/schematic-verifications"
    payload = {
        "intent_id": selector[0], "intent_revision": selector[1],
        "intent_fingerprint": selector[2], "plan_id": plan.plan_id,
        "plan_fingerprint": plan.fingerprint, "artifact_fingerprint": artifact_fp,
        "generation_operation_id": "s02-write", "erc_operation_id": "s02-erc",
    }
    headers = {"X-Copilot-Requester": "engineer-1", "X-Environment-Tenant": "tenant-1"}
    with TestClient(app) as client:
        injected = client.post(url, json={**payload, "aggregate_status": "SCHEMATIC_VERIFIED"}, headers=headers)
        assert injected.status_code == 422
        response = client.post(url, json=payload, headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["record"]["erc_status"] == "PASS"
        assert body["record"]["aggregate_status"] == "BLOCKED"
        assert body["currentness"] == "CURRENT"
        assert "hardware/main.kicad_sch" not in response.text
        fetched = client.get(
            f"/api/v1/copilot/projects/{project_id}/schematic-verifications/{body['record']['verification_id']}",
            headers=headers,
        )
        assert fetched.status_code == 200, fetched.text
        assert fetched.json() == body
        listed = client.get(url, headers=headers)
        assert listed.status_code == 200, listed.text
        assert listed.json() == [body]
        assert client.get(url + "?limit=101", headers=headers).status_code == 422
        assert client.get(url, headers={**headers, "X-Copilot-Requester": "other-user"}).status_code == 403
