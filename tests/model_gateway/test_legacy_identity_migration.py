from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from embedded_copilot.model_gateway import (
    CredentialProfile,
    EndpointProfile,
    EndpointType,
    LegacyIdentityMigrationService,
    LegacyIdentityResolver,
    LegacyIdentityStatus,
    ModelGatewayManagementService,
    ModelGatewayState,
    ProtocolFamily,
    ProviderConnection,
    ProviderConnectionStatus,
    ProviderProfile,
    SQLiteModelGatewayStore,
    VerificationStatus,
)
from embedded_copilot.model_gateway.presets import manual_model_profile


def _state(*, two_connections: bool = False, deleted: bool = False, quarantined: bool = False) -> ModelGatewayState:
    provider = ProviderProfile(
        provider_id="deepseek",
        display_name="DeepSeek",
        supported_protocols=(ProtocolFamily.OPENAI_CHAT,),
    )
    count = (1, 2) if two_connections else (1,)
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
        for index in count
    )
    status = (
        ProviderConnectionStatus.DELETED
        if deleted
        else ProviderConnectionStatus.QUARANTINED
        if quarantined
        else ProviderConnectionStatus.ACTIVE
    )
    connections = tuple(
        ProviderConnection(
            connection_id=f"connection-{index}",
            provider_id="deepseek",
            endpoint_id=f"endpoint-{index}",
            credential_ref=f"credential:deepseek-{index}",
            status=status if index == 1 else ProviderConnectionStatus.DISABLED,
        )
        for index in count
    )
    credentials = tuple(
        CredentialProfile(
            credential_id=f"credential-{index}",
            provider_id="deepseek",
            display_name=f"Credential {index}",
            secret_ref=f"credential:deepseek-{index}",
        )
        for index in count
    )
    model = manual_model_profile(
        model_profile_id="model-deepseek-chat",
        provider_id="deepseek",
        endpoint_id="endpoint-1",
        remote_model_id="deepseek-chat",
        display_name="DeepSeek Chat",
    )
    return ModelGatewayState(
        providers=(provider,),
        endpoints=endpoints,
        connections=connections,
        credentials=credentials,
        models=(model,),
    )


def _source(**updates: str) -> dict[str, str]:
    value = {
        "source_type": "provider_configuration",
        "record_reference": "chat-model",
        "provider_id": "deepseek",
        "endpoint_id": "endpoint-1",
        "credential_ref": "credential:deepseek-1",
        "model_profile_id": "model-deepseek-chat",
        "model_id": "deepseek-chat",
    }
    value.update(updates)
    return value


def _store(tmp_path: Path, state: ModelGatewayState) -> SQLiteModelGatewayStore:
    store = SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3")
    store.save(state)
    return store


def test_unique_correlation_persists_and_restart_is_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path, _state())
    service = LegacyIdentityMigrationService(store)
    with sqlite3.connect(tmp_path / "gateway.sqlite3") as connection:
        receipt_count_before = connection.execute("SELECT COUNT(*) FROM model_invocation_receipts").fetchone()[0]

    first = service.apply([_source()])
    restored = store.load()
    assert restored is not None
    assert first.unique_migrated == 1
    assert first.ambiguous_quarantined == first.unresolved_quarantined == 0
    assert len(restored.identity_correlations) == 1
    correlation = restored.identity_correlations[0]
    assert correlation.provider_id == "deepseek"
    assert correlation.connection_id == "connection-1"
    assert correlation.status == "UNIQUE"
    marker = restored.model_identity_source_fingerprint

    second = LegacyIdentityMigrationService(SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3")).apply([_source()])
    restarted = store.load()
    assert second.unchanged == 1
    assert restarted is not None
    assert restarted.identity_correlations[0].correlation_fingerprint == correlation.correlation_fingerprint
    assert restarted.model_identity_source_fingerprint == marker
    with sqlite3.connect(tmp_path / "gateway.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_invocation_receipts").fetchone()[0] == receipt_count_before


def test_ambiguous_writes_one_record_issue_and_never_quarantines_connections(tmp_path: Path) -> None:
    store = _store(tmp_path, _state(two_connections=True))
    before = {item.connection_id: item.status for item in store.load().connections}
    service = LegacyIdentityMigrationService(store)
    source = _source(endpoint_id=None, credential_ref=None, model_profile_id=None)
    source.pop("endpoint_id")
    source.pop("credential_ref")
    source.pop("model_profile_id")
    result = service.apply([source])
    state = store.load()
    assert result.ambiguous_quarantined == 1
    assert state is not None
    assert state.identity_correlations == ()
    assert len(state.integrity_issues) == 1
    assert state.integrity_issues[0].reason == "RECORD_MIGRATION_AMBIGUOUS"
    assert {item.connection_id: item.status for item in state.connections} == before
    first_fingerprint = state.fingerprint

    repeated = service.apply([source])
    assert repeated.unchanged == 1
    repeated_state = store.load()
    assert repeated_state is not None
    assert len(repeated_state.integrity_issues) == 1
    assert repeated_state.fingerprint == first_fingerprint


def test_unresolved_model_only_writes_unresolved_issue_without_default(tmp_path: Path) -> None:
    store = _store(tmp_path, _state())
    result = LegacyIdentityMigrationService(store).apply(
        [{"source_type": "legacy_pool", "record_reference": "row-1", "model_id": "deepseek-chat"}]
    )
    state = store.load()
    assert result.ambiguous_quarantined + result.unresolved_quarantined == 1
    assert state is not None
    assert state.identity_correlations == ()
    assert state.integrity_issues[0].reason == "LEGACY_IDENTITY_UNRESOLVED"
    assert state.integrity_issues[0].provider_id is None


@pytest.mark.parametrize("kwargs", [{"deleted": True}, {"quarantined": True}])
def test_historical_lifecycle_correlation_does_not_activate_connection(tmp_path: Path, kwargs: dict[str, bool]) -> None:
    store = _store(tmp_path, _state(**kwargs))
    result = LegacyIdentityMigrationService(store).apply([_source()])
    state = store.load()
    assert result.unique_migrated == 1
    assert state is not None
    assert state.identity_correlations[0].historical_correlation is True
    assert state.identity_correlations[0].current_eligible is False
    assert state.connections[0].status in {ProviderConnectionStatus.DELETED, ProviderConnectionStatus.QUARANTINED}


def test_source_change_replaces_current_projection_without_two_current_correlations(tmp_path: Path) -> None:
    store = _store(tmp_path, _state())
    service = LegacyIdentityMigrationService(store)
    service.apply([_source()])
    changed = _source(model_id="deepseek-chat-v2")
    result = service.apply([changed])
    state = store.load()
    assert result.ambiguous_quarantined + result.unresolved_quarantined == 1
    assert state is not None
    assert len(state.identity_correlations) == 0
    assert len(state.integrity_issues) == 1
    assert state.integrity_issues[0].source_fingerprint != state.model_identity_source_fingerprint


def test_preview_is_zero_write_and_shared_with_apply(tmp_path: Path) -> None:
    store = _store(tmp_path, _state())
    before = store.load()
    assert before is not None
    before_payload = json.dumps(before.model_dump(mode="json"), sort_keys=True)
    service = LegacyIdentityMigrationService(store)
    preview = service.preview([_source()])
    after = store.load()
    assert after is not None
    assert json.dumps(after.model_dump(mode="json"), sort_keys=True) == before_payload
    assert preview.results[0].status is LegacyIdentityStatus.UNIQUE


def test_atomic_failure_leaves_correlations_issues_and_marker_unchanged(tmp_path: Path) -> None:
    store = _store(tmp_path, _state())
    before = store.load()
    assert before is not None

    def fail(_: ModelGatewayState) -> None:
        raise RuntimeError("injected pre-commit failure")

    with pytest.raises(RuntimeError, match="injected"):
        LegacyIdentityMigrationService(store).apply([_source()], before_commit=fail)
    after = store.load()
    assert after is not None
    assert after.fingerprint == before.fingerprint
    assert after.identity_correlations == before.identity_correlations
    assert after.integrity_issues == before.integrity_issues
    assert after.model_identity_source_fingerprint == before.model_identity_source_fingerprint


def test_custom_source_semantics_and_no_secret_persistence(tmp_path: Path) -> None:
    store = _store(tmp_path, _state())
    result = LegacyIdentityMigrationService(store).apply(
        [_source(source_type="old_project_default", record_reference="project-x")],
        source_semantics="PROJECT_DEFAULT",
    )
    state = store.load()
    assert result.unique_migrated == 1
    assert state is not None
    assert state.identity_correlations[0].source_semantics == "PROJECT_DEFAULT"
    with sqlite3.connect(tmp_path / "gateway.sqlite3") as connection:
        payload = connection.execute("SELECT payload FROM model_gateway_state WHERE id = 1").fetchone()[0]
    assert "actual-secret" not in payload
    assert "actual-secret-value" not in payload


def test_stale_revision_correlation_does_not_promote_verification(tmp_path: Path) -> None:
    state = _state()
    verified_model = state.models[0].model_copy(update={"verification_status": VerificationStatus.VERIFIED})
    payload = state.model_dump(mode="json")
    payload["models"] = [verified_model.model_dump(mode="json")]
    payload["fingerprint"] = "sha256:" + "0" * 64
    state = ModelGatewayState.model_validate_json(json.dumps(payload))
    store = _store(tmp_path, state)
    resolver = LegacyIdentityResolver.from_state(
        state,
        endpoint_revisions={"endpoint-1": "endpoint-rev-2"},
        credential_revisions={"credential:deepseek-1": "credential-rev-2"},
        model_revisions={"model-deepseek-chat": "model-rev-2"},
    )
    source = _source(endpoint_revision="endpoint-rev-1", credential_revision="credential-rev-1", model_revision="model-rev-1")
    LegacyIdentityMigrationService(store, resolver=resolver).apply([source])
    restored = store.load()
    assert restored is not None
    assert restored.identity_correlations[0].verification_current is False
    assert restored.identity_correlations[0].credential_revision_currentness is False


def test_management_restart_restores_correlations_without_governance_promotion(tmp_path: Path) -> None:
    path = tmp_path / "gateway.sqlite3"
    store = SQLiteModelGatewayStore(path)
    state = _state()
    store.save(state)
    LegacyIdentityMigrationService(store).apply([_source()])
    management = ModelGatewayManagementService(store=SQLiteModelGatewayStore(path))
    assert management.restore_from_store() is True
    assert len(management.state().identity_correlations) == 1
    assert management.list_allowed_model_policies() == ()
    assert management.state().project_default_model_policies == ()
