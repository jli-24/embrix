"""Compatibility DTOs for the pre-v4.6 API route.

They are intentionally not exported from :mod:`embedded_copilot.engineering_optimization`.
The versioned v2.6 API imports them from ``contracts`` while v4.6 public models
remain evidence-first and do not expose an APPROVED proposal state.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from .contracts import fingerprint, identifier, safe_text, tuple_only, utc_datetime


class _LegacyContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


def _canonical(value: BaseModel, *, exclude: set[str]) -> str:
    encoded = json.dumps(
        value.model_dump(mode="json", exclude=exclude),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class OptimizationCategory(StrEnum):
    POWER = "POWER"
    PERFORMANCE = "PERFORMANCE"
    MEMORY = "MEMORY"
    LATENCY = "LATENCY"
    COMMUNICATION = "COMMUNICATION"
    RELIABILITY = "RELIABILITY"


class OptimizationConfidence(StrEnum):
    VERIFIED = "VERIFIED"
    PROJECTED = "PROJECTED"
    UNVERIFIED = "UNVERIFIED"


class OptimizationStatus(StrEnum):
    IDENTIFIED = "IDENTIFIED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class OptimizationTarget(StrEnum):
    FIRMWARE = "FIRMWARE"
    HARDWARE = "HARDWARE"
    BUILD = "BUILD"
    CONFIGURATION = "CONFIGURATION"
    TEST = "TEST"


class OptimizationFinding(_LegacyContract):
    finding_id: str
    category: OptimizationCategory
    target: OptimizationTarget
    current_state: str
    suggested_direction: str
    risk: str
    confidence: OptimizationConfidence
    evidence_reference: str
    status: OptimizationStatus
    fingerprint: str

    @field_validator("finding_id", "evidence_reference", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("current_state", "suggested_direction", "risk", mode="before")
    @classmethod
    def _text(cls, value: object, info) -> str:
        return safe_text(value, field=info.field_name)

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return fingerprint(value)

    @model_validator(mode="after")
    def _bind(self):
        if self.fingerprint != _canonical(self, exclude={"fingerprint", "status"}):
            raise ValueError("legacy optimization finding fingerprint mismatch")
        return self

    @classmethod
    def create(cls, **values: object):
        provisional = cls.model_construct(
            **{**values, "fingerprint": "sha256:" + "0" * 64}
        )
        return cls.model_validate(
            {
                **values,
                "fingerprint": _canonical(
                    provisional, exclude={"fingerprint", "status"}
                ),
            }
        )


class OptimizationAnalysis(_LegacyContract):
    project_id: str
    findings: tuple[OptimizationFinding, ...]
    fingerprint: str

    @field_validator("project_id", mode="before")
    @classmethod
    def _project(cls, value: object) -> str:
        return identifier(value, field="project_id")

    @field_validator("findings", mode="before")
    @classmethod
    def _findings(cls, value: object) -> tuple[OptimizationFinding, ...]:
        return tuple(
            OptimizationFinding.model_validate(copy.deepcopy(item))
            for item in tuple_only(value, field="findings")
        )

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return fingerprint(value)

    @model_validator(mode="after")
    def _bind(self):
        if len({item.finding_id for item in self.findings}) != len(self.findings):
            raise ValueError("legacy finding ids are not unique")
        if self.fingerprint != _canonical(self, exclude={"fingerprint"}):
            raise ValueError("legacy optimization analysis fingerprint mismatch")
        return self

    @classmethod
    def create(cls, **values: object):
        provisional = cls.model_construct(
            **{**values, "fingerprint": "sha256:" + "0" * 64}
        )
        return cls.model_validate(
            {**values, "fingerprint": _canonical(provisional, exclude={"fingerprint"})}
        )


class OptimizationApprovalRequest(_LegacyContract):
    finding_id: str
    finding_fingerprint: str
    reviewer: str
    decided_at: datetime

    @field_validator("finding_id", "reviewer", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("finding_fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return fingerprint(value, field="finding_fingerprint")

    @field_validator("decided_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value)
            except ValueError as error:
                raise ValueError("decided_at is invalid") from error
        return utc_datetime(value, field="decided_at")


@runtime_checkable
class OptimizationAnalysisPort(Protocol):
    def get_snapshot(self, project_id: str) -> OptimizationAnalysis | None: ...


@runtime_checkable
class OptimizationApprovalPort(Protocol):
    def approve(self, request: OptimizationApprovalRequest) -> OptimizationFinding: ...
    def reject(self, request: OptimizationApprovalRequest) -> OptimizationFinding: ...


def validate_finding(value: object) -> OptimizationFinding:
    if type(value) is not OptimizationFinding:
        raise TypeError("legacy optimization finding is invalid")
    return OptimizationFinding.model_validate(
        copy.deepcopy(value.model_dump(mode="python"))
    )


def validate_analysis(value: object) -> OptimizationAnalysis:
    if type(value) is not OptimizationAnalysis:
        raise TypeError("legacy optimization analysis is invalid")
    return OptimizationAnalysis.model_validate(
        copy.deepcopy(value.model_dump(mode="python"))
    )


__all__ = (
    "OptimizationAnalysis",
    "OptimizationAnalysisPort",
    "OptimizationApprovalPort",
    "OptimizationApprovalRequest",
    "OptimizationCategory",
    "OptimizationConfidence",
    "OptimizationFinding",
    "OptimizationStatus",
    "OptimizationTarget",
    "validate_analysis",
    "validate_finding",
)
