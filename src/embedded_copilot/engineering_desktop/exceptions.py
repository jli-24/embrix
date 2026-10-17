class EngineeringDesktopError(Exception):
    pass


class DesktopRejected(EngineeringDesktopError):
    pass


class DesktopApplicationRejected(DesktopRejected):
    """The bound Application Runtime rejected the question."""


class DesktopApplicationFailed(EngineeringDesktopError):
    """The bound Application Runtime failed without an answer."""


class DesktopConflict(EngineeringDesktopError):
    pass


class DesktopUnavailable(EngineeringDesktopError):
    pass


class DesktopAuditUnavailable(EngineeringDesktopError):
    pass
