from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    SecretStr,
    field_validator,
    model_validator,
)

from embedded_copilot.model_gateway import (
    ModelPolicy,
    ModelProviderConfigurationError,
)
from embedded_copilot.model_gateway.contracts import identifier

_CLOUD_KEYS = {
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "QWEN_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "xai": "XAI_API_KEY",
    "zhipu": "ZHIPU_API_KEY",
    "alibaba": "ALIBABA_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
    "minimax": "MINIMAX_API_KEY",
}
_CLOUD_MODELS = {
    "openai": "OPENAI_MODEL",
    "deepseek": "DEEPSEEK_MODEL",
    "qwen": "QWEN_MODEL",
    "anthropic": "ANTHROPIC_MODEL",
    "google": "GOOGLE_MODEL",
    "xai": "XAI_MODEL",
    "zhipu": "ZHIPU_MODEL",
    "alibaba": "ALIBABA_MODEL",
    "moonshot": "MOONSHOT_MODEL",
    "minimax": "MINIMAX_MODEL",
}
_EXTENDED_CLOUD_DEFAULT_ENDPOINTS = {
    "anthropic": "https://api.anthropic.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta",
    "xai": "https://api.x.ai/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "alibaba": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "minimax": None,
}
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class ProductionProvider(StrEnum):
    OPENAI = "openai"
    DEEPSEEK = "deepseek"
    QWEN = "qwen"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    XAI = "xai"
    ZHIPU = "zhipu"
    ALIBABA = "alibaba"
    MOONSHOT = "moonshot"
    MINIMAX = "minimax"
    OLLAMA = "ollama"
    VLLM = "vllm"


def _endpoint(value: object, *, provider: ProductionProvider) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("provider endpoint is invalid")
    checked = value.strip().rstrip("/")
    parsed = urlsplit(checked)
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/v1"
        or parsed.hostname is None
    ):
        raise ValueError("provider endpoint is invalid")
    is_loopback = parsed.hostname.casefold() in _LOOPBACK_HOSTS
    if provider is ProductionProvider.OLLAMA:
        if parsed.scheme != "http" or not is_loopback or parsed.port is None:
            raise ValueError("provider endpoint is invalid")
    elif provider is ProductionProvider.VLLM:
        if parsed.scheme == "http":
            if not is_loopback or parsed.port is None:
                raise ValueError("provider endpoint is invalid")
        elif parsed.scheme != "https":
            raise ValueError("provider endpoint is invalid")
    else:
        raise ValueError("provider endpoint is fixed")
    return checked


class ProductionModelSettings(BaseModel):
    """Explicit production settings; never discovers process configuration."""

    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
        validate_default=True,
    )

    provider: ProductionProvider
    model_name: str
    api_key: SecretStr | None
    base_url: str | None
    timeout_seconds: float = 30.0
    max_retries: int = 2
    retry_backoff_seconds: float = 0.25
    supports_image_input: bool = False

    @field_validator("model_name", mode="before")
    @classmethod
    def _model_name(cls, value: object) -> str:
        return identifier(value, field="model_name")

    @field_validator("base_url", mode="before")
    @classmethod
    def _base_url(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("provider endpoint is invalid")
        candidate = value.strip().rstrip("/")
        parsed = urlsplit(candidate)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.hostname is None
            or parsed.path in {"", "/"}
        ):
            raise ValueError("provider endpoint is invalid")
        try:
            port = parsed.port
            if port is not None and not 1 <= port <= 65535:
                raise ValueError("provider endpoint is invalid")
        except ValueError as error:
            raise ValueError("provider endpoint is invalid") from error
        return candidate

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def _timeout(cls, value: object) -> float:
        if type(value) is not float or not 0.1 <= value <= 120.0:
            raise ValueError("provider timeout is invalid")
        return value

    @field_validator("max_retries", mode="before")
    @classmethod
    def _retries(cls, value: object) -> int:
        if type(value) is not int or not 0 <= value <= 5:
            raise ValueError("provider retry count is invalid")
        return value

    @field_validator("retry_backoff_seconds", mode="before")
    @classmethod
    def _backoff(cls, value: object) -> float:
        if type(value) is not float or not 0.0 <= value <= 2.0:
            raise ValueError("provider retry backoff is invalid")
        return value

    @field_validator("supports_image_input", mode="before")
    @classmethod
    def _supports_image_input(cls, value: object) -> bool:
        if type(value) is not bool:
            raise ValueError("image input capability is invalid")
        return value

    @model_validator(mode="after")
    def _provider_boundary(self) -> ProductionModelSettings:
        key = self.api_key
        if key is not None:
            raw_key = key.get_secret_value().strip()
            if not raw_key or "\n" in raw_key or "\r" in raw_key:
                raise ValueError("provider credential is invalid")
            object.__setattr__(self, "api_key", SecretStr(raw_key))
        if self.provider in {
            ProductionProvider.OPENAI,
            ProductionProvider.DEEPSEEK,
            ProductionProvider.QWEN,
        }:
            if self.api_key is None or self.base_url is not None:
                raise ValueError("cloud provider configuration is invalid")
            return self
        if self.provider.value in _EXTENDED_CLOUD_DEFAULT_ENDPOINTS:
            if self.api_key is None:
                raise ValueError("cloud provider configuration is invalid")
            endpoint = self.base_url or _EXTENDED_CLOUD_DEFAULT_ENDPOINTS[self.provider.value]
            if endpoint is None:
                raise ValueError("provider endpoint is required")
            if urlsplit(endpoint).scheme != "https":
                raise ValueError("provider endpoint is invalid")
            object.__setattr__(self, "base_url", endpoint)
            return self
        endpoint = _endpoint(self.base_url, provider=self.provider)
        if endpoint is None:
            raise ValueError("local provider endpoint is required")
        object.__setattr__(self, "base_url", endpoint)
        if self.provider is ProductionProvider.OLLAMA:
            if self.api_key is not None:
                raise ValueError("ollama credential is invalid")
        elif endpoint.startswith("https://") and self.api_key is None:
            raise ValueError("remote vllm credential is required")
        return self

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> ProductionModelSettings:
        """Build settings from an explicitly supplied mapping, never os.environ."""

        try:
            raw_provider = values.get("MODEL_PROVIDER")
            if not isinstance(raw_provider, str):
                raise TypeError
            provider = ProductionProvider(raw_provider.strip().casefold())
            model = values.get("MODEL_NAME") or values.get(
                _CLOUD_MODELS.get(provider.value, "")
            )
            if not isinstance(model, str):
                raise TypeError
            if provider.value in _CLOUD_KEYS:
                raw_key = values.get(_CLOUD_KEYS[provider.value])
                if not isinstance(raw_key, str):
                    raise ValueError
                api_key: SecretStr | None = SecretStr(raw_key)
                if provider.value in _EXTENDED_CLOUD_DEFAULT_ENDPOINTS:
                    base_url = values.get(
                        provider.value.upper() + "_BASE_URL",
                        _EXTENDED_CLOUD_DEFAULT_ENDPOINTS[provider.value],
                    )
                else:
                    base_url = None
            elif provider is ProductionProvider.OLLAMA:
                if "OLLAMA_API_KEY" in values:
                    raise ValueError
                api_key = None
                base_url = values.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1")
            else:
                raw_key = values.get("VLLM_API_KEY")
                api_key = SecretStr(raw_key) if isinstance(raw_key, str) else None
                base_url = values.get("VLLM_BASE_URL", "http://127.0.0.1:8000/v1")
            return cls(
                provider=provider,
                model_name=model,
                api_key=api_key,
                base_url=base_url,
                timeout_seconds=float(values.get("MODEL_TIMEOUT_SECONDS", "30.0")),
                max_retries=int(values.get("MODEL_MAX_RETRIES", "2")),
                retry_backoff_seconds=float(
                    values.get("MODEL_RETRY_BACKOFF_SECONDS", "0.25")
                ),
            )
        except Exception as error:
            raise ModelProviderConfigurationError() from error

    def to_model_policy(self) -> ModelPolicy:
        return ModelPolicy(
            primary_provider=self.provider.value,
            model_name=self.model_name,
        )


__all__ = ("ProductionModelSettings", "ProductionProvider")
