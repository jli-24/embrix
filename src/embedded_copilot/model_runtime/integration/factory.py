from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from embedded_copilot.model_gateway import (
    DeepSeekProviderAdapter,
    ModelGatewayPort,
    ModelGatewayService,
    ModelPolicy,
    ModelProviderPort,
    OpenAIProviderAdapter,
    ProtocolFamily,
    ProtocolHTTPProviderAdapter,
    QwenProviderAdapter,
)

from .adapters import LocalOpenAICompatibleProviderAdapter
from .retry import RetryingModelProviderAdapter
from .settings import ProductionModelSettings, ProductionProvider

_EXTENDED_PROTOCOLS = {
    ProductionProvider.ANTHROPIC: ProtocolFamily.ANTHROPIC_MESSAGES,
    ProductionProvider.GOOGLE: ProtocolFamily.GOOGLE_GENAI,
    ProductionProvider.XAI: ProtocolFamily.OPENAI_CHAT,
    ProductionProvider.ZHIPU: ProtocolFamily.OPENAI_CHAT,
    ProductionProvider.ALIBABA: ProtocolFamily.OPENAI_CHAT,
    ProductionProvider.MOONSHOT: ProtocolFamily.OPENAI_CHAT,
    ProductionProvider.MINIMAX: ProtocolFamily.OPENAI_CHAT,
}


@dataclass(frozen=True, slots=True)
class ProductionModelBinding:
    """Credential-free composition result for Application dependency injection."""

    gateway: ModelGatewayPort
    model_policy: ModelPolicy
    provider: ProductionProvider

    def __post_init__(self) -> None:
        if not isinstance(self.gateway, ModelGatewayPort):
            raise TypeError("model gateway port is invalid")
        if type(self.provider) is not ProductionProvider:
            raise TypeError("production provider identity is invalid")
        checked = ModelPolicy.model_validate(
            copy.deepcopy(self.model_policy.model_dump(mode="python"))
        )
        object.__setattr__(self, "model_policy", checked)


def _settings(value: ProductionModelSettings) -> ProductionModelSettings:
    if type(value) is not ProductionModelSettings:
        raise TypeError("production model settings are invalid")
    return ProductionModelSettings.model_validate(
        copy.deepcopy(value.model_dump(mode="python"))
    )


def create_production_provider(
    settings: ProductionModelSettings,
    *,
    http_transport: httpx.BaseTransport | None = None,
    async_transport: httpx.AsyncBaseTransport | None = None,
    sleep: Callable[[float], None] | None = None,
    asleep: Callable[[float], Awaitable[None]] | None = None,
) -> ModelProviderPort:
    checked = _settings(settings)
    common = {
        "timeout_seconds": checked.timeout_seconds,
        "supports_image_input": checked.supports_image_input,
        "http_transport": http_transport,
        "async_transport": async_transport,
    }
    if checked.provider is ProductionProvider.OPENAI:
        base: ModelProviderPort = OpenAIProviderAdapter(
            api_key=checked.api_key, **common
        )
    elif checked.provider is ProductionProvider.DEEPSEEK:
        base = DeepSeekProviderAdapter(api_key=checked.api_key, **common)
    elif checked.provider is ProductionProvider.QWEN:
        base = QwenProviderAdapter(api_key=checked.api_key, **common)
    elif checked.provider in _EXTENDED_PROTOCOLS:
        if checked.base_url is None:
            raise ValueError("provider endpoint is invalid")
        base = ProtocolHTTPProviderAdapter(
            provider=checked.provider.value,
            endpoint_id=f"endpoint-{checked.provider.value}",
            base_url=checked.base_url,
            protocol_family=_EXTENDED_PROTOCOLS[checked.provider],
            api_key=checked.api_key,
            timeout_seconds=checked.timeout_seconds,
            http_transport=http_transport,
            async_transport=async_transport,
        )
    else:
        if checked.base_url is None:
            raise ValueError("local provider endpoint is invalid")
        base = LocalOpenAICompatibleProviderAdapter(
            provider=checked.provider,
            base_url=checked.base_url,
            api_key=checked.api_key,
            **common,
        )
    return RetryingModelProviderAdapter(
        provider=base,
        max_retries=checked.max_retries,
        retry_backoff_seconds=checked.retry_backoff_seconds,
        sleep=sleep,
        asleep=asleep,
    )


def create_production_model_binding(
    settings: ProductionModelSettings,
    *,
    http_transport: httpx.BaseTransport | None = None,
    async_transport: httpx.AsyncBaseTransport | None = None,
    sleep: Callable[[float], None] | None = None,
    asleep: Callable[[float], Awaitable[None]] | None = None,
) -> ProductionModelBinding:
    checked = _settings(settings)
    provider = create_production_provider(
        checked,
        http_transport=http_transport,
        async_transport=async_transport,
        sleep=sleep,
        asleep=asleep,
    )
    return ProductionModelBinding(
        gateway=ModelGatewayService(providers={checked.provider.value: provider}),
        model_policy=checked.to_model_policy(),
        provider=checked.provider,
    )


__all__ = (
    "ProductionModelBinding",
    "create_production_model_binding",
    "create_production_provider",
)
