from __future__ import annotations


class EngineeringValidationError(Exception):
    """Base error for the isolated engineering validation boundary."""


class ValidationRejected(EngineeringValidationError):
    """The request or a returned finding violates a validation boundary."""


class ValidationConflict(EngineeringValidationError):
    """An immutable finding or report identity conflicts with stored content."""


class ValidationUnavailable(EngineeringValidationError):
    """An injected rule or store could not complete safely."""


__all__ = (
    "EngineeringValidationError",
    "ValidationConflict",
    "ValidationRejected",
    "ValidationUnavailable",
)
