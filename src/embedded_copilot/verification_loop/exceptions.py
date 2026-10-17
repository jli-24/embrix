from __future__ import annotations


class VerificationLoopError(RuntimeError):
    """Fixed, sanitized verification boundary error."""


class VerificationRejected(VerificationLoopError):
    def __init__(self) -> None:
        super().__init__("verification request rejected")


__all__ = ("VerificationLoopError", "VerificationRejected")
