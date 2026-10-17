from __future__ import annotations

from datetime import UTC, datetime

from embedded_copilot.application import ApplicationQuestionRequest
from embedded_copilot.engineering_context import (
    ContextSourceType,
    EngineeringContextEvidence,
    EngineeringContextSnapshot,
)
from embedded_copilot.model_gateway import ModelPolicy, ModelRequest

NOW = datetime(2026, 8, 10, 14, 0, tzinfo=UTC)


def context(
    *, task: str = "How is ESP32 GPIO12 configured?"
) -> EngineeringContextSnapshot:
    evidence = EngineeringContextEvidence.create(
        evidence_id="memory-1",
        project_id="project-1",
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
        project_id="project-1",
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


def model_request(policy: ModelPolicy) -> ModelRequest:
    snapshot = context(task="check GPIO12")
    return ModelRequest(
        request_id="request-1",
        task="check GPIO12",
        engineering_context=snapshot,
        system_instruction="Use supplied evidence only.",
        constraints=(),
        model_policy=policy,
    )


def application_request() -> ApplicationQuestionRequest:
    return ApplicationQuestionRequest(
        request_id="request-1",
        trace_id="trace-1",
        project_id="project-1",
        requester="engineer-1",
        question="How is ESP32 GPIO12 configured?",
        constraints=(),
        requested_at=NOW,
    )
