from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_FP = re.compile(r"^sha256:[a-f0-9]{64}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+\-]{0,159}$")
_AUTO = "sha256:" + "0" * 64


class ExecutionContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        revalidate_instances="always",
        validate_default=True,
    )


def identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    value = unicodedata.normalize("NFC", value).strip()
    if _ID.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def fingerprint(value: object, *, field: str = "fingerprint") -> str:
    if not isinstance(value, str) or _FP.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def safe_text(value: object, *, field: str, maximum: int = 256) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    value = unicodedata.normalize("NFC", value).strip()
    if not value or len(value) > maximum or "\x00" in value:
        raise ValueError(f"{field} is invalid")
    return value


def utc_datetime(value: object, *, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"{field} must be timezone-aware") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


class FingerprintedContract(ExecutionContract):
    fingerprint: str = _AUTO

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return fingerprint(value)

    @model_validator(mode="after")
    def _bind_fingerprint(self):
        payload = self.model_dump(mode="json", exclude={"fingerprint"})
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        expected = "sha256:" + hashlib.sha256(encoded).hexdigest()
        if self.fingerprint == _AUTO:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("fingerprint mismatch")
        return self


class OperationType(StrEnum):
    BUILD = "BUILD"
    TEST = "TEST"
    FLASH = "FLASH"
    UART_OBSERVATION = "UART_OBSERVATION"
    HIL = "HIL"


class ApprovalPurpose(StrEnum):
    WORKSPACE_APPLY = "WORKSPACE_APPLY"
    PROPOSAL = "PROPOSAL"
    BUILD = "BUILD"
    TEST = "TEST"
    FLASH = "FLASH"
    UART_OBSERVATION = "UART_OBSERVATION"
    HIL = "HIL"


class ApprovalStatus(StrEnum):
    ISSUED = "ISSUED"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class OperationStatus(StrEnum):
    CREATED = "CREATED"
    WAIT_APPROVAL = "WAIT_APPROVAL"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class RecoveryState(StrEnum):
    NONE = "NONE"
    INTERRUPTED = "INTERRUPTED"
    TARGET_STATE_UNKNOWN = "TARGET_STATE_UNKNOWN"
    OPERATOR_DECISION_REQUIRED = "OPERATOR_DECISION_REQUIRED"
    RECOVERED = "RECOVERED"


class OperationEventType(StrEnum):
    OPERATION_CREATED = "OPERATION_CREATED"
    APPROVAL_ISSUED = "APPROVAL_ISSUED"
    OPERATION_QUEUED = "OPERATION_QUEUED"
    OPERATION_CLAIMED = "OPERATION_CLAIMED"
    OPERATION_STARTED = "OPERATION_STARTED"
    CANCELLATION_REQUESTED = "CANCELLATION_REQUESTED"
    CANCELLATION_PENDING = "CANCELLATION_PENDING"
    OPERATION_SUCCEEDED = "OPERATION_SUCCEEDED"
    OPERATION_FAILED = "OPERATION_FAILED"
    OPERATION_CANCELLED = "OPERATION_CANCELLED"
    OPERATION_TIMED_OUT = "OPERATION_TIMED_OUT"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    RECOVERY_RESOLVED = "RECOVERY_RESOLVED"


class TargetBindingStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    CANDIDATE = "CANDIDATE"
    BOUND = "BOUND"
    REVOKED = "REVOKED"


class TargetDiscoveryCandidate(FingerprintedContract):
    candidate_id: str
    tenant_id: str
    project_id: str
    target_kind: str
    model: str
    observed_identity_hash: str
    discovered_at: datetime

    _ids = field_validator("candidate_id", "tenant_id", "project_id", "target_kind", "model", "observed_identity_hash", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _time = field_validator("discovered_at", mode="before")(lambda value: utc_datetime(value, field="discovered_at"))


class ExecutionTarget(FingerprintedContract):
    target_id: str
    tenant_id: str
    project_id: str
    target_kind: str
    board_family: str
    mcu_family: str
    stable_identity_hash: str
    candidate_id: str
    binding_status: TargetBindingStatus
    bound_by: str
    bound_at: datetime

    _ids = field_validator("target_id", "tenant_id", "project_id", "target_kind", "board_family", "mcu_family", "stable_identity_hash", "candidate_id", "bound_by", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _time = field_validator("bound_at", mode="before")(lambda value: utc_datetime(value, field="bound_at"))

    @model_validator(mode="after")
    def _must_be_bound(self):
        if self.binding_status is not TargetBindingStatus.BOUND:
            raise ValueError("operation target must be explicitly bound")
        return self


class ToolchainIdentity(FingerprintedContract):
    toolchain_id: str
    tool_version: str
    tool_family: str
    supported_operation: OperationType
    supported_target: str

    _ids = field_validator("toolchain_id", "tool_version", "tool_family", "supported_target", mode="before")(lambda value, info: identifier(value, field=info.field_name))


class ArtifactIdentity(FingerprintedContract):
    artifact_id: str
    tenant_id: str
    project_id: str
    target_id: str
    workspace_fingerprint: str
    build_operation_id: str
    toolchain_id: str
    toolchain_version: str
    build_configuration: str
    artifact_type: Literal["ELF", "BIN", "HEX", "MAP"]
    artifact_sha256: str
    size_bytes: int = Field(ge=0, le=512 * 1024 * 1024)
    created_at: datetime

    _ids = field_validator("artifact_id", "tenant_id", "project_id", "target_id", "build_operation_id", "toolchain_id", "toolchain_version", "build_configuration", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _fps = field_validator("workspace_fingerprint", "artifact_sha256", mode="before")(lambda value, info: fingerprint(value, field=info.field_name))
    _time = field_validator("created_at", mode="before")(lambda value: utc_datetime(value, field="created_at"))


class ExecutionProposal(FingerprintedContract):
    proposal_id: str
    tenant_id: str
    project_id: str
    requester_id: str
    target_id: str
    target_fingerprint: str
    workspace_snapshot_id: str
    workspace_fingerprint: str
    approved_workspace_operation_id: str
    approved_workspace_receipt_fingerprint: str
    operation_type: OperationType
    toolchain_id: str
    toolchain_version: str
    build_configuration: str
    created_at: datetime

    _ids = field_validator("proposal_id", "tenant_id", "project_id", "requester_id", "target_id", "workspace_snapshot_id", "approved_workspace_operation_id", "toolchain_id", "toolchain_version", "build_configuration", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _fps = field_validator("target_fingerprint", "workspace_fingerprint", "approved_workspace_receipt_fingerprint", mode="before")(lambda value, info: fingerprint(value, field=info.field_name))
    _time = field_validator("created_at", mode="before")(lambda value: utc_datetime(value, field="created_at"))


class OperationBase(FingerprintedContract):
    operation_id: str
    tenant_id: str
    project_id: str
    requester_id: str
    target_id: str
    target_fingerprint: str
    workspace_snapshot_id: str
    workspace_fingerprint: str
    proposal_id: str
    proposal_fingerprint: str
    operation_type: OperationType
    created_at: datetime
    expires_at: datetime
    nonce: str

    _ids = field_validator("operation_id", "tenant_id", "project_id", "requester_id", "target_id", "workspace_snapshot_id", "proposal_id", "nonce", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _fps = field_validator("target_fingerprint", "workspace_fingerprint", "proposal_fingerprint", mode="before")(lambda value, info: fingerprint(value, field=info.field_name))
    _times = field_validator("created_at", "expires_at", mode="before")(lambda value, info: utc_datetime(value, field=info.field_name))


class BuildOperation(OperationBase):
    operation_type: Literal[OperationType.BUILD] = OperationType.BUILD
    toolchain: ToolchainIdentity
    configuration: str
    expected_artifact_type: Literal["ELF", "BIN", "HEX", "MAP"]

    _configuration = field_validator("configuration", mode="before")(lambda value: identifier(value, field="configuration"))


class FlashOperation(OperationBase):
    operation_type: Literal[OperationType.FLASH] = OperationType.FLASH
    artifact: ArtifactIdentity
    tool_id: str
    tool_version: str

    _ids = field_validator("tool_id", "tool_version", mode="before")(lambda value, info: identifier(value, field=info.field_name))

    @model_validator(mode="after")
    def _artifact_binding(self):
        if (
            self.artifact.project_id != self.project_id
            or self.artifact.target_id != self.target_id
            or self.artifact.workspace_fingerprint != self.workspace_fingerprint
        ):
            raise ValueError("artifact target binding mismatch")
        return self


class TestOperation(OperationBase):
    """A typed request to execute one validation action.

    This contract participates in the controlled operation lifecycle only. It
    does not define a test runner, test framework, or hardware test platform.
    """

    operation_type: Literal[OperationType.TEST] = OperationType.TEST
    test_suite_id: str
    test_profile_id: str
    tool_id: Literal["FIRMWARE_TEST"] = "FIRMWARE_TEST"
    artifact: ArtifactIdentity | None = None

    _ids = field_validator("test_suite_id", "test_profile_id", mode="before")(
        lambda value, info: identifier(value, field=info.field_name)
    )

    @model_validator(mode="after")
    def _artifact_binding(self):
        if self.artifact is not None and (
            self.artifact.project_id != self.project_id
            or self.artifact.target_id != self.target_id
            or self.artifact.workspace_fingerprint != self.workspace_fingerprint
        ):
            raise ValueError("artifact target binding mismatch")
        return self


class UartObservationOperation(OperationBase):
    operation_type: Literal[OperationType.UART_OBSERVATION] = OperationType.UART_OBSERVATION
    port_binding_id: str
    baud_rate: Literal[9600, 19200, 38400, 57600, 115200, 230400]
    duration_seconds: int = Field(ge=1, le=300)
    max_bytes: int = Field(ge=1, le=1024 * 1024)
    expected_patterns: tuple[str, ...] = ()

    _port = field_validator("port_binding_id", mode="before")(lambda value: identifier(value, field="port_binding_id"))

    @field_validator("expected_patterns", mode="before")
    @classmethod
    def _patterns(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (tuple, list)) or len(value) > 32:
            raise ValueError("expected_patterns is invalid")
        return tuple(safe_text(item, field="expected_pattern", maximum=128) for item in value)


class HilStepKind(StrEnum):
    WAIT_FOR_UART_PATTERN = "WAIT_FOR_UART_PATTERN"
    ASSERT_UART_PATTERN = "ASSERT_UART_PATTERN"
    ASSERT_NO_PANIC = "ASSERT_NO_PANIC"
    WAIT_DURATION = "WAIT_DURATION"
    POWER_CYCLE = "POWER_CYCLE"
    RESET_TARGET = "RESET_TARGET"


class HilStep(ExecutionContract):
    kind: HilStepKind
    pattern: str | None = None
    duration_seconds: int | None = Field(default=None, ge=1, le=300)

    @field_validator("pattern", mode="before")
    @classmethod
    def _pattern(cls, value: object) -> str | None:
        return None if value is None else safe_text(value, field="pattern", maximum=128)

    @model_validator(mode="after")
    def _shape(self):
        needs_pattern = self.kind in {HilStepKind.WAIT_FOR_UART_PATTERN, HilStepKind.ASSERT_UART_PATTERN}
        needs_duration = self.kind is HilStepKind.WAIT_DURATION
        if needs_pattern != (self.pattern is not None) or needs_duration != (self.duration_seconds is not None):
            raise ValueError("HIL step parameters do not match step kind")
        return self


class HilScenario(FingerprintedContract):
    scenario_id: str
    project_id: str
    target_id: str
    artifact_id: str
    steps: tuple[HilStep, ...]
    timeout_seconds: int = Field(ge=1, le=900)

    _ids = field_validator("scenario_id", "project_id", "target_id", "artifact_id", mode="before")(lambda value, info: identifier(value, field=info.field_name))

    @field_validator("steps", mode="before")
    @classmethod
    def _steps(cls, value: object) -> tuple[HilStep, ...]:
        if not isinstance(value, (tuple, list)) or not value or len(value) > 64:
            raise ValueError("HIL steps are invalid")
        return tuple(HilStep.model_validate(item) for item in value)


class HilOperation(OperationBase):
    operation_type: Literal[OperationType.HIL] = OperationType.HIL
    scenario: HilScenario
    flash_operation_id: str

    _flash = field_validator("flash_operation_id", mode="before")(lambda value: identifier(value, field="flash_operation_id"))

    @model_validator(mode="after")
    def _scenario_binding(self):
        if self.scenario.project_id != self.project_id or self.scenario.target_id != self.target_id:
            raise ValueError("HIL scenario binding mismatch")
        return self


class OperationRequest(ExecutionContract):
    operation: Annotated[
        Union[BuildOperation, TestOperation, FlashOperation, UartObservationOperation, HilOperation],
        Field(discriminator="operation_type"),
    ]


class OperationApprovalGrant(FingerprintedContract):
    grant_id: str
    tenant_id: str
    project_id: str
    requester_id: str
    approval_purpose: ApprovalPurpose
    operation_id: str
    operation_type: OperationType
    operation_fingerprint: str
    proposal_id: str
    proposal_fingerprint: str
    target_id: str
    target_fingerprint: str
    workspace_snapshot_id: str
    workspace_fingerprint: str
    artifact_id: str | None = None
    artifact_sha256: str | None = None
    reviewer_id: str
    status: ApprovalStatus
    expires_at: datetime
    nonce: str
    issued_at: datetime

    _ids = field_validator("grant_id", "tenant_id", "project_id", "requester_id", "operation_id", "proposal_id", "target_id", "workspace_snapshot_id", "reviewer_id", "nonce", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _fps = field_validator("operation_fingerprint", "proposal_fingerprint", "target_fingerprint", "workspace_fingerprint", "artifact_sha256", mode="before")(lambda value, info: None if value is None else fingerprint(value, field=info.field_name))
    _optional_artifact = field_validator("artifact_id", mode="before")(lambda value: None if value is None else identifier(value, field="artifact_id"))
    _times = field_validator("expires_at", "issued_at", mode="before")(lambda value, info: utc_datetime(value, field=info.field_name))

    @model_validator(mode="after")
    def _purpose_binding(self):
        expected = {
            OperationType.BUILD: ApprovalPurpose.BUILD,
            OperationType.TEST: ApprovalPurpose.TEST,
            OperationType.FLASH: ApprovalPurpose.FLASH,
            OperationType.UART_OBSERVATION: ApprovalPurpose.UART_OBSERVATION,
            OperationType.HIL: ApprovalPurpose.HIL,
        }[self.operation_type]
        if self.approval_purpose is not expected:
            raise ValueError("approval purpose does not match operation type")
        if (self.artifact_id is None) != (self.artifact_sha256 is None):
            raise ValueError("artifact approval binding is incomplete")
        return self


class OperationStatusView(FingerprintedContract):
    operation_id: str
    tenant_id: str
    project_id: str
    operation_type: OperationType
    status: OperationStatus
    approval_id: str | None
    worker_id: str | None
    recovery_state: RecoveryState
    cancellation_requested: bool
    updated_at: datetime
    receipt_fingerprint: str | None = None
    safe_detail: str = ""

    _ids = field_validator("operation_id", "tenant_id", "project_id", "worker_id", "approval_id", mode="before")(lambda value, info: None if value is None else identifier(value, field=info.field_name))
    _fps = field_validator("receipt_fingerprint", mode="before")(lambda value: None if value is None else fingerprint(value, field="receipt_fingerprint"))
    _time = field_validator("updated_at", mode="before")(lambda value: utc_datetime(value, field="updated_at"))
    _detail = field_validator("safe_detail", mode="before")(lambda value: safe_text(value, field="safe_detail", maximum=512) if value else "")


class OperationEvent(FingerprintedContract):
    """Immutable lifecycle observation for one controlled operation."""

    event_id: str
    sequence: int = Field(ge=1)
    tenant_id: str
    project_id: str
    operation_id: str
    operation_fingerprint: str
    operation_type: OperationType
    event_type: OperationEventType
    status: OperationStatus
    recovery_state: RecoveryState
    approval_id: str | None = None
    receipt_fingerprint: str | None = None
    reason_code: str
    occurred_at: datetime

    _ids = field_validator(
        "event_id",
        "tenant_id",
        "project_id",
        "operation_id",
        "approval_id",
        "reason_code",
        mode="before",
    )(
        lambda value, info: None
        if value is None
        else identifier(value, field=info.field_name)
    )
    _fps = field_validator(
        "operation_fingerprint", "receipt_fingerprint", mode="before"
    )(
        lambda value, info: None
        if value is None
        else fingerprint(value, field=info.field_name)
    )
    _time = field_validator("occurred_at", mode="before")(
        lambda value: utc_datetime(value, field="occurred_at")
    )


class OperationReceipt(FingerprintedContract):
    operation_id: str
    tenant_id: str
    project_id: str
    operation_type: OperationType
    status: OperationStatus
    tool_execution_status: Literal["SUCCEEDED", "FAILED", "UNAVAILABLE", "CANCELLED", "TIMED_OUT"]
    flash_verification_status: Literal["NOT_APPLICABLE", "NOT_REPORTED", "VERIFIED", "FAILED"]
    target_runtime_verification_status: Literal["NOT_APPLICABLE", "NOT_VERIFIED"]
    artifact: ArtifactIdentity | None = None
    evidence_references: tuple[str, ...] = ()
    safe_diagnostics_summary: str = ""
    recovery_state: RecoveryState = RecoveryState.NONE
    created_at: datetime

    _ids = field_validator("operation_id", "tenant_id", "project_id", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _refs = field_validator("evidence_references", mode="before")(lambda value: tuple(identifier(item, field="evidence_reference") for item in value) if isinstance(value, (tuple, list)) else (_ for _ in ()).throw(ValueError("evidence_references is invalid")))
    _detail = field_validator("safe_diagnostics_summary", mode="before")(lambda value: safe_text(value, field="safe_diagnostics_summary", maximum=1024) if value else "")
    _time = field_validator("created_at", mode="before")(lambda value: utc_datetime(value, field="created_at"))


class CancellationRequest(FingerprintedContract):
    request_id: str
    tenant_id: str
    project_id: str
    operation_id: str
    requester_id: str
    reason: str
    requested_at: datetime

    _ids = field_validator("request_id", "tenant_id", "project_id", "operation_id", "requester_id", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _reason = field_validator("reason", mode="before")(lambda value: safe_text(value, field="reason", maximum=256))
    _time = field_validator("requested_at", mode="before")(lambda value: utc_datetime(value, field="requested_at"))


class RecoveryDecision(FingerprintedContract):
    decision_id: str
    tenant_id: str
    project_id: str
    operation_id: str
    reviewer_id: str
    decision: Literal["MARK_FAILED", "MARK_TARGET_SAFE", "REQUIRES_MANUAL_INTERVENTION"]
    decided_at: datetime

    _ids = field_validator("decision_id", "tenant_id", "project_id", "operation_id", "reviewer_id", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _time = field_validator("decided_at", mode="before")(lambda value: utc_datetime(value, field="decided_at"))


__all__ = tuple(name for name in globals() if not name.startswith("_") and name not in {"Annotated", "Union", "Literal"})
