from __future__ import annotations


class FailureAnalysisError(RuntimeError):
    """Fixed, sanitized failure-analysis boundary error."""


class FailureAnalysisRejected(FailureAnalysisError):
    def __init__(self) -> None:
        super().__init__("failure analysis request rejected")


class FailureAnalysisUnavailable(FailureAnalysisError):
    def __init__(self) -> None:
        super().__init__("failure analysis unavailable")


__all__ = (
    "FailureAnalysisError",
    "FailureAnalysisRejected",
    "FailureAnalysisUnavailable",
)
