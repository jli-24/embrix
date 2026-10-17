from __future__ import annotations


class AutonomousEngineeringLoopError(RuntimeError):
    """Fixed, sanitized loop boundary error."""


class LoopRejected(AutonomousEngineeringLoopError):
    def __init__(self) -> None:
        super().__init__("autonomous engineering loop request rejected")


class LoopUnavailable(AutonomousEngineeringLoopError):
    def __init__(self) -> None:
        super().__init__("autonomous engineering loop unavailable")


__all__ = ("AutonomousEngineeringLoopError", "LoopRejected", "LoopUnavailable")
