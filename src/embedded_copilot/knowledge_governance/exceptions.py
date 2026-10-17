from __future__ import annotations


class KnowledgeGovernanceError(RuntimeError):
    pass


class GovernanceRejected(KnowledgeGovernanceError):
    def __init__(self) -> None:
        super().__init__("knowledge governance request rejected")


class GovernanceConflictError(KnowledgeGovernanceError):
    def __init__(self) -> None:
        super().__init__("knowledge governance state conflict")


class GovernanceUnavailable(KnowledgeGovernanceError):
    def __init__(self) -> None:
        super().__init__("knowledge governance unavailable")


__all__ = ("GovernanceConflictError", "GovernanceRejected", "GovernanceUnavailable", "KnowledgeGovernanceError")
