from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from embedded_copilot.model_gateway import ModelProviderConfigurationError
from embedded_copilot.model_runtime.integration import (
    ProductionModelSettings,
    ProductionProvider,
)


@pytest.mark.parametrize(
    ("provider", "key_name", "model_name"),
    (
        ("openai", "OPENAI_API_KEY", "gpt-4o-mini"),
        ("deepseek", "DEEPSEEK_API_KEY", "deepseek-chat"),
        ("qwen", "QWEN_API_KEY", "qwen-plus"),
    ),
)
def test_cloud_settings_are_explicit_and_keep_credentials_secret(
    provider: str, key_name: str, model_name: str
) -> None:
    settings = ProductionModelSettings.from_mapping(
        {
            "MODEL_PROVIDER": provider,
            "MODEL_NAME": model_name,
            key_name: "production-secret",
            "MODEL_TIMEOUT_SECONDS": "8.5",
            "MODEL_MAX_RETRIES": "2",
            "MODEL_RETRY_BACKOFF_SECONDS": "0.25",
        }
    )
    assert settings.provider.value == provider
    assert settings.model_name == model_name
    assert isinstance(settings.api_key, SecretStr)
    assert "production-secret" not in repr(settings)
    assert settings.timeout_seconds == 8.5
    assert settings.max_retries == 2
    assert settings.retry_backoff_seconds == 0.25


@pytest.mark.parametrize(
    ("provider", "key_name", "model_name"),
    (
        ("anthropic", "ANTHROPIC_API_KEY", "claude-test"),
        ("google", "GOOGLE_API_KEY", "gemini-test"),
        ("xai", "XAI_API_KEY", "grok-test"),
        ("zhipu", "ZHIPU_API_KEY", "glm-test"),
        ("alibaba", "ALIBABA_API_KEY", "qwen-test"),
        ("moonshot", "MOONSHOT_API_KEY", "kimi-test"),
        ("minimax", "MINIMAX_API_KEY", "minimax-test"),
    ),
)
def test_extended_cloud_settings_are_supported_without_exposing_credentials(
    provider: str, key_name: str, model_name: str
) -> None:
    settings = ProductionModelSettings.from_mapping(
        {
            "MODEL_PROVIDER": provider,
            "MODEL_NAME": model_name,
            key_name: "production-secret",
            **(
                {"MINIMAX_BASE_URL": "https://api.minimax.example/v1"}
                if provider == "minimax"
                else {}
            ),
        }
    )
    assert settings.provider is ProductionProvider(provider)
    assert settings.model_name == model_name
    assert isinstance(settings.api_key, SecretStr)
    assert "production-secret" not in repr(settings)


def test_settings_do_not_implicitly_read_process_environment(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-read")
    monkeypatch.setenv("MODEL_NAME", "gpt-test")
    with pytest.raises(ModelProviderConfigurationError):
        ProductionModelSettings.from_mapping({})


def test_ollama_is_limited_to_credential_free_loopback() -> None:
    settings = ProductionModelSettings.from_mapping(
        {
            "MODEL_PROVIDER": "ollama",
            "MODEL_NAME": "qwen2.5:7b",
            "OLLAMA_BASE_URL": "http://127.0.0.1:11434/v1",
        }
    )
    assert settings.provider is ProductionProvider.OLLAMA
    assert settings.api_key is None
    assert settings.base_url == "http://127.0.0.1:11434/v1"
    for invalid in (
        {"OLLAMA_BASE_URL": "https://models.example/v1"},
        {"OLLAMA_API_KEY": "forbidden"},
    ):
        with pytest.raises(ModelProviderConfigurationError):
            ProductionModelSettings.from_mapping(
                {
                    "MODEL_PROVIDER": "ollama",
                    "MODEL_NAME": "qwen2.5:7b",
                    **invalid,
                }
            )


def test_vllm_accepts_loopback_or_authenticated_https_only() -> None:
    loopback = ProductionModelSettings.from_mapping(
        {
            "MODEL_PROVIDER": "vllm",
            "MODEL_NAME": "Qwen2.5-7B-Instruct",
            "VLLM_BASE_URL": "http://localhost:8000/v1",
        }
    )
    remote = ProductionModelSettings.from_mapping(
        {
            "MODEL_PROVIDER": "vllm",
            "MODEL_NAME": "Qwen2.5-7B-Instruct",
            "VLLM_BASE_URL": "https://models.example/v1",
            "VLLM_API_KEY": "production-secret",
        }
    )
    assert loopback.api_key is None
    assert isinstance(remote.api_key, SecretStr)
    for environment in (
        {
            "MODEL_PROVIDER": "vllm",
            "MODEL_NAME": "model",
            "VLLM_BASE_URL": "http://10.0.0.8:8000/v1",
        },
        {
            "MODEL_PROVIDER": "vllm",
            "MODEL_NAME": "model",
            "VLLM_BASE_URL": "https://models.example/v1",
        },
    ):
        with pytest.raises(ModelProviderConfigurationError):
            ProductionModelSettings.from_mapping(environment)


def test_settings_are_strict_frozen_and_extra_forbidden() -> None:
    settings = ProductionModelSettings(
        provider=ProductionProvider.OPENAI,
        model_name="gpt-test",
        api_key=SecretStr("secret"),
        base_url=None,
        timeout_seconds=30.0,
        max_retries=1,
        retry_backoff_seconds=0.0,
    )
    with pytest.raises(ValidationError):
        settings.model_name = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ProductionModelSettings.model_validate(
            {
                **settings.model_dump(mode="python"),
                "unknown": "value",
            }
        )
    with pytest.raises(ValidationError):
        ProductionModelSettings(
            provider=ProductionProvider.VLLM,
            model_name="model",
            api_key=None,
            base_url=None,
            timeout_seconds=30.0,
            max_retries=1,
            retry_backoff_seconds=0.0,
        )
