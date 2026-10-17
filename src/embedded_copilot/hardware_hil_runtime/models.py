from __future__ import annotations

import copy
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import field_validator, model_validator

from embedded_copilot.hardware_runtime import (
    FirmwareArtifactReference,
    HardwareTargetReference,
)

from .contracts import (
    FingerprintedHILContract,
    deterministic_id,
    fingerprint,
    identifier,
    safe_text,
    tuple_only,
    utc_datetime,
)


def _exact(value: object, model: type, *, field: str):
    if type(value) is model:
        value = value.model_dump(mode="python")
    elif type(value) is not dict:
        raise ValueError(f"{field} is invalid")
    return model.model_validate(copy.deepcopy(value))


class EngineeringProposalKind(StrEnum):
    PCB = "PCB"
    FIRMWARE = "FIRMWARE"
    REPAIR = "REPAIR"
    OPTIMIZATION = "OPTIMIZATION"
    EDA = "EDA"


class HILTestType(StrEnum):
    BOOT = "BOOT"
    GPIO = "GPIO"
    SENSOR = "SENSOR"
    POWER = "POWER"
    TIMING = "TIMING"


class HILRiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class HardwareApprovalDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_CHANGE = "REQUEST_CHANGE"


class HILExecutionStatus(StrEnum):
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class HardwareObservationType(StrEnum):
    UART_BOOT = "UART_BOOT"
    UART_ERROR = "UART_ERROR"
    GPIO_STATE = "GPIO_STATE"
    SENSOR_SAMPLE = "SENSOR_SAMPLE"
    POWER_MEASUREMENT = "POWER_MEASUREMENT"
    TIMING_METRIC = "TIMING_METRIC"


class HILEvidenceStatus(StrEnum):
    OBSERVED = "OBSERVED"
    PROJECTED = "PROJECTED"


class HILAuditEventType(StrEnum):
    TEST_PROPOSAL_CREATED = "TEST_PROPOSAL_CREATED"
    REVIEW_SUBMITTED = "REVIEW_SUBMITTED"
    TEST_APPROVED = "TEST_APPROVED"
    HIL_STARTED = "HIL_STARTED"
    OBSERVATION_CAPTURED = "OBSERVATION_CAPTURED"
    HIL_FAILED = "HIL_FAILED"


class EngineeringProposalReference(FingerprintedHILContract):
    project_id: str
    kind: EngineeringProposalKind
    proposal_id: str
    proposal_fingerprint: str

    @field_validator("project_id", "proposal_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("proposal_fingerprint", mode="before")
    @classmethod
    def _proposal_fp(cls, value: object) -> str:
        return fingerprint(value, field="proposal_fingerprint")


class HardwareFixtureReference(FingerprintedHILContract):
    project_id: str
    fixture_id: str
    target_fingerprint: str
    supported_observation_types: tuple[HardwareObservationType, ...]
    calibration_evidence_fingerprint: str
    expires_at: datetime

    @field_validator("project_id", "fixture_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator(
        "target_fingerprint", "calibration_evidence_fingerprint", mode="before"
    )
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("supported_observation_types", mode="before")
    @classmethod
    def _types(cls, value: object) -> tuple[HardwareObservationType, ...]:
        items = tuple_only(value, field="supported_observation_types")
        if not items or len(items) != len(set(items)):
            raise ValueError("fixture observation types are invalid")
        return tuple(sorted(items, key=lambda item: item.value))

    @field_validator("expires_at", mode="before")
    @classmethod
    def _expires(cls, value: object) -> datetime:
        return utc_datetime(value, field="expires_at")


class HardwareTestProposalRequest(FingerprintedHILContract):
    request_id: str
    project_id: str
    source_proposal: EngineeringProposalReference
    target: HardwareTargetReference
    firmware: FirmwareArtifactReference
    fixture: HardwareFixtureReference
    test_type: HILTestType
    test_steps: tuple[str, ...]
    expected_observations: tuple[HardwareObservationType, ...]
    risk_level: HILRiskLevel
    evidence_refs: tuple[str, ...]
    created_by: str
    created_at: datetime

    @field_validator("request_id", "project_id", "created_by", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("source_proposal", mode="before")
    @classmethod
    def _source(cls, value: object) -> EngineeringProposalReference:
        return _exact(value, EngineeringProposalReference, field="source_proposal")

    @field_validator("target", mode="before")
    @classmethod
    def _target(cls, value: object) -> HardwareTargetReference:
        return _exact(value, HardwareTargetReference, field="target")

    @field_validator("firmware", mode="before")
    @classmethod
    def _firmware(cls, value: object) -> FirmwareArtifactReference:
        return _exact(value, FirmwareArtifactReference, field="firmware")

    @field_validator("fixture", mode="before")
    @classmethod
    def _fixture(cls, value: object) -> HardwareFixtureReference:
        return _exact(value, HardwareFixtureReference, field="fixture")

    @field_validator("test_steps", mode="before")
    @classmethod
    def _steps(cls, value: object) -> tuple[str, ...]:
        items = tuple(
            safe_text(item, field="test_step", maximum=256)
            for item in tuple_only(value, field="test_steps")
        )
        if not items or len(items) != len(set(items)):
            raise ValueError("test steps are invalid")
        return items

    @field_validator("expected_observations", mode="before")
    @classmethod
    def _observations(cls, value: object) -> tuple[HardwareObservationType, ...]:
        items = tuple_only(value, field="expected_observations")
        if not items or len(items) != len(set(items)):
            raise ValueError("expected observations are invalid")
        return tuple(sorted(items, key=lambda item: item.value))

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence(cls, value: object) -> tuple[str, ...]:
        items = tuple(
            identifier(item, field="evidence_ref")
            for item in tuple_only(value, field="evidence_refs")
        )
        if not items or len(items) != len(set(items)):
            raise ValueError("evidence references are invalid")
        return items

    @field_validator("created_at", mode="before")
    @classmethod
    def _created(cls, value: object) -> datetime:
        return utc_datetime(value, field="created_at")

    @model_validator(mode="after")
    def _binding(self):
        if (
            self.source_proposal.project_id != self.project_id
            or self.target.project_id != self.project_id
            or self.firmware.project_id != self.project_id
            or self.fixture.project_id != self.project_id
            or self.firmware.target != self.target
            or self.fixture.target_fingerprint != self.target.fingerprint
            or not set(self.expected_observations).issubset(
                self.fixture.supported_observation_types
            )
        ):
            raise ValueError("test proposal request binding mismatch")
        return self


class HardwareTestProposal(HardwareTestProposalRequest):
    proposal_id: str

    @field_validator("proposal_id", mode="before")
    @classmethod
    def _proposal_id(cls, value: object) -> str:
        return identifier(value, field="proposal_id")


class HardwareApprovalScope(FingerprintedHILContract):
    project_id: str
    target_fingerprint: str
    firmware_fingerprint: str
    fixture_fingerprint: str
    test_type: HILTestType
    observation_types: tuple[HardwareObservationType, ...]
    expires_at: datetime

    @field_validator("project_id", mode="before")
    @classmethod
    def _project(cls, value: object) -> str:
        return identifier(value, field="project_id")

    @field_validator(
        "target_fingerprint",
        "firmware_fingerprint",
        "fixture_fingerprint",
        mode="before",
    )
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("observation_types", mode="before")
    @classmethod
    def _observations(cls, value: object) -> tuple[HardwareObservationType, ...]:
        items = tuple_only(value, field="observation_types")
        if not items or len(items) != len(set(items)):
            raise ValueError("scope observation types are invalid")
        return tuple(sorted(items, key=lambda item: item.value))

    @field_validator("expires_at", mode="before")
    @classmethod
    def _expires(cls, value: object) -> datetime:
        return utc_datetime(value, field="expires_at")


class HardwareApprovalRequest(FingerprintedHILContract):
    approval_id: str
    proposal: HardwareTestProposal
    requested_reviewer: str
    requested_at: datetime

    @field_validator("approval_id", "requested_reviewer", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("proposal", mode="before")
    @classmethod
    def _proposal(cls, value: object) -> HardwareTestProposal:
        return _exact(value, HardwareTestProposal, field="proposal")

    @field_validator("requested_at", mode="before")
    @classmethod
    def _requested(cls, value: object) -> datetime:
        return utc_datetime(value, field="requested_at")


class HardwareApprovalRecord(FingerprintedHILContract):
    approval_id: str
    proposal_id: str
    project_id: str
    requested_reviewer: str
    reviewer: str
    decision: HardwareApprovalDecision
    approved_at: datetime
    scope: HardwareApprovalScope

    @field_validator(
        "approval_id",
        "proposal_id",
        "project_id",
        "requested_reviewer",
        "reviewer",
        mode="before",
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("approved_at", mode="before")
    @classmethod
    def _approved(cls, value: object) -> datetime:
        return utc_datetime(value, field="approved_at")

    @field_validator("scope", mode="before")
    @classmethod
    def _scope(cls, value: object) -> HardwareApprovalScope:
        return _exact(value, HardwareApprovalScope, field="scope")

    @model_validator(mode="after")
    def _binding(self):
        if (
            self.scope.project_id != self.project_id
            or self.requested_reviewer != self.reviewer
            or self.approved_at > self.scope.expires_at
        ):
            raise ValueError("approval scope mismatch")
        return self


class HILExecutionRequest(FingerprintedHILContract):
    execution_id: str
    proposal: HardwareTestProposal
    approval: HardwareApprovalRecord
    target: HardwareTargetReference
    firmware: FirmwareArtifactReference
    fixture: HardwareFixtureReference
    authorized_at: datetime

    @field_validator("execution_id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return identifier(value, field="execution_id")

    @field_validator("proposal", mode="before")
    @classmethod
    def _proposal(cls, value: object) -> HardwareTestProposal:
        return _exact(value, HardwareTestProposal, field="proposal")

    @field_validator("approval", mode="before")
    @classmethod
    def _approval(cls, value: object) -> HardwareApprovalRecord:
        return _exact(value, HardwareApprovalRecord, field="approval")

    @field_validator("target", mode="before")
    @classmethod
    def _target(cls, value: object) -> HardwareTargetReference:
        return _exact(value, HardwareTargetReference, field="target")

    @field_validator("firmware", mode="before")
    @classmethod
    def _firmware(cls, value: object) -> FirmwareArtifactReference:
        return _exact(value, FirmwareArtifactReference, field="firmware")

    @field_validator("fixture", mode="before")
    @classmethod
    def _fixture(cls, value: object) -> HardwareFixtureReference:
        return _exact(value, HardwareFixtureReference, field="fixture")

    @field_validator("authorized_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="authorized_at")

    @model_validator(mode="after")
    def _binding(self):
        scope = self.approval.scope
        if (
            self.approval.decision is not HardwareApprovalDecision.APPROVE
            or self.proposal.project_id != scope.project_id
            or self.approval.proposal_id != self.proposal.proposal_id
            or self.target != self.proposal.target
            or self.firmware != self.proposal.firmware
            or self.fixture != self.proposal.fixture
            or scope.target_fingerprint != self.target.fingerprint
            or scope.firmware_fingerprint != self.firmware.firmware_fingerprint
            or scope.fixture_fingerprint != self.fixture.fingerprint
            or scope.test_type is not self.proposal.test_type
            or scope.observation_types != self.proposal.expected_observations
            or self.authorized_at < self.approval.approved_at
            or self.authorized_at > scope.expires_at
        ):
            raise ValueError("execution authorization binding mismatch")
        return self


class HILExecutionReceipt(FingerprintedHILContract):
    execution_id: str
    request_fingerprint: str
    project_id: str
    target_fingerprint: str
    firmware_fingerprint: str
    status: HILExecutionStatus
    occurred_at: datetime

    @field_validator("execution_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator(
        "request_fingerprint",
        "target_fingerprint",
        "firmware_fingerprint",
        mode="before",
    )
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("occurred_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="occurred_at")


class HardwareObservationRequest(FingerprintedHILContract):
    request_id: str
    execution: HILExecutionReceipt
    observation_type: HardwareObservationType
    requested_at: datetime

    @field_validator("request_id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return identifier(value, field="request_id")

    @field_validator("execution", mode="before")
    @classmethod
    def _execution(cls, value: object) -> HILExecutionReceipt:
        return _exact(value, HILExecutionReceipt, field="execution")

    @field_validator("requested_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="requested_at")


class HardwareObservation(FingerprintedHILContract):
    observation_id: str
    request_fingerprint: str
    project_id: str
    target_fingerprint: str
    firmware_fingerprint: str
    observation_type: HardwareObservationType
    content: str
    status: Literal[HILEvidenceStatus.OBSERVED] = HILEvidenceStatus.OBSERVED
    observed_at: datetime

    @field_validator("observation_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator(
        "request_fingerprint",
        "target_fingerprint",
        "firmware_fingerprint",
        mode="before",
    )
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("content", mode="before")
    @classmethod
    def _content(cls, value: object) -> str:
        return safe_text(value, field="observation content", maximum=512)

    @field_validator("observed_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="observed_at")


class HILRuntimeEvidence(FingerprintedHILContract):
    observation: HardwareObservation
    status: Literal[HILEvidenceStatus.OBSERVED] = HILEvidenceStatus.OBSERVED

    @field_validator("observation", mode="before")
    @classmethod
    def _observation(cls, value: object) -> HardwareObservation:
        return _exact(value, HardwareObservation, field="observation")


class HardwareHILContextOverlay(FingerprintedHILContract):
    overlay_id: str
    project_id: str
    base_context_fingerprint: str | None
    evidence: tuple[HILRuntimeEvidence, ...]
    status: Literal[HILEvidenceStatus.PROJECTED] = HILEvidenceStatus.PROJECTED
    created_at: datetime

    @field_validator("overlay_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("base_context_fingerprint", mode="before")
    @classmethod
    def _base(cls, value: object) -> str | None:
        return (
            None
            if value is None
            else fingerprint(value, field="base_context_fingerprint")
        )

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence(cls, value: object) -> tuple[HILRuntimeEvidence, ...]:
        items = tuple(
            _exact(item, HILRuntimeEvidence, field="runtime evidence")
            for item in tuple_only(value, field="evidence")
        )
        if not items or len({item.observation.observation_id for item in items}) != len(
            items
        ):
            raise ValueError("runtime evidence is invalid")
        return items

    @field_validator("created_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="created_at")

    @model_validator(mode="after")
    def _binding(self):
        if any(
            item.observation.project_id != self.project_id for item in self.evidence
        ):
            raise ValueError("context overlay project mismatch")
        return self


class HILMemoryCandidateReference(FingerprintedHILContract):
    project_id: str
    overlay_fingerprint: str
    candidate_id: str
    candidate_fingerprint: str
    review_status: Literal["REVIEW_REQUIRED"] = "REVIEW_REQUIRED"

    @field_validator("project_id", "candidate_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("overlay_fingerprint", "candidate_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)


class HILAuditEvent(FingerprintedHILContract):
    event_id: str
    event_type: HILAuditEventType
    project_id: str
    operation_fingerprint: str
    result_fingerprint: str | None
    timestamp: datetime

    @field_validator("event_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("operation_fingerprint", mode="before")
    @classmethod
    def _op(cls, value: object) -> str:
        return fingerprint(value, field="operation_fingerprint")

    @field_validator("result_fingerprint", mode="before")
    @classmethod
    def _result(cls, value: object) -> str | None:
        return None if value is None else fingerprint(value, field="result_fingerprint")

    @field_validator("timestamp", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="timestamp")

    @model_validator(mode="after")
    def _id_binding(self):
        if self.event_id != deterministic_id(
            "hil-event", self.operation_fingerprint, self.event_type.value
        ):
            raise ValueError("HIL audit event identity mismatch")
        return self


class HILReport(FingerprintedHILContract):
    proposal: HardwareTestProposal
    approval: HardwareApprovalRecord
    receipt: HILExecutionReceipt
    observations: tuple[HardwareObservation, ...]
    overlay: HardwareHILContextOverlay
    memory_candidates: tuple[HILMemoryCandidateReference, ...]

    @field_validator("proposal", mode="before")
    @classmethod
    def _proposal(cls, value: object) -> HardwareTestProposal:
        return _exact(value, HardwareTestProposal, field="proposal")

    @field_validator("approval", mode="before")
    @classmethod
    def _approval(cls, value: object) -> HardwareApprovalRecord:
        return _exact(value, HardwareApprovalRecord, field="approval")

    @field_validator("receipt", mode="before")
    @classmethod
    def _receipt(cls, value: object) -> HILExecutionReceipt:
        return _exact(value, HILExecutionReceipt, field="receipt")

    @field_validator("observations", mode="before")
    @classmethod
    def _observations(cls, value: object) -> tuple[HardwareObservation, ...]:
        return tuple(
            _exact(item, HardwareObservation, field="observation")
            for item in tuple_only(value, field="observations")
        )

    @field_validator("overlay", mode="before")
    @classmethod
    def _overlay(cls, value: object) -> HardwareHILContextOverlay:
        return _exact(value, HardwareHILContextOverlay, field="overlay")

    @field_validator("memory_candidates", mode="before")
    @classmethod
    def _memory(cls, value: object) -> tuple[HILMemoryCandidateReference, ...]:
        return tuple(
            _exact(item, HILMemoryCandidateReference, field="memory candidate")
            for item in tuple_only(value, field="memory_candidates")
        )


__all__ = tuple(
    name for name in globals() if name.startswith(("Engineering", "Hardware", "HIL"))
)
