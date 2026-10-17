class EngineeringRepairError(Exception):
    """Base error for the v4.2 repair governance boundary."""


class RepairRejected(EngineeringRepairError):
    pass


class RepairConflict(EngineeringRepairError):
    pass


class RepairUnavailable(EngineeringRepairError):
    pass


class FailureNotFound(RepairUnavailable):
    pass


class ArtifactNotFound(RepairUnavailable):
    pass


class EvidenceMissing(RepairRejected):
    pass


class ReviewNotFound(RepairUnavailable):
    pass


class ApprovalRejected(RepairRejected):
    pass


class TargetDrift(RepairRejected):
    pass


class ApplyFailed(RepairUnavailable):
    pass


class BuildFailed(RepairUnavailable):
    pass


class VerificationFailed(RepairUnavailable):
    pass


class AuditWriteFailed(RepairUnavailable):
    pass


__all__ = (
    "ApplyFailed",
    "ApprovalRejected",
    "ArtifactNotFound",
    "AuditWriteFailed",
    "BuildFailed",
    "EngineeringRepairError",
    "EvidenceMissing",
    "FailureNotFound",
    "RepairConflict",
    "RepairRejected",
    "RepairUnavailable",
    "ReviewNotFound",
    "TargetDrift",
    "VerificationFailed",
)
