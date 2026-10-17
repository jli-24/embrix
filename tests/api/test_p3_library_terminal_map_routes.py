"""P3 formal HTTP current original/Review gates."""
import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from embedded_copilot.api.desktop_identity import LocalDevelopmentIdentityAdapter
from embedded_copilot.api.project_catalog_routes import router
from tests.project_catalog.test_p3_library_terminal_maps import _runtime


def test_library_formal_api_trusted_owner_strict_candidate_and_stale_original(tmp_path,monkeypatch):
    catalog,detail,values,root=_runtime(tmp_path,monkeypatch)
    app=FastAPI();app.include_router(router);app.state.project_catalog_runtime=catalog
    app.state.desktop_identity_provider=LocalDevelopmentIdentityAdapter(tenant_id=detail.tenant_id,requester=detail.requester)
    @app.middleware("http")
    async def trace(request,call_next):
        request.state.trace_id="p3-api-test"
        return await call_next(request)
    base=f"/api/v1/copilot/projects/{detail.project_id}"
    path=f"{base}/conversations/{detail.conversation_id}/library-terminal-maps"
    body=json.loads(json.dumps(values,default=lambda value:value.model_dump(mode="json")))
    with TestClient(app) as client:
        assert client.get(path).json()==[]
        assert client.post(path,json={**body,"reviewer_id":"spoof"}).status_code==422
        proposed=client.post(path,json=body);assert proposed.status_code==200,proposed.text
        evidence=proposed.json()["evidence"]
        assert client.get(path).json()[0]["currentness"]=="REVIEW"
        review_path=f"{base}/library-terminal-maps/{evidence['evidence_id']}/review"
        review_body={"evidence_fingerprint":evidence["fingerprint"],"decision":"APPROVED"}
        reviewed=client.post(review_path,json=review_body);assert reviewed.status_code==200,reviewed.text
        assert reviewed.json()["review"]["reviewer_id"]==detail.requester
        assert client.get(path).json()[0]["currentness"]=="APPROVED"
        app.state.desktop_identity_provider=LocalDevelopmentIdentityAdapter(tenant_id=detail.tenant_id,requester="other")
        assert client.get(path).status_code==403
        assert client.post(review_path,json=review_body).status_code==403
        app.state.desktop_identity_provider=LocalDevelopmentIdentityAdapter(tenant_id=detail.tenant_id,requester=detail.requester)
        (root/"footprint.kicad_mod").write_bytes(b"changed")
        assert client.get(path).json()[0]["currentness"]=="STALE"
        assert client.post(review_path,json=review_body).status_code==503
