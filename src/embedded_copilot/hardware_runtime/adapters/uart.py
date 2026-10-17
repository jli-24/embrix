from __future__ import annotations

import copy
from typing import Protocol, runtime_checkable

from ..models import HardwareObservation, HardwareObservationRequest


@runtime_checkable
class UARTObservationBridgePort(Protocol):
    def observe(self, request: HardwareObservationRequest) -> HardwareObservation: ...


class UARTObservationAdapter:
    __slots__ = ("_bridge",)

    def __init__(self, bridge: UARTObservationBridgePort) -> None:
        if not isinstance(bridge, UARTObservationBridgePort):
            raise TypeError("UART observation bridge is invalid")
        self._bridge = bridge

    def observe(self, request: HardwareObservationRequest) -> HardwareObservation:
        return self._bridge.observe(copy.deepcopy(request))


__all__ = ("UARTObservationAdapter", "UARTObservationBridgePort")
