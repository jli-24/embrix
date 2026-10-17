from __future__ import annotations

import pytest

from embedded_copilot.core.config import Settings
from embedded_copilot.model_runtime.composition.config import (
    ModelRuntimeConfig,
    load_model_runtime_config,
)


def test_model_provider_defaults_to_explicit_unavailable() -> None:
    settings = Settings(_env_file=None)

    config = load_model_runtime_config(settings)

    assert config == ModelRuntimeConfig(
        provider="unavailable",
        base_url=None,
        model=None,
        timeout_seconds=10.0,
        require_tls=False,
    )
    assert settings.version == "7.1.0"


def test_ollama_is_opt_in_and_requires_configured_model() -> None:
    with pytest.raises(ValueError, match="model runtime configuration is invalid"):
        load_model_runtime_config(
            Settings(
                model_provider="ollama",
                ollama_model=None,
                _env_file=None,
            )
        )

    config = load_model_runtime_config(
        Settings(
            model_provider="ollama",
            ollama_model="vendor/arbitrary-model:latest",
            _env_file=None,
        )
    )

    assert config.provider == "ollama"
    assert config.model == "vendor/arbitrary-model:latest"
    assert config.base_url == "http://127.0.0.1:11434"


@pytest.mark.parametrize(
    "base_url",
    (
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://[::1]:11434",
        "http://100.64.0.1:11434",
        "http://100.127.255.254:11434",
        "http://[fd7a:115c:a1e0::1234]:11434",
    ),
)
def test_local_and_tailscale_literal_addresses_are_allowed(base_url: str) -> None:
    config = load_model_runtime_config(
        Settings(
            model_provider="ollama",
            ollama_base_url=base_url,
            ollama_model="edge-model",
            _env_file=None,
        )
    )

    assert config.base_url == base_url


@pytest.mark.parametrize(
    "base_url",
    (
        "http://ollama.internal:11434",
        "http://edge.tailnet.ts.net:11434",
        "http://8.8.8.8:11434",
        "http://192.168.1.10:11434",
        "http://100.128.0.1:11434",
        "http://user:password@127.0.0.1:11434",
        "http://127.0.0.1:11434/api",
        "http://127.0.0.1:11434?token=private",
        "http://127.0.0.1:11434#fragment",
    ),
)
def test_non_allowlisted_or_credential_endpoints_are_rejected(
    base_url: str,
) -> None:
    with pytest.raises(ValueError, match="model runtime configuration is invalid"):
        load_model_runtime_config(
            Settings(
                model_provider="ollama",
                ollama_base_url=base_url,
                ollama_model="edge-model",
                _env_file=None,
            )
        )


def test_tls_requirement_is_configurable_without_bypassing_address_allowlist() -> None:
    with pytest.raises(ValueError, match="model runtime configuration is invalid"):
        load_model_runtime_config(
            Settings(
                model_provider="ollama",
                ollama_base_url="http://100.64.0.10:11434",
                ollama_model="edge-model",
                ollama_require_tls=True,
                _env_file=None,
            )
        )

    secure = load_model_runtime_config(
        Settings(
            model_provider="ollama",
            ollama_base_url="https://100.64.0.10:11434",
            ollama_model="edge-model",
            ollama_require_tls=True,
            _env_file=None,
        )
    )
    assert secure.require_tls is True

    with pytest.raises(ValueError, match="model runtime configuration is invalid"):
        load_model_runtime_config(
            Settings(
                model_provider="ollama",
                ollama_base_url="https://8.8.8.8:11434",
                ollama_model="edge-model",
                ollama_require_tls=True,
                _env_file=None,
            )
        )


def test_prefixed_environment_variables_configure_model_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBEDDED_COPILOT_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv(
        "EMBEDDED_COPILOT_OLLAMA_BASE_URL",
        "https://100.64.0.20:11434",
    )
    monkeypatch.setenv("EMBEDDED_COPILOT_OLLAMA_MODEL", "edge-model")
    monkeypatch.setenv("EMBEDDED_COPILOT_OLLAMA_REQUIRE_TLS", "true")

    config = load_model_runtime_config(Settings(_env_file=None))

    assert config == ModelRuntimeConfig(
        provider="ollama",
        base_url="https://100.64.0.20:11434",
        model="edge-model",
        timeout_seconds=10.0,
        require_tls=True,
    )
