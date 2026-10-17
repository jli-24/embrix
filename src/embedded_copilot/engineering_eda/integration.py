from __future__ import annotations

"""Typed EDA integration boundary.

This module deliberately contains no agent loop, persistence, shell construction,
or provider client.  Backends receive a validated operation and return typed
artifact projections.  The integration layer owns the read-first/read-back
invariant; Engineering Core remains the owner of truth and verification.
"""

from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import field_validator, model_validator

from .exceptions import EDARejected, EDAUnavailable
from .contracts import FingerprintedEDAContract, fingerprint, identifier, utc_datetime
from .models import EDAArtifactReference


class EDABackend(StrEnum):
    KICAD = "KICAD"
    JLCEDA = "JLCEDA"
    ALTIUM = "ALTIUM"


class EDAOperationKind(StrEnum):
    READ_PROJECT = "READ_PROJECT"
    READ_SCHEMATIC = "READ_SCHEMATIC"
    READ_PCB = "READ_PCB"
    READ_BACK = "READ_BACK"
    WRITE_BOARD = "WRITE_BOARD"
    VALIDATE_ERC = "VALIDATE_ERC"
    VALIDATE_DRC = "VALIDATE_DRC"
    EXPORT_GERBER = "EXPORT_GERBER"
    EXPORT_DRILL = "EXPORT_DRILL"
    EXPORT_STEP = "EXPORT_STEP"


class EDAExecutionStatus(StrEnum):
    EXECUTION_SUCCESS = "EXECUTION_SUCCESS"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    EXECUTION_UNAVAILABLE = "EXECUTION_UNAVAILABLE"
    EXECUTION_BLOCKED = "EXECUTION_BLOCKED"


class EDAReadBackStatus(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    READ_BACK_PASS = "READ_BACK_PASS"
    READ_BACK_FAIL = "READ_BACK_FAIL"
    READ_BACK_UNAVAILABLE = "READ_BACK_UNAVAILABLE"


class EDAVerificationStatus(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    VERIFICATION_PASS = "VERIFICATION_PASS"
    VERIFICATION_FAIL = "VERIFICATION_FAIL"
    VERIFICATION_UNAVAILABLE = "VERIFICATION_UNAVAILABLE"


_MUTATING = frozenset({EDAOperationKind.WRITE_BOARD})


class EDAOperation(FingerprintedEDAContract):
    """A backend-neutral operation; raw commands and scripts are forbidden."""

    operation_id: str
    backend: EDABackend
    kind: EDAOperationKind
    project_id: str
    source_artifact: EDAArtifactReference
    approval_fingerprint: str | None = None
    expected_after_fingerprint: str | None = None
    requested_at: datetime

    @field_validator("operation_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("source_artifact", mode="before")
    @classmethod
    def _artifact(cls, value: object) -> EDAArtifactReference:
        if type(value) is EDAArtifactReference:
            return value
        if isinstance(value, dict):
            return EDAArtifactReference.model_validate(value)
        raise TypeError("source_artifact is invalid")

    @field_validator("approval_fingerprint", "expected_after_fingerprint", mode="before")
    @classmethod
    def _optional_fps(cls, value: object, info) -> str | None:
        return None if value is None else fingerprint(value, field=info.field_name)

    @field_validator("requested_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="requested_at")

    @model_validator(mode="after")
    def _binding(self) -> EDAOperation:
        if self.source_artifact.project_id != self.project_id:
            raise ValueError("EDA operation project mismatch")
        if self.kind in _MUTATING and self.approval_fingerprint is None:
            raise ValueError("mutating EDA operation requires current approval")
        if self.kind not in _MUTATING and self.expected_after_fingerprint is not None:
            raise ValueError("expected after fingerprint is only valid for mutation")
        return self


class EDAOperationReceipt(FingerprintedEDAContract):
    """Execution + read-back + verification closure for one typed operation."""

    operation_id: str
    backend: EDABackend
    operation_fingerprint: str
    artifact_before: EDAArtifactReference
    artifact_after: EDAArtifactReference
    approval_fingerprint: str | None
    execution_status: EDAExecutionStatus
    readback_status: EDAReadBackStatus
    verification_status: EDAVerificationStatus
    readback_fingerprint: str | None
    verification_fingerprint: str | None
    created_at: datetime

    @field_validator("operation_id", mode="before")
    @classmethod
    def _operation_id(cls, value: object) -> str:
        return identifier(value, field="operation_id")

    @field_validator("operation_fingerprint", "approval_fingerprint", "readback_fingerprint", "verification_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str | None:
        return None if value is None else fingerprint(value, field=info.field_name)

    @field_validator("artifact_before", "artifact_after", mode="before")
    @classmethod
    def _artifacts(cls, value: object) -> EDAArtifactReference:
        if type(value) is EDAArtifactReference:
            return value
        if isinstance(value, dict):
            return EDAArtifactReference.model_validate(value)
        raise TypeError("EDA receipt artifact is invalid")

    @field_validator("created_at", mode="before")
    @classmethod
    def _created(cls, value: object) -> datetime:
        return utc_datetime(value, field="created_at")

    @model_validator(mode="after")
    def _closure(self) -> EDAOperationReceipt:
        if self.artifact_before.project_id != self.artifact_after.project_id:
            raise ValueError("EDA receipt artifact project mismatch")
        if self.execution_status is not EDAExecutionStatus.EXECUTION_SUCCESS:
            if self.readback_status is EDAReadBackStatus.READ_BACK_PASS or self.verification_status is EDAVerificationStatus.VERIFICATION_PASS:
                raise ValueError("failed execution cannot have passing closure")
        if self.readback_status is EDAReadBackStatus.READ_BACK_PASS:
            if self.readback_fingerprint != self.artifact_after.content_fingerprint:
                raise ValueError("read-back fingerprint mismatch")
        elif self.readback_fingerprint is not None:
            raise ValueError("read-back fingerprint requires a passing read-back")
        if self.verification_status is EDAVerificationStatus.VERIFICATION_PASS and self.readback_status is not EDAReadBackStatus.READ_BACK_PASS:
            raise ValueError("verification pass requires read-back pass")
        return self


@runtime_checkable
class EDABackendPort(Protocol):
    backend: EDABackend

    def read(self, operation: EDAOperation) -> EDAArtifactReference: ...

    def execute(self, operation: EDAOperation, current: EDAArtifactReference) -> EDAArtifactReference: ...


class EDAIntegrationLayer:
    """Read-first/write-second orchestration over an injected backend."""

    def __init__(self, backend: EDABackendPort) -> None:
        if not isinstance(backend, EDABackendPort):
            raise TypeError("EDA backend is invalid")
        self._backend = backend

    def read(self, operation: EDAOperation) -> EDAArtifactReference:
        checked = EDAOperation.model_validate(operation.model_dump(mode="python"), strict=False)
        if checked.backend is not self._backend.backend:
            raise EDARejected("EDA backend mismatch")
        current = self._backend.read(checked)
        if current.project_id != checked.project_id or current.artifact_id != checked.source_artifact.artifact_id:
            raise EDARejected("EDA read artifact identity mismatch")
        return EDAArtifactReference.model_validate(current.model_dump(mode="python"), strict=False)

    def write_then_read_back(
        self,
        operation: EDAOperation,
        *,
        verification_fingerprint: str | None = None,
        now: datetime,
    ) -> EDAOperationReceipt:
        checked = EDAOperation.model_validate(operation.model_dump(mode="python"), strict=False)
        if checked.kind not in _MUTATING:
            raise EDARejected("operation is not a mutation")
        before = self.read(checked)
        if before.content_fingerprint != checked.source_artifact.content_fingerprint:
            raise EDARejected("EDA source artifact drift")
        try:
            after = self._backend.execute(checked, before)
            after = EDAArtifactReference.model_validate(after.model_dump(mode="python"), strict=False)
        except EDAUnavailable:
            raise
        except Exception as error:
            raise EDAUnavailable("EDA backend execution unavailable") from error
        if (
            checked.expected_after_fingerprint is not None
            and after.content_fingerprint != checked.expected_after_fingerprint
        ):
            raise EDARejected("EDA backend output does not match approved expected artifact")
        readback = self._backend.read(checked)
        readback = EDAArtifactReference.model_validate(readback.model_dump(mode="python"), strict=False)
        readback_pass = readback.content_fingerprint == after.content_fingerprint
        readback_status = EDAReadBackStatus.READ_BACK_PASS if readback_pass else EDAReadBackStatus.READ_BACK_FAIL
        verification_status = (
            EDAVerificationStatus.VERIFICATION_PASS
            if readback_pass and verification_fingerprint is not None
            else EDAVerificationStatus.VERIFICATION_UNAVAILABLE
            if readback_pass
            else EDAVerificationStatus.NOT_APPLICABLE
        )
        return EDAOperationReceipt(
            operation_id=checked.operation_id,
            backend=checked.backend,
            operation_fingerprint=checked.fingerprint,
            artifact_before=before,
            artifact_after=after,
            approval_fingerprint=checked.approval_fingerprint,
            execution_status=EDAExecutionStatus.EXECUTION_SUCCESS,
            readback_status=readback_status,
            verification_status=verification_status,
            readback_fingerprint=readback.content_fingerprint if readback_pass else None,
            verification_fingerprint=verification_fingerprint if verification_status is EDAVerificationStatus.VERIFICATION_PASS else None,
            created_at=utc_datetime(now, field="now"),
        )


class KiCadEDAIntegrationBackend:
    """Adapter seam for the existing KiCad writer/parser/DRC tool chain.

    File access and CLI invocation remain injected.  This class never accepts
    shell text or JavaScript and therefore cannot turn an LLM string into an
    EDA side effect.
    """

    backend = EDABackend.KICAD

    def __init__(self, *, reader, executor) -> None:
        if not callable(reader) or not callable(executor):
            raise TypeError("KiCad reader and executor are required")
        self._reader = reader
        self._executor = executor

    def read(self, operation: EDAOperation) -> EDAArtifactReference:
        return self._reader(operation)

    def execute(self, operation: EDAOperation, current: EDAArtifactReference) -> EDAArtifactReference:
        return self._executor(operation, current)


class JLCEDAUnavailableBackend:
    """Explicit fail-closed JLCEDA seam until a trusted EPRJ3 adapter exists."""

    backend = EDABackend.JLCEDA

    def read(self, operation: EDAOperation) -> EDAArtifactReference:
        raise EDAUnavailable("JLCEDA EPRJ3 adapter is not configured")

    def execute(self, operation: EDAOperation, current: EDAArtifactReference) -> EDAArtifactReference:
        raise EDAUnavailable("JLCEDA EPRJ3 adapter is not configured")


__all__ = (
    "EDABackend",
    "EDAExecutionStatus",
    "EDAIntegrationLayer",
    "EDAOperation",
    "EDAOperationKind",
    "EDAOperationReceipt",
    "EDAReadBackStatus",
    "EDAVerificationStatus",
    "JLCEDAUnavailableBackend",
    "KiCadEDAIntegrationBackend",
)
