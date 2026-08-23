from __future__ import annotations

import json
import sqlite3
import threading
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
    FingerprintedContract,
    HilOperation,
    OperationEvent,
    OperationEventType,
    OperationApprovalGrant,
    OperationReceipt,
    OperationStatus,
    OperationStatusView,
    RecoveryDecision,
    RecoveryState,
    TestOperation,
    UartObservationOperation,
    TargetDiscoveryCandidate,
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

    def _get(self, kind: str, tenant_id: str, project_id: str, object_id: str, model: type[T]) -> T | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT body FROM v66_objects WHERE kind=? AND tenant_id=? AND project_id=? AND object_id=?",
                (kind, tenant_id, project_id, object_id),
            ).fetchone()
        return self._load(row, model)

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
        body = json.loads(row["body"])
        model = {
            "BUILD": BuildOperation,
            "TEST": TestOperation,
            "FLASH": FlashOperation,
            "UART_OBSERVATION": UartObservationOperation,
            "HIL": HilOperation,
        }.get(body.get("operation_type"))
        if model is None:
            return None
        return model.model_validate_json(row["body"])

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
            body = json.loads(row["body"])
            model = {
                "BUILD": BuildOperation,
                "TEST": TestOperation,
                "FLASH": FlashOperation,
                "UART_OBSERVATION": UartObservationOperation,
                "HIL": HilOperation,
            }.get(body.get("operation_type"))
            if model is not None:
                result.append(model.model_validate_json(row["body"]))
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

    def claim_next(self, *, worker_id: str, lease_seconds: int, now: datetime) -> tuple[str, str, str] | None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT tenant_id, project_id, operation_id FROM v66_queue WHERE state='QUEUED' ORDER BY operation_id LIMIT 1"
            ).fetchone()
            if row is None:
                connection.execute("COMMIT")
                return None
            lease = (now + timedelta(seconds=lease_seconds)).isoformat()
            connection.execute(
                "UPDATE v66_queue SET state='CLAIMED', worker_id=?, lease_until=? WHERE tenant_id=? AND project_id=? AND operation_id=? AND state='QUEUED'",
                (worker_id, lease, row["tenant_id"], row["project_id"], row["operation_id"]),
            )
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

    def recover_running(self, *, now: datetime) -> tuple[OperationStatusView, ...]:
        recovered: list[OperationStatusView] = []
        with self._lock, self._connect() as connection:
            rows = connection.execute("SELECT body FROM v66_objects WHERE kind='status'").fetchall()
        for row in rows:
            value = OperationStatusView.model_validate_json(row["body"])
            if value.status is not OperationStatus.RUNNING:
                continue
            recovery = RecoveryState.TARGET_STATE_UNKNOWN if value.operation_type.value == "FLASH" else RecoveryState.INTERRUPTED
            updated = OperationStatusView.model_validate({
                **value.model_dump(mode="python"),
                "status": OperationStatus.RECOVERY_REQUIRED,
                "recovery_state": recovery,
                "safe_detail": "worker interruption requires recovery decision",
                "updated_at": now,
                "fingerprint": "sha256:" + "0" * 64,
            })
            self.save_status(updated)
            recovered.append(updated)
        return tuple(recovered)


__all__ = ("SQLiteExecutionStore",)
