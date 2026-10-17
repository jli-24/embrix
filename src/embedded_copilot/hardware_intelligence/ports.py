from typing import Protocol, runtime_checkable

from .contracts import HardwareProposal


@runtime_checkable
class HardwareIntelligencePort(Protocol):
    def analyze(
        self, requirement: object, context: object, datasheet: tuple, graph: object
    ) -> HardwareProposal: ...


__all__ = ("HardwareIntelligencePort",)
