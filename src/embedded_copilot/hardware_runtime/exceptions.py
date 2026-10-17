class HardwareRuntimeError(Exception):
    """Base fail-closed hardware runtime error."""


class HardwareRejected(HardwareRuntimeError):
    pass


class HardwareConflict(HardwareRuntimeError):
    pass


class HardwareUnavailable(HardwareRuntimeError):
    pass


class HardwareApprovalRequired(HardwareRejected):
    pass


class HardwareResourceDenied(HardwareRejected):
    pass


class HardwareAuditUnavailable(HardwareUnavailable):
    pass


__all__ = tuple(name for name in globals() if name.startswith("Hardware"))
