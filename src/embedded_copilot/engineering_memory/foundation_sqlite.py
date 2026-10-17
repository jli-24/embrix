from __future__ import annotations

import copy
import json
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .exceptions import MemoryOperationConflict
from .foundation import (
    DebugMemoryPayload,
    DecisionMemoryPayload,
    EvidenceMemoryPayload,
    FoundationEngineeringMemoryRecord,
    FoundationMemoryQuery,
    FoundationMemoryType,
    ProjectMemoryPayload,
)

FOUNDATION_SCHEMA_VERSION = 1
_TABLE = "engineering_memory_foundation"
_META_TABLE = "engineering_memory_foundation_meta"
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{1,63}")


class FoundationMemoryPersistenceError(RuntimeError):
    """Stable fail-closed error for malformed or corrupted persisted rows."""


class FoundationMemoryMigrationError(RuntimeError):
    """Stable fail-closed error for an unsupported or incomplete migration."""


def _payload_type(memory_type: FoundationMemoryType) -> type[Any]:
    if memory_type is FoundationMemoryType.PROJECT:
        return ProjectMemoryPayload
    if memory_type is FoundationMemoryType.DECISION:
        return DecisionMemoryPayload
    if memory_type is FoundationMemoryType.DEBUG:
        return DebugMemoryPayload
    return EvidenceMemoryPayload


def _path(value: str | Path) -> Path:
    if not isinstance(value, (str, Path)):
        raise TypeError("foundation persistence path is invalid")
    checked = Path(value)
    if not checked.name:
        raise ValueError("foundation persistence path is invalid")
    return checked


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _tokens(value: str) -> frozenset[str]:
    return frozenset(item.casefold() for item in _TOKEN.findall(value))


def _search_text(record: FoundationEngineeringMemoryRecord) -> str:
    return (
        f"{record.memory_id} {record.memory_type.value} "
        f"{_canonical(record.payload.model_dump(mode='json'))}"
    )


def _checked_record(value: object) -> FoundationEngineeringMemoryRecord:
    if type(value) is not FoundationEngineeringMemoryRecord:
        raise TypeError("foundation memory record is invalid")
    return FoundationEngineeringMemoryRecord.model_validate(
        copy.deepcopy(value.model_dump(mode="python"))
    )


def _checked_query(value: object) -> FoundationMemoryQuery:
    if type(value) is not FoundationMemoryQuery:
        raise TypeError("foundation memory query is invalid")
    return FoundationMemoryQuery.model_validate(
        copy.deepcopy(value.model_dump(mode="python"))
    )


def _created_at_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _record_row(record: FoundationEngineeringMemoryRecord) -> tuple[object, ...]:
    checked = _checked_record(record)
    return (
        checked.memory_id,
        checked.tenant_id,
        checked.project_id,
        checked.memory_type.value,
        _canonical(checked.payload.model_dump(mode="json")),
        _created_at_text(checked.created_at),
        _canonical(list(checked.source_references)),
        checked.status,
        checked.fingerprint,
    )


def _load_row(row: tuple[Any, ...]) -> FoundationEngineeringMemoryRecord:
    try:
        (
            memory_id,
            tenant_id,
            project_id,
            memory_type_value,
            payload_json,
            created_at_value,
            source_references_json,
            status,
            fingerprint,
        ) = row
        memory_type = FoundationMemoryType(memory_type_value)
        payload_data = json.loads(payload_json)
        if _canonical(payload_data) != payload_json:
            raise ValueError("payload JSON is not canonical")
        if memory_type is FoundationMemoryType.DECISION:
            payload_data["timestamp"] = datetime.fromisoformat(
                str(payload_data["timestamp"])
            )
        payload = _payload_type(memory_type).model_validate(payload_data)
        source_references_data = json.loads(source_references_json)
        if type(source_references_data) is not list:
            raise ValueError("source references are invalid")
        if any(type(item) is not str for item in source_references_data):
            raise ValueError("source references are invalid")
        normalized_references = sorted(set(source_references_data))
        if source_references_data != normalized_references:
            raise ValueError("source references are not canonical")
        created_at = datetime.fromisoformat(str(created_at_value))
        record = FoundationEngineeringMemoryRecord.model_validate(
            {
                "memory_id": memory_id,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "memory_type": memory_type,
                "payload": payload,
                "created_at": created_at,
                "source_references": tuple(source_references_data),
                "status": status,
                "fingerprint": fingerprint,
            }
        )
        if _created_at_text(record.created_at) != created_at_value:
            raise ValueError("created_at is not canonical")
        return record
    except FoundationMemoryPersistenceError:
        raise
    except (KeyError, TypeError, ValueError, ValidationError, json.JSONDecodeError) as error:
        raise FoundationMemoryPersistenceError(
            "persisted foundation memory integrity check failed"
        ) from error


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_META_TABLE} (
            singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
            schema_version INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            memory_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            memory_type TEXT NOT NULL CHECK(memory_type IN ('PROJECT', 'DECISION', 'DEBUG', 'EVIDENCE')),
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            source_references_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status = 'CANDIDATE'),
            fingerprint TEXT NOT NULL,
            PRIMARY KEY (tenant_id, project_id, memory_id)
        )
        """
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_scope "
        f"ON {_TABLE}(tenant_id, project_id)"
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_scope_type "
        f"ON {_TABLE}(tenant_id, project_id, memory_type)"
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_created_at "
        f"ON {_TABLE}(created_at)"
    )


def _validate_schema(connection: sqlite3.Connection) -> None:
    columns = {
        row[1]: row
        for row in connection.execute(f"PRAGMA table_info({_TABLE})").fetchall()
    }
    required = {
        "memory_id",
        "tenant_id",
        "project_id",
        "memory_type",
        "payload_json",
        "created_at",
        "source_references_json",
        "status",
        "fingerprint",
    }
    if set(columns) != required:
        raise FoundationMemoryMigrationError("foundation schema columns are invalid")
    primary_key = {
        name
        for name, row in columns.items()
        if row[5] > 0
    }
    if primary_key != {"tenant_id", "project_id", "memory_id"}:
        raise FoundationMemoryMigrationError("foundation schema identity is invalid")
    meta_columns = {
        row[1]
        for row in connection.execute(f"PRAGMA table_info({_META_TABLE})").fetchall()
    }
    if meta_columns != {"singleton", "schema_version"}:
        raise FoundationMemoryMigrationError("foundation schema metadata is invalid")


def migrate_foundation_schema(database: str | Path) -> int:
    try:
        target = _path(database)
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(target), timeout=5, isolation_level=None) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("BEGIN IMMEDIATE")
            _create_schema(connection)
            _validate_schema(connection)
            row = connection.execute(
                f"SELECT schema_version FROM {_META_TABLE} WHERE singleton = 1"
            ).fetchone()
            if row is None:
                connection.execute(
                    f"INSERT INTO {_META_TABLE}(singleton, schema_version) VALUES (1, ?)",
                    (FOUNDATION_SCHEMA_VERSION,),
                )
            elif type(row[0]) is not int or row[0] > FOUNDATION_SCHEMA_VERSION:
                raise FoundationMemoryMigrationError(
                    "foundation schema is newer than this runtime"
                )
            elif row[0] < FOUNDATION_SCHEMA_VERSION:
                connection.execute(
                    f"UPDATE {_META_TABLE} SET schema_version = ? WHERE singleton = 1",
                    (FOUNDATION_SCHEMA_VERSION,),
                )
            connection.commit()
    except FoundationMemoryMigrationError:
        raise
    except (OSError, sqlite3.Error, TypeError, ValueError) as error:
        raise FoundationMemoryMigrationError(
            "foundation schema migration failed"
        ) from error
    return FOUNDATION_SCHEMA_VERSION


initialize_foundation_schema = migrate_foundation_schema


class SQLiteEngineeringMemoryRepository:
    """Persistent implementation of the narrow Foundation repository Port."""

    __slots__ = ("_database", "_lock")

    def __init__(self, database: str | Path) -> None:
        self._database = _path(database)
        self._lock = threading.RLock()
        migrate_foundation_schema(self._database)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._database), timeout=5)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def save(
        self, record: FoundationEngineeringMemoryRecord
    ) -> FoundationEngineeringMemoryRecord:
        checked = _checked_record(record)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"SELECT memory_id, tenant_id, project_id, memory_type, payload_json, "
                f"created_at, source_references_json, status, fingerprint FROM {_TABLE} "
                "WHERE tenant_id = ? AND project_id = ? AND memory_id = ?",
                checked.identity,
            ).fetchone()
            if row is not None:
                existing = _load_row(row)
                if existing.fingerprint != checked.fingerprint:
                    raise MemoryOperationConflict()
                return copy.deepcopy(existing)
            connection.execute(
                f"INSERT INTO {_TABLE} (memory_id, tenant_id, project_id, memory_type, "
                "payload_json, created_at, source_references_json, status, fingerprint) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _record_row(checked),
            )
        return copy.deepcopy(checked)

    def search(
        self, query: FoundationMemoryQuery
    ) -> tuple[FoundationEngineeringMemoryRecord, ...]:
        checked = _checked_query(query)
        statement = (
            f"SELECT memory_id, tenant_id, project_id, memory_type, payload_json, "
            f"created_at, source_references_json, status, fingerprint FROM {_TABLE} "
            "WHERE tenant_id = ? AND project_id = ?"
        )
        parameters: list[object] = [checked.tenant_id, checked.project_id]
        if checked.memory_type is not None:
            statement += " AND memory_type = ?"
            parameters.append(checked.memory_type.value)
        with self._lock, self._connect() as connection:
            rows = connection.execute(statement, tuple(parameters)).fetchall()
        values = tuple(_load_row(row) for row in rows)
        query_tokens = _tokens(checked.query)
        ordered = sorted(
            values,
            key=lambda value: (
                -len(query_tokens & _tokens(_search_text(value))),
                value.created_at,
                value.memory_id,
                value.fingerprint,
            ),
        )[: checked.limit]
        return tuple(copy.deepcopy(value) for value in ordered)


__all__ = (
    "FOUNDATION_SCHEMA_VERSION",
    "FoundationMemoryMigrationError",
    "FoundationMemoryPersistenceError",
    "SQLiteEngineeringMemoryRepository",
    "initialize_foundation_schema",
    "migrate_foundation_schema",
)
