from __future__ import annotations

from embedded_copilot.model_gateway import (
    BillingClass,
    BillingProvenance,
    EndpointProfile,
    EndpointType,
    ModelAvailability,
    ModelCatalogSource,
    ModelDescriptor,
    ModelGatewayManagementService,
    ProtocolFamily,
    descriptor_to_model_profile,
    get_usable_models,
    merge_model_metadata,
    official_model_catalog,
    official_model_metadata,
    provider_presets,
)
from embedded_copilot.production_release.model_pool import ProviderModelPool


def _management() -> tuple[ModelGatewayManagementService, EndpointProfile]:
    management = ModelGatewayManagementService()
    for preset in provider_presets():
        management.add_provider(preset)
    endpoint = EndpointProfile(
        endpoint_id="endpoint-zhipu",
        provider_id="zhipu",
        display_name="Zhipu",
        endpoint_type=EndpointType.OFFICIAL,
        protocol_family=ProtocolFamily.OPENAI_CHAT,
        base_url="https://open.bigmodel.cn/api/paas/v4",
        credential_ref="credential:zhipu",
    )
    management.add_endpoint(endpoint)
    return management, endpoint


def _descriptor(model_id: str, *, availability: ModelAvailability = ModelAvailability.AVAILABLE) -> ModelDescriptor:
    return ModelDescriptor(
        provider_id="zhipu",
        endpoint_id="endpoint-zhipu",
        model_id=model_id,
        display_name=model_id,
        protocol_family=ProtocolFamily.OPENAI_CHAT,
        availability=availability,
        availability_source=ModelCatalogSource.PROVIDER_API,
        catalog_source=ModelCatalogSource.PROVIDER_API,
        credential_revision="credential-revision-1",
    )


def test_zhipu_catalog_is_source_linked_and_does_not_invent_screenshot_models() -> None:
    catalog = {item.remote_model_id: item for item in official_model_catalog("zhipu", "endpoint-zhipu")}

    assert catalog["glm-4.7-flash"].billing_class is BillingClass.FREE
    assert catalog["glm-4.7-flash"].billing_provenance is BillingProvenance.PROVIDER_METADATA
    assert catalog["glm-4.7-flash"].billing_source == "ZHIPU_OFFICIAL_GLM_4_7_FLASH"
    assert catalog["glm-4.7-flash"].metadata_source == "ZHIPU_OFFICIAL_GLM_4_7_FLASH"
    assert catalog["glm-4.7"].billing_class is BillingClass.PAID
    assert catalog["glm-4.6"].billing_class is BillingClass.UNKNOWN
    assert "glm-5.3" not in catalog
    assert "glm-5.3-flash" not in catalog


def test_discovery_preserves_canonical_ids_and_merges_billing_metadata() -> None:
    management, endpoint = _management()

    class _Discovery:
        def discover(self, requested: EndpointProfile):
            assert requested.endpoint_id == endpoint.endpoint_id
            return tuple(
                _descriptor(model_id)
                for model_id in (
                    "glm-4.7-flash",
                    "glm-4.7",
                    "glm-4.6",
                    "glm-5.3",
                    "totally-free-looking-flash",
                )
            )

    result = ProviderModelPool(management, discovery=_Discovery()).refresh(
        endpoint,
        credential_revision="credential-revision-1",
        credential_configured=True,
    )

    assert result.status == "UPDATED"
    models = {item.remote_model_id: item for item in result.models}
    assert set(models) == {
        "glm-4.7-flash",
        "glm-4.7",
        "glm-4.6",
        "glm-5.3",
        "totally-free-looking-flash",
    }
    assert models["glm-4.7-flash"].billing_class is BillingClass.FREE
    assert models["glm-4.7"].billing_class is BillingClass.PAID
    assert models["glm-4.6"].billing_class is BillingClass.UNKNOWN
    assert models["glm-5.3"].billing_class is BillingClass.UNKNOWN
    assert models["totally-free-looking-flash"].billing_class is BillingClass.UNKNOWN
    assert models["totally-free-looking-flash"].billing_provenance is BillingProvenance.UNKNOWN
    assert all(item.catalog_source is ModelCatalogSource.PROVIDER_API for item in models.values())


def test_deprecated_free_model_is_hidden_while_rate_limited_free_model_remains_visible() -> None:
    deprecated = merge_model_metadata(_descriptor("glm-4.5-flash"), official_model_metadata("zhipu", "glm-4.5-flash"))
    assert deprecated.billing_class is BillingClass.FREE
    assert deprecated.deprecated is True
    assert deprecated.availability is ModelAvailability.DEPRECATED
    assert get_usable_models(
        (deprecated,),
        provider_id="zhipu",
        endpoint_id="endpoint-zhipu",
        credential_revision="credential-revision-1",
    ) == ()

    rate_limited = descriptor_to_model_profile(
        _descriptor("glm-4.7-flash", availability=ModelAvailability.RATE_LIMITED)
    )
    rate_limited = merge_model_metadata(rate_limited, official_model_metadata("zhipu", "glm-4.7-flash"))
    usable = get_usable_models(
        (rate_limited,),
        provider_id="zhipu",
        endpoint_id="endpoint-zhipu",
        credential_revision="credential-revision-1",
    )
    assert [item.remote_model_id for item in usable] == ["glm-4.7-flash"]
    assert usable[0].billing_class is BillingClass.FREE


def test_permission_denied_free_model_is_not_visible_and_billing_stays_separate() -> None:
    denied = merge_model_metadata(
        _descriptor("glm-4.7-flash", availability=ModelAvailability.PERMISSION_DENIED),
        official_model_metadata("zhipu", "glm-4.7-flash"),
    )
    assert denied.billing_class is BillingClass.FREE
    assert denied.availability is ModelAvailability.PERMISSION_DENIED
    assert get_usable_models(
        (denied,),
        provider_id="zhipu",
        endpoint_id="endpoint-zhipu",
        credential_revision="credential-revision-1",
    ) == ()


def test_explicit_provider_billing_metadata_has_priority_over_unknown_name() -> None:
    descriptor = _descriptor("totally-free-looking-flash")
    explicit = descriptor.model_copy(
        update={
            "billing_class": BillingClass.PAID,
            "billing_provenance": BillingProvenance.VERIFIED,
            "billing_source": "provider_verified_metadata",
        }
    )
    merged = merge_model_metadata(explicit, None)
    assert merged.billing_class is BillingClass.PAID
    assert merged.billing_provenance is BillingProvenance.VERIFIED
    assert merged.billing_source == "provider_verified_metadata"
