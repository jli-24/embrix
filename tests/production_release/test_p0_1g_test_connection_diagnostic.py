from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from embedded_copilot.api.desktop_identity import ProductionLocalSessionIdentityAdapter
from embedded_copilot.api.v70_release_routes import router
from embedded_copilot.core.config import Settings
from embedded_copilot.production_release import test_connection_diagnostic as diagnostic
from embedded_copilot.production_release.credentials import InMemoryCredentialProvider
from embedded_copilot.production_release.provider import (
    DEEPSEEK_BASE_URL,
    ProviderConfigurationService,
    ProviderConfigurationStore,
)
from embedded_copilot.production_release.runtime import ProductionReleaseRuntime

TEST_CONNECTION_PATH = "/api/v1/copilot/desktop/system/provider/test-connection"


def _events(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _transport(counter: list[int], *, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        counter.append(1)
        assert str(request.url) == f"{DEEPSEEK_BASE_URL}/chat/completions"
        if status != 200:
            return httpx.Response(status, json={"error": "redacted"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]},
        )

    return httpx.MockTransport(handler)


def _client(
    tmp_path: Path,
    counter: list[int],
    *,
    middleware: bool = True,
) -> tuple[TestClient, ProductionReleaseRuntime, ProviderConfigurationService]:
    credentials = InMemoryCredentialProvider()
    runtime = ProductionReleaseRuntime.create(
        Settings(environment="production", production_state_root=tmp_path / "state", _env_file=None),
        credentials=credentials,
    )
    runtime.start()
    app = FastAPI()
    if middleware:
        from embedded_copilot.production_release.test_connection_diagnostic import (
            TestConnectionDispatchDiagnosticMiddleware,
        )

        app.add_middleware(TestConnectionDispatchDiagnosticMiddleware)
    app.include_router(router)
    app.state.production_release_runtime = runtime
    app.state.desktop_identity_provider = ProductionLocalSessionIdentityAdapter(runtime.sessions)
    service = ProviderConfigurationService(
        configuration_path=runtime.layout.production_config,
        credentials=runtime.credentials,
        http_transport=_transport(counter),
        now=lambda: datetime(2026, 8, 29, tzinfo=UTC),
    )
    app.state.provider_configuration_service = service
    return TestClient(app), runtime, service


def _exchange(client: TestClient, runtime: ProductionReleaseRuntime) -> None:
    nonce = runtime.sessions.issue_bootstrap(requester="requester", tenant_id="tenant")
    response = client.post(
        "/api/v1/copilot/desktop/system/bootstrap/exchange",
        json={"nonce": nonce},
    )
    assert response.status_code == 200


def test_test_connection_diagnostic_is_disabled_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.delenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, raising=False)
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    with diagnostic.request_scope("request-disabled"):
        diagnostic.emit(
            "HTTP_TEST_CONNECTION_REQUEST_RECEIVED",
            result="RECEIVED",
            provider="deepseek",
        )

    assert not output.exists()


def test_test_connection_diagnostic_rejects_untrusted_request_id_and_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)

    with diagnostic.request_scope("TEST_CONNECTION_CANARY_API_KEY_DO_NOT_LEAK"):
        diagnostic.emit(
            "HTTP_TEST_CONNECTION_REQUEST_RECEIVED",
            result="TEST_CONNECTION_CANARY_API_KEY_DO_NOT_LEAK",
            provider="deepseek",
        )
        diagnostic.emit(
            "HTTP_TEST_CONNECTION_REQUEST_RECEIVED",
            result="RECEIVED",
            provider="deepseek",
        )

    rows = _events(output)
    assert len(rows) == 1
    assert rows[0]["result"] == "RECEIVED"
    assert isinstance(rows[0]["request_id"], str)
    assert diagnostic._REQUEST_ID_PATTERN.fullmatch(rows[0]["request_id"]) is not None
    assert "TEST_CONNECTION_CANARY_API_KEY_DO_NOT_LEAK" not in output.read_text(encoding="utf-8")


def test_test_connection_request_is_observed_before_auth_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)
    client, _, _ = _client(tmp_path, [], middleware=True)

    response = client.post(TEST_CONNECTION_PATH)

    assert response.status_code == 401
    rows = _events(output)
    assert [row["stage"] for row in rows] == [
        "HTTP_TEST_CONNECTION_REQUEST_RECEIVED",
        "HTTP_TEST_CONNECTION_RESPONSE",
    ]
    assert rows[-1]["safe_http_status"] == 401
    assert len({row["request_id"] for row in rows}) == 1
    content = output.read_text(encoding="utf-8")
    assert "TEST_CONNECTION_CANARY_API_KEY_DO_NOT_LEAK" not in content
    assert all(
        marker not in content
        for marker in ("request_body", "headers", "Authorization", "Cookie", "nonce", "SecretStr")
    )


def test_test_connection_success_emits_fixed_route_service_provider_and_receipt_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.delenv("EMBRIX_CREDENTIAL_SAVE_DIAGNOSTIC", raising=False)
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)
    counter: list[int] = []
    client, runtime, service = _client(tmp_path, counter)
    service.save_credential(SecretStr("TEST_CONNECTION_CANARY_API_KEY_DO_NOT_LEAK"))
    _exchange(client, runtime)

    response = client.post(TEST_CONNECTION_PATH)

    assert response.status_code == 200
    assert response.json()["connection_status"] == "AVAILABLE"
    assert counter == [1]
    rows = _events(output)
    assert [row["stage"] for row in rows] == [
        "HTTP_TEST_CONNECTION_REQUEST_RECEIVED",
        "TEST_CONNECTION_ROUTE_ENTERED",
        "TEST_CONNECTION_SERVICE_ENTERED",
        "TEST_CONNECTION_PROVIDER_CALL_BEGIN",
        "TEST_CONNECTION_PROVIDER_CALL_SUCCESS",
        "TEST_CONNECTION_RECEIPT_PERSIST_BEGIN",
        "TEST_CONNECTION_RECEIPT_PERSIST_SUCCESS",
        "TEST_CONNECTION_ROUTE_SUCCESS",
        "HTTP_TEST_CONNECTION_RESPONSE",
    ]
    assert rows[-1]["safe_http_status"] == 200
    assert len({row["request_id"] for row in rows}) == 1
    assert {key for row in rows for key in row} <= diagnostic.ALLOWED_EVENT_FIELDS
    content = output.read_text(encoding="utf-8")
    assert "TEST_CONNECTION_CANARY_API_KEY_DO_NOT_LEAK" not in content
    assert all(marker not in content for marker in ("request_body", "headers", "Cookie", "Authorization"))


def test_test_connection_provider_failure_still_records_receipt_without_extra_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.delenv("EMBRIX_CREDENTIAL_SAVE_DIAGNOSTIC", raising=False)
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)
    counter: list[int] = []
    client, runtime, service = _client(tmp_path, counter)
    service._http_transport = _transport(counter, status=401)  # type: ignore[attr-defined]
    service.save_credential(SecretStr("TEST_CONNECTION_CANARY_API_KEY_DO_NOT_LEAK"))
    _exchange(client, runtime)

    response = client.post(TEST_CONNECTION_PATH)

    assert response.status_code == 200
    assert response.json()["connection_status"] == "PROVIDER_AUTH_FAILED"
    assert response.json()["connection_diagnostic"] == {
        "stage": "PROVIDER_RESPONSE",
        "http_method": "POST",
        "request_url": f"{DEEPSEEK_BASE_URL}/chat/completions",
            "http_status": 401,
            "provider_error_code": "AUTHENTICATION_FAILED",
            "error_code": "HTTP_401",
            "normalized_error": "PROVIDER_AUTH_FAILED",
        "transport_error_code": None,
        "protocol_family": "OPENAI_CHAT",
    }
    assert counter == [1]
    stages = [row["stage"] for row in _events(output)]
    assert "TEST_CONNECTION_PROVIDER_CALL_FAILURE" in stages
    failure = next(
        row for row in _events(output)
        if row["stage"] == "TEST_CONNECTION_PROVIDER_CALL_FAILURE"
    )
    assert failure["safe_error_class"] == "PROVIDER_AUTH_FAILED"
    assert failure["safe_http_status"] == 401
    assert "TEST_CONNECTION_RECEIPT_PERSIST_BEGIN" in stages
    assert "TEST_CONNECTION_RECEIPT_PERSIST_SUCCESS" in stages


def test_test_connection_receipt_sink_failure_is_observable_and_preserves_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "events.jsonl"
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: output)
    counter: list[int] = []
    _, _, service = _client(tmp_path, counter, middleware=False)
    service.save_credential(SecretStr("TEST_CONNECTION_CANARY_API_KEY_DO_NOT_LEAK"))

    def fail_record(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise OSError("TEST_CONNECTION_CANARY_RAW_EXCEPTION")

    monkeypatch.setattr(ProviderConfigurationStore, "record_test", fail_record)
    with diagnostic.request_scope("receipt-failure"), pytest.raises(OSError):
        service.test_connection()

    stages = [row["stage"] for row in _events(output)]
    assert stages == [
        "TEST_CONNECTION_SERVICE_ENTERED",
        "TEST_CONNECTION_PROVIDER_CALL_BEGIN",
        "TEST_CONNECTION_PROVIDER_CALL_SUCCESS",
        "TEST_CONNECTION_RECEIPT_PERSIST_BEGIN",
        "TEST_CONNECTION_RECEIPT_PERSIST_FAILURE",
    ]
    assert "TEST_CONNECTION_CANARY_RAW_EXCEPTION" not in output.read_text(encoding="utf-8")


def test_test_connection_diagnostic_sink_failure_is_non_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingPath:
        parent = None

        def __init__(self) -> None:
            self.parent = self

        def mkdir(self, **kwargs: object) -> None:
            del kwargs
            raise OSError("TEST_CONNECTION_CANARY_SINK_FAILURE")

    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setattr(diagnostic, "diagnostic_output_path", lambda: FailingPath())

    with diagnostic.request_scope("sink-failure"):
        diagnostic.emit("TEST_CONNECTION_SERVICE_ENTERED", result="ENTERED", provider="deepseek")


def test_diagnostic_does_not_add_provider_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter: list[int] = []
    _, _, service = _client(tmp_path, counter, middleware=False)
    service.save_credential(SecretStr("TEST_CONNECTION_CANARY_API_KEY_DO_NOT_LEAK"))

    monkeypatch.delenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, raising=False)
    with diagnostic.request_scope("disabled"):
        service.test_connection()
    disabled_count = len(counter)

    second_counter: list[int] = []
    _, _, enabled_service = _client(tmp_path / "enabled", second_counter, middleware=False)
    enabled_service.save_credential(SecretStr("TEST_CONNECTION_CANARY_API_KEY_DO_NOT_LEAK"))
    monkeypatch.setenv(diagnostic.DIAGNOSTIC_ENVIRONMENT_VARIABLE, "1")
    with diagnostic.request_scope("enabled"):
        enabled_service.test_connection()

    assert disabled_count == 1
    assert len(second_counter) == 1
