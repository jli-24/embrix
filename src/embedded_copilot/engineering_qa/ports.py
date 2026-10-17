from __future__ import annotations

from typing import Protocol, runtime_checkable

from embedded_copilot.engineering_context import (
    EngineeringContextRequest,
    EngineeringContextSnapshot,
)
from embedded_copilot.model_gateway import ModelRequest, ModelResponse


@runtime_checkable
class EngineeringContextProvider(Protocol):
    def get_context(self, request: EngineeringContextRequest) -> EngineeringContextSnapshot | None: ...


@runtime_checkable
class EngineeringQAModelGateway(Protocol):
    def generate(self, request: ModelRequest) -> ModelResponse: ...

    async def agenerate(self, request: ModelRequest) -> ModelResponse: ...


__all__ = ("EngineeringContextProvider", "EngineeringQAModelGateway")
