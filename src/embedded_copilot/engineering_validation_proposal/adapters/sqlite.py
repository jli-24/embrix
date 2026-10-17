from __future__ import annotations

import copy
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..exceptions import ProposalConflict, ProposalRejected
from ..models import (
    AuditEvent,
    EngineeringFinding,
    OperationProposal,
    ProposalReviewBinding,
    ValidationRequest,
    ValidationResult,
)


class SQLiteV64Store:
    """Production-bound v6.4 persistence using the existing SQLite state file."""

    _SCHEMA_VERSION = 2

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
            if version > self._SCHEMA_VERSION:
                raise RuntimeError("v6.4 schema is newer than this runtime")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS v63_tombstones (
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    tombstoned_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, project_id)
                );
                CREATE TABLE IF NOT EXISTS v64_validation_requests (
                    tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    validation_id TEXT NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, project_id, validation_id)
                );
                CREATE TABLE IF NOT EXISTS v64_validation_results (
                    tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    validation_id TEXT NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, project_id, validation_id)
                );
                CREATE TABLE IF NOT EXISTS v64_findings (
                    tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    finding_id TEXT NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, project_id, finding_id)
                );
                CREATE TABLE IF NOT EXISTS v64_proposals (
                    tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    proposal_id TEXT NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, project_id, proposal_id)
                );
                CREATE TABLE IF NOT EXISTS v64_proposal_reviews (
                    tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    review_id TEXT NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, project_id, review_id)
                );
                CREATE TABLE IF NOT EXISTS v64_status_history (
                    tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    history_id TEXT NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, project_id, history_id)
                );
                CREATE TABLE IF NOT EXISTS v64_audit (
                    tenant_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    event_id TEXT NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, project_id, event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_v64_proposals_status
                    ON v64_proposals(tenant_id, project_id);
                CREATE INDEX IF NOT EXISTS idx_v64_reviews_proposal
                    ON v64_proposal_reviews(tenant_id, project_id);
                """
            )
            if version < self._SCHEMA_VERSION:
                connection.execute(f"PRAGMA user_version = {self._SCHEMA_VERSION}")

    @staticmethod
    def _body(value: Any) -> str:
        return value.model_dump_json()

    @staticmethod
    def _load(body: str, model: type[Any]) -> Any:
        def restore(value: Any) -> Any:
            if isinstance(value, list):
                return tuple(restore(item) for item in value)
            if isinstance(value, dict):
                return {key: restore(item) for key, item in value.items()}
            return value

        return model.model_validate(restore(json.loads(body)))

    def is_project_tombstoned(self, tenant_id: str, project_id: str) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM v63_tombstones WHERE tenant_id=? AND project_id=?",
                (tenant_id, project_id),
            ).fetchone()
        return row is not None

    def tombstone_project(self, tenant_id: str, project_id: str, tombstoned_at: str) -> bool:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO v63_tombstones(tenant_id, project_id, tombstoned_at) VALUES(?,?,?) ON CONFLICT(tenant_id, project_id) DO NOTHING",
                (tenant_id, project_id, tombstoned_at),
            )
        return True

    def _save(self, table: str, tenant_id: str, project_id: str, object_id: str, value: Any) -> Any:
        if self.is_project_tombstoned(tenant_id, project_id):
            raise ProposalRejected("project is tombstoned")
        id_column = {
            "v64_validation_requests": "validation_id",
            "v64_validation_results": "validation_id",
            "v64_findings": "finding_id",
            "v64_proposals": "proposal_id",
            "v64_proposal_reviews": "review_id",
            "v64_status_history": "history_id",
            "v64_audit": "event_id",
        }[table]
        with self._lock, self._connect() as connection:
            row = connection.execute(
                f"SELECT body FROM {table} WHERE tenant_id=? AND project_id=? AND {id_column}=?",
                (tenant_id, project_id, object_id),
            ).fetchone()
            if row is not None and row[0] != self._body(value):
                # State transitions update the proposal/review row.  Immutable
                # request/finding/result/audit identities remain conflict-safe.
                mutable = table in {"v64_proposals", "v64_proposal_reviews"}
                if not mutable:
                    raise ProposalConflict("v6.4 identity conflict")
            connection.execute(
                f"INSERT INTO {table}(tenant_id, project_id, {id_column}, body) VALUES(?,?,?,?) "
                f"ON CONFLICT(tenant_id, project_id, {id_column}) DO UPDATE SET body=excluded.body",
                (tenant_id, project_id, object_id, self._body(value)),
            )
        return copy.deepcopy(value)

    def _get(self, table: str, tenant_id: str, project_id: str, object_id: str, model: type[Any]) -> Any | None:
        if self.is_project_tombstoned(tenant_id, project_id):
            return None
        id_column = {
            "v64_validation_requests": "validation_id",
            "v64_validation_results": "validation_id",
            "v64_findings": "finding_id",
            "v64_proposals": "proposal_id",
            "v64_proposal_reviews": "review_id",
        }[table]
        with self._lock, self._connect() as connection:
            row = connection.execute(
                f"SELECT body FROM {table} WHERE tenant_id=? AND project_id=? AND {id_column}=?",
                (tenant_id, project_id, object_id),
            ).fetchone()
        return None if row is None else self._load(row[0], model)

    def _list(self, table: str, tenant_id: str, project_id: str, model: type[Any], id_column: str, *, limit: int | None = None) -> tuple[Any, ...]:
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 100):
            raise ValueError("list limit is invalid")
        if self.is_project_tombstoned(tenant_id, project_id):
            return ()
        with self._lock, self._connect() as connection:
            statement = f"SELECT body FROM {table} WHERE tenant_id=? AND project_id=? ORDER BY {id_column}"
            parameters: tuple[object, ...] = (tenant_id, project_id)
            if limit is not None:
                statement += " LIMIT ?"
                parameters += (limit,)
            rows = connection.execute(statement, parameters).fetchall()
        return tuple(self._load(row[0], model) for row in rows)

    def save_request(self, request):
        return self._save("v64_validation_requests", request.tenant_id, request.project_id, request.validation_id, request)

    def get_request(self, tenant_id, project_id, validation_id):
        return self._get("v64_validation_requests", tenant_id, project_id, validation_id, ValidationRequest)

    def list_requests(self, tenant_id, project_id, *, limit=None):
        return self._list("v64_validation_requests", tenant_id, project_id, ValidationRequest, "validation_id", limit=limit)

    def save_finding(self, finding):
        return self._save("v64_findings", finding.tenant_id, finding.project_id, finding.finding_id, finding)

    def get_finding(self, tenant_id, project_id, finding_id):
        return self._get("v64_findings", tenant_id, project_id, finding_id, EngineeringFinding)

    def list_findings(self, tenant_id, project_id, *, limit=None):
        return self._list("v64_findings", tenant_id, project_id, EngineeringFinding, "finding_id", limit=limit)

    def save_result(self, result):
        return self._save("v64_validation_results", result.tenant_id, result.project_id, result.validation_id, result)

    def get_result(self, tenant_id, project_id, validation_id):
        return self._get("v64_validation_results", tenant_id, project_id, validation_id, ValidationResult)

    def save_proposal(self, proposal):
        return self._save("v64_proposals", proposal.tenant_id, proposal.project_id, proposal.proposal_id, proposal)

    def get_proposal(self, tenant_id, project_id, proposal_id):
        return self._get("v64_proposals", tenant_id, project_id, proposal_id, OperationProposal)

    def list_proposals(self, tenant_id, project_id, *, limit=None):
        return self._list("v64_proposals", tenant_id, project_id, OperationProposal, "proposal_id", limit=limit)

    def save_review(self, review):
        return self._save("v64_proposal_reviews", review.tenant_id, review.project_id, review.review_id, review)

    def get_review(self, tenant_id, project_id, review_id):
        return self._get("v64_proposal_reviews", tenant_id, project_id, review_id, ProposalReviewBinding)

    def list_reviews(self, tenant_id, project_id, *, limit=None):
        return self._list("v64_proposal_reviews", tenant_id, project_id, ProposalReviewBinding, "review_id", limit=limit)

    def append_history(self, history):
        return self._save("v64_status_history", history.tenant_id, history.project_id, history.history_id, history)

    def append_audit(self, event: AuditEvent):
        return self._save("v64_audit", event.tenant_id, event.project_id, event.event_id, event)


__all__ = ("SQLiteV64Store",)
