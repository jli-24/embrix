from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import field_validator

from embedded_copilot.engineering_copilot.common import (
    Fingerprinted,
    finalize,
    fp,
    ident,
    text,
    tuple_values,
)


class RepairTargetType(StrEnum):
    FIRMWARE_CHANGE = "firmware_change"
    CONFIGURATION_CHANGE = "configuration_change"
    TEST_CHANGE = "test_change"
    HARDWARE_REVIEW = "hardware_review"


class RepairContract(Fingerprinted):
    pass


class RepairProposal(RepairContract):
    proposal_id: str
    project_id: str
    failure_id: str
    failure_analysis_fingerprint: str
    context_fingerprint: str
    target_type: RepairTargetType
    change_summary: str
    affected_component: str
    evidence_refs: tuple[str, ...]
    confidence: float

    @field_validator(
        "proposal_id", "project_id", "failure_id", "affected_component", mode="before"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator(
        "failure_analysis_fingerprint", "context_fingerprint", mode="before"
    )
    @classmethod
    def _fingerprints(cls, value: object, info) -> str:
        return fp(value, info.field_name)

    @field_validator("change_summary", mode="before")
    @classmethod
    def _summary(cls, value: object) -> str:
        return text(value, "change_summary", 1024)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence(cls, value: object) -> tuple[str, ...]:
        refs = tuple(
            ident(item, "evidence_ref") for item in tuple_values(value, "evidence_refs")
        )
        if not refs:
            raise ValueError("repair proposal requires evidence")
        return tuple(sorted(set(refs)))

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: object) -> float:
        if type(value) is not float or not 0.0 <= value <= 1.0:
            raise ValueError("confidence is invalid")
        return value

    @classmethod
    def create(cls, **values: object) -> RepairProposal:
        return finalize(cls, values)


def proposal_id(
    failure_id: str, analysis_fingerprint: str, target: RepairTargetType
) -> str:
    digest = hashlib.sha256(
        f"{failure_id}:{analysis_fingerprint}:{target.value}".encode()
    ).hexdigest()
    return f"repair-{digest[:32]}"


__all__ = ("RepairContract", "RepairProposal", "RepairTargetType", "proposal_id")
