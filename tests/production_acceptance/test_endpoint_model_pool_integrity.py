from __future__ import annotations

from collections.abc import Mapping

from embedded_copilot.model_gateway import (
    EndpointProfile,
    EndpointType,
    ModelAvailability,
    ModelCatalogSource,
    ModelDescriptor,
    ModelGatewayManagementService,
    ProtocolFamily,
    ProviderProfile,
    RecordProvenance,
    descriptor_to_model_profile,
    provider_presets,
)
from embedded_copilot.production_release.model_pool import ProviderModelPool


def _management() -> ModelGatewayManagementService:
    management = ModelGatewayManagementService()
    for preset in provider_presets():
        management.add_provider(preset)
    management.add_provider(
        ProviderProfile(
            provider_id="custom-relay-x",
            display_name="Relay X",
            provider_kind="custom",
            supported_protocols=(ProtocolFamily.OPENAI_CHAT,),
            default_endpoint_template=None,
        )
    )
    return management


def _endpoint(
    provider_id: str,
    endpoint_id: str,
    *,
    base_url: str,
    credential_ref: str,
    endpoint_type: EndpointType = EndpointType.OFFICIAL,
) -> EndpointProfile:
    return EndpointProfile(
        endpoint_id=endpoint_id,
        provider_id=provider_id,
        display_name=f"{provider_id} {endpoint_id}",
        endpoint_type=endpoint_type,
        protocol_family=ProtocolFamily.OPENAI_CHAT,
        base_url=base_url,
        credential_ref=credential_ref,
    )


def _descriptor(
    endpoint: EndpointProfile,
    model_id: str,
    *,
    credential_revision: str | None,
    endpoint_revision: str | None,
    record_provenance: RecordProvenance = RecordProvenance.UNKNOWN,
    catalog_source: ModelCatalogSource = ModelCatalogSource.PROVIDER_API,
) -> ModelDescriptor:
    return ModelDescriptor(
        provider_id=endpoint.provider_id,
        endpoint_id=endpoint.endpoint_id,
        model_id=model_id,
        display_name=model_id,
        protocol_family=endpoint.protocol_family,
        availability=ModelAvailability.AVAILABLE,
        availability_source=ModelCatalogSource.PROVIDER_API,
        catalog_source=catalog_source,
        credential_revision=credential_revision,
        endpoint_revision=endpoint_revision,
        record_provenance=record_provenance,
    )


class _Discovery:
    def __init__(self, models: Mapping[str, tuple[str, ...]]) -> None:
        self.models = dict(models)

    def discover(self, endpoint: EndpointProfile) -> tuple[ModelDescriptor, ...]:
        return tuple(
            _descriptor(
                endpoint,
                model_id,
                credential_revision=None,
                endpoint_revision=None,
            )
            for model_id in self.models[endpoint.endpoint_id]
        )


def test_current_pool_excludes_legacy_reference_quarantine_and_unbound_records() -> None:
    management = _management()
    endpoint = _endpoint(
        "zhipu",
        "endpoint-zhipu",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        credential_ref="credential:zhipu",
    )
    management.add_endpoint(endpoint)
    pool = ProviderModelPool(
        management,
        discovery=_Discovery({endpoint.endpoint_id: ("glm-5.3", "glm-5.3-flash")}),
    )
    revision = "hmac-sha256:" + "a" * 64
    endpoint_revision = ProviderModelPool.endpoint_revision(endpoint)

    refreshed = pool.refresh(
        endpoint,
        credential_revision=revision,
        credential_configured=True,
    )
    assert [item.remote_model_id for item in refreshed.models] == ["glm-5.3", "glm-5.3-flash"]

    contamination = (
        ("legacy-glm-4.7", RecordProvenance.UNKNOWN, ModelCatalogSource.UNKNOWN, revision),
        ("reference-glm-5", RecordProvenance.OFFICIAL_CATALOG, ModelCatalogSource.EMBRIX_OFFICIAL_CATALOG, revision),
        ("quarantine-glm-4.5", RecordProvenance.PROVIDER_METADATA, ModelCatalogSource.UNKNOWN, revision),
        ("unbound-current", RecordProvenance.EXPLICIT_CONFIGURATION, ModelCatalogSource.PROVIDER_API, None),
    )
    for model_id, provenance, source, model_revision in contamination:
        management.models.upsert(
            descriptor_to_model_profile(
                _descriptor(
                    endpoint,
                    model_id,
                    credential_revision=model_revision,
                    endpoint_revision=endpoint_revision,
                    record_provenance=provenance,
                    catalog_source=source,
                )
            )
        )

    usable = pool.usable(endpoint, credential_revision=revision)
    assert [item.remote_model_id for item in usable] == ["glm-5.3", "glm-5.3-flash"]


def test_current_pool_accepts_only_current_discovery_and_bound_explicit_records() -> None:
    management = _management()
    endpoint = _endpoint(
        "deepseek",
        "endpoint-deepseek",
        base_url="https://api.deepseek.com/v1",
        credential_ref="credential:deepseek",
    )
    other_endpoint = _endpoint(
        "deepseek",
        "endpoint-deepseek-other",
        base_url="https://relay.example/v1",
        credential_ref="credential:deepseek-other",
    )
    management.add_endpoint(endpoint)
    management.add_endpoint(other_endpoint)
    pool = ProviderModelPool(
        management,
        discovery=_Discovery({endpoint.endpoint_id: ("deepseek-flash", "deepseek-v4-pro")}),
    )
    current_revision = "revision-current"
    endpoint_revision = ProviderModelPool.endpoint_revision(endpoint)
    pool.refresh(endpoint, credential_revision=current_revision, credential_configured=True)

    current = pool.usable(endpoint, credential_revision=current_revision)
    assert {item.remote_model_id for item in current} == {"deepseek-flash", "deepseek-v4-pro"}

    def profile(
        *,
        model_profile_id: str,
        model_id: str,
        endpoint_id: str,
        credential_revision: str | None,
        endpoint_revision: str | None,
        provenance: RecordProvenance,
    ):
        target = endpoint if endpoint_id == endpoint.endpoint_id else other_endpoint
        return descriptor_to_model_profile(
            _descriptor(
                target,
                model_id,
                credential_revision=credential_revision,
                endpoint_revision=endpoint_revision,
                record_provenance=provenance,
            )
        ).model_copy(update={"model_profile_id": model_profile_id})

    management.models.upsert(
        profile(
            model_profile_id="historical-deepseek-flash",
            model_id="deepseek-flash",
            endpoint_id=endpoint.endpoint_id,
            credential_revision="revision-old",
            endpoint_revision=endpoint_revision,
            provenance=RecordProvenance.ENDPOINT_DISCOVERY,
        )
    )
    management.models.upsert(
        profile(
            model_profile_id="other-endpoint-deepseek-flash",
            model_id="deepseek-flash",
            endpoint_id=other_endpoint.endpoint_id,
            credential_revision=current_revision,
            endpoint_revision=ProviderModelPool.endpoint_revision(other_endpoint),
            provenance=RecordProvenance.ENDPOINT_DISCOVERY,
        )
    )
    management.models.upsert(
        profile(
            model_profile_id="reference-deepseek-flash",
            model_id="deepseek-flash",
            endpoint_id=endpoint.endpoint_id,
            credential_revision=current_revision,
            endpoint_revision=endpoint_revision,
            provenance=RecordProvenance.OFFICIAL_CATALOG,
        )
    )
    management.models.upsert(
        profile(
            model_profile_id="explicit-current",
            model_id="explicit-current",
            endpoint_id=endpoint.endpoint_id,
            credential_revision=current_revision,
            endpoint_revision=endpoint_revision,
            provenance=RecordProvenance.EXPLICIT_CONFIGURATION,
        )
    )
    management.models.upsert(
        profile(
            model_profile_id="explicit-unbound",
            model_id="explicit-unbound",
            endpoint_id=endpoint.endpoint_id,
            credential_revision=None,
            endpoint_revision=None,
            provenance=RecordProvenance.EXPLICIT_CONFIGURATION,
        )
    )

    current_ids = {
        item.remote_model_id
        for item in pool.usable(endpoint, credential_revision=current_revision)
    }
    assert current_ids == {"deepseek-flash", "deepseek-v4-pro", "explicit-current"}
    assert {item.remote_model_id for item in management.list_models()} >= {
        "deepseek-flash",
        "deepseek-v4-pro",
        "explicit-current",
    }


def test_credential_revision_refresh_replaces_current_pool_but_retains_history() -> None:
    management = _management()
    endpoint = _endpoint(
        "zhipu",
        "endpoint-zhipu",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        credential_ref="credential:zhipu",
    )
    management.add_endpoint(endpoint)
    discovery = _Discovery({endpoint.endpoint_id: ("model-a", "model-b")})
    pool = ProviderModelPool(management, discovery=discovery)

    pool.refresh(endpoint, credential_revision="revision-1", credential_configured=True)
    discovery.models[endpoint.endpoint_id] = ("model-c",)
    pool.refresh(endpoint, credential_revision="revision-2", credential_configured=True)

    current = pool.usable(endpoint, credential_revision="revision-2")
    assert [item.remote_model_id for item in current] == ["model-c"]
    history = {item.remote_model_id for item in management.list_models()}
    assert {"model-a", "model-b", "model-c"} <= history
    assert all(item.credential_revision == "revision-2" for item in current)


def test_endpoint_and_provider_pools_are_isolated() -> None:
    management = _management()
    zhipu_a = _endpoint(
        "zhipu",
        "endpoint-zhipu-a",
        base_url="https://zhipu-a.example/v1",
        credential_ref="credential:zhipu-a",
    )
    zhipu_b = _endpoint(
        "zhipu",
        "endpoint-zhipu-b",
        base_url="https://zhipu-b.example/v1",
        credential_ref="credential:zhipu-b",
    )
    deepseek = _endpoint(
        "deepseek",
        "endpoint-deepseek",
        base_url="https://deepseek.example/v1",
        credential_ref="credential:deepseek",
    )
    for endpoint in (zhipu_a, zhipu_b, deepseek):
        management.add_endpoint(endpoint)
    pool = ProviderModelPool(
        management,
        discovery=_Discovery(
            {
                zhipu_a.endpoint_id: ("glm-a",),
                zhipu_b.endpoint_id: ("glm-b",),
                deepseek.endpoint_id: ("deepseek-chat",),
            }
        ),
    )
    for endpoint in (zhipu_a, zhipu_b, deepseek):
        pool.refresh(endpoint, credential_revision="revision-1", credential_configured=True)

    assert [item.remote_model_id for item in pool.usable(zhipu_a, credential_revision="revision-1")] == ["glm-a"]
    assert [item.remote_model_id for item in pool.usable(zhipu_b, credential_revision="revision-1")] == ["glm-b"]
    assert [item.remote_model_id for item in pool.usable(deepseek, credential_revision="revision-1")] == ["deepseek-chat"]


def test_relay_models_remain_in_relay_pool() -> None:
    management = _management()
    relay = _endpoint(
        "custom-relay-x",
        "endpoint-relay-x",
        base_url="https://relay.example/v1",
        credential_ref="credential:custom-relay-x",
        endpoint_type=EndpointType.RELAY,
    )
    official = _endpoint(
        "zhipu",
        "endpoint-zhipu",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        credential_ref="credential:zhipu",
    )
    management.add_endpoint(relay)
    management.add_endpoint(official)
    pool = ProviderModelPool(
        management,
        discovery=_Discovery(
            {
                relay.endpoint_id: ("claude-x", "glm-x", "gpt-x"),
                official.endpoint_id: ("glm-5.3",),
            }
        ),
    )
    pool.refresh(relay, credential_revision="relay-revision", credential_configured=True)
    pool.refresh(official, credential_revision="zhipu-revision", credential_configured=True)

    assert {item.remote_model_id for item in pool.usable(relay, credential_revision="relay-revision")} == {
        "claude-x",
        "glm-x",
        "gpt-x",
    }
    assert {item.remote_model_id for item in pool.usable(official, credential_revision="zhipu-revision")} == {"glm-5.3"}


def test_endpoint_revision_change_does_not_reuse_stale_cache() -> None:
    management = _management()
    first = _endpoint(
        "zhipu",
        "endpoint-zhipu",
        base_url="https://zhipu-a.example/v1",
        credential_ref="credential:zhipu",
    )
    second = first.model_copy(update={"base_url": "https://zhipu-b.example/v1"})
    management.add_endpoint(first)

    class _FailingDiscovery:
        calls = 0

        def discover(self, endpoint: EndpointProfile) -> tuple[ModelDescriptor, ...]:
            self.calls += 1
            if self.calls == 1:
                return (_descriptor(endpoint, "model-from-endpoint-a", credential_revision=None, endpoint_revision=None),)
            raise RuntimeError("transport failure")

    pool = ProviderModelPool(management, discovery=_FailingDiscovery())
    assert pool.refresh(first, credential_revision="revision-1", credential_configured=True).status == "UPDATED"
    failed = pool.refresh(second, credential_revision="revision-1", credential_configured=True)

    assert failed.status == "UNAVAILABLE"
    assert failed.models == ()


def test_credential_ref_change_isolated_for_same_provider_and_endpoint_id() -> None:
    management = _management()
    credential_a = _endpoint(
        "zhipu",
        "endpoint-zhipu",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        credential_ref="credential:zhipu-a",
    )
    credential_b = credential_a.model_copy(update={"credential_ref": "credential:zhipu-b"})
    management.add_endpoint(credential_a)

    class _DiscoveryByCredential:
        def discover(self, endpoint: EndpointProfile) -> tuple[ModelDescriptor, ...]:
            model_id = "model-from-credential-a" if endpoint.credential_ref.endswith("-a") else "model-from-credential-b"
            return (_descriptor(endpoint, model_id, credential_revision=None, endpoint_revision=None),)

    pool = ProviderModelPool(management, discovery=_DiscoveryByCredential())
    pool.refresh(credential_a, credential_revision="revision-1", credential_configured=True)
    pool.refresh(credential_b, credential_revision="revision-1", credential_configured=True)

    assert pool.usable(credential_a, credential_revision="revision-1") == ()
    assert [item.remote_model_id for item in pool.usable(credential_b, credential_revision="revision-1")] == [
        "model-from-credential-b"
    ]
