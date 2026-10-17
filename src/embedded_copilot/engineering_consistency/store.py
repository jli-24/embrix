from __future__ import annotations

import copy
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from .contracts import (
    ConsistencyCheckStatus,
    ConsistencyOverallStatus,
    HardwareFirmwareConsistencyResult,
    VerificationLevel,
)


def _restore_json(value: object) -> object:
    if isinstance(value, list):
        return tuple(_restore_json(item) for item in value)
    if isinstance(value, dict):
        return {key: _restore_json(item) for key, item in value.items()}
    return value


class ConsistencyStoreConflict(RuntimeError):
    """The same immutable result identity was presented with different data."""


class SQLiteConsistencyStore:
    """Persistent, immutable result store for read-only consistency projections."""

    _SCHEMA_VERSION = 1

    def __init__(self, database: str | Path) -> None:
        self.database = str(database)
        self._lock = threading.RLock()
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > self._SCHEMA_VERSION:
                raise RuntimeError("consistency schema is newer than this runtime")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS engineering_consistency_results (
                    project_id TEXT NOT NULL,
                    result_id TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    body TEXT NOT NULL,
                    stored_at TEXT NOT NULL,
                    PRIMARY KEY (project_id, result_id)
                );
                CREATE INDEX IF NOT EXISTS idx_consistency_latest
                    ON engineering_consistency_results(project_id, stored_at DESC);
                """
            )
            if version < self._SCHEMA_VERSION:
                connection.execute(f"PRAGMA user_version = {self._SCHEMA_VERSION}")

    @staticmethod
    def _load(body: str) -> HardwareFirmwareConsistencyResult:
        payload = _restore_json(json.loads(body))
        if not isinstance(payload, dict):
            raise TypeError("consistency result body is invalid")
        for timestamp_field in ("computed_at", "created_at"):
            timestamp = payload.get(timestamp_field)
            if not isinstance(timestamp, str):
                continue
            parsed = datetime.fromisoformat(timestamp)
            payload[timestamp_field] = (
                parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
            )
        if isinstance(payload.get("overall_status"), str):
            payload["overall_status"] = ConsistencyOverallStatus(
                payload["overall_status"]
            )
        if isinstance(payload.get("verification_level"), str):
            payload["verification_level"] = VerificationLevel(
                payload["verification_level"]
            )
        for field in ("artifact_integrity", "vector_table_status", "swd_mapping_status"):
            if isinstance(payload.get(field), str):
                payload[field] = ConsistencyCheckStatus(payload[field])
        for check in (*payload.get("checks", ()),):
            if isinstance(check, dict) and isinstance(check.get("status"), str):
                check["status"] = ConsistencyCheckStatus(check["status"])
        evidence = payload.get("evidence")
        if isinstance(evidence, dict):
            for field in ("artifact_integrity", "vector_table_status", "swd_mapping_status"):
                if isinstance(evidence.get(field), str):
                    evidence[field] = ConsistencyCheckStatus(evidence[field])
            for check in (*evidence.get("checks", ()),):
                if isinstance(check, dict) and isinstance(check.get("status"), str):
                    check["status"] = ConsistencyCheckStatus(check["status"])
        return HardwareFirmwareConsistencyResult.model_validate(payload)

    def save(
        self, result: HardwareFirmwareConsistencyResult
    ) -> HardwareFirmwareConsistencyResult:
        checked = HardwareFirmwareConsistencyResult.model_validate(
            copy.deepcopy(result.model_dump(mode="python"))
        )
        body = checked.model_dump_json()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT body FROM engineering_consistency_results WHERE project_id=? AND result_id=?",
                (checked.project_id, checked.result_id),
            ).fetchone()
            if row is not None and row[0] != body:
                raise ConsistencyStoreConflict("consistency result identity conflict")
            connection.execute(
                "INSERT INTO engineering_consistency_results(project_id,result_id,request_fingerprint,body,stored_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(project_id,result_id) DO NOTHING",
                (
                    checked.project_id,
                    checked.result_id,
                    checked.request_fingerprint,
                    body,
                    datetime.now(UTC).isoformat(),
                ),
            )
        return copy.deepcopy(checked)

    def get(
        self, project_id: str, result_id: str
    ) -> HardwareFirmwareConsistencyResult | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT body FROM engineering_consistency_results WHERE project_id=? AND result_id=?",
                (project_id, result_id),
            ).fetchone()
        return None if row is None else self._load(row[0])

    def get_latest(self, project_id: str) -> HardwareFirmwareConsistencyResult | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT body FROM engineering_consistency_results WHERE project_id=? ORDER BY stored_at DESC, result_id DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        return None if row is None else self._load(row[0])

    def get_current(
        self, project_id: str, request_fingerprint: str
    ) -> HardwareFirmwareConsistencyResult | None:
        """Return a cached result only when its complete input fingerprint is current."""

        result = self.get_latest(project_id)
        if result is None or result.request_fingerprint != request_fingerprint:
            return None
        return result


__all__ = ("ConsistencyStoreConflict", "SQLiteConsistencyStore")
