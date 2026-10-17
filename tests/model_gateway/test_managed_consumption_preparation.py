"""Offline proof using the actual managed resolver and production Vault callbacks."""
from __future__ import annotations

import os
import socket
import sqlite3
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from embedded_copilot.model_gateway import (
    EngineeringStage,
    ModelGatewayService,
    ModelInvocationRequest,
    SelectionMode,
    SQLiteModelGatewayStore,
    StageModelPolicy,
)
from embedded_copilot.model_gateway.adapters.protocol_http import (
    ProtocolHTTPProviderAdapter,
)
from embedded_copilot.model_gateway.invocation import service as module
from embedded_copilot.model_gateway.invocation.contracts import InvocationMode
from embedded_copilot.model_gateway.transport import ResolvedTransportEntry
from embedded_copilot.production_release.model_pool import ProviderModelPool
from embedded_copilot.production_release.provider import (
    ProviderConfigurationService,
    ReloadableModelGateway,
)
from tests.model_gateway.test_invocation_service import (
    _RUNTIME_CONNECTION_ID,
    _RUNTIME_CREDENTIAL_REVISION,
    _RUNTIME_NOW,
    IDENTITY,
    _governed_runtime,
    _request,
    _task_authority,
)


class Vault:
    def __init__(self) -> None:
        self.calls = 0
        self.blocked = False

    def get(self, *args: object, **kwargs: object) -> bytes | None:
        self.calls += 1
        if not self.blocked:
            return None
        raise AssertionError('preview must not read Vault.get')


class Authority:
    def revalidate(self, reference: Any) -> Any:
        return reference


@pytest.fixture(autouse=True)
def no_external(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject(*args: object, **kwargs: object) -> None:
        raise AssertionError('preview must not construct client or contact external resources')
    monkeypatch.setattr(httpx, 'Client', reject)
    monkeypatch.setattr(httpx, 'AsyncClient', reject)
    monkeypatch.setattr(subprocess, 'Popen', reject)
    monkeypatch.setattr(os, 'system', reject)
    monkeypatch.setattr(socket, 'create_connection', reject)
    monkeypatch.setattr(socket.socket, 'connect', reject)


def case(tmp_path: Path, *, reloadable: bool = True) -> tuple[Any, ...]:
    management, *_ = _governed_runtime()
    endpoint = management.endpoint_profile(IDENTITY.endpoint_id)
    revision = ProviderModelPool.endpoint_revision(endpoint)
    model = management.model_profile(IDENTITY.model_profile_id)
    management.models.upsert(model.model_copy(update={
        'endpoint_revision': revision,
        'capability_sources': tuple(e.model_copy(update={'endpoint_revision': revision}) for e in model.capability_sources),
    }))
    management.set_stage_model_policy(StageModelPolicy(
        id='hardware-preview-policy', project_id='project-1', stage_id=EngineeringStage.HARDWARE_SELECTION,
        selection_mode=SelectionMode.FIXED_MODEL, fixed_model=IDENTITY, created_at=_RUNTIME_NOW, updated_at=_RUNTIME_NOW,
    ))
    vault = Vault()
    provider = ProviderConfigurationService(configuration_path=tmp_path/'provider.json', credentials=vault, now=lambda: _RUNTIME_NOW)
    vault.calls = 0
    vault.blocked = True
    provider._management = management
    resolver = module.ManagedModelSelectionResolver(
        management, available_credentials=provider.available_credential_refs,
        credential_revision_for_connection=provider.credential_revision_for_connection,
        endpoint_revision=ProviderModelPool.endpoint_revision,
    )
    adapter = ProtocolHTTPProviderAdapter(provider='relay', endpoint_id=IDENTITY.endpoint_id, base_url=endpoint.base_url,
        protocol_family=endpoint.protocol_family, api_key='offline-placeholder')
    entry = ResolvedTransportEntry(provider_id=IDENTITY.provider_id, endpoint_id=IDENTITY.endpoint_id,
        credential_ref=IDENTITY.credential_ref, adapter=adapter, connection_id=_RUNTIME_CONNECTION_ID,
        credential_revision=_RUNTIME_CREDENTIAL_REVISION, endpoint_revision=revision)
    gateway = ModelGatewayService(providers={'relay': adapter}, transport_entries=(entry,))
    active = ReloadableModelGateway() if reloadable else gateway
    if reloadable:
        active.install_transport(gateway, (entry,))
    store = SQLiteModelGatewayStore(tmp_path/'invocations.db')
    service = module.ModelInvocationService(selection_resolver=resolver, gateway=active, receipt_store=store, clock=lambda: _RUNTIME_NOW)
    request = ModelInvocationRequest.from_model_request(_request(), stage_id=EngineeringStage.HARDWARE_SELECTION,
        task_type='HARDWARE_SELECTION_PROPOSAL').model_copy(update={'invocation_mode':InvocationMode.TASK, 'authority_reference':_task_authority()})
    return service, resolver, management, vault, active, gateway, entry, request, store


def zero_effects(store: Any, vault: Vault, request: Any) -> None:
    assert vault.calls == 0
    assert store.get_attempt_for_project(request.project_id, request.request_id) is None
    with sqlite3.connect(store._path) as connection:
        assert connection.execute('SELECT count(*) FROM model_invocation_budget_claims').fetchone()[0] == 0


@pytest.mark.parametrize('reloadable', [False, True])
def test_real_resolver_production_callbacks_do_not_read_vault(tmp_path: Path, reloadable: bool) -> None:
    service, resolver, management, vault, _active, _gateway, entry, request, store = case(tmp_path, reloadable=reloadable)
    context = service.prepare_consumption_context(request, authority_source=Authority())
    assert context.execution_snapshot.connection_id == entry.connection_id
    assert context.execution_snapshot.endpoint_revision == ProviderModelPool.endpoint_revision(management.endpoint_profile(IDENTITY.endpoint_id))
    zero_effects(store, vault, request)
    # The ordinary execution resolver retains the original live credential gate.
    with pytest.raises(AssertionError, match='Vault.get'):
        resolver.select_governed_model(service._task_profile(request))
    assert vault.calls == 1


@pytest.mark.parametrize('field', ['connection_id', 'credential_revision', 'endpoint_revision'])
def test_missing_held_revision_is_blocked_without_vault(tmp_path: Path, field: str) -> None:
    service, _resolver, _management, vault, active, gateway, entry, request, store = case(tmp_path)
    active.install_transport(gateway, (replace(entry, **{field: None}),))
    with pytest.raises((ValueError, RuntimeError)):
        service.prepare_consumption_context(request, authority_source=Authority())
    zero_effects(store, vault, request)


def test_missing_binding_is_blocked_without_vault(tmp_path: Path) -> None:
    service, _resolver, _management, vault, _active, _gateway, _entry, request, store = case(tmp_path)
    service._gateway = ReloadableModelGateway()
    with pytest.raises((ValueError, RuntimeError)):
        service.prepare_consumption_context(request, authority_source=Authority())
    zero_effects(store, vault, request)


def test_current_endpoint_drift_is_blocked_without_vault(tmp_path: Path) -> None:
    service, _resolver, management, vault, _active, _gateway, _entry, request, store = case(tmp_path)
    endpoint = management.endpoint_profile(IDENTITY.endpoint_id)
    management.upsert_endpoint(endpoint.model_copy(update={'base_url': 'https://changed.example.test/v1'}))
    with pytest.raises((ValueError, RuntimeError)):
        service.prepare_consumption_context(request, authority_source=Authority())
    zero_effects(store, vault, request)


@pytest.mark.parametrize('reloadable', [False, True])
def test_binding_swap_during_preparation_is_blocked_without_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reloadable: bool) -> None:
    service, _resolver, _management, vault, active, gateway, entry, request, store = case(tmp_path, reloadable=reloadable)
    original = gateway.prepare_bound_transport
    def swap(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if reloadable:
            active.install_transport(gateway, (replace(entry),))
        else:
            gateway.install_transport_entries((replace(entry),))
        return result
    monkeypatch.setattr(gateway, 'prepare_bound_transport', swap)
    with pytest.raises((ValueError, RuntimeError)):
        service.prepare_consumption_context(request, authority_source=Authority())
    zero_effects(store, vault, request)

@pytest.mark.parametrize('field', ['connection_id', 'credential_revision', 'endpoint_revision'])
def test_mismatched_held_revision_is_blocked_without_vault(tmp_path: Path, field: str) -> None:
    service, _resolver, _management, vault, active, gateway, entry, request, store = case(tmp_path)
    active.install_transport(gateway, (replace(entry, **{field: 'stale-revision'}),))
    with pytest.raises((ValueError, RuntimeError)):
        service.prepare_consumption_context(request, authority_source=Authority())
    zero_effects(store, vault, request)


def test_duplicate_connection_metadata_is_blocked_without_vault(tmp_path: Path) -> None:
    _service, resolver, _management, vault, _active, _gateway, entry, request, store = case(tmp_path)
    with pytest.raises(RuntimeError):
        resolver._for_preparation((entry, replace(entry, endpoint_id='other-endpoint')))
    zero_effects(store, vault, request)


def test_reloadable_underlying_entry_drift_is_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _resolver, _management, vault, _active, gateway, entry, request, store = case(tmp_path)
    original = gateway.prepare_bound_transport
    def drift(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        gateway.install_transport_entries((replace(entry),))
        return result
    monkeypatch.setattr(gateway, 'prepare_bound_transport', drift)
    with pytest.raises(RuntimeError):
        service.prepare_consumption_context(request, authority_source=Authority())
    zero_effects(store, vault, request)
