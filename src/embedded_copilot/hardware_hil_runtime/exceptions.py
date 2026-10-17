class HardwareHILError(Exception):
    """Base exception for the v4.5 HIL governance boundary."""


class HILRejected(HardwareHILError):
    pass


class HILApprovalRequired(HardwareHILError):
    pass


class HILUnavailable(HardwareHILError):
    pass


class HILConflict(HardwareHILError):
    pass


class HILAuditUnavailable(HardwareHILError):
    pass
