from __future__ import annotations


class RepairProposalError(RuntimeError):
    """Fixed, sanitized repair proposal boundary error."""


class RepairProposalRejected(RepairProposalError):
    def __init__(self) -> None:
        super().__init__("repair proposal request rejected")


__all__ = ("RepairProposalError", "RepairProposalRejected")
