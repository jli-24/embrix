class EngineeringEDAError(Exception):
    """Base fail-closed EDA boundary error."""


class EDARejected(EngineeringEDAError):
    pass


class EDAConflict(EngineeringEDAError):
    pass


class EDAUnavailable(EngineeringEDAError):
    pass


class EDAApprovalRequired(EDARejected):
    pass


class EDAArtifactDrift(EDARejected):
    pass


class UnsupportedEDAChange(EDARejected):
    pass


class EDAAuditUnavailable(EDAUnavailable):
    pass


__all__ = tuple(
    name
    for name in globals()
    if name.startswith("EDA")
    or name == "EngineeringEDAError"
    or name == "UnsupportedEDAChange"
)
