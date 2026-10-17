"""P2 formal Catalog HTTP with trusted identity and current source gates."""
from __future__ import annotations

import json
from fastapi import FastAPI
from fastapi.testclient import TestClient

from embedded_copilot.api.desktop_identity import LocalDevelopmentIdentityAdapter
from embedded_copilot.api.project_catalog_routes import router
from embedded_copilot.project_catalog.models import StandardApplicabilityEvidenceV2
from tests.project_catalog.test_p2_standard_applicability import _runtime, _values


def test_standard_formal_api_current_review_and_strict_identity(tmp_path, monkeypatch):
    catalog, detail, _circuit, _source = _runtime(tmp_path, monkeypatch)
    app = FastAPI()
    @app.middleware("http")
    async def trace(request, call_next):
        request.state.trace_id = "p2-api-test"
        return await call_next(request)
    app.include_router(router)
    app.state.project_catalog_runtime = catalog
    app.state.desktop_identity_provider = LocalDevelopmentIdentityAdapter(
        tenant_id=detail.tenant_id, requester=detail.requester)
    base = f"/api/v1/copilot/projects/{detail.project_id}"
    path = f"{base}/conversations/{detail.conversation_id}/standard-applicability"
    payload = _values(catalog, detail)
    body = json.loads(json.dumps(payload, default=lambda value: value.model_dump(mode="json")))
    with TestClient(app) as client:
        assert client.get(path).json() == []
        for extra in ("tenant_id", "reviewer_id", "status", "schema_version"):
            assert client.post(path, json={**body, extra: "spoof"}).status_code == 422
        proposed = client.post(path, json=body, headers={"X-Environment-Tenant": "spoof"})
        assert proposed.status_code == 200, proposed.text
        evidence = proposed.json()["evidence"]
        assert evidence["tenant_id"] == detail.tenant_id
        assert StandardApplicabilityEvidenceV2.model_validate_json(json.dumps(evidence)).fingerprint == evidence["fingerprint"]
        assert client.get(path).json()[0]["currentness"] == "REVIEW"
        review_path = f"{base}/standard-applicability/{evidence['evidence_id']}/review"
        review_body = {"evidence_fingerprint": evidence["fingerprint"], "decision": "APPROVED"}
        assert client.post(review_path, json={**review_body, "reviewer_id": "spoof"}).status_code == 422
        assert client.post(review_path, json={**review_body, "evidence_fingerprint": "sha256:" + "0" * 64}).status_code == 409
        reviewed = client.post(review_path, json=review_body)
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["review"]["reviewer_id"] == detail.requester
        assert client.get(path).json()[0]["currentness"] == "APPROVED"
        app.state.desktop_identity_provider = LocalDevelopmentIdentityAdapter(
            tenant_id=detail.tenant_id, requester="other")
        assert client.get(path).status_code == 403
        assert client.post(review_path, json=review_body).status_code == 403
        app.state.desktop_identity_provider = LocalDevelopmentIdentityAdapter(
            tenant_id=detail.tenant_id, requester=detail.requester)
        monkeypatch.setattr(catalog, "current_circuit_intents", lambda *a, **k: ())
        assert client.get(path).json()[0]["currentness"] == "STALE"
        assert client.post(review_path, json=review_body).status_code == 409
