"""Only synthetic account premises; no provider or EDA request is made."""

import sqlite3
from datetime import timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from embedded_copilot.api.desktop_identity import (
    TrustedDesktopIdentity,
    desktop_identity,
)
from embedded_copilot.api.model_consumption_routes import router
from embedded_copilot.application.model_consumption import ModelConsumptionService
from tests.model_gateway.test_consumption_core import authority
from tests.model_gateway.test_counter_bundle_integration import chain


def app_case(
    tmp_path, monkeypatch, *, account=True, authentication="LOCAL_BOOTSTRAP_SESSION"
):
    invocation, store, model_request, current, events = chain(tmp_path, monkeypatch)
    model_request = type(model_request).model_validate(
        {
            **model_request.model_dump(mode="python"),
            "task_type": "HARDWARE_SELECTION_PROPOSAL",
        }
    )
    identity = TrustedDesktopIdentity(
        requester="owner",
        tenant_id=model_request.authority_reference.tenant_id,
        authentication=authentication,
    )
    context = invocation.prepare_consumption_context(
        model_request, authority_source=current
    )
    from embedded_copilot.api import project_catalog_routes as candidate_routes

    def builder(*_args):
        return model_request, current, None, None, None, None

    monkeypatch.setattr(candidate_routes, "_prepare_model_candidate", builder)

    class Catalog:
        def get_project_for_requester(self, *_args, **_kwargs):
            pass

        def conversation(self, *_args, **_kwargs):
            pass

    class Account:
        authority_id = "offline-host"

        def read(self, scope):
            return authority(
                scope,
                observed_at=context.now - timedelta(minutes=1),
                valid_until=context.now + timedelta(minutes=20),
            )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[desktop_identity] = lambda: identity
    app.state.project_catalog_runtime = Catalog()
    app.state.model_invocation_service = invocation
    app.state.model_consumption_service = ModelConsumptionService(
        store,
        account_authority_source=Account() if account else None,
        clock=lambda: context.now,
    )

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "offline-trace"
        return await call_next(request)

    body = {
        "schema_version": 1,
        "selection_schema_version": 1,
        "operation_id": "HARDWARE_SELECTION_PROPOSAL",
        "request_id": model_request.request_id,
        "plan_id": "plan",
        "phase_id": "phase",
        "plan_task_id": "plan-task",
    }
    prefix = f"/api/v1/copilot/projects/{model_request.project_id}/conversations/{model_request.payload.conversation_id}/model-consumption"
    return TestClient(app), prefix, body, store, events


def test_confirm_recover_revoke_never_dispatches(tmp_path, monkeypatch):
    client, prefix, body, store, events = app_case(tmp_path, monkeypatch)
    response = client.post(prefix + "/preview", json=body)
    assert response.status_code == 200, response.text
    prepared = response.json()
    assert prepared["status"] == "READY" and prepared["dispatch_ready"] is False
    preview = prepared["preview"]
    consent = {
        "preview_id": preview["preview_id"],
        "preview_fingerprint": preview["fingerprint"],
        "consent": True,
    }
    issued = client.post(prefix + "/grants", json=consent)
    assert issued.status_code == 200, issued.text
    grant = issued.json()
    assert grant["status"] == "ACTIVE"
    assert client.post(prefix + "/grants", json=consent).json() == grant
    assert client.get(prefix + "/grants/" + grant["grant_id"]).json() == grant
    revoked = client.post(
        prefix + "/grants/" + grant["grant_id"] + "/revoke",
        json={"expected_revision": 1},
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "REVOKED"
    assert events == []
    assert (
        store.get_attempt_for_project(
            preview["scope"]["project_id"], body["request_id"]
        )
        is None
    )
    assert (
        "account_authority" not in issued.text and "offline-account" not in issued.text
    )


def test_missing_account_source_previews_but_cannot_sign(tmp_path, monkeypatch):
    client, prefix, body, store, events = app_case(tmp_path, monkeypatch, account=False)
    preview = client.post(prefix + "/preview", json=body).json()
    assert preview["status"] == "BLOCKED"
    assert preview["preview"] is None
    with sqlite3.connect(store._path) as connection:
        raw = connection.execute(
            "SELECT payload FROM model_consumption_previews"
        ).fetchone()[0]
    from embedded_copilot.model_gateway.invocation.consumption_contracts import (
        ConsumptionPreviewV1,
    )

    internal_preview = ConsumptionPreviewV1.model_validate_json(raw)
    result = client.post(
        prefix + "/grants",
        json={
            "preview_id": internal_preview.preview_id,
            "preview_fingerprint": internal_preview.fingerprint,
            "consent": True,
        },
    )
    assert result.status_code == 503
    assert result.json()["error"] == "ACCOUNT_AUTHORITY_UNAVAILABLE"
    assert events == []


@pytest.mark.parametrize(
    "authentication", ["LOCAL_DEVELOPMENT", "LOCAL_DEVELOPMENT_HEADER"]
)
def test_development_identity_is_not_consumption_authority(
    tmp_path, monkeypatch, authentication
):
    client, prefix, body, _, events = app_case(
        tmp_path, monkeypatch, authentication=authentication
    )
    result = client.post(prefix + "/preview", json=body)
    assert result.status_code == 401 and events == []


@pytest.mark.parametrize(
    "extra", [{"stage_id": "CHAT"}, {"model": "other"}, {"witness": {}}, {"fee": "0"}]
)
def test_untrusted_qualification_fields_rejected(tmp_path, monkeypatch, extra):
    client, prefix, body, _, events = app_case(tmp_path, monkeypatch)
    assert client.post(prefix + "/preview", json={**body, **extra}).status_code == 422
    assert events == []


def test_confirmation_false_and_extra_envelope_rejected(tmp_path, monkeypatch):
    client, prefix, _body, _, events = app_case(tmp_path, monkeypatch)
    for extra in (
        {"consent": False},
        {"consent": 1},
        {"consent": True, "envelope": {}},
    ):
        result = client.post(
            prefix + "/grants",
            json={"preview_id": "p", "preview_fingerprint": "x", **extra},
        )
        assert result.status_code == 422
    assert events == []


def test_lost_confirmation_response_recovers_same_grant_when_current_readers_fail(
    tmp_path, monkeypatch
):
    client, prefix, body, store, events = app_case(tmp_path, monkeypatch)
    preview = client.post(prefix + "/preview", json=body).json()["preview"]
    consent = {
        "preview_id": preview["preview_id"],
        "preview_fingerprint": preview["fingerprint"],
        "consent": True,
    }
    committed = client.post(prefix + "/grants", json=consent).json()
    client.app.state.model_consumption_service._account_source = None

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("offline current source unavailable")

    monkeypatch.setattr(
        type(client.app.state.model_invocation_service),
        "prepare_consumption_context",
        unavailable,
    )
    recovered = client.post(prefix + "/grants", json=consent)
    assert recovered.status_code == 200, recovered.text
    value = recovered.json()
    assert value["status"] == "STALE"
    for field in (
        "grant_id",
        "revision",
        "not_after",
        "preview_id",
        "preview_fingerprint",
    ):
        assert value[field] == committed[field]
    with sqlite3.connect(store._path) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM model_consumption_grants"
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM model_consumption_grant_events"
            ).fetchone()[0]
            == 1
        )
    assert events == []


@pytest.mark.parametrize("field", ["schema_version", "selection_schema_version"])
@pytest.mark.parametrize("value", [True, 1.0])
def test_version_fields_require_exact_json_integer(tmp_path, monkeypatch, field, value):
    client, prefix, body, _store, events = app_case(tmp_path, monkeypatch)
    assert client.post(prefix + "/preview", json={**body, field: value}).status_code == 422
    assert events == []


def test_get_never_reads_account_authority(tmp_path, monkeypatch):
    client, prefix, body, _store, events = app_case(tmp_path, monkeypatch)
    prepared = client.post(prefix + "/preview", json=body).json()["preview"]
    grant = client.post(prefix + "/grants", json={"preview_id": prepared["preview_id"],
        "preview_fingerprint": prepared["fingerprint"], "consent": True}).json()
    source = client.app.state.model_consumption_service._account_source
    def forbidden(*_args):
        raise AssertionError("GET cannot read or invalidate account authority")
    monkeypatch.setattr(source, "read", forbidden)
    response = client.get(prefix + "/grants/" + grant["grant_id"])
    assert response.status_code == 200 and response.json()["status"] == "ACTIVE"
    assert response.json()["dispatch_ready"] is False and events == []
