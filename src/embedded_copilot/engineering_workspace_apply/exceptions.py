class WorkspaceApplyError(Exception):
    """Base error for the v6.5 controlled apply boundary."""


class ApplyRejected(WorkspaceApplyError):
    pass


class ApplyConflict(WorkspaceApplyError):
    pass


class ApplyUnavailable(WorkspaceApplyError):
    pass


class RecoveryRequired(WorkspaceApplyError):
    pass


__all__ = ("ApplyConflict", "ApplyRejected", "ApplyUnavailable", "RecoveryRequired", "WorkspaceApplyError")
