from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_memory import MemoryOperationConflict
from embedded_copilot.engineering_memory.foundation import (
    DebugMemoryPayload,
    DecisionMemoryPayload,
    EvidenceMemoryPayload,
    EvidenceResultReference,
    FoundationEngineeringMemoryRecord,
    FoundationMemoryQuery,
    FoundationMemoryType,
    ProjectMemoryPayload,
)
from embedded_copilot.engineering_memory.foundation_sqlite import (
    FOUNDATION_SCHEMA_VERSION,
    FoundationMemoryMigrationError,
    FoundationMemoryPersistenceError,
    SQLiteEngineeringMemoryRepository,
    migrate_foundation_schema,
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
FP = "sha256:" + "a" * 64


def _record(
    memory_id: str,
    memory_type: FoundationMemoryType = FoundationMemoryType.DECISION,
    *,
    tenant_id: str = "tenant-1",
    project_id: str = "project-1",
) -> FoundationEngineeringMemoryRecord:
    payloads = {
        FoundationMemoryType.PROJECT: ProjectMemoryPayload(
            project_id=project_id,
            project_type="STM32",
            hardware_target="STM32F407",
            requirement_summary="PWM output",
            architecture_summary="FreeRTOS task",
        ),
        FoundationMemoryType.DECISION: DecisionMemoryPayload(
            reason="compatibility",
            context="motor board",
            choice=memory_id,
            timestamp=NOW,
        ),
        FoundationMemoryType.DEBUG: DebugMemoryPayload(
            symptom="PWM abnormal",
            root_cause="timer mismatch",
            fix="updated configuration",
            verification="Build PASS",
        ),
        FoundationMemoryType.EVIDENCE: EvidenceMemoryPayload(
            validation_result=EvidenceResultReference(
                status="PASS",
                source_reference="validation:E0",
                fingerprint=FP,
                artifact_reference="artifact:validation-1",
            )
        ),
    }
    return FoundationEngineeringMemoryRecord.create(
        memory_id=memory_id,
        tenant_id=tenant_id,
        project_id=project_id,
        memory_type=memory_type,
        payload=payloads[memory_type],
        created_at=NOW,
        source_references=(f"foundation:{memory_id}",),
    )


def _query(**changes: object) -> FoundationMemoryQuery:
    values: dict[str, object] = {
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "query": "",
    }
    values.update(changes)
    return FoundationMemoryQuery(**values)


def test_fresh_schema_and_migration_are_deterministic_and_idempotent(tmp_path) -> None:
    database = tmp_path / "memory.sqlite3"
    assert migrate_foundation_schema(database) == FOUNDATION_SCHEMA_VERSION
    assert migrate_foundation_schema(database) == FOUNDATION_SCHEMA_VERSION
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "engineering_memory_foundation" in tables
        assert "engineering_memory_foundation_meta" in tables
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        assert {
            "idx_engineering_memory_foundation_scope",
            "idx_engineering_memory_foundation_scope_type",
            "idx_engineering_memory_foundation_created_at",
        } <= indexes
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0


def test_existing_database_migration_preserves_existing_tables(tmp_path) -> None:
    database = tmp_path / "existing.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 2")
        connection.execute("CREATE TABLE existing_state (value TEXT NOT NULL)")
        connection.execute("INSERT INTO existing_state(value) VALUES ('keep')")
    migrate_foundation_schema(database)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM existing_state").fetchone() == ("keep",)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_migration_rejects_future_schema_without_partial_write(tmp_path) -> None:
    database = tmp_path / "future.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE engineering_memory_foundation_meta "
            "(singleton INTEGER PRIMARY KEY, schema_version INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO engineering_memory_foundation_meta(singleton, schema_version) VALUES (1, 99)"
        )
    with pytest.raises(FoundationMemoryMigrationError):
        migrate_foundation_schema(database)


def test_migration_rejects_incompatible_existing_foundation_table(tmp_path) -> None:
    database = tmp_path / "incompatible.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE engineering_memory_foundation "
            "(memory_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)"
        )
    with pytest.raises(FoundationMemoryMigrationError):
        migrate_foundation_schema(database)


def test_save_load_round_trips_all_four_payload_types_and_restart(tmp_path) -> None:
    database = tmp_path / "memory.sqlite3"
    repository = SQLiteEngineeringMemoryRepository(database)
    records = tuple(
        _record(f"memory-{memory_type.value.lower()}", memory_type)
        for memory_type in FoundationMemoryType
    )
    for record in records:
        repository.save(record)
    restarted = SQLiteEngineeringMemoryRepository(database)
    loaded = restarted.search(_query(limit=100))
    assert {item.memory_type for item in loaded} == set(FoundationMemoryType)
    assert {item.fingerprint for item in loaded} == {
        item.fingerprint for item in records
    }
    assert {item.payload for item in loaded} == {item.payload for item in records}


def test_save_is_idempotent_and_conflict_is_rejected(tmp_path) -> None:
    repository = SQLiteEngineeringMemoryRepository(tmp_path / "memory.sqlite3")
    record = _record("memory-1")
    assert repository.save(record) == record
    assert repository.save(record) == record
    changed = _record("memory-1", FoundationMemoryType.DEBUG)
    with pytest.raises(MemoryOperationConflict):
        repository.save(changed)


def test_search_is_scoped_filtered_bounded_and_deterministic(tmp_path) -> None:
    repository = SQLiteEngineeringMemoryRepository(tmp_path / "memory.sqlite3")
    repository.save(_record("memory-b"))
    repository.save(_record("memory-a"))
    repository.save(_record("other-project", project_id="project-2"))
    repository.save(_record("other-tenant", tenant_id="tenant-2"))
    assert tuple(item.memory_id for item in repository.search(_query())) == (
        "memory-a",
        "memory-b",
    )
    assert repository.search(_query(memory_type=FoundationMemoryType.DEBUG)) == ()
    assert len(repository.search(_query(limit=1))) == 1


def test_search_uses_parameterized_scope_values(tmp_path) -> None:
    repository = SQLiteEngineeringMemoryRepository(tmp_path / "memory.sqlite3")
    repository.save(_record("memory-1"))
    query = _query(query="x' OR 1=1 --")
    assert tuple(item.memory_id for item in repository.search(query)) == ("memory-1",)


@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("payload_json", '{"choice":"tampered"}'),
        ("memory_type", "DEBUG"),
        ("source_references_json", '["foundation:other"]'),
        ("fingerprint", "sha256:" + "b" * 64),
    ),
)
def test_tampered_row_is_rejected_without_auto_repair(
    tmp_path, column: str, value: str
) -> None:
    database = tmp_path / "memory.sqlite3"
    repository = SQLiteEngineeringMemoryRepository(database)
    repository.save(_record("memory-1"))
    with sqlite3.connect(database) as connection:
        connection.execute(
            f"UPDATE engineering_memory_foundation SET {column}=? "
            "WHERE tenant_id=? AND project_id=? AND memory_id=?",
            (value, "tenant-1", "project-1", "memory-1"),
        )
    with pytest.raises(FoundationMemoryPersistenceError):
        repository.search(_query())
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            f"SELECT {column} FROM engineering_memory_foundation "
            "WHERE memory_id='memory-1'"
        ).fetchone()[0] == value


def test_noncanonical_payload_json_is_rejected_without_repair(tmp_path) -> None:
    database = tmp_path / "memory.sqlite3"
    repository = SQLiteEngineeringMemoryRepository(database)
    repository.save(_record("memory-1"))
    with sqlite3.connect(database) as connection:
        payload_json = connection.execute(
            "SELECT payload_json FROM engineering_memory_foundation "
            "WHERE memory_id='memory-1'"
        ).fetchone()[0]
        noncanonical = json.dumps(json.loads(payload_json), indent=2)
        connection.execute(
            "UPDATE engineering_memory_foundation SET payload_json=? "
            "WHERE memory_id='memory-1'",
            (noncanonical,),
        )
    with pytest.raises(FoundationMemoryPersistenceError):
        repository.search(_query())


def test_sensitive_payload_cannot_be_persisted(tmp_path) -> None:
    repository = SQLiteEngineeringMemoryRepository(tmp_path / "memory.sqlite3")
    with pytest.raises(ValidationError):
        repository.save(
            FoundationEngineeringMemoryRecord.create(
                memory_id="memory-1",
                tenant_id="tenant-1",
                project_id="project-1",
                memory_type=FoundationMemoryType.DEBUG,
                payload=DebugMemoryPayload(
                    symptom="api_key=secret",
                    root_cause="r",
                    fix="f",
                    verification="v",
                ),
                created_at=NOW,
                source_references=("debug:1",),
            )
        )
