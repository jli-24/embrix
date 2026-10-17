from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from embedded_copilot.model_gateway.management import ModelGatewayManagementService
from embedded_copilot.model_gateway.persistence import (
    IdentityIntegrityIssue,
    SQLiteModelGatewayStore,
)
from embedded_copilot.model_gateway.presets import manual_model_profile
from embedded_copilot.model_gateway.profiles import (
    EndpointProfile,
    EndpointType,
    ModelProfile,
    ProtocolFamily,
    ProviderProfile,
)
from embedded_copilot.model_gateway_storage import _load_state_with_quarantine


def _seed_store(path: Path) -> None:
    service = ModelGatewayManagementService(store=SQLiteModelGatewayStore(path))
    service.add_provider(
        ProviderProfile(
            provider_id="zhipu",
            display_name="智谱",
            supported_protocols=(ProtocolFamily.OPENAI_CHAT,),
        )
    )
    service.add_endpoint(
        EndpointProfile(
            endpoint_id="endpoint-zhipu",
            provider_id="zhipu",
            display_name="智谱 Endpoint",
            endpoint_type=EndpointType.OFFICIAL,
            protocol_family=ProtocolFamily.OPENAI_CHAT,
            base_url="https://example.invalid/v4",
            credential_ref="credential:zhipu",
        )
    )
    service.add_model(
        manual_model_profile(
            model_profile_id="model-zhipu-glm-test",
            provider_id="zhipu",
            endpoint_id="endpoint-zhipu",
            remote_model_id="glm-test",
            display_name="GLM Test",
        )
    )
    service.save()


def _read_payload(path: Path) -> dict[str, object]:
    with sqlite3.connect(path) as connection:
        value = connection.execute(
            "SELECT payload FROM model_gateway_state WHERE id = 1"
        ).fetchone()
    assert value is not None
    payload = json.loads(value[0])
    assert isinstance(payload, dict)
    return payload


def _write_payload(path: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(
        {key: value for key, value in payload.items() if key != "fingerprint"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload["fingerprint"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE model_gateway_state SET fingerprint = ?, payload = ? WHERE id = 1",
            (
                payload["fingerprint"],
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            ),
        )


def _issue(
    *,
    provider_id: str,
    endpoint_id: str,
    credential_ref: str,
    model_profile_id: str,
    model_id: str,
    detected_at: datetime,
) -> IdentityIntegrityIssue:
    return IdentityIntegrityIssue(
        record_type="MODEL",
        record_id=model_profile_id,
        provider_id=provider_id,
        endpoint_id=endpoint_id,
        credential_ref=credential_ref,
        model_profile_id=model_profile_id,
        model_id=model_id,
        reason="RECORD_SCHEMA_INVALID",
        detected_at=detected_at,
    )


def test_same_bad_legacy_record_restored_twice_is_one_issue_and_restart_stable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "model-gateway.sqlite3"
    _seed_store(path)
    payload = _read_payload(path)
    models = payload["models"]
    assert isinstance(models, list)
    models[0]["remote_model_id"] = None
    generated, _ = _load_state_with_quarantine(payload)
    assert len(generated.integrity_issues) == 1
    payload["integrity_issues"] = [generated.integrity_issues[0].model_dump(mode="json")]
    _write_payload(path, payload)

    first = SQLiteModelGatewayStore(path).load()
    second = SQLiteModelGatewayStore(path).load()

    assert first is not None and second is not None
    assert len(first.integrity_issues) == 1
    assert len(second.integrity_issues) == 1

    recovered = ModelGatewayManagementService(store=SQLiteModelGatewayStore(path))
    assert recovered.restore_from_store() is True
    recovered.save()
    restarted = SQLiteModelGatewayStore(path).load()

    assert restarted is not None
    assert len(restarted.integrity_issues) == 1


def test_same_model_id_with_distinct_bad_identities_remains_two_issues(
    tmp_path: Path,
) -> None:
    path = tmp_path / "model-gateway.sqlite3"
    _seed_store(path)
    payload = _read_payload(path)
    first = _issue(
        provider_id="provider-a",
        endpoint_id="endpoint-a",
        credential_ref="credential:a",
        model_profile_id="profile-a",
        model_id="glm-x",
        detected_at=datetime(2026, 9, 14, 1, tzinfo=UTC),
    )
    duplicate = first.model_copy(update={"detected_at": datetime(2026, 9, 14, 2, tzinfo=UTC)})
    second = _issue(
        provider_id="provider-b",
        endpoint_id="endpoint-b",
        credential_ref="credential:b",
        model_profile_id="profile-b",
        model_id="glm-x",
        detected_at=datetime(2026, 9, 14, 3, tzinfo=UTC),
    )
    payload["integrity_issues"] = [
        first.model_dump(mode="json"),
        duplicate.model_dump(mode="json"),
        second.model_dump(mode="json"),
    ]
    _write_payload(path, payload)

    restored = SQLiteModelGatewayStore(path).load()

    assert restored is not None
    assert len(restored.integrity_issues) == 2
    assert {item.provider_id for item in restored.integrity_issues} == {"provider-a", "provider-b"}
    assert {item.model_id for item in restored.integrity_issues} == {"glm-x"}


def test_quarantine_is_not_a_current_model_or_allowed_pool_entry(tmp_path: Path) -> None:
    path = tmp_path / "model-gateway.sqlite3"
    _seed_store(path)
    payload = _read_payload(path)
    issue = _issue(
        provider_id="zhipu",
        endpoint_id="endpoint-zhipu",
        credential_ref="credential:zhipu",
        model_profile_id="model-zhipu-invalid",
        model_id="glm-invalid",
        detected_at=datetime(2026, 9, 14, tzinfo=UTC),
    )
    payload["integrity_issues"] = [issue.model_dump(mode="json")]
    _write_payload(path, payload)

    restored = SQLiteModelGatewayStore(path).load()
    assert restored is not None
    service = ModelGatewayManagementService()
    service.restore(restored)

    assert all(item.model_profile_id != issue.model_profile_id for item in service.list_models())
    assert service.list_allowed_model_policies() == ()
