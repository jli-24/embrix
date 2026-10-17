from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from embedded_copilot.api.desktop_identity import ProductionLocalSessionIdentityAdapter
from embedded_copilot.api.v70_release_routes import router as release_router
from embedded_copilot.core.config import Settings
from embedded_copilot.model_gateway import (
    EndpointProfile,
    EndpointType,
    ModelAvailability,
    ModelCatalogSource,
    ModelDescriptor,
    ModelGatewayManagementService,
    ModelPoolCache,
    ProtocolFamily,
    SQLiteModelGatewayStore,
    descriptor_to_model_profile,
    get_usable_models,
    provider_presets,
    RecordProvenance,
)
from embedded_copilot.production_release.credentials import InMemoryCredentialProvider
from embedded_copilot.production_release.model_pool import ProviderModelPool
from embedded_copilot.production_release.provider import (
    ProviderConfigurationService,
    ProviderConnectionStatus,
)
from embedded_copilot.production_release.runtime import ProductionReleaseRuntime


def _management(tmp_path: Path) -> ModelGatewayManagementService:
    management = ModelGatewayManagementService(
        store=SQLiteModelGatewayStore(tmp_path / "model-gateway.sqlite3")
    )
    for preset in provider_presets():
        management.add_provider(preset)
    return management


def _service(tmp_path: Path, handler) -> tuple[ProviderConfigurationService, InMemoryCredentialProvider, ModelGatewayManagementService]:
    credentials = InMemoryCredentialProvider()
    management = _management(tmp_path)
    return (
        ProviderConfigurationService(
            configuration_path=tmp_path / "production.json",
            credentials=credentials,
            http_transport=httpx.MockTransport(handler),
            model_gateway_management=management,
        ),
        credentials,
        management,
    )


def _ok(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content.decode("utf-8"))
    return httpx.Response(
        200,
        json={
            "id": "scoped-receipt",
            "model": payload["model"],
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
        },
    )


@pytest.mark.parametrize("provider", ["openai", "deepseek", "zhipu"])
def test_credential_onboarding_preserves_active_and_persists_empty_model(tmp_path: Path, provider: str) -> None:
    service, _, management = _service(tmp_path, _ok)
    service.save_configuration(provider="zhipu", model="glm-4.7-flash")
    active = management.global_policy().active_model_profile_id
    result = service.save_credential(SecretStr("onboarding-test-secret"), provider=provider)
    assert result.credential_status == "CONFIGURED"
    if provider != "zhipu":
        assert result.model_id is None
        assert result.configuration_state == "FULLY_CONFIGURED"
    assert service.current_provider == "zhipu"
    assert management.global_policy().active_model_profile_id == active
    assert service.recent_verifications() == ()
    assert "onboarding-test-secret" not in (tmp_path / "production.json").read_text()
    assert "onboarding-test-secret" not in result.model_dump_json()
    restored = ProviderConfigurationService(configuration_path=tmp_path / "production.json", credentials=service._credentials, model_gateway_management=management)
    assert restored.projection(provider_id=provider).credential_status == "CONFIGURED"
    if provider != "zhipu":
        assert restored.projection(provider_id=provider).model_id is None


def test_http_credential_onboarding_does_not_replace_active_runtime_projection(
    tmp_path: Path,
) -> None:
    service, _, _ = _service(tmp_path, _ok)
    runtime = ProductionReleaseRuntime.create(
        Settings(
            environment="production",
            production_state_root=tmp_path / "runtime-state",
            _env_file=None,
        )
    )
    runtime.start()
    app = FastAPI()
    app.include_router(release_router)
    app.state.production_release_runtime = runtime
    app.state.desktop_identity_provider = ProductionLocalSessionIdentityAdapter(
        runtime.sessions
    )
    app.state.provider_configuration_service = service

    with TestClient(app) as client:
        nonce = runtime.sessions.issue_bootstrap(requester="requester", tenant_id="tenant")
        assert client.post(
            "/api/v1/copilot/desktop/system/bootstrap/exchange",
            json={"nonce": nonce},
        ).status_code == 200
        configured = client.put(
            "/api/v1/copilot/desktop/system/provider/configuration",
            json={"provider": "zhipu", "model": "glm-4.7-flash"},
        )
        assert configured.status_code == 200
        before = app.state.provider_projection
        onboarding = client.put(
            "/api/v1/copilot/desktop/system/provider/credential",
            json={"provider": "openai", "secret": "openai-onboarding-secret"},
        )

    assert onboarding.status_code == 200
    assert onboarding.json()["model_id"] is None
    assert app.state.provider_projection.provider == before.provider == "zhipu"
    assert app.state.provider_projection.model == before.model == "glm-4.7-flash"


def test_connection_save_accepts_no_selected_model_without_governance_mutation(tmp_path: Path) -> None:
    service, _, management = _service(tmp_path, _ok)
    active_before = management.global_policy().active_model_profile_id

    saved = service.save_configuration(provider="zhipu")

    assert saved.provider == "zhipu"
    assert saved.model_id is None
    assert saved.configuration_state != "FULLY_CONFIGURED"
    assert management.global_policy().active_model_profile_id == active_before
    assert service.projection(provider_id="zhipu").credential_status == "NOT_CONFIGURED"


@pytest.mark.parametrize("provider,models", [("openai", ["gpt-4o", "gpt-4o-mini"]), ("deepseek", ["deepseek-chat", "deepseek-reasoner"]), ("zhipu", ["glm-4.7-flash", "glm-4-plus"])])
def test_onboarding_listing_second_save_revision_and_cache(tmp_path: Path, provider: str, models: list[str]) -> None:
    calls: list[str] = []
    failure = [False]
    def listing(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        assert request.url.path.endswith("/models")
        assert request.headers["Authorization"] == "Bearer onboarding-listing-secret"
        return httpx.Response(429 if failure[0] else 200, json={"data": [{"id": model} for model in models]})
    service, _, _ = _service(tmp_path, listing)
    saved = service.save_credential(SecretStr("onboarding-listing-secret"), provider=provider)
    assert saved.model_id is None
    assert saved.discovery_required
    assert calls == []
    with pytest.raises(ValueError):
        service.save_configuration(provider=provider, model="")
    with pytest.raises(ValueError):
        service.save_configuration(provider=provider, model=models[0])
    refreshed = service.refresh_model_pool(provider_id=provider)
    assert refreshed.status == "UPDATED"
    assert calls == ["GET"]
    assert {m.model_id for m in service.projection(provider_id=provider).model_pool} == set(models)
    profile = next(item for item in service.projection(provider_id=provider).provider_profiles if item.provider_id == provider)
    assert profile.discovered_model_count == len(models)
    assert profile.usable_model_count == 0
    assert service.recent_verifications() == ()
    failure[0] = True
    assert service.refresh_model_pool(provider_id=provider).status == "STALE"
    assert service.projection(provider_id=provider).discovery_state == "STALE_WITH_CACHE"
    full = service.save_configuration(provider=provider, model=models[0])
    assert full.model_id == models[0]
    assert full.configuration_state == "FULLY_CONFIGURED"
    assert service.current_provider == provider
    assert service.recent_verifications() == ()
    rotated = service.save_credential(SecretStr("rotated-onboarding-secret"), provider=provider)
    assert rotated.configuration_fingerprint != saved.configuration_fingerprint
    assert rotated.model_id is None
    assert rotated.model_pool == ()
    assert service.refresh_model_pool(provider_id=provider).status == "UNAVAILABLE"
    assert service.projection(provider_id=provider).credential_status == "CONFIGURED"
    assert service.current_provider == provider


def test_catalog_and_usable_pool_are_provider_endpoint_and_revision_scoped(tmp_path: Path) -> None:
    management = _management(tmp_path)
    management.add_endpoint(
        EndpointProfile(
            endpoint_id="endpoint-deepseek",
            provider_id="deepseek",
            display_name="DeepSeek",
            endpoint_type=EndpointType.OFFICIAL,
            protocol_family=ProtocolFamily.OPENAI_CHAT,
            base_url="https://api.deepseek.com/v1",
            credential_ref="credential:deepseek",
        )
    )
    management.add_endpoint(
        EndpointProfile(
            endpoint_id="endpoint-zhipu",
            provider_id="zhipu",
            display_name="Zhipu",
            endpoint_type=EndpointType.OFFICIAL,
            protocol_family=ProtocolFamily.OPENAI_CHAT,
            base_url="https://open.bigmodel.cn/api/paas/v4",
            credential_ref="credential:zhipu",
        )
    )
    deepseek = ModelDescriptor(
        provider_id="deepseek",
        endpoint_id="endpoint-deepseek",
        model_id="deepseek-chat",
        display_name="DeepSeek Chat",
        protocol_family=ProtocolFamily.OPENAI_CHAT,
        availability=ModelAvailability.AVAILABLE,
        availability_source=ModelCatalogSource.PROVIDER_API,
        catalog_source=ModelCatalogSource.PROVIDER_API,
        credential_revision="credential-rev-1",
        endpoint_revision="endpoint-rev-1",
    )
    zhipu = ModelDescriptor(
        provider_id="zhipu",
        endpoint_id="endpoint-zhipu",
        model_id="glm-4.7-flash",
        display_name="GLM 4.7 Flash",
        protocol_family=ProtocolFamily.OPENAI_CHAT,
        availability=ModelAvailability.AVAILABLE,
        availability_source=ModelCatalogSource.PROVIDER_API,
        catalog_source=ModelCatalogSource.PROVIDER_API,
        credential_revision="credential-rev-1",
        endpoint_revision="endpoint-rev-1",
    )
    management.models.upsert(descriptor_to_model_profile(deepseek))
    management.models.upsert(descriptor_to_model_profile(zhipu))

    usable = get_usable_models(
        management.list_models(),
        provider_id="deepseek",
        endpoint_id="endpoint-deepseek",
        credential_revision="credential-rev-1",
        endpoint_revision="endpoint-rev-1",
    )
    assert [item.remote_model_id for item in usable] == ["deepseek-chat"]
    assert not any(item.remote_model_id == "glm-4.7-flash" for item in usable)
    assert get_usable_models(
        management.list_models(),
        provider_id="deepseek",
        endpoint_id="endpoint-deepseek",
        credential_revision="old-revision",
        endpoint_revision="endpoint-rev-1",
    ) == ()


def test_official_catalog_is_not_inserted_into_current_endpoint_pool(tmp_path: Path) -> None:
    management = _management(tmp_path)
    endpoint = EndpointProfile(
        endpoint_id="endpoint-zhipu",
        provider_id="zhipu",
        display_name="Zhipu",
        endpoint_type=EndpointType.OFFICIAL,
        protocol_family=ProtocolFamily.OPENAI_CHAT,
        base_url="https://open.bigmodel.cn/api/paas/v4",
        credential_ref="credential:zhipu",
    )
    management.add_endpoint(endpoint)
    pool = ProviderModelPool(management, discovery=None)

    result = pool.refresh(
        endpoint,
        credential_revision="credential-revision",
        credential_configured=True,
    )

    assert result.status == "UPDATED"
    assert result.models == ()
    assert management.list_models() == ()


def test_rate_limited_model_remains_selectable(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(429, json={"error": "rate limited"})

    service, _, _ = _service(tmp_path, handler)
    service.save_configuration(provider="zhipu", model="glm-4.7-flash")
    service.save_credential(SecretStr("zhipu-secret"), provider="zhipu")

    projection = service.test_connection()

    assert projection.connection_status is ProviderConnectionStatus.RATE_LIMITED
    assert any(item.model_id == "glm-4.7-flash" for item in projection.model_pool)
    assert next(item for item in projection.model_pool if item.model_id == "glm-4.7-flash").availability is ModelAvailability.RATE_LIMITED


def test_draft_test_binds_receipt_but_does_not_promote_provider(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        calls.append((str(request.url), payload["model"]))
        return _ok(request)

    service, credentials, management = _service(tmp_path, handler)
    service.save_credential(SecretStr("deepseek-secret"))
    service.test_connection()
    credentials.set("CHAT_PROVIDER_API_KEY:zhipu", b"zhipu-secret")

    tested = service.test_connection(
        draft={
            "provider": "zhipu",
            "model": "glm-4.7-flash",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "endpoint_id": "endpoint-zhipu",
            "credential_ref": "credential:zhipu",
            "protocol_family": "OPENAI_CHAT",
            "model_profile_id": "model-zhipu-glm-4.7-flash",
        }
    )

    assert tested.provider == "zhipu"
    assert tested.model == "glm-4.7-flash"
    assert service.current_provider == "deepseek"
    assert service.projection().provider == "deepseek"
    assert calls[-1][0].startswith("https://open.bigmodel.cn/api/paas/v4")
    receipt = service.latest_verification(provider_id="zhipu", model_id="glm-4.7-flash")
    assert receipt is not None
    assert receipt.configuration_fingerprint == tested.configuration_fingerprint
    assert receipt.credential_revision != "UNKNOWN"
    assert receipt.endpoint_revision != "UNKNOWN"
    assert receipt.model_revision != "UNKNOWN"
    assert management.global_policy().active_model_profile_id != "model-zhipu-glm-4.7-flash"


def test_connection_probe_rejects_historical_model_before_provider_call(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return _ok(request)

    service, _credentials, management = _service(tmp_path, handler)
    connection = service.create_connection(
        provider_id="zhipu",
        connection_name="历史模型隔离测试",
        endpoint_url="https://zhipu-relay.example.test/v1",
    )
    service.save_credential(
        SecretStr("zhipu-secret"), connection_id=connection.connection_id
    )
    endpoint = management.endpoint_profile(connection.endpoint_id)
    assert endpoint is not None
    credential_revision = service.credential_revision_for_connection(
        connection.connection_id
    )
    current = descriptor_to_model_profile(
        ModelDescriptor(
            provider_id=connection.provider_id,
            endpoint_id=connection.endpoint_id,
            model_id="relay-model",
            display_name="Relay Model",
            protocol_family=ProtocolFamily.OPENAI_CHAT,
            availability=ModelAvailability.AVAILABLE,
            availability_source=ModelCatalogSource.PROVIDER_API,
            availability_provenance=ModelCatalogSource.PROVIDER_API,
            catalog_source=ModelCatalogSource.PROVIDER_API,
            credential_revision=credential_revision,
            endpoint_revision=ProviderModelPool.endpoint_revision(endpoint),
            record_provenance=RecordProvenance.ENDPOINT_DISCOVERY,
        )
    )
    management.models.upsert(current)
    assert service.model_pool(connection_id=connection.connection_id) == (current,)

    management.models.upsert(
        current.model_copy(
            update={
                "credential_revision": "historical-credential-revision",
                "endpoint_revision": "historical-endpoint-revision",
            }
        )
    )
    with pytest.raises(ValueError, match="PROVIDER_MODEL_MISMATCH"):
        service.test_connection(
            draft={
                "connection_id": connection.connection_id,
                "provider_id": connection.provider_id,
                "endpoint_id": connection.endpoint_id,
                "credential_ref": connection.credential_ref,
                "model_profile_id": current.model_profile_id,
                "model_id": current.remote_model_id,
            }
        )
    assert calls == []


def test_http_draft_test_receives_explicit_provider_model_endpoint_identity(tmp_path: Path) -> None:
    service, credentials, _ = _service(tmp_path, _ok)
    service.save_credential(SecretStr("deepseek-secret"))
    service.test_connection()
    credentials.set("CHAT_PROVIDER_API_KEY:zhipu", b"zhipu-secret")
    runtime = ProductionReleaseRuntime.create(
        Settings(
            environment="production",
            production_state_root=tmp_path / "runtime-state",
            _env_file=None,
        ),
        credentials=credentials,
    )
    runtime.start()
    app = FastAPI()
    app.include_router(release_router)
    app.state.production_release_runtime = runtime
    app.state.desktop_identity_provider = ProductionLocalSessionIdentityAdapter(runtime.sessions)
    app.state.provider_configuration_service = service

    with TestClient(app) as client:
        nonce = runtime.sessions.issue_bootstrap(requester="requester", tenant_id="tenant")
        assert client.post(
            "/api/v1/copilot/desktop/system/bootstrap/exchange",
            json={"nonce": nonce},
        ).status_code == 200
        response = client.post(
            "/api/v1/copilot/desktop/system/provider/test-connection",
            json={
                "provider": "zhipu",
                "model": "glm-4.7-flash",
                "base_url": "https://open.bigmodel.cn/api/paas/v4",
                "endpoint_id": "endpoint-zhipu",
                "credential_ref": "credential:zhipu",
                "protocol_family": "OPENAI_CHAT",
                "model_profile_id": "model-zhipu-glm-4.7-flash",
            },
        )

    assert response.status_code == 200
    assert response.json()["provider"] == "zhipu"
    assert service.current_provider == "deepseek"


def test_official_provider_rejects_cross_provider_model_identity(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path, _ok)

    with pytest.raises(ValueError, match="PROVIDER_MODEL_MISMATCH"):
        service.save_configuration(provider="deepseek", model="glm-4.7-flash")
    with pytest.raises(ValueError, match="PROVIDER_MODEL_MISMATCH"):
        service.save_configuration(provider="zhipu", model="deepseek-chat")


def test_model_pool_cache_returns_stale_last_known_good_entry() -> None:
    now = [datetime.now(UTC)]
    cache = ModelPoolCache(ttl_seconds=1, now=lambda: now[0])
    cache.put(("deepseek", "endpoint-deepseek", "credential-rev-1"), ())
    now[0] += timedelta(seconds=2)
    stale = cache.get(("deepseek", "endpoint-deepseek", "credential-rev-1"))
    assert stale is not None
    assert stale.stale is True
