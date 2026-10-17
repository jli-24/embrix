from __future__ import annotations

import copy
from typing import Protocol, runtime_checkable

from ..models import HardwareFlashReceipt, HardwareFlashRequest


@runtime_checkable
class JLinkFlashBridgePort(Protocol):
    def flash(self, request: HardwareFlashRequest) -> HardwareFlashReceipt: ...


class JLinkFlashAdapter:
    __slots__ = ("_bridge",)

    def __init__(self, bridge: JLinkFlashBridgePort) -> None:
        if not isinstance(bridge, JLinkFlashBridgePort):
            raise TypeError("J-Link bridge is invalid")
        self._bridge = bridge

    def flash(self, request: HardwareFlashRequest) -> HardwareFlashReceipt:
        return self._bridge.flash(copy.deepcopy(request))


__all__ = ("JLinkFlashAdapter", "JLinkFlashBridgePort")
