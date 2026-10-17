from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


MANIFEST_SCHEMA_VERSION = 2


class KnowledgeManifestError(RuntimeError):
    """Raised when the authoritative knowledge identity store is unavailable."""


@dataclass(frozen=True, slots=True)
class KnowledgeManifestRecord:
    document_id: str
    project_id: str
    tenant: str
    requester: str
    source_reference: str
    document_fingerprint: str
    content_fingerprint: str
    parser_version: str
    chunker_version: str
    embedding_provider: str
    embedding_model: str
    embedding_dimension: int
    ingestion_status: str
    chunk_count: int
    created_at: str
    updated_at: str
    ingestion_revision: str
    error_code: str | None = None
    deleted: bool = False
    embedding_identity: str = ""

    @property
    def identity(self) -> tuple[str, str, str, str, str, str, int, str]:
        return (
            self.project_id,
            self.document_fingerprint,
            self.parser_version,
            self.chunker_version,
            self.embedding_provider,
            self.embedding_model,
            self.embedding_dimension,
            self.embedding_identity,
        )

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), default=str)
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SQLiteKnowledgeManifestStore:
    """SQLite-backed authoritative document/index identity store.

    The migration is explicit and versioned. Chroma is only an index; callers
    must consult this store before accepting a retrieval result.
    """

    def __init__(self, database: str | Path) -> None:
        self._database = Path(database)
        self._database.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database, timeout=5)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _migrate(self) -> None:
        with self._lock, self._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > MANIFEST_SCHEMA_VERSION:
                raise KnowledgeManifestError("knowledge manifest schema is newer than runtime")
            if version < 1:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS knowledge_ingestion_manifest (
                        document_id TEXT NOT NULL,
                        project_id TEXT NOT NULL,
                        tenant TEXT NOT NULL,
                        requester TEXT NOT NULL,
                        source_reference TEXT NOT NULL,
                        document_fingerprint TEXT NOT NULL,
                        content_fingerprint TEXT NOT NULL,
                        parser_version TEXT NOT NULL,
                        chunker_version TEXT NOT NULL,
                        embedding_provider TEXT NOT NULL,
                        embedding_model TEXT NOT NULL,
                        embedding_dimension INTEGER NOT NULL,
                        ingestion_status TEXT NOT NULL,
                        chunk_count INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        ingestion_revision TEXT NOT NULL,
                        error_code TEXT,
                        deleted INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY (tenant, project_id, document_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_knowledge_manifest_project
                        ON knowledge_ingestion_manifest (tenant, project_id, ingestion_status, deleted);
                    CREATE TABLE IF NOT EXISTS knowledge_project_tombstone (
                        tenant TEXT NOT NULL,
                        project_id TEXT NOT NULL,
                        requester TEXT NOT NULL,
                        deleted_at TEXT NOT NULL,
                        PRIMARY KEY (tenant, project_id)
                    );
                    """
                )
                connection.execute("PRAGMA user_version = 1")
                version = 1
            if version < 2:
                connection.execute(
                    "ALTER TABLE knowledge_ingestion_manifest "
                    "ADD COLUMN embedding_identity TEXT NOT NULL DEFAULT ''"
                )
                connection.execute("PRAGMA user_version = 2")

    @staticmethod
    def _record(row: tuple[object, ...]) -> KnowledgeManifestRecord:
        return KnowledgeManifestRecord(
            document_id=str(row[0]), project_id=str(row[1]), tenant=str(row[2]), requester=str(row[3]),
            source_reference=str(row[4]), document_fingerprint=str(row[5]), content_fingerprint=str(row[6]),
            parser_version=str(row[7]), chunker_version=str(row[8]), embedding_provider=str(row[9]),
            embedding_model=str(row[10]), embedding_dimension=int(row[11]), ingestion_status=str(row[12]),
            chunk_count=int(row[13]), created_at=str(row[14]), updated_at=str(row[15]),
            ingestion_revision=str(row[16]), error_code=None if row[17] is None else str(row[17]),
            deleted=bool(row[18]), embedding_identity=str(row[19]),
        )

    _COLUMNS = (
        "document_id, project_id, tenant, requester, source_reference, document_fingerprint, "
        "content_fingerprint, parser_version, chunker_version, embedding_provider, embedding_model, "
        "embedding_dimension, ingestion_status, chunk_count, created_at, updated_at, ingestion_revision, "
        "error_code, deleted, embedding_identity"
    )

    def get(self, *, tenant: str, project_id: str, document_id: str) -> KnowledgeManifestRecord | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                f"SELECT {self._COLUMNS} FROM knowledge_ingestion_manifest WHERE tenant=? AND project_id=? AND document_id=?",
                (tenant, project_id, document_id),
            ).fetchone()
        return None if row is None else self._record(row)

    def list_ready(self, *, tenant: str, project_id: str) -> tuple[KnowledgeManifestRecord, ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"SELECT {self._COLUMNS} FROM knowledge_ingestion_manifest WHERE tenant=? AND project_id=? AND ingestion_status='READY' AND deleted=0 ORDER BY document_id",
                (tenant, project_id),
            ).fetchall()
        return tuple(self._record(row) for row in rows)

    def is_project_tombstoned(self, *, tenant: str, project_id: str) -> bool:
        with self._lock, self._connect() as connection:
            return connection.execute(
                "SELECT 1 FROM knowledge_project_tombstone WHERE tenant=? AND project_id=?",
                (tenant, project_id),
            ).fetchone() is not None

    def upsert(self, record: KnowledgeManifestRecord) -> KnowledgeManifestRecord:
        values = tuple(asdict(record).values())
        with self._lock, self._connect() as connection:
            connection.execute(
                f"INSERT INTO knowledge_ingestion_manifest ({self._COLUMNS.replace(', ', ', ')}) VALUES ({','.join('?' for _ in values)}) "
                "ON CONFLICT(tenant, project_id, document_id) DO UPDATE SET "
                "requester=excluded.requester, source_reference=excluded.source_reference, document_fingerprint=excluded.document_fingerprint, "
                "content_fingerprint=excluded.content_fingerprint, parser_version=excluded.parser_version, chunker_version=excluded.chunker_version, "
                "embedding_provider=excluded.embedding_provider, embedding_model=excluded.embedding_model, embedding_dimension=excluded.embedding_dimension, "
                "ingestion_status=excluded.ingestion_status, chunk_count=excluded.chunk_count, updated_at=excluded.updated_at, "
                "ingestion_revision=excluded.ingestion_revision, error_code=excluded.error_code, deleted=excluded.deleted, "
                "embedding_identity=excluded.embedding_identity",
                values,
            )
        return record

    def tombstone_project(self, *, tenant: str, project_id: str, requester: str, now: datetime | None = None) -> int:
        timestamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO knowledge_project_tombstone (tenant, project_id, requester, deleted_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(tenant, project_id) DO UPDATE SET requester=excluded.requester, deleted_at=excluded.deleted_at",
                (tenant, project_id, requester, timestamp),
            )
            cursor = connection.execute(
                "UPDATE knowledge_ingestion_manifest SET ingestion_status='DELETED', deleted=1, requester=?, updated_at=?, error_code='PROJECT_TOMBSTONED' WHERE tenant=? AND project_id=?",
                (requester, timestamp, tenant, project_id),
            )
        return cursor.rowcount


class InMemoryKnowledgeManifestStore:
    """Test-only manifest seam; never used by production composition."""

    def __init__(self) -> None:
        self._values: dict[tuple[str, str, str], KnowledgeManifestRecord] = {}
        self._tombstones: dict[tuple[str, str], str] = {}

    def get(self, *, tenant: str, project_id: str, document_id: str) -> KnowledgeManifestRecord | None:
        return self._values.get((tenant, project_id, document_id))

    def list_ready(self, *, tenant: str, project_id: str) -> tuple[KnowledgeManifestRecord, ...]:
        return tuple(
            value for value in self._values.values()
            if value.tenant == tenant and value.project_id == project_id
            and value.ingestion_status == "READY" and not value.deleted
        )

    def is_project_tombstoned(self, *, tenant: str, project_id: str) -> bool:
        return (tenant, project_id) in self._tombstones

    def upsert(self, record: KnowledgeManifestRecord) -> KnowledgeManifestRecord:
        self._values[(record.tenant, record.project_id, record.document_id)] = record
        return record

    def tombstone_project(self, *, tenant: str, project_id: str, requester: str, now: datetime | None = None) -> int:
        timestamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
        self._tombstones[(tenant, project_id)] = timestamp
        changed = 0
        for key, value in tuple(self._values.items()):
            if value.tenant == tenant and value.project_id == project_id and not value.deleted:
                self._values[key] = KnowledgeManifestRecord(
                    **{**asdict(value), "requester": requester, "updated_at": timestamp,
                       "ingestion_status": "DELETED", "error_code": "PROJECT_TOMBSTONED", "deleted": True}
                )
                changed += 1
        return changed


__all__ = (
    "KnowledgeManifestError",
    "KnowledgeManifestRecord",
    "InMemoryKnowledgeManifestStore",
    "SQLiteKnowledgeManifestStore",
)
