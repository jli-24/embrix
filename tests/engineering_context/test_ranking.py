from hashlib import sha256

from embedded_copilot.engineering_context import (
    ContextSourceType,
    EngineeringContextEvidence,
)
from embedded_copilot.engineering_context.ranking import rank_evidence


def _item(source_type, evidence_id, status):
    return EngineeringContextEvidence.create(
        evidence_id=evidence_id,
        project_id="project-1",
        source_type=source_type,
        subject="GPIO12",
        summary="GPIO12 fact",
        source_reference=f"source:{evidence_id}",
        source_fingerprint="sha256:" + sha256(evidence_id.encode()).hexdigest(),
        confidence=0.99,
        verification_status=status,
    )


def test_evidence_tier_precedes_confidence_and_id() -> None:
    ranked = rank_evidence(
        (
            _item(ContextSourceType.MULTIMODAL_OBSERVATION, "image-a", "PROJECTED"),
            _item(ContextSourceType.ENGINEERING_MEMORY, "memory-z", "APPROVED"),
            _item(ContextSourceType.KNOWLEDGE_GATEWAY, "knowledge-a", "UNVERIFIED"),
        ),
        "GPIO12",
        (),
    )
    assert tuple(item.evidence_id for item in ranked) == (
        "memory-z",
        "image-a",
        "knowledge-a",
    )
