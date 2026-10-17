from __future__ import annotations

import copy

from pydantic import field_validator

from embedded_copilot.engineering_copilot.common import (
    Fingerprinted,
    confidence,
    finalize,
    ident,
    text,
    tuple_values,
)
from embedded_copilot.pcb.models import UnifiedPCBModel


class PCBInputBundle(Fingerprinted):
    project_id: str
    pcb_model: UnifiedPCBModel
    schematic: tuple[str, ...] = ()
    bom: tuple[str, ...] = ()
    netlist: tuple[str, ...] = ()

    @field_validator("project_id", mode="before")
    @classmethod
    def _id(cls, v):
        return ident(v, "project_id")

    @field_validator("pcb_model", mode="before")
    @classmethod
    def _model(cls, v):
        return UnifiedPCBModel.model_validate(
            copy.deepcopy(
                v.model_dump(mode="python") if isinstance(v, UnifiedPCBModel) else v
            )
        )

    @field_validator("schematic", "bom", "netlist", mode="before")
    @classmethod
    def _tuples(cls, v, info):
        return tuple(
            text(x, info.field_name, 2048) for x in tuple_values(v, info.field_name)
        )

    @classmethod
    def create(cls, **v):
        return finalize(cls, v)


class PCBConnectivityGraph(Fingerprinted):
    project_id: str
    components: tuple[str, ...]
    nets: tuple[str, ...]
    connections: tuple[str, ...] = ()
    interfaces: tuple[str, ...] = ()

    @field_validator("project_id", mode="before")
    @classmethod
    def _id(cls, v):
        return ident(v, "project_id")

    @field_validator("components", "nets", "connections", "interfaces", mode="before")
    @classmethod
    def _tuples(cls, v, info):
        return tuple(
            text(x, info.field_name, 256) for x in tuple_values(v, info.field_name)
        )

    @classmethod
    def create(cls, **v):
        return finalize(cls, v)


class PCBIssue(Fingerprinted):
    issue_id: str
    project_id: str
    severity: str
    category: str
    description: str
    evidence_refs: tuple[str, ...] = ()
    suggestion: str

    @field_validator("issue_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, v, info):
        return ident(v, info.field_name)

    @field_validator("severity", "category", "description", "suggestion", mode="before")
    @classmethod
    def _text(cls, v, info):
        return text(v, info.field_name)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _refs(cls, v):
        return tuple(
            text(x, "evidence_ref", 256) for x in tuple_values(v, "evidence_refs")
        )

    @classmethod
    def create(cls, **v):
        return finalize(cls, v)


class SchematicProposal(Fingerprinted):
    proposal_id: str
    project_id: str
    connections: tuple[str, ...]
    evidence_refs: tuple[str, ...] = ()
    confidence: float

    @field_validator("proposal_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, v, info):
        return ident(v, info.field_name)

    @field_validator("connections", "evidence_refs", mode="before")
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


__all__ = ("PCBConnectivityGraph", "PCBInputBundle", "PCBIssue", "SchematicProposal")
