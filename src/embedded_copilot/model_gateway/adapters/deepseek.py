from __future__ import annotations

from pydantic import SecretStr

from .openai_compatible import OpenAICompatibleHTTPAdapter

DEEPSEEK_CHAT_COMPLETIONS_URL = "https://api.deepseek.com/v1"


class DeepSeekProviderAdapter(OpenAICompatibleHTTPAdapter):
    def __init__(self, *, api_key: SecretStr | str | None = None, **kwargs) -> None:
        super().__init__(
            provider="deepseek",
            base_url=DEEPSEEK_CHAT_COMPLETIONS_URL,
            api_key=api_key,
            **kwargs,
        )


__all__ = ("DEEPSEEK_CHAT_COMPLETIONS_URL", "DeepSeekProviderAdapter")
