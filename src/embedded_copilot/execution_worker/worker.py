from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from embedded_copilot.engineering_copilot import SchematicErcResult
from embedded_copilot.engineering_workspace_apply import WorkspaceApplyService
from embedded_copilot.engineering_workspace_apply.exceptions import WorkspaceApplyError
from embedded_copilot.pcb_production import PCBDrcResult, PCBDrcStatus

from .adapters import (
    BuildToolAdapter,
    FlashToolAdapter,
    HilScenarioAdapter,
    UartObservationAdapter,
    UnavailableTestAdapter,
)
from .artifacts import ArtifactStore
from .contracts import (
    ApprovalStatus,
    ArtifactIdentity,
    parse_library_observation_payload,
    BuildOperation,
    FirmwareSourceWriteOperation,
    FlashOperation,
    HardwareBringupOperation,
    HilOperation,
    KiCadManufacturingOperation,
    KiCadNativeFootprintMoveOperation,
    NativeMoveOfflineResult,
    NativeMoveProductionResultV1,
    OperationReceipt,
    OperationStatus,
    OperationStatusView,
    OperationType,
    PcbDrcBaselineOperation,
    PcbDrcOperation,
    PCBWriteOperation,
    RecoveryState,
    SchematicErcOperation,
    SchematicParseOperation,
    SchematicReadBackOperation,
    SchematicXmlResult,
    SchematicWriteOperation,
    SimulationOperation,
    TestOperation,
    UartObservationOperation,
    WorkspaceMutationResult,
    approval_purpose_for_operation,
)
from .native_move import NativeMoveProductionAdapter
from .ports import (
    AdapterResult,
    ExecutionContext,
    ManufacturingAdapterPort,
    SimulationAdapterPort,
    TargetRegistryPort,
    TestAdapterPort,
    WorkspaceResolverPort,
)
from .registry import ExecutionToolRegistry
from .service import build_proposal_binding_matches
from .store import SQLiteExecutionStore


class _Cancellation:
    def __init__(self, store: SQLiteExecutionStore, tenant_id: str, project_id: str, operation_id: str) -> None:
        self._store = store
        self._tenant_id = tenant_id
        self._project_id = project_id
        self._operation_id = operation_id

    def is_cancelled(self) -> bool:
        status = self._store.get_status(self._tenant_id, self._project_id, self._operation_id)
        return bool(status and status.cancellation_requested)


class ExecutionWorker:
    """Single local worker boundary. It claims persisted operations and calls typed adapters."""

    def __init__(self, *, worker_id: str, store: SQLiteExecutionStore, registry: ExecutionToolRegistry | None, workspace_resolver: WorkspaceResolverPort | None, targets: TargetRegistryPort | None, artifact_store: ArtifactStore | None = None, build=None, test: TestAdapterPort | None = None, simulation: SimulationAdapterPort | None = None, flash=None, uart=None, hil=None, hardware_bringup=None, firmware_source_write=None, schematic_write=None, schematic_erc=None, schematic_parse=None, schematic_readback=None, pcb_write=None, pcb_drc=None, manufacturing: ManufacturingAdapterPort | None = None, native_move_adapter: object | None = None, drc_baseline_collector: object | None = None, drc_baseline_revalidator: Callable[[object], bool] | None = None, recovery_workspace: WorkspaceApplyService | None = None, native_move_revalidator: Callable[[object], bool] | None = None, allowed_operation_types: tuple[OperationType, ...] | None = None, build_context_revalidator: Callable[[BuildOperation], bool] | None = None, design_context_revalidator: Callable[[object], bool] | None = None, library_observation_authorizer: Callable[..., bool] | None = None, build_sandbox_root: Path | None = None, clock: Callable[[], datetime] | None = None) -> None:
        self.worker_id = worker_id
        self._store = store
        self._registry = registry
        self._workspace_resolver = workspace_resolver
        self._targets = targets
        self._artifact_store = artifact_store
        self._build = build or BuildToolAdapter()
        self._test = test if test is not None else UnavailableTestAdapter()
        self._test_adapter_configured = test is not None
        self._simulation = simulation if simulation is not None else (test if test is not None else UnavailableTestAdapter())
        self._simulation_adapter_configured = simulation is not None or test is not None
        self._flash = flash or FlashToolAdapter()
        self._uart = uart or UartObservationAdapter()
        self._hil = hil or HilScenarioAdapter(self._uart)
        self._hardware_bringup = hardware_bringup
        self._firmware_source_write = firmware_source_write
        self._schematic_write = schematic_write
        self._schematic_erc = schematic_erc
        self._schematic_parse = schematic_parse
        self._schematic_readback = schematic_readback
        self._pcb_write = pcb_write
        self._pcb_drc = pcb_drc
        self._manufacturing = manufacturing
        self._drc_baseline_collector = drc_baseline_collector
        self._drc_baseline_revalidator = drc_baseline_revalidator
        self._native_move_adapter = native_move_adapter
        self._recovery_workspace = recovery_workspace
        self._native_move_revalidator = native_move_revalidator
        self._allowed_operation_types = allowed_operation_types
        self._build_context_revalidator = build_context_revalidator
        self._design_context_revalidator = design_context_revalidator
        self._library_observation_authorizer = library_observation_authorizer
        self._build_sandbox_root = Path(build_sandbox_root) if build_sandbox_root is not None else store._database.parent / "build-sandboxes"
        self._clock = clock or (lambda: datetime.now(UTC))

    def _classify_native_recovery(self, operation: KiCadNativeFootprintMoveOperation) -> RecoveryState:
        context = operation.production_context
        workspace = self._recovery_workspace
        if context is None or workspace is None:
            return RecoveryState.TARGET_STATE_UNKNOWN
        try:
            receipt = workspace.get_receipt(operation.project_id, operation.operation_id)
            if receipt is None or (
                receipt.project_id, receipt.operation_id, receipt.changeset_id, receipt.changeset_fingerprint,
            ) != (
                operation.project_id, operation.operation_id, context.changeset.changeset_id,
                context.changeset.fingerprint,
            ) or receipt.recovery_id is None:
                return RecoveryState.TARGET_STATE_UNKNOWN
            recovery = workspace._store.get_recovery(operation.project_id, receipt.recovery_id)
            if recovery is None or recovery.operation_id != operation.operation_id or recovery.targets != receipt.targets:
                return RecoveryState.TARGET_STATE_UNKNOWN
            root = workspace._resolve(operation.tenant_id, operation.project_id, operation.target_id).root
            board = workspace._safe_path(root, operation.pcb_relative_path)
            actual = "sha256:" + hashlib.sha256(board.read_bytes()).hexdigest() if board.is_file() else None
            entry = context.changeset.entries[0]
            if actual not in {entry.before_fingerprint, entry.after_fingerprint}:
                return RecoveryState.TARGET_STATE_UNKNOWN
            current = workspace.snapshot(
                tenant_id=operation.tenant_id, project_id=operation.project_id,
                target_id=operation.target_id,
            )
            frozen_files = {
                item.relative_file_reference: item.content_fingerprint
                for item in context.workspace_snapshot.files
            }
            current_files = {
                item.relative_file_reference: item.content_fingerprint
                for item in current.files
            }
            if current_files.get(operation.pcb_relative_path) != actual:
                return RecoveryState.TARGET_STATE_UNKNOWN
            if {
                path: digest for path, digest in frozen_files.items()
                if path != operation.pcb_relative_path
            } != {
                path: digest for path, digest in current_files.items()
                if path != operation.pcb_relative_path
            }:
                return RecoveryState.TARGET_STATE_UNKNOWN
            return RecoveryState.OPERATOR_DECISION_REQUIRED
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return RecoveryState.TARGET_STATE_UNKNOWN

    def _classify_recovery(self, operation: object) -> RecoveryState:
        if isinstance(operation, PcbDrcBaselineOperation):
            workspace = self._recovery_workspace
            try:
                if workspace is None:
                    return RecoveryState.TARGET_STATE_UNKNOWN
                snapshot = workspace.snapshot(tenant_id=operation.tenant_id, project_id=operation.project_id, target_id=operation.target_id)
                prl = next((item for item in snapshot.files if item.relative_file_reference == operation.prl_relative_path), None)
                pcb = next((item for item in snapshot.files if item.relative_file_reference == operation.pcb_relative_path), None)
                if ((snapshot.project_fingerprint, snapshot.target_fingerprint) != (operation.workspace_fingerprint, operation.target_fingerprint)
                    or pcb is None or pcb.content_fingerprint != operation.pcb_sha256
                    or (None if prl is None else prl.content_fingerprint) != operation.prl_before_sha256):
                    return RecoveryState.TARGET_STATE_UNKNOWN
                return RecoveryState.INTERRUPTED
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                return RecoveryState.TARGET_STATE_UNKNOWN
        return self._classify_native_recovery(operation)

    def recover_after_restart(self) -> tuple[OperationStatusView, ...]:
        types = self._allowed_operation_types
        if types is not None and OperationType.KICAD_NATIVE_FOOTPRINT_MOVE not in types:
            types = (*types, OperationType.KICAD_NATIVE_FOOTPRINT_MOVE)
        if types is not None and OperationType.KICAD_PCB_DRC_BASELINE not in types:
            types = (*types, OperationType.KICAD_PCB_DRC_BASELINE)
        return self._store.recover_running(
            now=self._clock(),
            operation_types=types,
            recovery_classify=self._classify_recovery,
        )

    def run_once(self) -> OperationStatusView | None:
        claim = self._store.claim_next(
            worker_id=self.worker_id,
            lease_seconds=60,
            now=self._clock(),
            operation_types=self._allowed_operation_types,
        )
        if claim is None:
            return None
        tenant_id, project_id, operation_id = claim
        status = self._store.get_status(tenant_id, project_id, operation_id)
        if status is None or status.approval_id is None:
            return self._fail(None, status, tenant_id, project_id, operation_id, "operation approval unavailable", "APPROVAL_UNAVAILABLE")
        # The operation payload is intentionally supplied by the application
        # composition, not reconstructed from arbitrary queue fields.
        operation = getattr(self._store, "get_operation", lambda *_: None)(tenant_id, project_id, operation_id)
        if operation is None:
            return self._fail(None, status, tenant_id, project_id, operation_id, "typed operation unavailable", "TYPED_OPERATION_UNAVAILABLE")
        approval = self._store.get_approval(tenant_id, project_id, status.approval_id)
        if (
            approval is None
            or approval.operation_id != operation.operation_id
            or approval.operation_type is not operation.operation_type
            or approval.approval_purpose is not approval_purpose_for_operation(operation.operation_type)
            or approval.operation_fingerprint != operation.fingerprint
            or approval.requester_id != operation.requester_id
            or approval.proposal_id != operation.proposal_id
            or approval.proposal_fingerprint != operation.proposal_fingerprint
            or approval.target_id != operation.target_id
            or approval.target_fingerprint != operation.target_fingerprint
            or approval.workspace_snapshot_id != operation.workspace_snapshot_id
            or approval.workspace_fingerprint != operation.workspace_fingerprint
            or approval.status is not ApprovalStatus.ISSUED
        ):
            return self._fail(operation, status, tenant_id, project_id, operation_id, "approval binding mismatch", "APPROVAL_BINDING_MISMATCH")
        if isinstance(operation, SchematicErcOperation) and operation.source_parse_operation_id is None:
            return self._fail(operation, status, tenant_id, project_id, operation_id,
                              "schematic ERC canonical parse evidence unavailable", "APPROVAL_STALE")
        independent_schematic = isinstance(operation, (SchematicParseOperation, SchematicErcOperation))
        if independent_schematic:
            stopped = self._stop_schematic_before_dispatch(operation, approval)
            if stopped is not None:
                return stopped
        if isinstance(operation, BuildOperation):
            try:
                proposal = self._store.get_proposal(tenant_id, project_id, operation.proposal_id)
                valid_proposal = build_proposal_binding_matches(operation, proposal)
            except (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
                valid_proposal = False
            if not valid_proposal:
                return self._fail(operation, status, tenant_id, project_id, operation_id, "proposal binding mismatch", "PROPOSAL_BINDING_MISMATCH")
            if operation.firmware_build_request is not None:
                if self._build_context_revalidator is None:
                    return self._fail(operation, status, tenant_id, project_id, operation_id, "current build context unavailable", "APPROVAL_STALE")
                else:
                    try:
                        current = self._build_context_revalidator(operation)
                    except (OSError, RuntimeError, TypeError, ValueError):
                        current = False
                    if current is not True:
                        return self._fail(operation, status, tenant_id, project_id, operation_id, "build approval is stale", "APPROVAL_STALE")
        if isinstance(operation, SchematicParseOperation) and self._design_context_revalidator is None:
            return self._fail(operation, status, tenant_id, project_id, operation_id, "schematic evidence currentness unavailable", "APPROVAL_STALE")
        if isinstance(operation, (SchematicWriteOperation, SchematicErcOperation, SchematicParseOperation, PCBWriteOperation, PcbDrcOperation)) and self._design_context_revalidator is not None:
            try:
                current_design = self._design_context_revalidator(operation)
            except (OSError, RuntimeError, TypeError, ValueError):
                current_design = False
            if current_design is not True:
                return self._fail(operation, status, tenant_id, project_id, operation_id, "design approval is stale", "APPROVAL_STALE")
        native_move = isinstance(operation, KiCadNativeFootprintMoveOperation)
        if native_move:
            if self._native_move_adapter is None or not callable(getattr(self._native_move_adapter, "execute", None)) or self._native_move_revalidator is None or (
                operation.production_context is not None and not isinstance(self._native_move_adapter, NativeMoveProductionAdapter)
            ):
                return self._fail(operation, status, tenant_id, project_id, operation_id, "native footprint move is unavailable", "NATIVE_MOVE_UNAVAILABLE")
            try:
                current_native_move = self._native_move_revalidator(operation)
            except (OSError, RuntimeError, TypeError, ValueError):
                current_native_move = False
            if current_native_move is not True:
                return self._fail(operation, status, tenant_id, project_id, operation_id, "native footprint move source is stale", "APPROVAL_STALE")
        if isinstance(operation, PcbDrcBaselineOperation):
            try:
                self._require_baseline(operation, approval)
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
                return self._fail(operation, status, tenant_id, project_id, operation_id, "DRC baseline input unavailable before approval consumption", "APPROVAL_STALE")
        running = self._store.start_operation_transition(
            operation,
            approval_id=approval.grant_id,
            worker_id=self.worker_id,
            now=self._clock(),
            baseline_revalidate=(lambda: self._baseline_current(operation, approval)) if isinstance(operation, PcbDrcBaselineOperation) else None,
        )
        if running is None:
            return self._fail(operation, status, tenant_id, project_id, operation_id, "approval expired or replayed", "APPROVAL_EXPIRED_OR_REPLAYED")
        if isinstance(operation, PcbDrcBaselineOperation):
            return self._run_baseline(operation, running, approval)
        if independent_schematic:
            stopped = self._stop_schematic_before_dispatch(operation, approval)
            if stopped is not None:
                return stopped
        try:
            if isinstance(operation, SchematicParseOperation):
                current_design = self._design_context_revalidator(operation) is True
                stopped = self._stop_schematic_before_dispatch(operation, approval)
                if stopped is not None:
                    return stopped
                if not current_design:
                    return self._terminal(operation, running, OperationStatus.FAILED, "schematic evidence changed before dispatch", "APPROVAL_STALE")
                adapter = self._schematic_readback if isinstance(operation, SchematicReadBackOperation) else self._schematic_parse
                if adapter is None or not callable(getattr(adapter, "execute", None)):
                    return self._terminal(operation, running, OperationStatus.FAILED, "schematic XML adapter unavailable", "EVIDENCE_UNAVAILABLE")
                stopped = self._stop_schematic_before_dispatch(operation, approval)
                if stopped is not None:
                    return stopped
                result = adapter.execute(operation)
            elif native_move:
                try:
                    current_native_move = self._native_move_revalidator(operation)
                except (OSError, RuntimeError, TypeError, ValueError):
                    current_native_move = False
                if current_native_move is not True:
                    return self._fail(operation, running, tenant_id, project_id, operation_id, "native footprint move source changed before dispatch", "APPROVAL_STALE")
                if operation.production_context is None:
                    result = self._native_move_adapter.execute(operation)
                else:
                    consumed = self._store.get_approval(tenant_id, project_id, approval.grant_id)
                    if consumed is None or consumed.status is not ApprovalStatus.CONSUMED:
                        return self._terminal(operation, running, OperationStatus.FAILED, "native approval consumption unavailable", "APPROVAL_BINDING_MISMATCH")
                    result = self._native_move_adapter.execute(
                        operation, consumed, _Cancellation(self._store, tenant_id, project_id, operation_id),
                    )
            elif isinstance(operation, FirmwareSourceWriteOperation):
                if self._firmware_source_write is None or not callable(getattr(self._firmware_source_write, "execute", None)):
                    raise ValueError("firmware source write adapter unavailable")
                result = self._firmware_source_write.execute(operation)
            elif isinstance(operation, SchematicWriteOperation):
                if self._schematic_write is None or not callable(getattr(self._schematic_write, "execute", None)):
                    raise ValueError("schematic write adapter unavailable")
                result = self._schematic_write.execute(operation)
            elif isinstance(operation, SchematicErcOperation):
                if self._schematic_erc is None or not callable(getattr(self._schematic_erc, "execute", None)):
                    raise ValueError("schematic ERC adapter unavailable")
                if independent_schematic:
                    try:
                        current_design = self._design_context_revalidator is not None and self._design_context_revalidator(operation) is True
                    except (OSError, RuntimeError, TypeError, ValueError):
                        current_design = False
                    stopped = self._stop_schematic_before_dispatch(operation, approval)
                    if stopped is not None:
                        return stopped
                    if not current_design:
                        return self._terminal(operation, running, OperationStatus.FAILED, "schematic evidence changed before dispatch", "APPROVAL_STALE")
                result = self._schematic_erc.execute(operation)
            elif isinstance(operation, PCBWriteOperation):
                if self._pcb_write is None or not callable(getattr(self._pcb_write, "execute", None)):
                    raise ValueError("PCB write adapter unavailable")
                result = self._pcb_write.execute(operation)
            elif isinstance(operation, PcbDrcOperation):
                if self._pcb_drc is None or not callable(getattr(self._pcb_drc, "execute", None)):
                    raise ValueError("PCB DRC adapter unavailable")
                result = self._pcb_drc.execute(operation)
            elif isinstance(operation, KiCadManufacturingOperation):
                if self._manufacturing is None or not callable(getattr(self._manufacturing, "execute", None)):
                    raise ValueError("manufacturing adapter unavailable")
                if self._targets is None or self._workspace_resolver is None or self._registry is None:
                    raise ValueError("execution worker dependencies are unavailable")
                target = self._targets.get_bound(tenant_id=tenant_id, project_id=project_id, target_id=operation.target_id)
                if target is None or target.fingerprint != operation.target_fingerprint:
                    return self._fail(operation, running, tenant_id, project_id, operation_id, "TARGET_MISMATCH", "TARGET_MISMATCH")
                target = self._targets.revalidate(target)
                if target.fingerprint != operation.target_fingerprint:
                    return self._fail(operation, running, tenant_id, project_id, operation_id, "TARGET_MISMATCH", "TARGET_MISMATCH")
                workspace_root = self._workspace_resolver.resolve(tenant_id=tenant_id, project_id=project_id, snapshot_id=operation.workspace_snapshot_id, snapshot_fingerprint=operation.workspace_fingerprint)
                resolution = self._registry.resolve(operation.tool_id, target_kind=target.target_kind, operation=operation.operation_type)
                context = ExecutionContext(workspace_root=workspace_root, state_root=workspace_root / ".embedded-copilot-state", target=target, tool=resolution, cancellation=_Cancellation(self._store, tenant_id, project_id, operation_id), artifact_store=self._artifact_store)
                result = self._manufacturing.execute(operation, context)
            else:
                if self._targets is None or self._workspace_resolver is None or self._registry is None:
                    raise ValueError("execution worker dependencies are unavailable")
                target = self._targets.get_bound(tenant_id=tenant_id, project_id=project_id, target_id=operation.target_id)
                if target is None or target.fingerprint != operation.target_fingerprint:
                    return self._fail(operation, running, tenant_id, project_id, operation_id, "TARGET_MISMATCH", "TARGET_MISMATCH")
                target = self._targets.revalidate(target)
                if target.fingerprint != operation.target_fingerprint:
                    return self._fail(operation, running, tenant_id, project_id, operation_id, "TARGET_MISMATCH", "TARGET_MISMATCH")
                try:
                    workspace_root = self._workspace_resolver.resolve(tenant_id=tenant_id, project_id=project_id, snapshot_id=operation.workspace_snapshot_id, snapshot_fingerprint=operation.workspace_fingerprint)
                except (OSError, ValueError, WorkspaceApplyError):
                    if isinstance(operation, BuildOperation):
                        return self._terminal(operation, running, OperationStatus.FAILED, "build approval is stale", "APPROVAL_STALE")
                    raise
                tool_id = getattr(getattr(operation, "toolchain", None), "toolchain_id", None) or getattr(operation, "tool_id", None) or "SERIAL_UART"
                registry_operation = operation.operation_type
                if isinstance(operation, HardwareBringupOperation):
                    registry_operation = OperationType.UART_OBSERVATION if operation.action == "UART" else OperationType.FLASH
                resolution = self._registry.resolve(tool_id, target_kind=target.target_kind, operation=registry_operation)
                state_root = workspace_root / ".embedded-copilot-state"
                if isinstance(operation, BuildOperation) and operation.firmware_build_request is not None:
                    source_root, state_root = self._stage_build_workspace(operation, workspace_root)
                    # A source changed while staging invalidates the approved snapshot.
                    try:
                        self._workspace_resolver.resolve(tenant_id=tenant_id, project_id=project_id, snapshot_id=operation.workspace_snapshot_id, snapshot_fingerprint=operation.workspace_fingerprint)
                    except (OSError, ValueError, WorkspaceApplyError):
                        return self._terminal(operation, running, OperationStatus.FAILED, "build approval is stale", "APPROVAL_STALE")
                    if self._build_context_revalidator is not None:
                        try:
                            current = self._build_context_revalidator(operation)
                        except (OSError, RuntimeError, TypeError, ValueError):
                            current = False
                        if current is not True:
                            return self._terminal(operation, running, OperationStatus.FAILED, "build approval is stale", "APPROVAL_STALE")
                    workspace_root = source_root
                context = ExecutionContext(workspace_root=workspace_root, state_root=state_root, target=target, tool=resolution, cancellation=_Cancellation(self._store, tenant_id, project_id, operation_id), artifact_store=self._artifact_store)
                result = self._dispatch(operation, context)
        except TimeoutError:
            return self._terminal(operation, running, OperationStatus.TIMED_OUT, "operation timed out", "ADAPTER_TIMED_OUT")
        except InterruptedError:
            return self._terminal(operation, running, OperationStatus.CANCELLED, "operation cancelled", "ADAPTER_CANCELLED")
        except (OSError, RuntimeError, ValueError, PermissionError, WorkspaceApplyError):
            return self._terminal(operation, running, OperationStatus.FAILED, "operation failed closed", "ADAPTER_FAILED_CLOSED")
        if not isinstance(result, AdapterResult):
            return self._terminal(
                operation,
                running,
                OperationStatus.FAILED,
                "adapter result invalid",
                "ADAPTER_RESULT_INVALID",
            )
        independent_schematic = isinstance(operation, SchematicParseOperation) or (isinstance(operation, SchematicErcOperation) and operation.source_parse_operation_id is not None)
        if independent_schematic:
            current = self._store.get_status(tenant_id, project_id, operation_id)
            if current is None or current.cancellation_requested:
                return self._terminal(operation, running, OperationStatus.CANCELLED, "schematic evidence cancelled", "OPERATION_CANCELLED")
            if self._clock() >= min(operation.expires_at, approval.expires_at):
                return self._terminal(operation, running, OperationStatus.TIMED_OUT, "schematic evidence result arrived late", "ADAPTER_TIMED_OUT")
            try:
                current_source = self._design_context_revalidator is not None and self._design_context_revalidator(operation) is True
            except (OSError, RuntimeError, TypeError, ValueError):
                current_source = False
            if not current_source:
                return self._terminal(operation, running, OperationStatus.FAILED, "schematic evidence source changed", "APPROVAL_STALE")
            current = self._store.get_status(tenant_id, project_id, operation_id)
            if current is None or current.cancellation_requested:
                return self._terminal(operation, running, OperationStatus.CANCELLED, "schematic evidence cancelled", "OPERATION_CANCELLED")
            if self._clock() >= min(operation.expires_at, approval.expires_at):
                return self._terminal(operation, running, OperationStatus.TIMED_OUT, "schematic evidence result arrived late", "ADAPTER_TIMED_OUT")
        if native_move and operation.production_context is None and (
            result.workspace_apply_receipt is not None
            or result.artifact is not None
            or result.artifact_content is not None
            or result.additional_artifacts
            or result.schematic_erc_result is not None
            or result.pcb_artifact is not None
            or result.pcb_drc_result is not None
            or result.build_result is not None
            or result.build_evidence is not None
            or result.evidence_references
        ):
            return self._terminal(operation, running, OperationStatus.FAILED, "offline native move result contains production evidence", "NATIVE_MOVE_EVIDENCE_INVALID")
        if (
            isinstance(operation, BuildOperation)
            and result.status == "SUCCEEDED"
            and (result.build_result is None or result.build_evidence is None)
        ):
            return self._terminal(
                operation,
                running,
                OperationStatus.FAILED,
                "build verification evidence unavailable",
                "BUILD_EVIDENCE_UNAVAILABLE",
            )
        status_value = {"SUCCEEDED": OperationStatus.SUCCEEDED, "FAILED": OperationStatus.FAILED, "UNAVAILABLE": OperationStatus.FAILED, "CANCELLED": OperationStatus.CANCELLED, "TIMED_OUT": OperationStatus.TIMED_OUT}.get(result.status, OperationStatus.FAILED)
        native_production = result.native_move_production
        if native_move and operation.production_context is not None:
            if not isinstance(native_production, NativeMoveProductionResultV1) or (
                native_production.candidate_id != operation.proposal_id
                or native_production.candidate_fingerprint != operation.proposal_fingerprint
                or native_production.mapping_review_id != operation.mapping_review_id
                or native_production.mapping_review_fingerprint != operation.mapping_review_fingerprint
                or native_production.production_context_fingerprint != operation.production_context.fingerprint
                or native_production.import_revision_id != operation.import_revision_id
                or native_production.import_revision_fingerprint != operation.import_revision_fingerprint
                or native_production.before_manifest_fingerprint != operation.workspace_fingerprint
                or (result.workspace_apply_receipt is None) != (native_production.workspace_apply_receipt_fingerprint is None)
                or (result.workspace_apply_receipt is not None and result.workspace_apply_receipt.fingerprint != native_production.workspace_apply_receipt_fingerprint)
                or result.artifact is not None or result.artifact_content is not None or result.additional_artifacts
                or result.pcb_artifact is not None or result.pcb_drc_result is not None
            ):
                return self._terminal(operation, running, OperationStatus.RECOVERY_REQUIRED, "native production result rejected", "NATIVE_MOVE_EVIDENCE_INVALID")
            if native_production.recovery_state is not RecoveryState.NONE:
                status_value = OperationStatus.RECOVERY_REQUIRED
        elif result.native_move_production is not None:
            return self._terminal(operation, running, OperationStatus.FAILED, "native production evidence is operation-specific", "ADAPTER_RESULT_INVALID")
        schematic_xml_result = None
        library_observation = None
        if isinstance(operation, SchematicParseOperation):
            try:
                schematic_xml_result = SchematicXmlResult.model_validate(
                    result.schematic_xml_result.model_dump(mode="python") if isinstance(result.schematic_xml_result, SchematicXmlResult) else result.schematic_xml_result,
                    strict=False,
                )
                identity = schematic_xml_result.netlist_artifact
                if (schematic_xml_result.operation_id != operation.operation_id
                        or schematic_xml_result.source_write_operation_id != operation.source_write_operation_id
                        or schematic_xml_result.source_write_receipt_fingerprint != operation.source_write_receipt_fingerprint
                        or schematic_xml_result.input_artifact_path != operation.artifact_path
                        or schematic_xml_result.fingerprint_before != operation.artifact_fingerprint
                        or identity != result.artifact or identity.artifact_type != "NETLIST"
                        or identity.tenant_id != tenant_id or identity.project_id != project_id
                        or identity.target_id != operation.target_id
                        or identity.workspace_fingerprint != operation.workspace_fingerprint
                        or identity.build_operation_id != operation.operation_id
                        or result.artifact_content is None or result.workspace_apply_receipt is not None
                        or (result.additional_artifacts and not (type(operation) is SchematicReadBackOperation
                            and operation.library_map_evidence_id is not None))
                        or self._design_context_revalidator(operation) is not True
                        or (result.status == "SUCCEEDED") != (schematic_xml_result.parse_status == "PASS")):
                    raise ValueError("schematic XML result binding mismatch")
                if isinstance(operation, SchematicReadBackOperation) and operation.library_map_evidence_id is not None:
                    if result.status != "SUCCEEDED" or len(result.additional_artifacts) != 1:
                        raise ValueError("library observation count invalid")
                    library_identity, library_bytes = result.additional_artifacts[0]
                    library_identity = ArtifactIdentity.model_validate(library_identity.model_dump(mode="python"))
                    library_observation = parse_library_observation_payload(library_bytes, library_identity)
                    if (library_observation.source_evidence_id != operation.library_map_evidence_id
                        or library_observation.source_evidence_fingerprint != operation.library_map_evidence_fingerprint
                        or library_identity.tenant_id != tenant_id or library_identity.project_id != project_id
                        or library_identity.target_id != operation.target_id
                        or library_identity.workspace_fingerprint != operation.workspace_fingerprint
                        or library_identity.build_operation_id != operation.operation_id
                        or library_identity.toolchain_id != identity.toolchain_id
                        or library_identity.toolchain_version != identity.toolchain_version
                        or library_identity.build_configuration != identity.build_configuration
                        or result.evidence_references != (library_identity.artifact_id,)
                        or self._library_observation_authorizer is None
                        or self._library_observation_authorizer(operation, library_identity, library_bytes, result.artifact_content) is not True):
                        raise ValueError("library observation source invalid")
            except sqlite3.Error:
                if type(operation) is SchematicReadBackOperation and operation.library_map_evidence_id is not None:
                    return self._terminal(operation, running, OperationStatus.FAILED, "library observation database read rejected", "ARTIFACT_MISMATCH")
                raise
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                return self._terminal(operation, running, OperationStatus.FAILED, "schematic XML result rejected", "ARTIFACT_MISMATCH")
        elif result.schematic_xml_result is not None:
            return self._terminal(operation, running, OperationStatus.FAILED, "schematic XML result is operation-specific", "ADAPTER_RESULT_INVALID")
        workspace_receipt = result.workspace_apply_receipt
        if (
            workspace_receipt is not None
            and getattr(getattr(workspace_receipt, "recovery_status", None), "value", None)
            == "RECOVERY_REQUIRED"
        ):
            status_value = OperationStatus.RECOVERY_REQUIRED
        if library_observation is not None:
            if self._artifact_store is None:
                return self._terminal(operation, running, OperationStatus.FAILED, "artifact store unavailable", "ARTIFACT_STORE_UNAVAILABLE")
            try:
                for artifact, content in ((result.artifact, result.artifact_content), *result.additional_artifacts):
                    self._artifact_store.put(artifact, content)
                    self._store.save_artifact(artifact)
                registered = self._store.get_artifact(tenant_id, project_id, library_observation.artifact.artifact_id)
                primary = self._store.get_artifact(tenant_id, project_id, result.artifact.artifact_id)
                if registered != library_observation.artifact or primary != result.artifact:
                    raise ValueError("registered library artifact changed")
                raw = self._artifact_store.read(registered)
                xml = self._artifact_store.read(primary)
                parse_library_observation_payload(raw, registered)
                if (len(xml) != primary.size_bytes
                    or self._library_observation_authorizer(operation, registered, raw, xml) is not True
                    or self._design_context_revalidator(operation) is not True):
                    raise ValueError("registered library evidence stale")
                current = self._store.get_status(tenant_id, project_id, operation_id)
                if current is None or current.cancellation_requested:
                    return self._terminal(operation, running, OperationStatus.CANCELLED, "library observation cancelled", "OPERATION_CANCELLED")
                if self._clock() >= min(operation.expires_at, approval.expires_at):
                    return self._terminal(operation, running, OperationStatus.TIMED_OUT, "library observation saved late", "ADAPTER_TIMED_OUT")
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
                return self._terminal(operation, running, OperationStatus.FAILED, "library observation persistence rejected", "ARTIFACT_IDENTITY_INVALID")
        if result.artifact is not None and library_observation is None:
            if self._artifact_store is None or result.artifact_content is None:
                return self._terminal(operation, running, OperationStatus.FAILED, "artifact store unavailable", "ARTIFACT_STORE_UNAVAILABLE")
            try:
                self._artifact_store.put(result.artifact, result.artifact_content)
            except ValueError:
                return self._terminal(operation, running, OperationStatus.FAILED, "artifact identity verification failed", "ARTIFACT_IDENTITY_INVALID")
            self._store.save_artifact(result.artifact)
        if result.additional_artifacts and library_observation is None:
            if self._artifact_store is None:
                return self._terminal(operation, running, OperationStatus.FAILED, "artifact store unavailable", "ARTIFACT_STORE_UNAVAILABLE")
            try:
                for identity, content in result.additional_artifacts:
                    self._artifact_store.put(identity, content)
                    self._store.save_artifact(identity)
            except ValueError:
                return self._terminal(operation, running, OperationStatus.FAILED, "artifact identity verification failed", "ARTIFACT_IDENTITY_INVALID")
        diagnostics_summary = result.summary
        if (
            operation.operation_type is OperationType.TEST
            and not self._test_adapter_configured
            and result.status == "UNAVAILABLE"
        ):
            diagnostics_summary = "TEST_ADAPTER_NOT_CONFIGURED"
        workspace_mutations = ()
        workspace_receipt_fingerprint = None
        firmware_evidence_parse = "NOT_APPLICABLE"
        receipt_recovery_state = RecoveryState.NONE
        if isinstance(operation, FirmwareSourceWriteOperation):
            firmware_evidence_parse = "UNAVAILABLE"
            if workspace_receipt is not None:
                workspace_receipt_fingerprint = workspace_receipt.fingerprint
                recovery_value = workspace_receipt.recovery_status.value
                if recovery_value == "RECOVERY_REQUIRED":
                    receipt_recovery_state = RecoveryState.OPERATOR_DECISION_REQUIRED
                elif recovery_value == "RECOVERED":
                    receipt_recovery_state = RecoveryState.RECOVERED
                if status_value is OperationStatus.SUCCEEDED:
                    workspace_mutations = tuple(
                        WorkspaceMutationResult(
                            relative_path=item.relative_path,
                            content_fingerprint=item.content_fingerprint,
                            recovery_state=recovery_value,
                        )
                        for item in operation.files
                    )
        elif isinstance(operation, (SchematicWriteOperation, PCBWriteOperation)):
            if workspace_receipt is not None:
                workspace_receipt_fingerprint = workspace_receipt.fingerprint
                recovery_value = workspace_receipt.recovery_status.value
                if recovery_value == "RECOVERY_REQUIRED":
                    receipt_recovery_state = RecoveryState.OPERATOR_DECISION_REQUIRED
                elif recovery_value == "RECOVERED":
                    receipt_recovery_state = RecoveryState.RECOVERED
                if status_value is OperationStatus.SUCCEEDED:
                    workspace_mutations = (
                        WorkspaceMutationResult(
                            relative_path=operation.target_relative_path,
                            content_fingerprint=operation.content_fingerprint,
                            recovery_state=recovery_value,
                        ),
                    )
        schematic_erc_status = None
        schematic_erc_result_fingerprint = None
        schematic_erc_result = None
        if isinstance(operation, SchematicErcOperation) and workspace_receipt is not None:
            return self._terminal(
                operation,
                running,
                OperationStatus.FAILED,
                "schematic ERC adapter returned workspace mutation evidence",
                "ERC_RESULT_INVALID",
            )
        if isinstance(operation, SchematicErcOperation) and workspace_receipt is None:
            try:
                erc_result = SchematicErcResult.model_validate(
                    result.schematic_erc_result.model_dump(mode="python")
                    if isinstance(result.schematic_erc_result, SchematicErcResult)
                    else result.schematic_erc_result,
                    strict=False,
                )
            except (TypeError, ValueError):
                return self._terminal(
                    operation,
                    running,
                    OperationStatus.FAILED,
                    "schematic ERC result invalid",
                    "ERC_RESULT_INVALID",
                )
            if (
                erc_result.operation_id != operation.operation_id
                or erc_result.schematic_artifact_ref != operation.schematic_artifact_ref
                or erc_result.schematic_artifact_ref != operation.target_relative_path
                or (
                    erc_result.schematic_content_fingerprint is not None
                    and erc_result.schematic_content_fingerprint != operation.schematic_artifact_fingerprint
                )
                or erc_result.receipt_ref != operation.source_operation_receipt_fingerprint
            ):
                return self._terminal(
                    operation,
                    running,
                    OperationStatus.FAILED,
                    "schematic ERC result binding mismatch",
                    "ERC_RESULT_BINDING_MISMATCH",
                )
            expected_result_status = {
                "ERC_PASS": "SUCCEEDED",
                "ERC_FAIL": "SUCCEEDED",
                "ERC_WARNINGS": "SUCCEEDED",
                "ERC_BLOCKED": "FAILED",
                "ERC_UNAVAILABLE": "UNAVAILABLE",
            }[erc_result.status.value]
            if result.status != expected_result_status:
                return self._terminal(
                    operation,
                    running,
                    OperationStatus.FAILED,
                    "schematic ERC result status mismatch",
                    "ERC_RESULT_INVALID",
                )
            schematic_erc_status = getattr(getattr(erc_result, "status", None), "value", None)
            schematic_erc_result_fingerprint = getattr(erc_result, "fingerprint", None)
            schematic_erc_result = erc_result
        pcb_drc_result = None
        if isinstance(operation, PcbDrcOperation):
            try:
                pcb_drc_result = PCBDrcResult.model_validate(
                    result.pcb_drc_result.model_dump(mode="python") if isinstance(result.pcb_drc_result, PCBDrcResult) else result.pcb_drc_result,
                    strict=False,
                )
            except (TypeError, ValueError):
                return self._terminal(operation, running, OperationStatus.FAILED, "PCB DRC result invalid", "PCB_DRC_RESULT_INVALID")
            if pcb_drc_result.operation_id != operation.operation_id or pcb_drc_result.pcb_artifact_ref != operation.pcb_artifact_ref or pcb_drc_result.pcb_content_hash != operation.pcb_content_fingerprint:
                return self._terminal(operation, running, OperationStatus.FAILED, "PCB DRC result binding mismatch", "PCB_DRC_RESULT_BINDING_MISMATCH")
            expected_status = "SUCCEEDED" if pcb_drc_result.status in {PCBDrcStatus.DRC_PASS, PCBDrcStatus.DRC_WARNINGS, PCBDrcStatus.DRC_FAIL} else "UNAVAILABLE"
            if result.status != expected_status:
                return self._terminal(operation, running, OperationStatus.FAILED, "PCB DRC result status mismatch", "PCB_DRC_RESULT_INVALID")
        native_move_offline = None
        if native_move and operation.production_context is None:
            native_move_offline = NativeMoveOfflineResult(
                candidate_id=operation.proposal_id,
                candidate_fingerprint=operation.proposal_fingerprint,
                mapping_review_id=operation.mapping_review_id,
                mapping_review_fingerprint=operation.mapping_review_fingerprint,
                import_revision_id=operation.import_revision_id,
                import_revision_fingerprint=operation.import_revision_fingerprint,
                source_set_fingerprint=operation.source_set_fingerprint,
                diff_fingerprint=operation.diff_fingerprint,
                fake_result_status=result.status,
            )
        if native_move and operation.production_context is not None and native_production is not None:
            workspace_receipt_fingerprint = native_production.workspace_apply_receipt_fingerprint
            receipt_recovery_state = native_production.recovery_state
        if library_observation is not None:
            try:
                registered = self._store.get_artifact(tenant_id, project_id, library_observation.artifact.artifact_id)
                raw = self._artifact_store.read(registered)
                primary = self._store.get_artifact(tenant_id, project_id, result.artifact.artifact_id)
                xml = self._artifact_store.read(primary)
                if (registered != library_observation.artifact or primary != result.artifact
                    or len(xml) != primary.size_bytes
                    or self._library_observation_authorizer(operation, registered, raw, xml) is not True
                    or self._design_context_revalidator(operation) is not True):
                    raise ValueError("library observation final source stale")
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
                return self._terminal(operation, running, OperationStatus.FAILED, "library observation final source rejected", "APPROVAL_STALE")
            current = self._store.get_status(tenant_id, project_id, operation_id)
            if current is None or current.cancellation_requested:
                return self._terminal(operation, running, OperationStatus.CANCELLED, "library observation cancelled", "OPERATION_CANCELLED")
            if self._clock() >= min(operation.expires_at, approval.expires_at):
                return self._terminal(operation, running, OperationStatus.TIMED_OUT, "library observation saved late", "ADAPTER_TIMED_OUT")
        receipt = OperationReceipt(operation_id=operation_id, tenant_id=tenant_id, project_id=project_id, operation_type=operation.operation_type, status=status_value, tool_execution_status=result.status, flash_verification_status=result.flash_verification_status, target_runtime_verification_status=("NOT_APPLICABLE" if isinstance(operation, (FirmwareSourceWriteOperation, SchematicWriteOperation, SchematicErcOperation, SchematicParseOperation, PCBWriteOperation, PcbDrcOperation, KiCadNativeFootprintMoveOperation)) else "NOT_VERIFIED"), artifact=result.artifact, evidence_references=result.evidence_references, safe_diagnostics_summary=diagnostics_summary, recovery_state=receipt_recovery_state, workspace_mutations=workspace_mutations, workspace_apply_receipt_fingerprint=workspace_receipt_fingerprint, firmware_evidence_parse=firmware_evidence_parse, schematic_erc_status=schematic_erc_status, schematic_erc_result_fingerprint=schematic_erc_result_fingerprint, schematic_erc_result=schematic_erc_result, pcb_artifact=result.pcb_artifact, pcb_drc_result=pcb_drc_result, build_result=result.build_result, build_evidence=result.build_evidence, native_move_offline=native_move_offline, native_move_production=native_production, schematic_parse_result=(schematic_xml_result if operation.operation_type is OperationType.KICAD_SCHEMATIC_PARSE else None), schematic_readback_result=(schematic_xml_result if operation.operation_type is OperationType.KICAD_SCHEMATIC_READ_BACK else None), correlation=running.correlation, created_at=self._clock())
        reason_code = {
            OperationStatus.SUCCEEDED: "ADAPTER_SUCCEEDED",
            OperationStatus.FAILED: diagnostics_summary if diagnostics_summary == "TEST_ADAPTER_NOT_CONFIGURED" else "ADAPTER_FAILED",
            OperationStatus.CANCELLED: "ADAPTER_CANCELLED",
            OperationStatus.TIMED_OUT: "ADAPTER_TIMED_OUT",
            OperationStatus.RECOVERY_REQUIRED: "WORKSPACE_RECOVERY_REQUIRED",
        }[status_value]
        return self._terminal(
            operation,
            running,
            status_value,
            diagnostics_summary,
            reason_code,
            receipt=receipt,
        )

    def _require_baseline(self, operation: PcbDrcBaselineOperation, approval: object) -> None:
        from .drc_baseline import PcbDrcBaselineCollector
        collector = self._drc_baseline_collector
        if (not isinstance(collector, PcbDrcBaselineCollector) or self._artifact_store is None
            or not callable(self._drc_baseline_revalidator)
            or collector.policy != operation.collector_policy or not collector.available()
            or not collector.target_current(operation)):
            raise ValueError("baseline canonical composition unavailable")
        # Closure and full Workspace I/O precede the last authority read.
        if self._drc_baseline_revalidator(operation) is not True:
            raise ValueError("baseline C1 authority changed")
        current = self._store.get_status(operation.tenant_id, operation.project_id, operation.operation_id)
        if current is None or current.cancellation_requested:
            raise InterruptedError("baseline cancelled")
        if self._clock() >= min(operation.expires_at, approval.expires_at):
            raise TimeoutError("baseline expired")

    def _baseline_current(self, operation: PcbDrcBaselineOperation, approval: object) -> bool:
        try:
            self._require_baseline(operation, approval)
            return True
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
            return False

    def _require_baseline_member(self, operation: PcbDrcBaselineOperation, identity: ArtifactIdentity, content: bytes) -> None:
        manifest_roles = {"tool_bundle", "rules_manifest", "dependency_manifest", "workspace-manifest", "workspace-after"}
        log_roles = {"environment", "input-pcb", "prl-before", "raw-report", "prl-after", "provenance", "context"}
        prefix = operation.operation_id + "-"
        role = identity.artifact_id[len(prefix):] if identity.artifact_id.startswith(prefix) else ""
        expected_type = "MANIFEST" if role in manifest_roles else "LOG" if role in log_roles else None
        if (expected_type is None or identity.artifact_type != expected_type
            or identity.build_configuration != "DRC_BASELINE_OBSERVATION"
            or type(content) is not bytes or identity.size_bytes != len(content)
            or identity.artifact_sha256 != "sha256:" + hashlib.sha256(content).hexdigest()
            or (identity.tenant_id, identity.project_id, identity.target_id, identity.workspace_fingerprint,
                identity.build_operation_id, identity.toolchain_id, identity.toolchain_version) != (
                operation.tenant_id, operation.project_id, operation.target_id, operation.workspace_fingerprint,
                operation.operation_id, operation.collector_policy.tool_id, operation.collector_policy.tool_version)):
            raise ValueError("baseline member binding changed")

    def _require_baseline_collection(self, operation: PcbDrcBaselineOperation, members: tuple[ArtifactIdentity, ...]) -> None:
        from .drc_baseline import _encoded
        roles = {"tool_bundle", "rules_manifest", "dependency_manifest", "environment", "input-pcb",
            "workspace-manifest", "raw-report", "workspace-after", "provenance", "context"}
        if operation.prl_before_sha256 is not None:
            roles.update({"prl-before", "prl-after"})
        identities = {identity.artifact_id: identity for identity in members}
        if len(identities) != len(members) or set(identities) != {operation.operation_id + "-" + role for role in roles}:
            raise ValueError("baseline complete collection unavailable")
        snapshot = self._drc_baseline_collector.workspace.snapshot(
            tenant_id=operation.tenant_id, project_id=operation.project_id, target_id=operation.target_id)
        if (snapshot.project_fingerprint, snapshot.target_fingerprint) != (operation.workspace_fingerprint, operation.target_fingerprint):
            raise ValueError("baseline canonical Workspace changed")
        expected = _encoded({"schema_version": 1, "entries": [{"role": "FILE", "relative_path": item.relative_file_reference,
            "sha256": item.content_fingerprint, "byte_size": item.size, "source": "canonical-workspace"}
            for item in sorted(snapshot.files, key=lambda item: item.relative_file_reference)]})
        for role in ("workspace-manifest", "workspace-after"):
            if self._artifact_store.read(identities[operation.operation_id + "-" + role]) != expected:
                raise ValueError("baseline Workspace inventory changed")
        pcb = next((item for item in snapshot.files if item.relative_file_reference == operation.pcb_relative_path), None)
        prl = next((item for item in snapshot.files if item.relative_file_reference == operation.prl_relative_path), None)
        if (pcb is None or pcb.content_fingerprint != operation.pcb_sha256
            or (None if prl is None else prl.content_fingerprint) != operation.prl_before_sha256):
            raise ValueError("baseline canonical input changed")
        for role, expected_sha in (("input-pcb", operation.pcb_sha256),
            *((role, operation.prl_before_sha256) for role in ("prl-before", "prl-after") if operation.prl_before_sha256 is not None)):
            content = self._artifact_store.read(identities[operation.operation_id + "-" + role])
            if "sha256:" + hashlib.sha256(content).hexdigest() != expected_sha:
                raise ValueError("baseline captured input changed")

    def _baseline_success_current(self, operation: PcbDrcBaselineOperation, approval: object, receipt: OperationReceipt, members: tuple[ArtifactIdentity, ...]) -> bool:
        from .native_drc_provenance import validate_drc_v2_context
        try:
            refs = receipt.evidence_references
            frozen = {identity.artifact_id: identity for identity in members}
            if set(frozen) != set(refs) or len(frozen) != len(members):
                return False
            if len(set(refs)) != len(refs) or receipt.artifact is None or receipt.artifact.artifact_id not in refs:
                return False
            for ref in refs:
                identity = self._store.get_artifact(operation.tenant_id, operation.project_id, ref)
                if (identity is None or identity != frozen[ref] or identity.build_operation_id != operation.operation_id
                    or identity.target_id != operation.target_id or identity.workspace_fingerprint != operation.workspace_fingerprint):
                    return False
                self._require_baseline_member(operation, identity, self._artifact_store.read(identity))
            if receipt.artifact.artifact_id != operation.operation_id + "-context":
                return False
            if self._store.get_artifact(operation.tenant_id, operation.project_id, receipt.artifact.artifact_id) != receipt.artifact:
                return False
            def read(identity):
                if identity.artifact_id not in refs or self._store.get_artifact(operation.tenant_id, operation.project_id, identity.artifact_id) != identity:
                    raise ValueError("baseline final registered member changed")
                return self._artifact_store.read(identity)
            self._require_baseline_collection(operation, members)
            context = validate_drc_v2_context(json.loads(self._artifact_store.read(receipt.artifact)), read_artifact=read, policy=operation.collector_policy)
            errors = sum(item["severity"] == "error" for item in context["violations"])
            warnings = sum(item["severity"] == "warning" for item in context["violations"])
            return (receipt.safe_diagnostics_summary == f"DRC_BASELINE_OBSERVATION scope=BASELINE_COMPARISON_ONLY error_count={errors} warning_count={warnings}"
                and self._baseline_current(operation, approval))
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
            return False

    def _run_baseline(self, operation: PcbDrcBaselineOperation, running: OperationStatusView, approval: object) -> OperationStatusView:
        from .native_drc_provenance import validate_drc_v2_context
        registered: list[str] = []
        result = None
        pairs = ()
        try:
            self._require_baseline(operation, approval)
            result = self._drc_baseline_collector.execute(
                operation, deadline=min(operation.expires_at, approval.expires_at),
                cancellation=_Cancellation(self._store, operation.tenant_id, operation.project_id, operation.operation_id),
                revalidate=lambda: self._drc_baseline_revalidator(operation) is True,
            )
            pairs = result.additional_artifacts + (() if result.artifact is None else ((result.artifact, result.artifact_content),))
            if len({item.artifact_id for item, _ in pairs}) != len(pairs):
                raise ValueError("baseline duplicate member")
            for identity, content in pairs:
                self._require_baseline_member(operation, identity, content)
                self._require_baseline(operation, approval)
                self._artifact_store.put(identity, content)
                self._require_baseline(operation, approval)
                self._store.save_artifact(identity)
                registered.append(identity.artifact_id)
                self._require_baseline(operation, approval)
            if result.status != "SUCCEEDED":
                raise {"TIMED_OUT": TimeoutError, "CANCELLED": InterruptedError}.get(result.status, ValueError)("baseline failed")
            if (result.artifact is None or result.artifact.artifact_id != operation.operation_id + "-context"
                or len(set(result.evidence_references)) != len(result.evidence_references)
                or set(result.evidence_references) != set(registered)):
                raise ValueError("baseline context references unavailable")
            for identity, _ in pairs:
                saved = self._store.get_artifact(operation.tenant_id, operation.project_id, identity.artifact_id)
                if saved != identity:
                    raise ValueError("baseline registered member changed")
                self._artifact_store.read(saved)
            self._require_baseline_collection(operation, tuple(identity for identity, _ in pairs))
            context = json.loads(self._artifact_store.read(result.artifact))
            def read_member(identity):
                saved = self._store.get_artifact(operation.tenant_id, operation.project_id, identity.artifact_id)
                if saved != identity or identity.artifact_id not in registered:
                    raise ValueError("baseline member not registered")
                return self._artifact_store.read(saved)
            context = validate_drc_v2_context(context, read_artifact=read_member, policy=operation.collector_policy)
            errors = sum(item["severity"] == "error" for item in context["violations"])
            warnings = sum(item["severity"] == "warning" for item in context["violations"])
            summary = f"DRC_BASELINE_OBSERVATION scope=BASELINE_COMPARISON_ONLY error_count={errors} warning_count={warnings}"
            self._require_baseline(operation, approval)
            receipt = OperationReceipt(operation_id=operation.operation_id, tenant_id=operation.tenant_id,
                project_id=operation.project_id, operation_type=operation.operation_type, status=OperationStatus.SUCCEEDED,
                tool_execution_status="SUCCEEDED", flash_verification_status="NOT_APPLICABLE", target_runtime_verification_status="NOT_APPLICABLE",
                artifact=result.artifact, evidence_references=tuple(registered), safe_diagnostics_summary=summary,
                recovery_state=RecoveryState.NONE, correlation=running.correlation, created_at=self._clock())
            return self._terminal(operation, running, OperationStatus.SUCCEEDED, summary, "DRC_BASELINE_OBSERVED", receipt=receipt, baseline_members=tuple(identity for identity,_ in pairs))
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError, sqlite3.Error) as error:
            # Lost authority forbids success, not immutable recording of an observed failure.
            # Every diagnostic member still has exact scope and actual byte identity.
            for identity, content in pairs:
                try:
                    if identity.artifact_id in registered:
                        continue
                    self._require_baseline_member(operation, identity, content)
                    self._baseline_current(operation, approval)
                    self._artifact_store.put(identity, content)
                    self._baseline_current(operation, approval)
                    self._store.save_artifact(identity)
                    registered.append(identity.artifact_id)
                    self._baseline_current(operation, approval)
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError, sqlite3.Error):
                    continue
            # Preserve already persisted members and the unique collector report. Never replay.
            state = self._classify_recovery(operation)
            recovery = RecoveryState.NONE if state is RecoveryState.INTERRUPTED else RecoveryState.TARGET_STATE_UNKNOWN
            current = self._store.get_status(operation.tenant_id, operation.project_id, operation.operation_id)
            status = OperationStatus.CANCELLED if current is not None and current.cancellation_requested else OperationStatus.TIMED_OUT if self._clock() >= min(operation.expires_at, approval.expires_at) or isinstance(error, TimeoutError) else OperationStatus.FAILED
            receipt = OperationReceipt(operation_id=operation.operation_id, tenant_id=operation.tenant_id,
                project_id=operation.project_id, operation_type=operation.operation_type, status=status,
                tool_execution_status=status.value, flash_verification_status="NOT_APPLICABLE", target_runtime_verification_status="NOT_APPLICABLE",
                evidence_references=tuple(registered), safe_diagnostics_summary="DRC_BASELINE_OBSERVATION_UNAVAILABLE",
                recovery_state=recovery, correlation=running.correlation, created_at=self._clock())
            return self._terminal(operation, running, status, receipt.safe_diagnostics_summary, "DRC_BASELINE_FAILED", receipt=receipt)

    def _stage_build_workspace(self, operation: BuildOperation, original: Path) -> tuple[Path, Path]:
        request = operation.firmware_build_request
        if request is None or request.configuration is None:
            raise ValueError("typed build configuration unavailable")
        original = original.resolve(strict=True)
        sandbox_root = self._build_sandbox_root.resolve(strict=False)
        if sandbox_root == original or sandbox_root.is_relative_to(original) or original.is_relative_to(sandbox_root):
            raise ValueError("build sandbox overlaps project workspace")
        excluded = request.configuration.build_directory.replace("\\", "/")
        excluded_parts = () if excluded == "." else tuple(excluded.split("/"))
        required = [item.relative_path for item in request.source_artifacts]
        required.extend(
            item for item in (
                request.configuration.linker_script,
                request.configuration.cmake_project,
                request.configuration.cmake_toolchain_file,
            ) if item is not None
        )
        if excluded_parts and any(tuple(path.replace("\\", "/").split("/"))[:len(excluded_parts)] == excluded_parts for path in required):
            raise ValueError("build inputs overlap excluded output directory")
        sandbox_root.mkdir(parents=True, exist_ok=True)
        operation_directory = hashlib.sha256(
            f"{operation.tenant_id}|{operation.project_id}|{operation.operation_id}".encode("utf-8")
        ).hexdigest()
        operation_root = sandbox_root / operation_directory
        operation_root.mkdir(exist_ok=False)
        source_root = operation_root / "source"
        source_root.mkdir()
        stack = [(original, source_root, ())]
        while stack:
            source_dir, target_dir, parts = stack.pop()
            with os.scandir(source_dir) as entries:
                for entry in entries:
                    relative = (*parts, entry.name)
                    if entry.is_symlink():
                        raise ValueError("symlink in build workspace")
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name == ".embedded-copilot-state" or (excluded_parts and relative == excluded_parts):
                            continue
                        child = target_dir / entry.name
                        child.mkdir()
                        stack.append((Path(entry.path), child, relative))
                    elif entry.is_file(follow_symlinks=False):
                        target = target_dir / entry.name
                        shutil.copyfile(entry.path, target)
                        if hashlib.sha256(Path(entry.path).read_bytes()).digest() != hashlib.sha256(target.read_bytes()).digest():
                            raise ValueError("source changed during build staging")
                    else:
                        raise ValueError("unsupported build workspace entry")
        return source_root, operation_root

    def _dispatch(self, operation: BuildOperation | TestOperation | SimulationOperation | FlashOperation | HardwareBringupOperation | UartObservationOperation | HilOperation, context: ExecutionContext):
        if operation.operation_type is OperationType.BUILD:
            return self._build.execute(operation, context)
        if operation.operation_type is OperationType.TEST:
            return self._test.execute(operation, context)
        if operation.operation_type is OperationType.SIMULATION:
            return self._simulation.execute(operation, context)
        if operation.operation_type is OperationType.FLASH:
            return self._flash.execute(operation, context)
        if operation.operation_type is OperationType.UART_OBSERVATION:
            return self._uart.execute(operation, context)
        if operation.operation_type is OperationType.HIL:
            return self._hil.execute(operation, context)
        if operation.operation_type is OperationType.HARDWARE_BRINGUP:
            if self._hardware_bringup is None or not callable(getattr(self._hardware_bringup, "execute", None)):
                return AdapterResult("UNAVAILABLE", "hardware bring-up adapter unavailable")
            return self._hardware_bringup.execute(operation, context)
        raise ValueError("unsupported operation")

    def _stop_schematic_before_dispatch(self, operation, approval) -> OperationStatusView | None:
        current = self._store.get_status(operation.tenant_id, operation.project_id, operation.operation_id)
        if current is None:
            raise RuntimeError("schematic evidence status unavailable")
        if current.cancellation_requested:
            return self._terminal(operation, current, OperationStatus.CANCELLED, "schematic evidence cancelled before dispatch", "OPERATION_CANCELLED")
        if self._clock() >= min(operation.expires_at, approval.expires_at):
            return self._terminal(operation, current, OperationStatus.TIMED_OUT, "schematic evidence expired before dispatch", "OPERATION_TIMED_OUT")
        return None

    def _fail(self, operation, status, tenant_id: str, project_id: str, operation_id: str, detail: str, reason_code: str):
        if status is None:
            return None
        return self._terminal(operation, status, OperationStatus.FAILED, detail, reason_code)

    def _terminal(self, operation, status: OperationStatusView, operation_status: OperationStatus, detail: str, reason_code: str, *, receipt: OperationReceipt | None = None, baseline_members: tuple[ArtifactIdentity, ...] = ()) -> OperationStatusView:
        current = self._store.get_status(status.tenant_id, status.project_id, status.operation_id) or status
        independent_schematic = isinstance(operation, SchematicParseOperation) or (
            isinstance(operation, SchematicErcOperation) and operation.source_parse_operation_id is not None
        )
        if independent_schematic and operation_status is OperationStatus.SUCCEEDED:
            approval = None if current.approval_id is None else self._store.get_approval(
                operation.tenant_id, operation.project_id, current.approval_id,
            )
            if current.cancellation_requested:
                operation_status, detail, reason_code, receipt = (
                    OperationStatus.CANCELLED, "schematic evidence cancelled before result commit", "OPERATION_CANCELLED", None,
                )
            elif approval is None or self._clock() >= min(operation.expires_at, approval.expires_at):
                operation_status, detail, reason_code, receipt = (
                    OperationStatus.TIMED_OUT, "schematic evidence expired before result commit", "OPERATION_TIMED_OUT", None,
                )
        updated = current.model_validate({
            **current.model_dump(mode="python"),
            "status": operation_status,
            "safe_detail": detail,
            "receipt_fingerprint": None if receipt is None else receipt.fingerprint,
            "recovery_state": receipt.recovery_state if isinstance(operation, PcbDrcBaselineOperation) and receipt is not None else current.recovery_state,
            "updated_at": self._clock(),
            "fingerprint": "sha256:" + "0" * 64,
        })
        if operation is None:
            if receipt is not None:
                self._store.save_receipt(receipt)
            self._store.mark_queue(tenant_id=status.tenant_id, project_id=status.project_id, operation_id=status.operation_id, state=operation_status.value)
            return self._store.save_status(updated)
        return self._store.terminal_transition(
            operation,
            current,
            updated,
            reason_code=reason_code,
            receipt=receipt,
            baseline_revalidate=(lambda: self._baseline_success_current(operation, self._store.get_approval(operation.tenant_id, operation.project_id, current.approval_id), receipt, baseline_members)) if isinstance(operation, PcbDrcBaselineOperation) and operation_status is OperationStatus.SUCCEEDED else None,
        )


__all__ = ("ExecutionWorker",)
