class EngineeringWorkspaceError(Exception):
    pass


class WorkspaceRejected(EngineeringWorkspaceError):
    pass


class WorkspaceConflict(EngineeringWorkspaceError):
    pass


class WorkspaceUnavailable(EngineeringWorkspaceError):
    pass
