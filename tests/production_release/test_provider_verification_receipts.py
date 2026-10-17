from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from embedded_copilot.production_release.provider_verification import (
    ConnectionTestRecord,
    GoldenEvaluationStatus,
    ModelRuntimeHealth,
    ProviderVerificationReceipt,
    ProviderVerificationReceiptStore,
    ProviderVerificationTarget,
    ReceiptConflict,
    ResponseParseStatus,
    VerificationStatus,
    evaluate_provider_golden,
)


def _target(*, credential_revision: str = "hmac-sha256:" + "a" * 64) -> ProviderVerificationTarget:
    return ProviderVerificationTarget(
        provider_id="zhipu",
        endpoint_id="endpoint-zhipu",
        model_id="glm-4.7-flash",
        credential_revision=credential_revision,
        endpoint_revision="sha256:" + "b" * 64,
        model_revision="sha256:" + "c" * 64,
    )


def _receipt(
    *,
    connection_id: str | None = None,
    status: VerificationStatus = VerificationStatus.PASS,
    http_status: int | str = 200,
    verified_model: str | None = "glm-4.7-flash",
    credential_revision: str = "hmac-sha256:" + "a" * 64,
    response_parse_status: ResponseParseStatus = ResponseParseStatus.PASS,
) -> ProviderVerificationReceipt:
    return ProviderVerificationReceipt.create(
        provider_id="zhipu",
        connection_id=connection_id,
        endpoint_id="endpoint-zhipu",
        model_id="glm-4.7-flash",
        credential_ref="credential:zhipu",
        credential_revision=credential_revision,
        endpoint_revision="sha256:" + "b" * 64,
        model_revision="sha256:" + "c" * 64,
        protocol_family="OPENAI_CHAT",
        requested_at=datetime(2026, 9, 11, 12, 0, tzinfo=UTC),
        completed_at=datetime(2026, 9, 11, 12, 0, 1, tzinfo=UTC),
        latency_ms=1.0,
        request_url_safe="https://open.bigmodel.cn/api/paas/v4/chat/completions",
        http_status=http_status,
        provider_request_id="req-123",
        requested_model="glm-4.7-flash",
        verified_model=verified_model,
        response_parse_status=response_parse_status,
        model_available=True,
        connection_status="AVAILABLE" if status is VerificationStatus.PASS else "PROVIDER_RATE_LIMITED",
        verification_status=status,
        input_tokens=2,
        output_tokens=3,
        total_tokens=5,
        usage_source="PROVIDER_REPORTED",
        cost="UNKNOWN",
        currency="UNKNOWN",
        cost_source="UNKNOWN",
        cache_status="UNKNOWN",
    )


def test_receipt_store_is_append_only_and_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "provider-verification.sqlite3"
    receipt = _receipt()
    store = ProviderVerificationReceiptStore(path)

    assert store.append(receipt) == receipt
    with pytest.raises(ReceiptConflict):
        store.append(receipt)

    restarted = ProviderVerificationReceiptStore(path)
    assert restarted.get(receipt.verification_id) == receipt
    assert restarted.latest_for_provider("zhipu") == receipt
    assert restarted.latest_for_provider_model("zhipu", "glm-4.7-flash") == receipt
    assert restarted.find_current_for_configuration(_target()) == receipt


def test_receipt_serialization_contains_no_secret_or_request_body(tmp_path: Path) -> None:
    path = tmp_path / "provider-verification.sqlite3"
    receipt = _receipt()
    ProviderVerificationReceiptStore(path).append(receipt)

    with path.open("rb") as handle:
        database_bytes = handle.read()
    assert b"Bearer" not in database_bytes
    assert b"api_key" not in database_bytes
    assert b"messages" not in database_bytes
    assert b"glm-4.7-flash" in database_bytes


def test_golden_evaluator_requires_2xx_parse_model_and_revision_match() -> None:
    result = evaluate_provider_golden(_receipt(), _target())
    assert result.status is GoldenEvaluationStatus.PASS
    assert result.is_golden is True

    limited = _receipt(
        status=VerificationStatus.PARTIAL,
        http_status=429,
        verified_model=None,
        response_parse_status=ResponseParseStatus.NOT_APPLICABLE,
    )
    assert evaluate_provider_golden(limited, _target()).reason_code == "HTTP_NOT_2XX"

    stale = _receipt(credential_revision="hmac-sha256:" + "d" * 64)
    stale_result = evaluate_provider_golden(stale, _target())
    assert stale_result.status is GoldenEvaluationStatus.STALE
    assert stale_result.reason_code == "REVISION_MISMATCH"

    not_persisted = evaluate_provider_golden(_receipt(), _target(), verification_persisted=False)
    assert not_persisted.status is GoldenEvaluationStatus.BLOCKED
    assert not_persisted.reason_code == "RECEIPT_NOT_PERSISTED"


def test_receipt_store_orders_latest_by_completed_at(tmp_path: Path) -> None:
    path = tmp_path / "provider-verification.sqlite3"
    first = _receipt()
    second = ProviderVerificationReceipt.model_validate(
        {
            **first.model_dump(mode="python"),
            "verification_id": "verification-second",
            "completed_at": first.completed_at + timedelta(seconds=1),
            "fingerprint": "sha256:" + "0" * 64,
        }
    )
    store = ProviderVerificationReceiptStore(path)
    store.append(first)
    store.append(second)

    assert store.latest_for_provider("zhipu") == second
    assert tuple(item.verification_id for item in store.list_recent(2)) == (
        "verification-second",
        first.verification_id,
    )


def test_connection_test_history_is_projected_by_connection_and_secret_free(tmp_path: Path) -> None:
    path = tmp_path / "provider-verification.sqlite3"
    first = _receipt(connection_id="connection-a")
    second = ProviderVerificationReceipt.model_validate(
        {
            **first.model_dump(mode="python"),
            "verification_id": "verification-history-second",
            "connection_id": "connection-a",
            "completed_at": first.completed_at + timedelta(seconds=1),
            "fingerprint": "sha256:" + "0" * 64,
        }
    )
    other = ProviderVerificationReceipt.model_validate(
        {
            **first.model_dump(mode="python"),
            "verification_id": "verification-history-other",
            "connection_id": "connection-b",
            "fingerprint": "sha256:" + "0" * 64,
        }
    )
    store = ProviderVerificationReceiptStore(path)
    store.append(first)
    store.append(second)
    store.append(other)

    history = store.list_test_history("connection-a", limit=2)
    assert tuple(item.test_id for item in history) == (
        "verification-history-second",
        first.verification_id,
    )
    assert all(isinstance(item, ConnectionTestRecord) for item in history)
    assert history[0].connection_id == "connection-a"
    assert history[0].credential_revision == first.credential_revision
    assert history[0].status == first.connection_status
    assert history[0].error_code is None

    database_bytes = path.read_bytes()
    assert b"headers" not in database_bytes
    assert b"response_body" not in database_bytes
    assert b"Bearer" not in database_bytes


def test_connection_test_history_preserves_failure_status_and_safe_error_code(
    tmp_path: Path,
) -> None:
    path = tmp_path / "provider-verification.sqlite3"
    failure = ProviderVerificationReceipt.model_validate(
        {
            **_receipt(connection_id="connection-failure").model_dump(mode="python"),
            "verification_id": "verification-history-failure",
            "connection_status": "RATE_LIMITED",
            "provider_error_code": "PROVIDER_RATE_LIMITED",
            "normalized_error_code": "PROVIDER_RATE_LIMITED",
            "verification_status": VerificationStatus.PARTIAL,
            "fingerprint": "sha256:" + "0" * 64,
        }
    )
    store = ProviderVerificationReceiptStore(path)
    store.append(failure)

    history = store.list_test_history("connection-failure")
    assert len(history) == 1
    assert history[0].status == "RATE_LIMITED"
    assert history[0].error_code == "PROVIDER_RATE_LIMITED"


def test_model_runtime_health_keeps_unknown_values_and_identity_separate() -> None:
    identity = {
        "provider_id": "zhipu",
        "endpoint_id": "endpoint-zhipu",
        "credential_ref": "credential:zhipu",
        "model_profile_id": "profile-zhipu",
        "model_id": "glm-4.7-flash",
    }
    unknown = ModelRuntimeHealth.from_receipts(identity=identity, receipts=())
    assert unknown.identity.model_dump() == identity
    assert unknown.endpoint_status == "UNKNOWN"
    assert unknown.error_rate is None
    assert unknown.latency_ms is None
    assert unknown.quota_status == "UNKNOWN"
    assert unknown.sample_count == 0

    receipt = _receipt(connection_id="connection-a")
    observed = ModelRuntimeHealth.from_receipts(identity=identity, receipts=(receipt,))
    assert observed.identity.model_dump() == identity
    assert observed.endpoint_status == "AVAILABLE"
    assert observed.error_rate == 0.0
    assert observed.latency_ms == receipt.latency_ms
    assert observed.sample_count == 1
