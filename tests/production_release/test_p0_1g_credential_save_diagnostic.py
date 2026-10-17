from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from embedded_copilot.api.desktop_identity import ProductionLocalSessionIdentityAdapter
from embedded_copilot.api.v70_release_models import ProviderCredentialInput
from embedded_copilot.api.v70_release_routes import router
from embedded_copilot.core.config import Settings
from embedded_copilot.production_release import credential_save_diagnostic as diagnostic
from embedded_copilot.production_release.credential_save_diagnostic import (
    ConfigurationDispatchDiagnosticMiddleware,
)
from embedded_copilot.production_release.credentials import (
    CHAT_PROVIDER_CREDENTIAL_PURPOSE,
    PROVIDER_BINDING_INTEGRITY_PURPOSE,
    CredentialUnavailable,
    InMemoryCredentialProvider,
)
from embedded_copilot.production_release.provider import ProviderConfigurationService
from embedded_copilot.production_release.provider import ProviderConfigurationStore
from embedded_copilot.production_release.runtime import ProductionReleaseRuntime


def _events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _service(tmp_path: Path) -> tuple[ProviderConfigurationService, InMemoryCredentialProvider]:
    credentials = InMemoryCredentialProvider()
    service = ProviderConfigurationService(
        configuration_path=tmp_path / "production.json",
        credentials=credentials,
    )
    return service, credentials


def _client(tmp_path: Path) -> tuple[TestClient, ProductionReleaseRuntime]:
    credentials = InMemoryCredentialProvider()
    runtime = ProductionReleaseRuntime.create(
        Settings(environment="production", production_state_root=tmp_path / "state", _env_file=None),
        credentials=credentials,
    )
    runtime.start()
    app = FastAPI()
    app.add_middleware(ConfigurationDispatchDiagnosticMiddleware)
    app.include_router(router)
    app.state.production_release_runtime = runtime
    app.state.desktop_identity_provider = ProductionLocalSessionIdentityAdapter(runtime.sessions)
    app.state.provider_configuration_service = ProviderConfigurationService(
        configuration_path=runtime.layout.production_config,
        credentials=runtime.credentials,
    )
    return TestClient(app), runtime


def _exchange_session(client: TestClient, runtime: ProductionReleaseRuntime) -> None:
    nonce = runtime.sessions.issue_bootstrap(requester="requester", tenant_id="tenant")
    response = client.post(
        "/api/v1/copilot/desktop/system/bootstrap/exchange",
        json={"nonce": nonce},
    )
    assert response.status_code == 200


def test_credential_save_diagnostic_is_disabled_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.delenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, raising=False)
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    with diagnostic.request_scope("request-disabled"):
        diagnostic.emit("HTTP_ROUTE_ENTERED", result="ENTERED")

    assert not output.exists()


def test_configuration_request_is_observed_before_auth_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)
    app = FastAPI()
    app.add_middleware(ConfigurationDispatchDiagnosticMiddleware)
    app.include_router(router)

    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.put(
        "/api/v1/copilot/desktop/system/provider/configuration",
        json={"provider": "deepseek", "model": "EMBRIX_SECRET_CANARY_model"},
    )

    assert response.status_code == 401
    rows = _events(output)
    assert [row["stage"] for row in rows] == [
        "HTTP_CONFIGURATION_REQUEST_RECEIVED",
        "HTTP_CONFIGURATION_RESPONSE",
    ]
    assert len({row["request_id"] for row in rows}) == 1
    assert rows[-1]["safe_http_status"] == 401
    content = output.read_text(encoding="utf-8")
    assert "EMBRIX_SECRET_CANARY" not in content
    assert all(
        marker not in content
        for marker in ("request_body", "headers", "Authorization", "Cookie", "nonce", "SecretStr")
    )


def test_configuration_middleware_ignores_unrelated_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)
    app = FastAPI()
    app.add_middleware(ConfigurationDispatchDiagnosticMiddleware)

    from fastapi.testclient import TestClient

    assert TestClient(app).get("/unrelated").status_code == 404
    assert not output.exists()


def test_configuration_route_emits_route_service_store_and_response_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)
    client, runtime = _client(tmp_path)
    _exchange_session(client, runtime)

    response = client.put(
        "/api/v1/copilot/desktop/system/provider/configuration",
        json={"provider": "deepseek", "model": "deepseek-chat"},
    )

    assert response.status_code == 200
    rows = _events(output)
    assert [row["stage"] for row in rows] == [
        "HTTP_CONFIGURATION_REQUEST_RECEIVED",
        "CONFIGURATION_ROUTE_ENTERED",
        "CONFIGURATION_SERVICE_ENTERED",
        "CONFIGURATION_STORE_SAVE_BEGIN",
        "CONFIGURATION_STORE_SAVE_SUCCESS",
        "CONFIGURATION_ROUTE_SUCCESS",
        "HTTP_CONFIGURATION_RESPONSE",
    ]
    assert len({row["request_id"] for row in rows}) == 1
    assert rows[-1]["safe_http_status"] == 200
    content = output.read_text(encoding="utf-8")
    assert "request_body" not in content
    assert "headers" not in content
    assert "deepseek-chat" not in content


def test_configuration_store_failure_emits_safe_failure_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)
    service, _ = _service(tmp_path)

    def fail_write(store: object, value: object) -> None:
        del store, value
        raise OSError("EMBRIX_SECRET_CANARY_raw_exception")

    monkeypatch.setattr(ProviderConfigurationStore, "_write", fail_write)
    with diagnostic.request_scope("configuration-store-failure"):
        with pytest.raises(OSError):
            service.save_configuration(provider="deepseek", model="deepseek-chat")

    rows = _events(output)
    assert [row["stage"] for row in rows] == [
        "CONFIGURATION_SERVICE_ENTERED",
        "CONFIGURATION_STORE_SAVE_BEGIN",
        "CONFIGURATION_STORE_SAVE_FAILURE",
    ]
    assert rows[-1]["safe_error_class"] == "DIAGNOSTIC_ERROR"
    content = output.read_text(encoding="utf-8")
    assert "EMBRIX_SECRET_CANARY" not in content
    assert "deepseek-chat" not in content


def test_credential_save_diagnostic_allowlist_and_success_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)
    service, credentials = _service(tmp_path)

    result = service.save_credential(SecretStr("EMBRIX_SECRET_CANARY_success"))

    assert result.credential_status == "CONFIGURED"
    rows = _events(output)
    assert {key for row in rows for key in row} <= diagnostic.ALLOWED_EVENT_FIELDS
    stages = [row["stage"] for row in rows]
    assert stages[:8] == [
        "PROVIDER_SERVICE_ENTERED",
        "SECRET_VALIDATION_PASSED",
        "INTEGRITY_IDENTITY_BEGIN",
        "INTEGRITY_TARGET_EXISTS",
        "INTEGRITY_TARGET_CREATE_BEGIN",
        "INTEGRITY_TARGET_CREATE_SUCCESS",
        "CREDENTIAL_FINGERPRINT_READY",
        "BINDING_INVALIDATE_BEGIN",
    ]
    assert "BINDING_INVALIDATE_SUCCESS" in stages
    assert "PRIMARY_CREDENTIAL_WRITE_SUCCESS" in stages
    assert "PRIMARY_CREDENTIAL_READBACK_BEGIN" in stages
    assert "PRIMARY_CREDENTIAL_READBACK_SUCCESS" in stages
    assert "SAFE_CONFIG_PERSIST_SUCCESS" in stages
    assert "PROVIDER_PROJECTION_RETURNED" in stages
    assert "EMBRIX_SECRET_CANARY" not in output.read_text(encoding="utf-8")
    assert credentials.get(CHAT_PROVIDER_CREDENTIAL_PURPOSE) == b"EMBRIX_SECRET_CANARY_success"


def test_credential_readback_failure_is_observable_without_secret_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    class WriteOnlyCredentialProvider(InMemoryCredentialProvider):
        def get(self, purpose: str) -> bytes | None:
            if purpose == CHAT_PROVIDER_CREDENTIAL_PURPOSE:
                return None
            return super().get(purpose)

    service = ProviderConfigurationService(
        configuration_path=tmp_path / "production.json",
        credentials=WriteOnlyCredentialProvider(),
    )

    with pytest.raises(CredentialUnavailable):
        service.save_credential(SecretStr("EMBRIX_SECRET_CANARY_readback"))

    rows = _events(output)
    stages = [row["stage"] for row in rows]
    assert "PRIMARY_CREDENTIAL_READBACK_BEGIN" in stages
    assert "PRIMARY_CREDENTIAL_READBACK_FAILURE" in stages
    assert "PRIMARY_CREDENTIAL_READBACK_SUCCESS" not in stages
    content = output.read_text(encoding="utf-8")
    assert "EMBRIX_SECRET_CANARY" not in content


def test_integrity_write_failure_is_observable_before_binding_invalidation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    class FailingIntegrityProvider(InMemoryCredentialProvider):
        def set(self, purpose: str, value: bytes) -> None:
            if purpose == PROVIDER_BINDING_INTEGRITY_PURPOSE:
                error = CredentialUnavailable("credential manager unavailable")
                error.win32_error_code = 5  # type: ignore[attr-defined]
                raise error
            super().set(purpose, value)

    credentials = FailingIntegrityProvider()
    service = ProviderConfigurationService(
        configuration_path=tmp_path / "production.json",
        credentials=credentials,
    )

    with pytest.raises(CredentialUnavailable):
        service.save_credential(SecretStr("EMBRIX_SECRET_CANARY_integrity"))

    rows = _events(output)
    stages = [row["stage"] for row in rows]
    assert "INTEGRITY_TARGET_CREATE_FAILURE" in stages
    assert "BINDING_INVALIDATE_BEGIN" not in stages
    failure = next(row for row in rows if row["stage"] == "INTEGRITY_TARGET_CREATE_FAILURE")
    assert failure["safe_error_class"] == "CREDENTIAL_STORE_UNAVAILABLE"
    assert failure["safe_win32_error_code"] == 5
    assert "EMBRIX_SECRET_CANARY" not in output.read_text(encoding="utf-8")


def test_provider_credential_route_emits_only_safe_route_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)
    client, runtime = _client(tmp_path)
    _exchange_session(client, runtime)

    response = client.put(
        "/api/v1/copilot/desktop/system/provider/credential",
        json={"provider": "deepseek", "secret": "EMBRIX_SECRET_CANARY_route"},
    )

    assert response.status_code == 200
    rows = _events(output)
    assert rows[0]["stage"] == "HTTP_ROUTE_ENTERED"
    assert rows[-1]["stage"] == "HTTP_ROUTE_SUCCESS"
    assert {key for row in rows for key in row} <= diagnostic.ALLOWED_EVENT_FIELDS
    content = output.read_text(encoding="utf-8")
    assert "EMBRIX_SECRET_CANARY" not in content
    assert "request_body" not in content
    assert "headers" not in content
    assert "SecretStr" not in content


def test_provider_credential_input_hides_secret_in_validation_errors() -> None:
    with pytest.raises(ValueError) as error:
        ProviderCredentialInput(provider="deepseek", secret="")
    assert "EMBRIX_SECRET_CANARY" not in str(error.value)
