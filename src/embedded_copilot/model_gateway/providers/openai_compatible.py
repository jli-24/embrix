from __future__ import annotations

import asyncio
import copy

from ..adapters.deepseek import DeepSeekProviderAdapter
from ..adapters.openai import OpenAIProviderAdapter
from ..adapters.openai_compatible import OpenAICompatibleTransport
from ..adapters.qwen import QwenProviderAdapter
from ..contracts import (
    ModelRequest,
    ModelResponse,
    ProviderCapability,
    validate_model_response,
)
from ..exceptions import ModelGatewayUnavailable
from ..ports import ModelProviderPort


class OpenAICompatibleProviderAdapter(ModelProviderPort):
    """Compatibility wrapper for the former injected-only adapter."""

    __slots__ = ("_transport",)

    def __init__(self, *, transport: OpenAICompatibleTransport | None = None) -> None:
        self._transport = transport

    @property
    def capabilities(self) -> tuple[ProviderCapability, ...]:
        return (
            ProviderCapability.TEXT,
            *((ProviderCapability.VISION,) if bool(getattr(self._transport, "supports_image_input", False)) else ()),
        )

    def generate(self, request: ModelRequest) -> ModelResponse:
        if self._transport is None:
            raise ModelGatewayUnavailable()
        try:
            return validate_model_response(
                self._transport.generate(copy.deepcopy(request))
            )
        except ModelGatewayUnavailable:
            raise
        except (TypeError, ValueError):
            raise ModelGatewayUnavailable() from None

    async def agenerate(self, request: ModelRequest) -> ModelResponse:
        if self._transport is None:
            raise ModelGatewayUnavailable()
        try:
            return validate_model_response(
                await self._transport.agenerate(copy.deepcopy(request))
            )
        except asyncio.CancelledError:
            raise
        except ModelGatewayUnavailable:
            raise
        except (TypeError, ValueError):
            raise ModelGatewayUnavailable() from None


__all__ = (
    "DeepSeekProviderAdapter",
    "OpenAICompatibleProviderAdapter",
    "OpenAICompatibleTransport",
    "OpenAIProviderAdapter",
    "QwenProviderAdapter",
)
