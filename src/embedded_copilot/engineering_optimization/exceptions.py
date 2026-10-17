class EngineeringOptimizationError(Exception):
    pass


class OptimizationRejected(EngineeringOptimizationError):
    pass


class OptimizationApprovalRequired(EngineeringOptimizationError):
    pass


class OptimizationUnavailable(EngineeringOptimizationError):
    pass


class OptimizationAuditUnavailable(EngineeringOptimizationError):
    pass


class OptimizationConflict(EngineeringOptimizationError):
    pass
