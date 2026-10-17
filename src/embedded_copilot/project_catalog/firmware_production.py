from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from embedded_copilot.desktop_copilot_chat.contracts import (
    FingerprintedChatContract,
    fingerprint,
    identifier,
    safe_text,
    utc_datetime,
)
from embedded_copilot.engineering_consistency.contracts import (
    ConsistencyCheck,
    ConsistencyCheckStatus,
    ConsistencyEvidence,
    ConsistencyOverallStatus,
    HardwareFirmwareConsistencyResult,
    VerificationLevel,
)
from embedded_copilot.engineering_copilot import FirmwareSpecification
from embedded_copilot.firmware_engineering import FirmwareBuildArtifact


def _tupleize(value: object) -> object:
    if isinstance(value, list):
        return tuple(_tupleize(item) for item in value)
    if isinstance(value, dict):
        return {key: _tupleize(item) for key, item in value.items()}
    return value


def _restore_consistency(value: object) -> HardwareFirmwareConsistencyResult:
    payload = _tupleize(value)
    if not isinstance(payload, dict):
        raise TypeError("consistency result is invalid")

    def restore_check(item: object) -> ConsistencyCheck:
        if isinstance(item, ConsistencyCheck):
            return item
        if not isinstance(item, dict):
            raise TypeError("consistency check is invalid")
        checked = dict(item)
        checked["status"] = (
            checked["status"]
            if isinstance(checked["status"], ConsistencyCheckStatus)
            else ConsistencyCheckStatus(checked["status"])
        )
        return ConsistencyCheck.model_validate(checked, strict=False)

    checks = tuple(restore_check(item) for item in payload["checks"])
    evidence_payload = dict(payload["evidence"])
    evidence_payload["checks"] = checks
    for field in ("artifact_integrity", "vector_table_status", "swd_mapping_status"):
        evidence_payload[field] = (
            evidence_payload[field]
            if isinstance(evidence_payload[field], ConsistencyCheckStatus)
            else ConsistencyCheckStatus(evidence_payload[field])
        )
    payload["checks"] = checks
    payload["evidence"] = ConsistencyEvidence.model_validate(evidence_payload, strict=False)
    return HardwareFirmwareConsistencyResult.model_validate(payload, strict=False)


class FirmwareProductionStatus(StrEnum):
    BUILD_FAILED = "BUILD_FAILED"
    BUILD_PASS = "BUILD_PASS"
    CONSISTENCY_FAILED = "CONSISTENCY_FAILED"
    FIRMWARE_GATE_PASS = "FIRMWARE_GATE_PASS"
    STALE = "STALE"


class FirmwareBuildArtifactProjection(FingerprintedChatContract):
    artifact_id: str
    artifact_type: Literal["ELF", "BIN", "HEX", "MAP", "LOG"]
    relative_path: str
    content_fingerprint: str
    size_bytes: int = Field(ge=0, le=512 * 1024 * 1024)
    build_receipt_id: str

    _ids = field_validator("artifact_id", "build_receipt_id", mode="before")(
        lambda value, info: identifier(value, field=info.field_name)
    )
    @field_validator("relative_path", mode="before")
    @classmethod
    def _relative_path(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("relative_path is invalid")
        normalized = value.replace("\\", "/")
        if (
            not normalized
            or normalized.startswith("/")
            or ":" in normalized
            or any(part in {"", ".", ".."} for part in normalized.split("/"))
        ):
            raise ValueError("relative_path is invalid")
        return normalized

    _content = field_validator("content_fingerprint", mode="before")(
        lambda value: fingerprint(value, field="content_fingerprint")
    )


class FirmwareBuildReceiptProjection(FingerprintedChatContract):
    receipt_id: str
    project_id: str
    conversation_id: str
    operation_id: str
    operation_receipt_fingerprint: str
    request_fingerprint: str
    firmware_spec_id: str
    firmware_plan_fingerprint: str
    hw_fw_binding_fingerprint: str
    design_truth_id: str
    design_truth_fingerprint: str
    implementation_id: str
    implementation_fingerprint: str
    build_configuration_id: str
    build_configuration_fingerprint: str
    source_fingerprints: tuple[str, ...] = ()
    status: Literal[
        "BUILD_PASS", "BUILD_FAIL", "BUILD_BLOCKED", "BUILD_TIMEOUT", "BUILD_UNAVAILABLE"
    ]
    toolchain_id: str
    toolchain_version: str
    build_system: str
    artifact_ids: tuple[str, ...] = ()
    log_artifact_id: str | None = None
    raw_evidence_artifact_id: str | None = None
    configure_exit_code: int | None = Field(default=None, ge=0)
    compiler_exit_code: int | None = Field(default=None, ge=0)
    assembler_exit_code: int | None = Field(default=None, ge=0)
    linker_exit_code: int | None = Field(default=None, ge=0)
    objcopy_exit_code: int | None = Field(default=None, ge=0)
    tool_observed: bool = False
    created_at: datetime

    _ids = field_validator(
        "receipt_id", "project_id", "conversation_id", "operation_id", "firmware_spec_id",
        "design_truth_id", "implementation_id", "build_configuration_id", "toolchain_id",
        "toolchain_version", "build_system", "log_artifact_id", "raw_evidence_artifact_id", mode="before",
    )(
        lambda value, info: None if value is None else identifier(value, field=info.field_name)
    )
    _fps = field_validator(
        "operation_receipt_fingerprint", "request_fingerprint", "firmware_plan_fingerprint",
        "hw_fw_binding_fingerprint", "design_truth_fingerprint", "implementation_fingerprint",
        "build_configuration_fingerprint", mode="before",
    )(
        lambda value, info: fingerprint(value, field=info.field_name)
    )
    _sources = field_validator("source_fingerprints", mode="before")(
        lambda value: tuple(fingerprint(item, field="source_fingerprint") for item in value)
        if isinstance(value, (tuple, list)) else (_ for _ in ()).throw(ValueError("source_fingerprints is invalid"))
    )
    _artifacts = field_validator("artifact_ids", mode="before")(
        lambda value: tuple(identifier(item, field="artifact_id") for item in value)
        if isinstance(value, (tuple, list)) else (_ for _ in ()).throw(ValueError("artifact_ids is invalid"))
    )
    _time = field_validator("created_at", mode="before")(
        lambda value: utc_datetime(value, field="created_at")
    )

    @model_validator(mode="after")
    def _raw_evidence_binding(self) -> "FirmwareBuildReceiptProjection":
        if self.raw_evidence_artifact_id is not None and self.raw_evidence_artifact_id not in self.artifact_ids:
            raise ValueError("firmware raw evidence artifact binding mismatch")
        return self


class FirmwareProductionState(FingerprintedChatContract):
    production_id: str
    project_id: str
    conversation_id: str
    firmware_spec_id: str
    firmware_plan_fingerprint: str
    hw_fw_binding_fingerprint: str
    design_truth_id: str
    design_truth_fingerprint: str
    implementation_id: str
    implementation_fingerprint: str
    build_configuration_id: str
    build_configuration_fingerprint: str
    source_fingerprints: tuple[str, ...] = ()
    build_receipt: FirmwareBuildReceiptProjection
    artifacts: tuple[FirmwareBuildArtifactProjection, ...] = ()
    consistency_result: HardwareFirmwareConsistencyResult | None = None
    status: FirmwareProductionStatus
    stale_inputs: tuple[str, ...] = ()
    updated_at: datetime

    _ids = field_validator(
        "production_id", "project_id", "conversation_id", "firmware_spec_id", "design_truth_id",
        "implementation_id", "build_configuration_id", mode="before",
    )(
        lambda value, info: identifier(value, field=info.field_name)
    )
    _fps = field_validator(
        "firmware_plan_fingerprint", "hw_fw_binding_fingerprint", "design_truth_fingerprint",
        "implementation_fingerprint", "build_configuration_fingerprint", mode="before",
    )(
        lambda value, info: fingerprint(value, field=info.field_name)
    )
    _sources = field_validator("source_fingerprints", mode="before")(
        lambda value: tuple(fingerprint(item, field="source_fingerprint") for item in value)
        if isinstance(value, (tuple, list)) else (_ for _ in ()).throw(ValueError("source_fingerprints is invalid"))
    )
    _artifacts = field_validator("artifacts", mode="before")(
        lambda value: tuple(
            item if isinstance(item, FirmwareBuildArtifactProjection)
            else FirmwareBuildArtifactProjection.model_validate(item, strict=False)
            for item in value
        )
        if isinstance(value, (tuple, list)) else (_ for _ in ()).throw(ValueError("artifacts is invalid"))
    )
    _stale = field_validator("stale_inputs", mode="before")(
        lambda value: tuple(safe_text(item, field="stale_input", maximum=512) for item in value)
        if isinstance(value, (tuple, list)) else (_ for _ in ()).throw(ValueError("stale_inputs is invalid"))
    )
    _time = field_validator("updated_at", mode="before")(
        lambda value: utc_datetime(value, field="updated_at")
    )

    @field_validator("build_receipt", mode="before")
    @classmethod
    def _receipt(cls, value: object) -> FirmwareBuildReceiptProjection:
        return value if isinstance(value, FirmwareBuildReceiptProjection) else FirmwareBuildReceiptProjection.model_validate(value, strict=False)

    @field_validator("consistency_result", mode="before")
    @classmethod
    def _consistency(cls, value: object) -> HardwareFirmwareConsistencyResult | None:
        if value is None:
            return None
        if isinstance(value, HardwareFirmwareConsistencyResult):
            return value
        return _restore_consistency(value)

    @model_validator(mode="after")
    def _binding(self) -> "FirmwareProductionState":
        receipt = self.build_receipt
        if (
            receipt.project_id != self.project_id
            or receipt.conversation_id != self.conversation_id
            or receipt.firmware_spec_id != self.firmware_spec_id
            or receipt.firmware_plan_fingerprint != self.firmware_plan_fingerprint
            or receipt.hw_fw_binding_fingerprint != self.hw_fw_binding_fingerprint
            or receipt.design_truth_id != self.design_truth_id
            or receipt.design_truth_fingerprint != self.design_truth_fingerprint
            or receipt.implementation_id != self.implementation_id
            or receipt.implementation_fingerprint != self.implementation_fingerprint
            or receipt.build_configuration_id != self.build_configuration_id
            or receipt.build_configuration_fingerprint != self.build_configuration_fingerprint
            or receipt.source_fingerprints != self.source_fingerprints
        ):
            raise ValueError("firmware production receipt binding mismatch")
        if any(
            item.build_receipt_id != receipt.receipt_id
            or item.artifact_id not in (*receipt.artifact_ids, receipt.log_artifact_id)
            for item in self.artifacts
        ):
            raise ValueError("firmware production artifact binding mismatch")
        result = self.consistency_result
        if result is not None:
            if result.project_id != self.project_id or (
                result.conversation_id is not None and result.conversation_id != self.conversation_id
            ):
                raise ValueError("firmware production consistency identity mismatch")
            if result.firmware_specification_fingerprint != self.firmware_plan_fingerprint:
                raise ValueError("firmware production consistency plan mismatch")
            if result.design_truth_fingerprint != self.design_truth_fingerprint:
                raise ValueError("firmware production consistency DesignTruth mismatch")
            if result.implementation_fingerprint != self.implementation_fingerprint:
                raise ValueError("firmware production consistency implementation mismatch")
            if result.build_configuration_fingerprint != self.build_configuration_fingerprint:
                raise ValueError("firmware production consistency build configuration mismatch")
            if (
                result.build_receipt_id != receipt.receipt_id
                and result.build_operation_id != receipt.operation_id
            ):
                raise ValueError("firmware production consistency receipt mismatch")
        if self.status is FirmwareProductionStatus.FIRMWARE_GATE_PASS:
            if (
                receipt.status != "BUILD_PASS"
                or result is None
                or result.overall_status is not ConsistencyOverallStatus.CONSISTENT
                or result.verification_level is not VerificationLevel.L3_ENGINEERING_VERIFIED
                or result.stale_inputs
                or self.stale_inputs
            ):
                raise ValueError("firmware gate pass requires verified build and consistency")
        if self.status is FirmwareProductionStatus.STALE and not self.stale_inputs:
            raise ValueError("stale firmware production requires stale inputs")
        return self

    def mark_stale(self, *, reason: str, updated_at: datetime) -> "FirmwareProductionState":
        checked_reason = safe_text(reason, field="stale_reason", maximum=512)
        return self.model_copy(
            update={
                "status": FirmwareProductionStatus.STALE,
                "stale_inputs": tuple(dict.fromkeys((*self.stale_inputs, checked_reason))),
                "updated_at": updated_at,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )


def firmware_binding_fingerprint(specification: FirmwareSpecification) -> str:
    """Fingerprint the typed HW/FW bindings independently from the firmware plan."""

    payload = {
        "firmware_spec_id": specification.firmware_spec_id,
        "design_truth_id": specification.design_truth_id,
        "design_truth_fingerprint": specification.design_truth_fingerprint,
        "bindings": [item.model_dump(mode="json") for item in specification.bindings],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def firmware_production_id(project_id: str, conversation_id: str) -> str:
    digest = hashlib.sha256(f"{project_id}|{conversation_id}".encode("utf-8")).hexdigest()[:32]
    return f"firmware-production-{digest}"


def artifact_projection(
    artifact: FirmwareBuildArtifact,
    *,
    build_receipt_id: str,
) -> FirmwareBuildArtifactProjection:
    return FirmwareBuildArtifactProjection(
        artifact_id=artifact.reference,
        artifact_type=artifact.artifact_type,
        relative_path=artifact.relative_path,
        content_fingerprint=artifact.content_fingerprint,
        size_bytes=artifact.size_bytes,
        build_receipt_id=build_receipt_id,
    )


__all__ = [
    "FirmwareBuildArtifactProjection",
    "FirmwareBuildReceiptProjection",
    "FirmwareProductionState",
    "FirmwareProductionStatus",
    "artifact_projection",
    "firmware_binding_fingerprint",
    "firmware_production_id",
]
