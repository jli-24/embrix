from __future__ import annotations


class EngineeringContextError(Exception):
    """Base error for the read-only context boundary."""


class EngineeringContextRejected(EngineeringContextError):
    """The supplied projection violates the context contract."""

    def __init__(self, message: str = "engineering context projection rejected") -> None:
        super().__init__(message)


class EngineeringContextUnavailable(EngineeringContextError):
    """A required read-only projection is unavailable."""

    def __init__(self, message: str = "engineering context projection unavailable") -> None:
        super().__init__(message)


class EngineeringContextExternalEvidenceUnavailable(EngineeringContextUnavailable):
    """An explicitly requested external evidence source is unavailable."""


__all__ = (
    "EngineeringContextError",
    "EngineeringContextExternalEvidenceUnavailable",
    "EngineeringContextRejected",
    "EngineeringContextUnavailable",
)
