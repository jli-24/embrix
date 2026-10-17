from __future__ import annotations

import copy
import json
import sqlite3
import threading
from datetime import UTC, datetime

from ..contracts import (
    validate_human_decision,
    validate_review_id,
    validate_review_record,
    validate_review_request,
)
from ..exceptions import ReviewConflict
from ..models import (
    HumanDecision,
    ReviewDecision,
    ReviewRecord,
    ReviewRequest,
    ReviewStatus,
    ReviewTargetType,
    identifier,
)


def _db_call(connection: sqlite3.Connection, statement: str, parameters: tuple[object, ...] = ()):
    return getattr(connection, "execute")(statement, parameters)


def _db_script(connection: sqlite3.Connection, script: str):
    return getattr(connection, "executescript")(script)


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("timestamp is invalid")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _request_from_json(value: str) -> ReviewRequest:
    payload = json.loads(value)
    payload["target_reference"]["target_type"] = ReviewTargetType(payload["target_reference"]["target_type"])
    payload["created_at"] = _timestamp(payload["created_at"])
    return validate_review_request(ReviewRequest.model_validate(payload))


def _decision_from_json(value: str) -> HumanDecision:
    payload = json.loads(value)
    payload["decision"] = ReviewDecision(payload["decision"])
    payload["decided_at"] = _timestamp(payload["decided_at"])
    return validate_human_decision(HumanDecision.model_validate(payload))


def _record_from_json(value: str) -> ReviewRecord:
    payload = json.loads(value)
    payload["review_request"] = _request_from_json(json.dumps(payload["review_request"]))
    payload["decision"] = _decision_from_json(json.dumps(payload["decision"]))
    payload["status"] = ReviewStatus(payload["status"])
    return validate_review_record(ReviewRecord.model_validate(payload))


def _copy(value: object, model: type) -> object:
    return model.model_validate(copy.deepcopy(value.model_dump(mode="python")))


class _SQLiteReviewBase:
    __slots__ = ("_database", "_lock", "_tenant_id")
    _SCHEMA_VERSION = 1

    def __init__(self, database: object, *, tenant_id: str) -> None:
        self._database = str(database)
        self._tenant_id = identifier(tenant_id, field="tenant_id")
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=5)
        _db_call(connection, "PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            version = int(_db_call(connection, "PRAGMA user_version").fetchone()[0])
            if version > self._SCHEMA_VERSION:
                raise RuntimeError("engineering review schema is newer than this runtime")
            _db_script(
                connection,
                """
                CREATE TABLE IF NOT EXISTS engineering_review_requests (
                    tenant_id TEXT NOT NULL,
                    review_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, review_id)
                );
                CREATE TABLE IF NOT EXISTS engineering_review_decisions (
                    tenant_id TEXT NOT NULL,
                    review_id TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, review_id),
                    UNIQUE (tenant_id, decision_id)
                );
                CREATE TABLE IF NOT EXISTS engineering_review_records (
                    tenant_id TEXT NOT NULL,
                    review_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, review_id)
                );
                CREATE INDEX IF NOT EXISTS idx_engineering_review_decisions_id
                    ON engineering_review_decisions(tenant_id, decision_id);
                """
            )
            if version < self._SCHEMA_VERSION:
                _db_call(connection, f"PRAGMA user_version = {self._SCHEMA_VERSION}")


class SQLiteReviewStore(_SQLiteReviewBase):
    """Tenant-bound persistent ReviewStore; raw prompts and provider payloads are not accepted."""

    def save_review_request(self, request: ReviewRequest) -> ReviewRequest:
        checked = validate_review_request(request)
        with self._lock, self._connect() as connection:
            row = _db_call(connection,
                "SELECT body FROM engineering_review_requests WHERE tenant_id = ? AND review_id = ?",
                (self._tenant_id, checked.review_id),
            ).fetchone()
            if row is not None and _request_from_json(row[0]) != checked:
                raise ReviewConflict()
            _db_call(connection,
                "INSERT INTO engineering_review_requests(tenant_id, review_id, body) VALUES(?, ?, ?) "
                "ON CONFLICT(tenant_id, review_id) DO UPDATE SET body=excluded.body",
                (self._tenant_id, checked.review_id, checked.model_dump_json()),
            )
        return _copy(checked, ReviewRequest)

    def get_review_request(self, review_id: str) -> ReviewRequest | None:
        checked_id = validate_review_id(review_id)
        with self._lock, self._connect() as connection:
            row = _db_call(connection,
                "SELECT body FROM engineering_review_requests WHERE tenant_id = ? AND review_id = ?",
                (self._tenant_id, checked_id),
            ).fetchone()
        return None if row is None else _request_from_json(row[0])

    def save_decision(self, decision: HumanDecision) -> HumanDecision:
        checked = validate_human_decision(decision)
        with self._lock, self._connect() as connection:
            request = _db_call(connection,
                "SELECT body FROM engineering_review_requests WHERE tenant_id = ? AND review_id = ?",
                (self._tenant_id, checked.review_id),
            ).fetchone()
            existing = _db_call(connection,
                "SELECT body FROM engineering_review_decisions WHERE tenant_id = ? AND review_id = ?",
                (self._tenant_id, checked.review_id),
            ).fetchone()
            same_decision_id = _db_call(connection,
                "SELECT review_id FROM engineering_review_decisions WHERE tenant_id = ? AND decision_id = ?",
                (self._tenant_id, checked.decision_id),
            ).fetchone()
            if (
                request is None
                or existing is not None
                and _decision_from_json(existing[0]) != checked
                or same_decision_id is not None
                and same_decision_id[0] != checked.review_id
            ):
                raise ReviewConflict()
            _db_call(connection,
                "INSERT INTO engineering_review_decisions(tenant_id, review_id, decision_id, body) VALUES(?, ?, ?, ?) "
                "ON CONFLICT(tenant_id, review_id) DO UPDATE SET decision_id=excluded.decision_id, body=excluded.body",
                (self._tenant_id, checked.review_id, checked.decision_id, checked.model_dump_json()),
            )
        return _copy(checked, HumanDecision)

    def get_decision(self, review_id: str) -> HumanDecision | None:
        checked_id = validate_review_id(review_id)
        with self._lock, self._connect() as connection:
            row = _db_call(connection,
                "SELECT body FROM engineering_review_decisions WHERE tenant_id = ? AND review_id = ?",
                (self._tenant_id, checked_id),
            ).fetchone()
        return None if row is None else _decision_from_json(row[0])

    def save_record(self, record: ReviewRecord) -> ReviewRecord:
        checked = validate_review_record(record)
        review_id = checked.review_request.review_id
        with self._lock, self._connect() as connection:
            request = _db_call(connection,
                "SELECT body FROM engineering_review_requests WHERE tenant_id = ? AND review_id = ?",
                (self._tenant_id, review_id),
            ).fetchone()
            decision = _db_call(connection,
                "SELECT body FROM engineering_review_decisions WHERE tenant_id = ? AND review_id = ?",
                (self._tenant_id, review_id),
            ).fetchone()
            existing = _db_call(connection,
                "SELECT body FROM engineering_review_records WHERE tenant_id = ? AND review_id = ?",
                (self._tenant_id, review_id),
            ).fetchone()
            if (
                request is None
                or decision is None
                or _request_from_json(request[0]) != checked.review_request
                or _decision_from_json(decision[0]) != checked.decision
                or existing is not None
                and _record_from_json(existing[0]) != checked
            ):
                raise ReviewConflict()
            _db_call(connection,
                "INSERT INTO engineering_review_records(tenant_id, review_id, body) VALUES(?, ?, ?) "
                "ON CONFLICT(tenant_id, review_id) DO UPDATE SET body=excluded.body",
                (self._tenant_id, review_id, checked.model_dump_json()),
            )
        return _copy(checked, ReviewRecord)

    def get_record(self, review_id: str) -> ReviewRecord | None:
        checked_id = validate_review_id(review_id)
        with self._lock, self._connect() as connection:
            row = _db_call(connection,
                "SELECT body FROM engineering_review_records WHERE tenant_id = ? AND review_id = ?",
                (self._tenant_id, checked_id),
            ).fetchone()
        return None if row is None else _record_from_json(row[0])


class SQLiteDecisionAuditSink(_SQLiteReviewBase):
    """Persistent audit projection sharing the tenant-bound review database."""

    def record_decision(self, record: ReviewRecord) -> ReviewRecord:
        checked = validate_review_record(record)
        review_id = checked.review_request.review_id
        with self._lock, self._connect() as connection:
            row = _db_call(connection,
                "SELECT body FROM engineering_review_records WHERE tenant_id = ? AND review_id = ?",
                (self._tenant_id, review_id),
            ).fetchone()
            if row is not None and _record_from_json(row[0]) != checked:
                raise ReviewConflict()
            _db_call(connection,
                "INSERT INTO engineering_review_records(tenant_id, review_id, body) VALUES(?, ?, ?) "
                "ON CONFLICT(tenant_id, review_id) DO UPDATE SET body=excluded.body",
                (self._tenant_id, review_id, checked.model_dump_json()),
            )
        return _copy(checked, ReviewRecord)

    def get_record(self, review_id: str) -> ReviewRecord | None:
        checked_id = validate_review_id(review_id)
        with self._lock, self._connect() as connection:
            row = _db_call(connection,
                "SELECT body FROM engineering_review_records WHERE tenant_id = ? AND review_id = ?",
                (self._tenant_id, checked_id),
            ).fetchone()
        return None if row is None else _record_from_json(row[0])


__all__ = ("SQLiteDecisionAuditSink", "SQLiteReviewStore")
