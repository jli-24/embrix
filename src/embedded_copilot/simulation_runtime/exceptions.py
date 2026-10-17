class SimulationRuntimeError(RuntimeError):
    pass


class SimulationRejected(SimulationRuntimeError):
    pass


class SimulationUnavailable(SimulationRuntimeError):
    pass


__all__ = ("SimulationRejected", "SimulationRuntimeError", "SimulationUnavailable")
