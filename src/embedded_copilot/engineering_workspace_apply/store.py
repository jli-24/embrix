from __future__ import annotations

import copy
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from .exceptions import ApplyConflict, ApplyRejected
from .models import (
    ApplyApprovalGrant,
    ApplyReceipt,
    ChangePreview,
    ChangeSet,
    OperationView,
    RecoveryReceipt,
    WorkspaceAuditEvent,
)


class SQLiteWorkspaceApplyStore:
    """Durable v6.5 state. Raw recovery contents never enter SQLite."""

    SCHEMA_VERSION = 3

    def __init__(self, database: str | Path) -> None:
        self.database = str(database)
        self._lock = threading.RLock()
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database, timeout=5, isolation_level="DEFERRED")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > self.SCHEMA_VERSION:
                raise RuntimeError("v6.5 schema is newer than this runtime")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS v63_tombstones (
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    tombstoned_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, project_id)
                );
                CREATE TABLE IF NOT EXISTS v65_changesets (tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, changeset_id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY (tenant_id, project_id, changeset_id));
                CREATE TABLE IF NOT EXISTS v65_previews (project_id TEXT NOT NULL, changeset_id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY (project_id, changeset_id));
                CREATE TABLE IF NOT EXISTS v65_approvals (tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, grant_id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY (tenant_id, project_id, grant_id));
                CREATE TABLE IF NOT EXISTS v65_operations (project_id TEXT NOT NULL, operation_id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY (project_id, operation_id));
                CREATE TABLE IF NOT EXISTS v65_receipts (project_id TEXT NOT NULL, operation_id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY (project_id, operation_id));
                CREATE TABLE IF NOT EXISTS v65_recovery (project_id TEXT NOT NULL, recovery_id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY (project_id, recovery_id));
                CREATE TABLE IF NOT EXISTS v65_audit (tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, event_id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY (tenant_id, project_id, event_id));
                CREATE TABLE IF NOT EXISTS v65_status_history (tenant_id TEXT NOT NULL, project_id TEXT NOT NULL, history_id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY (tenant_id, project_id, history_id));
                CREATE TABLE IF NOT EXISTS v65_eda_validation (project_id TEXT NOT NULL, validation_id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY (project_id, validation_id));
                """
            )
            if version < self.SCHEMA_VERSION:
                connection.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")

    @staticmethod
    def _body(value: Any) -> str:
        return value.model_dump_json()

    @staticmethod
    def _load(body: str, model: type[Any]) -> Any:
        def restore(value: Any, key: str | None = None) -> Any:
            if isinstance(value, dict):
                return {item_key: restore(item_value, item_key) for item_key, item_value in value.items()}
            if isinstance(value, list):
                return [restore(item, key) for item in value]
            if isinstance(value, str) and key is not None and key.endswith("_at"):
                try:
                    return datetime.fromisoformat(value)
                except ValueError:
                    return value
            return value
        return model.model_validate(restore(json.loads(body)))

    def is_project_tombstoned(self, tenant_id: str, project_id: str) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT 1 FROM v63_tombstones WHERE tenant_id=? AND project_id=?", (tenant_id, project_id)).fetchone()
        return row is not None

    def tombstone_project(self, tenant_id: str, project_id: str, tombstoned_at: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("INSERT OR IGNORE INTO v63_tombstones(tenant_id, project_id, tombstoned_at) VALUES(?,?,?)", (tenant_id, project_id, tombstoned_at))

    def _save(self, table: str, key_columns: tuple[str, ...], key_values: tuple[str, ...], value: Any) -> Any:
        tenant_id = getattr(value, "tenant_id", None)
        project_id = getattr(value, "project_id", None)
        if isinstance(tenant_id, str) and isinstance(project_id, str) and self.is_project_tombstoned(tenant_id, project_id):
            raise ApplyRejected("project is tombstoned")
        body = self._body(value)
        columns = ",".join(key_columns)
        placeholders = ",".join("?" for _ in key_columns)
        updates = ",".join(f"{column}=excluded.{column}" for column in ("body",))
        with self._lock, self._connect() as connection:
            row = connection.execute(f"SELECT body FROM {table} WHERE " + " AND ".join(f"{column}=?" for column in key_columns), key_values).fetchone()
            if row is not None and table in {"v65_audit", "v65_recovery"} and row[0] != body:
                raise ApplyConflict("immutable v6.5 identity conflict")
            connection.execute(f"INSERT INTO {table}({columns},body) VALUES({placeholders},?) ON CONFLICT DO UPDATE SET {updates}", (*key_values, body))
        return copy.deepcopy(value)

    def save_changeset(self, value: ChangeSet) -> ChangeSet:
        if not value.proposal_id.startswith("native-move-candidate-"):
            return self._save("v65_changesets", ("tenant_id", "project_id", "changeset_id"), (value.tenant_id, value.project_id, value.changeset_id), value)
        # The reviewed native candidate has one immutable changeset identity. A retry
        # may have a later clock, but must never replace its first canonical record.
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT body FROM v65_changesets WHERE tenant_id=? AND project_id=? AND changeset_id=?",
                (value.tenant_id, value.project_id, value.changeset_id),
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO v65_changesets(tenant_id,project_id,changeset_id,body) VALUES(?,?,?,?)",
                    (value.tenant_id, value.project_id, value.changeset_id, self._body(value)),
                )
                return copy.deepcopy(value)
            existing = ChangeSet.model_validate_json(row[0])
            fields = (
                "tenant_id", "project_id", "requester_id", "proposal_id", "proposal_fingerprint",
                "target", "target_fingerprint", "base_project_fingerprint",
                "validation_fingerprint", "evidence_fingerprints", "staging_fingerprint", "entries",
            )
            if any(getattr(existing, field) != getattr(value, field) for field in fields):
                raise ApplyConflict("native changeset immutable identity conflict")
            return copy.deepcopy(existing)

    def get_changeset(self, tenant_id: str, project_id: str, changeset_id: str) -> ChangeSet | None:
        return self._get("v65_changesets", ("tenant_id", "project_id", "changeset_id"), (tenant_id, project_id, changeset_id), ChangeSet)

    def list_changesets(self, tenant_id: str, project_id: str, *, limit: int = 20) -> tuple[ChangeSet, ...]:
        return self._list("v65_changesets", ("tenant_id", "project_id"), (tenant_id, project_id), ChangeSet, limit=limit)

    def save_preview(self, value: ChangePreview) -> ChangePreview:
        return self._save("v65_previews", ("project_id", "changeset_id"), (value.project_id, value.changeset_id), value)

    def get_preview(self, project_id: str, changeset_id: str) -> ChangePreview | None:
        return self._get("v65_previews", ("project_id", "changeset_id"), (project_id, changeset_id), ChangePreview)

    def save_approval(self, value: ApplyApprovalGrant) -> ApplyApprovalGrant:
        return self._save("v65_approvals", ("tenant_id", "project_id", "grant_id"), (value.tenant_id, value.project_id, value.grant_id), value)

    def get_approval(self, tenant_id: str, project_id: str, grant_id: str) -> ApplyApprovalGrant | None:
        return self._get("v65_approvals", ("tenant_id", "project_id", "grant_id"), (tenant_id, project_id, grant_id), ApplyApprovalGrant)

    def consume_approval(self, tenant_id: str, project_id: str, grant_id: str, *, now: str) -> ApplyApprovalGrant | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT body FROM v65_approvals WHERE tenant_id=? AND project_id=? AND grant_id=?", (tenant_id, project_id, grant_id)).fetchone()
            if row is None:
                return None
            value = self._load(row[0], ApplyApprovalGrant)
            from datetime import datetime
            current = datetime.fromisoformat(now)
            if value.status.value != "ISSUED" or current >= value.expires_at:
                return None
            material = value.model_dump(mode="python")
            material.pop("fingerprint", None)
            material["status"] = "CONSUMED"
            consumed = ApplyApprovalGrant.model_validate(material)
            connection.execute("UPDATE v65_approvals SET body=? WHERE tenant_id=? AND project_id=? AND grant_id=?", (self._body(consumed), tenant_id, project_id, grant_id))
            return consumed

    def save_operation(self, value: OperationView) -> OperationView:
        return self._save("v65_operations", ("project_id", "operation_id"), (value.project_id, value.operation_id), value)

    def get_operation(self, project_id: str, operation_id: str) -> OperationView | None:
        return self._get("v65_operations", ("project_id", "operation_id"), (project_id, operation_id), OperationView)

    def save_receipt(self, value: ApplyReceipt) -> ApplyReceipt:
        return self._save("v65_receipts", ("project_id", "operation_id"), (value.project_id, value.operation_id), value)

    def get_receipt(self, project_id: str, operation_id: str) -> ApplyReceipt | None:
        return self._get("v65_receipts", ("project_id", "operation_id"), (project_id, operation_id), ApplyReceipt)

    def list_receipts(self, project_id: str, *, limit: int = 20) -> tuple[ApplyReceipt, ...]:
        return self._list("v65_receipts", ("project_id",), (project_id,), ApplyReceipt, limit=limit)

    def list_audit(self, tenant_id: str, project_id: str, *, limit: int = 20) -> tuple[WorkspaceAuditEvent, ...]:
        return self._list("v65_audit", ("tenant_id", "project_id"), (tenant_id, project_id), WorkspaceAuditEvent, limit=limit)

    def save_recovery(self, value: RecoveryReceipt) -> RecoveryReceipt:
        return self._save("v65_recovery", ("project_id", "recovery_id"), (value.project_id, value.recovery_id), value)

    def get_recovery(self, project_id: str, recovery_id: str) -> RecoveryReceipt | None:
        return self._get("v65_recovery", ("project_id", "recovery_id"), (project_id, recovery_id), RecoveryReceipt)

    def append_audit(self, value: WorkspaceAuditEvent) -> WorkspaceAuditEvent:
        return self._save("v65_audit", ("tenant_id", "project_id", "event_id"), (value.tenant_id, value.project_id, value.event_id), value)

    def _get(self, table: str, key_columns: tuple[str, ...], key_values: tuple[str, ...], model: type[Any]) -> Any | None:
        if self.is_project_tombstoned(key_values[0], key_values[1]) if len(key_values) >= 2 and table not in {"v65_operations", "v65_receipts", "v65_recovery", "v65_previews"} else False:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT body FROM " + table + " WHERE " + " AND ".join(f"{column}=?" for column in key_columns), key_values).fetchone()
        return None if row is None else self._load(row[0], model)

    def _list(self, table: str, key_columns: tuple[str, ...], key_values: tuple[str, ...], model: type[Any], *, limit: int) -> tuple[Any, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("list limit is invalid")
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT body FROM " + table + " WHERE " + " AND ".join(f"{column}=?" for column in key_columns) + " LIMIT ?",
                (*key_values, limit),
            ).fetchall()
        return tuple(self._load(row[0], model) for row in rows)


__all__ = ("SQLiteWorkspaceApplyStore",)
