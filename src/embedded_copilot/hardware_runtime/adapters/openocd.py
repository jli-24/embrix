from __future__ import annotations

import copy
from typing import Protocol, runtime_checkable

from ..models import HardwareFlashReceipt, HardwareFlashRequest


@runtime_checkable
class OpenOCDFlashBridgePort(Protocol):
    def flash(self, request: HardwareFlashRequest) -> HardwareFlashReceipt: ...


class OpenOCDFlashAdapter:
    __slots__ = ("_bridge",)

    def __init__(self, bridge: OpenOCDFlashBridgePort) -> None:
        if not isinstance(bridge, OpenOCDFlashBridgePort):
            raise TypeError("OpenOCD bridge is invalid")
        self._bridge = bridge

    def flash(self, request: HardwareFlashRequest) -> HardwareFlashReceipt:
        return self._bridge.flash(copy.deepcopy(request))


__all__ = ("OpenOCDFlashAdapter", "OpenOCDFlashBridgePort")
