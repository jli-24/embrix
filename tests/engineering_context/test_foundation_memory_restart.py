from __future__ import annotations

from datetime import UTC, datetime

from embedded_copilot.engineering_context import (
    EngineeringContextRequest,
    EngineeringContextService,
    ReadOnlyProjectionSource,
)
from embedded_copilot.engineering_memory.foundation import (
    DecisionMemoryPayload,
    FoundationEngineeringMemoryRecord,
    FoundationMemoryQuery,
    FoundationMemoryType,
)
from embedded_copilot.engineering_memory.foundation_service import (
    EngineeringMemoryContextProjectionAdapter,
    EngineeringMemoryService,
)
from embedded_copilot.engineering_memory.foundation_sqlite import (
    SQLiteEngineeringMemoryRepository,
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def _record(memory_id: str) -> FoundationEngineeringMemoryRecord:
    return FoundationEngineeringMemoryRecord.create(
        memory_id=memory_id,
        tenant_id="tenant-1",
        project_id="project-1",
        memory_type=FoundationMemoryType.DECISION,
        payload=DecisionMemoryPayload(
            reason="compatibility",
            context="motor board",
            choice="STM32F407",
            timestamp=NOW,
        ),
        created_at=NOW,
        source_references=(f"decision:{memory_id}",),
    )


def _request() -> EngineeringContextRequest:
    return EngineeringContextRequest(
        tenant_id="tenant-1",
        project_id="project-1",
        task="STM32F407",
    )


def _snapshot(repository) -> tuple:
    service = EngineeringMemoryService(repository)
    adapter = EngineeringMemoryContextProjectionAdapter(
        service=service,
        source=ReadOnlyProjectionSource(),
    )
    return EngineeringContextService(source_port=adapter).get_context(_request()).memory_evidence


def test_projection_is_equivalent_before_and_after_sqlite_restart(tmp_path) -> None:
    database = tmp_path / "memory.sqlite3"
    first_repository = SQLiteEngineeringMemoryRepository(database)
    first_repository.save(_record("memory-1"))
    before = _snapshot(first_repository)

    second_repository = SQLiteEngineeringMemoryRepository(database)
    after = _snapshot(second_repository)

    assert before == after
    assert before[0].source_fingerprint == after[0].source_fingerprint
    assert before[0].provenance == after[0].provenance == ("foundation-candidate",)


def test_restart_persistence_keeps_identity_payload_and_candidate_status(tmp_path) -> None:
    database = tmp_path / "memory.sqlite3"
    original = _record("memory-1")
    SQLiteEngineeringMemoryRepository(database).save(original)
    recovered = SQLiteEngineeringMemoryRepository(database).search(
        FoundationMemoryQuery(
            tenant_id="tenant-1", project_id="project-1", query="STM32F407"
        )
    )[0]
    assert recovered.identity == original.identity
    assert recovered.payload == original.payload
    assert recovered.created_at == original.created_at
    assert recovered.source_references == original.source_references
    assert recovered.status == "CANDIDATE"
    assert recovered.fingerprint == original.fingerprint
