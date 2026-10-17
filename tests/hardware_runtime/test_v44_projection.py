from datetime import UTC, datetime

from embedded_copilot.engineering_context import (
    EngineeringRuntimeContextOverlay,
    RuntimeContextEvidence,
    RuntimeEvidenceType,
)
from embedded_copilot.hardware_runtime.projection import (
    RuntimeMemoryCandidateReference,
    RuntimeMemoryProjectionService,
)


def test_overlay_projects_review_required_memory_candidate_without_promotion() -> None:
    evidence = RuntimeContextEvidence(
        evidence_id="hardware-1",
        project_id="project-1",
        evidence_type=RuntimeEvidenceType.HARDWARE_OBSERVATION,
        source_id="observation-1",
        source_fingerprint="sha256:" + "1" * 64,
        summary="UART boot observation completed",
        observed_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    overlay = EngineeringRuntimeContextOverlay(
        overlay_id="overlay-1",
        project_id="project-1",
        base_context_fingerprint=None,
        evidence=(evidence,),
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
    )

    class Port:
        def project(self, value):
            return (
                RuntimeMemoryCandidateReference(
                    project_id=value.project_id,
                    overlay_fingerprint=value.fingerprint,
                    candidate_id="memory-candidate-1",
                    candidate_fingerprint="sha256:" + "2" * 64,
                ),
            )

    service = RuntimeMemoryProjectionService(Port())
    candidate = service.project(overlay)[0]
    assert candidate.review_status == "REVIEW_REQUIRED"
    assert not hasattr(service, "promote")
