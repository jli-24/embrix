from __future__ import annotations

from typing import Protocol, runtime_checkable

from .contracts import ModelRequest, ModelResponse, ProviderCapability
from .governance import BoundModelIdentity


@runtime_checkable
class ModelGatewayPort(Protocol):
    def generate(self, request: ModelRequest) -> ModelResponse: ...

    async def agenerate(self, request: ModelRequest) -> ModelResponse: ...


@runtime_checkable
class BoundModelGatewayPort(Protocol):
    """Canonical gateway seam for an already governance-bound identity."""

    def generate_bound(
        self, identity: BoundModelIdentity, request: ModelRequest
    ) -> ModelResponse: ...

    async def agenerate_bound(
        self, identity: BoundModelIdentity, request: ModelRequest
    ) -> ModelResponse: ...


@runtime_checkable
class ModelProviderPort(Protocol):
    @property
    def capabilities(self) -> tuple[ProviderCapability, ...]: ...

    def generate(self, request: ModelRequest) -> ModelResponse: ...

    async def agenerate(self, request: ModelRequest) -> ModelResponse: ...


__all__ = ("BoundModelGatewayPort", "ModelGatewayPort", "ModelProviderPort")
