from __future__ import annotations

from ..exceptions import HILUnavailable


class LegacyHILAdapter:
    """Optional narrow bridge; the caller owns any legacy HIL service adapter."""

    __slots__ = ("_port",)

    def __init__(self, port=None) -> None:
        self._port = port

    def execute(self, request):
        if self._port is None:
            raise HILUnavailable("legacy HIL bridge unavailable")
        return self._port.execute(request)
