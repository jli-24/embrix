from datetime import UTC, datetime

from embedded_copilot.engineering_context import (
    ContextSourceType,
    EngineeringContextEvidence,
    EngineeringContextSnapshot,
    EngineeringRuntimeContextOverlay,
    RuntimeContextEvidence,
    RuntimeEvidenceType,
)


def test_runtime_overlay_keeps_runtime_evidence_separate() -> None:
    evidence = RuntimeContextEvidence(
        evidence_id="eda-1",
        project_id="project-1",
        evidence_type=RuntimeEvidenceType.EDA_VALIDATION,
        source_id="validation-1",
        source_fingerprint="sha256:" + "1" * 64,
        summary="DRC completed with no violations",
        observed_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    overlay = EngineeringRuntimeContextOverlay(
        overlay_id="overlay-1",
        project_id="project-1",
        base_context_fingerprint=None,
        evidence=(evidence,),
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    assert overlay.evidence == (evidence,)
    assert overlay.base_context_fingerprint is None


def test_runtime_overlay_public_names_remain_compatible_aliases() -> None:
    from embedded_copilot.engineering_context import (
        ContextEvidenceType,
        ContextOverlayEvidence,
        EngineeringContextOverlay,
    )

    assert RuntimeEvidenceType is ContextEvidenceType
    assert RuntimeContextEvidence is ContextOverlayEvidence
    assert EngineeringRuntimeContextOverlay is EngineeringContextOverlay


def test_optimization_overlay_does_not_change_existing_snapshot_fingerprint() -> None:
    """A fixed legacy Snapshot remains byte-for-byte identity stable.

    Runtime overlays are deliberately external to EngineeringContextSnapshot.  This
    regression guards the additive v4.6 RuntimeEvidenceType from changing the
    legacy Snapshot's canonical identity.
    """
    context_evidence = EngineeringContextEvidence.create(
        evidence_id="legacy-memory-1",
        project_id="project-1",
        source_type=ContextSourceType.ENGINEERING_MEMORY,
        subject="board target",
        summary="Legacy board constraint",
        fact_key="board.constraint",
        fact_value="preserved",
        source_reference="memory:legacy-1",
        source_fingerprint="sha256:" + "2" * 64,
        confidence=0.9,
        verification_status="APPROVED",
    )
    snapshot = EngineeringContextSnapshot.create(
        project_id="project-1",
        task_description="fixed legacy snapshot",
        memory_evidence=(context_evidence,),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.9,
        provenance=(context_evidence.source(),),
        verification_status="APPROVED",
    )
    before = snapshot.fingerprint
    overlay = EngineeringRuntimeContextOverlay(
        overlay_id="optimization-overlay-1",
        project_id="project-1",
        base_context_fingerprint=before,
        evidence=(
            RuntimeContextEvidence(
                evidence_id="optimization-decision-1",
                project_id="project-1",
                evidence_type=RuntimeEvidenceType.OPTIMIZATION_DECISION,
                source_id="proposal-1",
                source_fingerprint="sha256:" + "3" * 64,
                summary="Approved optimization proposal projection",
                observed_at=datetime(2026, 8, 10, tzinfo=UTC),
            ),
        ),
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    assert overlay.base_context_fingerprint == before
    assert snapshot.fingerprint == before
    assert (
        EngineeringContextSnapshot.model_validate(
            snapshot.model_dump(mode="python")
        ).fingerprint
        == before
    )
