class ExecutionError(Exception):
    """固定的、脱敏的执行边界错误。"""


class ExecutionRejected(ExecutionError):
    pass


class ExecutionUnavailable(ExecutionError):
    pass


__all__ = ("ExecutionError", "ExecutionRejected", "ExecutionUnavailable")
