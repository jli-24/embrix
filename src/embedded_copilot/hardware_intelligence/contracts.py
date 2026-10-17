from __future__ import annotations

from typing import ClassVar

from pydantic import field_validator

from embedded_copilot.engineering_copilot.common import (
    Fingerprinted,
    confidence,
    finalize,
    ident,
    text,
    tuple_values,
)


class DatasheetEvidence(Fingerprinted):
    evidence_id: str
    project_id: str
    subject: str
    summary: str
    source_reference: str
    confidence: float

    @field_validator("evidence_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, v, info):
        return ident(v, info.field_name)

    @field_validator("subject", "summary", "source_reference", mode="before")
    @classmethod
    def _text(cls, v, info):
        return text(v, info.field_name)

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v):
        return confidence(v)

    @classmethod
    def create(cls, **v):
        return finalize(cls, v)


class GraphProjection(Fingerprinted):
    project_id: str
    relations: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    @field_validator("project_id", mode="before")
    @classmethod
    def _id(cls, v):
        return ident(v, "project_id")

    @field_validator("relations", "evidence_refs", mode="before")
    @classmethod
    def _tuple(cls, v, info):
        return tuple(
            text(x, info.field_name, 512) for x in tuple_values(v, info.field_name)
        )

    @classmethod
    def create(cls, **v):
        return finalize(cls, v)


class HardwareProposal(Fingerprinted):
    _fingerprint_exclude: ClassVar[frozenset[str]] = frozenset({"rationale"})
    proposal_id: str
    project_id: str
    mcu_recommendation: str
    power_architecture: tuple[str, ...] = ()
    interface_design: tuple[str, ...] = ()
    bom_proposal: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    confidence: float
    rationale: str | None = None

    @field_validator("proposal_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, v, info):
        return ident(v, info.field_name)

    @field_validator("mcu_recommendation", "rationale", mode="before")
    @classmethod
    def _text(cls, v, info):
        return None if v is None else text(v, info.field_name)

    @field_validator(
        "power_architecture",
        "interface_design",
        "bom_proposal",
        "evidence_refs",
        mode="before",
    )
    @classmethod
    def _tuples(cls, v, info):
        return tuple(
            text(x, info.field_name, 512) for x in tuple_values(v, info.field_name)
        )

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v):
        return confidence(v)

    @classmethod
    def create(cls, **v):
        return finalize(cls, v)


__all__ = ("DatasheetEvidence", "GraphProjection", "HardwareProposal")
