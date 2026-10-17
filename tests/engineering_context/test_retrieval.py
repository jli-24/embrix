from __future__ import annotations

from embedded_copilot.engineering_context import (
    ContextSourceType,
    EngineeringContextEvidence,
    EngineeringContextRequest,
)
from embedded_copilot.engineering_context.retrieval import retrieve_context


def test_retrieval_applies_task_and_constraints_deterministically() -> None:
    values = tuple(
        EngineeringContextEvidence.create(
            evidence_id=evidence_id,
            project_id="project-1",
            source_type=ContextSourceType.KNOWLEDGE_GATEWAY,
            subject=subject,
            summary=summary,
            source_reference=f"knowledge:{evidence_id}",
            source_fingerprint="sha256:" + suffix * 64,
            confidence=confidence,
            verification_status="VERIFIED",
        )
        for evidence_id, subject, summary, suffix, confidence in (
            ("knowledge-z", "camera", "camera interface", "a", 0.8),
            ("knowledge-a", "camera", "camera interface SPI", "b", 0.8),
            ("knowledge-c", "sensor", "temperature sensor", "c", 0.9),
        )
    )
    request = EngineeringContextRequest(
        project_id="project-1", task="camera", constraints=("SPI",)
    )
    ranked = retrieve_context(values, request)
    assert tuple(item.evidence_id for item in ranked) == (
        "knowledge-a",
        "knowledge-z",
        "knowledge-c",
    )
