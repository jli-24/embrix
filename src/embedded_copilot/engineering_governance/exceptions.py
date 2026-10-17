from __future__ import annotations


class KnowledgeGovernanceError(Exception):
    """Base error for the isolated knowledge governance boundary."""


class GovernanceRejected(KnowledgeGovernanceError):
    """The request or state transition failed closed."""


class GovernanceConflict(KnowledgeGovernanceError):
    """The request conflicts with an existing immutable record."""


class GovernanceUnavailable(KnowledgeGovernanceError):
    """The injected storage or sink could not complete the operation."""


class CandidateNotFound(GovernanceRejected):
    """The requested candidate does not exist."""


__all__ = (
    "CandidateNotFound",
    "GovernanceConflict",
    "GovernanceRejected",
    "GovernanceUnavailable",
    "KnowledgeGovernanceError",
)
