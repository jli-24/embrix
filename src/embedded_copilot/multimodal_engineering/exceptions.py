class MultimodalEngineeringError(RuntimeError):
    def __init__(self, message: str = "multimodal engineering unavailable") -> None:
        super().__init__(message)


class MultimodalRejected(MultimodalEngineeringError):
    def __init__(self) -> None:
        super().__init__("multimodal engineering request rejected")


class MultimodalUnavailable(MultimodalEngineeringError):
    pass


class MultimodalEvidenceMissing(MultimodalRejected):
    def __init__(self) -> None:
        MultimodalEngineeringError.__init__(self, "multimodal evidence is missing")


class MultimodalProviderError(MultimodalUnavailable):
    def __init__(self, message: str = "multimodal provider unavailable") -> None:
        super().__init__(message)


class MultimodalProviderAuthenticationError(MultimodalProviderError):
    def __init__(self) -> None:
        super().__init__("multimodal provider authentication failed")


class MultimodalProviderRateLimited(MultimodalProviderError):
    def __init__(self) -> None:
        super().__init__("multimodal provider rate limited")


class MultimodalProviderTimeout(MultimodalProviderError):
    def __init__(self) -> None:
        super().__init__("multimodal provider timed out")


class MultimodalProviderMalformedResponse(MultimodalProviderError):
    def __init__(self) -> None:
        super().__init__("multimodal provider response is invalid")


class MultimodalProviderUnavailable(MultimodalProviderError):
    pass


class MultimodalContextUnavailable(MultimodalEngineeringError):
    def __init__(self) -> None:
        super().__init__("multimodal context unavailable")


class MultimodalAuditUnavailable(MultimodalEngineeringError):
    def __init__(self) -> None:
        super().__init__("multimodal audit unavailable")


class MultimodalAuditConflict(MultimodalEngineeringError):
    def __init__(self) -> None:
        super().__init__("multimodal audit conflict")


__all__ = tuple(name for name in globals() if name.startswith("Multimodal"))
