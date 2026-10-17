class AgentRuntimeError(RuntimeError):
    def __init__(self, message: str = "agent capability runtime unavailable") -> None:
        super().__init__(message)


class CapabilityRejected(AgentRuntimeError):
    def __init__(self, message: str = "capability request rejected") -> None:
        super().__init__(message)


class CapabilityUnavailable(AgentRuntimeError):
    pass


class CapabilityBindingRejected(CapabilityRejected):
    pass


class CapabilityBindingUnavailable(CapabilityUnavailable):
    pass


class CapabilityPolicyRejected(CapabilityRejected):
    pass


class CapabilityRegistryError(AgentRuntimeError):
    pass


class CapabilityAuditUnavailable(AgentRuntimeError):
    def __init__(self) -> None:
        super().__init__("capability audit unavailable")


class CapabilityAuditConflict(CapabilityAuditUnavailable):
    def __init__(self) -> None:
        AgentRuntimeError.__init__(self, "capability audit conflict")


__all__ = tuple(name for name in globals() if name.startswith(("Agent", "Capability")))
