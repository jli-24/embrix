from __future__ import annotations

import copy
import hashlib
from enum import StrEnum

from pydantic import field_validator, model_validator

from embedded_copilot.engineering_context import (
    ContextSourceReference,
    ContextVerificationStatus,
)
from embedded_copilot.engineering_copilot.common import (
    Fingerprinted,
    canonical,
    confidence,
    finalize,
    fp,
    ident,
    text,
    tuple_values,
)
from embedded_copilot.execution import (
    BuildResult,
    DebugObservation,
    DebugSeverity,
    ExecutionStatus,
    FlashResult,
)


class FailureSource(StrEnum):
    BUILD = "BUILD"
    FLASH = "FLASH"
    DEBUG = "DEBUG"


class FailureCategory(StrEnum):
    BUILD_FAILED = "BUILD_FAILED"
    BUILD_TIMEOUT = "BUILD_TIMEOUT"
    FLASH_FAILED = "FLASH_FAILED"
    FLASH_TIMEOUT = "FLASH_TIMEOUT"
    UART_ERROR = "UART_ERROR"
    EXECUTION_UNAVAILABLE = "EXECUTION_UNAVAILABLE"
    MALFORMED_OBSERVATION = "MALFORMED_OBSERVATION"


class FailureContract(Fingerprinted):
    pass


def _observation_id(project_id: str, source: FailureSource, source_fp: str) -> str:
    digest = hashlib.sha256(
        f"{project_id}:{source.value}:{source_fp}".encode()
    ).hexdigest()
    return f"failure-observation-{digest[:32]}"


class FailureObservation(FailureContract):
    observation_id: str
    project_id: str
    target_id: str
    source: FailureSource
    source_id: str
    source_fingerprint: str
    status: ExecutionStatus
    category: FailureCategory
    signal_refs: tuple[str, ...]
    exit_code: int | None = None
    severity: DebugSeverity | None = None

    @field_validator(
        "observation_id", "project_id", "target_id", "source_id", mode="before"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator("source_fingerprint", "fingerprint", mode="before")
    @classmethod
    def _fingerprints(cls, value: object, info) -> str:
        return fp(value, info.field_name)

    @field_validator("signal_refs", mode="before")
    @classmethod
    def _signals(cls, value: object) -> tuple[str, ...]:
        refs = tuple(
            ident(item, "signal_ref") for item in tuple_values(value, "signal_refs")
        )
        if len(refs) != len(set(refs)):
            raise ValueError("signal refs must be unique")
        return tuple(sorted(refs))

    @model_validator(mode="after")
    def _valid_failure(self) -> FailureObservation:
        if self.source is FailureSource.DEBUG:
            if self.severity is None:
                raise ValueError("debug observation severity is required")
        elif self.exit_code is None and self.status not in {
            ExecutionStatus.TIMEOUT,
            ExecutionStatus.UNAVAILABLE,
            ExecutionStatus.REJECTED,
        }:
            raise ValueError("execution failure exit code is required")
        return self

    @classmethod
    def create(cls, **values: object) -> FailureObservation:
        return finalize(cls, values)

    @classmethod
    def from_build(
        cls, result: BuildResult, *, project_id: str, target_id: str
    ) -> FailureObservation:
        checked = BuildResult.model_validate(
            copy.deepcopy(result.model_dump(mode="python"))
        )
        if checked.status is ExecutionStatus.SUCCEEDED:
            raise ValueError("successful build is not a failure")
        category = (
            FailureCategory.BUILD_TIMEOUT
            if checked.status is ExecutionStatus.TIMEOUT
            else FailureCategory.BUILD_FAILED
            if checked.status is ExecutionStatus.FAILED
            else FailureCategory.EXECUTION_UNAVAILABLE
        )
        return cls.create(
            observation_id=_observation_id(
                project_id, FailureSource.BUILD, checked.fingerprint
            ),
            project_id=project_id,
            target_id=target_id,
            source=FailureSource.BUILD,
            source_id=checked.request_id,
            source_fingerprint=checked.fingerprint,
            status=checked.status,
            category=category,
            signal_refs=(f"build-result-{checked.request_id}",),
            exit_code=checked.exit_code,
        )

    @classmethod
    def from_flash(
        cls, result: FlashResult, *, project_id: str, target_id: str
    ) -> FailureObservation:
        checked = FlashResult.model_validate(
            copy.deepcopy(result.model_dump(mode="python"))
        )
        if checked.status is ExecutionStatus.SUCCEEDED:
            raise ValueError("successful flash is not a failure")
        category = (
            FailureCategory.FLASH_TIMEOUT
            if checked.status is ExecutionStatus.TIMEOUT
            else FailureCategory.FLASH_FAILED
            if checked.status is ExecutionStatus.FAILED
            else FailureCategory.EXECUTION_UNAVAILABLE
        )
        return cls.create(
            observation_id=_observation_id(
                project_id, FailureSource.FLASH, checked.fingerprint
            ),
            project_id=project_id,
            target_id=target_id,
            source=FailureSource.FLASH,
            source_id=checked.request_id,
            source_fingerprint=checked.fingerprint,
            status=checked.status,
            category=category,
            signal_refs=(f"flash-result-{checked.request_id}",),
            exit_code=checked.exit_code,
        )

    @classmethod
    def from_debug(cls, result: DebugObservation) -> FailureObservation:
        checked = DebugObservation.model_validate(
            copy.deepcopy(result.model_dump(mode="python"))
        )
        if checked.severity is DebugSeverity.INFO:
            raise ValueError("informational debug observation is not a failure")
        return cls.create(
            observation_id=_observation_id(
                checked.project_id, FailureSource.DEBUG, checked.fingerprint
            ),
            project_id=checked.project_id,
            target_id=checked.target_id,
            source=FailureSource.DEBUG,
            source_id=checked.observation_id,
            source_fingerprint=checked.fingerprint,
            status=ExecutionStatus.FAILED,
            category=FailureCategory.UART_ERROR,
            signal_refs=(f"debug-observation-{checked.observation_id}",),
            severity=checked.severity,
        )


class FailureAnalysisNote(FailureContract):
    note: str
    source: str = "MODEL"

    @field_validator("note", "source", mode="before")
    @classmethod
    def _text(cls, value: object, info) -> str:
        return text(value, info.field_name, 2048)

    @classmethod
    def create(cls, **values: object) -> FailureAnalysisNote:
        return finalize(cls, values)


class FailureAnalysis(FailureContract):
    _fingerprint_exclude = frozenset({"note"})
    failure_id: str
    project_id: str
    target_id: str
    category: FailureCategory
    observation_fingerprint: str
    context_fingerprint: str
    root_cause_candidate: str
    evidence_refs: tuple[str, ...]
    confidence: float
    provenance: tuple[ContextSourceReference, ...]
    verification_status: ContextVerificationStatus
    note: FailureAnalysisNote | None = None

    @field_validator("failure_id", "project_id", "target_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return ident(value, info.field_name)

    @field_validator("observation_fingerprint", "context_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fp(value, info.field_name)

    @field_validator("root_cause_candidate", mode="before")
    @classmethod
    def _root_cause(cls, value: object) -> str:
        return text(value, "root_cause_candidate", 1024)

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence(cls, value: object) -> tuple[str, ...]:
        refs = tuple(
            ident(item, "evidence_ref") for item in tuple_values(value, "evidence_refs")
        )
        if not refs:
            raise ValueError("failure analysis requires evidence")
        return tuple(sorted(set(refs)))

    @field_validator("provenance", mode="before")
    @classmethod
    def _provenance(cls, value: object) -> tuple[ContextSourceReference, ...]:
        return tuple(
            ContextSourceReference.model_validate(copy.deepcopy(item))
            for item in tuple_values(value, "provenance")
        )

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: object) -> float:
        return confidence(value)

    @field_validator("verification_status", mode="before")
    @classmethod
    def _verification_status(cls, value: object) -> ContextVerificationStatus:
        return ContextVerificationStatus(value)

    @classmethod
    def create(cls, **values: object) -> FailureAnalysis:
        material = copy.deepcopy(values)
        material["verification_status"] = ContextVerificationStatus(
            material["verification_status"]
        )
        material.setdefault("fingerprint", "sha256:" + "0" * 64)
        provisional = cls.model_construct(**material)
        material["fingerprint"] = canonical(
            provisional, exclude={"fingerprint", "note"}
        )
        return cls.model_validate(material)


__all__ = (
    "FailureAnalysis",
    "FailureAnalysisNote",
    "FailureCategory",
    "FailureContract",
    "FailureObservation",
    "FailureSource",
)
