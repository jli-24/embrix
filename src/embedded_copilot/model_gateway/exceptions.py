from __future__ import annotations

_SAFE_TRANSPORT_ERROR_CODES = frozenset(
    {
        "PROXY_ERROR",
        "CONNECT_TIMEOUT",
        "READ_TIMEOUT",
        "WRITE_TIMEOUT",
        "POOL_TIMEOUT",
        "CONNECT_ERROR",
        "READ_ERROR",
        "WRITE_ERROR",
        "CLOSE_ERROR",
        "PROTOCOL_ERROR",
        "HTTP_TRANSPORT_ERROR",
    }
)
_SAFE_TRANSPORT_STAGES = frozenset({"DNS", "TCP", "TLS", "HTTP"})


def _safe_transport_code(value: object) -> str | None:
    return value if isinstance(value, str) and value in _SAFE_TRANSPORT_ERROR_CODES else None


def _safe_http_status(value: object) -> int | None:
    return value if type(value) is int and 100 <= value <= 599 else None


def _safe_transport_stage(value: object) -> str | None:
    return value if isinstance(value, str) and value in _SAFE_TRANSPORT_STAGES else None


class ModelGatewayError(RuntimeError):
    def __init__(self, message: str = "model gateway request unavailable") -> None:
        super().__init__(message)


class ModelGatewayUnavailable(ModelGatewayError):
    pass


class ModelGatewayRejected(ModelGatewayError):
    def __init__(
        self,
        message: str = "model gateway request rejected",
        *,
        reason_code: str = "MODEL_GATEWAY_EXECUTION_FAILED",
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class ModelProviderError(ModelGatewayUnavailable):
    """Fixed, sanitized external-provider failure."""

    def __init__(
        self,
        message: str = "model provider unavailable",
        *,
        safe_http_status: int | None = None,
        safe_transport_error_code: str | None = None,
        safe_transport_stage: str | None = None,
    ) -> None:
        super().__init__(message)
        self.safe_http_status = _safe_http_status(safe_http_status)
        self.safe_transport_error_code = _safe_transport_code(safe_transport_error_code)
        self.safe_transport_stage = _safe_transport_stage(safe_transport_stage)


class ModelProviderParseError(ModelProviderError):
    def __init__(self) -> None:
        super().__init__("model provider response is invalid")


class ModelProviderAnswerEmpty(ModelProviderParseError):
    def __init__(self) -> None:
        ModelProviderError.__init__(self, "model provider answer is empty")


class ModelProviderTimeout(ModelProviderError):
    def __init__(
        self,
        *,
        safe_transport_error_code: str | None = None,
        safe_transport_stage: str | None = None,
    ) -> None:
        super().__init__(
            "model provider timed out",
            safe_transport_error_code=safe_transport_error_code,
            safe_transport_stage=safe_transport_stage,
        )


class ModelProviderUnauthorized(ModelProviderError):
    def __init__(self, *, safe_http_status: int | None = None) -> None:
        super().__init__(
            "model provider authentication failed",
            safe_http_status=safe_http_status,
        )


class ModelProviderModelUnavailable(ModelProviderError):
    def __init__(self, *, safe_http_status: int | None = None) -> None:
        super().__init__(
            "model provider model unavailable",
            safe_http_status=safe_http_status,
        )


class ModelProviderRateLimited(ModelProviderError):
    def __init__(self, *, safe_http_status: int | None = None) -> None:
        super().__init__(
            "model provider rate limited",
            safe_http_status=safe_http_status,
        )


class ModelProviderHTTPError(ModelProviderError):
    """A provider returned a non-success HTTP status."""

    def __init__(self, *, safe_http_status: int) -> None:
        super().__init__(
            "model provider returned an HTTP error",
            safe_http_status=safe_http_status,
        )


class ModelProviderNetworkError(ModelProviderError):
    """A provider request failed before a usable HTTP response was received."""

    def __init__(
        self,
        *,
        safe_transport_error_code: str,
        safe_transport_stage: str | None = None,
    ) -> None:
        super().__init__(
            "model provider network request failed",
            safe_transport_error_code=safe_transport_error_code,
            safe_transport_stage=safe_transport_stage,
        )


class ModelProviderNotImplemented(ModelProviderError):
    def __init__(self) -> None:
        super().__init__("local model provider is not implemented")


class ModelProviderConfigurationError(ModelProviderError):
    def __init__(self) -> None:
        super().__init__("model provider configuration is invalid")


class ModelGatewayProviderFailure(ModelGatewayUnavailable):
    """Provider failure preserved across the gateway boundary as a safe code."""

    def __init__(
        self,
        reason_code: str,
        *,
        provider_response_present: bool = False,
        validation_stage: str = "PROVIDER_TRANSPORT",
        safe_http_status: int | None = None,
        safe_transport_error_code: str | None = None,
        safe_transport_stage: str | None = None,
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.provider_response_present = provider_response_present
        self.validation_stage = validation_stage
        self.safe_http_status = _safe_http_status(safe_http_status)
        self.safe_transport_error_code = _safe_transport_code(safe_transport_error_code)
        self.safe_transport_stage = _safe_transport_stage(safe_transport_stage)


__all__ = (
    "ModelGatewayError",
    "ModelGatewayProviderFailure",
    "ModelGatewayRejected",
    "ModelGatewayUnavailable",
    "ModelProviderAnswerEmpty",
    "ModelProviderConfigurationError",
    "ModelProviderError",
    "ModelProviderHTTPError",
    "ModelProviderModelUnavailable",
    "ModelProviderNetworkError",
    "ModelProviderNotImplemented",
    "ModelProviderParseError",
    "ModelProviderRateLimited",
    "ModelProviderTimeout",
    "ModelProviderUnauthorized",
)
