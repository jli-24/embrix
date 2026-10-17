class CopilotEnvironmentError(Exception):
    pass


class EnvironmentRejected(CopilotEnvironmentError):
    pass


class EnvironmentConflict(CopilotEnvironmentError):
    pass


class EnvironmentUnavailable(CopilotEnvironmentError):
    pass
