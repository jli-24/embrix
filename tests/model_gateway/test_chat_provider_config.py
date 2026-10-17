from __future__ import annotations

import pytest

from embedded_copilot.model_gateway import (
    ModelProviderConfigurationError,
    ModelProviderSettings,
)


def test_chat_provider_has_priority_over_legacy_provider() -> None:
    settings = ModelProviderSettings.from_environment(
        {
            "CHAT_MODEL_PROVIDER": "deepseek",
            "MODEL_PROVIDER": "qwen",
            "DEEPSEEK_API_KEY": "deepseek-secret",
            "DEEPSEEK_MODEL": "deepseek-chat",
        }
    )

    assert settings.provider == "deepseek"
    assert settings.model_name == "deepseek-chat"


def test_legacy_provider_remains_compatible() -> None:
    settings = ModelProviderSettings.from_environment(
        {
            "MODEL_PROVIDER": "qwen",
            "QWEN_API_KEY": "qwen-secret",
            "QWEN_MODEL": "qwen-plus",
        }
    )

    assert settings.provider == "qwen"
    assert settings.model_name == "qwen-plus"


def test_missing_provider_uses_deepseek_default_then_fails_closed_without_credentials() -> None:
    with pytest.raises(ModelProviderConfigurationError) as raised:
        ModelProviderSettings.from_environment({})

    assert "secret" not in str(raised.value).casefold()
    assert "api" not in str(raised.value).casefold()


@pytest.mark.parametrize(
    "environment",
    (
        {"CHAT_MODEL_PROVIDER": "deepseek", "DEEPSEEK_MODEL": "deepseek-chat"},
        {"CHAT_MODEL_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "secret"},
        {"CHAT_MODEL_PROVIDER": "unsupported", "MODEL_NAME": "ignored"},
    ),
)
def test_chat_provider_configuration_failures_are_sanitized(environment: dict[str, str]) -> None:
    with pytest.raises(ModelProviderConfigurationError) as raised:
        ModelProviderSettings.from_environment(environment)

    message = str(raised.value).casefold()
    assert "secret" not in message
    assert "ignored" not in message


def test_chat_provider_does_not_use_generic_model_name() -> None:
    with pytest.raises(ModelProviderConfigurationError):
        ModelProviderSettings.from_environment(
            {
                "CHAT_MODEL_PROVIDER": "deepseek",
                "MODEL_NAME": "deepseek-chat",
                "DEEPSEEK_API_KEY": "secret",
            }
        )


def test_explicit_mapping_does_not_read_dotenv() -> None:
    with pytest.raises(ModelProviderConfigurationError):
        ModelProviderSettings.from_environment({})
