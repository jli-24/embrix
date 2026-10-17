from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from embedded_copilot.model_gateway import (
    EndpointProfile,
    EndpointType,
    ModelGatewayManagementService,
    ProtocolFamily,
    ProviderKind,
    ProviderProfile,
    ProviderConnectionStatus,
)
from embedded_copilot.model_gateway.persistence import SQLiteModelGatewayStore
from embedded_copilot.production_release.credentials import (
    InMemoryCredentialProvider,
    provider_connection_credential_purpose,
)
from embedded_copilot.production_release.provider import ProviderConfigurationService


def _service(
    tmp_path: Path,
    *,
    model: object = None,
    model_profile_id: object = None,
    chat_model_present: bool = True,
    management: ModelGatewayManagementService | None = None,
    credentials: InMemoryCredentialProvider | None = None,
) -> tuple[ProviderConfigurationService, ModelGatewayManagementService, InMemoryCredentialProvider]:
    management = management or ModelGatewayManagementService(
        store=SQLiteModelGatewayStore(tmp_path / "model-gateway.sqlite3")
    )
    if management.providers.get("custom-demo") is None:
        management.add_provider(
            ProviderProfile(
                provider_id="custom-demo",
                display_name="Demo",
                provider_kind=ProviderKind.CUSTOM,
                supported_protocols=(ProtocolFamily.OPENAI_CHAT,),
            )
        )
    for endpoint_id, credential_ref in (
        ("endpoint-demo-a", "credential:demo-a"),
        ("endpoint-demo-b", "credential:demo-b"),
    ):
        if management.endpoint_profile(endpoint_id) is None:
            management.add_endpoint(
                EndpointProfile(
                    endpoint_id=endpoint_id,
                    provider_id="custom-demo",
                    display_name=endpoint_id,
                    endpoint_type=EndpointType.RELAY,
                    protocol_family=ProtocolFamily.OPENAI_CHAT,
                    base_url=f"https://{endpoint_id}.example.test/v1",
                    credential_ref=credential_ref,
                )
            )
        if not any(item.endpoint_id == endpoint_id for item in management.list_connections()):
            management.create_connection(
                provider_id="custom-demo",
                endpoint_id=endpoint_id,
                connection_name=endpoint_id,
                credential_ref=credential_ref,
                connection_id=f"conn-{endpoint_id.rsplit('-', 1)[-1]}",
            )
    credentials = credentials or InMemoryCredentialProvider()
    for connection_id, value in (("conn-a", b"secret-a"), ("conn-b", b"secret-b")):
        if credentials.get(provider_connection_credential_purpose(connection_id)) is None:
            credentials.set(provider_connection_credential_purpose(connection_id), value)
    # Read-only revision checks during restore require the integrity key to
    # already exist, as it does after the normal credential save flow.
    credentials.get_or_create_provider_binding_key()
    payload: dict[str, object] = {
        "chat_provider": "custom-demo",
        "chat_base_url": "https://endpoint-demo-a.example.test/v1",
        "chat_endpoint_id": "endpoint-demo-a",
        "chat_credential_ref": "credential:demo-a",
        "chat_model_profile_id": model_profile_id,
    }
    if chat_model_present:
        payload["chat_model"] = model
    (tmp_path / "production.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    service = ProviderConfigurationService(
        configuration_path=tmp_path / "production.json",
        credentials=credentials,
        model_gateway_management=management,
    )
    return service, management, credentials


def test_bootstrap_is_model_free_and_ignores_legacy_corruption(tmp_path: Path) -> None:
    service, management, _ = _service(
        tmp_path,
        model="invalid-model-xyz",
        model_profile_id="missing-profile",
    )
    management.set_global_policy(
        management.global_policy().model_copy(
            update={"active_model_profile_id": "missing-global-model"}
        )
    )

    # Changing the legacy policy after bootstrap cannot affect the canonical
    # connection-scoped transport snapshot.
    service._refresh_canonical_binding()  # type: ignore[attr-defined]
    snapshot = service.binding.transport_binding
    assert snapshot is not None
    assert service.binding.transport_ready is True
    assert {entry.connection_id for entry in snapshot.entries} == {"conn-a", "conn-b"}


def test_absent_chat_model_and_empty_model_pool_do_not_block_transport(tmp_path: Path) -> None:
    service, management, _ = _service(
        tmp_path, model=None, model_profile_id=None, chat_model_present=False
    )
    management.restore(management.state().model_copy(update={"models": ()}))
    assert service.binding.transport_ready is True
    assert management.list_models() == ()
    assert management.list_allowed_model_policies() == ()


def test_same_provider_connections_restore_to_distinct_exact_entries(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)
    snapshot = service.binding.transport_binding
    assert snapshot is not None
    assert snapshot.keys == (
        ("custom-demo", "endpoint-demo-a", "credential:demo-a"),
        ("custom-demo", "endpoint-demo-b", "credential:demo-b"),
    )
    assert {entry.connection_id for entry in snapshot.entries} == {"conn-a", "conn-b"}


def test_invalid_connection_is_excluded_without_polluting_sibling(tmp_path: Path) -> None:
    service, _, credentials = _service(tmp_path)
    credentials.delete(provider_connection_credential_purpose("conn-a"))
    service._apply_persisted_canonical_binding()  # type: ignore[attr-defined]
    snapshot = service.binding.transport_binding
    assert snapshot is not None
    assert [entry.connection_id for entry in snapshot.entries] == ["conn-b"]


def test_credential_rotation_restore_uses_latest_revision(tmp_path: Path) -> None:
    service, management, credentials = _service(tmp_path)
    before = service.binding.transport_binding
    assert before is not None
    old_revision = next(
        entry.credential_revision for entry in before.entries if entry.connection_id == "conn-a"
    )
    credentials.set(provider_connection_credential_purpose("conn-a"), b"secret-a-rotated")
    management.save()

    restarted_management = ModelGatewayManagementService(
        store=SQLiteModelGatewayStore(tmp_path / "model-gateway.sqlite3")
    )
    assert restarted_management.restore_from_store() is True
    restarted, _, _ = _service(
        tmp_path,
        management=restarted_management,
        credentials=credentials,
    )
    after = restarted.binding.transport_binding
    assert after is not None
    new_revision = next(
        entry.credential_revision for entry in after.entries if entry.connection_id == "conn-a"
    )
    assert new_revision is not None and new_revision != old_revision


def test_endpoint_revision_restore_rebuilds_adapter_snapshot(tmp_path: Path) -> None:
    service, management, credentials = _service(tmp_path)
    before = service.binding.transport_binding
    assert before is not None
    old_revision = next(
        entry.endpoint_revision for entry in before.entries if entry.connection_id == "conn-a"
    )
    endpoint = management.endpoint_profile("endpoint-demo-a")
    assert endpoint is not None
    management.upsert_endpoint(
        endpoint.model_copy(
            update={
                "base_url": "https://endpoint-demo-a-revision.example.test/v1",
                "updated_at": endpoint.updated_at + timedelta(seconds=1),
            }
        )
    )
    management.save()
    restarted_management = ModelGatewayManagementService(
        store=SQLiteModelGatewayStore(tmp_path / "model-gateway.sqlite3")
    )
    assert restarted_management.restore_from_store() is True
    restarted, _, _ = _service(
        tmp_path, management=restarted_management, credentials=credentials
    )
    after = restarted.binding.transport_binding
    assert after is not None
    changed = next(entry for entry in after.entries if entry.connection_id == "conn-a")
    assert changed.endpoint_revision != old_revision
    assert changed.adapter is not next(
        entry.adapter for entry in before.entries if entry.connection_id == "conn-a"
    )
    assert changed.adapter._base_url == "https://endpoint-demo-a-revision.example.test/v1/"


def test_disabled_deleted_and_quarantined_connections_are_excluded(tmp_path: Path) -> None:
    service, management, _ = _service(tmp_path)
    management.disable_connection("conn-a")
    management.update_connection("conn-b", status=ProviderConnectionStatus.QUARANTINED)
    service._apply_persisted_canonical_binding()  # type: ignore[attr-defined]
    assert service.binding.transport_binding is None
    assert service.binding.transport_ready is False

    # Tombstoned history remains in the registry while still being excluded.
    management.tombstone_connection("conn-b")
    assert {item.connection_id for item in management.list_connections()} == {"conn-a", "conn-b"}


def test_connection_lifecycle_invalidates_only_the_target_entry(tmp_path: Path) -> None:
    service, _, _ = _service(tmp_path)
    service.disable_connection("conn-a")
    snapshot = service.binding.transport_binding
    assert snapshot is not None
    assert [entry.connection_id for entry in snapshot.entries] == ["conn-b"]

    service.delete_connection("conn-b")
    assert service.binding.transport_binding is None


def test_restore_never_calls_provider_transport(tmp_path: Path) -> None:
    calls: list[object] = []

    class NoRequestTransport:
        def handle_request(self, request: object) -> object:
            calls.append(request)
            raise AssertionError("provider request is forbidden during restore")

    service, _, _ = _service(tmp_path)
    service._http_transport = NoRequestTransport()  # type: ignore[attr-defined]
    service._apply_persisted_canonical_binding()  # type: ignore[attr-defined]
    assert calls == []
