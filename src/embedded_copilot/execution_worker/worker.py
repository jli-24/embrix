from __future__ import annotations

from datetime import UTC, datetime
from typing import Callable

from .adapters import (
    BuildToolAdapter,
    FlashToolAdapter,
    HilScenarioAdapter,
    UartObservationAdapter,
    UnavailableTestAdapter,
)
from .contracts import (
    ApprovalStatus,
    BuildOperation,
    FlashOperation,
    HilOperation,
    OperationReceipt,
    OperationStatus,
    OperationStatusView,
    OperationType,
    RecoveryState,
    TestOperation,
    UartObservationOperation,
)
from .ports import ExecutionContext, TargetRegistryPort, TestAdapterPort, WorkspaceResolverPort
from .registry import ExecutionToolRegistry
from .store import SQLiteExecutionStore
from .artifacts import ArtifactStore


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

    def __init__(self, *, worker_id: str, store: SQLiteExecutionStore, registry: ExecutionToolRegistry, workspace_resolver: WorkspaceResolverPort, targets: TargetRegistryPort, artifact_store: ArtifactStore | None = None, build=None, test: TestAdapterPort | None = None, flash=None, uart=None, hil=None, clock: Callable[[], datetime] | None = None) -> None:
        self.worker_id = worker_id
        self._store = store
        self._registry = registry
        self._workspace_resolver = workspace_resolver
        self._targets = targets
        self._artifact_store = artifact_store
        self._build = build or BuildToolAdapter()
        self._test = test if test is not None else UnavailableTestAdapter()
        self._test_adapter_configured = test is not None
        self._flash = flash or FlashToolAdapter()
        self._uart = uart or UartObservationAdapter()
        self._hil = hil or HilScenarioAdapter(self._uart)
        self._clock = clock or (lambda: datetime.now(UTC))

    def recover_after_restart(self) -> tuple[OperationStatusView, ...]:
        return self._store.recover_running(now=self._clock())

    def run_once(self) -> OperationStatusView | None:
        claim = self._store.claim_next(worker_id=self.worker_id, lease_seconds=60, now=self._clock())
        if claim is None:
            return None
        tenant_id, project_id, operation_id = claim
        status = self._store.get_status(tenant_id, project_id, operation_id)
        if status is None or status.approval_id is None:
            return self._fail(status, tenant_id, project_id, operation_id, "operation approval unavailable")
        # The operation payload is intentionally supplied by the application
        # composition, not reconstructed from arbitrary queue fields.
        operation = getattr(self._store, "get_operation", lambda *_: None)(tenant_id, project_id, operation_id)
        if operation is None:
            return self._fail(status, tenant_id, project_id, operation_id, "typed operation unavailable")
        approval = self._store.get_approval(tenant_id, project_id, status.approval_id)
        if (
            approval is None
            or approval.operation_type is not operation.operation_type
            or approval.approval_purpose.value != operation.operation_type.value
            or approval.operation_fingerprint != operation.fingerprint
            or approval.status is not ApprovalStatus.ISSUED
        ):
            return self._fail(status, tenant_id, project_id, operation_id, "approval binding mismatch")
        consumed = self._store.consume_approval(tenant_id=tenant_id, project_id=project_id, grant_id=approval.grant_id, operation_fingerprint=operation.fingerprint, now=self._clock())
        if consumed is None:
            return self._fail(status, tenant_id, project_id, operation_id, "approval expired or replayed")
        running = OperationStatusView.model_validate({**status.model_dump(mode="python"), "status": OperationStatus.RUNNING, "worker_id": self.worker_id, "updated_at": self._clock(), "fingerprint": "sha256:" + "0" * 64})
        self._store.save_status(running)
        try:
            target = self._targets.get_bound(tenant_id=tenant_id, project_id=project_id, target_id=operation.target_id)
            if target is None or target.fingerprint != operation.target_fingerprint:
                return self._fail(running, tenant_id, project_id, operation_id, "TARGET_MISMATCH")
            target = self._targets.revalidate(target)
            if target.fingerprint != operation.target_fingerprint:
                return self._fail(running, tenant_id, project_id, operation_id, "TARGET_MISMATCH")
            workspace_root = self._workspace_resolver.resolve(tenant_id=tenant_id, project_id=project_id, snapshot_id=operation.workspace_snapshot_id, snapshot_fingerprint=operation.workspace_fingerprint)
            tool_id = getattr(getattr(operation, "toolchain", None), "toolchain_id", None) or getattr(operation, "tool_id", None) or "SERIAL_UART"
            resolution = self._registry.resolve(tool_id, target_kind=target.target_kind, operation=operation.operation_type)
            context = ExecutionContext(workspace_root=workspace_root, state_root=workspace_root / ".embedded-copilot-state", target=target, tool=resolution, cancellation=_Cancellation(self._store, tenant_id, project_id, operation_id), artifact_store=self._artifact_store)
            result = self._dispatch(operation, context)
        except TimeoutError:
            return self._terminal(running, OperationStatus.TIMED_OUT, "operation timed out")
        except InterruptedError:
            return self._terminal(running, OperationStatus.CANCELLED, "operation cancelled")
        except (OSError, ValueError, PermissionError):
            return self._terminal(running, OperationStatus.FAILED, "operation failed closed")
        status_value = {"SUCCEEDED": OperationStatus.SUCCEEDED, "FAILED": OperationStatus.FAILED, "UNAVAILABLE": OperationStatus.FAILED, "CANCELLED": OperationStatus.CANCELLED, "TIMED_OUT": OperationStatus.TIMED_OUT}.get(result.status, OperationStatus.FAILED)
        if result.artifact is not None:
            if self._artifact_store is None or result.artifact_content is None:
                return self._terminal(running, OperationStatus.FAILED, "artifact store unavailable")
            try:
                self._artifact_store.put(result.artifact, result.artifact_content)
            except ValueError:
                return self._terminal(running, OperationStatus.FAILED, "artifact identity verification failed")
            self._store.save_artifact(result.artifact)
        diagnostics_summary = result.summary
        if (
            operation.operation_type is OperationType.TEST
            and not self._test_adapter_configured
            and result.status == "UNAVAILABLE"
        ):
            diagnostics_summary = "TEST_ADAPTER_NOT_CONFIGURED"
        receipt = OperationReceipt(operation_id=operation_id, tenant_id=tenant_id, project_id=project_id, operation_type=operation.operation_type, status=status_value, tool_execution_status=result.status, flash_verification_status=result.flash_verification_status, target_runtime_verification_status="NOT_VERIFIED", artifact=result.artifact, evidence_references=result.evidence_references, safe_diagnostics_summary=diagnostics_summary, recovery_state=RecoveryState.NONE, created_at=self._clock())
        self._store.save_receipt(receipt)
        self._store.mark_queue(tenant_id=tenant_id, project_id=project_id, operation_id=operation_id, state=status_value.value)
        final = running.model_validate({**running.model_dump(mode="python"), "status": status_value, "worker_id": self.worker_id, "receipt_fingerprint": receipt.fingerprint, "updated_at": self._clock(), "fingerprint": "sha256:" + "0" * 64})
        return self._store.save_status(final)

    def _dispatch(self, operation: BuildOperation | TestOperation | FlashOperation | UartObservationOperation | HilOperation, context: ExecutionContext):
        if operation.operation_type is OperationType.BUILD:
            return self._build.execute(operation, context)
        if operation.operation_type is OperationType.TEST:
            return self._test.execute(operation, context)
        if operation.operation_type is OperationType.FLASH:
            return self._flash.execute(operation, context)
        if operation.operation_type is OperationType.UART_OBSERVATION:
            return self._uart.execute(operation, context)
        if operation.operation_type is OperationType.HIL:
            return self._hil.execute(operation, context)
        raise ValueError("unsupported operation")

    def _fail(self, status, tenant_id: str, project_id: str, operation_id: str, detail: str):
        if status is None:
            return None
        return self._terminal(status, OperationStatus.FAILED, detail)

    def _terminal(self, status: OperationStatusView, operation_status: OperationStatus, detail: str) -> OperationStatusView:
        updated = status.model_validate({**status.model_dump(mode="python"), "status": operation_status, "safe_detail": detail, "updated_at": self._clock(), "fingerprint": "sha256:" + "0" * 64})
        self._store.mark_queue(tenant_id=status.tenant_id, project_id=status.project_id, operation_id=status.operation_id, state=operation_status.value)
        return self._store.save_status(updated)


__all__ = ("ExecutionWorker",)
