from __future__ import annotations

from datetime import UTC, datetime

import pytest

from embedded_copilot.engineering_context import (
    EngineeringContextRequest,
    EngineeringContextService,
    ReadOnlyProjectionSource,
)
from embedded_copilot.engineering_memory.foundation import (
    DecisionMemoryPayload,
    FoundationEngineeringMemoryRecord,
    FoundationMemoryType,
)
from embedded_copilot.engineering_memory.foundation_repository import (
    InMemoryEngineeringMemoryRepository,
)
from embedded_copilot.engineering_memory.foundation_service import (
    EngineeringMemoryContextProjectionAdapter,
    EngineeringMemoryService,
)
from embedded_copilot.engineering_memory.foundation_sqlite import (
    SQLiteEngineeringMemoryRepository,
)
from embedded_copilot.engineering_qa import (
    EngineeringQAService,
    EngineeringQuestion,
    InsufficientEvidence,
)
from embedded_copilot.model_gateway import ModelResponse

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def _record() -> FoundationEngineeringMemoryRecord:
    return FoundationEngineeringMemoryRecord.create(
        memory_id="memory-1",
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
        source_references=("decision:memory-1",),
    )


class _Gateway:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request):
        self.calls += 1
        return ModelResponse(
            content="Answer",
            model_name=request.model_policy.model_name,
            provider="local",
            provenance=request.engineering_context.provenance,
        )

    async def agenerate(self, request):
        return self.generate(request)


def _question() -> EngineeringQuestion:
    return EngineeringQuestion(
        question_id="question-1",
        project_id="project-1",
        question="Which MCU was selected?",
    )


def test_candidate_memory_cannot_satisfy_engineering_qa_evidence_gate() -> None:
    repository = InMemoryEngineeringMemoryRepository()
    repository.save(_record())
    context_provider = EngineeringContextService(
        source_port=EngineeringMemoryContextProjectionAdapter(
            service=EngineeringMemoryService(repository),
            source=ReadOnlyProjectionSource(),
        )
    )
    gateway = _Gateway()
    service = EngineeringQAService(
        context_provider=context_provider,
        model_gateway=gateway,
        tenant_id="tenant-1",
    )
    with pytest.raises(InsufficientEvidence):
        service.answer(_question())
    assert gateway.calls == 0


def test_without_foundation_injection_legacy_empty_memory_channel_is_preserved() -> None:
    context_provider = EngineeringContextService(
        source_port=ReadOnlyProjectionSource()
    )
    request = EngineeringContextRequest(
        tenant_id="tenant-1", project_id="project-1", task="Which MCU was selected?"
    )
    assert context_provider.get_context(request) is None
    gateway = _Gateway()
    answer = EngineeringQAService(
        context_provider=context_provider,
        model_gateway=gateway,
        require_evidence=False,
        tenant_id="tenant-1",
    ).answer(_question())
    assert answer.evidence_refs == ()
    assert gateway.calls == 1


def test_persistent_candidate_after_restart_cannot_satisfy_evidence_gate(tmp_path) -> None:
    database = tmp_path / "memory.sqlite3"
    SQLiteEngineeringMemoryRepository(database).save(_record())
    context_provider = EngineeringContextService(
        source_port=EngineeringMemoryContextProjectionAdapter(
            service=EngineeringMemoryService(SQLiteEngineeringMemoryRepository(database)),
            source=ReadOnlyProjectionSource(),
        )
    )
    gateway = _Gateway()
    service = EngineeringQAService(
        context_provider=context_provider,
        model_gateway=gateway,
        tenant_id="tenant-1",
    )
    with pytest.raises(InsufficientEvidence):
        service.answer(_question())
    assert gateway.calls == 0
