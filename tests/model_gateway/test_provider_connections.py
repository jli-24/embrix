from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from embedded_copilot.model_gateway import (
    EndpointProfile,
    EndpointRegistry,
    EndpointType,
    ModelGatewayManagementService,
    ProtocolFamily,
    ProviderConnection,
    ProviderConnectionRegistry,
    ProviderConnectionStatus,
    ProviderProfile,
    SQLiteModelGatewayStore,
)


def _provider() -> ProviderProfile:
    return ProviderProfile(
        provider_id="deepseek",
        display_name="DeepSeek",
        supported_protocols=(ProtocolFamily.OPENAI_CHAT,),
    )


def _endpoint(endpoint_id: str, *, provider_id: str = "deepseek") -> EndpointProfile:
    return EndpointProfile(
        endpoint_id=endpoint_id,
        provider_id=provider_id,
        display_name=endpoint_id,
        endpoint_type=EndpointType.OFFICIAL,
        protocol_family=ProtocolFamily.OPENAI_CHAT,
        base_url=f"https://{endpoint_id}.example.test/v1",
        credential_ref=f"credential:{provider_id}",
    )


def test_connection_identity_lifecycle_and_endpoint_ownership() -> None:
    providers = (_provider(),)
    endpoints = EndpointRegistry((_endpoint("endpoint-a"), _endpoint("endpoint-b")))
    registry = ProviderConnectionRegistry(
        providers=providers,
        endpoints=endpoints,
        id_factory=lambda prefix: f"{prefix}-generated",
    )

    first = registry.create(
        provider_id="deepseek",
        connection_name="Personal API",
        endpoint_id="endpoint-a",
        credential_ref="credential:deepseek",
    )
    renamed = registry.update(first.connection_id, connection_name="Company API")
    second = registry.create(
        provider_id="deepseek",
        connection_name="Test API",
        endpoint_id="endpoint-b",
        credential_ref="credential:deepseek",
    )

    assert first.connection_id == "conn-generated"
    assert renamed.connection_id == first.connection_id
    assert renamed.connection_name == "Company API"
    assert renamed.fingerprint == first.fingerprint
    assert second.connection_id != first.connection_id
    assert {item.provider_id for item in registry.list()} == {"deepseek"}

    disabled = registry.disable(first.connection_id)
    assert disabled.status is ProviderConnectionStatus.DISABLED
    assert registry.update(first.connection_id, status=ProviderConnectionStatus.ACTIVE).status is ProviderConnectionStatus.ACTIVE
    tombstone = registry.tombstone(first.connection_id)
    assert tombstone.status is ProviderConnectionStatus.DELETED
    assert tombstone.deleted_at is not None
    with pytest.raises(ValueError, match="endpoint ownership"):
        registry.create(
            provider_id="deepseek",
            connection_name="Duplicate",
            endpoint_id="endpoint-a",
            credential_ref="credential:deepseek",
        )


def test_connection_contract_rejects_invalid_status_and_cross_provider_endpoint() -> None:
    with pytest.raises(ValidationError):
        ProviderConnection(
            connection_id="conn-invalid",
            provider_id="deepseek",
            connection_name="Invalid",
            status="NOT_A_STATUS",
            endpoint_id="endpoint-a",
            credential_ref="credential:deepseek",
        )

    registry = ProviderConnectionRegistry(
        providers=(_provider(), ProviderProfile(provider_id="local", display_name="Local")),
        endpoints=(_endpoint("endpoint-a"), _endpoint("endpoint-local", provider_id="local")),
    )
    with pytest.raises(ValueError, match="provider"):
        registry.create(
            provider_id="deepseek",
            connection_name="Invalid binding",
            endpoint_id="endpoint-local",
            credential_ref="credential:deepseek",
        )


def test_duplicate_connection_id_and_endpoint_url_change_keep_connection_identity() -> None:
    endpoint = _endpoint("endpoint-a")
    endpoints = EndpointRegistry((endpoint, _endpoint("endpoint-b")))
    registry = ProviderConnectionRegistry(providers=(_provider(),), endpoints=endpoints)
    first = registry.create(
        provider_id="deepseek",
        endpoint_id="endpoint-a",
        connection_name="Stable",
        credential_ref="credential:deepseek",
        connection_id="conn-stable",
    )
    with pytest.raises(ValueError, match="duplicate provider connection"):
        registry.create(
            provider_id="deepseek",
            endpoint_id="endpoint-b",
            connection_name="Duplicate ID",
            credential_ref="credential:deepseek",
            connection_id="conn-stable",
        )
    updated_endpoint = endpoint.model_copy(
        update={
            "base_url": "https://endpoint-a-new.example.test/v1",
            "updated_at": datetime(2026, 9, 15, 0, 1, tzinfo=UTC),
        }
    )
    endpoints.replace(updated_endpoint)
    assert registry.get(first.connection_id).connection_id == "conn-stable"


def test_connections_persist_restore_tombstone_quarantine_without_secret(tmp_path: Path) -> None:
    path = tmp_path / "model-gateway.sqlite3"
    service = ModelGatewayManagementService(store=SQLiteModelGatewayStore(path))
    service.add_provider(_provider())
    service.add_endpoint(_endpoint("endpoint-a"))
    connection = service.create_connection(
        provider_id="deepseek",
        connection_name="Persisted",
        endpoint_id="endpoint-a",
        credential_ref="credential:deepseek",
    )
    service.tombstone_connection(connection.connection_id)
    service.add_endpoint(_endpoint("endpoint-b"))
    quarantined = ProviderConnection(
        connection_id="conn-quarantined",
        provider_id="deepseek",
        connection_name="Needs review",
        status=ProviderConnectionStatus.QUARANTINED,
        endpoint_id="endpoint-b",
        credential_ref="credential:deepseek",
        created_at=datetime(2026, 9, 15, tzinfo=UTC),
        updated_at=datetime(2026, 9, 15, tzinfo=UTC),
    )
    service.add_connection(quarantined)
    service.save()

    with sqlite3.connect(path) as connection_db:
        raw = connection_db.execute("SELECT payload FROM model_gateway_state WHERE id = 1").fetchone()[0]
    assert "actual-secret-value" not in raw
    recovered = ModelGatewayManagementService(store=SQLiteModelGatewayStore(path))
    assert recovered.restore_from_store() is True
    restored = {item.connection_id: item for item in recovered.list_connections()}
    assert restored[connection.connection_id].status is ProviderConnectionStatus.DELETED
    assert restored["conn-quarantined"].status is ProviderConnectionStatus.QUARANTINED
    assert restored[connection.connection_id].credential_ref == "credential:deepseek"


def test_legacy_unique_connection_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    service = ModelGatewayManagementService(store=SQLiteModelGatewayStore(path))
    service.add_provider(_provider())
    service.add_endpoint(_endpoint("endpoint-a"))
    service.save()

    with sqlite3.connect(path) as connection:
        payload = json.loads(connection.execute("SELECT payload FROM model_gateway_state WHERE id = 1").fetchone()[0])
        payload.pop("connections", None)
        payload.pop("migration_version", None)
        payload.pop("migration_source_fingerprint", None)
        payload.update(
            {
                "chat_provider": "deepseek",
                "chat_connection_name": "Personal API",
                "chat_endpoint_id": "endpoint-a",
                "chat_credential_ref": "credential:deepseek",
            }
        )
        encoded = json.dumps(
            {key: value for key, value in payload.items() if key != "fingerprint"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        fingerprint = "sha256:" + hashlib.sha256(encoded).hexdigest()
        payload["fingerprint"] = fingerprint
        connection.execute(
            "UPDATE model_gateway_state SET fingerprint = ?, payload = ? WHERE id = 1",
            (fingerprint, json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        )

    first = SQLiteModelGatewayStore(path).load()
    second = SQLiteModelGatewayStore(path).load()
    assert first is not None and second is not None
    assert len(first.connections) == len(second.connections) == 1
    assert first.connections[0].connection_id == second.connections[0].connection_id
    assert first.connections[0].connection_name == "Personal API"
    assert first.migration_version >= 1
    assert first.migration_source_fingerprint is not None

    recovered = ModelGatewayManagementService(store=SQLiteModelGatewayStore(path))
    recovered.restore(first)
    recovered.save()
    roundtripped = SQLiteModelGatewayStore(path).load()
    assert roundtripped is not None
    assert roundtripped.migration_version == first.migration_version
    assert roundtripped.migration_source_fingerprint == first.migration_source_fingerprint


def test_legacy_ambiguous_connection_is_quarantined_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "ambiguous.sqlite3"
    service = ModelGatewayManagementService(store=SQLiteModelGatewayStore(path))
    service.add_provider(_provider())
    service.add_endpoint(_endpoint("endpoint-a"))
    service.add_endpoint(_endpoint("endpoint-b"))
    service.save()

    with sqlite3.connect(path) as connection:
        payload = json.loads(connection.execute("SELECT payload FROM model_gateway_state WHERE id = 1").fetchone()[0])
        payload.pop("connections", None)
        payload.update({"chat_provider": "deepseek", "chat_connection_name": "Ambiguous"})
        encoded = json.dumps(
            {key: value for key, value in payload.items() if key != "fingerprint"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        fingerprint = "sha256:" + hashlib.sha256(encoded).hexdigest()
        payload["fingerprint"] = fingerprint
        connection.execute(
            "UPDATE model_gateway_state SET fingerprint = ?, payload = ? WHERE id = 1",
            (fingerprint, json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        )

    restored = SQLiteModelGatewayStore(path).load()
    assert restored is not None
    assert restored.connections == ()
    assert any(issue.reason == "RECORD_MIGRATION_AMBIGUOUS" for issue in restored.integrity_issues)


def test_legacy_custom_relay_is_retained_and_migrated_without_changing_provider_id(
    tmp_path: Path,
) -> None:
    path = tmp_path / "custom-relay.sqlite3"
    provider_id = "custom-my-relay-abc123"
    service = ModelGatewayManagementService(store=SQLiteModelGatewayStore(path))
    service.add_provider(
        ProviderProfile(
            provider_id=provider_id,
            display_name="My Relay",
        )
    )
    service.add_endpoint(
        EndpointProfile(
            endpoint_id="endpoint-custom-relay",
            provider_id=provider_id,
            display_name="Relay endpoint",
            endpoint_type=EndpointType.RELAY,
            protocol_family=ProtocolFamily.OPENAI_CHAT,
            base_url="https://relay.example.test/v1",
            credential_ref="credential:custom-relay",
        )
    )
    service.save()

    with sqlite3.connect(path) as connection:
        payload = json.loads(connection.execute("SELECT payload FROM model_gateway_state WHERE id = 1").fetchone()[0])
        payload.pop("connections", None)
        payload["chat_provider_profiles"] = {
            provider_id: {
                "provider": provider_id,
                "connection_name": "My Relay",
                "endpoint_id": "endpoint-custom-relay",
                "credential_ref": "credential:custom-relay",
            }
        }
        encoded = json.dumps(
            {key: value for key, value in payload.items() if key != "fingerprint"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        fingerprint = "sha256:" + hashlib.sha256(encoded).hexdigest()
        payload["fingerprint"] = fingerprint
        connection.execute(
            "UPDATE model_gateway_state SET fingerprint = ?, payload = ? WHERE id = 1",
            (fingerprint, json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        )

    restored = SQLiteModelGatewayStore(path).load()
    assert restored is not None
    assert restored.providers[0].provider_id == provider_id
    assert restored.endpoints[0].provider_id == provider_id
    assert restored.connections[0].provider_id == provider_id


def test_owned_endpoint_rejects_provider_or_credential_drift() -> None:
    service = ModelGatewayManagementService()
    service.add_provider(_provider())
    service.add_endpoint(_endpoint("endpoint-a"))
    service.create_connection(
        provider_id="deepseek",
        endpoint_id="endpoint-a",
        credential_ref="credential:deepseek",
    )

    with pytest.raises(ValueError, match="credential ownership"):
        service.upsert_endpoint(
            _endpoint("endpoint-a").model_copy(update={"credential_ref": "credential:other"})
        )
