from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..contracts import ModelRequest, ModelResponse, ProviderCapability
from ..exceptions import ModelProviderNotImplemented
from ..ports import ModelProviderPort


@runtime_checkable
class LocalModelRunner(Protocol):
    """Compatibility-only protocol; v3.3.1 does not invoke local runners."""

    def generate(self, request: ModelRequest) -> ModelResponse: ...

    async def agenerate(self, request: ModelRequest) -> ModelResponse: ...


class LocalModelAdapter(ModelProviderPort):
    """Explicitly unavailable placeholder for future local inference support."""

    def __init__(self, *, runner: LocalModelRunner | None = None) -> None:
        del runner

    @property
    def capabilities(self) -> tuple[ProviderCapability, ...]:
        return (ProviderCapability.TEXT,)

    def generate(self, request: ModelRequest) -> ModelResponse:
        del request
        raise ModelProviderNotImplemented()

    async def agenerate(self, request: ModelRequest) -> ModelResponse:
        del request
        raise ModelProviderNotImplemented()


__all__ = ("LocalModelAdapter", "LocalModelRunner")
