from __future__ import annotations

from typing import Protocol, runtime_checkable

from .contracts import VisionRequest, VisionResponse


@runtime_checkable
class VisionModelProviderPort(Protocol):
    @property
    def provider(self) -> str: ...

    @property
    def model(self) -> str: ...

    def generate(self, request: VisionRequest) -> VisionResponse: ...

    async def agenerate(self, request: VisionRequest) -> VisionResponse: ...


__all__ = ("VisionModelProviderPort",)
