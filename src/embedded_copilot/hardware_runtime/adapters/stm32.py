from __future__ import annotations

import copy
from typing import Protocol, runtime_checkable

from ..models import HardwareBuildReceipt, HardwareBuildRequest


@runtime_checkable
class STM32BuildBridgePort(Protocol):
    def build(self, request: HardwareBuildRequest) -> HardwareBuildReceipt: ...


class STM32BuildAdapter:
    __slots__ = ("_bridge",)

    def __init__(self, bridge: STM32BuildBridgePort) -> None:
        if not isinstance(bridge, STM32BuildBridgePort):
            raise TypeError("STM32 build bridge is invalid")
        self._bridge = bridge

    def build(self, request: HardwareBuildRequest) -> HardwareBuildReceipt:
        return self._bridge.build(copy.deepcopy(request))


__all__ = ("STM32BuildAdapter", "STM32BuildBridgePort")
