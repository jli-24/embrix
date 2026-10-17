from __future__ import annotations

from ..exceptions import HILUnavailable


class ESP32HILAdapter:
    """A narrow delegate; concrete ESP-IDF/esptool ownership remains outside v4.5."""

    __slots__ = ("_port",)

    def __init__(self, port) -> None:
        self._port = port

    def execute(self, request):
        if self._port is None:
            raise HILUnavailable("ESP32 HIL adapter unavailable")
        return self._port.execute(request)
