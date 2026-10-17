from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from embedded_copilot.execution_worker import (
    ArtifactStore,
    ExecutionOperationService,
    ExecutionWorker,
)
from embedded_copilot.execution_worker.contracts import (
    ArtifactIdentity,
    OperationStatus,
    OperationStatusView,
)

from .board import read_board_facts
from .contracts import (
    FabricationMode,
    FabricationRecord,
    FabricationSourceRevision,
    ManufacturingCheck,
    ManufacturingCheckStatus,
    ManufacturingExportVerification,
    ManufacturingParameterApprovalState,
    ManufacturingParameterDecision,
    ManufacturingSpecification,
    UpstreamGate,
)
from .execution import ManufacturingExecutionBridge
from .entry_gate import ManufacturingEntryGatePort
from .service import (
    FabricationReadinessService,
    ManufacturingPackageService,
    audit_manufacturing_parameters,
    close_manufacturing_parameters,
    output_files_from_artifacts,
    output_records,
    verify_manufacturing_exports,
)


@dataclass(frozen=True, slots=True)
class ManufacturingPipelineResult:
    record: FabricationRecord
    operation_ids: tuple[str, ...]


class ManufacturingPipeline:
    """Connect real KiCad exports, worker artifacts and readiness evaluation."""

    def __init__(
        self,
        *,
        operation_service: ExecutionOperationService,
        worker: ExecutionWorker,
        readiness: FabricationReadinessService,
        artifact_store: ArtifactStore | None = None,
        package_service: ManufacturingPackageService | None = None,
        entry_gate: ManufacturingEntryGatePort,
    ) -> None:
        self._operation_service = operation_service
        self._worker = worker
        self._readiness = readiness
        self._artifact_store = artifact_store
        self._package_service = package_service or ManufacturingPackageService()
        self._entry_gate = entry_gate

    def _submit_approved(
        self,
        request,
        *,
        reviewer_id: str,
        approval_nonce: str,
        expires_at: datetime,
    ) -> OperationStatusView:
        existing = self._operation_service.get_status(
            tenant_id=request.operation.tenant_id,
            project_id=request.operation.project_id,
            operation_id=request.operation.operation_id,
        )
        if existing is not None:
            if existing.status is OperationStatus.SUCCEEDED:
                persisted = self._operation_service.get_operation(
                    tenant_id=request.operation.tenant_id,
                    project_id=request.operation.project_id,
                    operation_id=request.operation.operation_id,
                )
                if persisted != request.operation:
                    raise RuntimeError("manufacturing operation identity or fingerprint mismatch")
                return existing
            raise RuntimeError(
                f"manufacturing operation {request.operation.operation_id} already exists in {existing.status.value}"
            )
        ManufacturingExecutionBridge(
            operations=self._operation_service,
            worker=self._worker,
        ).submit_approved(
            request,
            reviewer_id=reviewer_id,
            approval_nonce=approval_nonce,
            expires_at=expires_at,
            run_worker=False,
        )
        deadline = time.monotonic() + 240
        terminal = {
            OperationStatus.SUCCEEDED,
            OperationStatus.FAILED,
            OperationStatus.TIMED_OUT,
            OperationStatus.CANCELLED,
            OperationStatus.RECOVERY_REQUIRED,
        }
        while time.monotonic() < deadline:
            status = self._operation_service.get_status(
                tenant_id=request.operation.tenant_id,
                project_id=request.operation.project_id,
                operation_id=request.operation.operation_id,
            )
            if status is None:
                raise RuntimeError("manufacturing operation status is unavailable")
            if status.status in terminal:
                return status
            self._worker.run_once()
            time.sleep(0.05)
        raise RuntimeError("manufacturing operation completion timed out")

    def run(
        self,
        *,
        source: FabricationSourceRevision,
        specification: ManufacturingSpecification,
        project_root: Path,
        pcb_path: Path,
        bom_path: Path,
        schematic_path: Path,
        elf_path: Path,
        tenant_id: str,
        requester_id: str,
        target_id: str,
        target_fingerprint: str,
        workspace_snapshot_id: str,
        workspace_fingerprint: str,
        proposal_id: str,
        proposal_fingerprint: str,
        conversation_id: str,
        output_root_relative: str,
        parameter_decisions: Sequence[ManufacturingParameterDecision] = (),
        requirement_specification: object | None = None,
        engineering_decision: object | None = None,
        design_truth: object | None = None,
        pcb_artifact: object | None = None,
        bom_artifact: object | None = None,
        existing_evidence: Sequence[object] = (),
        kicad_version: str = "10.0.5",
        upstream_checks: Sequence[ManufacturingCheck] = (),
        upstream_gates: Sequence[UpstreamGate] = (),
        now: datetime | None = None,
    ) -> ManufacturingPipelineResult:
        timestamp = (now or datetime.now(UTC)).astimezone(UTC)
        root = Path(project_root).resolve(strict=False)
        output_scope = "".join(
            character if character.isalnum() or character in "._-" else "-"
            for character in output_root_relative.replace("\\", "/")
        ).strip("-")
        operation_prefix = f"fabrication-{source.project_revision}-{output_scope}"
        persisted_export = self._operation_service.get_operation(
            tenant_id=tenant_id,
            project_id=source.project_id,
            operation_id=f"fabrication-{source.project_revision}-gerber",
        )
        if persisted_export is not None:
            persisted_created_at = getattr(persisted_export, "created_at", None)
            if isinstance(persisted_created_at, datetime):
                timestamp = persisted_created_at.astimezone(UTC)
        board = read_board_facts(pcb_path)
        parameter_audit, normalized_decisions = audit_manufacturing_parameters(
            specification=specification,
            requirement_specification=requirement_specification,
            engineering_decision=engineering_decision,
            design_truth=design_truth,
            pcb_artifact=pcb_artifact,
            bom_artifact=bom_artifact,
            existing_evidence=existing_evidence,
            parameter_decisions=parameter_decisions,
            project_id=source.project_id,
            now=timestamp,
        )
        if specification.status.value == "INCOMPLETE" and any(
            item.status is ManufacturingParameterApprovalState.APPROVED for item in parameter_audit
        ):
            try:
                specification = close_manufacturing_parameters(specification, normalized_decisions)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("BLOCKED_MANUFACTURING_PARAMETER_REQUIRED") from exc
            parameter_audit, normalized_decisions = audit_manufacturing_parameters(
                specification=specification,
                requirement_specification=requirement_specification,
                engineering_decision=engineering_decision,
                design_truth=design_truth,
                pcb_artifact=pcb_artifact,
                bom_artifact=bom_artifact,
                existing_evidence=existing_evidence,
                parameter_decisions=normalized_decisions,
                project_id=source.project_id,
                now=timestamp,
            )
        if any(item.status is not ManufacturingParameterApprovalState.APPROVED for item in parameter_audit):
            raise RuntimeError("BLOCKED_MANUFACTURING_PARAMETER_REQUIRED")
        entry_gate_receipt = self._entry_gate.assert_current(
            tenant_id=tenant_id,
            project_id=source.project_id,
            conversation_id=conversation_id,
            requester=requester_id,
            pcb_artifact_fingerprint=source.pcb_sha256,
        )
        pcb_relative = pcb_path.resolve(strict=False).relative_to(root).as_posix()
        operation_ids: list[str] = []
        outputs = []
        gerber_files = []
        drill_files = []
        export_verifications: list[ManufacturingExportVerification] = []
        export_kinds = ["GERBER", "DRILL"]
        if specification.fabrication_mode is FabricationMode.PCB_ASSEMBLY:
            export_kinds.append("POSITION")
        for kind in export_kinds:
            operation_id = f"{operation_prefix}-{kind.casefold()}"
            request = ManufacturingExecutionBridge.build_operation(
                source=source,
                specification=specification,
                tenant_id=tenant_id,
                requester_id=requester_id,
                target_id=target_id,
                target_fingerprint=target_fingerprint,
                workspace_snapshot_id=workspace_snapshot_id,
                workspace_fingerprint=workspace_fingerprint,
                proposal_id=proposal_id,
                proposal_fingerprint=proposal_fingerprint,
                conversation_id=conversation_id,
                export_kind=kind,
                source_pcb_relative_path=pcb_relative,
                output_workspace_relative=f"{output_root_relative}/{kind.casefold()}",
                layer_list=board.expected_gerber_layers if kind == "GERBER" else (),
                operation_id=operation_id,
                nonce=f"{operation_id}-nonce",
                created_at=timestamp,
                expires_at=timestamp + timedelta(minutes=15),
                entry_gate_receipt=entry_gate_receipt,
            )
            status = self._submit_approved(
                request,
                reviewer_id=requester_id,
                approval_nonce=f"{operation_id}-approval",
                expires_at=timestamp + timedelta(minutes=15),
            )
            if status.status is not OperationStatus.SUCCEEDED:
                raise RuntimeError(f"manufacturing export {kind} did not complete")
            receipt = self._operation_service.get_receipt(tenant_id=tenant_id, project_id=source.project_id, operation_id=operation_id)
            if receipt is None:
                raise RuntimeError("manufacturing receipt is unavailable")
            artifacts: list[ArtifactIdentity] = []
            for artifact_id in receipt.evidence_references:
                artifact = self._operation_service.get_artifact(tenant_id=tenant_id, project_id=source.project_id, artifact_id=artifact_id)
                if artifact is not None:
                    if self._artifact_store is not None:
                        self._artifact_store.read(artifact)
                    artifacts.append(artifact)
            output_root = (root / f"{output_root_relative}/{kind.casefold()}").resolve(strict=False)
            output_files = output_files_from_artifacts(artifacts, output_root=output_root)
            output_projection = output_records(
                output_files,
                output_root=output_root,
                source=source,
                relative_prefix=kind.casefold(),
            )
            outputs.extend(output_projection)
            if kind == "GERBER":
                gerber_files.extend(output_files)
            elif kind == "DRILL":
                drill_files.extend(output_files)
            operation_ids.append(operation_id)
        export_verifications.extend(
            verify_manufacturing_exports(
                board=board,
                source=source,
                gerber_files=tuple(gerber_files),
                drill_files=tuple(drill_files),
            )
        )
        manifest = None
        package = None
        if all(item.status is ManufacturingCheckStatus.PASS for item in export_verifications):
            lineage_notes = [
                f"source_revision_fingerprint={source.fingerprint}",
                "kicad_tool_id=KICAD_GERBER_EXPORT,KICAD_DRILL_EXPORT",
                f"kicad_tool_version={kicad_version}",
                (
                    "generation_configuration="
                    f"gerber_layers:{','.join(board.expected_gerber_layers)};"
                    "drill_format:excellon;drill_origin:absolute;"
                    "excellon_units:mm;separate_through_hole:true"
                ),
            ]
            for label, value in (
                ("pcb_state_fingerprint", getattr(entry_gate_receipt, "pcb_state_fingerprint", None)),
                ("g7_summary_fingerprint", getattr(entry_gate_receipt, "summary_fingerprint", None)),
                ("ready_for_fab_fingerprint", getattr(entry_gate_receipt, "ready_for_fab_fingerprint", None)),
                (
                    "ready_for_fab_target_fingerprint",
                    getattr(entry_gate_receipt, "ready_for_fab_target_fingerprint", None),
                ),
                ("approval_fingerprint", getattr(entry_gate_receipt, "approval_fingerprint", None)),
                (
                    "manufacturer_profile_fingerprint",
                    getattr(entry_gate_receipt, "manufacturer_profile_fingerprint", None),
                ),
                ("dfm_fingerprint", getattr(entry_gate_receipt, "dfm_fingerprint", None)),
            ):
                if value is not None:
                    lineage_notes.append(f"{label}={value}")
            manifest, package = self._package_service.build(
                source=source,
                specification=specification,
                outputs=tuple(outputs),
                kicad_version=kicad_version,
                root_relative_path=output_root_relative,
                lineage_notes=tuple(lineage_notes),
                now=timestamp,
            )
            package_request = ManufacturingExecutionBridge.build_operation(
                source=source,
                specification=specification,
                tenant_id=tenant_id,
                requester_id=requester_id,
                target_id=target_id,
                target_fingerprint=target_fingerprint,
                workspace_snapshot_id=workspace_snapshot_id,
                workspace_fingerprint=workspace_fingerprint,
                proposal_id=proposal_id,
                proposal_fingerprint=proposal_fingerprint,
                conversation_id=conversation_id,
                export_kind="PACKAGE",
                source_pcb_relative_path=pcb_relative,
                # Reserve only a child of the current G8 output root.  The
                # previous source-fingerprint path could collide with a
                # historical package from an earlier run and would allow the
                # package adapter to clear that directory before materialize.
                output_workspace_relative=f"{output_root_relative}/package",
                operation_id=f"{operation_prefix}-package",
                nonce=f"fabrication-{source.project_revision}-package-nonce",
                created_at=timestamp,
                expires_at=timestamp + timedelta(minutes=15),
                entry_gate_receipt=entry_gate_receipt,
            )
            package_status = self._submit_approved(
                package_request,
                reviewer_id=requester_id,
                approval_nonce=f"fabrication-{source.project_revision}-package-approval",
                expires_at=timestamp + timedelta(minutes=15),
            )
            if package_status.status is not OperationStatus.SUCCEEDED:
                raise RuntimeError("manufacturing package operation did not complete")
            operation_ids.append(package_request.operation.operation_id)
            self._package_service.materialize(
                root=root / output_root_relative,
                pcb_path=pcb_path,
                source=source,
                specification=specification,
                manifest=manifest,
                evidence_refs=tuple(operation_ids),
                now=timestamp,
            )
        record = self._readiness.evaluate(
            source=source,
            pcb_path=pcb_path,
            bom_path=bom_path,
            schematic_path=schematic_path,
            elf_path=elf_path,
            explicit_mode=specification.fabrication_mode,
            mode_approval_ref=proposal_id,
            mode_approved_by=requester_id,
            mode_evidence_refs=(proposal_id, proposal_fingerprint),
            specification=specification,
            parameter_decisions=normalized_decisions,
            requirement_specification=requirement_specification,
            engineering_decision=engineering_decision,
            design_truth=design_truth,
            pcb_artifact=pcb_artifact,
            bom_artifact=bom_artifact,
            existing_evidence=existing_evidence,
            upstream_checks=tuple(upstream_checks),
            upstream_gates=tuple(upstream_gates),
            outputs=tuple(outputs),
            manifest=manifest,
            package=package,
            kicad_version=kicad_version,
            package_root_relative=output_root_relative,
            export_verifications=tuple(export_verifications),
            now=timestamp,
        )
        return ManufacturingPipelineResult(record=record, operation_ids=tuple(operation_ids))


__all__ = ("ManufacturingPipeline", "ManufacturingPipelineResult")
