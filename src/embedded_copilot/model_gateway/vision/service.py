from __future__ import annotations

import asyncio
import copy

from pydantic import ValidationError

from ..exceptions import ModelGatewayRejected, ModelGatewayUnavailable
from .contracts import (
    VisionRequest,
    VisionResponse,
    validate_vision_request,
    validate_vision_response,
)
from .exceptions import VisionProviderError
from .ports import VisionModelProviderPort


class VisionService:
    """Context-first service over one explicitly injected vision provider."""

    __slots__ = ("_model", "_provider", "_provider_name")

    def __init__(self, *, provider: VisionModelProviderPort) -> None:
        if not isinstance(provider, VisionModelProviderPort):
            raise TypeError("vision provider port is invalid")
        if not isinstance(provider.provider, str) or not provider.provider:
            raise TypeError("vision provider binding is invalid")
        if not isinstance(provider.model, str) or not provider.model:
            raise TypeError("vision model binding is invalid")
        self._provider = provider
        self._provider_name = provider.provider
        self._model = provider.model

    def _request(self, request: VisionRequest) -> VisionRequest:
        try:
            return validate_vision_request(request)
        except (TypeError, ValueError, ValidationError) as error:
            raise ModelGatewayRejected("vision request rejected") from error

    def _response(self, value: object) -> VisionResponse:
        try:
            response = validate_vision_response(value)
        except (TypeError, ValueError, ValidationError) as error:
            raise ModelGatewayRejected("vision response rejected") from error
        if response.provider != self._provider_name or response.model != self._model:
            raise ModelGatewayRejected("vision response provenance rejected")
        return response

    def generate(self, request: VisionRequest) -> VisionResponse:
        checked = self._request(request)
        try:
            value = self._provider.generate(copy.deepcopy(checked))
        except asyncio.CancelledError:
            raise
        except VisionProviderError:
            raise
        except Exception as error:
            raise ModelGatewayUnavailable("vision provider unavailable") from error
        return self._response(value)

    async def agenerate(self, request: VisionRequest) -> VisionResponse:
        checked = self._request(request)
        try:
            value = await self._provider.agenerate(copy.deepcopy(checked))
        except asyncio.CancelledError:
            raise
        except VisionProviderError:
            raise
        except Exception as error:
            raise ModelGatewayUnavailable("vision provider unavailable") from error
        return self._response(value)


__all__ = ("VisionService",)
