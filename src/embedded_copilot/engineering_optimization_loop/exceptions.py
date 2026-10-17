from __future__ import annotations


class EngineeringOptimizationLoopError(Exception):
    """Base error for the bounded optimization loop service."""


class OptimizationRejected(EngineeringOptimizationLoopError):
    """The request or generated proposal crossed a validated boundary."""


class OptimizationConflict(EngineeringOptimizationLoopError):
    """A stored proposal identity conflicts with different content."""


class OptimizationUnavailable(EngineeringOptimizationLoopError):
    """A required optimization dependency failed closed."""


__all__ = (
    "EngineeringOptimizationLoopError",
    "OptimizationConflict",
    "OptimizationRejected",
    "OptimizationUnavailable",
)

