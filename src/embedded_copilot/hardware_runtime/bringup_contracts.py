from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .contracts import (
    FingerprintedHardwareContract,
    fingerprint,
    identifier,
    safe_text,
    tuple_only,
    utc_datetime,
)


class BringupResult(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    BLOCKED = "BLOCKED"
    UNAVAILABLE = "UNAVAILABLE"
    STALE = "STALE"


class HardwareConnectionStatus(StrEnum):
    CONNECTED = "CONNECTED"
    FAILED = "FAILED"
    BLOCKED_NO_HARDWARE = "BLOCKED_NO_HARDWARE"


class HardwareSessionStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


class HardwareLineage(FingerprintedHardwareContract):
    project_id: str
    pcb_sha256: str
    schematic_sha256: str
    firmware_sha256: str
    elf_sha256: str
    build_id: str
    design_truth_id: str
    hardware_session_id: str

    _ids = field_validator("project_id", "build_id", "design_truth_id", "hardware_session_id", mode="before")(
        lambda value, info: identifier(value, field=info.field_name)
    )
    _fps = field_validator("pcb_sha256", "schematic_sha256", "firmware_sha256", "elf_sha256", mode="before")(
        lambda value, info: fingerprint(value, field=info.field_name)
    )


class HardwareConnection(FingerprintedHardwareContract):
    connection_id: str
    project_id: str
    probe_type: Literal["ST_LINK", "OPENOCD", "PYOCD"]
    probe_serial: str
    target_mcu: Literal["STM32F103C8T6"]
    connection_time: datetime
    firmware_hash: str
    pcb_hash: str
    schematic_hash: str
    elf_hash: str
    build_id: str
    design_truth_id: str
    hardware_session_id: str
    status: HardwareConnectionStatus
    device_id: str | None = None
    reason_code: str = "CONNECTED"

    _ids = field_validator(
        "connection_id", "project_id", "probe_serial", "build_id", "design_truth_id", "hardware_session_id",
        "device_id", "reason_code", mode="before",
    )(lambda value, info: None if value is None else identifier(value, field=info.field_name))
    _fps = field_validator("firmware_hash", "pcb_hash", "schematic_hash", "elf_hash", mode="before")(
        lambda value, info: fingerprint(value, field=info.field_name)
    )
    _time = field_validator("connection_time", mode="before")(lambda value: utc_datetime(value, field="connection_time"))

    @model_validator(mode="after")
    def _status_binding(self):
        if self.status is HardwareConnectionStatus.CONNECTED and self.device_id is None:
            raise ValueError("connected hardware requires device id")
        if self.status is HardwareConnectionStatus.BLOCKED_NO_HARDWARE and self.device_id is not None:
            raise ValueError("blocked hardware cannot have device id")
        return self


class HardwareSession(FingerprintedHardwareContract):
    session_id: str
    project_id: str
    connection_id: str
    probe_serial: str
    target_mcu: Literal["STM32F103C8T6"]
    started_at: datetime
    ended_at: datetime | None = None
    status: HardwareSessionStatus = HardwareSessionStatus.OPEN

    _ids = field_validator("session_id", "project_id", "connection_id", "probe_serial", mode="before")(
        lambda value, info: identifier(value, field=info.field_name)
    )
    _time = field_validator("started_at", "ended_at", mode="before")(
        lambda value, info: None if value is None else utc_datetime(value, field=info.field_name)
    )

    @model_validator(mode="after")
    def _state(self):
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("hardware session ended before it started")
        if self.status is HardwareSessionStatus.OPEN and self.ended_at is not None:
            raise ValueError("open hardware session cannot be ended")
        return self


class HardwareToolResult(FingerprintedHardwareContract):
    """Sanitised result returned by an injected real-tool adapter."""

    result: BringupResult
    reason_code: str
    detail: str
    observed_from_hardware: bool
    device_id: str | None = None
    target_detected: bool = False
    flash_success: bool = False
    memory_verify: bool = False
    artifact_hash_match: bool = False
    vector_table: int | None = Field(default=None, ge=0, le=0xFFFFFFFF)
    reset_handler: int | None = Field(default=None, ge=0, le=0xFFFFFFFF)
    stack_pointer: int | None = Field(default=None, ge=0, le=0xFFFFFFFF)
    mcu_state: str | None = None
    observed_lines: tuple[str, ...] = ()
    observed_at: datetime

    _ids = field_validator("reason_code", "device_id", "mcu_state", mode="before")(
        lambda value, info: None if value is None else identifier(value, field=info.field_name)
    )
    _detail = field_validator("detail", mode="before")(lambda value: safe_text(value, field="detail", maximum=1024))
    _lines = field_validator("observed_lines", mode="before")(
        lambda value: tuple(safe_text(item, field="observed_line", maximum=256) for item in tuple_only(value, field="observed_lines"))
    )
    _time = field_validator("observed_at", mode="before")(lambda value: utc_datetime(value, field="observed_at"))

    @model_validator(mode="after")
    def _no_unobserved_pass(self):
        if self.result is BringupResult.PASS and not self.observed_from_hardware:
            raise ValueError("hardware PASS requires real observation")
        return self


class FlashEvidence(FingerprintedHardwareContract):
    evidence_id: str
    operation_id: str
    hardware_session_id: str
    firmware_sha256: str
    device_id: str
    probe_serial: str
    timestamp: datetime
    target_detected: bool
    flash_success: bool
    memory_verify: bool
    artifact_hash_match: bool
    result: BringupResult
    lineage: HardwareLineage
    source: Literal["REAL_HARDWARE"] = "REAL_HARDWARE"

    _ids = field_validator("evidence_id", "operation_id", "hardware_session_id", "device_id", "probe_serial", mode="before")(
        lambda value, info: identifier(value, field=info.field_name)
    )
    _fp = field_validator("firmware_sha256", mode="before")(lambda value: fingerprint(value, field="firmware_sha256"))
    _time = field_validator("timestamp", mode="before")(lambda value: utc_datetime(value, field="timestamp"))

    @model_validator(mode="after")
    def _binding(self):
        if self.hardware_session_id != self.lineage.hardware_session_id or self.firmware_sha256 != self.lineage.firmware_sha256:
            raise ValueError("flash evidence lineage mismatch")
        if self.result is BringupResult.PASS and not all((self.target_detected, self.flash_success, self.memory_verify, self.artifact_hash_match)):
            raise ValueError("flash PASS requires all verification checks")
        return self


class BootEvidence(FingerprintedHardwareContract):
    evidence_id: str
    operation_id: str
    hardware_session_id: str
    timestamp: datetime
    vector_table: int | None
    reset_handler: int | None
    stack_pointer: int | None
    mcu_state: str | None
    result: BringupResult
    lineage: HardwareLineage
    source: Literal["REAL_HARDWARE"] = "REAL_HARDWARE"

    _ids = field_validator("evidence_id", "operation_id", "hardware_session_id", mode="before")(
        lambda value, info: identifier(value, field=info.field_name)
    )
    _time = field_validator("timestamp", mode="before")(lambda value: utc_datetime(value, field="timestamp"))

    @model_validator(mode="after")
    def _binding(self):
        if self.hardware_session_id != self.lineage.hardware_session_id:
            raise ValueError("boot evidence lineage mismatch")
        if self.result is BringupResult.PASS and any(value is None for value in (self.vector_table, self.reset_handler, self.stack_pointer, self.mcu_state)):
            raise ValueError("boot PASS requires vector table, reset handler, stack pointer and MCU state")
        return self


class UartEvidence(FingerprintedHardwareContract):
    evidence_id: str
    operation_id: str
    hardware_session_id: str
    firmware_sha256: str
    timestamp: datetime
    baud_rate: int = Field(ge=1200, le=4_000_000)
    expected: tuple[str, ...]
    observed: tuple[str, ...]
    matched: tuple[str, ...]
    result: BringupResult
    lineage: HardwareLineage
    source: Literal["REAL_HARDWARE"] = "REAL_HARDWARE"

    _ids = field_validator("evidence_id", "operation_id", "hardware_session_id", mode="before")(
        lambda value, info: identifier(value, field=info.field_name)
    )
    _fp = field_validator("firmware_sha256", mode="before")(lambda value: fingerprint(value, field="firmware_sha256"))
    _tuples = field_validator("expected", "observed", "matched", mode="before")(
        lambda value, info: tuple(safe_text(item, field=info.field_name.removesuffix("d"), maximum=256) for item in tuple_only(value, field=info.field_name))
    )
    _time = field_validator("timestamp", mode="before")(lambda value: utc_datetime(value, field="timestamp"))

    @model_validator(mode="after")
    def _binding(self):
        if self.hardware_session_id != self.lineage.hardware_session_id or self.firmware_sha256 != self.lineage.firmware_sha256:
            raise ValueError("UART evidence lineage mismatch")
        if self.result is BringupResult.PASS and set(self.expected) - set(self.matched):
            raise ValueError("UART PASS requires every expected pattern")
        return self


HardwareEvidence = FlashEvidence | BootEvidence | UartEvidence | HardwareConnection | HardwareSession


class GateCheck(FingerprintedHardwareContract):
    name: str
    status: BringupResult
    reason: str

    _ids = field_validator("name", mode="before")(lambda value: identifier(value, field="name"))
    _reason = field_validator("reason", mode="before")(lambda value: safe_text(value, field="reason", maximum=256))


class HardwareBringupGateResult(FingerprintedHardwareContract):
    gate_id: Literal["EMBRIX_HARDWARE_BRINGUP_GATE"] = "EMBRIX_HARDWARE_BRINGUP_GATE"
    status: Literal["PASS", "BLOCKED", "FAIL"]
    checks: tuple[GateCheck, ...]
    summary: str

    @field_validator("checks", mode="before")
    @classmethod
    def _checks(cls, value: object) -> tuple[GateCheck, ...]:
        return tuple(GateCheck.model_validate(item.model_dump(mode="python") if isinstance(item, GateCheck) else item) for item in tuple_only(value, field="checks"))

    _summary = field_validator("summary", mode="before")(lambda value: safe_text(value, field="summary", maximum=512))


__all__ = tuple(name for name in globals() if not name.startswith("_"))
