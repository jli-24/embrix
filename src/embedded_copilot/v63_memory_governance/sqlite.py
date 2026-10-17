from __future__ import annotations

import copy
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .models import (
    ApprovedKnowledge,
    EngineeringMemory,
    EvidenceIdentity,
    KnowledgeCandidate,
    KnowledgeEvolutionProposal,
    MemoryCandidate,
    ReviewItem,
    V63AuditEvent,
)


class SQLiteV63Store:
    """Dedicated, restart-safe v6.3 SQLite store.

    The store persists only validated structured DTOs. It intentionally does
    not share the legacy Review/Manifest database schema version.
    """

    # v6.3 owns schema version 1. It accepts the additive v6.4 version 2
    # without claiming ownership of the v6.4 tables.
    _SCHEMA_VERSION = 1
    _MAX_ACCEPTED_SCHEMA_VERSION = 2

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
            if version > self._MAX_ACCEPTED_SCHEMA_VERSION:
                raise RuntimeError("v6.3 schema is newer than this runtime")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS v63_evidence (
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, project_id, evidence_id)
                );
                CREATE TABLE IF NOT EXISTS v63_memory_candidates (
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, project_id, candidate_id)
                );
                CREATE TABLE IF NOT EXISTS v63_memories (
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, project_id, memory_id)
                );
                CREATE TABLE IF NOT EXISTS v63_knowledge_candidates (
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, project_id, candidate_id)
                );
                CREATE TABLE IF NOT EXISTS v63_approved_knowledge (
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    knowledge_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, project_id, knowledge_id)
                );
                CREATE TABLE IF NOT EXISTS v63_reviews (
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    review_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, project_id, review_id)
                );
                CREATE TABLE IF NOT EXISTS v63_evolution_proposals (
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    proposal_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, project_id, proposal_id)
                );
                CREATE TABLE IF NOT EXISTS v63_audit (
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    body TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, project_id, event_id)
                );
                CREATE TABLE IF NOT EXISTS v63_tombstones (
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    tombstoned_at TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, project_id)
                );
                CREATE TABLE IF NOT EXISTS v63_status_history (
                    tenant_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    body TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_v63_reviews_status
                    ON v63_reviews(tenant_id, project_id);
                CREATE INDEX IF NOT EXISTS idx_v63_history_object
                    ON v63_status_history(tenant_id, project_id, object_id, sequence);
                """
            )
            if version < self._SCHEMA_VERSION:
                connection.execute(f"PRAGMA user_version = {self._SCHEMA_VERSION}")

    @staticmethod
    def _body(value: Any) -> str:
        return value.model_dump_json()

    @staticmethod
    def _load(body: str, model: type[Any]) -> Any:
        # v6.3 DTOs deliberately require immutable tuples. JSON has no tuple
        # type, so restore every persisted array before strict revalidation.
        def restore(value: Any) -> Any:
            if isinstance(value, list):
                return tuple(restore(item) for item in value)
            if isinstance(value, dict):
                return {key: restore(item) for key, item in value.items()}
            return value

        return model.model_validate(restore(json.loads(body)))

    def _save(self, table: str, tenant_id: str, project_id: str, object_id: str, value: Any) -> Any:
        with self._lock, self._connect() as connection:
            if self.is_tombstoned(tenant_id, project_id, connection=connection) and table not in {"v63_audit", "v63_status_history"}:
                raise RuntimeError("project is tombstoned")
            connection.execute(
                f"INSERT INTO {table}(tenant_id, project_id, {self._id_column(table)}, body) VALUES(?,?,?,?) "
                f"ON CONFLICT(tenant_id, project_id, {self._id_column(table)}) DO UPDATE SET body=excluded.body",
                (tenant_id, project_id, object_id, self._body(value)),
            )
        return copy.deepcopy(value)

    @staticmethod
    def _id_column(table: str) -> str:
        return {
            "v63_evidence": "evidence_id",
            "v63_memory_candidates": "candidate_id",
            "v63_memories": "memory_id",
            "v63_knowledge_candidates": "candidate_id",
            "v63_approved_knowledge": "knowledge_id",
            "v63_reviews": "review_id",
            "v63_evolution_proposals": "proposal_id",
            "v63_audit": "event_id",
        }[table]

    def _get(self, table: str, tenant_id: str, project_id: str, object_id: str, model: type[Any]) -> Any | None:
        with self._lock, self._connect() as connection:
            if self.is_tombstoned(tenant_id, project_id, connection=connection):
                return None
            row = connection.execute(
                f"SELECT body FROM {table} WHERE tenant_id=? AND project_id=? AND {self._id_column(table)}=?",
                (tenant_id, project_id, object_id),
            ).fetchone()
        return None if row is None else self._load(row[0], model)

    def _list(self, table: str, tenant_id: str, project_id: str, model: type[Any], *, limit: int | None = None) -> tuple[Any, ...]:
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 100):
            raise ValueError("list limit is invalid")
        with self._lock, self._connect() as connection:
            if self.is_tombstoned(tenant_id, project_id, connection=connection):
                return ()
            statement = f"SELECT body FROM {table} WHERE tenant_id=? AND project_id=? ORDER BY {self._id_column(table)}"
            parameters: tuple[object, ...] = (tenant_id, project_id)
            if limit is not None:
                statement += " LIMIT ?"
                parameters += (limit,)
            rows = connection.execute(
                statement,
                parameters,
            ).fetchall()
        return tuple(self._load(row[0], model) for row in rows)

    def is_tombstoned(self, tenant_id: str, project_id: str, *, connection: sqlite3.Connection | None = None) -> bool:
        own = connection is None
        if own:
            with self._lock, self._connect() as local:
                return self.is_tombstoned(tenant_id, project_id, connection=local)
        row = connection.execute(
            "SELECT 1 FROM v63_tombstones WHERE tenant_id=? AND project_id=?",
            (tenant_id, project_id),
        ).fetchone()
        return row is not None

    def tombstone_project(self, tenant_id: str, project_id: str, tombstoned_at: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO v63_tombstones(tenant_id, project_id, tombstoned_at) VALUES(?,?,?) "
                "ON CONFLICT(tenant_id, project_id) DO NOTHING",
                (tenant_id, project_id, tombstoned_at),
            )
        return cursor.rowcount == 1

    def save_evidence(self, value: EvidenceIdentity) -> EvidenceIdentity:
        return self._save("v63_evidence", value.tenant_id, value.project_id, value.evidence_id, value)

    def get_evidence(self, tenant_id: str, project_id: str, evidence_id: str) -> EvidenceIdentity | None:
        return self._get("v63_evidence", tenant_id, project_id, evidence_id, EvidenceIdentity)

    def save_memory_candidate(self, value: MemoryCandidate) -> MemoryCandidate:
        return self._save("v63_memory_candidates", value.tenant_id, value.project_id, value.memory_candidate_id, value)

    def get_memory_candidate(self, tenant_id: str, project_id: str, candidate_id: str) -> MemoryCandidate | None:
        return self._get("v63_memory_candidates", tenant_id, project_id, candidate_id, MemoryCandidate)

    def list_memory_candidates(self, tenant_id: str, project_id: str) -> tuple[MemoryCandidate, ...]:
        return self._list("v63_memory_candidates", tenant_id, project_id, MemoryCandidate)

    def save_memory(self, value: EngineeringMemory) -> EngineeringMemory:
        return self._save("v63_memories", value.tenant_id, value.project_id, value.memory_id, value)

    def get_memory(self, tenant_id: str, project_id: str, memory_id: str) -> EngineeringMemory | None:
        return self._get("v63_memories", tenant_id, project_id, memory_id, EngineeringMemory)

    def list_memories(self, tenant_id: str, project_id: str, *, limit: int | None = None) -> tuple[EngineeringMemory, ...]:
        return self._list("v63_memories", tenant_id, project_id, EngineeringMemory, limit=limit)

    def save_knowledge_candidate(self, value: KnowledgeCandidate) -> KnowledgeCandidate:
        return self._save("v63_knowledge_candidates", value.tenant_id, value.project_id, value.knowledge_candidate_id, value)

    def get_knowledge_candidate(self, tenant_id: str, project_id: str, candidate_id: str) -> KnowledgeCandidate | None:
        return self._get("v63_knowledge_candidates", tenant_id, project_id, candidate_id, KnowledgeCandidate)

    def list_knowledge_candidates(self, tenant_id: str, project_id: str) -> tuple[KnowledgeCandidate, ...]:
        return self._list("v63_knowledge_candidates", tenant_id, project_id, KnowledgeCandidate)

    def save_knowledge(self, value: ApprovedKnowledge) -> ApprovedKnowledge:
        return self._save("v63_approved_knowledge", value.tenant_id, value.project_id, value.knowledge_id, value)

    def get_knowledge(self, tenant_id: str, project_id: str, knowledge_id: str) -> ApprovedKnowledge | None:
        return self._get("v63_approved_knowledge", tenant_id, project_id, knowledge_id, ApprovedKnowledge)

    def list_knowledge(self, tenant_id: str, project_id: str, *, limit: int | None = None) -> tuple[ApprovedKnowledge, ...]:
        return self._list("v63_approved_knowledge", tenant_id, project_id, ApprovedKnowledge, limit=limit)

    def save_review(self, value: ReviewItem) -> ReviewItem:
        return self._save("v63_reviews", value.tenant_id, value.project_id, value.review_id, value)

    def get_review(self, tenant_id: str, project_id: str, review_id: str) -> ReviewItem | None:
        return self._get("v63_reviews", tenant_id, project_id, review_id, ReviewItem)

    def list_reviews(self, tenant_id: str, project_id: str, *, limit: int | None = None) -> tuple[ReviewItem, ...]:
        return self._list("v63_reviews", tenant_id, project_id, ReviewItem, limit=limit)

    def save_proposal(self, value: KnowledgeEvolutionProposal) -> KnowledgeEvolutionProposal:
        return self._save("v63_evolution_proposals", value.tenant_id, value.project_id, value.proposal_id, value)

    def get_proposal(self, tenant_id: str, project_id: str, proposal_id: str) -> KnowledgeEvolutionProposal | None:
        return self._get("v63_evolution_proposals", tenant_id, project_id, proposal_id, KnowledgeEvolutionProposal)

    def list_proposals(self, tenant_id: str, project_id: str) -> tuple[KnowledgeEvolutionProposal, ...]:
        return self._list("v63_evolution_proposals", tenant_id, project_id, KnowledgeEvolutionProposal)

    def save_audit(self, value: V63AuditEvent) -> V63AuditEvent:
        return self._save("v63_audit", value.tenant_id, value.project_id, value.event_id, value)

    def list_audit(self, tenant_id: str, project_id: str) -> tuple[V63AuditEvent, ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT body FROM v63_audit "
                "WHERE tenant_id=? AND project_id=? ORDER BY event_id",
                (tenant_id, project_id),
            ).fetchall()
        return tuple(self._load(row[0], V63AuditEvent) for row in rows)

    def append_status_history(self, value: V63AuditEvent) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO v63_status_history(tenant_id, project_id, object_id, body) VALUES(?,?,?,?)",
                (value.tenant_id, value.project_id, value.object_id, value.model_dump_json()),
            )

    def list_status_history(self, tenant_id: str, project_id: str) -> tuple[V63AuditEvent, ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT body FROM v63_status_history "
                "WHERE tenant_id=? AND project_id=? ORDER BY sequence",
                (tenant_id, project_id),
            ).fetchall()
        return tuple(self._load(row[0], V63AuditEvent) for row in rows)


__all__ = ("SQLiteV63Store",)
