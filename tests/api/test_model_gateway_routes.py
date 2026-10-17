from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from embedded_copilot.api.desktop_identity import (
    LocalDevelopmentIdentityAdapter,
    ProductionLocalSessionIdentityAdapter,
)
from embedded_copilot.api.model_gateway_routes import router
from embedded_copilot.core.config import Settings
from embedded_copilot.model_gateway import (
    EngineeringStage,
    GateStatus,
    InvocationStatus,
    ModelInvocationReceipt,
    SelectionMode,
    SQLiteModelGatewayStore,
)
from embedded_copilot.model_gateway.invocation.contracts import (
    ModelExecutionSnapshot,
    ModelInvocationAttempt,
)
from embedded_copilot.model_gateway.management import ModelGatewayManagementService
from embedded_copilot.model_gateway.profiles import (
    CapabilityStatus,
    CredentialProfile,
    EndpointProfile,
    EndpointType,
    ModelAvailability,
    ModelCapability,
    ModelCatalogSource,
    ModelProfile,
    ProtocolFamily,
    ProviderProfile,
    RecordProvenance,
)
from embedded_copilot.model_gateway.registry import CapabilityProbeResult
from embedded_copilot.production_release.credentials import InMemoryCredentialProvider
from embedded_copilot.production_release.model_pool import (
    ModelDescriptor,
    ModelPoolRefreshResult,
)
from embedded_copilot.production_release.runtime import ProductionReleaseRuntime
from embedded_copilot.production_release.session import LocalSessionManager
from embedded_copilot.project_catalog import (
    ProjectCatalogForbidden,
    ProjectCatalogRuntime,
    ProjectCreationRequest,
    ProjectType,
)


def _client() -> tuple[TestClient, ModelGatewayManagementService]:
    app = FastAPI()
    app.include_router(router)
    service = ModelGatewayManagementService()
    service.add_provider(
        ProviderProfile(
            provider_id="openai",
            display_name="OpenAI",
            supported_protocols=(ProtocolFamily.OPENAI_RESPONSES,),
        )
    )
    service.add_endpoint(
        EndpointProfile(
            endpoint_id="endpoint-openai",
            provider_id="openai",
            display_name="Official",
            endpoint_type=EndpointType.OFFICIAL,
            protocol_family=ProtocolFamily.OPENAI_RESPONSES,
            base_url="https://api.openai.com/v1",
            credential_ref="credential:openai",
        )
    )
    app.state.model_gateway_management_service = service
    sessions = LocalSessionManager()
    token = sessions.exchange(sessions.issue_bootstrap(requester="owner-1", tenant_id="tenant-1"))
    app.state.desktop_identity_provider = ProductionLocalSessionIdentityAdapter(sessions)

    class _Catalog(ProjectCatalogRuntime):
        def __init__(self) -> None:
            pass

        def get_project_for_requester(self, project_id, *, tenant_id, requester):
            if project_id != "project-a" or tenant_id != "tenant-1" or requester != "owner-1":
                raise ProjectCatalogForbidden("project access denied")
            return object()

    app.state.project_catalog_runtime = _Catalog()
    client = TestClient(app)
    client.cookies.set("EmbeddedCopilotSession", token)
    return client, service


def test_anonymous_model_configuration_is_rejected_before_mutation() -> None:
    client, service = _client()
    client.cookies.clear()
    before = tuple(service.list_providers())
    response = client.post("/api/v1/model-gateway/providers", json={
        "provider_id": "anonymous-provider", "display_name": "Anonymous Provider",
        "supported_protocols": ["OPENAI_CHAT"],
    })
    assert response.status_code == 401
    assert tuple(service.list_providers()) == before


def test_development_identity_cannot_write_model_configuration() -> None:
    client, service = _client()
    client.app.state.desktop_identity_provider = LocalDevelopmentIdentityAdapter()
    before = tuple(service.list_providers())
    response = client.post("/api/v1/model-gateway/providers", json={
        "provider_id": "dev-provider", "display_name": "Development Provider",
        "supported_protocols": ["OPENAI_CHAT"],
    })
    assert response.status_code == 401
    assert response.json()["detail"] == "DESKTOP_SESSION_REQUIRED"
    assert tuple(service.list_providers()) == before


def _receipt(
    project_id: str, request_id: str, *, failed: bool = False,
    completed_second: int | None = None, connection_id: str | None = None,
) -> ModelInvocationReceipt:
    return ModelInvocationReceipt(
        schema_version=3 if connection_id is not None else 1,
        request_id=request_id,
        trace_context={"trace_id": request_id, "correlation": {
            "request_id": request_id, "trace_id": request_id, "project_id": project_id,
        }},
        project_id=project_id,
        stage_id=EngineeringStage.REQUIREMENT_ANALYSIS,
        task_type="REQUIREMENT",
        policy_source="PROJECT_DEFAULT",
        selection_mode=SelectionMode.FIXED_MODEL,
        selection_reason="PROJECT_DEFAULT",
        selected_model={
            "provider_id": "deepseek", "endpoint_id": "endpoint-deepseek",
            "credential_ref": "credential:private-reference", "model_profile_id": "profile-1",
            "model_id": "deepseek-chat",
        },
        connection_id=connection_id,
        gates=tuple(
            {"name": name, "status": GateStatus.PASS}
            for name in ("capability", "availability", "credential", "endpoint_health", "quota")
        ),
        started_at=datetime(2026, 9, 13, tzinfo=UTC),
        completed_at=datetime(
            2026, 9, 13, 0, 0,
            completed_second if completed_second is not None else (2 if failed else 1),
            tzinfo=UTC,
        ),
        latency_ms=1.0,
        result=InvocationStatus.FAILED if failed else InvocationStatus.SUCCESS,
        error_code="PROVIDER_UNAVAILABLE" if failed else None,
    )


def _receipt_client(tmp_path: Path):
    root = tmp_path / "projects"
    root.mkdir()
    catalog = ProjectCatalogRuntime(
        project_root=root, state_database=tmp_path / "catalog.sqlite3", initialize_async=False,
    )
    projects = []
    for index, owner in enumerate(("owner-1", "owner-2"), 1):
        preview = catalog.preview(ProjectCreationRequest(
            name=f"Receipt Project {index}", relative_path=f"receipt-project-{index}",
            project_type=ProjectType.ESP_IDF,
        ), tenant_id="tenant-1", requester=owner)
        projects.append(catalog.create(
            preview.preview_id, preview.fingerprint,
            confirmed_at=datetime.now(UTC), tenant_id="tenant-1", requester=owner,
        ).project_id)
    store = SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3")
    app = FastAPI()
    app.include_router(router)
    app.state.desktop_identity_provider = LocalDevelopmentIdentityAdapter(
        requester="owner-1", tenant_id="tenant-1",
    )
    app.state.project_catalog_runtime = catalog
    app.state.model_gateway_management_service = ModelGatewayManagementService(store=store)
    return TestClient(app), store, projects, catalog


def test_request_override_endpoint_cannot_issue_execution_authorization() -> None:
    client, _ = _client()
    response = client.post("/api/v1/model-gateway/overrides", json={
        "request_id": "request-1", "model_profile_id": "model-current",
    })
    assert response.status_code == 403
    assert response.json()["detail"] == "REQUEST_OVERRIDE_UNAUTHORIZED"


def test_invocation_attempt_unknown_is_project_scoped_and_read_only(tmp_path: Path) -> None:
    client, store, projects, catalog = _receipt_client(tmp_path)
    project_id, foreign_project = projects
    snapshot = ModelExecutionSnapshot(
        project_id=project_id, stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT,
        selected_model={
            "provider_id": "relay", "endpoint_id": "endpoint-relay",
            "credential_ref": "credential:relay", "model_profile_id": "model-relay",
            "model_id": "relay-model",
        },
        connection_id="connection-1", credential_revision="credential-revision-1",
        endpoint_revision="endpoint-revision-1", policy_source="STAGE_POLICY",
        policy_fingerprint="sha256:" + "1" * 64,
        selection_fingerprint="sha256:" + "2" * 64,
    )
    attempt = ModelInvocationAttempt(
        project_id=project_id, request_id="request-unknown",
        request_fingerprint="sha256:" + "3" * 64, snapshot=snapshot,
    )
    store.begin_attempt(attempt)
    store.mark_attempt_unknown(store.mark_dispatch_intent(attempt))
    prefix = f"/api/v1/model-gateway/invocations/projects/{project_id}/attempts/request-unknown"
    response = client.get(prefix)
    assert response.status_code == 200
    assert response.json()["status"] == "OUTCOME_UNKNOWN"
    assert response.json()["confirmed_provider_request_count"] is None
    assert response.json()["gateway_response_observed"] is None
    assert "credential" not in json.dumps(response.json()).lower()
    assert client.get(prefix.replace(project_id, foreign_project)).status_code == 403
    assert client.get(prefix.replace("request-unknown", "request-missing")).status_code == 404
    catalog.close()


def test_invocation_receipts_are_project_scoped_paginated_and_redacted(tmp_path: Path) -> None:
    client, store, projects, _ = _receipt_client(tmp_path)
    first, other = projects
    old = store.append_receipt(_receipt(first, "request-old"))
    new = store.append_receipt(_receipt(first, "request-new", failed=True, connection_id="conn-at-invocation"))
    store.append_receipt(_receipt(other, "request-foreign", completed_second=3))
    prefix = f"/api/v1/model-gateway/invocations/projects/{first}/receipts"
    page = client.get(prefix, params={"limit": 1, "offset": 0})
    assert page.status_code == 200
    assert [row["fingerprint"] for row in page.json()["items"]] == [new.fingerprint]
    assert page.json()["total"] == 2
    assert client.get(prefix, params={"limit": 1, "offset": 1}).json()["items"][0]["fingerprint"] == old.fingerprint
    assert client.get(prefix, params={"request_id": "request-foreign"}).json()["items"] == []
    assert client.get(prefix, params={"request_id": "request-old"}).json()["total"] == 1
    assert client.get(prefix, params={"chat_request_id": "new"}).json()["items"][0]["request_id"] == "request-new"
    assert client.get(prefix, params={"request_id": "request-new", "chat_request_id": "new"}).status_code == 422
    detail = client.get(f"{prefix}/{new.fingerprint}")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["error_code"] == "PROVIDER_UNAVAILABLE"
    assert set(payload) == {"request_id", "trace_id", "project_id", "stage_id", "policy_source", "selected_model", "connection_id", "usage", "result", "error_code", "started_at", "completed_at", "fingerprint", "invocation_mode", "authority_reference", "gateway_call_count", "gateway_response_observed", "transport_send_count"}
    assert payload["connection_id"] == "conn-at-invocation"
    assert client.get(f"{prefix}/{old.fingerprint}").json()["connection_id"] is None
    assert set(payload["selected_model"]) == {"provider_id", "endpoint_id", "model_profile_id", "model_id"}
    assert "credential" not in json.dumps(payload).lower()
    assert client.get(f"{prefix}/{'sha256:' + '0' * 64}").status_code == 404
    assert client.get(f"{prefix}/{store.list_receipts_for_project(other)[0].fingerprint}").status_code == 404


def test_invocation_receipts_require_owner_and_trusted_identity(tmp_path: Path) -> None:
    client, store, projects, catalog = _receipt_client(tmp_path)
    first, other = projects
    store.append_receipt(_receipt(first, "request-one"))
    prefix = "/api/v1/model-gateway/invocations/projects"
    assert client.get(f"{prefix}/{other}/receipts").status_code == 403
    assert client.get(f"{prefix}/missing/receipts").status_code == 404
    client.app.state.desktop_identity_provider = LocalDevelopmentIdentityAdapter(
        requester="owner-1", tenant_id="tenant-2",
    )
    assert client.get(f"{prefix}/{first}/receipts").status_code == 403
    client.app.state.desktop_identity_provider = None
    client.app.state.settings = type("SettingsStub", (), {"environment": "production"})()
    assert client.get(f"{prefix}/{first}/receipts").status_code == 401
    catalog.close()


def test_invocation_receipts_survive_store_reopen_and_fail_closed(tmp_path: Path) -> None:
    client, store, projects, catalog = _receipt_client(tmp_path)
    project_id = projects[0]
    prefix = f"/api/v1/model-gateway/invocations/projects/{project_id}/receipts"
    assert client.get(prefix).json() == {"items": [], "total": 0, "limit": 50, "offset": 0}
    receipt = store.append_receipt(_receipt(project_id, "request-persisted", failed=True))
    client.app.state.model_gateway_management_service = ModelGatewayManagementService(
        store=SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3")
    )
    assert client.get(prefix).json()["items"][0]["fingerprint"] == receipt.fingerprint
    assert client.get(f"{prefix}/{receipt.fingerprint}").json()["result"] == "FAILED"
    client.app.state.model_gateway_management_service = ModelGatewayManagementService()
    unavailable = client.get(prefix)
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"] == "INVOCATION_RECEIPTS_UNAVAILABLE"
    catalog.close()


def test_invocation_receipt_reports_only_persisted_provider_usage(tmp_path: Path) -> None:
    client, store, projects, catalog = _receipt_client(tmp_path)
    project_id = projects[0]
    historical = store.append_receipt(_receipt(project_id, "historical"))
    current = _receipt(project_id, "usage")
    current = ModelInvocationReceipt.model_validate({
        **current.model_dump(mode="python", exclude={"fingerprint"}),
        "schema_version": 4,
        "usage": {
            "input_tokens": 12, "output_tokens": 5, "total_tokens": 17,
            "usage_source": "PROVIDER_REPORTED",
        },
    })
    stored = store.append_receipt(current)
    client.app.state.model_gateway_management_service = ModelGatewayManagementService(
        store=SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3")
    )
    prefix = f"/api/v1/model-gateway/invocations/projects/{project_id}/receipts"
    assert client.get(f"{prefix}/{historical.fingerprint}").json()["usage"] is None
    assert client.get(f"{prefix}/{historical.fingerprint}").json()["gateway_response_observed"] is None
    payload = client.get(f"{prefix}/{stored.fingerprint}").json()
    assert payload["usage"] == {
        "input_tokens": 12, "output_tokens": 5, "total_tokens": 17,
        "cached_input_tokens": None, "cache_write_tokens": None,
        "reasoning_tokens": None, "usage_source": "PROVIDER_REPORTED",
    }
    assert payload["gateway_response_observed"] is None
    assert client.get(prefix, params={"request_id": "usage"}).json()["items"][0]["usage"] == payload["usage"]
    catalog.close()


def test_model_gateway_api_projects_catalog_without_secrets() -> None:
    client, _ = _client()
    client.post(
        "/api/v1/model-gateway/credentials",
        json={
            "credential_id": "credential-openai",
            "provider_id": "openai",
            "display_name": "Personal",
            "secret_ref": "credential:openai",
        },
    )
    providers = client.get("/api/v1/model-gateway/providers")
    templates = client.get("/api/v1/model-gateway/provider-templates")
    endpoints = client.get("/api/v1/model-gateway/endpoints")
    assert providers.status_code == 200
    assert providers.json()[0]["provider_id"] == "openai"
    assert {item["provider_id"] for item in providers.json()} == {"openai"}
    assert "deepseek" not in providers.text
    assert {item["provider_id"] for item in templates.json()} >= {"openai", "deepseek", "zhipu"}
    assert endpoints.status_code == 200
    assert endpoints.json()[0]["credential_configured"] is True
    assert "secret_ref" not in endpoints.text
    assert "api_key" not in endpoints.text


def test_governance_models_keep_provider_endpoint_and_record_source_bound() -> None:
    client, service = _client()
    for provider_id in ("deepseek", "zhipu"):
        service.add_provider(
            ProviderProfile(
                provider_id=provider_id,
                display_name=provider_id.title(),
                supported_protocols=(ProtocolFamily.OPENAI_CHAT,),
            )
        )
        service.add_endpoint(
            EndpointProfile(
                endpoint_id=f"endpoint-{provider_id}",
                provider_id=provider_id,
                display_name="Relay",
                endpoint_type=EndpointType.RELAY,
                protocol_family=ProtocolFamily.OPENAI_CHAT,
                base_url="https://relay.example.com/v1",
                credential_ref=f"credential:{provider_id}",
            )
        )
        service.add_credential(
            CredentialProfile(
                credential_id=f"credential-{provider_id}",
                provider_id=provider_id,
                display_name="Test credential",
                secret_ref=f"credential:{provider_id}",
            )
        )
        service.add_model(
            ModelProfile(
                model_profile_id=f"model-{provider_id}",
                provider_id=provider_id,
                endpoint_id=f"endpoint-{provider_id}",
                remote_model_id=f"{provider_id}-chat",
                display_name=f"{provider_id} chat",
                advertised_family=provider_id,
                record_provenance=RecordProvenance.EXPLICIT_CONFIGURATION,
            )
        )
    service.add_model(
        ModelProfile(
            model_profile_id="model-provider-metadata",
            provider_id="deepseek",
            endpoint_id="endpoint-deepseek",
            remote_model_id="metadata-only",
            display_name="Metadata only",
            advertised_family="deepseek",
            record_provenance=RecordProvenance.PROVIDER_METADATA,
        )
    )

    response = client.get("/api/v1/model-gateway/governance/available-models")

    assert response.status_code == 200
    rows = response.json()
    assert {item["provider_id"] for item in rows} == {"deepseek", "zhipu"}
    assert all(item["provider_id"] == item["endpoint_id"].removeprefix("endpoint-") for item in rows)
    assert "metadata-only" not in {item["model_id"] for item in rows}


def test_model_gateway_api_accepts_only_credential_reference() -> None:
    client, service = _client()
    response = client.post(
        "/api/v1/model-gateway/credentials",
        json={
            "credential_id": "credential-openai",
            "provider_id": "openai",
            "display_name": "Personal",
            "secret_ref": "credential:openai",
        },
    )
    assert response.status_code == 201
    assert response.json() == {
        "credential_id": "credential-openai",
        "provider_id": "openai",
        "configured": True,
    }
    assert len(service.list_credentials()) == 1


def test_discovery_endpoint_is_unavailable_without_an_injected_port() -> None:
    client, _ = _client()
    response = client.post("/api/v1/model-gateway/endpoints/endpoint-openai/discover")
    assert response.status_code == 503
    assert response.json() == {"detail": "MODEL_DISCOVERY_UNAVAILABLE"}


def test_management_models_and_probe_candidates_use_current_endpoint_pool() -> None:
    client, service = _client()
    current = ModelProfile(
        model_profile_id="model-current",
        provider_id="openai",
        endpoint_id="endpoint-openai",
        remote_model_id="gpt-current",
        display_name="Current",
        advertised_family="OPENAI_RESPONSES",
        capabilities=(ModelCapability.TEXT,),
        availability=ModelAvailability.AVAILABLE,
        availability_status=ModelAvailability.AVAILABLE,
        availability_source=ModelCatalogSource.PROVIDER_API,
        availability_provenance=ModelCatalogSource.PROVIDER_API,
        catalog_source=ModelCatalogSource.PROVIDER_API,
        credential_revision="credential-current",
        endpoint_revision="endpoint-current",
        record_provenance=RecordProvenance.ENDPOINT_DISCOVERY,
    )
    historical = current.model_copy(
        update={
            "model_profile_id": "model-historical",
            "remote_model_id": "gpt-history",
            "credential_revision": "credential-old",
            "endpoint_revision": "endpoint-old",
        }
    )
    service.models.upsert(current)
    service.models.upsert(historical)

    class _CurrentProvider:
        def configured_provider_ids(self) -> tuple[str, ...]:
            return ("openai",)

        def model_pool(self, *, provider_id: str, refresh: bool = False) -> tuple[ModelProfile, ...]:
            assert provider_id == "openai"
            assert refresh is False
            return (current,)

    client.app.state.provider_configuration_service = _CurrentProvider()

    listed = client.get("/api/v1/model-gateway/models")
    assert listed.status_code == 200
    assert [item["remote_model_id"] for item in listed.json()] == ["gpt-current"]
    assert {item.remote_model_id for item in service.list_models()} == {"gpt-current", "gpt-history"}

    calls: list[str] = []

    class _Probe:
        def probe(self, endpoint: EndpointProfile, model: ModelProfile, probe_type: str) -> CapabilityProbeResult:
            calls.append(model.remote_model_id)
            return CapabilityProbeResult(
                endpoint_id=endpoint.endpoint_id,
                model_profile_id=model.model_profile_id,
                probe_type=probe_type,
                probe_version="v1",
                timestamp=datetime.now(UTC),
                status=CapabilityStatus.PASS,
                provider_id=model.provider_id,
                credential_ref=endpoint.credential_ref,
                model_id=model.remote_model_id,
                credential_revision=model.credential_revision,
                endpoint_revision=model.endpoint_revision,
            )

    service.set_capability_probe_port(_Probe())
    rejected = client.post(
        "/api/v1/model-gateway/models/model-historical/capability-probes/TEXT"
    )
    assert rejected.status_code == 404
    assert rejected.json()["detail"] == "MODEL_NOT_IN_CURRENT_ENDPOINT_POOL"
    assert calls == []

    accepted = client.post(
        "/api/v1/model-gateway/models/model-current/capability-probes/TEXT"
    )
    assert accepted.status_code == 200, accepted.text
    assert calls == ["gpt-current"]

    class _StaleProbe(_Probe):
        def probe(self, endpoint: EndpointProfile, model: ModelProfile, probe_type: str) -> CapabilityProbeResult:
            return super().probe(endpoint, model, probe_type).model_copy(update={"credential_revision": "credential-old"})

    service.set_capability_probe_port(_StaleProbe())
    stale = client.post("/api/v1/model-gateway/models/model-current/capability-probes/TEXT")
    assert stale.status_code == 409
    assert stale.json()["detail"] == "CAPABILITY_PROBE_IDENTITY_INVALID"


def test_production_composition_wires_text_capability_probe_and_fails_closed_for_other_types(
    tmp_path: Path, monkeypatch,
) -> None:
    from embedded_copilot.api import main as api_main
    from embedded_copilot.production_release.provider import (
        ProviderConfigurationService,
    )

    class _Discovery:
        def discover(
            self,
            endpoint: EndpointProfile,
            *,
            connection_id: str | None = None,
            credential_revision: str | None = None,
        ) -> tuple[ModelDescriptor, ...]:
            del connection_id
            return (
                ModelDescriptor(
                    provider_id=endpoint.provider_id,
                    endpoint_id=endpoint.endpoint_id,
                    model_id="glm-production-probe",
                    display_name="GLM production probe",
                    protocol_family=endpoint.protocol_family,
                    availability=ModelAvailability.AVAILABLE,
                    availability_source=ModelCatalogSource.PROVIDER_API,
                    catalog_source=ModelCatalogSource.PROVIDER_API,
                    discovered_at=datetime(2026, 9, 22, tzinfo=UTC),
                    credential_revision=credential_revision,
                ),
            )

    requests: list[httpx.Request] = []
    reply_status = {"code": 200}

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if reply_status["code"] == 0:
            raise httpx.ConnectError("offline", request=request)
        if reply_status["code"] != 200:
            return httpx.Response(reply_status["code"], json={"error": {"message": "test failure"}}, request=request)
        payload = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "capability-probe",
                "model": payload["model"],
                "choices": [{"message": {"content": "ok"}}],
            },
            request=request,
        )

    def provider_factory(**kwargs):
        kwargs["model_discovery"] = _Discovery()
        kwargs["http_transport"] = httpx.MockTransport(transport)
        return ProviderConfigurationService(**kwargs)

    monkeypatch.setattr(api_main, "ProviderConfigurationService", provider_factory)
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("<html>desktop</html>", encoding="utf-8")
    projects = tmp_path / "projects"
    projects.mkdir()
    runtime = ProductionReleaseRuntime.create(
        Settings(
            environment="production",
            production_state_root=tmp_path / "state",
            project_root=projects,
            desktop_static_assets=assets,
            _env_file=None,
        ),
        credentials=InMemoryCredentialProvider(),
    )
    app = api_main.create_app(settings=runtime.settings, production_release_runtime=runtime)

    with TestClient(app) as client:
        anonymous = client.post(
            "/api/v1/model-gateway/provider-connections",
            json={
                "provider_id": "zhipu", "connection_name": "Anonymous",
                "endpoint_id": "endpoint-zhipu-anonymous",
                "endpoint_url": "https://zhipu.example.test/v1",
                "protocol_family": "OPENAI_CHAT",
            },
        )
        assert anonymous.status_code == 401
        assert requests == []
        nonce = runtime.sessions.issue_bootstrap(requester="owner-1", tenant_id="tenant-1")
        assert client.post(
            "/api/v1/copilot/desktop/system/bootstrap/exchange", json={"nonce": nonce},
        ).status_code == 200
        connection = client.post(
            "/api/v1/model-gateway/provider-connections",
            json={
                "provider_id": "zhipu",
                "connection_name": "Capability probe",
                "endpoint_id": "endpoint-zhipu-capability",
                "endpoint_url": "https://zhipu.example.test/v1",
                "protocol_family": "OPENAI_CHAT",
            },
        ).json()
        assert connection["legacy_source"] is None
        registry = app.state.model_gateway_management_service.connections
        registry.update(connection["connection_id"], legacy_source="provider_configuration_migration")
        migrated = client.get(f"/api/v1/model-gateway/provider-connections/{connection['connection_id']}")
        assert migrated.status_code == 200, migrated.text
        assert migrated.json()["legacy_source"] == "provider_configuration_migration"
        registry.update(connection["connection_id"], legacy_source=None)
        credential = client.put(
            f"/api/v1/model-gateway/provider-connections/{connection['connection_id']}/credential",
            json={"secret": "production-capability-secret"},
        )
        assert credential.status_code == 200, credential.text
        refreshed = client.post(
            f"/api/v1/model-gateway/provider-connections/{connection['connection_id']}/models/refresh"
        )
        assert refreshed.status_code == 200, refreshed.text
        model = refreshed.json()["models"][0]
        current_model = app.state.provider_configuration_service.model_pool(
            connection_id=connection["connection_id"]
        )[0]

        text_probe = client.post(
            f"/api/v1/model-gateway/models/{model['model_profile_id']}/capability-probes/TEXT"
        )
        assert text_probe.status_code == 200, text_probe.text
        body = text_probe.json()
        assert body["status"] == "PASS"
        assert {
            key: body[key]
            for key in (
                "provider_id",
                "endpoint_id",
                "credential_ref",
                "model_profile_id",
                "model_id",
                "credential_revision",
                "endpoint_revision",
            )
        } == {
            "provider_id": "zhipu",
            "endpoint_id": "endpoint-zhipu-capability",
            "credential_ref": connection["credential_ref"],
            "model_profile_id": model["model_profile_id"],
            "model_id": "glm-production-probe",
                "credential_revision": current_model.credential_revision,
                "endpoint_revision": current_model.endpoint_revision,
        }
        assert "production-capability-secret" not in text_probe.text
        assert len(requests) == 1

        vision_probe = client.post(
            f"/api/v1/model-gateway/models/{model['model_profile_id']}/capability-probes/VISION"
        )
        assert vision_probe.status_code == 200, vision_probe.text
        assert vision_probe.json()["status"] == "UNAVAILABLE"
        assert len(requests) == 1

        test_url = f"/api/v1/model-gateway/provider-connections/{connection['connection_id']}/test-connection"
        payload = {"model_profile_id": model["model_profile_id"], "model_id": "glm-production-probe"}
        current_profile = app.state.model_gateway_management_service.model_profile(model["model_profile_id"])
        assert current_profile is not None
        assert (current_profile.provider_id, current_profile.endpoint_id, current_profile.remote_model_id) == ("zhipu", "endpoint-zhipu-capability", "glm-production-probe")
        for http_code, expected in (
            (200, "AVAILABLE"),
            (401, "PROVIDER_AUTH_FAILED"),
            (404, "PROVIDER_MODEL_UNAVAILABLE"),
            (429, "PROVIDER_RATE_LIMITED"),
            (0, "PROVIDER_NETWORK_UNAVAILABLE"),
        ):
            reply_status["code"] = http_code
            refreshed = client.post(f"/api/v1/model-gateway/provider-connections/{connection['connection_id']}/models/refresh")
            assert refreshed.status_code == 200, refreshed.text
            tested = client.post(test_url, json=payload)
            assert tested.status_code == 200, f"{http_code}: {tested.text}"
            assert tested.json()["test_result"]["status"] == expected
            if http_code == 200:
                assert tested.json()["test_result"]["reason_code"] is None
            else:
                assert tested.json()["test_result"]["reason_code"]
        reply_status["code"] = 200

        local_connection = client.post(
            "/api/v1/model-gateway/provider-connections",
            json={
                "provider_id": "local",
                "connection_name": "Local capability probe",
                "endpoint_id": "endpoint-local-capability",
                "endpoint_url": "http://127.0.0.1:11434/v1",
                "protocol_family": "LOCAL_OPENAI_COMPATIBLE",
            },
        )
        assert local_connection.status_code == 201, local_connection.text
        local_connection_id = local_connection.json()["connection_id"]
        provider_service = app.state.provider_configuration_service
        local_endpoint = app.state.model_gateway_management_service.endpoint_profile(
            "endpoint-local-capability"
        )
        provider_service._model_pool.refresh(
            local_endpoint,
            credential_revision=None,
            credential_configured=True,
            connection_id=local_connection_id,
        )
        local_model = provider_service.model_pool(
            connection_id=local_connection_id
        )[0]
        local_probe = client.post(
            f"/api/v1/model-gateway/models/{local_model.model_profile_id}/capability-probes/TEXT"
        )
        assert local_probe.status_code == 200, local_probe.text
        assert local_probe.json()["status"] == "PASS"
        assert local_probe.json()["credential_ref"] == "credential:local"
        assert len(requests) == 7


def test_production_discovery_route_admits_through_current_model_pool() -> None:
    client, service = _client()
    current = ModelProfile(
        model_profile_id="model-discovered-current",
        provider_id="openai",
        endpoint_id="endpoint-openai",
        remote_model_id="gpt-discovered",
        display_name="Discovered",
        advertised_family="OPENAI_RESPONSES",
        availability=ModelAvailability.AVAILABLE,
        availability_status=ModelAvailability.AVAILABLE,
        availability_source=ModelCatalogSource.PROVIDER_API,
        availability_provenance=ModelCatalogSource.PROVIDER_API,
        catalog_source=ModelCatalogSource.PROVIDER_API,
        credential_revision="credential-current",
        endpoint_revision="endpoint-current",
        record_provenance=RecordProvenance.ENDPOINT_DISCOVERY,
    )
    historical = current.model_copy(
        update={
            "model_profile_id": "model-discovered-old",
            "remote_model_id": "gpt-old",
            "credential_revision": "credential-old",
            "endpoint_revision": "endpoint-old",
        }
    )
    service.models.upsert(historical)
    calls: list[str] = []

    class _CurrentProvider:
        def configured_provider_ids(self) -> tuple[str, ...]:
            return ("openai",)

        def model_pool(self, *, provider_id: str, refresh: bool = False) -> tuple[ModelProfile, ...]:
            del refresh
            assert provider_id == "openai"
            return (current,)

        def refresh_model_pool(self, *, provider_id: str) -> ModelPoolRefreshResult:
            calls.append(provider_id)
            service.models.upsert(current)
            return ModelPoolRefreshResult(
                provider_id=provider_id,
                endpoint_id="endpoint-openai",
                models=(current,),
                status="UPDATED",
            )

    client.app.state.provider_configuration_service = _CurrentProvider()
    response = client.post("/api/v1/model-gateway/endpoints/endpoint-openai/discover")

    assert response.status_code == 200
    assert [item["remote_model_id"] for item in response.json()] == ["gpt-discovered"]
    assert calls == ["openai"]
    assert {item.remote_model_id for item in service.list_models()} == {"gpt-discovered", "gpt-old"}


def test_task_profiles_are_projected_as_typed_routing_defaults() -> None:
    client, _ = _client()
    response = client.get("/api/v1/model-gateway/task-profiles")
    assert response.status_code == 200
    assert {item["task_type"] for item in response.json()} >= {"FIRMWARE", "VISION", "EMBEDDING"}


def test_account_usage_is_explicitly_unsupported_without_provider_capability() -> None:
    client, _ = _client()
    response = client.post("/api/v1/model-gateway/usage/accounts/endpoint-openai/refresh")
    assert response.status_code == 200
    assert response.json()["status"] == "UNSUPPORTED"


def test_credential_projection_and_endpoint_test_are_secret_free() -> None:
    client, service = _client()
    service.add_credential(
        CredentialProfile(
            credential_id="credential-openai",
            provider_id="openai",
            display_name="Personal",
            secret_ref="credential:openai",
        )
    )
    credentials = client.get("/api/v1/model-gateway/credentials")
    assert credentials.status_code == 200
    assert credentials.json() == [
        {
            "credential_id": "credential-openai",
            "provider_id": "openai",
            "display_name": "Personal",
            "auth_type": "API_KEY",
            "enabled": True,
            "configured": True,
        }
    ]
    assert "secret_ref" not in credentials.text

    class _Probe:
        def test_connection(self, endpoint: EndpointProfile) -> str:
            assert endpoint.endpoint_id == "endpoint-openai"
            return "AVAILABLE"

    client.app.state.model_gateway_connection_probe = _Probe()
    tested = client.post("/api/v1/model-gateway/endpoints/endpoint-openai/test-connection")
    assert tested.status_code == 200
    assert tested.json()["health_status"] == "AVAILABLE"


def test_task_assignment_update_is_project_bound_and_persisted_in_policy() -> None:
    client, service = _client()
    service.add_model(
        ModelProfile(
            model_profile_id="openai-model",
            provider_id="openai",
            endpoint_id="endpoint-openai",
            remote_model_id="gpt-test",
            display_name="GPT test",
            advertised_family="GPT",
            capabilities=(ModelCapability.TEXT,),
            capability_sources=(),
        )
    )
    response = client.put(
        "/api/v1/model-gateway/assignments/project-a/FIRMWARE",
        json={"task_type": "FIRMWARE", "model_profile_id": "openai-model"},
    )
    assert response.status_code == 200
    assert response.json()["task_type"] == "FIRMWARE"
    policy = client.get("/api/v1/model-gateway/policies/projects/project-a")
    assert policy.status_code == 200
    assert policy.json()["task_assignments"][0]["model_profile_id"] == "openai-model"


def test_legacy_project_assignment_rejects_non_owner_before_write() -> None:
    client, service = _client()
    service.add_model(ModelProfile(
        model_profile_id="openai-model", provider_id="openai", endpoint_id="endpoint-openai",
        remote_model_id="gpt-test", display_name="GPT test", advertised_family="GPT",
        capabilities=(ModelCapability.TEXT,), capability_sources=(),
    ))
    response = client.put(
        "/api/v1/model-gateway/assignments/project-b/FIRMWARE",
        json={"task_type": "FIRMWARE", "model_profile_id": "openai-model"},
    )
    assert response.status_code == 403
    assert service.project_policy("project-b") is None
