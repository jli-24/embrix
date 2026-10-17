class EngineeringTaskRuntimeError(RuntimeError):
    """Base safe error for private engineering-task orchestration."""


class EngineeringTaskRejected(EngineeringTaskRuntimeError):
    """A typed input, plan, context, or role result failed validation."""


class EngineeringTaskUnavailable(EngineeringTaskRuntimeError):
    """An injected read-only dependency was unavailable."""


__all__ = (
    "EngineeringTaskRejected",
    "EngineeringTaskRuntimeError",
    "EngineeringTaskUnavailable",
)
