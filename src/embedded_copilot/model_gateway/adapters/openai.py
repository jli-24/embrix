from __future__ import annotations

from pydantic import SecretStr

from .openai_compatible import OpenAICompatibleHTTPAdapter, OpenAICompatibleTransport

OPENAI_CHAT_COMPLETIONS_URL = "https://api.openai.com/v1"


class OpenAIProviderAdapter(OpenAICompatibleHTTPAdapter):
    def __init__(self, *, api_key: SecretStr | str | None = None, **kwargs) -> None:
        super().__init__(
            provider="openai",
            base_url=OPENAI_CHAT_COMPLETIONS_URL,
            api_key=api_key,
            **kwargs,
        )


__all__ = (
    "OPENAI_CHAT_COMPLETIONS_URL",
    "OpenAICompatibleTransport",
    "OpenAIProviderAdapter",
)
