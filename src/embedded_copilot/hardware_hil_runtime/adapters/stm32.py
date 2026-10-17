from __future__ import annotations

from ..exceptions import HILUnavailable


class STM32HILAdapter:
    __slots__ = ("_port",)

    def __init__(self, port=None) -> None:
        self._port = port

    def execute(self, request):
        if self._port is None:
            raise HILUnavailable("STM32 HIL backend unavailable")
        return self._port.execute(request)
