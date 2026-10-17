from __future__ import annotations

from ..exceptions import ModelGatewayUnavailable


class VisionProviderError(ModelGatewayUnavailable):
    """Base class for sanitized Vision provider failures."""

    def __init__(self, message: str = "vision provider request unavailable") -> None:
        super().__init__(message)


class VisionAuthenticationError(VisionProviderError):
    def __init__(self) -> None:
        super().__init__("vision provider authentication failed")


class VisionRateLimitError(VisionProviderError):
    def __init__(self) -> None:
        super().__init__("vision provider rate limited")


class VisionTimeoutError(VisionProviderError):
    def __init__(self) -> None:
        super().__init__("vision provider timed out")


class VisionUnavailableError(VisionProviderError):
    def __init__(self) -> None:
        super().__init__("vision provider unavailable")


class VisionMalformedResponseError(VisionProviderError):
    def __init__(self) -> None:
        super().__init__("vision provider response malformed")


__all__ = (
    "VisionAuthenticationError",
    "VisionMalformedResponseError",
    "VisionProviderError",
    "VisionRateLimitError",
    "VisionTimeoutError",
    "VisionUnavailableError",
)
