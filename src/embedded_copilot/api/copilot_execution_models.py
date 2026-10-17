from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from embedded_copilot.correlation import CorrelationContext
from embedded_copilot.execution_worker.contracts import (
    ArtifactIdentity,
    BuildEvidence,
    NativeMoveOfflineResult,
    NativeMoveProductionResultV1,
    HilScenario,
    OperationEvent,
    OperationReceipt,
    OperationStatusView,
    OperationApprovalGrant,
    safe_text,
    identifier,
    fingerprint,
    utc_datetime,
)


class ExecutionApiContract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid", hide_input_in_errors=True)


class OperationCreateBase(ExecutionApiContract):
    project_id: str
    target_id: str
    target_kind: Literal["ESP32-S3", "STM32F446RE", "STM32F103C8T6"]
    target_fingerprint: str
    workspace_snapshot_id: str
    workspace_fingerprint: str
    proposal_id: str
    proposal_fingerprint: str
    nonce: str

    _ids = field_validator("project_id", "target_id", "target_kind", "workspace_snapshot_id", "proposal_id", "nonce", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _fps = field_validator("target_fingerprint", "workspace_fingerprint", "proposal_fingerprint", mode="before")(lambda value, info: fingerprint(value, field=info.field_name))


class BuildOperationCreateRequest(OperationCreateBase):
    toolchain_id: Literal["ESP_IDF_BUILD", "CMAKE_NINJA_BUILD", "ARM_NONE_EABI_GCC"]
    toolchain_version: str
    tool_family: Literal["ESP_IDF", "CMAKE_NINJA", "ARM_NONE_EABI_GCC"]
    configuration: str
    expected_artifact_type: Literal["ELF", "BIN", "HEX", "MAP"]

    _text = field_validator("toolchain_version", "configuration", mode="before")(lambda value, info: identifier(value, field=info.field_name))


class FlashOperationCreateRequest(OperationCreateBase):
    artifact: ArtifactIdentity
    tool_id: Literal["ESPTOOL_FLASH", "OPENOCD_FLASH", "STLINK_FLASH"]
    tool_version: str

    _text = field_validator("tool_version", mode="before")(lambda value: identifier(value, field="tool_version"))


class TestOperationCreateRequest(ExecutionApiContract):
    project_id: str
    target_id: str
    target_fingerprint: str
    workspace_id: str
    workspace_fingerprint: str
    proposal_id: str
    proposal_fingerprint: str
    nonce: str
    test_suite_id: str
    test_profile_id: str
    artifact_identity: ArtifactIdentity | None = None

    _ids = field_validator(
        "project_id",
        "target_id",
        "workspace_id",
        "proposal_id",
        "nonce",
        "test_suite_id",
        "test_profile_id",
        mode="before",
    )(lambda value, info: identifier(value, field=info.field_name))
    _fps = field_validator(
        "target_fingerprint",
        "workspace_fingerprint",
        "proposal_fingerprint",
        mode="before",
    )(lambda value, info: fingerprint(value, field=info.field_name))

    @model_validator(mode="after")
    def _artifact_binding(self):
        if self.artifact_identity is not None and (
            self.artifact_identity.project_id != self.project_id
            or self.artifact_identity.target_id != self.target_id
            or self.artifact_identity.workspace_fingerprint != self.workspace_fingerprint
        ):
            raise ValueError("artifact target binding mismatch")
        return self


class UartOperationCreateRequest(OperationCreateBase):
    port_binding_id: str
    baud_rate: Literal[9600, 19200, 38400, 57600, 115200, 230400]
    duration_seconds: int = Field(ge=1, le=300)
    max_bytes: int = Field(ge=1, le=1024 * 1024)
    expected_patterns: tuple[str, ...] = ()

    _port = field_validator("port_binding_id", mode="before")(lambda value: identifier(value, field="port_binding_id"))
    _patterns = field_validator("expected_patterns", mode="before")(lambda value: tuple(safe_text(item, field="expected_pattern", maximum=128) for item in value) if isinstance(value, (tuple, list)) and len(value) <= 32 else (_ for _ in ()).throw(ValueError("expected_patterns is invalid")))


class HilOperationCreateRequest(OperationCreateBase):
    scenario: HilScenario
    flash_operation_id: str

    _flash = field_validator("flash_operation_id", mode="before")(lambda value: identifier(value, field="flash_operation_id"))


class OperationApprovalRequest(ExecutionApiContract):
    reviewer_id: str
    nonce: str
    expires_at: datetime

    _ids = field_validator("reviewer_id", "nonce", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _time = field_validator("expires_at", mode="before")(lambda value: utc_datetime(value, field="expires_at"))


class OperationCancelRequest(ExecutionApiContract):
    reason: str

    _reason = field_validator("reason", mode="before")(lambda value: safe_text(value, field="reason", maximum=256))


class SchematicApprovalContext(ExecutionApiContract):
    fingerprint: str
    requester_id: str
    expires_at: datetime
    conversation_id: str
    schematic_artifact_fingerprint: str
    source_operation_id: str
    source_operation_receipt_fingerprint: str
    source_parse_operation_id: str | None
    source_parse_receipt_fingerprint: str | None
    proposal_id: str
    proposal_fingerprint: str

    _ids = field_validator("requester_id", "conversation_id", "source_operation_id", "proposal_id", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _fps = field_validator("fingerprint", "schematic_artifact_fingerprint", "source_operation_receipt_fingerprint", "proposal_fingerprint", mode="before")(lambda value, info: fingerprint(value, field=info.field_name))
    _time = field_validator("expires_at", mode="before")(lambda value: utc_datetime(value, field="expires_at"))

    @model_validator(mode="after")
    def _parse_pair(self):
        if (self.source_parse_operation_id is None) != (self.source_parse_receipt_fingerprint is None):
            raise ValueError("ERC parse context is incomplete")
        if self.source_parse_operation_id is not None:
            identifier(self.source_parse_operation_id, field="source_parse_operation_id")
            fingerprint(self.source_parse_receipt_fingerprint)
        return self


class OperationStatusResponse(ExecutionApiContract):
    operation_id: str
    tenant_id: str
    project_id: str
    operation_type: str
    status: str
    approval_id: str | None
    worker_id: str | None
    recovery_state: str
    cancellation_requested: bool
    updated_at: datetime
    receipt_fingerprint: str | None
    safe_detail: str
    target_id: str | None = None
    workspace_id: str | None = None
    correlation: CorrelationContext | None = None
    schematic_approval_context: SchematicApprovalContext | None = None


class OperationApprovalResponse(ExecutionApiContract):
    grant_id: str
    tenant_id: str
    project_id: str
    approval_purpose: str
    operation_id: str
    operation_type: str
    operation_fingerprint: str
    artifact_id: str | None
    artifact_sha256: str | None
    reviewer_id: str
    status: str
    expires_at: datetime
    fingerprint: str


class OperationEventResponse(ExecutionApiContract):
    event_id: str
    sequence: int
    tenant_id: str
    project_id: str
    operation_id: str
    operation_fingerprint: str
    operation_type: str
    event_type: str
    status: str
    recovery_state: str
    approval_id: str | None
    receipt_fingerprint: str | None
    reason_code: str
    occurred_at: datetime
    fingerprint: str
    correlation: CorrelationContext | None = None


class BuildEvidenceResponse(ExecutionApiContract):
    operation_id: str
    request_fingerprint: str
    result_status: str
    toolchain_id: str
    toolchain_version: str
    source_fingerprints: tuple[str, ...]
    output_artifact_ids: tuple[str, ...]
    log_artifact_id: str | None
    tool_observed: bool
    build_system: str | None
    compiler_exit_code: int | None
    linker_exit_code: int | None
    elf_verified: bool
    bin_verified: bool
    created_at: datetime
    fingerprint: str


class OperationReceiptResponse(ExecutionApiContract):
    operation_id: str
    tenant_id: str
    project_id: str
    operation_type: str
    status: str
    tool_execution_status: str
    flash_verification_status: str
    target_runtime_verification_status: str
    artifact: ArtifactIdentity | None
    build_evidence: BuildEvidenceResponse | None
    native_move_offline: NativeMoveOfflineResult | None = None
    native_move_production: NativeMoveProductionResultV1 | None = None
    source_revision: str | None = None
    evidence_references: tuple[str, ...]
    safe_diagnostics_summary: str
    recovery_state: str
    created_at: datetime
    fingerprint: str


def _public_artifact_reference(value: str) -> bool:
    return bool(value) and all(char.isascii() and (char.isalnum() or char in "._-") for char in value)


def receipt_response(
    value: OperationReceipt, *, source_revision: str | None = None
) -> OperationReceiptResponse:
    evidence: BuildEvidence | None = value.build_evidence
    artifact = value.artifact
    artifact_references = () if evidence is None else evidence.output_artifact_ids
    if (
        artifact is not None and not _public_artifact_reference(artifact.artifact_id)
        or any(not _public_artifact_reference(ref) for ref in artifact_references)
        or evidence is not None and evidence.log_artifact_id is not None and not _public_artifact_reference(evidence.log_artifact_id)
    ):
        raise ValueError("receipt artifact reference is not public-safe")
    evidence_ref = f"build-evidence-{value.operation_id}"
    evidence_references = (
        (evidence_ref,)
        if value.operation_type.value == "BUILD"
        and evidence is not None
        and _public_artifact_reference(evidence_ref)
        and evidence_ref in value.evidence_references
        else ()
    )
    baseline_summary = None
    if value.operation_type.value == "KICAD_PCB_DRC_BASELINE":
        import re
        if any(not _public_artifact_reference(ref) for ref in value.evidence_references):
            raise ValueError("baseline artifact reference is not public-safe")
        evidence_references = value.evidence_references
        if value.status.value == "SUCCEEDED":
            if re.fullmatch(r"DRC_BASELINE_OBSERVATION scope=BASELINE_COMPARISON_ONLY error_count=(0|[1-9][0-9]*) warning_count=(0|[1-9][0-9]*)", value.safe_diagnostics_summary) is None:
                raise ValueError("baseline actual observation summary unavailable")
            baseline_summary = value.safe_diagnostics_summary
        else:
            baseline_summary = "DRC_BASELINE_OBSERVATION_UNAVAILABLE"
    return OperationReceiptResponse(
        operation_id=value.operation_id, tenant_id=value.tenant_id, project_id=value.project_id,
        operation_type=value.operation_type.value, status=value.status.value,
        tool_execution_status=value.tool_execution_status,
        flash_verification_status=value.flash_verification_status,
        target_runtime_verification_status=value.target_runtime_verification_status,
        artifact=artifact,
        source_revision=source_revision,
        evidence_references=evidence_references,
        safe_diagnostics_summary=(baseline_summary if baseline_summary is not None else evidence.result_status if evidence is not None else value.tool_execution_status),
        build_evidence=None if evidence is None else BuildEvidenceResponse(
            operation_id=evidence.operation_id, request_fingerprint=evidence.request_fingerprint,
            result_status=evidence.result_status, toolchain_id=evidence.toolchain_id,
            toolchain_version=evidence.toolchain_version, source_fingerprints=evidence.source_fingerprints,
            output_artifact_ids=evidence.output_artifact_ids, log_artifact_id=evidence.log_artifact_id,
            tool_observed=evidence.tool_observed,
            build_system=evidence.build_system, compiler_exit_code=evidence.compiler_exit_code,
            linker_exit_code=evidence.linker_exit_code, elf_verified=evidence.elf_verified,
            bin_verified=evidence.bin_verified, created_at=evidence.created_at,
            fingerprint=evidence.fingerprint,
        ),
        native_move_offline=value.native_move_offline,
        native_move_production=value.native_move_production,
        recovery_state=value.recovery_state.value, created_at=value.created_at,
        fingerprint=value.fingerprint,
    )


def status_response(
    value: OperationStatusView,
    *,
    target_id: str | None = None,
    workspace_id: str | None = None,
    schematic_approval_context: SchematicApprovalContext | None = None,
) -> OperationStatusResponse:
    return OperationStatusResponse(
        operation_id=value.operation_id, tenant_id=value.tenant_id, project_id=value.project_id,
        operation_type=value.operation_type.value, status=value.status.value, approval_id=value.approval_id,
        worker_id=value.worker_id, recovery_state=value.recovery_state.value, cancellation_requested=value.cancellation_requested,
        updated_at=value.updated_at, receipt_fingerprint=value.receipt_fingerprint, safe_detail=value.safe_detail,
        target_id=target_id, workspace_id=workspace_id,
        correlation=value.correlation, schematic_approval_context=schematic_approval_context,
    )


def approval_response(value: OperationApprovalGrant) -> OperationApprovalResponse:
    return OperationApprovalResponse(
        grant_id=value.grant_id, tenant_id=value.tenant_id, project_id=value.project_id,
        approval_purpose=value.approval_purpose.value, operation_id=value.operation_id,
        operation_type=value.operation_type.value, operation_fingerprint=value.operation_fingerprint,
        artifact_id=value.artifact_id, artifact_sha256=value.artifact_sha256, reviewer_id=value.reviewer_id,
        status=value.status.value, expires_at=value.expires_at, fingerprint=value.fingerprint,
    )


def event_response(value: OperationEvent) -> OperationEventResponse:
    return OperationEventResponse(
        event_id=value.event_id,
        sequence=value.sequence,
        tenant_id=value.tenant_id,
        project_id=value.project_id,
        operation_id=value.operation_id,
        operation_fingerprint=value.operation_fingerprint,
        operation_type=value.operation_type.value,
        event_type=value.event_type.value,
        status=value.status.value,
        recovery_state=value.recovery_state.value,
        approval_id=value.approval_id,
        receipt_fingerprint=value.receipt_fingerprint,
        reason_code=value.reason_code,
        occurred_at=value.occurred_at,
        fingerprint=value.fingerprint,
        correlation=value.correlation,
    )


__all__ = (
    "BuildOperationCreateRequest", "FlashOperationCreateRequest", "HilOperationCreateRequest",
    "OperationApprovalRequest", "OperationApprovalResponse", "OperationCancelRequest",
    "OperationEventResponse", "OperationStatusResponse", "TestOperationCreateRequest", "UartOperationCreateRequest",
    "BuildEvidenceResponse", "OperationReceiptResponse", "approval_response", "event_response", "receipt_response", "status_response",
)
