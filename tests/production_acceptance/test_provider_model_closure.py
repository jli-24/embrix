from __future__ import annotations

import json
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from embedded_copilot.api.copilot_capability_routes import router as capability_router
from embedded_copilot.api.desktop_identity import ProductionLocalSessionIdentityAdapter
from embedded_copilot.api.v70_release_routes import router as release_router
from embedded_copilot.core.config import Settings
from embedded_copilot.model_gateway import (
    ModelGatewayManagementService,
    SQLiteModelGatewayStore,
    provider_presets,
)
from embedded_copilot.production_release.credentials import InMemoryCredentialProvider
from embedded_copilot.production_release.provider import (
    ProviderConfigurationService,
    ProviderConnectionStatus,
)
from embedded_copilot.production_release.runtime import ProductionReleaseRuntime


def _service(tmp_path: Path, handler) -> ProviderConfigurationService:
    credentials = InMemoryCredentialProvider()
    management = ModelGatewayManagementService(
        store=SQLiteModelGatewayStore(tmp_path / "model-gateway.sqlite3")
    )
    for preset in provider_presets():
        management.add_provider(preset)
    return ProviderConfigurationService(
        configuration_path=tmp_path / "production.json",
        credentials=credentials,
        http_transport=httpx.MockTransport(handler),
        model_gateway_management=management,
    )


def test_connection_persists_verified_model_and_protocol_response_metadata(
    tmp_path: Path,
) -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "id": "resp-zhipu-001",
                "model": "glm-4.7-flash",
                "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            },
        )

    service = _service(tmp_path, handler)
    service.save_configuration(provider="zhipu", model="glm-4.7-flash")
    service.save_credential(SecretStr("zhipu-secret"), provider="zhipu")

    projection = service.test_connection()

    assert projection.connection_status is ProviderConnectionStatus.AVAILABLE
    assert projection.connection == "verified"
    assert projection.model_id == "glm-4.7-flash"
    assert projection.model_available is True
    assert projection.verified_model == "glm-4.7-flash"
    assert projection.available_models == ("glm-4.7-flash",)
    assert projection.response_model == "glm-4.7-flash"
    assert projection.response_id == "resp-zhipu-001"
    assert projection.http_status == 200
    assert projection.protocol_adapter == "OPENAI_CHAT"
    assert requests == [
        {
            "model": "glm-4.7-flash",
            "messages": [{"role": "user", "content": "Respond with OK."}],
        }
    ]


def test_connection_failure_keeps_categorized_http_status_and_clears_model_availability(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(401, json={"error": "redacted"})

    service = _service(tmp_path, handler)
    service.save_configuration(provider="zhipu", model="glm-4.7-flash")
    service.save_credential(SecretStr("zhipu-secret"), provider="zhipu")

    projection = service.test_connection()

    assert projection.connection_status is ProviderConnectionStatus.AUTH_FAILED
    assert projection.model_available is False
    assert projection.verified_model is None
    assert projection.connection_diagnostic is not None
    assert projection.connection_diagnostic.http_status == 401


def test_http_test_connection_refreshes_runtime_capability_projection(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "id": "resp-zhipu-002",
                "model": "glm-4.7-flash",
                "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            },
        )

    credentials = InMemoryCredentialProvider()
    runtime = ProductionReleaseRuntime.create(
        Settings(
            environment="production",
            production_state_root=tmp_path / "state",
            _env_file=None,
        ),
        credentials=credentials,
    )
    runtime.start()
    management = ModelGatewayManagementService(
        store=SQLiteModelGatewayStore(tmp_path / "model-gateway.sqlite3")
    )
    for preset in provider_presets():
        management.add_provider(preset)
    service = ProviderConfigurationService(
        configuration_path=tmp_path / "production.json",
        credentials=credentials,
        http_transport=httpx.MockTransport(handler),
        model_gateway_management=management,
    )
    app = FastAPI()
    app.include_router(release_router)
    app.include_router(capability_router)
    app.state.production_release_runtime = runtime
    app.state.desktop_identity_provider = ProductionLocalSessionIdentityAdapter(
        runtime.sessions
    )
    app.state.provider_configuration_service = service
    app.state.desktop_copilot_chat_runtime = object()
    app.state.runtime_component_statuses = {"chat_model": "unavailable"}
    app.state.runtime_capability_statuses = {
        "chat_model": "unavailable",
        "engineering_analysis_completion": "unavailable",
        "provider_configuration": "available",
    }

    with TestClient(app) as client:
        nonce = runtime.sessions.issue_bootstrap(requester="requester", tenant_id="tenant")
        assert client.post(
            "/api/v1/copilot/desktop/system/bootstrap/exchange",
            json={"nonce": nonce},
        ).status_code == 200
        assert client.put(
            "/api/v1/copilot/desktop/system/provider/configuration",
            json={"provider": "zhipu", "model": "glm-4.7-flash"},
        ).status_code == 200
        assert client.put(
            "/api/v1/copilot/desktop/system/provider/credential",
            json={"provider": "zhipu", "secret": "zhipu-secret"},
        ).status_code == 200
        tested = client.post(
            "/api/v1/copilot/desktop/system/provider/test-connection"
        )
        capabilities = client.get("/api/v1/copilot/capabilities")

    assert tested.status_code == 200
    assert tested.json()["connection"] == "verified"
    assert tested.json()["model_available"] is True
    by_name = {item["capability"]: item for item in capabilities.json()}
    assert by_name["CHAT_MODEL"]["status"] == "AVAILABLE"
    assert by_name["CHAT_ANALYSIS"]["status"] == "AVAILABLE"
