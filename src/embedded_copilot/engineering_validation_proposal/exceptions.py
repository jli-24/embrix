class ValidationProposalError(RuntimeError):
    """Base error for the canonical v6.4 boundary."""


class ProposalRejected(ValidationProposalError):
    """The request or transition violates a contract."""


class ProposalConflict(ValidationProposalError):
    """The identity already exists with different content or a replay conflicts."""


class ProposalUnavailable(ValidationProposalError):
    """A required production dependency is unavailable."""


__all__ = (
    "ProposalConflict",
    "ProposalRejected",
    "ProposalUnavailable",
    "ValidationProposalError",
)
