from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from embedded_copilot.api.desktop_identity import ProductionLocalSessionIdentityAdapter
from embedded_copilot.api.model_gateway_routes import router as model_gateway_router
from embedded_copilot.api.v70_release_routes import router as v70_router
from embedded_copilot.model_gateway import (
    AccountUsageSnapshot,
    ModelAvailability,
    ModelCatalogSource,
    ModelDescriptor,
    ModelGatewayManagementService,
    ProtocolFamily,
    ProviderProfile,
    ProviderUsageCapability,
    UsageSnapshotStatus,
)
from embedded_copilot.production_release.credentials import InMemoryCredentialProvider
from embedded_copilot.production_release.provider import ProviderConfigurationService
from embedded_copilot.production_release.provider_verification import (
    ProviderVerificationReceiptStore,
)
from embedded_copilot.production_release.session import LocalSessionManager


class _Discovery:
    def discover(
        self,
        endpoint,
        *,
        connection_id: str | None = None,
        credential_revision: str | None = None,
    ) -> tuple[ModelDescriptor, ...]:
        model_id = "model-a" if endpoint.endpoint_id.endswith("personal") else "model-c"
        return (
            ModelDescriptor(
                provider_id=endpoint.provider_id,
                endpoint_id=endpoint.endpoint_id,
                model_id=model_id,
                display_name=model_id,
                protocol_family=endpoint.protocol_family,
                availability=ModelAvailability.AVAILABLE,
                availability_source=ModelCatalogSource.PROVIDER_API,
                catalog_source=ModelCatalogSource.PROVIDER_API,
                discovered_at=datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
                credential_revision=credential_revision,
            ),
        )


class _Usage:
    def read(
        self,
        endpoint,
        credential_ref: str | None,
        *,
        connection_id: str | None = None,
        credential_revision: str | None = None,
    ) -> AccountUsageSnapshot:
        balance = 10.0 if endpoint.endpoint_id.endswith("personal") else 100.0
        return AccountUsageSnapshot(
            provider_id=endpoint.provider_id,
            endpoint_id=endpoint.endpoint_id,
            connection_id=connection_id,
            credential_ref=credential_ref,
            credential_revision=credential_revision,
            balance=balance,
            current_balance=balance,
            status=UsageSnapshotStatus.AVAILABLE,
        )


def _app(
    tmp_path: Path,
    *,
    http_transport: object | None = None,
) -> tuple[TestClient, ModelGatewayManagementService, ProviderConfigurationService]:
    management = ModelGatewayManagementService()
    management.add_provider(
        ProviderProfile(
            provider_id="zhipu",
            display_name="智谱 AI",
            supported_protocols=(ProtocolFamily.OPENAI_CHAT,),
            usage_capabilities=(ProviderUsageCapability.USAGE_QUERY,),
        )
    )
    management.set_account_usage_port(_Usage())
    credentials = InMemoryCredentialProvider()
    provider = ProviderConfigurationService(
        configuration_path=tmp_path / "provider.json",
        credentials=credentials,
        http_transport=http_transport,
        model_gateway_management=management,
        model_discovery=_Discovery(),
        verification_store=ProviderVerificationReceiptStore(
            tmp_path / "verification.sqlite3"
        ),
    )
    app = FastAPI()
    app.include_router(model_gateway_router)
    app.include_router(v70_router)
    app.state.model_gateway_management_service = management
    app.state.provider_configuration_service = provider
    sessions = LocalSessionManager()
    token = sessions.exchange(sessions.issue_bootstrap(requester="owner-1", tenant_id="tenant-1"))
    app.state.desktop_identity_provider = ProductionLocalSessionIdentityAdapter(sessions)
    app.state.test_sessions = sessions
    client = TestClient(app)
    client.cookies.set("EmbeddedCopilotSession", token)
    return client, management, provider


def _create(client: TestClient, endpoint_id: str, name: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/model-gateway/provider-connections",
        json={
            "provider_id": "zhipu",
            "connection_name": name,
            "endpoint_id": endpoint_id,
            "endpoint_url": f"https://{endpoint_id}.example.test/v1",
            "protocol_family": "OPENAI_CHAT",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _credential(client: TestClient, connection_id: str, secret: str) -> None:
    response = client.put(
        f"/api/v1/model-gateway/provider-connections/{connection_id}/credential",
        json={"secret": secret},
    )
    assert response.status_code == 200, response.text
    assert secret not in response.text


def test_connection_crud_keeps_identity_and_tombstones(tmp_path: Path) -> None:
    client, _management, _provider = _app(tmp_path)
    created = _create(client, "endpoint-zhipu-crud", "个人 API")
    connection_id = created["connection_id"]

    listed = client.get("/api/v1/model-gateway/provider-connections")
    assert listed.status_code == 200
    row = next(item for item in listed.json() if item["connection_id"] == connection_id)
    assert row["provider_display_name"] == "智谱 AI"
    assert row["endpoint_id"] == "endpoint-zhipu-crud"
    assert row["credential_configured"] is False

    renamed = client.patch(
        f"/api/v1/model-gateway/provider-connections/{connection_id}",
        json={"connection_name": "个人 API（重命名）"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["connection_id"] == connection_id
    assert renamed.json()["connection_name"] == "个人 API（重命名）"

    endpoint_changed = client.patch(
        f"/api/v1/model-gateway/provider-connections/{connection_id}",
        json={"endpoint_url": "https://zhipu-relay.example.test/v2"},
    )
    assert endpoint_changed.status_code == 200
    assert endpoint_changed.json()["connection_id"] == connection_id
    assert endpoint_changed.json()["endpoint_id"] == "endpoint-zhipu-crud"
    assert endpoint_changed.json()["endpoint_url"].endswith("/v2")

    deleted = client.delete(
        f"/api/v1/model-gateway/provider-connections/{connection_id}"
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"connection_id": connection_id, "status": "DELETED"}
    detail = client.get(
        f"/api/v1/model-gateway/provider-connections/{connection_id}"
    )
    assert detail.status_code == 200
    assert detail.json()["status"] == "DELETED"


def test_multi_protocol_connection_requires_explicit_backend_supported_protocol(
    tmp_path: Path,
) -> None:
    client, management, _provider = _app(tmp_path)
    management.add_provider(
        ProviderProfile(
            provider_id="openai",
            display_name="OpenAI",
            supported_protocols=(
                ProtocolFamily.OPENAI_RESPONSES,
                ProtocolFamily.OPENAI_CHAT,
            ),
        )
    )

    missing = client.post(
        "/api/v1/model-gateway/provider-connections",
        json={
            "provider_id": "openai",
            "connection_name": "OpenAI 主连接",
            "endpoint_url": "https://api.openai.com/v1",
        },
    )
    assert missing.status_code == 422
    assert missing.json()["detail"] == "PROVIDER_PROTOCOL_REQUIRED"

    selected = client.post(
        "/api/v1/model-gateway/provider-connections",
        json={
            "provider_id": "openai",
            "connection_name": "OpenAI Responses",
            "endpoint_url": "https://api.openai.com/v1",
            "protocol_family": "OPENAI_RESPONSES",
        },
    )
    assert selected.status_code == 201, selected.text
    assert selected.json()["protocol_family"] == "OPENAI_RESPONSES"


def test_connection_scoped_discovery_usage_and_governance_projection(
    tmp_path: Path,
) -> None:
    client, _management, _provider = _app(tmp_path)
    personal = _create(client, "endpoint-zhipu-personal", "个人 API")
    company = _create(client, "endpoint-zhipu-company", "公司 API")
    _credential(client, personal["connection_id"], "personal-secret")
    _credential(client, company["connection_id"], "company-secret")

    personal_models = client.post(
        f"/api/v1/model-gateway/provider-connections/{personal['connection_id']}/models/refresh"
    )
    company_models = client.post(
        f"/api/v1/model-gateway/provider-connections/{company['connection_id']}/models/refresh"
    )
    assert personal_models.status_code == company_models.status_code == 200
    assert [item["remote_model_id"] for item in personal_models.json()["models"]] == ["model-a"]
    assert [item["remote_model_id"] for item in company_models.json()["models"]] == ["model-c"]
    assert personal_models.json()["raw_model_count"] == 1
    assert personal_models.json()["normalized_model_count"] == 1
    assert personal["connection_id"] == personal_models.json()["connection_id"]
    assert "personal-secret" not in personal_models.text
    assert "company-secret" not in company_models.text

    personal_usage = client.get(
        f"/api/v1/model-gateway/provider-connections/{personal['connection_id']}/usage"
    )
    company_usage = client.get(
        f"/api/v1/model-gateway/provider-connections/{company['connection_id']}/usage"
    )
    assert personal_usage.status_code == company_usage.status_code == 200
    assert personal_usage.json()["connection_id"] == personal["connection_id"]
    assert personal_usage.json()["current_balance"] == 10.0
    assert company_usage.json()["current_balance"] == 100.0

    governance = client.get("/api/v1/model-gateway/governance/available-models")
    assert governance.status_code == 200, governance.text
    rows = {item["connection_id"]: item for item in governance.json()}
    assert rows[personal["connection_id"]]["connection_name"] == "个人 API"
    assert rows[personal["connection_id"]]["provider_display_name"] == "智谱 AI"
    assert rows[personal["connection_id"]]["protocol_family"] == "OPENAI_CHAT"
    assert rows[personal["connection_id"]]["discovered_at"] is not None


def test_test_connection_requires_current_connection_model_identity(tmp_path: Path) -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "probe-id",
                "model": payload["model"],
                "choices": [{"message": {"content": "ok"}}],
            },
            request=request,
        )

    client, _management, _provider = _app(
        tmp_path, http_transport=httpx.MockTransport(transport)
    )
    personal = _create(client, "endpoint-zhipu-personal", "个人 API")
    company = _create(client, "endpoint-zhipu-company", "公司 API")
    _credential(client, personal["connection_id"], "personal-secret")
    _credential(client, company["connection_id"], "company-secret")
    personal_refresh = client.post(
        f"/api/v1/model-gateway/provider-connections/{personal['connection_id']}/models/refresh"
    )
    company_refresh = client.post(
        f"/api/v1/model-gateway/provider-connections/{company['connection_id']}/models/refresh"
    )
    personal_model = personal_refresh.json()["models"][0]
    company_model = company_refresh.json()["models"][0]

    passed = client.post(
        f"/api/v1/model-gateway/provider-connections/{personal['connection_id']}/test-connection",
        json={
            "model_profile_id": personal_model["model_profile_id"],
            "model_id": personal_model["remote_model_id"],
        },
    )
    assert passed.status_code == 200, passed.text
    assert passed.json()["verification"]["current"] == 1

    crossed = client.post(
        f"/api/v1/model-gateway/provider-connections/{personal['connection_id']}/test-connection",
        json={
            "model_profile_id": company_model["model_profile_id"],
            "model_id": company_model["remote_model_id"],
        },
    )
    assert crossed.status_code == 409
    assert crossed.json()["detail"] == "PROVIDER_MODEL_MISMATCH"


def test_anonymous_canonical_diagnostic_never_calls_provider(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"id": "probe", "model": "model-a", "choices": [{"message": {"content": "ok"}}]}, request=request)

    client, _management, _provider = _app(tmp_path, http_transport=httpx.MockTransport(transport))
    connection = _create(client, "endpoint-zhipu-anon", "匿名拒绝")
    connection_id = connection["connection_id"]
    _credential(client, connection_id, "test-only-secret")
    refreshed = client.post(f"/api/v1/model-gateway/provider-connections/{connection_id}/models/refresh")
    model = refreshed.json()["models"][0]
    client.cookies.clear()
    response = client.post(
        f"/api/v1/model-gateway/provider-connections/{connection_id}/test-connection",
        json={"model_profile_id": model["model_profile_id"], "model_id": model["remote_model_id"]},
    )
    assert response.status_code == 401
    assert calls == []


def test_quota_query_get_routes_require_session_before_external_port(tmp_path: Path) -> None:
    client, management, _provider = _app(tmp_path)
    connection = _create(client, "endpoint-zhipu-quota", "额度诊断")
    connection_id = connection["connection_id"]

    class _CountingUsage(_Usage):
        def __init__(self) -> None:
            self.calls = 0

        def read(self, endpoint, credential_ref, *, connection_id=None, credential_revision=None):
            self.calls += 1
            return super().read(endpoint, credential_ref, connection_id=connection_id, credential_revision=credential_revision)

    usage = _CountingUsage()
    management.set_account_usage_port(usage, ttl_seconds=1)
    assert client.get("/api/v1/model-gateway/provider-connections").status_code == 200
    assert usage.calls >= 1
    before = usage.calls
    client.cookies.clear()
    for path in (
        "/api/v1/model-gateway/provider-connections",
        f"/api/v1/model-gateway/provider-connections/{connection_id}",
        f"/api/v1/model-gateway/provider-connections/{connection_id}/usage",
    ):
        assert client.get(path).status_code == 401
    assert usage.calls == before


def test_runtime_health_projects_only_current_connection_receipts(tmp_path: Path) -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "runtime-health-probe",
                "model": payload["model"],
                "choices": [{"message": {"content": "ok"}}],
            },
            request=request,
        )

    client, _management, _provider = _app(
        tmp_path, http_transport=httpx.MockTransport(transport)
    )
    connection = _create(client, "endpoint-zhipu-health", "健康投影")
    connection_id = connection["connection_id"]
    _credential(client, connection_id, "runtime-health-secret-v1")
    refreshed = client.post(
        f"/api/v1/model-gateway/provider-connections/{connection_id}/models/refresh"
    )
    model = refreshed.json()["models"][0]
    tested = client.post(
        f"/api/v1/model-gateway/provider-connections/{connection_id}/test-connection",
        json={
            "model_profile_id": model["model_profile_id"],
            "model_id": model["remote_model_id"],
        },
    )
    assert tested.status_code == 200, tested.text

    history = client.get(
        f"/api/v1/model-gateway/provider-connections/{connection_id}/test-history"
    )
    assert history.status_code == 200, history.text
    assert len(history.json()) == 1
    test_record = history.json()[0]
    assert test_record["test_id"]
    assert test_record["connection_id"] == connection_id
    assert test_record["provider_id"] == "zhipu"
    assert test_record["endpoint_id"] == "endpoint-zhipu-health"
    assert test_record["credential_ref"] == connection["credential_ref"]
    assert test_record["status"] == "AVAILABLE"
    assert "runtime-health-secret-v1" not in history.text

    health = client.get(
        f"/api/v1/model-gateway/provider-connections/{connection_id}/runtime-health"
    )
    assert health.status_code == 200, health.text
    assert len(health.json()) == 1
    item = health.json()[0]
    assert item["identity"] == {
        "provider_id": "zhipu",
        "endpoint_id": "endpoint-zhipu-health",
        "credential_ref": connection["credential_ref"],
        "model_profile_id": model["model_profile_id"],
        "model_id": "model-c",
    }
    assert item["endpoint_status"] == "AVAILABLE"
    assert item["last_success_at"] is not None
    assert item["last_failure_at"] is None
    assert item["error_rate"] == 0.0
    assert item["latency_ms"] is not None
    assert item["quota_status"] == "AVAILABLE"
    assert item["observed_at"] is not None
    assert item["sample_count"] == 1
    assert item["evidence_freshness"] == "CURRENT"
    assert "runtime-health-secret-v1" not in health.text

    _credential(client, connection_id, "runtime-health-secret-v2")
    refreshed_after_rotation = client.post(
        f"/api/v1/model-gateway/provider-connections/{connection_id}/models/refresh"
    )
    assert refreshed_after_rotation.status_code == 200
    after_rotation = client.get(
        f"/api/v1/model-gateway/provider-connections/{connection_id}/runtime-health"
    )
    assert after_rotation.status_code == 200
    rotated = after_rotation.json()[0]
    assert rotated["sample_count"] == 0
    assert rotated["endpoint_status"] == "UNKNOWN"
    assert rotated["last_success_at"] is None
    assert rotated["last_failure_at"] is None
    assert rotated["error_rate"] is None
    assert rotated["latency_ms"] is None
    assert rotated["quota_status"] == "UNKNOWN"
    assert rotated["observed_at"] is None
    assert rotated["evidence_freshness"] == "STALE"


def test_legacy_provider_routes_delegate_only_when_unique(tmp_path: Path) -> None:
    client, _management, _provider = _app(tmp_path)
    personal = _create(client, "endpoint-zhipu-personal", "个人 API")
    unique = client.get(
        "/api/v1/copilot/desktop/system/provider?provider_id=zhipu"
    )
    assert unique.status_code == 200
    assert unique.json()["endpoint_id"] == personal["endpoint_id"]

    _create(client, "endpoint-zhipu-company", "公司 API")
    ambiguous = client.get(
        "/api/v1/copilot/desktop/system/provider?provider_id=zhipu"
    )
    assert ambiguous.status_code == 409
    assert ambiguous.json()["error"] == "PROVIDER_CONNECTION_AMBIGUOUS"


def test_disabled_deleted_and_quarantined_connections_fail_closed(
    tmp_path: Path,
) -> None:
    client, management, _provider = _app(tmp_path)
    disabled = _create(client, "endpoint-zhipu-disabled", "禁用连接")
    disabled_patch = client.patch(
        f"/api/v1/model-gateway/provider-connections/{disabled['connection_id']}",
        json={"enabled": False},
    )
    assert disabled_patch.status_code == 200
    assert disabled_patch.json()["status"] == "DISABLED"
    refresh_disabled = client.post(
        f"/api/v1/model-gateway/provider-connections/{disabled['connection_id']}/models/refresh"
    )
    usage_refresh_disabled = client.post(
        f"/api/v1/model-gateway/provider-connections/{disabled['connection_id']}/usage/refresh"
    )
    assert refresh_disabled.status_code == usage_refresh_disabled.status_code == 409
    assert refresh_disabled.json()["detail"] == "PROVIDER_CONNECTION_DISABLED"

    quarantined = _create(client, "endpoint-zhipu-quarantined", "隔离连接")
    management.update_connection(
        quarantined["connection_id"], status="QUARANTINED"
    )
    quarantined_refresh = client.post(
        f"/api/v1/model-gateway/provider-connections/{quarantined['connection_id']}/models/refresh"
    )
    assert quarantined_refresh.status_code == 409
    assert quarantined_refresh.json()["detail"] == "PROVIDER_CONNECTION_QUARANTINED"

    deleted = _create(client, "endpoint-zhipu-deleted", "删除连接")
    deleted_response = client.delete(
        f"/api/v1/model-gateway/provider-connections/{deleted['connection_id']}"
    )
    assert deleted_response.status_code == 200
    deleted_refresh = client.post(
        f"/api/v1/model-gateway/provider-connections/{deleted['connection_id']}/models/refresh"
    )
    assert deleted_refresh.status_code == 410
    assert deleted_refresh.json()["detail"] == "PROVIDER_CONNECTION_DELETED"


def test_credential_validation_and_exception_responses_never_echo_secret(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, _management, _provider = _app(tmp_path)
    created = _create(client, "endpoint-zhipu-security", "安全连接")
    secret = "EMBRIX_SECRET_CANARY"
    invalid = client.put(
        f"/api/v1/model-gateway/provider-connections/{created['connection_id']}/credential",
        json={"secret": "x" * 4097 + secret},
    )
    assert invalid.status_code == 422
    assert secret not in invalid.text

    def fail_save(self, secret_value, **kwargs):
        del self, secret_value, kwargs
        raise ValueError(f"provider rejected {secret}")

    monkeypatch.setattr(ProviderConfigurationService, "save_credential", fail_save)
    failed = client.put(
        f"/api/v1/model-gateway/provider-connections/{created['connection_id']}/credential",
        json={"secret": secret},
    )
    assert failed.status_code == 422
    assert secret not in failed.text
