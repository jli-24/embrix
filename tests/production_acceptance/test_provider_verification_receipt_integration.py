from __future__ import annotations

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
    ModelGatewayManagementService,
    SQLiteModelGatewayStore,
    provider_presets,
)
from embedded_copilot.production_release.credentials import (
    InMemoryCredentialProvider,
    provider_credential_purpose,
)
from embedded_copilot.production_release.provider import (
    ProviderConfigurationService,
    ProviderConnectionStatus,
)
from embedded_copilot.production_release.provider_verification import (
    GoldenEvaluationStatus,
    ProviderVerificationReceipt,
    ProviderVerificationReceiptStore,
    evaluate_provider_golden,
)
from embedded_copilot.production_release.runtime import ProductionReleaseRuntime


def _service(
    tmp_path: Path,
    response: httpx.Response | Exception,
    *,
    with_credential: bool = True,
    verification_store: ProviderVerificationReceiptStore | None = None,
) -> tuple[ProviderConfigurationService, InMemoryCredentialProvider]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    credentials = InMemoryCredentialProvider()

    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(response, Exception):
            raise response
        return response

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
        verification_store=verification_store
        or ProviderVerificationReceiptStore(tmp_path / "provider-verification.sqlite3"),
    )
    service.save_configuration(provider="zhipu", model="glm-4.7-flash")
    if with_credential:
        service.save_credential(
            SecretStr("test-only-provider-secret"), provider="zhipu"
        )
    return service, credentials


def _success() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-receipt-test",
            "model": "glm-4.7-flash",
            "choices": [
                {"message": {"content": "OK"}, "finish_reason": "stop"}
            ],
            "usage": {
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "total_tokens": 3,
            },
        },
    )


@pytest.mark.parametrize(
    ("status", "expected_status", "expected_available", "expected_http"),
    [
        (200, "PASS", True, 200),
        (429, "PARTIAL", True, 429),
        (404, "FAIL", False, 404),
        (401, "FAIL", "UNKNOWN", 401),
    ],
)
def test_real_probe_outcomes_append_sanitized_receipt_and_sync_projection(
    tmp_path: Path,
    status: int,
    expected_status: str,
    expected_available: bool | str,
    expected_http: int,
) -> None:
    service, credentials = _service(
        tmp_path,
        _success()
        if status == 200
        else httpx.Response(status, json={"error": "provider detail must not persist"}),
    )

    projection = service.test_connection()
    receipt = service.verification_store.latest_for_provider_model(
        "zhipu", "glm-4.7-flash"
    )

    assert receipt is not None
    assert receipt.verification_status.value == expected_status
    assert receipt.model_available == expected_available
    assert receipt.http_status == expected_http
    expected_connection = {
        200: ProviderConnectionStatus.AVAILABLE.value,
        429: ProviderConnectionStatus.RATE_LIMITED.value,
        404: ProviderConnectionStatus.MODEL_UNAVAILABLE.value,
        401: ProviderConnectionStatus.AUTH_FAILED.value,
    }[status]
    assert receipt.connection_status == expected_connection
    assert receipt.verification_id == projection.verification_id
    assert projection.verification_persisted is True
    assert projection.verification_status == expected_status
    assert projection.http_status == expected_http
    assert credentials.get(provider_credential_purpose("zhipu")) == b"test-only-provider-secret"
    assert "test-only-provider-secret" not in receipt.model_dump_json()
    assert "provider detail" not in receipt.model_dump_json()

    if status == 200:
        target = service._verification_target(
            endpoint=service._management.endpoint_profile(projection.endpoint_id),  # type: ignore[union-attr]
            requested_model="glm-4.7-flash",
        )
        evaluation = evaluate_provider_golden(receipt, target)
        assert evaluation.status is GoldenEvaluationStatus.PASS
        assert evaluation.is_golden is True
    else:
        assert projection.connection != "verified"


def test_network_failure_and_missing_credential_are_blocked_or_failed_with_tri_state(
    tmp_path: Path,
) -> None:
    request = httpx.Request("POST", "https://open.bigmodel.cn/api/paas/v4/chat/completions")
    service, _ = _service(
        tmp_path / "network",
        httpx.ConnectError("network unavailable", request=request),
    )
    network_projection = service.test_connection()
    network_receipt = service.verification_store.latest_for_provider("zhipu")
    assert network_receipt is not None
    assert network_receipt.model_available == "UNKNOWN"
    assert network_receipt.verification_status.value == "FAIL"
    assert network_receipt.http_status == "NOT_OBTAINED"
    assert network_projection.verification_persisted is True

    missing, _ = _service(
        tmp_path / "missing",
        _success(),
        with_credential=False,
    )
    missing_projection = missing.test_connection()
    missing_receipt = missing.verification_store.latest_for_provider("zhipu")
    assert missing_receipt is not None
    assert missing_receipt.model_available == "UNKNOWN"
    assert missing_receipt.verification_status.value == "BLOCKED"
    assert missing_receipt.http_status == "NOT_OBTAINED"
    assert missing_receipt.message_count == 0
    assert missing_receipt.request_shape == "not_sent"
    assert missing_projection.verification_persisted is True


def test_success_with_receipt_persistence_failure_is_partial_and_not_golden(
    tmp_path: Path,
) -> None:
    class FailingReceiptStore(ProviderVerificationReceiptStore):
        def append(self, receipt: ProviderVerificationReceipt) -> ProviderVerificationReceipt:
            raise OSError("receipt store unavailable")

    service, _ = _service(
        tmp_path,
        _success(),
        verification_store=FailingReceiptStore(tmp_path / "failing-receipts.sqlite3"),
    )

    projection = service.test_connection()

    assert projection.connection_status.value == "AVAILABLE"
    assert projection.verification_persisted is False
    assert projection.verification_status == "PARTIAL"
    assert projection.verification_persistence_error == "RECEIPT_PERSISTENCE_FAILED"
    assert projection.verification_id is not None
    assert service.verification_store.list_recent() == ()


def test_receipt_and_projection_survive_restart_without_migration_from_catalog(
    tmp_path: Path,
) -> None:
    service, credentials = _service(tmp_path, _success())
    first = service.test_connection()
    first_receipt = service.verification_store.get(first.verification_id or "missing")
    assert first_receipt is not None

    management = ModelGatewayManagementService(
        store=SQLiteModelGatewayStore(tmp_path / "model-gateway.sqlite3")
    )
    assert management.restore_from_store() is True
    restarted = ProviderConfigurationService(
        configuration_path=tmp_path / "production.json",
        credentials=credentials,
        http_transport=httpx.MockTransport(lambda request: _success()),
        model_gateway_management=management,
        verification_store=ProviderVerificationReceiptStore(
            tmp_path / "provider-verification.sqlite3"
        ),
    )
    latest = restarted.latest_verification()
    assert latest == first_receipt
    assert restarted.projection().verification_id == first.verification_id

    raw_config = (tmp_path / "production.json").read_text(encoding="utf-8")
    assert "test-only-provider-secret" not in raw_config
    raw_receipts = (tmp_path / "provider-verification.sqlite3").read_bytes()
    assert b"test-only-provider-secret" not in raw_receipts
    assert b"provider detail" not in raw_receipts


def test_read_only_receipt_api_returns_latest_and_recent_without_secret_material(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path, _success())
    projection = service.test_connection()
    runtime = ProductionReleaseRuntime.create(
        Settings(
            environment="production",
            production_state_root=tmp_path / "runtime-state",
            _env_file=None,
        ),
        credentials=InMemoryCredentialProvider(),
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
        nonce = runtime.sessions.issue_bootstrap(
            requester="receipt-test", tenant_id="tenant"
        )
        assert client.post(
            "/api/v1/copilot/desktop/system/bootstrap/exchange",
            json={"nonce": nonce},
        ).status_code == 200
        latest = client.get(
            "/api/v1/copilot/desktop/system/provider-verifications/latest"
        )
        recent = client.get(
            "/api/v1/copilot/desktop/system/provider-verifications?limit=10"
        )
        invalid = client.get(
            "/api/v1/copilot/desktop/system/provider-verifications?limit=0"
        )

    assert latest.status_code == 200
    assert latest.json()["verification_id"] == projection.verification_id
    assert latest.json()["verification_status"] == "PASS"
    assert latest.json()["provider_request_id"] == "chatcmpl-receipt-test"
    assert recent.status_code == 200
    assert recent.json()[0]["verification_id"] == projection.verification_id
    assert invalid.status_code == 422
    assert "test-only-provider-secret" not in latest.text
    assert "provider detail" not in latest.text
