from __future__ import annotations


class EngineeringEvolutionError(Exception):
    """Base error for the isolated engineering evolution boundary."""


class EvolutionRejected(EngineeringEvolutionError):
    """The request or lifecycle transition failed closed."""


class EvolutionConflict(EngineeringEvolutionError):
    """The request conflicts with an immutable record or terminal state."""


class EvolutionUnavailable(EngineeringEvolutionError):
    """An injected source, store, or sink could not complete safely."""


class ApprovedKnowledgeNotFound(EvolutionRejected):
    """The requested approved knowledge does not exist."""


class EvolutionProposalNotFound(EvolutionRejected):
    """The requested evolution proposal does not exist."""


__all__ = (
    "ApprovedKnowledgeNotFound",
    "EngineeringEvolutionError",
    "EvolutionConflict",
    "EvolutionProposalNotFound",
    "EvolutionRejected",
    "EvolutionUnavailable",
)
