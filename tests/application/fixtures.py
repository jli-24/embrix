from __future__ import annotations

from datetime import UTC, datetime

from embedded_copilot.application import ApplicationQuestionRequest
from embedded_copilot.engineering_context import (
    ContextSourceType,
    EngineeringContextEvidence,
    EngineeringContextSnapshot,
)

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


def request(**overrides: object) -> ApplicationQuestionRequest:
    values: dict[str, object] = {
        "request_id": "request-1",
        "trace_id": "trace-1",
        "project_id": "project-1",
        "requester": "engineer-1",
        "question": "Can GPIO12 be used on this board?",
        "constraints": (),
        "requested_at": NOW,
    }
    values.update(overrides)
    return ApplicationQuestionRequest(**values)


def snapshot(
    *,
    project_id: str = "project-1",
    task: str = "Can GPIO12 be used on this board?",
) -> EngineeringContextSnapshot:
    evidence = EngineeringContextEvidence.create(
        evidence_id="memory-1",
        project_id=project_id,
        source_type=ContextSourceType.ENGINEERING_MEMORY,
        subject="GPIO12",
        summary="GPIO12 is reserved by the current board configuration",
        fact_key="gpio12.function",
        fact_value="reserved",
        source_reference="memory:1",
        source_fingerprint="sha256:" + "a" * 64,
        confidence=0.8,
        verification_status="APPROVED",
    )
    return EngineeringContextSnapshot.create(
        project_id=project_id,
        task_description=task,
        memory_evidence=(evidence,),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.8,
        provenance=(evidence.source(),),
        verification_status="APPROVED",
    )
