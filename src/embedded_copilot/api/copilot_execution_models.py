from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from embedded_copilot.execution_worker.contracts import (
    ArtifactIdentity,
    HilScenario,
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
    target_kind: Literal["ESP32-S3", "STM32F446RE"]
    target_fingerprint: str
    workspace_snapshot_id: str
    workspace_fingerprint: str
    proposal_id: str
    proposal_fingerprint: str
    nonce: str

    _ids = field_validator("project_id", "target_id", "target_kind", "workspace_snapshot_id", "proposal_id", "nonce", mode="before")(lambda value, info: identifier(value, field=info.field_name))
    _fps = field_validator("target_fingerprint", "workspace_fingerprint", "proposal_fingerprint", mode="before")(lambda value, info: fingerprint(value, field=info.field_name))


class BuildOperationCreateRequest(OperationCreateBase):
    toolchain_id: Literal["ESP_IDF_BUILD", "CMAKE_NINJA_BUILD"]
    toolchain_version: str
    tool_family: Literal["ESP_IDF", "CMAKE_NINJA"]
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


def status_response(
    value: OperationStatusView,
    *,
    target_id: str | None = None,
    workspace_id: str | None = None,
) -> OperationStatusResponse:
    return OperationStatusResponse(
        operation_id=value.operation_id, tenant_id=value.tenant_id, project_id=value.project_id,
        operation_type=value.operation_type.value, status=value.status.value, approval_id=value.approval_id,
        worker_id=value.worker_id, recovery_state=value.recovery_state.value, cancellation_requested=value.cancellation_requested,
        updated_at=value.updated_at, receipt_fingerprint=value.receipt_fingerprint, safe_detail=value.safe_detail,
        target_id=target_id, workspace_id=workspace_id,
    )


def approval_response(value: OperationApprovalGrant) -> OperationApprovalResponse:
    return OperationApprovalResponse(
        grant_id=value.grant_id, tenant_id=value.tenant_id, project_id=value.project_id,
        approval_purpose=value.approval_purpose.value, operation_id=value.operation_id,
        operation_type=value.operation_type.value, operation_fingerprint=value.operation_fingerprint,
        artifact_id=value.artifact_id, artifact_sha256=value.artifact_sha256, reviewer_id=value.reviewer_id,
        status=value.status.value, expires_at=value.expires_at, fingerprint=value.fingerprint,
    )


__all__ = (
    "BuildOperationCreateRequest", "FlashOperationCreateRequest", "HilOperationCreateRequest",
    "OperationApprovalRequest", "OperationApprovalResponse", "OperationCancelRequest",
    "OperationStatusResponse", "TestOperationCreateRequest", "UartOperationCreateRequest",
    "approval_response", "status_response",
)
