from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from embedded_copilot.application.models import ApplicationQuestionRequest
from embedded_copilot.correlation import CorrelationContext
from embedded_copilot.firmware_engineering import (
    FirmwareBuildConfiguration,
    FirmwareBuildRequest,
    FirmwareSourceArtifact,
    create_firmware_build_request,
)

from .contracts import (
    BuildOperation,
    OperationRequest,
    OperationType,
    ToolchainIdentity,
    fingerprint,
    identifier,
)


class _ExecutionPort(Protocol):
    def create_operation(self, request: OperationRequest) -> object: ...
    def issue_approval(self, *, operation: OperationRequest, reviewer_id: str, nonce: str, expires_at: datetime) -> object: ...
    def enqueue(self, *, operation: OperationRequest, approval_id: str) -> object: ...


class FirmwareBuildExecutionBridge:
    """Projects approved design/source facts into the existing BUILD lifecycle."""

    def __init__(self, execution: _ExecutionPort) -> None:
        self._execution = execution

    def create_request(
        self,
        firmware_spec: object,
        implementation_spec: object,
        *,
        configuration: FirmwareBuildConfiguration,
        source_artifacts: tuple[FirmwareSourceArtifact, ...],
        approval_reference: str | None = None,
        expected_source_revision: str | None = None,
        actual_source_revision: str | None = None,
        hardware_firmware_conflicts: tuple[str, ...] = (),
    ) -> FirmwareBuildRequest:
        return create_firmware_build_request(
            firmware_spec,
            implementation_spec,
            configuration=configuration,
            source_artifacts=source_artifacts,
            approval_reference=approval_reference,
            expected_source_revision=expected_source_revision,
            actual_source_revision=actual_source_revision,
            hardware_firmware_conflicts=hardware_firmware_conflicts,
        )

    def approve_and_enqueue(
        self,
        request: FirmwareBuildRequest,
        *,
        tenant_id: str,
        requester_id: str,
        target_id: str,
        target_fingerprint: str,
        workspace_snapshot_id: str,
        workspace_fingerprint: str,
        proposal_id: str,
        proposal_fingerprint: str,
        reviewer_id: str,
        approval_nonce: str,
        expires_at: datetime,
        resolved_toolchain_version: str | None = None,
        application_request: ApplicationQuestionRequest | None = None,
    ) -> OperationRequest:
        operation_request = self.create_operation(
            request,
            tenant_id=tenant_id,
            requester_id=requester_id,
            target_id=target_id,
            target_fingerprint=target_fingerprint,
            workspace_snapshot_id=workspace_snapshot_id,
            workspace_fingerprint=workspace_fingerprint,
            proposal_id=proposal_id,
            proposal_fingerprint=proposal_fingerprint,
            approval_nonce=approval_nonce,
            expires_at=expires_at,
            resolved_toolchain_version=resolved_toolchain_version,
            application_request=application_request,
        )
        grant = self._execution.issue_approval(
            operation=operation_request,
            reviewer_id=reviewer_id,
            nonce=approval_nonce,
            expires_at=expires_at,
        )
        grant_id = getattr(grant, "grant_id", None)
        if not isinstance(grant_id, str):
            raise TypeError("build approval grant is invalid")
        self._execution.enqueue(operation=operation_request, approval_id=grant_id)
        return operation_request

    def create_operation(
        self,
        request: FirmwareBuildRequest,
        *,
        tenant_id: str,
        requester_id: str,
        target_id: str,
        target_fingerprint: str,
        workspace_snapshot_id: str,
        workspace_fingerprint: str,
        proposal_id: str,
        proposal_fingerprint: str,
        approval_nonce: str,
        expires_at: datetime,
        resolved_toolchain_version: str | None = None,
        application_request: ApplicationQuestionRequest | None = None,
    ) -> OperationRequest:
        checked = FirmwareBuildRequest.model_validate(request.model_dump(mode="python"))
        checked_application = None
        if application_request is not None:
            checked_application = ApplicationQuestionRequest.model_validate(
                application_request.model_dump(mode="python")
            )
            if checked_application.project_id != checked.project_id:
                raise ValueError("application request project does not match firmware build")
        now = datetime.now(UTC)
        configuration = checked.configuration
        if configuration is None:
            raise ValueError("firmware build configuration is required")
        toolchain_version = configuration.toolchain_version
        if resolved_toolchain_version is not None:
            resolved_toolchain_version = identifier(
                resolved_toolchain_version,
                field="resolved_toolchain_version",
            )
            if toolchain_version not in {"UNAVAILABLE", resolved_toolchain_version}:
                raise ValueError("resolved toolchain version conflicts with build configuration")
            toolchain_version = resolved_toolchain_version
        operation_id = f"firmware-build-{uuid4().hex}"
        correlation = (
            None
            if checked_application is None
            else CorrelationContext(
                request_id=checked_application.request_id,
                trace_id=checked_application.trace_id,
                project_id=checked_application.project_id,
                operation_id=operation_id,
            )
        )
        operation = BuildOperation(
            operation_id=operation_id,
            tenant_id=identifier(tenant_id, field="tenant_id"),
            project_id=checked.project_id,
            requester_id=identifier(requester_id, field="requester_id"),
            target_id=identifier(target_id, field="target_id"),
            target_fingerprint=fingerprint(target_fingerprint, field="target_fingerprint"),
            workspace_snapshot_id=identifier(workspace_snapshot_id, field="workspace_snapshot_id"),
            workspace_fingerprint=fingerprint(workspace_fingerprint, field="workspace_fingerprint"),
            proposal_id=identifier(proposal_id, field="proposal_id"),
            proposal_fingerprint=fingerprint(proposal_fingerprint, field="proposal_fingerprint"),
            created_at=now,
            expires_at=expires_at,
            nonce=identifier(approval_nonce, field="approval_nonce"),
            toolchain=ToolchainIdentity(
                toolchain_id=configuration.toolchain_id,
                tool_version=toolchain_version,
                tool_family=(
                    "ARM_NONE_EABI_GCC"
                    if configuration.toolchain_id == "ARM_NONE_EABI_GCC"
                    else configuration.build_system
                ),
                supported_operation=OperationType.BUILD,
                supported_target=configuration.target_kind,
            ),
            configuration=configuration.profile,
            expected_artifact_type=configuration.expected_artifact_types[0],
            firmware_build_request_id=checked.firmware_reference,
            firmware_build_request_fingerprint=checked.fingerprint,
            firmware_spec_id=checked.firmware_spec_id,
            firmware_spec_fingerprint=checked.firmware_spec_fingerprint,
            implementation_id=checked.implementation_id,
            implementation_fingerprint=checked.implementation_fingerprint,
            source_artifacts=checked.source_artifacts,
            build_system=checked.build_system,
            build_directory=configuration.build_directory,
            firmware_build_request=checked,
            correlation=correlation,
        )
        operation_request = OperationRequest(operation=operation)
        self._execution.create_operation(operation_request)
        return operation_request


__all__ = ["FirmwareBuildExecutionBridge"]
