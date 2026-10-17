from __future__ import annotations

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from embedded_copilot.api.v70_release_models import (
    ProviderConfigurationInput,
    ProviderConnectionTestInput,
)
from embedded_copilot.model_gateway import (
    AccountUsageSnapshot,
    BoundModelIdentity,
    EngineeringStage,
    ModelGatewayManagementService,
    ModelInvocationRequest,
    ModelInvocationService,
    ModelPolicy,
    ModelRequest,
    PolicyScope,
    RecordProvenance,
    SelectionMode,
    SQLiteModelGatewayStore,
    StageModelPolicy,
    UsageSnapshotStatus,
    VerificationStatus,
)
from embedded_copilot.model_gateway.invocation import ManagedModelSelectionResolver
from embedded_copilot.production_release.credentials import InMemoryCredentialProvider
from embedded_copilot.production_release.model_pool import ProviderModelPool
from embedded_copilot.production_release.provider import ProviderConfigurationService


class _UsagePort:
    def read(self, endpoint, credential_ref):
        return AccountUsageSnapshot(
            provider_id=endpoint.provider_id,
            endpoint_id=endpoint.endpoint_id,
            credential_ref=credential_ref,
            quota_remaining=1,
        )


def _transport(calls: list[tuple[str, str]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        calls.append((str(request.url), str(payload["model"])))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "offline answer"}}]},
        )

    return httpx.MockTransport(handler)


def _request() -> ModelRequest:
    from embedded_copilot.engineering_context import EngineeringContextSnapshot

    context = EngineeringContextSnapshot.create(
        project_id="project-authority",
        tenant_id="tenant-authority",
        task_description="authority test",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.0,
        provenance=(),
        verification_status="UNVERIFIED",
    )
    return ModelRequest(
        request_id="authority-request",
        task="Answer offline.",
        engineering_context=context,
        system_instruction="Answer offline only.",
        model_policy=ModelPolicy(primary_provider="unavailable", model_name="unavailable"),
        project_id="project-authority",
        task_type="GENERAL_CONVERSATION",
    )


def _setup(tmp_path: Path):
    calls: list[tuple[str, str]] = []
    credentials = InMemoryCredentialProvider()
    management = ModelGatewayManagementService(
        store=SQLiteModelGatewayStore(tmp_path / "model-gateway.sqlite3")
    )
    from embedded_copilot.model_gateway import provider_presets

    for preset in provider_presets():
        management.add_provider(preset)
    management.set_account_usage_port(_UsagePort())
    service = ProviderConfigurationService(
        configuration_path=tmp_path / "production.json",
        credentials=credentials,
        http_transport=_transport(calls),
        async_transport=_transport(calls),
        model_gateway_management=management,
    )
    service.save_credential(SecretStr("deepseek-secret"), provider="deepseek")
    service.test_connection()
    legacy_configuration = json.loads((tmp_path / "production.json").read_text(encoding="utf-8"))

    service.save_configuration(provider="zhipu", model="glm-4")
    service.save_credential(SecretStr("zhipu-secret"), provider="zhipu")
    assert service.test_connection().connection_status.value == "AVAILABLE"
    zhipu = service.projection()
    identity = BoundModelIdentity(
        provider_id="zhipu",
        endpoint_id=zhipu.endpoint_id or "",
        credential_ref=zhipu.credential_ref or "",
        model_profile_id=zhipu.model_profile_id or "",
        model_id=zhipu.model_id or "",
    )
    connection = next(
        item for item in management.list_connections()
        if item.provider_id == identity.provider_id and item.endpoint_id == identity.endpoint_id
    )
    management.restore(
        management.state().model_copy(
            update={
                "usage_snapshots": (
                    AccountUsageSnapshot(
                        provider_id=identity.provider_id,
                        endpoint_id=identity.endpoint_id,
                        connection_id=connection.connection_id,
                        credential_ref=identity.credential_ref,
                        credential_revision=service.credential_revision_for_connection(connection.connection_id),
                        quota_remaining=1,
                        status=UsageSnapshotStatus.AVAILABLE,
                    ),
                )
            }
        )
    )
    deepseek = next(
        item for item in management.list_models()
        if item.provider_id == "deepseek" and item.remote_model_id == "deepseek-chat"
    )
    legacy_identity = BoundModelIdentity(
        provider_id=deepseek.provider_id,
        endpoint_id=deepseek.endpoint_id,
        credential_ref="credential:deepseek",
        model_profile_id=deepseek.model_profile_id,
        model_id=deepseek.remote_model_id,
    )
    management.create_allowed_model_policy(
        scope=PolicyScope.GLOBAL,
        project_id=None,
        stage_id=None,
        identity=identity,
    )
    now = datetime.now(UTC)
    management.set_stage_model_policy(
        StageModelPolicy(
            id="stage-authority-fixed",
            project_id="project-authority",
            stage_id=EngineeringStage.REQUIREMENT_ANALYSIS,
            selection_mode=SelectionMode.FIXED_MODEL,
            fixed_model=identity,
            created_at=now,
            updated_at=now,
        )
    )
    management.set_active_model(legacy_identity.model_profile_id)
    (tmp_path / "production.json").write_text(
        json.dumps(legacy_configuration), encoding="utf-8"
    )
    restarted = ProviderConfigurationService(
        configuration_path=tmp_path / "production.json",
        credentials=credentials,
        http_transport=_transport(calls),
        async_transport=_transport(calls),
        model_gateway_management=management,
    )
    invocation = ModelInvocationService(
        selection_resolver=ManagedModelSelectionResolver(
            management,
            restarted.available_credential_refs,
            restarted.credential_revision_for_connection,
            ProviderModelPool.endpoint_revision,
        ),
        gateway=restarted.binding,
    )
    return calls, management, restarted, invocation, identity, legacy_identity


def test_provider_save_and_probe_do_not_change_global_runtime_selection(tmp_path: Path) -> None:
    calls, management, service, _, identity, legacy_identity = _setup(tmp_path)
    assert management.global_policy().active_model_profile_id == legacy_identity.model_profile_id


def test_canonical_connection_probe_writes_evidence_without_legacy_or_governance_mutation(
    tmp_path: Path,
) -> None:
    calls, management, service, _, identity, legacy_identity = _setup(tmp_path)
    connection = next(
        item for item in management.list_connections() if item.provider_id == identity.provider_id
    )
    legacy_file_before = json.loads(
        (tmp_path / "production.json").read_text(encoding="utf-8")
    )
    configuration_before = service._configuration
    profiles_before = copy.deepcopy(service._provider_profiles)
    connection_configurations_before = copy.deepcopy(service._connection_configurations)
    global_before = management.global_policy()
    allowed_before = management.list_allowed_model_policies()
    project_before = management.project_default_model_policy("project-authority")
    stage_before = management.stage_model_policy(
        "project-authority", EngineeringStage.REQUIREMENT_ANALYSIS
    )
    model_before = management.model_profile(identity.model_profile_id)

    result = service.test_connection(
        draft={
            "connection_id": connection.connection_id,
            "provider_id": identity.provider_id,
            "endpoint_id": identity.endpoint_id,
            "credential_ref": identity.credential_ref,
            "model_profile_id": identity.model_profile_id,
            "model_id": identity.model_id,
        }
    )

    assert result.model == identity.model_id
    assert calls[-1][1] == identity.model_id
    assert service._configuration == configuration_before
    assert service._provider_profiles == profiles_before
    assert service._connection_configurations == connection_configurations_before
    assert json.loads((tmp_path / "production.json").read_text(encoding="utf-8")) == legacy_file_before
    assert management.global_policy() == global_before
    assert management.list_allowed_model_policies() == allowed_before
    assert management.project_default_model_policy("project-authority") == project_before
    assert management.stage_model_policy(
        "project-authority", EngineeringStage.REQUIREMENT_ANALYSIS
    ) == stage_before
    model_after = management.model_profile(identity.model_profile_id)
    assert model_before is not None and model_after is not None
    assert model_after.verified_at != model_before.verified_at
    assert management.global_policy().active_model_profile_id == legacy_identity.model_profile_id


    assert service.projection().provider == "deepseek"
    assert calls[0][1] == "deepseek-chat"

    result = service.test_connection(
        draft={
            "provider_id": identity.provider_id,
            "endpoint_id": identity.endpoint_id,
            "credential_ref": identity.credential_ref,
            "model_profile_id": identity.model_profile_id,
            "model_id": identity.model_id,
        }
    )
    assert result.model == identity.model_id
    assert management.global_policy().active_model_profile_id == legacy_identity.model_profile_id


def test_successful_explicit_connection_test_restores_governed_transport_binding(
    tmp_path: Path,
) -> None:
    calls, management, service, _, identity, _ = _setup(tmp_path)
    connection = next(
        item for item in management.list_connections()
        if item.provider_id == identity.provider_id and item.endpoint_id == identity.endpoint_id
    )
    service.binding.invalidate("PROVIDER_CONNECTION_UNVERIFIED")
    assert service.binding.transport_binding is None

    service.test_connection(
        draft={
            "connection_id": connection.connection_id,
            "provider_id": identity.provider_id,
            "endpoint_id": identity.endpoint_id,
            "credential_ref": identity.credential_ref,
            "model_profile_id": identity.model_profile_id,
            "model_id": identity.model_id,
        }
    )

    assert len(calls) == 3
    assert service._last_probe_result["status"].value == "AVAILABLE"
    binding = service.binding.transport_binding
    assert binding is not None
    assert binding.resolve(
        (identity.provider_id, identity.endpoint_id, identity.credential_ref)
    ) is not None



def test_provider_configuration_is_connection_scoped_and_probe_identity_is_complete() -> None:
    assert ProviderConfigurationInput(provider="openai").model is None
    probe = ProviderConnectionTestInput(
        provider_id="openai",
        endpoint_id="endpoint-openai",
        credential_ref="credential:openai",
        model_profile_id="model-openai-gpt-4o-mini",
        model_id="gpt-4o-mini",
    )
    assert set(probe.model_dump()) >= {
        "provider_id",
        "endpoint_id",
        "credential_ref",
        "model_profile_id",
        "model_id",
    }
    with pytest.raises(ValueError):
        ProviderConnectionTestInput(
            provider_id="openai",
            endpoint_id="endpoint-openai",
            credential_ref="credential:openai",
            model_profile_id="model-openai-gpt-4o-mini",
        )


def test_legacy_probe_aliases_are_loadable_but_normalized_to_canonical_identity() -> None:
    probe = ProviderConnectionTestInput(
        provider="openai",
        endpoint_id="endpoint-openai",
        credential_ref="credential:openai",
        model_profile_id="model-openai-gpt-4o-mini",
        model="gpt-4o-mini",
    )
    assert probe.provider_id == "openai"
    assert probe.model_id == "gpt-4o-mini"
    assert "provider" not in probe.model_dump()
    assert "model" not in probe.model_dump()


def test_stage_fixed_model_wins_over_legacy_configuration_and_global_policy(tmp_path: Path) -> None:
    calls, management, _, invocation, identity, legacy_identity = _setup(tmp_path)
    result = invocation.invoke(
        ModelInvocationRequest.from_model_request(
            _request(),
            stage_id=EngineeringStage.REQUIREMENT_ANALYSIS,
            task_type="GENERAL_CONVERSATION",
        )
    )
    assert result.status.value == "SUCCESS"
    assert result.context is not None
    assert result.context.selected_model == identity
    assert result.context.selection_reason == "STAGE_FIXED_MODEL"
    assert result.response is not None
    assert result.response.provider == identity.provider_id
    assert result.response.remote_model_id == identity.model_id
    assert calls[-1][1] == identity.model_id
    assert management.global_policy().active_model_profile_id == legacy_identity.model_profile_id


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "change",
    ["disabled_model", "unverified_model", "invalid_provenance", "credential_revision", "endpoint_revision", "disabled_provider", "disabled_endpoint", "disabled_connection"],
)
def test_invocation_revalidates_current_runtime_identity(
    tmp_path: Path, change: str, asynchronous: bool
) -> None:
    calls, management, _, invocation, identity, _ = _setup(tmp_path)
    calls.clear()
    model = management.model_profile(identity.model_profile_id)
    assert model is not None
    if change == "disabled_model":
        management.models.upsert(model.model_copy(update={"enabled": False}))
    elif change == "unverified_model":
        management.models.upsert(model.model_copy(update={"verification_status": VerificationStatus.UNVERIFIED}))
    elif change == "invalid_provenance":
        management.models.upsert(model.model_copy(update={"record_provenance": RecordProvenance.OFFICIAL_CATALOG}))
    elif change == "credential_revision":
        management.models.upsert(model.model_copy(update={"credential_revision": "old-revision"}))
    elif change == "endpoint_revision":
        management.models.upsert(model.model_copy(update={"endpoint_revision": "old-revision"}))
    elif change == "disabled_provider":
        provider = management.providers.get(identity.provider_id)
        assert provider is not None
        management.providers.replace(provider.model_copy(update={"enabled": False}))
    elif change == "disabled_endpoint":
        endpoint = management.endpoints.get(identity.endpoint_id)
        assert endpoint is not None
        management.endpoints.replace(endpoint.model_copy(update={"enabled": False}))
    else:
        connection = next(item for item in management.list_connections() if item.endpoint_id == identity.endpoint_id)
        management.connections.disable(connection.connection_id)
    request = ModelInvocationRequest.from_model_request(
        _request(), stage_id=EngineeringStage.REQUIREMENT_ANALYSIS, task_type="GENERAL_CONVERSATION"
    )
    result = asyncio.run(invocation.ainvoke(request)) if asynchronous else invocation.invoke(request)
    assert result.status.value == "REJECTED"
    assert result.provider_request_count == 0
    assert calls == []


@pytest.mark.parametrize("asynchronous", [False, True])
def test_current_fixed_model_still_invokes(tmp_path: Path, asynchronous: bool) -> None:
    calls, _, _, invocation, identity, _ = _setup(tmp_path)
    calls.clear()
    request = ModelInvocationRequest.from_model_request(
        _request(), stage_id=EngineeringStage.REQUIREMENT_ANALYSIS, task_type="GENERAL_CONVERSATION"
    )
    result = asyncio.run(invocation.ainvoke(request)) if asynchronous else invocation.invoke(request)
    assert result.status.value == "SUCCESS", (result.error_code, result.detail)
    assert result.context.selected_model == identity
    assert len(calls) == 1


@pytest.mark.parametrize(
    "change",
    ["provider_id", "connection_id", "credential_ref", "credential_revision", "endpoint_id", "stale", "unknown", "unavailable", "auth_failed", "empty", "expired", "before_endpoint", "conflicting", "newer_failure"],
)
def test_quota_evidence_must_match_current_runtime_identity(tmp_path: Path, change: str) -> None:
    calls, management, _, invocation, identity, _ = _setup(tmp_path)
    calls.clear()
    current = next(item for item in management.account_usage_snapshots() if item.endpoint_id == identity.endpoint_id)
    endpoint = management.endpoints.get(identity.endpoint_id)
    assert endpoint is not None
    edits = {
        "provider_id": {"provider_id": "old-provider"},
        "connection_id": {"connection_id": "conn-old"},
        "credential_ref": {"credential_ref": "credential:old"},
        "credential_revision": {"credential_revision": "old-revision"},
        "endpoint_id": {"endpoint_id": "endpoint-old"},
        "stale": {"status": UsageSnapshotStatus.STALE},
        "unknown": {"status": UsageSnapshotStatus.UNKNOWN},
        "unavailable": {"status": UsageSnapshotStatus.UNAVAILABLE},
        "auth_failed": {"status": UsageSnapshotStatus.AUTH_FAILED},
        "empty": {"quota_remaining": 0},
        "expired": {"updated_at": datetime.now(UTC) - timedelta(seconds=301)},
        "before_endpoint": {"updated_at": endpoint.updated_at - timedelta(seconds=1)},
        "conflicting": {"credential_revision": "old-revision"},
        "newer_failure": {"credential_revision": "old-revision", "status": UsageSnapshotStatus.STALE, "updated_at": datetime.now(UTC)},
    }
    altered = current.model_copy(update=edits[change])
    snapshots = (current, altered) if change in {"conflicting", "newer_failure"} else (altered,)
    management.restore(management.state().model_copy(update={"usage_snapshots": snapshots}))
    result = invocation.invoke(ModelInvocationRequest.from_model_request(
        _request(), stage_id=EngineeringStage.REQUIREMENT_ANALYSIS, task_type="GENERAL_CONVERSATION"
    ))
    assert result.status.value == "REJECTED"
    assert result.provider_request_count == 0
    assert calls == []


def test_disabled_allowed_pool_policy_rejects_invocation(tmp_path: Path) -> None:
    calls, management, _, invocation, identity, _ = _setup(tmp_path)
    calls.clear()
    policy = next(
        item for item in management.list_allowed_model_policies()
        if item.model_profile_id == identity.model_profile_id
    )
    management.set_allowed_model_enabled(policy.id, False)
    result = invocation.invoke(ModelInvocationRequest.from_model_request(
        _request(), stage_id=EngineeringStage.REQUIREMENT_ANALYSIS, task_type="GENERAL_CONVERSATION"
    ))
    assert result.status.value == "REJECTED"
    assert result.provider_request_count == 0
    assert calls == []


def test_canonical_connection_and_probe_do_not_call_legacy_store_writers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, management, service, _, identity, _ = _setup(tmp_path)
    legacy_before = (tmp_path / "production.json").read_bytes()

    def fail_legacy_writer(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("canonical path called a legacy configuration writer")

    monkeypatch.setattr(service._store, "save", fail_legacy_writer)
    monkeypatch.setattr(service._store, "save_profiles", fail_legacy_writer)

    connection = service.create_connection(
        provider_id="openai",
        connection_name="Task C connection",
        endpoint_url="https://api.openai.com/v1",
        protocol_family="OPENAI_CHAT",
    )
    service.save_credential(
        SecretStr("connection-secret"), connection_id=connection.connection_id
    )
    target = next(
        item
        for item in management.list_connections()
        if item.provider_id == identity.provider_id
    )
    service.test_connection(
        draft={
            "connection_id": target.connection_id,
            "provider_id": identity.provider_id,
            "endpoint_id": identity.endpoint_id,
            "credential_ref": identity.credential_ref,
            "model_profile_id": identity.model_profile_id,
            "model_id": identity.model_id,
        }
    )

    assert (tmp_path / "production.json").read_bytes() == legacy_before
    assert calls[-1][1] == identity.model_id


def test_canonical_transport_restore_does_not_read_legacy_model_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, management, service, _, _, _ = _setup(tmp_path)
    service._configuration = service._configuration.model_copy(
        update={"model": "legacy-garbage", "model_profile_id": "legacy-invalid"}
    )

    def fail_legacy_reader(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("canonical transport restore read legacy governance")

    monkeypatch.setattr(management, "global_policy", fail_legacy_reader)
    monkeypatch.setattr(management, "model_profile", fail_legacy_reader)
    service.binding.invalidate("PROVIDER_CONNECTION_UNVERIFIED")
    service._apply_persisted_canonical_binding()

    assert service.binding.transport_ready is True
    assert service.binding.failure_code is None


def test_legacy_only_configuration_does_not_execute_without_governance(
    tmp_path: Path,
) -> None:
    credentials = InMemoryCredentialProvider()
    configuration_path = tmp_path / "production.json"
    configuration_path.write_text(
        json.dumps(
            {
                "chat_provider": "deepseek",
                "chat_model": "legacy-y",
                "chat_base_url": "https://api.deepseek.com/v1",
            }
        ),
        encoding="utf-8",
    )
    management = ModelGatewayManagementService(
        store=SQLiteModelGatewayStore(tmp_path / "model-gateway.sqlite3")
    )
    from embedded_copilot.model_gateway import provider_presets

    for preset in provider_presets():
        management.add_provider(preset)
    service = ProviderConfigurationService(
        configuration_path=configuration_path,
        credentials=credentials,
        model_gateway_management=management,
    )
    invocation = ModelInvocationService(
        selection_resolver=ManagedModelSelectionResolver(
            management, service.available_credential_refs
        ),
        gateway=service.binding,
    )

    result = invocation.invoke(
        ModelInvocationRequest.from_model_request(
            _request(),
            stage_id=EngineeringStage.REQUIREMENT_ANALYSIS,
            task_type="GENERAL_CONVERSATION",
        )
    )

    assert result.status.value == "REJECTED"
    assert result.response is None
