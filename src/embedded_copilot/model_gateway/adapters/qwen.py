from __future__ import annotations

from pydantic import SecretStr

from .openai_compatible import OpenAICompatibleHTTPAdapter

QWEN_CHAT_COMPLETIONS_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class QwenProviderAdapter(OpenAICompatibleHTTPAdapter):
    def __init__(self, *, api_key: SecretStr | str | None = None, **kwargs) -> None:
        super().__init__(
            provider="qwen",
            base_url=QWEN_CHAT_COMPLETIONS_URL,
            api_key=api_key,
            **kwargs,
        )


__all__ = ("QWEN_CHAT_COMPLETIONS_URL", "QwenProviderAdapter")
