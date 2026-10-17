from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from embedded_copilot.api.desktop_identity import ProductionLocalSessionIdentityAdapter
from embedded_copilot.api.main import create_app
from embedded_copilot.api.v70_release_routes import router
from embedded_copilot.core.config import Settings
from embedded_copilot.production_release.credentials import (
    CHAT_PROVIDER_CREDENTIAL_PURPOSE,
    InMemoryCredentialProvider,
)
from embedded_copilot.production_release.provider import ProviderConfigurationService
from embedded_copilot.production_release.runtime import ProductionReleaseRuntime


def _client(tmp_path: Path) -> tuple[TestClient, ProductionReleaseRuntime]:
    credentials = InMemoryCredentialProvider()
    runtime = ProductionReleaseRuntime.create(
        Settings(environment="production", production_state_root=tmp_path / "state", _env_file=None),
        credentials=credentials,
    )
    runtime.start()
    app = FastAPI()
    app.include_router(router)
    app.state.production_release_runtime = runtime
    app.state.desktop_identity_provider = ProductionLocalSessionIdentityAdapter(runtime.sessions)
    app.state.provider_configuration_service = ProviderConfigurationService(
        configuration_path=runtime.layout.production_config,
        credentials=runtime.credentials,
    )
    return TestClient(app), runtime


def _session(client: TestClient, runtime: ProductionReleaseRuntime) -> None:
    nonce = runtime.sessions.issue_bootstrap(requester="requester", tenant_id="tenant")
    response = client.post("/api/v1/copilot/desktop/system/bootstrap/exchange", json={"nonce": nonce})
    assert response.status_code == 200
    assert "EmbeddedCopilotSession" in response.headers["set-cookie"]
    assert nonce not in response.text


def test_v70_release_routes_require_one_time_bootstrap_and_redact_credential(tmp_path: Path) -> None:
    client, runtime = _client(tmp_path)
    assert client.get("/api/v1/copilot/desktop/system/health").status_code == 401
    _session(client, runtime)
    response = client.put("/api/v1/copilot/desktop/system/credentials", json={"provider": "deepseek", "secret": "test-only-secret"})
    assert response.status_code == 200
    assert "test-only-secret" not in response.text
    assert runtime.credentials.get(CHAT_PROVIDER_CREDENTIAL_PURPOSE) == b"test-only-secret"


def test_v70_provider_routes_reject_noncanonical_provider_and_hide_secret_input(tmp_path: Path) -> None:
    client, runtime = _client(tmp_path)
    _session(client, runtime)

    response = client.put(
        "/api/v1/copilot/desktop/system/provider/credential",
        json={"provider": "ollama", "secret": "EMBRIX_SECRET_CANARY_unsupported"},
    )

    assert response.status_code == 422
    assert response.json()["error"] == "PROVIDER_CONFIG_INVALID"
    assert "EMBRIX_SECRET_CANARY" not in response.text


def test_v70_provider_routes_map_credential_store_failure_to_safe_error(tmp_path: Path) -> None:
    client, runtime = _client(tmp_path)
    _session(client, runtime)

    class FailingCredentialProvider:
        def get(self, purpose: str) -> bytes | None:
            from embedded_copilot.production_release.credentials import (
                CredentialUnavailable,
            )

            del purpose
            raise CredentialUnavailable("credential manager unavailable")

        def set(self, purpose: str, value: bytes) -> None:
            del purpose, value
            raise AssertionError("set must not be reached")

        def delete(self, purpose: str) -> None:
            del purpose
            raise AssertionError("delete must not be reached")

        def get_or_create_provider_binding_key(self) -> bytes:
            raise AssertionError("integrity key must not be reached")

    failing = ProviderConfigurationService(
        configuration_path=runtime.layout.production_config,
        credentials=FailingCredentialProvider(),
    )
    client.app.state.provider_configuration_service = failing

    response = client.post("/api/v1/copilot/desktop/system/provider/test-connection")

    assert response.status_code == 503
    assert response.json()["error"] == "PROVIDER_CREDENTIAL_STORE_UNAVAILABLE"
    assert "credential manager unavailable" not in response.text


def test_v70_test_connection_maps_receipt_persistence_failure_to_safe_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, runtime = _client(tmp_path)
    _session(client, runtime)

    def fail_test_connection(self: ProviderConfigurationService) -> object:
        del self
        raise OSError("raw persistence failure")

    monkeypatch.setattr(ProviderConfigurationService, "test_connection", fail_test_connection)

    response = client.post("/api/v1/copilot/desktop/system/provider/test-connection")

    assert response.status_code == 503
    assert response.json()["error"] == "PROVIDER_CONFIGURATION_UNAVAILABLE"
    assert "raw persistence failure" not in response.text


def test_v70_restore_authorization_is_single_use_and_bound_to_identity(tmp_path: Path) -> None:
    client, runtime = _client(tmp_path)
    _session(client, runtime)
    backup = client.post("/api/v1/copilot/desktop/system/backups")
    backup_id = backup.json()["backup_id"]
    authorization = client.post("/api/v1/copilot/desktop/system/restore-authorizations", json={"backup_id": backup_id}).json()["authorization"]
    first = client.post("/api/v1/copilot/desktop/system/restore", json={"backup_id": backup_id, "authorization": authorization})
    assert first.status_code == 200
    replay = client.post("/api/v1/copilot/desktop/system/restore", json={"backup_id": backup_id, "authorization": authorization})
    assert replay.status_code == 403


def test_v70_about_never_claims_clean_machine_or_signed_release(tmp_path: Path) -> None:
    client, _runtime = _client(tmp_path)
    response = client.get("/api/v1/copilot/desktop/system/about")
    assert response.status_code == 200
    assert response.json()["product_version"] == "7.1.0"
    assert response.json()["clean_machine_acceptance"] == "UNVERIFIED"
    assert response.json()["installer_signing"] == "UNAVAILABLE / UNVERIFIED"


def test_v70_diagnostics_export_does_not_include_provider_secret(tmp_path: Path) -> None:
    client, runtime = _client(tmp_path)
    _session(client, runtime)
    runtime.layout.logs.joinpath("runtime.log").write_text("token=not-for-export\nstatus=ready", encoding="utf-8")
    response = client.post("/api/v1/copilot/desktop/system/diagnostics")
    assert response.status_code == 200
    archive = next(runtime.layout.diagnostics.glob("*.zip"))
    import zipfile
    with zipfile.ZipFile(archive) as bundle:
        values = b"".join(bundle.read(name) for name in bundle.namelist())
    assert b"not-for-export" not in values


def test_production_composition_uses_session_identity_and_static_desktop(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    assets.joinpath("index.html").write_text("<html><body>desktop</body></html>", encoding="utf-8")
    runtime = ProductionReleaseRuntime.create(
        Settings(environment="production", production_state_root=tmp_path / "state", desktop_static_assets=assets, project_root=tmp_path / "projects", _env_file=None),
        credentials=InMemoryCredentialProvider(),
    )
    with TestClient(create_app(settings=runtime.settings, production_release_runtime=runtime)) as client:
        nonce = runtime.sessions.issue_bootstrap(requester="requester", tenant_id="tenant")
        assert client.post("/api/v1/copilot/desktop/system/bootstrap/exchange", json={"nonce": nonce}).status_code == 200
        assert client.get("/api/v1/copilot/desktop/system/health").status_code == 200
        assert client.get("/desktop/").status_code == 200
