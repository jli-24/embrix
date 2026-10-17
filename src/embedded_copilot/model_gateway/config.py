from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr

from .contracts import ModelPolicy, identifier
from .exceptions import ModelProviderConfigurationError
from .profiles import ProtocolFamily

_PROVIDERS = frozenset({"openai", "anthropic", "google", "xai", "deepseek", "zhipu", "alibaba", "moonshot", "minimax", "custom", "qwen", "local"})
_ENVIRONMENT_NAMES = {
    provider: (f"{provider.upper()}_API_KEY", f"{provider.upper()}_MODEL")
    for provider in _PROVIDERS
    if provider != "local"
}
_DEFAULT_ENDPOINTS = {
    "anthropic": "https://api.anthropic.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta",
    "xai": "https://api.x.ai/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "alibaba": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "local": "http://127.0.0.1:11434/v1",
}
_DEFAULT_PROTOCOLS = {
    "openai": ProtocolFamily.OPENAI_RESPONSES,
    "anthropic": ProtocolFamily.ANTHROPIC_MESSAGES,
    "google": ProtocolFamily.GOOGLE_GENAI,
    "local": ProtocolFamily.LOCAL_OPENAI_COMPATIBLE,
}


@dataclass(frozen=True, slots=True)
class ModelProviderSettings:
    """Validated process configuration for one explicitly selected provider."""

    provider: str
    model_name: str
    api_key: SecretStr | None
    base_url: str | None = None
    protocol_family: ProtocolFamily | None = None
    timeout_seconds: float = 30.0
    supports_image_input: bool = False

    def __post_init__(self) -> None:
        try:
            provider = identifier(self.provider, field="provider")
            model_name = identifier(self.model_name, field="model_name")
        except (TypeError, ValueError) as error:
            raise ModelProviderConfigurationError() from error
        if provider not in _PROVIDERS:
            raise ModelProviderConfigurationError()
        base_url = self.base_url
        if base_url is not None:
            if not isinstance(base_url, str) or not base_url.strip().startswith(("http://", "https://")) or any(item in base_url for item in ("@", "?", "#", "\n", "\r")):
                raise ModelProviderConfigurationError()
            base_url = base_url.strip().rstrip("/")
            if not _is_loopback_url(base_url) and not base_url.startswith("https://"):
                raise ModelProviderConfigurationError()
        if (
            type(self.timeout_seconds) not in (int, float)
            or not 0.1 <= self.timeout_seconds <= 120.0
        ):
            raise ModelProviderConfigurationError()
        if type(self.supports_image_input) is not bool:
            raise ModelProviderConfigurationError()
        if provider == "local":
            if self.api_key is not None:
                raise ModelProviderConfigurationError()
        elif self.api_key is None or not self.api_key.get_secret_value().strip():
            raise ModelProviderConfigurationError()
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "model_name", model_name)
        object.__setattr__(self, "base_url", base_url)
        if self.protocol_family is None:
            object.__setattr__(self, "protocol_family", _DEFAULT_PROTOCOLS.get(provider, ProtocolFamily.OPENAI_CHAT))
        elif not isinstance(self.protocol_family, ProtocolFamily):
            try:
                object.__setattr__(self, "protocol_family", ProtocolFamily(self.protocol_family))
            except ValueError as error:
                raise ModelProviderConfigurationError() from error
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> ModelProviderSettings:
        values = _environment_values(environment)
        provider = values.get("CHAT_MODEL_PROVIDER") or values.get("MODEL_PROVIDER") or "deepseek"
        if not isinstance(provider, str) or not provider.strip():
            raise ModelProviderConfigurationError()
        provider = provider.strip().casefold()
        if provider == "local":
            model = values.get("LOCAL_MODEL", "local")
            return cls(provider=provider, model_name=model, api_key=None, base_url=values.get("LOCAL_BASE_URL", _DEFAULT_ENDPOINTS["local"]))
        names = _ENVIRONMENT_NAMES.get(provider)
        if names is None:
            raise ModelProviderConfigurationError()
        api_key = values.get(names[0])
        model = values.get(names[1])
        if not isinstance(api_key, str) or not isinstance(model, str):
            raise ModelProviderConfigurationError()
        base_url = values.get(f"{provider.upper()}_BASE_URL") or _DEFAULT_ENDPOINTS.get(provider)
        return cls(provider=provider, model_name=model, api_key=SecretStr(api_key), base_url=base_url)

    def to_model_policy(self) -> ModelPolicy:
        return ModelPolicy(
            primary_provider=self.provider,
            model_name=self.model_name,
        )


__all__ = ("ModelProviderSettings",)


def _environment_values(
    environment: Mapping[str, str] | None,
) -> Mapping[str, str]:
    if environment is not None:
        return environment
    values = dict(_dotenv_values(Path.cwd() / ".env"))
    for name, value in os.environ.items():
        values[name] = value
    return values


def _dotenv_values(path: Path) -> dict[str, str]:
    """Read only simple KEY=VALUE entries; never log or persist the values."""

    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            if not name or any(character.isspace() for character in name):
                continue
            values[name] = value.strip().strip("'\"")
    except OSError:
        return {}
    return values


def _is_loopback_url(value: str) -> bool:
    scheme, separator, remainder = value.partition("://")
    if not separator or scheme != "http":
        return False
    authority = remainder.split("/", 1)[0]
    host = authority[1:].split("]", 1)[0] if authority.startswith("[") and "]" in authority else authority.split(":", 1)[0]
    return host.casefold() in {"localhost", "127.0.0.1", "::1"}
