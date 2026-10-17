from __future__ import annotations

from ..exceptions import HILUnavailable


class UARTMeasurementAdapter:
    __slots__ = ("_port",)

    def __init__(self, port=None) -> None:
        self._port = port

    def collect(self, request):
        if self._port is None:
            raise HILUnavailable("UART observation unavailable")
        return self._port.collect(request)
