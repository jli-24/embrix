from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class EDACapabilityStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class EDAValidationEvidence:
    """Evidence emitted only by a real KiCad 10 validation adapter."""

    tool_version: str
    drc_completed: bool
    erc_completed: bool

    @property
    def is_kicad10(self) -> bool:
        return self.tool_version.startswith("kicad-10.")

    @property
    def drc_available(self) -> bool:
        return self.is_kicad10 and self.drc_completed

    @property
    def erc_available(self) -> bool:
        return self.is_kicad10 and self.erc_completed


@dataclass(frozen=True, slots=True)
class EDACapabilityProjection:
    changeset: EDACapabilityStatus
    drc: EDACapabilityStatus
    erc: EDACapabilityStatus
    apply: EDACapabilityStatus


def project_eda_capabilities(*, structured_changeset: bool, evidence: EDAValidationEvidence | None) -> EDACapabilityProjection:
    if evidence is None:
        return EDACapabilityProjection(
            changeset=EDACapabilityStatus.PARTIAL if structured_changeset else EDACapabilityStatus.UNAVAILABLE,
            drc=EDACapabilityStatus.UNAVAILABLE,
            erc=EDACapabilityStatus.UNAVAILABLE,
            apply=EDACapabilityStatus.UNAVAILABLE,
        )
    drc = EDACapabilityStatus.AVAILABLE if evidence.drc_available else EDACapabilityStatus.UNAVAILABLE
    erc = EDACapabilityStatus.AVAILABLE if evidence.erc_available else EDACapabilityStatus.UNAVAILABLE
    apply_status = EDACapabilityStatus.AVAILABLE if evidence.drc_available and evidence.erc_available else EDACapabilityStatus.UNAVAILABLE
    return EDACapabilityProjection(
        changeset=EDACapabilityStatus.PARTIAL if structured_changeset else EDACapabilityStatus.UNAVAILABLE,
        drc=drc,
        erc=erc,
        apply=apply_status,
    )


__all__ = ("EDACapabilityProjection", "EDACapabilityStatus", "EDAValidationEvidence", "project_eda_capabilities")
