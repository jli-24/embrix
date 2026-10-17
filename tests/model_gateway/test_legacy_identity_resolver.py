from __future__ import annotations

import copy
from datetime import UTC, datetime

from embedded_copilot.model_gateway import (
    CredentialProfile,
    EndpointProfile,
    EndpointType,
    LegacyIdentityResolver,
    LegacyIdentityStatus,
    ProtocolFamily,
    ProviderConnection,
    ProviderConnectionStatus,
    ProviderProfile,
    ModelGatewayState,
    SQLiteModelGatewayStore,
    integrity_issue_from_resolution,
)
from embedded_copilot.model_gateway.presets import manual_model_profile


def _fixture(*, two_connections: bool = False, status: ProviderConnectionStatus = ProviderConnectionStatus.ACTIVE):
    provider = ProviderProfile(
        provider_id="deepseek",
        display_name="DeepSeek",
        supported_protocols=(ProtocolFamily.OPENAI_CHAT,),
    )
    endpoints = tuple(
        EndpointProfile(
            endpoint_id=f"endpoint-{index}",
            provider_id="deepseek",
            display_name=f"Endpoint {index}",
            endpoint_type=EndpointType.OFFICIAL,
            protocol_family=ProtocolFamily.OPENAI_CHAT,
            base_url=f"https://endpoint-{index}.example.test/v1",
            credential_ref=f"credential:deepseek-{index}",
        )
        for index in ((1, 2) if two_connections else (1,))
    )
    connections = tuple(
        ProviderConnection(
            connection_id=f"connection-{index}",
            provider_id="deepseek",
            endpoint_id=f"endpoint-{index}",
            credential_ref=f"credential:deepseek-{index}",
            status=status if index == 1 else ProviderConnectionStatus.DISABLED,
        )
        for index in ((1, 2) if two_connections else (1,))
    )
    credentials = tuple(
        CredentialProfile(
            credential_id=f"credential-{index}",
            provider_id="deepseek",
            display_name=f"Credential {index}",
            secret_ref=f"credential:deepseek-{index}",
        )
        for index in ((1, 2) if two_connections else (1,))
    )
    model = manual_model_profile(
        model_profile_id="model-deepseek-chat",
        provider_id="deepseek",
        endpoint_id="endpoint-1",
        remote_model_id="deepseek-chat",
        display_name="DeepSeek Chat",
    )
    return provider, endpoints, connections, credentials, model


def _resolver(**kwargs):
    provider, endpoints, connections, credentials, model = _fixture(**kwargs.pop("fixture", {}))
    return LegacyIdentityResolver(
        providers=(provider,),
        endpoints=endpoints,
        connections=connections,
        credentials=credentials,
        models=(model,),
        **kwargs,
    )


def test_resolution_requires_ownership_context_for_model_id_only() -> None:
    result = _resolver().resolve({"model_id": "deepseek-chat"})
    assert result.status is LegacyIdentityStatus.UNRESOLVED
    assert result.resolved_identity is None


def test_provider_model_with_multiple_connections_is_ambiguous_even_when_one_disabled() -> None:
    result = _resolver(fixture={"two_connections": True}).resolve(
        {"provider_id": "deepseek", "model_id": "deepseek-chat"}
    )
    assert result.status is LegacyIdentityStatus.AMBIGUOUS
    assert result.candidate_count == 2


def test_profile_and_endpoint_contexts_are_proven_by_authoritative_records() -> None:
    resolver = _resolver()
    assert resolver.resolve({"model_profile_id": "model-deepseek-chat"}).status is LegacyIdentityStatus.UNIQUE
    assert resolver.resolve({"provider_id": "deepseek", "model_profile_id": "model-deepseek-chat"}).status is LegacyIdentityStatus.UNIQUE
    assert resolver.resolve({"endpoint_id": "endpoint-1", "model_id": "deepseek-chat"}).status is LegacyIdentityStatus.UNIQUE
    assert resolver.resolve({"provider_id": "deepseek", "endpoint_id": "endpoint-1", "model_id": "deepseek-chat"}).status is LegacyIdentityStatus.UNIQUE


def test_full_five_tuple_is_validated_and_conflicts_are_not_guessed() -> None:
    resolver = _resolver()
    source = {
        "provider_id": "deepseek",
        "endpoint_id": "endpoint-1",
        "credential_ref": "credential:deepseek-1",
        "model_profile_id": "model-deepseek-chat",
        "model_id": "deepseek-chat",
    }
    result = resolver.resolve(source)
    assert result.status is LegacyIdentityStatus.UNIQUE
    assert result.resolved_identity is not None
    assert result.resolved_identity.model_id == "deepseek-chat"
    assert resolver.resolve({**source, "credential_ref": "credential:deepseek-2"}).status is LegacyIdentityStatus.UNRESOLVED


def test_deleted_and_quarantined_connections_remain_historical_only() -> None:
    for status in (ProviderConnectionStatus.DELETED, ProviderConnectionStatus.QUARANTINED):
        result = _resolver(fixture={"status": status}).resolve(
            {
                "provider_id": "deepseek",
                "endpoint_id": "endpoint-1",
                "credential_ref": "credential:deepseek-1",
                "model_profile_id": "model-deepseek-chat",
                "model_id": "deepseek-chat",
            }
        )
        assert result.status is LegacyIdentityStatus.UNIQUE
        assert result.historical_correlation is True
        assert result.current_eligible is False


def test_revision_currentness_is_separate_from_identity_status() -> None:
    resolver = _resolver(
        endpoint_revisions={"endpoint-1": "endpoint-rev-2"},
        credential_revisions={"credential:deepseek-1": "credential-rev-2"},
        model_revisions={"model-deepseek-chat": "model-rev-2"},
    )
    result = resolver.resolve(
        {
            "provider_id": "deepseek",
            "endpoint_id": "endpoint-1",
            "credential_ref": "credential:deepseek-1",
            "model_profile_id": "model-deepseek-chat",
            "model_id": "deepseek-chat",
            "endpoint_revision": "endpoint-rev-1",
            "credential_revision": "credential-rev-1",
            "model_revision": "model-rev-1",
        }
    )
    assert result.status is LegacyIdentityStatus.UNIQUE
    assert result.endpoint_revision_currentness is False
    assert result.credential_revision_currentness is False
    assert result.model_revision_currentness is False
    assert result.verification_current is False


def test_custom_relay_alias_preserves_canonical_historical_provider() -> None:
    provider, endpoints, connections, credentials, model = _fixture()
    custom_provider = provider.model_copy(update={"provider_id": "custom-abc123"})
    custom_endpoint = endpoints[0].model_copy(update={"provider_id": "custom-abc123", "endpoint_id": "custom-endpoint", "credential_ref": "credential:custom-abc123"})
    custom_connection = connections[0].model_copy(update={"provider_id": "custom-abc123", "endpoint_id": "custom-endpoint", "credential_ref": "credential:custom-abc123"})
    custom_credential = credentials[0].model_copy(update={"provider_id": "custom-abc123", "secret_ref": "credential:custom-abc123"})
    custom_model = model.model_copy(update={"provider_id": "custom-abc123", "endpoint_id": "custom-endpoint"})
    resolver = LegacyIdentityResolver(providers=(custom_provider,), endpoints=(custom_endpoint,), connections=(custom_connection,), credentials=(custom_credential,), models=(custom_model,))
    result = resolver.resolve({"provider_id": "custom-relay", "model_id": "deepseek-chat"})
    assert result.status is LegacyIdentityStatus.UNIQUE
    assert result.resolved_identity is not None
    assert result.resolved_identity.provider_id == "custom-abc123"


def test_local_sentinel_does_not_create_a_secret_and_dry_run_is_repeatable() -> None:
    provider = ProviderProfile(provider_id="local", display_name="Local")
    endpoint = EndpointProfile(endpoint_id="local-endpoint", provider_id="local", display_name="Local", endpoint_type=EndpointType.LOCAL, protocol_family=ProtocolFamily.LOCAL_OPENAI_COMPATIBLE, base_url="http://127.0.0.1:11434/v1", credential_ref="credential:local")
    connection = ProviderConnection(connection_id="local-connection", provider_id="local", endpoint_id="local-endpoint", credential_ref="credential:local")
    model = manual_model_profile(model_profile_id="local-model", provider_id="local", endpoint_id="local-endpoint", remote_model_id="llama3", display_name="Llama")
    resolver = LegacyIdentityResolver(providers=(provider,), endpoints=(endpoint,), connections=(connection,), credentials=(), models=(model,))
    source = {"provider_id": "local", "endpoint_id": "local-endpoint", "model_id": "llama3"}
    before = copy.deepcopy(source)
    first = resolver.dry_run((source,))
    second = resolver.dry_run((source,))
    assert source == before
    assert first == second
    assert first.unique_count == 1
    assert first.results[0].resolved_identity.credential_ref == "credential:local"
    assert "secret" not in first.model_dump_json().lower()


def test_quarantine_contract_is_metadata_only_and_secret_free() -> None:
    result = _resolver().resolve({"model_id": "missing"})
    issue = integrity_issue_from_resolution(result, migration_version=2, migration_sub_version=1, detected_at=datetime(2026, 1, 1, tzinfo=UTC))
    assert issue is not None
    assert issue.source_type == "LEGACY"
    assert issue.record_reference == "legacy-record"
    assert issue.migration_version == 2
    assert issue.migration_sub_version == 1
    assert "secret" not in issue.model_dump_json().lower()


def test_preview_marks_same_source_conflict_ambiguous() -> None:
    resolver = _resolver()
    preview = resolver.preview(
        (
            {"record_reference": "same", "provider_id": "deepseek", "endpoint_id": "endpoint-1", "model_id": "deepseek-chat"},
            {"record_reference": "same", "provider_id": "deepseek", "endpoint_id": "endpoint-1", "model_id": "other-model"},
        )
    )
    assert preview.ambiguous_count == 2
    assert preview.unique_count == 0


def test_model_identity_marker_round_trips_in_the_existing_store(tmp_path) -> None:
    path = tmp_path / "gateway.sqlite3"
    store = SQLiteModelGatewayStore(path)
    state = ModelGatewayState(
        model_identity_migration_version=1,
        model_identity_source_fingerprint="sha256:" + "a" * 64,
    )
    store.save(state)
    restored = store.load()
    assert restored is not None
    assert restored.model_identity_migration_version == 1
    assert restored.model_identity_source_fingerprint == "sha256:" + "a" * 64
    assert SQLiteModelGatewayStore.MODEL_IDENTITY_MIGRATION_SUB_VERSION == 1
