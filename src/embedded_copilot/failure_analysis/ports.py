from __future__ import annotations

from typing import Protocol, runtime_checkable

from embedded_copilot.engineering_context import (
    EngineeringContextProviderPort,
)
from embedded_copilot.model_gateway import ModelGatewayPort


@runtime_checkable
class FailureContextPort(EngineeringContextProviderPort, Protocol):
    pass


@runtime_checkable
class FailureModelPort(ModelGatewayPort, Protocol):
    pass


__all__ = ("FailureContextPort", "FailureModelPort")
