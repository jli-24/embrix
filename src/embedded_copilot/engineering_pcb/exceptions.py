class EngineeringPCBError(Exception):
    """Base error for the v4.3 PCB bounded context."""


class PCBContractError(EngineeringPCBError):
    pass


class PCBArtifactNotFound(EngineeringPCBError):
    pass


class PCBParseError(EngineeringPCBError):
    pass


class PCBUnsupportedFormat(PCBParseError):
    pass


class PCBSourceDrift(PCBParseError):
    pass


class PCBModelConflict(EngineeringPCBError):
    pass


class PCBUnavailable(EngineeringPCBError):
    pass


class PCBGovernanceError(EngineeringPCBError):
    pass


class PCBConflict(EngineeringPCBError):
    pass


class PCBReviewRejected(EngineeringPCBError):
    pass
