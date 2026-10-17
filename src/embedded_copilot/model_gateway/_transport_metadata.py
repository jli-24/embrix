"""Internal, secret-free provider transport metadata."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .contracts import ModelResponse

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _provider_request_id(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("provider request id is invalid")
    candidate = value.strip()
    if not candidate or not _SAFE_IDENTIFIER.fullmatch(candidate):
        raise ValueError("provider request id is invalid")
    return candidate


@dataclass(frozen=True, slots=True)
class ProviderExecutionMetadata:
    """Validated metadata captured by the provider transport boundary."""

    provider_request_id: str | None = None
    http_status: int | None = None
    latency_ms: float | None = None
    transport_name: str | None = None
    connection_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "provider_request_id", _provider_request_id(self.provider_request_id)
        )
        if self.http_status is not None and (
            type(self.http_status) is not int or not 100 <= self.http_status <= 599
        ):
            raise ValueError("provider HTTP status is invalid")
        if self.latency_ms is not None and (
            type(self.latency_ms) not in (int, float)
            or not math.isfinite(float(self.latency_ms))
            or self.latency_ms < 0.0
        ):
            raise ValueError("provider latency is invalid")
        if self.transport_name is not None:
            name = _provider_request_id(self.transport_name)
            object.__setattr__(self, "transport_name", name)
        if self.connection_id is not None:
            object.__setattr__(self, "connection_id", _provider_request_id(self.connection_id))
        if self.latency_ms is not None:
            object.__setattr__(self, "latency_ms", float(self.latency_ms))


@dataclass(frozen=True, slots=True)
class ProviderExecutionResult:
    """Internal result pairing a normalized response with transport metadata."""

    response: ModelResponse
    metadata: ProviderExecutionMetadata | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.response, ModelResponse):
            raise TypeError("provider execution response is invalid")
        if self.metadata is not None and not isinstance(
            self.metadata, ProviderExecutionMetadata
        ):
            raise TypeError("provider execution metadata is invalid")


__all__ = ()
