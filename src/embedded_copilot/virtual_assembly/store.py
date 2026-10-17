from __future__ import annotations

import copy
import json
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from .contracts import VirtualBoardAssembly


class VirtualAssemblyStoreConflict(RuntimeError):
    """The same immutable assembly identity was presented with different data."""


def _restore_json(value: object) -> object:
    if isinstance(value, list):
        return tuple(_restore_json(item) for item in value)
    if isinstance(value, dict):
        return {key: _restore_json(item) for key, item in value.items()}
    return value


class SQLiteVirtualAssemblyStore:
    """Persistent immutable store for VirtualBoardAssembly projections."""

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
        with self._lock, closing(self._connect()) as connection, connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version > self._SCHEMA_VERSION:
                    raise RuntimeError("virtual assembly schema is newer than this runtime")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS virtual_board_assemblies (
                        project_id TEXT NOT NULL,
                        assembly_id TEXT NOT NULL,
                        assembly_fingerprint TEXT NOT NULL,
                        body TEXT NOT NULL,
                        stored_at TEXT NOT NULL,
                        PRIMARY KEY (project_id, assembly_id)
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_virtual_assembly_latest ON virtual_board_assemblies(project_id, stored_at DESC)"
                )
                if version < self._SCHEMA_VERSION:
                    connection.execute(f"PRAGMA user_version = {self._SCHEMA_VERSION}")

    @staticmethod
    def _load(body: str) -> VirtualBoardAssembly:
        payload = _restore_json(json.loads(body))
        if not isinstance(payload, dict):
            raise TypeError("virtual assembly body is invalid")
        return VirtualBoardAssembly.model_validate(payload, strict=False)

    def save(self, assembly: VirtualBoardAssembly) -> VirtualBoardAssembly:
        checked = VirtualBoardAssembly.model_validate(
            copy.deepcopy(assembly.model_dump(mode="python"))
        )
        body = checked.model_dump_json()
        with self._lock, closing(self._connect()) as connection, connection:
                row = connection.execute(
                    "SELECT body FROM virtual_board_assemblies WHERE project_id=? AND assembly_id=?",
                    (checked.project_id, checked.assembly_id),
                ).fetchone()
                if row is not None and row[0] != body:
                    raise VirtualAssemblyStoreConflict("virtual assembly identity conflict")
                connection.execute(
                    "INSERT INTO virtual_board_assemblies(project_id,assembly_id,assembly_fingerprint,body,stored_at) VALUES(?,?,?,?,?) ON CONFLICT(project_id,assembly_id) DO NOTHING",
                    (
                        checked.project_id,
                        checked.assembly_id,
                        checked.assembly_fingerprint,
                        body,
                        datetime.now(UTC).isoformat(),
                    ),
                )
        return copy.deepcopy(checked)

    def get(self, project_id: str, assembly_id: str) -> VirtualBoardAssembly | None:
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT body FROM virtual_board_assemblies WHERE project_id=? AND assembly_id=?",
                (project_id, assembly_id),
            ).fetchone()
        return None if row is None else self._load(row[0])

    def get_latest(self, project_id: str) -> VirtualBoardAssembly | None:
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT body FROM virtual_board_assemblies WHERE project_id=? ORDER BY stored_at DESC, assembly_id DESC LIMIT 1",
                (project_id,),
            ).fetchone()
        return None if row is None else self._load(row[0])

    def get_by_fingerprint(
        self, project_id: str, assembly_fingerprint: str
    ) -> VirtualBoardAssembly | None:
        with self._lock, closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT body FROM virtual_board_assemblies WHERE project_id=? AND assembly_fingerprint=? ORDER BY stored_at DESC LIMIT 1",
                (project_id, assembly_fingerprint),
            ).fetchone()
        return None if row is None else self._load(row[0])


__all__ = ("SQLiteVirtualAssemblyStore", "VirtualAssemblyStoreConflict")
