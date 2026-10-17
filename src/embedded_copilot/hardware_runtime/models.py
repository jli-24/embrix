from __future__ import annotations

import copy
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .contracts import (
    FingerprintedHardwareContract,
    deterministic_id,
    fingerprint,
    identifier,
    safe_text,
    tuple_only,
    utc_datetime,
)


def _exact(value: object, model: type, *, field: str):
    if type(value) is model:
        material = value.model_dump(mode="python")
    elif type(value) is dict:
        material = value
    else:
        raise ValueError(f"{field} is invalid")
    return model.model_validate(copy.deepcopy(material))


class HardwareCapability(StrEnum):
    BUILD = "BUILD"
    FLASH = "FLASH"
    UART_OBSERVE = "UART_OBSERVE"
    GPIO_OBSERVE = "GPIO_OBSERVE"
    LED_OBSERVE = "LED_OBSERVE"
    INTERRUPT_OBSERVE = "INTERRUPT_OBSERVE"
    SENSOR_OBSERVE = "SENSOR_OBSERVE"
    I2C_OBSERVE = "I2C_OBSERVE"
    SPI_OBSERVE = "SPI_OBSERVE"


class HardwareOperationType(StrEnum):
    BUILD = "BUILD"
    FLASH = "FLASH"


class HardwareRisk(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class HardwareReviewDecisionType(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_CHANGE = "REQUEST_CHANGE"


class HardwareReviewStatus(StrEnum):
    OPEN = "OPEN"
    DECIDED = "DECIDED"


class HardwareReceiptStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    REJECTED = "REJECTED"


class HardwareObservationType(StrEnum):
    UART_BOOT_LOG = "UART_BOOT_LOG"
    UART_ERROR_LOG = "UART_ERROR_LOG"
    GPIO_STATE = "GPIO_STATE"
    LED_STATE = "LED_STATE"
    INTERRUPT_METRIC = "INTERRUPT_METRIC"
    SENSOR_SAMPLE = "SENSOR_SAMPLE"
    I2C_TRANSACTION_SUMMARY = "I2C_TRANSACTION_SUMMARY"
    SPI_TRANSACTION_SUMMARY = "SPI_TRANSACTION_SUMMARY"

    @property
    def capability(self) -> HardwareCapability:
        return {
            self.UART_BOOT_LOG: HardwareCapability.UART_OBSERVE,
            self.UART_ERROR_LOG: HardwareCapability.UART_OBSERVE,
            self.GPIO_STATE: HardwareCapability.GPIO_OBSERVE,
            self.LED_STATE: HardwareCapability.LED_OBSERVE,
            self.INTERRUPT_METRIC: HardwareCapability.INTERRUPT_OBSERVE,
            self.SENSOR_SAMPLE: HardwareCapability.SENSOR_OBSERVE,
            self.I2C_TRANSACTION_SUMMARY: HardwareCapability.I2C_OBSERVE,
            self.SPI_TRANSACTION_SUMMARY: HardwareCapability.SPI_OBSERVE,
        }[self]


class HardwareObservationStatus(StrEnum):
    OBSERVED = "OBSERVED"
    UNAVAILABLE = "UNAVAILABLE"


class HardwareAuditEventType(StrEnum):
    OBSERVATION_COMPLETED = "OBSERVATION_COMPLETED"
    REVIEW_SUBMITTED = "REVIEW_SUBMITTED"
    BUILD_COMPLETED = "BUILD_COMPLETED"
    FLASH_COMPLETED = "FLASH_COMPLETED"
    OPERATION_FAILED = "OPERATION_FAILED"
    OPERATION_CANCELLED = "OPERATION_CANCELLED"


class HardwareTargetReference(FingerprintedHardwareContract):
    project_id: str
    device_id: str
    board_type: str
    chip_family: str
    target_profile: str
    firmware_target: str
    available_capabilities: tuple[HardwareCapability, ...]

    @field_validator(
        "project_id",
        "device_id",
        "board_type",
        "chip_family",
        "target_profile",
        "firmware_target",
        mode="before",
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("available_capabilities", mode="before")
    @classmethod
    def _capabilities(cls, value: object) -> tuple[HardwareCapability, ...]:
        items = tuple_only(value, field="available_capabilities")
        if not items or len(items) != len(set(items)):
            raise ValueError("hardware capabilities are invalid")
        return tuple(sorted(items, key=lambda item: item.value))


class FirmwareArtifactReference(FingerprintedHardwareContract):
    artifact_id: str
    project_id: str
    workspace_snapshot_fingerprint: str
    target: HardwareTargetReference
    firmware_fingerprint: str
    build_provenance_fingerprint: str
    created_at: datetime

    @field_validator("artifact_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator(
        "workspace_snapshot_fingerprint",
        "firmware_fingerprint",
        "build_provenance_fingerprint",
        mode="before",
    )
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("target", mode="before")
    @classmethod
    def _target(cls, value: object) -> HardwareTargetReference:
        return _exact(value, HardwareTargetReference, field="target")

    @field_validator("created_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="created_at")

    @model_validator(mode="after")
    def _binding(self):
        if self.target.project_id != self.project_id:
            raise ValueError("firmware target project mismatch")
        return self


class HardwareOperationPlan(FingerprintedHardwareContract):
    plan_id: str
    project_id: str
    operation: HardwareOperationType
    target: HardwareTargetReference
    firmware_artifact: FirmwareArtifactReference | None
    risk: HardwareRisk
    actor: str
    reason: str
    created_at: datetime

    @field_validator("plan_id", "project_id", "actor", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, value: object) -> str:
        return safe_text(value, field="reason", maximum=512)

    @field_validator("created_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="created_at")

    @model_validator(mode="after")
    def _binding(self):
        if self.target.project_id != self.project_id:
            raise ValueError("operation target mismatch")
        if (
            self.operation is HardwareOperationType.FLASH
            and self.firmware_artifact is None
        ):
            raise ValueError("flash plan requires firmware artifact")
        if self.firmware_artifact is not None and (
            self.firmware_artifact.project_id != self.project_id
            or self.firmware_artifact.target != self.target
        ):
            raise ValueError("operation firmware mismatch")
        return self


class HardwareReviewRequest(FingerprintedHardwareContract):
    review_id: str
    project_id: str
    plan_fingerprint: str
    operation: HardwareOperationType
    target_fingerprint: str
    firmware_fingerprint: str | None
    requester: str
    requested_at: datetime

    @field_validator("review_id", "project_id", "requester", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("plan_fingerprint", "target_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("firmware_fingerprint", mode="before")
    @classmethod
    def _optional_fp(cls, value: object) -> str | None:
        return (
            None if value is None else fingerprint(value, field="firmware_fingerprint")
        )

    @field_validator("requested_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="requested_at")


class HardwareReviewDecision(FingerprintedHardwareContract):
    decision_id: str
    review_id: str
    project_id: str
    decision: HardwareReviewDecisionType
    reviewer: str
    comment: str
    decided_at: datetime

    @field_validator(
        "decision_id", "review_id", "project_id", "reviewer", mode="before"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("comment", mode="before")
    @classmethod
    def _comment(cls, value: object) -> str:
        return safe_text(value, field="comment", maximum=512)

    @field_validator("decided_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="decided_at")


class HardwareReviewRecord(FingerprintedHardwareContract):
    request: HardwareReviewRequest
    decision: HardwareReviewDecision | None
    status: HardwareReviewStatus

    @model_validator(mode="after")
    def _state(self):
        if self.status is HardwareReviewStatus.OPEN and self.decision is not None:
            raise ValueError("open hardware review has a decision")
        if self.status is HardwareReviewStatus.DECIDED and self.decision is None:
            raise ValueError("decided hardware review lacks decision")
        if self.decision is not None and (
            self.decision.review_id != self.request.review_id
            or self.decision.project_id != self.request.project_id
            or self.decision.decided_at < self.request.requested_at
        ):
            raise ValueError("hardware review binding mismatch")
        return self


class HardwareApprovalReference(FingerprintedHardwareContract):
    review_id: str
    record_fingerprint: str
    plan_fingerprint: str
    project_id: str
    operation: HardwareOperationType
    target_fingerprint: str
    firmware_fingerprint: str | None
    reviewer: str
    approved_at: datetime

    @field_validator("review_id", "project_id", "reviewer", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator(
        "record_fingerprint", "plan_fingerprint", "target_fingerprint", mode="before"
    )
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("firmware_fingerprint", mode="before")
    @classmethod
    def _optional_fp(cls, value: object) -> str | None:
        return (
            None if value is None else fingerprint(value, field="firmware_fingerprint")
        )

    @field_validator("approved_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="approved_at")


class HardwareBuildRequest(FingerprintedHardwareContract):
    request_id: str
    project_id: str
    plan: HardwareOperationPlan
    approval: HardwareApprovalReference
    requested_at: datetime

    @field_validator("request_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("requested_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="requested_at")

    @model_validator(mode="after")
    def _binding(self):
        if (
            self.plan.operation is not HardwareOperationType.BUILD
            or self.approval.operation is not HardwareOperationType.BUILD
            or self.approval.plan_fingerprint != self.plan.fingerprint
            or self.plan.project_id != self.project_id
            or self.approval.project_id != self.project_id
            or self.requested_at < self.approval.approved_at
        ):
            raise ValueError("build approval mismatch")
        return self


class HardwareBuildReceipt(FingerprintedHardwareContract):
    request_id: str
    project_id: str
    request_fingerprint: str
    target_fingerprint: str
    status: HardwareReceiptStatus
    firmware_artifact: FirmwareArtifactReference | None
    completed_at: datetime

    @field_validator("request_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("request_fingerprint", "target_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("completed_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="completed_at")

    @model_validator(mode="after")
    def _state(self):
        if (self.status is HardwareReceiptStatus.SUCCEEDED) != (
            self.firmware_artifact is not None
        ):
            raise ValueError("build receipt status mismatch")
        if (
            self.firmware_artifact is not None
            and self.firmware_artifact.project_id != self.project_id
        ):
            raise ValueError("build artifact project mismatch")
        return self


class HardwareFlashRequest(FingerprintedHardwareContract):
    request_id: str
    project_id: str
    plan: HardwareOperationPlan
    approval: HardwareApprovalReference
    build_receipt: HardwareBuildReceipt
    requested_at: datetime

    @field_validator("request_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("requested_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="requested_at")

    @model_validator(mode="after")
    def _binding(self):
        firmware = self.plan.firmware_artifact
        built = self.build_receipt.firmware_artifact
        if (
            self.plan.operation is not HardwareOperationType.FLASH
            or self.approval.operation is not HardwareOperationType.FLASH
            or self.approval.plan_fingerprint != self.plan.fingerprint
            or self.plan.project_id != self.project_id
            or self.approval.project_id != self.project_id
            or self.build_receipt.status is not HardwareReceiptStatus.SUCCEEDED
            or firmware is None
            or built is None
            or built.firmware_fingerprint != firmware.firmware_fingerprint
            or built.target != self.plan.target
            or self.approval.firmware_fingerprint != firmware.firmware_fingerprint
            or self.requested_at < self.approval.approved_at
        ):
            raise ValueError("flash approval or build binding mismatch")
        return self


class HardwareFlashReceipt(FingerprintedHardwareContract):
    request_id: str
    project_id: str
    request_fingerprint: str
    target_fingerprint: str
    firmware_fingerprint: str
    status: HardwareReceiptStatus
    completed_at: datetime

    @field_validator("request_id", "project_id", mode="before")
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

    @field_validator("completed_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="completed_at")


class HardwareObservationRequest(FingerprintedHardwareContract):
    request_id: str
    project_id: str
    target: HardwareTargetReference
    observation_type: HardwareObservationType
    timeout_seconds: float = Field(gt=0.0, le=60.0)
    requester: str
    evidence_scope: tuple[str, ...]
    requested_at: datetime

    @field_validator("request_id", "project_id", "requester", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("target", mode="before")
    @classmethod
    def _target(cls, value: object) -> HardwareTargetReference:
        return _exact(value, HardwareTargetReference, field="target")

    @field_validator("evidence_scope", mode="before")
    @classmethod
    def _scope(cls, value: object) -> tuple[str, ...]:
        items = tuple(
            identifier(item, field="evidence_scope")
            for item in tuple_only(value, field="evidence_scope")
        )
        if not items or len(items) != len(set(items)):
            raise ValueError("evidence scope is invalid")
        return items

    @field_validator("requested_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="requested_at")

    @model_validator(mode="after")
    def _binding(self):
        if (
            self.target.project_id != self.project_id
            or self.observation_type.capability
            not in self.target.available_capabilities
        ):
            raise ValueError("observation target capability mismatch")
        return self


class UARTObservationContent(FingerprintedHardwareContract):
    content_type: Literal["UART"] = "UART"
    baud_rate: int = Field(ge=1200, le=4_000_000)
    data_bits: Literal[5, 6, 7, 8]
    parity: Literal["NONE", "EVEN", "ODD"]
    stop_bits: Literal[1, 2]
    lines: tuple[str, ...]

    @field_validator("lines", mode="before")
    @classmethod
    def _lines(cls, value: object) -> tuple[str, ...]:
        items = tuple(
            safe_text(item, field="uart_line", maximum=256)
            for item in tuple_only(value, field="lines")
        )
        if not 1 <= len(items) <= 128:
            raise ValueError("UART lines are invalid")
        return items


class GPIOObservationContent(FingerprintedHardwareContract):
    content_type: Literal["GPIO"] = "GPIO"
    signal_id: str
    state: Literal["LOW", "HIGH", "UNKNOWN"]

    @field_validator("signal_id", mode="before")
    @classmethod
    def _signal(cls, value: object) -> str:
        return identifier(value, field="signal_id")


class LEDObservationContent(FingerprintedHardwareContract):
    content_type: Literal["LED"] = "LED"
    indicator_id: str
    state: Literal["OFF", "ON", "BLINKING", "UNKNOWN"]

    @field_validator("indicator_id", mode="before")
    @classmethod
    def _indicator(cls, value: object) -> str:
        return identifier(value, field="indicator_id")


class InterruptObservationContent(FingerprintedHardwareContract):
    content_type: Literal["INTERRUPT"] = "INTERRUPT"
    metric_id: str
    count: int = Field(ge=0)
    interval_ms: int = Field(gt=0)

    @field_validator("metric_id", mode="before")
    @classmethod
    def _metric(cls, value: object) -> str:
        return identifier(value, field="metric_id")


class SensorObservationContent(FingerprintedHardwareContract):
    content_type: Literal["SENSOR"] = "SENSOR"
    sensor_id: str
    value: float
    unit: str

    @field_validator("sensor_id", "unit", mode="before")
    @classmethod
    def _fields(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)


class BusObservationContent(FingerprintedHardwareContract):
    content_type: Literal["I2C", "SPI"]
    bus_id: str
    transaction_count: int = Field(ge=0)
    error_count: int = Field(ge=0)

    @field_validator("bus_id", mode="before")
    @classmethod
    def _bus(cls, value: object) -> str:
        return identifier(value, field="bus_id")


HardwareObservationContent = Annotated[
    UARTObservationContent
    | GPIOObservationContent
    | LEDObservationContent
    | InterruptObservationContent
    | SensorObservationContent
    | BusObservationContent,
    Field(discriminator="content_type"),
]


class HardwareObservation(FingerprintedHardwareContract):
    observation_id: str
    request_fingerprint: str
    project_id: str
    target: HardwareTargetReference
    firmware_fingerprint: str | None
    observation_type: HardwareObservationType
    content: HardwareObservationContent | None
    status: HardwareObservationStatus
    adapter_id: str
    evidence_refs: tuple[str, ...]
    observed_at: datetime

    @field_validator("observation_id", "project_id", "adapter_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("request_fingerprint", mode="before")
    @classmethod
    def _request_fp(cls, value: object) -> str:
        return fingerprint(value, field="request_fingerprint")

    @field_validator("firmware_fingerprint", mode="before")
    @classmethod
    def _firmware_fp(cls, value: object) -> str | None:
        return (
            None if value is None else fingerprint(value, field="firmware_fingerprint")
        )

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _evidence(cls, value: object) -> tuple[str, ...]:
        items = tuple(
            identifier(item, field="evidence_ref")
            for item in tuple_only(value, field="evidence_refs")
        )
        if len(items) != len(set(items)):
            raise ValueError("observation evidence is not unique")
        return items

    @field_validator("observed_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="observed_at")

    @model_validator(mode="after")
    def _state(self):
        if self.target.project_id != self.project_id:
            raise ValueError("observation target mismatch")
        if self.status is HardwareObservationStatus.OBSERVED and (
            self.content is None or not self.evidence_refs
        ):
            raise ValueError("observed result requires content and evidence")
        if self.status is HardwareObservationStatus.UNAVAILABLE and (
            self.content is not None or self.evidence_refs
        ):
            raise ValueError("unavailable result cannot claim evidence")
        expected_content = {
            HardwareObservationType.UART_BOOT_LOG: "UART",
            HardwareObservationType.UART_ERROR_LOG: "UART",
            HardwareObservationType.GPIO_STATE: "GPIO",
            HardwareObservationType.LED_STATE: "LED",
            HardwareObservationType.INTERRUPT_METRIC: "INTERRUPT",
            HardwareObservationType.SENSOR_SAMPLE: "SENSOR",
            HardwareObservationType.I2C_TRANSACTION_SUMMARY: "I2C",
            HardwareObservationType.SPI_TRANSACTION_SUMMARY: "SPI",
        }[self.observation_type]
        if self.content is not None and self.content.content_type != expected_content:
            raise ValueError("observation content type mismatch")
        return self


class HardwareResourceGrant(FingerprintedHardwareContract):
    project_id: str
    requester: str
    target_fingerprint: str
    capabilities: tuple[HardwareCapability, ...]

    @field_validator("project_id", "requester", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("target_fingerprint", mode="before")
    @classmethod
    def _target_fp(cls, value: object) -> str:
        return fingerprint(value, field="target_fingerprint")

    @field_validator("capabilities", mode="before")
    @classmethod
    def _caps(cls, value: object) -> tuple[HardwareCapability, ...]:
        items = tuple_only(value, field="capabilities")
        if not items or len(items) != len(set(items)):
            raise ValueError("resource capabilities are invalid")
        return tuple(sorted(items, key=lambda item: item.value))


class HardwareAuditEvent(FingerprintedHardwareContract):
    event_id: str
    event_type: HardwareAuditEventType
    project_id: str
    operation_fingerprint: str
    target_fingerprint: str
    result_fingerprint: str | None
    error_code: str | None
    timestamp: datetime

    @field_validator("event_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("operation_fingerprint", "target_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("result_fingerprint", mode="before")
    @classmethod
    def _result_fp(cls, value: object) -> str | None:
        return None if value is None else fingerprint(value, field="result_fingerprint")

    @field_validator("error_code", mode="before")
    @classmethod
    def _error(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="error_code")

    @field_validator("timestamp", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="timestamp")

    @model_validator(mode="after")
    def _identity(self):
        expected = deterministic_id(
            "hardware-event", self.operation_fingerprint, self.event_type.value
        )
        if self.event_id != expected:
            raise ValueError("hardware audit event id mismatch")
        return self


class HardwareRepairHandoff(FingerprintedHardwareContract):
    handoff_id: str
    project_id: str
    approved_patch_fingerprint: str
    repair_review_record_fingerprint: str
    firmware_artifact: FirmwareArtifactReference
    created_at: datetime

    @field_validator("handoff_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator(
        "approved_patch_fingerprint", "repair_review_record_fingerprint", mode="before"
    )
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("created_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="created_at")

    @model_validator(mode="after")
    def _binding(self):
        if self.firmware_artifact.project_id != self.project_id:
            raise ValueError("repair handoff project mismatch")
        return self


def approval_from_record(
    record: HardwareReviewRecord, *, maximum_age: timedelta, now: datetime
) -> HardwareApprovalReference:
    checked = _exact(record, HardwareReviewRecord, field="review_record")
    if (
        checked.decision is None
        or checked.decision.decision is not HardwareReviewDecisionType.APPROVE
    ):
        raise ValueError("hardware review is not approved")
    if utc_datetime(now, field="now") - checked.decision.decided_at > maximum_age:
        raise ValueError("hardware approval expired")
    return HardwareApprovalReference(
        review_id=checked.request.review_id,
        record_fingerprint=checked.fingerprint,
        plan_fingerprint=checked.request.plan_fingerprint,
        project_id=checked.request.project_id,
        operation=checked.request.operation,
        target_fingerprint=checked.request.target_fingerprint,
        firmware_fingerprint=checked.request.firmware_fingerprint,
        reviewer=checked.decision.reviewer,
        approved_at=checked.decision.decided_at,
    )


__all__ = tuple(
    name
    for name in globals()
    if name.startswith(
        ("Hardware", "Firmware", "UART", "GPIO", "LED", "Interrupt", "Sensor", "Bus")
    )
    or name == "approval_from_record"
)
