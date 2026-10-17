from __future__ import annotations

import copy
from typing import Protocol, runtime_checkable

from ..models import (
    HardwareBuildReceipt,
    HardwareBuildRequest,
    HardwareFlashReceipt,
    HardwareFlashRequest,
)


@runtime_checkable
class ESP32ExecutionBridgePort(Protocol):
    def build(self, request: HardwareBuildRequest) -> HardwareBuildReceipt: ...

    def flash(self, request: HardwareFlashRequest) -> HardwareFlashReceipt: ...


class ESP32RuntimeAdapter:
    """Bridges an injected controlled execution port; never starts a process itself."""

    __slots__ = ("_bridge",)

    def __init__(self, bridge: ESP32ExecutionBridgePort) -> None:
        if not isinstance(bridge, ESP32ExecutionBridgePort):
            raise TypeError("ESP32 execution bridge is invalid")
        self._bridge = bridge

    def build(self, request: HardwareBuildRequest) -> HardwareBuildReceipt:
        return self._bridge.build(copy.deepcopy(request))

    def flash(self, request: HardwareFlashRequest) -> HardwareFlashReceipt:
        return self._bridge.flash(copy.deepcopy(request))


__all__ = ("ESP32ExecutionBridgePort", "ESP32RuntimeAdapter")
