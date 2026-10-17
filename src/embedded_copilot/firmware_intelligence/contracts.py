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


class FirmwareContext(Fingerprinted):
    project_id: str
    platform: str
    sdk_hal: str
    rtos: str
    clock_assumptions: tuple[str, ...] = ()
    pin_assumptions: tuple[str, ...] = ()
    peripheral_assumptions: tuple[str, ...] = ()
    interrupt_assumptions: tuple[str, ...] = ()

    @field_validator("project_id", mode="before")
    @classmethod
    def _id(cls, v):
        return ident(v, "project_id")

    @field_validator("platform", "sdk_hal", "rtos", mode="before")
    @classmethod
    def _text(cls, v, info):
        return text(v, info.field_name)

    @field_validator(
        "clock_assumptions",
        "pin_assumptions",
        "peripheral_assumptions",
        "interrupt_assumptions",
        mode="before",
    )
    @classmethod
    def _tuples(cls, v, info):
        return tuple(
            text(x, info.field_name, 512) for x in tuple_values(v, info.field_name)
        )

    @classmethod
    def create(cls, **v):
        return finalize(cls, v)


class InterfaceContract(Fingerprinted):
    name: str
    direction: str
    signal: str

    @field_validator("name", "direction", "signal", mode="before")
    @classmethod
    def _text(cls, v, info):
        return text(v, info.field_name)

    @classmethod
    def create(cls, **v):
        return finalize(cls, v)


class FirmwareProposal(Fingerprinted):
    _fingerprint_exclude: ClassVar[frozenset[str]] = frozenset({"rationale"})
    proposal_id: str
    project_id: str
    driver_design: tuple[str, ...]
    task_architecture: tuple[str, ...]
    api_proposal: tuple[str, ...]
    code_skeleton: tuple[str, ...]
    assumptions: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    confidence: float
    rationale: str | None = None

    @field_validator("proposal_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, v, info):
        return ident(v, info.field_name)

    @field_validator(
        "driver_design",
        "task_architecture",
        "api_proposal",
        "code_skeleton",
        "assumptions",
        "evidence_refs",
        mode="before",
    )
    @classmethod
    def _tuples(cls, v, info):
        return tuple(
            text(x, info.field_name, 4096) for x in tuple_values(v, info.field_name)
        )

    @field_validator("rationale", mode="before")
    @classmethod
    def _rationale(cls, v):
        return None if v is None else text(v, "rationale", 4096)

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, v):
        return confidence(v)

    @classmethod
    def create(cls, **v):
        return finalize(cls, v)


__all__ = ("FirmwareContext", "FirmwareProposal", "InterfaceContract")
