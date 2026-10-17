from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

from .contracts import (
    ApprovalStatus,
    CancellationRequest,
    BuildOperation,
    ExecutionProposal,
    ExecutionTarget,
    FlashOperation,
    HardwareBringupOperation,
    FirmwareSourceWriteOperation,
    SchematicErcOperation,
    SchematicParseOperation,
    SchematicReadBackOperation,
    SchematicWriteOperation,
    PCBWriteOperation,
    PcbDrcOperation,
    PcbDrcBaselineOperation,
    KiCadManufacturingOperation,
    KiCadNativeFootprintMoveOperation,
    FingerprintedContract,
    HilOperation,
    HilStepKind,
    OperationEvent,
    OperationEventType,
    OperationApprovalGrant,
    OperationReceipt,
    OperationStatus,
    OperationStatusView,
    OperationType,
    RecoveryDecision,
    RecoveryState,
    SimulationOperation,
    TestOperation,
    UartObservationOperation,
    TargetDiscoveryCandidate,
    identifier,
)

T = TypeVar("T", bound=FingerprintedContract)


class SQLiteExecutionStore:
    """Durable v6.6 state store with atomic approval consumption and worker claims."""

    def __init__(self, database: Path) -> None:
        self._database = Path(database)
        self._database.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS v66_objects (
                    kind TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    PRIMARY KEY(kind, tenant_id, project_id, object_id)
                );
                CREATE TABLE IF NOT EXISTS v66_queue (
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    worker_id TEXT,
                    lease_until TEXT,
                    PRIMARY KEY(tenant_id, project_id, operation_id)
                );
                CREATE TABLE IF NOT EXISTS v66_tombstones (
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    tombstoned_at TEXT NOT NULL,
                    PRIMARY KEY(tenant_id, project_id)
                );
                CREATE TABLE IF NOT EXISTS v66_operation_events (
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL CHECK(sequence > 0),
                    event_id TEXT NOT NULL,
                    operation_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    PRIMARY KEY(tenant_id, project_id, sequence),
                    UNIQUE(tenant_id, project_id, event_id)
                );
                CREATE INDEX IF NOT EXISTS v66_operation_events_by_operation
                ON v66_operation_events(
                    tenant_id, project_id, operation_id, sequence
                );
                """
            )

    @staticmethod
    def _body(value: Any) -> str:
        return json.dumps(value.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _load(row: sqlite3.Row | None, model: type[T]) -> T | None:
        return None if row is None else model.model_validate_json(row["body"])

    def _save(self, kind: str, value: T, *, object_id: str) -> T:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO v66_objects(kind, tenant_id, project_id, object_id, body) VALUES(?,?,?,?,?)",
                (kind, getattr(value, "tenant_id"), getattr(value, "project_id"), object_id, self._body(value)),
            )
        return value

    def _append_event_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        operation: Any,
        event_type: OperationEventType,
        status: OperationStatus,
        recovery_state: RecoveryState,
        reason_code: str,
        occurred_at: datetime,
        approval_id: str | None = None,
        receipt_fingerprint: str | None = None,
        event_id: str | None = None,
    ) -> OperationEvent:
        row = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence "
            "FROM v66_operation_events WHERE tenant_id=? AND project_id=?",
            (operation.tenant_id, operation.project_id),
        ).fetchone()
        event = OperationEvent(
            event_id=event_id or f"operation-event-{uuid4().hex}",
            sequence=int(row["next_sequence"]),
            tenant_id=operation.tenant_id,
            project_id=operation.project_id,
            operation_id=operation.operation_id,
            operation_fingerprint=operation.fingerprint,
            operation_type=operation.operation_type,
            event_type=event_type,
            status=status,
            recovery_state=recovery_state,
            approval_id=approval_id,
            receipt_fingerprint=receipt_fingerprint,
            reason_code=reason_code,
            occurred_at=occurred_at,
            correlation=self._correlation_for(operation, approval_id),
        )
        connection.execute(
            "INSERT INTO v66_operation_events(tenant_id, project_id, sequence, event_id, operation_id, body) "
            "VALUES(?,?,?,?,?,?)",
            (
                event.tenant_id,
                event.project_id,
                event.sequence,
                event.event_id,
                event.operation_id,
                self._body(event),
            ),
        )
        return event

    @staticmethod
    def _correlation_for(operation: Any, approval_id: str | None):
        correlation = getattr(operation, "correlation", None)
        if correlation is None:
            return None
        return correlation.__class__.model_validate(
            {
                **correlation.model_dump(mode="python"),
                "approval_id": approval_id,
            }
        )

    @classmethod
    def _assert_correlation_binding(
        cls,
        operation: Any,
        value: Any,
        *,
        approval_id: str | None,
        field: str,
    ) -> None:
        expected = cls._correlation_for(operation, approval_id)
        if value.correlation != expected:
            raise ValueError(f"{field} correlation binding mismatch")

    def _append_event(
        self,
        *,
        operation: Any,
        event_type: OperationEventType,
        status: OperationStatus,
        recovery_state: RecoveryState,
        reason_code: str,
        occurred_at: datetime,
        approval_id: str | None = None,
        receipt_fingerprint: str | None = None,
        event_id: str | None = None,
    ) -> OperationEvent:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                event = self._append_event_in_transaction(
                    connection,
                    operation=operation,
                    event_type=event_type,
                    status=status,
                    recovery_state=recovery_state,
                    reason_code=reason_code,
                    occurred_at=occurred_at,
                    approval_id=approval_id,
                    receipt_fingerprint=receipt_fingerprint,
                    event_id=event_id,
                )
            except Exception:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
        return event

    @staticmethod
    def _event_limit(limit: int) -> int:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("event query limit is invalid")
        return limit

    def list_operation_events(
        self,
        tenant_id: str,
        project_id: str,
        operation_id: str,
        *,
        limit: int = 100,
    ) -> tuple[OperationEvent, ...]:
        tenant_id = identifier(tenant_id, field="tenant_id")
        project_id = identifier(project_id, field="project_id")
        operation_id = identifier(operation_id, field="operation_id")
        limit = self._event_limit(limit)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT body FROM v66_operation_events WHERE tenant_id=? AND project_id=? AND operation_id=? ORDER BY sequence LIMIT ?",
                (tenant_id, project_id, operation_id, limit),
            ).fetchall()
        return tuple(OperationEvent.model_validate_json(row["body"]) for row in rows)

    def read_operation_events_after(
        self,
        tenant_id: str,
        project_id: str,
        *,
        last_event_id: str | None,
        limit: int = 100,
    ) -> tuple[OperationEvent, ...] | None:
        tenant_id = identifier(tenant_id, field="tenant_id")
        project_id = identifier(project_id, field="project_id")
        limit = self._event_limit(limit)
        with self._lock, self._connect() as connection:
            after_sequence = 0
            if last_event_id is not None:
                try:
                    last_event_id = identifier(last_event_id, field="last_event_id")
                except (TypeError, ValueError):
                    return None
                cursor = connection.execute(
                    "SELECT sequence FROM v66_operation_events WHERE tenant_id=? AND project_id=? AND event_id=?",
                    (tenant_id, project_id, last_event_id),
                ).fetchone()
                if cursor is None:
                    return None
                after_sequence = int(cursor["sequence"])
            rows = connection.execute(
                "SELECT body FROM v66_operation_events WHERE tenant_id=? AND project_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                (tenant_id, project_id, after_sequence, limit),
            ).fetchall()
        return tuple(OperationEvent.model_validate_json(row["body"]) for row in rows)

    def _get(self, kind: str, tenant_id: str, project_id: str, object_id: str, model: type[T]) -> T | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT body FROM v66_objects WHERE kind=? AND tenant_id=? AND project_id=? AND object_id=?",
                (kind, tenant_id, project_id, object_id),
            ).fetchone()
        return self._load(row, model)

    @staticmethod
    def _operation_from_body(body: str):
        payload = json.loads(body)
        model = {
            "BUILD": BuildOperation,
            "TEST": TestOperation,
            "SIMULATION": SimulationOperation,
            "FLASH": FlashOperation,
            "HARDWARE_BRINGUP": HardwareBringupOperation,
            "UART_OBSERVATION": UartObservationOperation,
            "HIL": HilOperation,
            "FIRMWARE_SOURCE_WRITE": FirmwareSourceWriteOperation,
            "KICAD_SCHEMATIC_WRITE": SchematicWriteOperation,
            "KICAD_SCHEMATIC_ERC": SchematicErcOperation,
            "KICAD_SCHEMATIC_PARSE": SchematicParseOperation,
            "KICAD_SCHEMATIC_READ_BACK": SchematicReadBackOperation,
            "KICAD_PCB_WRITE": PCBWriteOperation,
            "KICAD_NATIVE_FOOTPRINT_MOVE": KiCadNativeFootprintMoveOperation,
            "KICAD_PCB_DRC": PcbDrcOperation,
            "KICAD_PCB_DRC_BASELINE": PcbDrcBaselineOperation,
            "KICAD_GERBER_EXPORT": KiCadManufacturingOperation,
            "KICAD_DRILL_EXPORT": KiCadManufacturingOperation,
            "KICAD_POSITION_EXPORT": KiCadManufacturingOperation,
            "MANUFACTURING_PACKAGE_BUILD": KiCadManufacturingOperation,
        }.get(payload.get("operation_type"))
        if model is None:
            return None
        if model is HilOperation:
            payload["operation_type"] = OperationType.HIL
            for step in payload["scenario"]["steps"]:
                step["kind"] = HilStepKind(step["kind"])
            return model.model_validate(payload)
        return model.model_validate_json(body, strict=False)

    @staticmethod
    def _write_object(
        connection: sqlite3.Connection,
        *,
        kind: str,
        value: FingerprintedContract,
        object_id: str,
        replace: bool = False,
    ) -> None:
        verb = "INSERT OR REPLACE" if replace else "INSERT"
        connection.execute(
            f"{verb} INTO v66_objects(kind, tenant_id, project_id, object_id, body) VALUES(?,?,?,?,?)",
            (kind, value.tenant_id, value.project_id, object_id, SQLiteExecutionStore._body(value)),
        )

    def _require_persisted_operation(
        self, connection: sqlite3.Connection, operation: T
    ) -> None:
        row = connection.execute(
            "SELECT body FROM v66_objects WHERE kind='operation' AND tenant_id=? AND project_id=? AND object_id=?",
            (operation.tenant_id, operation.project_id, operation.operation_id),
        ).fetchone()
        persisted = None if row is None else self._operation_from_body(row["body"])
        if persisted != operation:
            raise ValueError("operation identity changed")

    def create_operation_transition(
        self, operation: T, status: OperationStatusView, *, occurred_at: datetime
    ) -> OperationStatusView:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._assert_correlation_binding(
                    operation,
                    status,
                    approval_id=status.approval_id,
                    field="operation status",
                )
                self._write_object(
                    connection,
                    kind="operation",
                    value=operation,
                    object_id=operation.operation_id,
                )
                self._write_object(
                    connection,
                    kind="status",
                    value=status,
                    object_id=status.operation_id,
                )
                self._append_event_in_transaction(
                    connection,
                    operation=operation,
                    event_type=OperationEventType.OPERATION_CREATED,
                    status=status.status,
                    recovery_state=status.recovery_state,
                    reason_code="AWAITING_EXPLICIT_APPROVAL",
                    occurred_at=occurred_at,
                )
            except Exception:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
        return status

    def issue_approval_transition(
        self, operation: T, approval: OperationApprovalGrant
    ) -> OperationApprovalGrant:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_persisted_operation(connection, operation)
                status_row = connection.execute(
                    "SELECT body FROM v66_objects WHERE kind='status' AND tenant_id=? AND project_id=? AND object_id=?",
                    (operation.tenant_id, operation.project_id, operation.operation_id),
                ).fetchone()
                status = self._load(status_row, OperationStatusView)
                if status is None or status.status is not OperationStatus.WAIT_APPROVAL:
                    raise ValueError("operation is not awaiting approval")
                self._write_object(
                    connection,
                    kind="approval",
                    value=approval,
                    object_id=approval.grant_id,
                )
                self._append_event_in_transaction(
                    connection,
                    operation=operation,
                    event_type=OperationEventType.APPROVAL_ISSUED,
                    status=status.status,
                    recovery_state=status.recovery_state,
                    reason_code="APPROVAL_ISSUED",
                    occurred_at=approval.issued_at,
                    approval_id=approval.grant_id,
                )
            except Exception:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
        return approval

    def enqueue_transition(
        self,
        operation: T,
        approval: OperationApprovalGrant,
        *,
        occurred_at: datetime,
    ) -> OperationStatusView:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_persisted_operation(connection, operation)
                status_row = connection.execute(
                    "SELECT body FROM v66_objects WHERE kind='status' AND tenant_id=? AND project_id=? AND object_id=?",
                    (operation.tenant_id, operation.project_id, operation.operation_id),
                ).fetchone()
                current = self._load(status_row, OperationStatusView)
                approval_row = connection.execute(
                    "SELECT body FROM v66_objects WHERE kind='approval' AND tenant_id=? AND project_id=? AND object_id=?",
                    (operation.tenant_id, operation.project_id, approval.grant_id),
                ).fetchone()
                persisted_approval = self._load(approval_row, OperationApprovalGrant)
                if (
                    current is None
                    or current.status is not OperationStatus.WAIT_APPROVAL
                    or persisted_approval != approval
                    or approval.operation_id != operation.operation_id
                    or approval.operation_type is not operation.operation_type
                    or approval.operation_fingerprint != operation.fingerprint
                    or approval.requester_id != operation.requester_id
                    or approval.proposal_id != operation.proposal_id
                    or approval.proposal_fingerprint != operation.proposal_fingerprint
                    or approval.target_id != operation.target_id
                    or approval.target_fingerprint != operation.target_fingerprint
                    or approval.workspace_snapshot_id != operation.workspace_snapshot_id
                    or approval.workspace_fingerprint != operation.workspace_fingerprint
                    or approval.status is not ApprovalStatus.ISSUED
                    or occurred_at >= approval.expires_at
                ):
                    raise ValueError("approval binding mismatch")
                self._assert_correlation_binding(
                    operation,
                    current,
                    approval_id=current.approval_id,
                    field="operation status",
                )
                updated = OperationStatusView.model_validate(
                    {
                        **current.model_dump(mode="python"),
                        "status": OperationStatus.QUEUED,
                        "approval_id": approval.grant_id,
                        "correlation": self._correlation_for(operation, approval.grant_id),
                        "updated_at": occurred_at,
                        "fingerprint": "sha256:" + "0" * 64,
                    }
                )
                connection.execute(
                    "UPDATE v66_objects SET body=? WHERE kind='status' AND tenant_id=? AND project_id=? AND object_id=? AND body=?",
                    (
                        self._body(updated),
                        operation.tenant_id,
                        operation.project_id,
                        operation.operation_id,
                        self._body(current),
                    ),
                )
                connection.execute(
                    "INSERT INTO v66_queue(tenant_id, project_id, operation_id, state, worker_id, lease_until) VALUES(?,?,?,?,NULL,NULL)",
                    (operation.tenant_id, operation.project_id, operation.operation_id, "QUEUED"),
                )
                self._append_event_in_transaction(
                    connection,
                    operation=operation,
                    event_type=OperationEventType.OPERATION_QUEUED,
                    status=updated.status,
                    recovery_state=updated.recovery_state,
                    reason_code="APPROVAL_BOUND",
                    occurred_at=occurred_at,
                    approval_id=approval.grant_id,
                )
            except Exception:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
        return updated

    def start_operation_transition(
        self,
        operation: T,
        *,
        approval_id: str,
        worker_id: str,
        now: datetime,
        baseline_revalidate: Callable[[], bool] | None = None,
    ) -> OperationStatusView | None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_persisted_operation(connection, operation)
            except Exception:
                connection.execute("ROLLBACK")
                raise
            status_row = connection.execute(
                "SELECT body FROM v66_objects WHERE kind='status' AND tenant_id=? AND project_id=? AND object_id=?",
                (operation.tenant_id, operation.project_id, operation.operation_id),
            ).fetchone()
            approval_row = connection.execute(
                "SELECT body FROM v66_objects WHERE kind='approval' AND tenant_id=? AND project_id=? AND object_id=?",
                (operation.tenant_id, operation.project_id, approval_id),
            ).fetchone()
            queue_row = connection.execute(
                "SELECT state, worker_id FROM v66_queue WHERE tenant_id=? AND project_id=? AND operation_id=?",
                (operation.tenant_id, operation.project_id, operation.operation_id),
            ).fetchone()
            status = self._load(status_row, OperationStatusView)
            approval = self._load(approval_row, OperationApprovalGrant)
            if (
                status is None
                or status.status is not OperationStatus.QUEUED
                or status.approval_id != approval_id
                or approval is None
                or approval.operation_id != operation.operation_id
                or approval.operation_type is not operation.operation_type
                or approval.operation_fingerprint != operation.fingerprint
                or approval.requester_id != operation.requester_id
                or approval.proposal_id != operation.proposal_id
                or approval.proposal_fingerprint != operation.proposal_fingerprint
                or approval.target_id != operation.target_id
                or approval.target_fingerprint != operation.target_fingerprint
                or approval.workspace_snapshot_id != operation.workspace_snapshot_id
                or approval.workspace_fingerprint != operation.workspace_fingerprint
                or approval.status is not ApprovalStatus.ISSUED
                or now >= approval.expires_at
                or queue_row is None
                or queue_row["state"] != "CLAIMED"
                or queue_row["worker_id"] != worker_id
            ):
                connection.execute("ROLLBACK")
                return None
            self._assert_correlation_binding(
                operation,
                status,
                approval_id=status.approval_id,
                field="operation status",
            )
            try:
                if isinstance(operation, PcbDrcBaselineOperation) and (baseline_revalidate is None or baseline_revalidate() is not True):
                    connection.execute("ROLLBACK")
                    return None
                consumed = OperationApprovalGrant.model_validate(
                    {
                        **approval.model_dump(mode="python"),
                        "status": ApprovalStatus.CONSUMED,
                        "fingerprint": "sha256:" + "0" * 64,
                    }
                )
                running = OperationStatusView.model_validate(
                    {
                        **status.model_dump(mode="python"),
                        "status": OperationStatus.RUNNING,
                        "worker_id": worker_id,
                        "updated_at": now,
                        "fingerprint": "sha256:" + "0" * 64,
                    }
                )
                connection.execute(
                    "UPDATE v66_objects SET body=? WHERE kind='approval' AND tenant_id=? AND project_id=? AND object_id=? AND body=?",
                    (self._body(consumed), operation.tenant_id, operation.project_id, approval_id, self._body(approval)),
                )
                connection.execute(
                    "UPDATE v66_objects SET body=? WHERE kind='status' AND tenant_id=? AND project_id=? AND object_id=? AND body=?",
                    (self._body(running), operation.tenant_id, operation.project_id, operation.operation_id, self._body(status)),
                )
                self._append_event_in_transaction(
                    connection,
                    operation=operation,
                    event_type=OperationEventType.OPERATION_STARTED,
                    status=running.status,
                    recovery_state=running.recovery_state,
                    reason_code="APPROVAL_CONSUMED",
                    occurred_at=now,
                    approval_id=approval_id,
                )
            except Exception:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
        return running

    def cancellation_transition(
        self,
        operation: T,
        request: CancellationRequest,
        current: OperationStatusView,
        updated: OperationStatusView,
    ) -> OperationStatusView:
        event_type = (
            OperationEventType.CANCELLATION_PENDING
            if updated.status is OperationStatus.CANCEL_PENDING
            else OperationEventType.CANCELLATION_REQUESTED
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_persisted_operation(connection, operation)
                row = connection.execute(
                    "SELECT body FROM v66_objects WHERE kind='status' AND tenant_id=? AND project_id=? AND object_id=?",
                    (operation.tenant_id, operation.project_id, operation.operation_id),
                ).fetchone()
                persisted = self._load(row, OperationStatusView)
                if persisted != current:
                    raise ValueError("operation state changed")
                self._assert_correlation_binding(
                    operation,
                    current,
                    approval_id=current.approval_id,
                    field="operation status",
                )
                self._assert_correlation_binding(
                    operation,
                    updated,
                    approval_id=updated.approval_id,
                    field="operation status",
                )
                self._write_object(connection, kind="cancellation", value=request, object_id=request.request_id)
                connection.execute(
                    "UPDATE v66_objects SET body=? WHERE kind='status' AND tenant_id=? AND project_id=? AND object_id=? AND body=?",
                    (self._body(updated), operation.tenant_id, operation.project_id, operation.operation_id, self._body(current)),
                )
                self._append_event_in_transaction(
                    connection,
                    operation=operation,
                    event_type=event_type,
                    status=updated.status,
                    recovery_state=updated.recovery_state,
                    reason_code="OPERATOR_REQUESTED_CANCELLATION",
                    occurred_at=request.requested_at,
                    approval_id=updated.approval_id,
                )
            except Exception:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
        return updated

    def terminal_transition(
        self,
        operation: T,
        current: OperationStatusView,
        updated: OperationStatusView,
        *,
        reason_code: str,
        receipt: OperationReceipt | None = None,
        baseline_revalidate: Callable[[], bool] | None = None,
    ) -> OperationStatusView:
        event_type = {
            OperationStatus.SUCCEEDED: OperationEventType.OPERATION_SUCCEEDED,
            OperationStatus.FAILED: OperationEventType.OPERATION_FAILED,
            OperationStatus.CANCELLED: OperationEventType.OPERATION_CANCELLED,
            OperationStatus.TIMED_OUT: OperationEventType.OPERATION_TIMED_OUT,
            OperationStatus.RECOVERY_REQUIRED: OperationEventType.RECOVERY_REQUIRED,
        }.get(updated.status)
        if event_type is None:
            raise ValueError("terminal operation status is invalid")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_persisted_operation(connection, operation)
                row = connection.execute(
                    "SELECT body FROM v66_objects WHERE kind='status' AND tenant_id=? AND project_id=? AND object_id=?",
                    (operation.tenant_id, operation.project_id, operation.operation_id),
                ).fetchone()
                persisted = self._load(row, OperationStatusView)
                if persisted != current or current.status not in {
                    OperationStatus.QUEUED,
                    OperationStatus.RUNNING,
                    OperationStatus.CANCEL_REQUESTED,
                    OperationStatus.CANCEL_PENDING,
                }:
                    raise ValueError("operation state changed")
                independent_schematic = isinstance(operation, SchematicParseOperation) or (
                    isinstance(operation, SchematicErcOperation) and operation.source_parse_operation_id is not None
                )
                if independent_schematic and updated.status is OperationStatus.SUCCEEDED:
                    approval_row = connection.execute(
                        "SELECT body FROM v66_objects WHERE kind='approval' AND tenant_id=? AND project_id=? AND object_id=?",
                        (operation.tenant_id, operation.project_id, current.approval_id),
                    ).fetchone()
                    approval = self._load(approval_row, OperationApprovalGrant)
                    if (current.cancellation_requested or approval is None
                        or updated.updated_at >= min(operation.expires_at, approval.expires_at)):
                        raise ValueError("schematic evidence success cancelled or expired")
                if isinstance(operation, PcbDrcBaselineOperation) and updated.status is OperationStatus.SUCCEEDED:
                    if baseline_revalidate is None or baseline_revalidate() is not True:
                        raise ValueError("baseline canonical input changed before final transaction")
                self._assert_correlation_binding(
                    operation,
                    current,
                    approval_id=current.approval_id,
                    field="operation status",
                )
                self._assert_correlation_binding(
                    operation,
                    updated,
                    approval_id=updated.approval_id,
                    field="operation status",
                )
                if receipt is not None:
                    if (
                        receipt.operation_id != operation.operation_id
                        or receipt.operation_type is not operation.operation_type
                        or receipt.status is not updated.status
                        or updated.receipt_fingerprint != receipt.fingerprint
                    ):
                        raise ValueError("receipt binding mismatch")
                    self._assert_correlation_binding(
                        operation,
                        receipt,
                        approval_id=updated.approval_id,
                        field="operation receipt",
                    )
                    self._write_object(
                        connection,
                        kind="receipt",
                        value=receipt,
                        object_id=receipt.operation_id,
                        replace=True,
                    )
                connection.execute(
                    "UPDATE v66_queue SET state=?, worker_id=NULL, lease_until=NULL WHERE tenant_id=? AND project_id=? AND operation_id=?",
                    (updated.status.value, operation.tenant_id, operation.project_id, operation.operation_id),
                )
                connection.execute(
                    "UPDATE v66_objects SET body=? WHERE kind='status' AND tenant_id=? AND project_id=? AND object_id=? AND body=?",
                    (self._body(updated), operation.tenant_id, operation.project_id, operation.operation_id, self._body(current)),
                )
                self._append_event_in_transaction(
                    connection,
                    operation=operation,
                    event_type=event_type,
                    status=updated.status,
                    recovery_state=updated.recovery_state,
                    reason_code=reason_code,
                    occurred_at=updated.updated_at,
                    approval_id=updated.approval_id,
                    receipt_fingerprint=updated.receipt_fingerprint,
                )
            except Exception:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
        return updated

    def recovery_resolution_transition(
        self,
        operation: T,
        current: OperationStatusView,
        decision: RecoveryDecision,
        updated: OperationStatusView,
    ) -> OperationStatusView:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_persisted_operation(connection, operation)
                row = connection.execute(
                    "SELECT body FROM v66_objects WHERE kind='status' AND tenant_id=? AND project_id=? AND object_id=?",
                    (operation.tenant_id, operation.project_id, operation.operation_id),
                ).fetchone()
                persisted = self._load(row, OperationStatusView)
                if persisted != current or current.status is not OperationStatus.RECOVERY_REQUIRED:
                    raise ValueError("operation state changed")
                self._assert_correlation_binding(
                    operation,
                    current,
                    approval_id=current.approval_id,
                    field="operation status",
                )
                self._assert_correlation_binding(
                    operation,
                    updated,
                    approval_id=updated.approval_id,
                    field="operation status",
                )
                self._write_object(connection, kind="recovery", value=decision, object_id=decision.decision_id)
                connection.execute(
                    "UPDATE v66_objects SET body=? WHERE kind='status' AND tenant_id=? AND project_id=? AND object_id=? AND body=?",
                    (self._body(updated), operation.tenant_id, operation.project_id, operation.operation_id, self._body(current)),
                )
                self._append_event_in_transaction(
                    connection,
                    operation=operation,
                    event_type=OperationEventType.RECOVERY_RESOLVED,
                    status=updated.status,
                    recovery_state=updated.recovery_state,
                    reason_code="RECOVERY_DECISION_APPLIED",
                    occurred_at=decision.decided_at,
                    approval_id=updated.approval_id,
                    receipt_fingerprint=updated.receipt_fingerprint,
                )
            except Exception:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
        return updated

    def save_proposal(self, value: ExecutionProposal) -> ExecutionProposal:
        return self._save("proposal", value, object_id=value.proposal_id)

    def save_operation(self, value: T) -> T:
        return self._save("operation", value, object_id=value.operation_id)

    def get_operation(self, tenant_id: str, project_id: str, operation_id: str):
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT body FROM v66_objects WHERE kind='operation' AND tenant_id=? AND project_id=? AND object_id=?",
                (tenant_id, project_id, operation_id),
            ).fetchone()
        if row is None:
            return None
        return self._operation_from_body(row["body"])

    def terminal_schematic_receipts(self) -> tuple[tuple[object, OperationReceipt], ...]:
        """Reconcile only committed receipts; never infer a result from RUNNING."""
        with self._lock, self._connect() as connection:
            rows = connection.execute("SELECT body FROM v66_objects WHERE kind='receipt' ORDER BY object_id").fetchall()
        result = []
        for row in rows:
            receipt = OperationReceipt.model_validate_json(row["body"], strict=False)
            if receipt.operation_type.value not in {"KICAD_SCHEMATIC_WRITE", "KICAD_SCHEMATIC_PARSE", "KICAD_SCHEMATIC_READ_BACK", "KICAD_SCHEMATIC_ERC"}:
                continue
            operation = self.get_operation(receipt.tenant_id, receipt.project_id, receipt.operation_id)
            if operation is not None:
                result.append((operation, receipt))
        return tuple(result)

    def list_operations(self, tenant_id: str, project_id: str, *, limit: int = 20) -> tuple[object, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("list limit is invalid")
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT body FROM v66_objects WHERE kind='operation' AND tenant_id=? AND project_id=? ORDER BY object_id LIMIT ?",
                (tenant_id, project_id, limit),
            ).fetchall()
        result: list[object] = []
        for row in rows:
            operation = self._operation_from_body(row["body"])
            if operation is not None:
                result.append(operation)
        return tuple(result)

    def get_proposal(self, tenant_id: str, project_id: str, proposal_id: str) -> ExecutionProposal | None:
        return self._get("proposal", tenant_id, project_id, proposal_id, ExecutionProposal)

    def save_target(self, value: ExecutionTarget) -> ExecutionTarget:
        return self._save("target", value, object_id=value.target_id)

    def save_candidate(self, value: TargetDiscoveryCandidate) -> TargetDiscoveryCandidate:
        return self._save("target_candidate", value, object_id=value.candidate_id)

    def get_candidate(self, tenant_id: str, project_id: str, candidate_id: str) -> TargetDiscoveryCandidate | None:
        return self._get("target_candidate", tenant_id, project_id, candidate_id, TargetDiscoveryCandidate)

    def save_artifact(self, value):
        return self._save("artifact", value, object_id=value.artifact_id)

    def get_artifact(self, tenant_id: str, project_id: str, artifact_id: str):
        from .contracts import ArtifactIdentity

        return self._get("artifact", tenant_id, project_id, artifact_id, ArtifactIdentity)

    def get_target(self, tenant_id: str, project_id: str, target_id: str) -> ExecutionTarget | None:
        return self._get("target", tenant_id, project_id, target_id, ExecutionTarget)

    def save_approval(self, value: OperationApprovalGrant) -> OperationApprovalGrant:
        return self._save("approval", value, object_id=value.grant_id)

    def get_approval(self, tenant_id: str, project_id: str, grant_id: str) -> OperationApprovalGrant | None:
        return self._get("approval", tenant_id, project_id, grant_id, OperationApprovalGrant)

    def consume_approval(self, *, tenant_id: str, project_id: str, grant_id: str, operation_fingerprint: str, now: datetime) -> OperationApprovalGrant | None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT body FROM v66_objects WHERE kind='approval' AND tenant_id=? AND project_id=? AND object_id=?",
                (tenant_id, project_id, grant_id),
            ).fetchone()
            value = self._load(row, OperationApprovalGrant)
            if value is None or value.operation_fingerprint != operation_fingerprint or value.status.value != "ISSUED" or now >= value.expires_at:
                connection.execute("ROLLBACK")
                return None
            material = value.model_dump(mode="python")
            material["status"] = ApprovalStatus.CONSUMED
            material["fingerprint"] = "sha256:" + "0" * 64
            consumed = OperationApprovalGrant.model_validate(material)
            connection.execute(
                "UPDATE v66_objects SET body=? WHERE kind='approval' AND tenant_id=? AND project_id=? AND object_id=?",
                (self._body(consumed), tenant_id, project_id, grant_id),
            )
            connection.execute("COMMIT")
            return consumed

    def save_status(self, value: OperationStatusView) -> OperationStatusView:
        return self._save("status", value, object_id=value.operation_id)

    def get_status(self, tenant_id: str, project_id: str, operation_id: str) -> OperationStatusView | None:
        return self._get("status", tenant_id, project_id, operation_id, OperationStatusView)

    def save_receipt(self, value: OperationReceipt) -> OperationReceipt:
        return self._save("receipt", value, object_id=value.operation_id)

    def get_receipt(self, tenant_id: str, project_id: str, operation_id: str) -> OperationReceipt | None:
        return self._get("receipt", tenant_id, project_id, operation_id, OperationReceipt)

    def save_cancellation(self, value: CancellationRequest) -> CancellationRequest:
        return self._save("cancellation", value, object_id=value.request_id)

    def save_recovery_decision(self, value: RecoveryDecision) -> RecoveryDecision:
        return self._save("recovery", value, object_id=value.decision_id)

    def enqueue(self, *, tenant_id: str, project_id: str, operation_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO v66_queue(tenant_id, project_id, operation_id, state, worker_id, lease_until) VALUES(?,?,?,?,NULL,NULL)",
                (tenant_id, project_id, operation_id, "QUEUED"),
            )

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        now: datetime,
        operation_types: tuple[OperationType, ...] | None = None,
    ) -> tuple[str, str, str] | None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT q.tenant_id, q.project_id, q.operation_id, o.body "
                "FROM v66_queue AS q JOIN v66_objects AS o "
                "ON o.kind='operation' AND o.tenant_id=q.tenant_id "
                "AND o.project_id=q.project_id AND o.object_id=q.operation_id "
                "WHERE q.state='QUEUED' ORDER BY q.operation_id"
            ).fetchall()
            allowed = None if operation_types is None else set(operation_types)
            row = next(
                (
                    item
                    for item in rows
                    if allowed is None
                    or getattr(
                        self._operation_from_body(item["body"]),
                        "operation_type",
                        None,
                    )
                    in allowed
                ),
                None,
            )
            if row is None:
                connection.execute("COMMIT")
                return None
            lease = (now + timedelta(seconds=lease_seconds)).isoformat()
            connection.execute(
                "UPDATE v66_queue SET state='CLAIMED', worker_id=?, lease_until=? WHERE tenant_id=? AND project_id=? AND operation_id=? AND state='QUEUED'",
                (worker_id, lease, row["tenant_id"], row["project_id"], row["operation_id"]),
            )
            status_row = connection.execute(
                "SELECT body FROM v66_objects WHERE kind='status' AND tenant_id=? AND project_id=? AND object_id=?",
                (row["tenant_id"], row["project_id"], row["operation_id"]),
            ).fetchone()
            operation = self.get_operation(
                row["tenant_id"], row["project_id"], row["operation_id"]
            )
            status = self._load(status_row, OperationStatusView)
            if operation is not None and status is not None:
                try:
                    self._append_event_in_transaction(
                        connection,
                        operation=operation,
                        event_type=OperationEventType.OPERATION_CLAIMED,
                        status=status.status,
                        recovery_state=status.recovery_state,
                        reason_code="WORKER_CLAIMED",
                        occurred_at=now,
                        approval_id=status.approval_id,
                    )
                except Exception:
                    connection.execute("ROLLBACK")
                    raise
            connection.execute("COMMIT")
            return row["tenant_id"], row["project_id"], row["operation_id"]

    def mark_queue(self, *, tenant_id: str, project_id: str, operation_id: str, state: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE v66_queue SET state=?, worker_id=NULL, lease_until=NULL WHERE tenant_id=? AND project_id=? AND operation_id=?",
                (state, tenant_id, project_id, operation_id),
            )

    def tombstone_project(self, tenant_id: str, project_id: str, tombstoned_at: datetime) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO v66_tombstones(tenant_id, project_id, tombstoned_at) VALUES(?,?,?)",
                (tenant_id, project_id, tombstoned_at.astimezone(UTC).isoformat()),
            )

    def is_project_tombstoned(self, tenant_id: str, project_id: str) -> bool:
        with self._lock, self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM v66_tombstones WHERE tenant_id=? AND project_id=?",
                (tenant_id, project_id),
            ).fetchone() is not None

    def recover_running(
        self,
        *,
        now: datetime,
        operation_types: tuple[OperationType, ...] | None = None,
        recovery_classify: Callable[[object], RecoveryState] | None = None,
    ) -> tuple[OperationStatusView, ...]:
        recovered: list[OperationStatusView] = []
        with self._lock, self._connect() as connection:
            rows = connection.execute("SELECT body FROM v66_objects WHERE kind='status'").fetchall()
        for row in rows:
            value = OperationStatusView.model_validate_json(row["body"])
            if operation_types is not None and value.operation_type not in operation_types:
                continue
            if value.status is not OperationStatus.RUNNING:
                continue
            operation = self.get_operation(value.tenant_id, value.project_id, value.operation_id)
            recovery = RecoveryState.TARGET_STATE_UNKNOWN if value.operation_type.value == "FLASH" else RecoveryState.INTERRUPTED
            if isinstance(operation, PcbDrcBaselineOperation) or (isinstance(operation, KiCadNativeFootprintMoveOperation) and operation.production_context is not None):
                try:
                    recovery = recovery_classify(operation) if recovery_classify is not None else RecoveryState.TARGET_STATE_UNKNOWN
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
                    recovery = RecoveryState.TARGET_STATE_UNKNOWN
            updated = OperationStatusView.model_validate({
                **value.model_dump(mode="python"),
                "status": OperationStatus.RECOVERY_REQUIRED,
                "recovery_state": recovery,
                "safe_detail": "worker interruption requires recovery decision",
                "updated_at": now,
                "correlation": (
                    self._correlation_for(operation, value.approval_id)
                    if operation is not None
                    else value.correlation
                ),
                "fingerprint": "sha256:" + "0" * 64,
            })
            if operation is None:
                self.save_status(updated)
                recovered.append(updated)
                continue
            with self._lock, self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    current_row = connection.execute(
                        "SELECT body FROM v66_objects WHERE kind='status' AND tenant_id=? AND project_id=? AND object_id=?",
                        (value.tenant_id, value.project_id, value.operation_id),
                    ).fetchone()
                    current = self._load(current_row, OperationStatusView)
                    if current != value or current.status is not OperationStatus.RUNNING:
                        connection.execute("ROLLBACK")
                        continue
                    connection.execute(
                        "UPDATE v66_objects SET body=? WHERE kind='status' AND tenant_id=? AND project_id=? AND object_id=? AND body=?",
                        (self._body(updated), value.tenant_id, value.project_id, value.operation_id, self._body(value)),
                    )
                    self._append_event_in_transaction(
                        connection,
                        operation=operation,
                        event_type=OperationEventType.RECOVERY_REQUIRED,
                        status=updated.status,
                        recovery_state=updated.recovery_state,
                        reason_code="WORKER_INTERRUPTED",
                        occurred_at=now,
                        approval_id=updated.approval_id,
                        receipt_fingerprint=updated.receipt_fingerprint,
                    )
                except Exception:
                    connection.execute("ROLLBACK")
                    raise
                connection.execute("COMMIT")
            recovered.append(updated)
        return tuple(recovered)


__all__ = ("SQLiteExecutionStore",)
