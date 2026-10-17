from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Any

import httpx
from pydantic import SecretStr

from embedded_copilot.model_gateway import (
    ModelProviderError,
    ModelProviderConfigurationError,
    ModelProviderPort,
    ModelProviderRateLimited,
    ModelProviderTimeout,
    ModelProviderUnauthorized,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ProviderCapability,
    validate_model_request,
)

from .settings import ProductionProvider, _endpoint


class LocalOpenAICompatibleProviderAdapter(ModelProviderPort):
    """Restricted OpenAI-compatible adapter for Ollama and vLLM endpoints."""

    __slots__ = (
        "_api_key",
        "_async_transport",
        "_base_url",
        "_provider",
        "_supports_image_input",
        "_sync_transport",
        "_timeout_seconds",
    )

    def __init__(
        self,
        *,
        provider: ProductionProvider,
        base_url: str,
        api_key: SecretStr | None,
        timeout_seconds: float,
        supports_image_input: bool = False,
        http_transport: httpx.BaseTransport | None = None,
        async_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if provider not in {ProductionProvider.OLLAMA, ProductionProvider.VLLM}:
            raise ValueError("local provider is invalid")
        checked_url = _endpoint(base_url, provider=provider)
        if checked_url is None:
            raise ValueError("local provider endpoint is invalid")
        if type(timeout_seconds) is not float or not 0.1 <= timeout_seconds <= 120.0:
            raise ValueError("local provider timeout is invalid")
        if type(supports_image_input) is not bool:
            raise ValueError("image input capability is invalid")
        if provider is ProductionProvider.OLLAMA and api_key is not None:
            raise ValueError("ollama credential is invalid")
        if checked_url.startswith("https://") and api_key is None:
            raise ValueError("remote provider credential is invalid")
        self._api_key = api_key
        self._async_transport = async_transport
        self._base_url = checked_url + "/"
        self._provider = provider.value
        self._supports_image_input = supports_image_input
        self._sync_transport = http_transport
        self._timeout_seconds = timeout_seconds

    @property
    def capabilities(self) -> tuple[ProviderCapability, ...]:
        return (
            ProviderCapability.TEXT,
            *((ProviderCapability.VISION,) if self._supports_image_input else ()),
        )

    def generate(self, request: ModelRequest) -> ModelResponse:
        checked = validate_model_request(request)
        if checked.images and not self._supports_image_input:
            raise ModelProviderConfigurationError()
        try:
            with httpx.Client(
                base_url=self._base_url,
                timeout=self._timeout_seconds,
                transport=self._sync_transport,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                response = client.post(
                    "chat/completions",
                    headers=self._headers(),
                    json=self._payload(checked),
                )
            return self._response(checked, response)
        except ModelProviderError:
            raise
        except httpx.TimeoutException:
            raise ModelProviderTimeout() from None
        except httpx.HTTPError:
            raise ModelProviderError() from None

    async def agenerate(self, request: ModelRequest) -> ModelResponse:
        checked = validate_model_request(request)
        if checked.images and not self._supports_image_input:
            raise ModelProviderConfigurationError()
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout_seconds,
                transport=self._async_transport,
                trust_env=False,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    "chat/completions",
                    headers=self._headers(),
                    json=self._payload(checked),
                )
            return self._response(checked, response)
        except asyncio.CancelledError:
            raise
        except ModelProviderError:
            raise
        except httpx.TimeoutException:
            raise ModelProviderTimeout() from None
        except httpx.HTTPError:
            raise ModelProviderError() from None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key.get_secret_value()}"
        return headers

    def _payload(self, request: ModelRequest) -> dict[str, object]:
        context = json.dumps(
            request.context.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        user_content: object = "\n".join(
            (
                "Engineering context:",
                context,
                "Task:",
                request.prompt,
                "Constraints:",
                json.dumps(
                    request.constraints,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
        )
        if request.images:
            user_content = [
                {"type": "text", "text": user_content},
                *(
                    {
                        "type": "image_url",
                        "image_url": {"url": image.content_ref, "detail": "auto"},
                    }
                    for image in request.images
                ),
            ]
        return {
            "model": request.model_policy.model_name,
            "messages": (
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": user_content},
            ),
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }

    def _response(
        self, request: ModelRequest, response: httpx.Response
    ) -> ModelResponse:
        if response.status_code in {401, 403}:
            raise ModelProviderUnauthorized()
        if response.status_code == 429:
            raise ModelProviderRateLimited()
        if response.status_code in {408, 504}:
            raise ModelProviderTimeout()
        if not 200 <= response.status_code < 300:
            raise ModelProviderError()
        try:
            payload = response.json()
            if not isinstance(payload, Mapping):
                raise TypeError
            choices = payload.get("choices")
            if (
                not isinstance(choices, Sequence)
                or isinstance(choices, (str, bytes))
                or not choices
            ):
                raise TypeError
            choice = choices[0]
            if not isinstance(choice, Mapping):
                raise TypeError
            message = choice.get("message")
            if not isinstance(message, Mapping):
                raise TypeError
            content = message.get("content")
            if not isinstance(content, str):
                raise TypeError
            finish_reason = choice.get("finish_reason", "STOP")
            if not isinstance(finish_reason, str):
                raise TypeError
            return ModelResponse(
                content=content,
                model_name=request.model_policy.model_name,
                provider=self._provider,
                usage=self._usage(payload.get("usage")),
                finish_reason=finish_reason,
                provenance=request.context.provenance,
            )
        except ModelProviderError:
            raise
        except (TypeError, ValueError):
            raise ModelProviderError() from None

    @staticmethod
    def _usage(value: Any) -> ModelUsage | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ModelProviderError()
        items = (
            value.get("prompt_tokens"),
            value.get("completion_tokens"),
            value.get("total_tokens"),
        )
        if any(type(item) is not int for item in items):
            raise ModelProviderError()
        return ModelUsage(
            input_tokens=items[0],
            output_tokens=items[1],
            total_tokens=items[2],
        )


__all__ = ("LocalOpenAICompatibleProviderAdapter",)
