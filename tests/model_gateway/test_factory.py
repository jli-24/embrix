from __future__ import annotations

import pytest
from pydantic import SecretStr

from embedded_copilot.model_gateway import (
    DeepSeekProviderAdapter,
    ModelProviderConfigurationError,
    ModelProviderSettings,
    OpenAIProviderAdapter,
    QwenProviderAdapter,
    create_model_provider,
)


@pytest.mark.parametrize(
    ("provider", "key", "model", "adapter"),
    (
        ("openai", "OPENAI_API_KEY", "OPENAI_MODEL", OpenAIProviderAdapter),
        ("deepseek", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", DeepSeekProviderAdapter),
        ("qwen", "QWEN_API_KEY", "QWEN_MODEL", QwenProviderAdapter),
    ),
)
def test_factory_uses_selected_provider_settings(provider, key, model, adapter) -> None:
    settings = ModelProviderSettings.from_environment(
        {"MODEL_PROVIDER": provider, key: "test-key", model: "test-model"}
    )
    assert isinstance(create_model_provider(settings=settings), adapter)
    policy = settings.to_model_policy()
    assert (policy.primary_provider, policy.model_name) == (provider, "test-model")


def test_factory_rejects_missing_or_invalid_configuration() -> None:
    with pytest.raises(ModelProviderConfigurationError):
        ModelProviderSettings.from_environment({})
    with pytest.raises(ModelProviderConfigurationError):
        ModelProviderSettings.from_environment({"MODEL_PROVIDER": "openai"})


def test_remote_plain_http_endpoint_is_rejected() -> None:
    with pytest.raises(ModelProviderConfigurationError):
        ModelProviderSettings(
            provider="custom",
            model_name="custom-model",
            api_key=SecretStr("test-key"),
            base_url="http://relay.example/v1",
        )
