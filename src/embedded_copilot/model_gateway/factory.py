from __future__ import annotations

from collections.abc import Mapping

from .adapters import (
    DeepSeekProviderAdapter,
    LocalModelAdapter,
    OpenAIProviderAdapter,
    QwenProviderAdapter,
)
from .adapters.protocol_http import ProtocolHTTPProviderAdapter
from .config import ModelProviderSettings
from .ports import ModelProviderPort


def create_model_provider(
    settings: ModelProviderSettings | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    http_transport: object | None = None,
    async_transport: object | None = None,
) -> ModelProviderPort:
    """Construct exactly one explicitly configured provider adapter."""

    checked = settings or ModelProviderSettings.from_environment(environment)
    common = {
        "timeout_seconds": checked.timeout_seconds,
        "supports_image_input": checked.supports_image_input,
        "http_transport": http_transport,
        "async_transport": async_transport,
    }
    if checked.provider == "openai":
        return OpenAIProviderAdapter(api_key=checked.api_key, **common)
    if checked.provider == "deepseek":
        return DeepSeekProviderAdapter(api_key=checked.api_key, **common)
    if checked.provider == "qwen":
        return QwenProviderAdapter(api_key=checked.api_key, **common)
    if checked.protocol_family is None or checked.base_url is None:
        return LocalModelAdapter()
    return ProtocolHTTPProviderAdapter(
        provider=checked.provider,
        base_url=checked.base_url,
        protocol_family=checked.protocol_family,
        api_key=checked.api_key,
        **common,
    )


__all__ = ("create_model_provider",)
