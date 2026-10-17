from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from embedded_copilot.api.desktop_identity import ProductionLocalSessionIdentityAdapter
from embedded_copilot.api.project_catalog_routes import router
from embedded_copilot.production_release.session import LocalSessionManager
from tests.project_catalog.test_source_replan_contract import _approved_v3_legacy_plan


def client_for(catalog, detail):
    app = FastAPI()
    app.include_router(router)
    app.state.project_catalog_runtime = catalog
    sessions = LocalSessionManager()
    app.state.desktop_identity_provider = ProductionLocalSessionIdentityAdapter(
        sessions
    )

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "selection-test"
        return await call_next(request)

    client = TestClient(app)
    client.cookies.set(
        "EmbeddedCopilotSession",
        sessions.exchange(
            sessions.issue_bootstrap(
                requester=detail.requester, tenant_id=detail.tenant_id
            )
        ),
    )
    return client


def test_formal_http_preview_proposal_approval_revoke_and_read_history(
    tmp_path, monkeypatch
):
    catalog, detail, *_ = _approved_v3_legacy_plan(tmp_path, monkeypatch)
    client = client_for(catalog, detail)
    prefix = f"/api/v1/copilot/projects/{detail.project_id}/conversations/{detail.conversation_id}"
    before_read = catalog._repository._database.read_bytes()
    legacy = client.get(prefix + "/hardware-selection/currentness")
    assert legacy.status_code == 200, legacy.text
    assert (
        legacy.json()["selection"]["proposal"]["fingerprint"]
        == detail.hardware_selection.proposal.fingerprint
    )
    assert catalog._repository._database.read_bytes() == before_read
    preview = client.get(
        prefix + "/hardware-selection/currentness", params={"view": "PREVIEW"}
    )
    assert preview.status_code == 200, preview.text
    data = preview.json()
    head = data["expected_head"]
    choices = [
        next(x for x in data["choices"] if x["slot_id"] == slot)
        for slot in data["required_slots"]
    ]
    body = {
        "schema_version": 2,
        "request_id": "http-replacement",
        "expected_head_revision_id": head[0],
        "expected_head_selection_id": head[1],
        "expected_head_selection_fingerprint": head[2],
        "preview_fingerprint": data["preview_fingerprint"],
        "selections": choices,
    }
    proposed = client.post(prefix + "/hardware-selection/proposal", json=body)
    assert proposed.status_code == 200, proposed.text
    current = proposed.json()["head"]
    current_read = client.get(prefix + "/hardware-selection/currentness")
    assert current_read.status_code == 200, current_read.text
    assert current_read.json()["selection"]["status"] == "REVIEW"
    aggregate = client.get(prefix).json()
    assert aggregate["selection_head"]["revision_id"] == current["revision_id"]
    assert (
        aggregate["hardware_selection"]["status"] == "REVIEW"
        and aggregate["schematic_planning"] is None
    )
    old = client.post(
        prefix + "/hardware-selection/decision", json={"decision": "APPROVED"}
    )
    assert old.status_code == 409
    decision = {
        "schema_version": 2,
        "revision_id": current["revision_id"],
        "selection_id": current["selection_id"],
        "selection_fingerprint": current["selection_fingerprint"],
        "expected_decision_fingerprint": None,
        "decision": "APPROVED",
    }
    approved = client.post(prefix + "/hardware-selection/decision", json=decision)
    assert approved.status_code == 200, approved.text
    approved_read = client.get(prefix + "/hardware-selection/currentness")
    assert approved_read.status_code == 200, approved_read.text
    assert (
        approved_read.json()["selection"]["proposal"]["fingerprint"]
        == current["selection_fingerprint"]
    )
    assert approved_read.json()["selection"]["status"] == "APPROVED"
    decision.update(
        decision="REVOKED",
        expected_decision_fingerprint=approved.json()["head"]["decision_fingerprint"],
    )
    revoked = client.post(prefix + "/hardware-selection/decision", json=decision)
    assert revoked.status_code == 200 and revoked.json()["selection"] is None
    history = client.get(
        prefix + "/hardware-selection/currentness",
        params={"view": "HISTORY", "revision_id": current["revision_id"]},
    )
    assert history.status_code == 200 and history.json()["head"]["status"] == "REVOKED"
    # Authentication and strict selectors are checked before persistent mutation.
    before = catalog._repository._database.read_bytes()
    client.cookies.clear()
    assert (
        client.post(prefix + "/hardware-selection/proposal", json=body).status_code
        == 401
    )
    assert catalog._repository._database.read_bytes() == before
