"""Stable private count response identity and legacy forwarding entrypoints.

HTTP collection is implemented by the adapter package. Importing this module
does not create a client, send a request, or grant dispatch permission.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..exceptions import ModelGatewayRejected


def _reject(code="BUDGET_EVIDENCE_UNAVAILABLE"):
    raise ModelGatewayRejected(reason_code=code)


@dataclass(frozen=True)
class CounterHTTPResponse:
    status_code: int
    response_bytes: bytes
    request_sha256: str
    wire_target: tuple
    response_request_id: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.status_code) is not int
            or not 100 <= self.status_code <= 599
            or type(self.response_bytes) is not bytes
            or type(self.request_sha256) is not str
            or type(self.wire_target) is not tuple
        ):
            _reject()



def execute_native_count(
    adapter: object, payload: bytes, *, dispatch_guard: Callable[..., object]
) -> CounterHTTPResponse:
    """Forward the legacy entrypoint without copying bytes or calling the guard."""
    from ..adapters.openai_count_collector import execute_native_count as collect

    return collect(adapter, payload, dispatch_guard=dispatch_guard)


async def aexecute_native_count(
    adapter: object, payload: bytes, *, dispatch_guard: Callable[..., object]
) -> CounterHTTPResponse:
    """Forward once and preserve cancellation and error propagation."""
    from ..adapters.openai_count_collector import aexecute_native_count as collect

    return await collect(adapter, payload, dispatch_guard=dispatch_guard)
