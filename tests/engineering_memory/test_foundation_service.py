from __future__ import annotations

from datetime import UTC, datetime

from embedded_copilot.engineering_context import (
    EngineeringContextRequest,
    GraphProjection,
    ReadOnlyProjectionSource,
)
from embedded_copilot.engineering_memory.foundation import (
    DecisionMemoryPayload,
    FoundationEngineeringMemoryRecord,
    FoundationMemoryQuery,
    FoundationMemoryType,
)
from embedded_copilot.engineering_memory.foundation_repository import (
    InMemoryEngineeringMemoryRepository,
)
from embedded_copilot.engineering_memory.foundation_service import (
    EngineeringMemoryContextProjectionAdapter,
    EngineeringMemoryService,
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def _record(memory_id: str = "memory-1") -> FoundationEngineeringMemoryRecord:
    return FoundationEngineeringMemoryRecord.create(
        memory_id=memory_id,
        tenant_id="tenant-1",
        project_id="project-1",
        memory_type=FoundationMemoryType.DECISION,
        payload=DecisionMemoryPayload(
            reason="peripheral compatibility",
            context="motor board",
            choice="STM32F407",
            timestamp=NOW,
        ),
        created_at=NOW,
        source_references=(f"decision:{memory_id}",),
    )


def _request(task: str = "STM32F407") -> EngineeringContextRequest:
    return EngineeringContextRequest(
        tenant_id="tenant-1",
        project_id="project-1",
        task=task,
    )


class _EmptySource:
    def read_memory(self, request):
        return ()

    def read_graph(self, request):
        return GraphProjection((), ())

    def read_knowledge(self, request):
        return ()

    def read_multimodal(self, request):
        return ()


def test_service_saves_and_searches_only_through_repository_port() -> None:
    repository = InMemoryEngineeringMemoryRepository()
    service = EngineeringMemoryService(repository)
    saved = service.save_memory(_record())
    assert saved.memory_id == "memory-1"
    result = service.search_memory(
        FoundationMemoryQuery(
            tenant_id="tenant-1", project_id="project-1", query="STM32"
        )
    )
    assert tuple(item.memory_id for item in result) == ("memory-1",)


def test_service_revalidates_repository_outputs() -> None:
    class InvalidRepository:
        def save(self, record):
            return object()

        def search(self, query):
            return ()

    service = EngineeringMemoryService(InvalidRepository())
    try:
        service.save_memory(_record())
    except TypeError as error:
        assert "record" in str(error)
    else:  # pragma: no cover - assertion keeps the failure explicit
        raise AssertionError("invalid repository output was accepted")


def test_build_context_projects_candidate_evidence_with_fixed_flags() -> None:
    repository = InMemoryEngineeringMemoryRepository()
    service = EngineeringMemoryService(repository)
    service.save_memory(_record())
    evidence = service.build_context(_request())
    assert len(evidence) == 1
    item = evidence[0]
    assert item.source_type.value == "ENGINEERING_MEMORY"
    assert item.verification_status.value == "REQUIRES_REVIEW"
    assert item.trust_level.value == "CANDIDATE"
    assert item.evidence_status.value == "CANDIDATE"
    assert item.provenance == ("foundation-candidate",)


def test_context_adapter_appends_foundation_memory_without_touching_other_sources() -> None:
    repository = InMemoryEngineeringMemoryRepository()
    service = EngineeringMemoryService(repository)
    service.save_memory(_record())
    adapter = EngineeringMemoryContextProjectionAdapter(
        service=service,
        source=ReadOnlyProjectionSource(),
    )
    result = adapter.read_memory(_request())
    assert len(result) == 1
    assert result[0].source_type.value == "ENGINEERING_MEMORY"
    assert adapter.read_graph(_request()) == GraphProjection((), ())
    assert adapter.read_knowledge(_request()) == ()
    assert adapter.read_multimodal(_request()) == ()


def test_build_context_is_tenant_and_project_scoped() -> None:
    repository = InMemoryEngineeringMemoryRepository()
    service = EngineeringMemoryService(repository)
    service.save_memory(_record())
    assert service.build_context(_request())
    assert service.build_context(
        EngineeringContextRequest(
            tenant_id="tenant-2", project_id="project-1", task="STM32F407"
        )
    ) == ()
    assert service.build_context(
        EngineeringContextRequest(
            tenant_id="tenant-1", project_id="project-2", task="STM32F407"
        )
    ) == ()
