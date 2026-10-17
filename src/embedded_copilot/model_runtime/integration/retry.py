from __future__ import annotations

import asyncio
import copy
import time
from collections.abc import Awaitable, Callable

from embedded_copilot.model_gateway import (
    ModelProviderConfigurationError,
    ModelProviderError,
    ModelProviderNotImplemented,
    ModelProviderPort,
    ModelProviderUnauthorized,
    ModelRequest,
    ModelResponse,
    ProviderCapability,
    validate_model_request,
    validate_model_response,
)


class RetryingModelProviderAdapter(ModelProviderPort):
    """Bounded retry wrapper for explicitly retryable provider failures."""

    __slots__ = ("_asleep", "_backoff", "_max_retries", "_provider", "_sleep")

    def __init__(
        self,
        *,
        provider: ModelProviderPort,
        max_retries: int,
        retry_backoff_seconds: float,
        sleep: Callable[[float], None] | None = None,
        asleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if not isinstance(provider, ModelProviderPort):
            raise TypeError("model provider port is invalid")
        if type(max_retries) is not int or not 0 <= max_retries <= 5:
            raise ValueError("provider retry count is invalid")
        if (
            type(retry_backoff_seconds) is not float
            or not 0.0 <= retry_backoff_seconds <= 2.0
        ):
            raise ValueError("provider retry backoff is invalid")
        self._asleep = asyncio.sleep if asleep is None else asleep
        self._backoff = retry_backoff_seconds
        self._max_retries = max_retries
        self._provider = provider
        self._sleep = time.sleep if sleep is None else sleep

    @property
    def capabilities(self) -> tuple[ProviderCapability, ...]:
        return tuple(self._provider.capabilities)

    def _delay(self, attempt: int) -> float:
        return self._backoff * (2**attempt)

    def generate(self, request: ModelRequest) -> ModelResponse:
        checked = validate_model_request(request)
        for attempt in range(self._max_retries + 1):
            try:
                return validate_model_response(
                    self._provider.generate(copy.deepcopy(checked))
                )
            except (
                ModelProviderUnauthorized,
                ModelProviderConfigurationError,
                ModelProviderNotImplemented,
            ):
                raise
            except ModelProviderError:
                if attempt >= self._max_retries:
                    raise
                delay = self._delay(attempt)
                if delay:
                    self._sleep(delay)
        raise AssertionError("unreachable provider retry state")

    async def agenerate(self, request: ModelRequest) -> ModelResponse:
        checked = validate_model_request(request)
        for attempt in range(self._max_retries + 1):
            try:
                return validate_model_response(
                    await self._provider.agenerate(copy.deepcopy(checked))
                )
            except asyncio.CancelledError:
                raise
            except (
                ModelProviderUnauthorized,
                ModelProviderConfigurationError,
                ModelProviderNotImplemented,
            ):
                raise
            except ModelProviderError:
                if attempt >= self._max_retries:
                    raise
                delay = self._delay(attempt)
                if delay:
                    await self._asleep(delay)
        raise AssertionError("unreachable provider retry state")


__all__ = ("RetryingModelProviderAdapter",)
