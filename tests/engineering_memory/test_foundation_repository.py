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
    EngineeringMemoryRepositoryPort,
    InMemoryEngineeringMemoryRepository,
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


def test_repository_implements_only_the_narrow_port() -> None:
    repository = InMemoryEngineeringMemoryRepository()
    assert isinstance(repository, EngineeringMemoryRepositoryPort)
    assert not any(hasattr(repository, name) for name in ("update", "delete", "clear"))


def test_save_and_idempotent_save_return_validated_copies() -> None:
    repository = InMemoryEngineeringMemoryRepository()
    first = repository.save(_record("memory-1"))
    second = repository.save(_record("memory-1"))
    assert first == second
    assert first is not second


def test_same_identity_with_different_fingerprint_conflicts() -> None:
    repository = InMemoryEngineeringMemoryRepository()
    repository.save(_record("memory-1", choice="STM32F407"))
    with pytest.raises(MemoryOperationConflict):
        repository.save(_record("memory-1", choice="STM32F429"))


def test_search_enforces_tenant_and_project_isolation() -> None:
    repository = InMemoryEngineeringMemoryRepository()
    repository.save(_record("memory-1"))
    repository.save(_record("memory-2", tenant_id="tenant-2"))
    repository.save(_record("memory-3", project_id="project-2"))
    assert tuple(item.memory_id for item in repository.search(_query())) == ("memory-1",)
    assert repository.search(_query(tenant_id="tenant-2"))[0].memory_id == "memory-2"
    assert repository.search(_query(project_id="project-2"))[0].memory_id == "memory-3"


def test_search_filters_type_and_empty_query_is_deterministic() -> None:
    repository = InMemoryEngineeringMemoryRepository()
    repository.save(_record("memory-b"))
    repository.save(_record("memory-a"))
    result = repository.search(_query(memory_type=FoundationMemoryType.DEBUG))
    assert result == ()
    assert tuple(item.memory_id for item in repository.search(_query())) == (
        "memory-a",
        "memory-b",
    )


def test_search_limit_is_bounded() -> None:
    repository = InMemoryEngineeringMemoryRepository()
    for index in range(1, 5):
        repository.save(_record(f"memory-{index}"))
    assert len(repository.search(_query(limit=2))) == 2
    with pytest.raises(ValueError):
        FoundationMemoryQuery(tenant_id="tenant-1", project_id="project-1", limit=101)


def test_search_token_ranking_and_memory_id_tie_break_are_stable() -> None:
    repository = InMemoryEngineeringMemoryRepository()
    repository.save(_record("memory-z", choice="CAN"))
    repository.save(_record("memory-a", choice="CAN"))
    repository.save(_record("memory-hit", choice="CAN bus"))
    query = _query(query="bus")
    first = repository.search(query)
    second = repository.search(query)
    assert tuple(item.memory_id for item in first) == tuple(
        item.memory_id for item in second
    )
    assert first[0].memory_id == "memory-hit"
    assert tuple(item.memory_id for item in first[1:]) == ("memory-a", "memory-z")


def test_repository_defensively_copies_inputs_and_outputs() -> None:
    repository = InMemoryEngineeringMemoryRepository()
    source = _record("memory-1")
    repository.save(source)
    returned = repository.search(_query())[0]
    object.__setattr__(returned.payload, "choice", "tampered")
    assert repository.search(_query())[0].payload.choice == "memory-1"


def test_created_at_is_part_of_stable_order_after_score() -> None:
    repository = InMemoryEngineeringMemoryRepository()
    repository.save(_record("memory-new", created_at=NOW + timedelta(seconds=1)))
    repository.save(_record("memory-old", created_at=NOW))
    assert tuple(item.memory_id for item in repository.search(_query(query="absent"))) == (
        "memory-old",
        "memory-new",
    )
