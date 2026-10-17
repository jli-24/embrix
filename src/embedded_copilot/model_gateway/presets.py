"""Conservative provider presets; secrets and discovered model claims stay external."""

from __future__ import annotations

from datetime import UTC, datetime

from .profiles import (
    BillingClass,
    BillingProvenance,
    DiscoveryMode,
    ModelAvailability,
    ModelCapability,
    ModelCatalogSource,
    ModelMetadata,
    ModelProfile,
    ProtocolFamily,
    ProviderKind,
    ProviderProfile,
    RecordProvenance,
)

_ZHIPU_PRICING_SOURCE = "ZHIPU_OFFICIAL_PRICING"
_ZHIPU_MODEL_OVERVIEW_SOURCE = "ZHIPU_OFFICIAL_MODEL_OVERVIEW"
_ZHIPU_GLM_47_FLASH_SOURCE = "ZHIPU_OFFICIAL_GLM_4_7_FLASH"
_ZHIPU_GLM_45_FLASH_SOURCE = "ZHIPU_OFFICIAL_GLM_4_5_FLASH"
_CONTROLLED_METADATA_UPDATED_AT = datetime(2026, 9, 12, tzinfo=UTC)

OFFICIAL_METADATA_SOURCE_URIS: dict[str, str] = {
    _ZHIPU_PRICING_SOURCE: "https://bigmodel.cn/pricing",
    _ZHIPU_MODEL_OVERVIEW_SOURCE: "https://docs.bigmodel.cn/cn/guide/start/model-overview",
    _ZHIPU_GLM_47_FLASH_SOURCE: "https://docs.bigmodel.cn/cn/guide/models/free/glm-4.7-flash",
    _ZHIPU_GLM_45_FLASH_SOURCE: "https://docs.bigmodel.cn/cn/guide/models/free/glm-4.5-flash",
}


def _zhipu_metadata() -> dict[str, ModelMetadata]:
    """Controlled, source-linked snapshot used by the canonical catalog."""

    def item(
        model_id: str,
        display_name: str,
        *,
        billing: BillingClass = BillingClass.UNKNOWN,
        billing_source: str | None = None,
        metadata_source: str = _ZHIPU_MODEL_OVERVIEW_SOURCE,
        lifecycle_status: str = "ACTIVE",
        deprecated: bool = False,
    ) -> ModelMetadata:
        return ModelMetadata(
            provider_id="zhipu",
            model_id=model_id,
            display_name=display_name,
            billing_class=billing,
            billing_provenance=BillingProvenance.PROVIDER_METADATA,
            billing_source=billing_source,
            lifecycle_status=lifecycle_status,
            deprecated=deprecated,
            metadata_updated_at=_CONTROLLED_METADATA_UPDATED_AT,
            metadata_source=metadata_source,
        )

    return {
        "glm-4.5": item("glm-4.5", "GLM 4.5", billing=BillingClass.PAID, billing_source=_ZHIPU_PRICING_SOURCE),
        "glm-4.5-air": item("glm-4.5-air", "GLM 4.5 Air", billing=BillingClass.PAID, billing_source=_ZHIPU_PRICING_SOURCE),
        "glm-4.5-flash": item(
            "glm-4.5-flash", "GLM 4.5 Flash", billing=BillingClass.FREE,
            billing_source=_ZHIPU_GLM_45_FLASH_SOURCE, metadata_source=_ZHIPU_GLM_45_FLASH_SOURCE,
            lifecycle_status="DEPRECATED", deprecated=True,
        ),
        "glm-4.6": item("glm-4.6", "GLM 4.6"),
        "glm-4.7": item("glm-4.7", "GLM 4.7", billing=BillingClass.PAID, billing_source=_ZHIPU_PRICING_SOURCE),
        "glm-4.7-flash": item(
            "glm-4.7-flash", "GLM 4.7 Flash", billing=BillingClass.FREE,
            billing_source=_ZHIPU_GLM_47_FLASH_SOURCE, metadata_source=_ZHIPU_GLM_47_FLASH_SOURCE,
        ),
        "glm-4-plus": item("glm-4-plus", "GLM 4 Plus", billing=BillingClass.PAID, billing_source=_ZHIPU_PRICING_SOURCE),
        "glm-4": item("glm-4", "GLM 4", billing=BillingClass.PAID, billing_source=_ZHIPU_PRICING_SOURCE),
        "glm-5": item("glm-5", "GLM 5", billing=BillingClass.PAID, billing_source=_ZHIPU_PRICING_SOURCE),
        "glm-5-turbo": item("glm-5-turbo", "GLM 5 Turbo", billing=BillingClass.PAID, billing_source=_ZHIPU_PRICING_SOURCE),
        "glm-5.1": item("glm-5.1", "GLM 5.1", billing=BillingClass.PAID, billing_source=_ZHIPU_PRICING_SOURCE),
        "glm-5.2": item("glm-5.2", "GLM 5.2", billing=BillingClass.PAID, billing_source=_ZHIPU_PRICING_SOURCE),
    }


def provider_presets() -> tuple[ProviderProfile, ...]:
    """Return the v1 catalog with partial metadata where provider details vary."""

    def profile(
        provider_id: str,
        display_name: str,
        kind: ProviderKind,
        protocols: tuple[ProtocolFamily | str, ...],
        endpoint: str | None,
        *,
        discovery: DiscoveryMode = DiscoveryMode.AUTO_DISCOVERY,
        status: str = "PRESET_PARTIAL",
    ) -> ProviderProfile:
        return ProviderProfile(
            provider_id=provider_id,
            display_name=display_name,
            provider_kind=kind,
            supported_protocols=protocols,
            usage_capabilities=(),
            preset_status=status,
            default_endpoint_template=endpoint,
            model_discovery_mode=discovery,
        )

    return (
        profile("openai", "OpenAI", ProviderKind.OPENAI, (ProtocolFamily.OPENAI_RESPONSES, ProtocolFamily.OPENAI_CHAT), "https://api.openai.com/v1"),
        profile("anthropic", "Anthropic", ProviderKind.ANTHROPIC, (ProtocolFamily.ANTHROPIC_MESSAGES,), "https://api.anthropic.com/v1"),
        profile("google", "Google", ProviderKind.GOOGLE, (ProtocolFamily.GOOGLE_GENAI,), "https://generativelanguage.googleapis.com/v1beta"),
        profile("xai", "xAI", ProviderKind.XAI, (ProtocolFamily.OPENAI_CHAT,), "https://api.x.ai/v1"),
        profile("deepseek", "DeepSeek", ProviderKind.DEEPSEEK, (ProtocolFamily.OPENAI_CHAT,), "https://api.deepseek.com/v1"),
        profile("zhipu", "智谱 AI", ProviderKind.ZHIPU, (ProtocolFamily.OPENAI_CHAT,), "https://open.bigmodel.cn/api/paas/v4"),
        profile("alibaba", "阿里云百炼", ProviderKind.ALIBABA, (ProtocolFamily.OPENAI_CHAT,), "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        profile("moonshot", "月之暗面", ProviderKind.MOONSHOT, (ProtocolFamily.OPENAI_CHAT,), "https://api.moonshot.cn/v1"),
        profile("minimax", "MiniMax", ProviderKind.MINIMAX, (ProtocolFamily.OPENAI_CHAT,), None),
        profile(
            "openai-compatible",
            "OpenAI Compatible",
            ProviderKind.CUSTOM,
            (ProtocolFamily.OPENAI_CHAT,),
            None,
            discovery=DiscoveryMode.MANUAL_MODEL_ID,
        ),
        profile(
            "anthropic-compatible",
            "Anthropic Compatible",
            ProviderKind.CUSTOM,
            (ProtocolFamily.ANTHROPIC_MESSAGES,),
            None,
            discovery=DiscoveryMode.MANUAL_MODEL_ID,
        ),
        profile("custom", "Custom Provider", ProviderKind.CUSTOM, (ProtocolFamily.CUSTOM,), None, discovery=DiscoveryMode.MANUAL_MODEL_ID),
        profile("local", "Local Endpoint", ProviderKind.LOCAL, (ProtocolFamily.LOCAL_OPENAI_COMPATIBLE,), None, discovery=DiscoveryMode.MANUAL_MODEL_ID),
    )


def provider_template_presets() -> tuple[ProviderProfile, ...]:
    """Return only templates that are valid entry points for the add flow.

    The legacy compatible identities remain readable by the gateway for
    backwards compatibility, but they are protocol adapters rather than
    provider templates and must never be rendered as provider cards.
    """

    custom_relay = ProviderProfile(
        provider_id="custom-relay",
        display_name="自定义 API / 中转站",
        provider_kind=ProviderKind.CUSTOM,
        supported_protocols=(ProtocolFamily.OPENAI_CHAT,),
        usage_capabilities=(),
        preset_status="PRESET_PARTIAL",
        default_endpoint_template=None,
        model_discovery_mode=DiscoveryMode.AUTO_DISCOVERY,
    )
    return tuple(
        item
        for item in provider_presets()
        if item.provider_id not in {"openai-compatible", "anthropic-compatible", "custom"}
    ) + (custom_relay,)


def manual_model_profile(
    *,
    model_profile_id: str,
    provider_id: str,
    endpoint_id: str,
    remote_model_id: str,
    display_name: str,
    capabilities: tuple[ModelCapability, ...] = (),
) -> object:
    """Create a manual model identity with explicitly unverified status."""

    from .profiles import DiscoveryStatus, ModelProfile, VerificationStatus

    return ModelProfile(
        model_profile_id=model_profile_id,
        provider_id=provider_id,
        endpoint_id=endpoint_id,
        remote_model_id=remote_model_id,
        display_name=display_name,
        advertised_family="UNKNOWN",
        input_modalities=capabilities,
        output_modalities=capabilities,
        capabilities=capabilities,
        discovery_status=DiscoveryStatus.MANUAL,
        verification_status=VerificationStatus.UNVERIFIED,
        record_provenance=RecordProvenance.EXPLICIT_CONFIGURATION,
    )


def official_model_catalog(provider_id: str, endpoint_id: str) -> tuple[ModelProfile, ...]:
    """Return the controlled Embrix catalog for one provider scope.

    This is intentionally a provider-keyed catalog, not one cross-provider list.
    Entries are catalog knowledge only until a provider API or verification
    receipt confirms availability for the current credential and endpoint.
    """

    catalog: dict[str, tuple[tuple[str, str], ...]] = {
        "openai": (("gpt-4o", "GPT-4o"), ("gpt-4o-mini", "GPT-4o mini")),
        "anthropic": (("claude-3-5-haiku-latest", "Claude 3.5 Haiku"), ("claude-3-5-sonnet-latest", "Claude 3.5 Sonnet")),
        "google": (("gemini-2.5-flash", "Gemini 2.5 Flash"), ("gemini-2.5-pro", "Gemini 2.5 Pro")),
        "xai": (("grok-3-mini", "Grok 3 Mini"),),
        "deepseek": (("deepseek-chat", "DeepSeek Chat"), ("deepseek-reasoner", "DeepSeek Reasoner")),
        "zhipu": tuple(
            (model_id, metadata.display_name)
            for model_id, metadata in _zhipu_metadata().items()
        ),
        "alibaba": (("qwen-plus", "Qwen Plus"), ("qwen-max", "Qwen Max")),
        "moonshot": (("moonshot-v1-8k", "Moonshot v1 8K"),),
        "minimax": (("MiniMax-Text-01", "MiniMax Text 01"),),
    }
    values = catalog.get(provider_id, ())
    metadata_by_id = _zhipu_metadata() if provider_id == "zhipu" else {}
    endpoint_suffix = "" if endpoint_id == f"endpoint-{provider_id}" else f"-{endpoint_id}"
    return tuple(
        ModelProfile(
            model_profile_id=f"model-{provider_id}{endpoint_suffix}-{model_id}"[:128],
            provider_id=provider_id,
            endpoint_id=endpoint_id,
            remote_model_id=model_id,
            display_name=display_name,
            advertised_family=provider_id,
            capabilities=metadata_by_id.get(model_id).capabilities if model_id in metadata_by_id else (),
            capability_sources=(),
            quality_source="PROVIDER_PRESET",
            enabled=True,
            discovery_status="UNKNOWN",
            availability=ModelAvailability.UNVERIFIED,
            availability_source=ModelCatalogSource.EMBRIX_OFFICIAL_CATALOG,
            catalog_source=ModelCatalogSource.EMBRIX_OFFICIAL_CATALOG,
            billing_class=(metadata_by_id.get(model_id).billing_class if model_id in metadata_by_id else BillingClass.UNKNOWN),
            billing_provenance=(metadata_by_id.get(model_id).billing_provenance if model_id in metadata_by_id else BillingProvenance.UNKNOWN),
            billing_source=(metadata_by_id.get(model_id).billing_source if model_id in metadata_by_id else None),
            lifecycle_status=(metadata_by_id.get(model_id).lifecycle_status if model_id in metadata_by_id else "ACTIVE"),
            deprecated=(metadata_by_id.get(model_id).deprecated if model_id in metadata_by_id else False),
            metadata_updated_at=(metadata_by_id.get(model_id).metadata_updated_at if model_id in metadata_by_id else None),
            metadata_source=(metadata_by_id.get(model_id).metadata_source if model_id in metadata_by_id else None),
            verification_status="UNVERIFIED",
            record_provenance=RecordProvenance.OFFICIAL_CATALOG,
        )
        for model_id, display_name in values
    )


def official_model_metadata(provider_id: str, model_id: str) -> ModelMetadata | None:
    """Return one source-linked metadata snapshot entry, if controlled."""

    if provider_id != "zhipu":
        return None
    return _zhipu_metadata().get(model_id)


__all__ = (
    "OFFICIAL_METADATA_SOURCE_URIS",
    "manual_model_profile",
    "official_model_catalog",
    "official_model_metadata",
    "provider_presets",
    "provider_template_presets",
)
