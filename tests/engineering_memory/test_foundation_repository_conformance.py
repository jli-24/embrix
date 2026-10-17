from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from embedded_copilot.engineering_memory import MemoryOperationConflict
from embedded_copilot.engineering_memory.foundation import (
    DecisionMemoryPayload,
    FoundationEngineeringMemoryRecord,
    FoundationMemoryQuery,
    FoundationMemoryType,
)
from embedded_copilot.engineering_memory.foundation_repository import (
    InMemoryEngineeringMemoryRepository,
)
from embedded_copilot.engineering_memory.foundation_sqlite import (
    SQLiteEngineeringMemoryRepository,
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def _record(
    memory_id: str,
    *,
    tenant_id: str = "tenant-1",
    project_id: str = "project-1",
    created_at: datetime = NOW,
    choice: str | None = None,
) -> FoundationEngineeringMemoryRecord:
    return FoundationEngineeringMemoryRecord.create(
        memory_id=memory_id,
        tenant_id=tenant_id,
        project_id=project_id,
        memory_type=FoundationMemoryType.DECISION,
        payload=DecisionMemoryPayload(
            reason="compatibility",
            context="motor board",
            choice=choice or memory_id,
            timestamp=created_at,
        ),
        created_at=created_at,
        source_references=(f"decision:{memory_id}",),
    )


def _query(**changes: object) -> FoundationMemoryQuery:
    values: dict[str, object] = {
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "query": "",
    }
    values.update(changes)
    return FoundationMemoryQuery(**values)


@pytest.fixture(params=("in_memory", "sqlite"))
def repository(request: pytest.FixtureRequest, tmp_path):
    if request.param == "in_memory":
        return InMemoryEngineeringMemoryRepository()
    return SQLiteEngineeringMemoryRepository(tmp_path / "memory.sqlite3")


def test_save_idempotency_conflict_and_scope_are_conformant(repository) -> None:
    first = _record("memory-1")
    assert repository.save(first) == first
    assert repository.save(first) == first
    with pytest.raises(MemoryOperationConflict):
        repository.save(_record("memory-1", choice="changed"))
    repository.save(_record("other-project", project_id="project-2"))
    repository.save(_record("other-tenant", tenant_id="tenant-2"))
    assert tuple(item.memory_id for item in repository.search(_query())) == ("memory-1",)


def test_search_filter_limit_order_and_defensive_reconstruction_are_conformant(
    repository,
) -> None:
    repository.save(_record("memory-z"))
    repository.save(_record("memory-a"))
    repository.save(_record("memory-new", created_at=NOW + timedelta(seconds=1)))
    result = repository.search(_query(limit=2))
    assert tuple(item.memory_id for item in result) == ("memory-a", "memory-z")
    assert repository.search(
        _query(memory_type=FoundationMemoryType.DEBUG)
    ) == ()
    object.__setattr__(result[0].payload, "choice", "tampered")
    assert repository.search(_query())[0].payload.choice == "memory-a"


def test_query_token_ranking_is_conformant(repository) -> None:
    repository.save(_record("memory-a", choice="CAN"))
    repository.save(_record("memory-hit", choice="CAN bus"))
    assert repository.search(_query(query="bus"))[0].memory_id == "memory-hit"
