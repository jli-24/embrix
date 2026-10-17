from __future__ import annotations


class EngineeringReviewError(Exception):
    """Base error for the isolated engineering review boundary."""


class ReviewRejected(EngineeringReviewError):
    """A request, decision, or target identity failed closed."""


class ReviewConflict(EngineeringReviewError):
    """An immutable review identity conflicts with stored content."""


class ReviewUnavailable(EngineeringReviewError):
    """An injected source, store, or audit sink could not complete safely."""


class ReviewTargetNotFound(ReviewRejected):
    """The requested review target does not exist."""


class ReviewNotFound(ReviewRejected):
    """The requested review does not exist."""


__all__ = (
    "EngineeringReviewError",
    "ReviewConflict",
    "ReviewNotFound",
    "ReviewRejected",
    "ReviewTargetNotFound",
    "ReviewUnavailable",
)

