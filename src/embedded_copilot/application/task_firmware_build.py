"""Explicit opt-in Firmware Build coordination over the existing execution service.

The trusted coordination source is injected by an authorized caller. This module
does not provide that authority, issue approvals, run workers, or invoke tools.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Literal

from embedded_copilot.correlation import CorrelationContext
from embedded_copilot.engineering_copilot.common import validate_copy
from embedded_copilot.engineering_copilot.contracts import (
    EngineeringPlan,
    PlanReviewDecision,
    PlanReviewStatus,
    PlanStatus,
)
from embedded_copilot.engineering_task_runtime.build_state import (
    BuildOperationReference,
    BuildReceiptReference,
    FirmwareBuildCoordinationState,
)
from embedded_copilot.engineering_task_runtime.contracts import (
    EngineeringTaskCoordination,
    validate_coordination,
)
from embedded_copilot.engineering_task_runtime.exceptions import EngineeringTaskRejected
from embedded_copilot.engineering_task_runtime.ports import (
    EngineeringTaskCoordinationSourcePort,
)
from embedded_copilot.execution_worker.contracts import (
    ArtifactIdentity,
    BuildOperation,
    OperationReceipt,
    OperationRequest,
    OperationStatus,
    OperationStatusView,
    OperationType,
    ToolchainIdentity,
)
from embedded_copilot.execution_worker.service import (
    ExecutionOperationService,
    ExecutionRejected,
)
from embedded_copilot.firmware_engineering import FirmwareBuildRequest


def _digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _semantic(operation: BuildOperation) -> dict[str, object]:
    """Exclude only retry-local timestamps/nonce and the derived fingerprint."""
    return operation.model_dump(
        mode="json", exclude={"created_at", "expires_at", "nonce", "fingerprint"}
    )


def _intent(
    coordination: EngineeringTaskCoordination,
    request: FirmwareBuildRequest,
    *,
    requester_id: str,
    target_id: str,
    target_fingerprint: str,
    workspace_snapshot_id: str,
    workspace_fingerprint: str,
    proposal_id: str,
    proposal_fingerprint: str,
    toolchain_version: str,
) -> str:
    # Metadata reference is observational, not part of the build intent.
    return "task-intent-" + _digest({
        "task": coordination.task_identity.model_dump(mode="json"),
        "request_reference": coordination.request_reference.model_dump(mode="json"),
        "plan": coordination.plan_reference.model_dump(mode="json"),
        "stage": coordination.current_stage_reference.model_dump(mode="json"),
        "build_request_fingerprint": request.fingerprint,
        "requester_id": requester_id,
        "target_id": target_id,
        "target_fingerprint": target_fingerprint,
        "workspace_snapshot_id": workspace_snapshot_id,
        "workspace_fingerprint": workspace_fingerprint,
        "proposal_id": proposal_id,
        "proposal_fingerprint": proposal_fingerprint,
        "toolchain_version": toolchain_version,
    })[:48]


class FirmwareBuildTaskService:
    """Build-only opt-in entry; projections are rebuilt from persisted operations.

    ``submit`` creates WAIT_APPROVAL. An external reviewer uses the existing
    ExecutionOperationService approval/enqueue methods, then an externally driven
    ExecutionWorker runs the adapter. ``refresh`` reads the authoritative result.
    BUILD_PASS is a typed build observation, never firmware/hardware VERIFIED.
    Non-CMAKE failures lacking BuildEvidence remain UNVERIFIED.
    """

    def __init__(self, execution: ExecutionOperationService) -> None:
        self._execution = execution

    def submit(
        self,
        coordination: EngineeringTaskCoordination,
        source: EngineeringTaskCoordinationSourcePort,
        build_request: FirmwareBuildRequest,
        *,
        requester_id: str,
        target_id: str,
        target_fingerprint: str,
        workspace_snapshot_id: str,
        workspace_fingerprint: str,
        proposal_id: str,
        proposal_fingerprint: str,
        expires_at: datetime,
        approval_nonce: str = "task-build-submission",
        resolved_toolchain_version: str | None = None,
    ) -> FirmwareBuildCoordinationState:
        checked = validate_coordination(coordination, source)
        request = self._validate_request(build_request)
        task = checked.task_identity
        self._require_build_phase(checked, source)
        if requester_id != source.bound_principal_id:
            raise EngineeringTaskRejected("build requester binding rejected")
        if request.project_id != task.project_id or request.configuration is None:
            raise EngineeringTaskRejected("firmware build request binding rejected")
        configuration = request.configuration
        version = configuration.toolchain_version
        if resolved_toolchain_version is not None:
            if version not in {"UNAVAILABLE", resolved_toolchain_version}:
                raise EngineeringTaskRejected("toolchain version conflict")
            version = resolved_toolchain_version
        operation_id = "task-build-" + _digest(
            (task.tenant_id, task.project_id, task.task_id)
        )[:40]
        # The intent digest binds the approved plan/stage and every build binding.
        # It is persisted in existing correlation fields, without a task store.
        correlation = CorrelationContext(
            request_id=_intent(
                checked, request, requester_id=requester_id, target_id=target_id,
                target_fingerprint=target_fingerprint,
                workspace_snapshot_id=workspace_snapshot_id,
                workspace_fingerprint=workspace_fingerprint,
                proposal_id=proposal_id, proposal_fingerprint=proposal_fingerprint,
                toolchain_version=version,
            ),
            trace_id="task-plan-" + _digest(checked.plan_reference.model_dump(mode="json"))[:48],
            task_id=task.task_id,
            project_id=task.project_id,
            operation_id=operation_id,
        )
        now = datetime.now(UTC)
        candidate = BuildOperation(
            operation_id=operation_id, tenant_id=task.tenant_id, project_id=task.project_id,
            requester_id=requester_id, target_id=target_id,
            target_fingerprint=target_fingerprint,
            workspace_snapshot_id=workspace_snapshot_id,
            workspace_fingerprint=workspace_fingerprint,
            proposal_id=proposal_id, proposal_fingerprint=proposal_fingerprint,
            created_at=now, expires_at=expires_at, nonce=approval_nonce,
            toolchain=ToolchainIdentity(
                toolchain_id=configuration.toolchain_id, tool_version=version,
                tool_family=("ARM_NONE_EABI_GCC" if configuration.toolchain_id == "ARM_NONE_EABI_GCC" else configuration.build_system),
                supported_operation=OperationType.BUILD,
                supported_target=configuration.target_kind,
            ),
            configuration=configuration.profile,
            expected_artifact_type=configuration.expected_artifact_types[0],
            firmware_build_request_id=request.firmware_reference,
            firmware_build_request_fingerprint=request.fingerprint,
            firmware_spec_id=request.firmware_spec_id,
            firmware_spec_fingerprint=request.firmware_spec_fingerprint,
            implementation_id=request.implementation_id,
            implementation_fingerprint=request.implementation_fingerprint,
            source_artifacts=request.source_artifacts,
            build_system=request.build_system,
            build_directory=configuration.build_directory,
            firmware_build_request=request,
            correlation=correlation,
        )
        existing = self._execution.get_operation(
            tenant_id=task.tenant_id, project_id=task.project_id, operation_id=operation_id
        )
        if existing is None:
            try:
                self._execution.create_operation(OperationRequest(operation=candidate))
            except (sqlite3.IntegrityError, ExecutionRejected):
                # Concurrent insert or service conflict: reread the authoritative row.
                existing = self._execution.get_operation(
                    tenant_id=task.tenant_id, project_id=task.project_id, operation_id=operation_id
                )
                if existing is None:
                    raise EngineeringTaskRejected("build operation creation rejected") from None
        try:
            persisted = validate_copy(existing, BuildOperation) if existing is not None else None
        except (TypeError, ValueError):
            raise EngineeringTaskRejected("build operation intent conflict") from None
        if persisted is not None and _semantic(persisted) != _semantic(candidate):
            raise EngineeringTaskRejected("build operation intent conflict")
        return self.refresh(checked, source, request, operation_id=operation_id)

    def refresh(
        self,
        coordination: EngineeringTaskCoordination,
        source: EngineeringTaskCoordinationSourcePort,
        build_request: FirmwareBuildRequest,
        *,
        operation_id: str,
    ) -> FirmwareBuildCoordinationState:
        checked = validate_coordination(coordination, source)
        request = self._validate_request(build_request)
        task = checked.task_identity
        self._require_build_phase(checked, source)
        expected_id = "task-build-" + _digest((task.tenant_id, task.project_id, task.task_id))[:40]
        if operation_id != expected_id or request.project_id != task.project_id:
            raise EngineeringTaskRejected("build operation identity rejected")
        operation = self._execution.get_operation(
            tenant_id=task.tenant_id, project_id=task.project_id, operation_id=operation_id
        )
        if not isinstance(operation, BuildOperation):
            raise EngineeringTaskRejected("build operation unavailable")
        try:
            operation = validate_copy(operation, BuildOperation)
        except (TypeError, ValueError):
            raise EngineeringTaskRejected("build operation binding rejected") from None
        if operation.correlation is None:
            raise EngineeringTaskRejected("build operation binding rejected")
        if (
            operation.operation_id != operation_id
            or operation.tenant_id != task.tenant_id
            or operation.project_id != task.project_id
            or operation.correlation.project_id != task.project_id
            or operation.correlation.operation_id != operation_id
            or operation.correlation.task_id != task.task_id
            or operation.correlation.trace_id != "task-plan-" + _digest(checked.plan_reference.model_dump(mode="json"))[:48]
            or operation.correlation.request_id != _intent(
                checked, request, requester_id=operation.requester_id,
                target_id=operation.target_id, target_fingerprint=operation.target_fingerprint,
                workspace_snapshot_id=operation.workspace_snapshot_id,
                workspace_fingerprint=operation.workspace_fingerprint,
                proposal_id=operation.proposal_id,
                proposal_fingerprint=operation.proposal_fingerprint,
                toolchain_version=operation.toolchain.tool_version,
            )
            or operation.requester_id != source.bound_principal_id
            or operation.firmware_build_request_fingerprint != request.fingerprint
            or operation.firmware_build_request != request
        ):
            raise EngineeringTaskRejected("build operation binding rejected")
        status = self._execution.get_status(
            tenant_id=task.tenant_id, project_id=task.project_id, operation_id=operation_id
        )
        if status is None:
            raise EngineeringTaskRejected("build operation status unavailable")
        try:
            status = validate_copy(status, OperationStatusView)
        except (AttributeError, TypeError, ValueError):
            raise EngineeringTaskRejected("build operation status rejected") from None
        if (
            status.operation_type is not OperationType.BUILD
            or status.tenant_id != task.tenant_id
            or status.project_id != task.project_id
            or status.operation_id != operation_id
            or status.correlation is None
            or status.correlation.task_id != task.task_id
            or status.correlation.project_id != task.project_id
            or status.correlation.operation_id != operation_id
            or status.correlation.request_id != operation.correlation.request_id
            or status.correlation.trace_id != operation.correlation.trace_id
        ):
            raise EngineeringTaskRejected("build operation status rejected")
        state = "BLOCKED" if status.status is OperationStatus.WAIT_APPROVAL else "UNVERIFIED"
        receipt_ref = None
        receipt = self._execution.get_receipt(
            tenant_id=task.tenant_id, project_id=task.project_id, operation_id=operation_id
        )
        if receipt is not None:
            state, receipt_ref = self._project_receipt(operation, status, receipt)
        return FirmwareBuildCoordinationState(
            task_id=task.task_id, tenant_id=task.tenant_id, project_id=task.project_id,
            plan_reference=checked.plan_reference,
            stage_reference=checked.current_stage_reference,
            operation_reference=BuildOperationReference(
                operation_id=operation_id, operation_fingerprint=operation.fingerprint,
                build_request_fingerprint=request.fingerprint,
            ),
            coordination_status=state, receipt_reference=receipt_ref,
        )

    @staticmethod
    def _validate_request(request: FirmwareBuildRequest) -> FirmwareBuildRequest:
        try:
            return validate_copy(request, FirmwareBuildRequest)
        except (TypeError, ValueError):
            raise EngineeringTaskRejected("firmware build request rejected") from None

    @staticmethod
    def _require_build_phase(
        coordination: EngineeringTaskCoordination,
        source: EngineeringTaskCoordinationSourcePort,
    ) -> None:
        try:
            resolved = source.resolve_plan(
                coordination.task_identity.task_id, coordination.plan_reference.plan_id
            )
            if type(resolved) is not tuple or len(resolved) != 2:
                raise ValueError("plan unavailable")
            plan = validate_copy(resolved[0], EngineeringPlan)
            review = validate_copy(resolved[1], PlanReviewDecision)
            if (
                plan.plan_id != coordination.plan_reference.plan_id
                or plan.project_id != coordination.task_identity.project_id
                or plan.content_fingerprint != coordination.plan_reference.content_fingerprint
                or plan.status is not PlanStatus.APPROVED
                or review.plan_id != plan.plan_id
                or review.project_id != plan.project_id
                or review.proposal_fingerprint != plan.content_fingerprint
                or review.fingerprint != coordination.plan_reference.review_fingerprint
                or review.status is not PlanReviewStatus.APPROVED
            ):
                raise ValueError("plan binding changed")
            phase = next(
                (item for item in plan.phases if item.phase_id == coordination.current_stage_reference.stage_id),
                None,
            )
            if (
                phase is None
                or phase.fingerprint != coordination.current_stage_reference.source_reference.phase_fingerprint
                or not any(item.output_type == "BUILD_ARTIFACT" for item in phase.outputs)
            ):
                raise ValueError("non-build phase")
        except Exception:  # noqa: BLE001 - trusted source may fail with provider-specific errors
            raise EngineeringTaskRejected("firmware build stage rejected") from None

    def _project_receipt(
        self,
        operation: BuildOperation,
        status: OperationStatusView,
        receipt: OperationReceipt,
    ) -> tuple[Literal["BUILD_PASS", "BUILD_FAILED", "UNVERIFIED", "BLOCKED"], BuildReceiptReference | None]:
        try:
            receipt = validate_copy(receipt, OperationReceipt)
        except (TypeError, ValueError):
            return "BLOCKED", None
        evidence = receipt.build_evidence
        artifact_ids = () if evidence is None else evidence.output_artifact_ids
        if (
            receipt.operation_id != operation.operation_id
            or receipt.tenant_id != operation.tenant_id
            or receipt.project_id != operation.project_id
            or receipt.operation_type is not OperationType.BUILD
            or receipt.status is not status.status
            or status.receipt_fingerprint != receipt.fingerprint
            or receipt.correlation is None
            or receipt.correlation.task_id != operation.correlation.task_id
            or receipt.correlation.project_id != operation.project_id
            or receipt.correlation.operation_id != operation.operation_id
            or receipt.correlation.request_id != operation.correlation.request_id
            or receipt.correlation.trace_id != operation.correlation.trace_id
        ):
            return "BLOCKED", None
        result = receipt.build_result
        if evidence is None or result is None:
            # The receipt itself is bound above; partial evidence and artifact
            # references have not been validated and must not leave this boundary.
            return "UNVERIFIED", BuildReceiptReference(
                operation_id=receipt.operation_id,
                receipt_fingerprint=receipt.fingerprint,
            )
        expected_sources = tuple(item.content_fingerprint for item in operation.source_artifacts)
        if (
            evidence.operation_id != operation.operation_id
            or evidence.request_fingerprint != operation.firmware_build_request_fingerprint
            or result.request_fingerprint != operation.firmware_build_request_fingerprint
            or evidence.toolchain_id != operation.toolchain.toolchain_id
            or evidence.toolchain_version != operation.toolchain.tool_version
            or evidence.source_fingerprints != expected_sources
            or evidence.build_system != operation.build_system
            or (receipt.artifact is not None and receipt.artifact.artifact_id not in artifact_ids)
        ):
            return "BLOCKED", None
        reference = BuildReceiptReference(
            operation_id=receipt.operation_id,
            receipt_fingerprint=receipt.fingerprint,
            evidence_fingerprint=evidence.fingerprint,
            artifact_ids=artifact_ids,
        )
        output_by_id = {item.reference: item for item in result.outputs}
        if (
            len(output_by_id) != len(result.outputs)
            or not set(output_by_id).issubset(set(artifact_ids))
            or (result.log_artifact_reference is not None and result.log_artifact_reference != evidence.log_artifact_id)
            or result.evidence_references != receipt.evidence_references
            or (result.result_status == "BUILD_PASS" and (
                not result.outputs
                or receipt.artifact is None
                or receipt.artifact.artifact_id not in output_by_id
            ))
        ):
            return "BLOCKED", None
        for artifact_id in set(artifact_ids) | ({evidence.log_artifact_id} if evidence.log_artifact_id else set()):
            artifact = self._execution.get_artifact(
                tenant_id=operation.tenant_id, project_id=operation.project_id, artifact_id=artifact_id
            )
            if not isinstance(artifact, ArtifactIdentity):
                return "BLOCKED", None
            try:
                artifact = validate_copy(artifact, ArtifactIdentity)
            except (TypeError, ValueError):
                return "BLOCKED", None
            if (
                artifact.artifact_id != artifact_id
                or artifact.tenant_id != operation.tenant_id
                or artifact.project_id != operation.project_id
                or artifact.build_operation_id != operation.operation_id
                or artifact.target_id != operation.target_id
                or artifact.workspace_fingerprint != operation.workspace_fingerprint
                or artifact.toolchain_id != operation.toolchain.toolchain_id
                or artifact.toolchain_version != operation.toolchain.tool_version
                or artifact.build_configuration != operation.configuration
            ):
                return "BLOCKED", None
            if receipt.artifact is not None and artifact_id == receipt.artifact.artifact_id and artifact != receipt.artifact:
                return "BLOCKED", None
            output = output_by_id.get(artifact_id)
            if output is not None and (
                output.artifact_type != artifact.artifact_type
                or output.content_fingerprint != artifact.artifact_sha256
                or output.size_bytes != artifact.size_bytes
            ):
                return "BLOCKED", None
            if evidence.log_artifact_id == artifact_id and artifact.artifact_type != "LOG":
                return "BLOCKED", None
        if (
            status.status is OperationStatus.SUCCEEDED
            and receipt.tool_execution_status == "SUCCEEDED"
            and result.result_status == "BUILD_PASS"
            and evidence.result_status == "BUILD_PASS"
            and evidence.tool_observed
            and receipt.artifact is not None
        ):
            return "BUILD_PASS", reference
        if status.status is OperationStatus.FAILED and result.result_status == "BUILD_FAIL" and evidence.result_status == "BUILD_FAIL":
            return "BUILD_FAILED", reference
        return "UNVERIFIED", reference


__all__ = ("FirmwareBuildTaskService",)
