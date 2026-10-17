from typing import Protocol, runtime_checkable

from .contracts import FirmwareProposal


@runtime_checkable
class FirmwareIntelligencePort(Protocol):
    def plan(
        self,
        requirement: object,
        hardware_context: object,
        interfaces: tuple,
        evidence_refs: tuple[str, ...],
    ) -> FirmwareProposal: ...


__all__ = ("FirmwareIntelligencePort",)
