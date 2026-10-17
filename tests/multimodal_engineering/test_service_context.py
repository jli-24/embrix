from __future__ import annotations

import asyncio

import pytest

from embedded_copilot.engineering_context import ContextVerificationStatus
from embedded_copilot.multimodal_engineering import (
    EngineeringArtifactType,
    FakeMultimodalProvider,
    MultimodalContextProjector,
    MultimodalEngineeringService,
    MultimodalEvidenceMissing,
    MultimodalModelReference,
    MultimodalRejected,
)

from .fixtures import artifact, base_context, observation, question


def test_service_sync_async_revalidate_provider_output_without_mutation() -> None:
    item = artifact()
    provider = FakeMultimodalProvider()
    service = MultimodalEngineeringService(provider=provider)
    sync = service.analyze(item)
    async_result = asyncio.run(service.aanalyze(item))
    assert sync == async_result == observation()
    assert provider.call_count == 2
    assert item == artifact()


def test_service_rejects_provider_artifact_or_model_drift() -> None:
    service = MultimodalEngineeringService(
        provider=FakeMultimodalProvider(
            fixed_observation=observation(EngineeringArtifactType.IMAGE)
        )
    )
    with pytest.raises(MultimodalRejected):
        service.analyze(artifact(EngineeringArtifactType.PCB_IMAGE))


def test_service_rejects_model_identity_changed_after_binding() -> None:
    provider = FakeMultimodalProvider()
    service = MultimodalEngineeringService(provider=provider)
    provider._reference = MultimodalModelReference(
        provider="other", model_name="other-vision"
    )
    with pytest.raises(MultimodalRejected):
        service.analyze(artifact())


def test_service_rejects_missing_evidence_from_provider() -> None:
    class _MissingEvidenceProvider(FakeMultimodalProvider):
        def analyze(self, item):
            result = super().analyze(item)
            material = result.model_dump(mode="python")
            material["evidence_refs"] = ()
            return result.model_construct(**material)

    with pytest.raises(MultimodalEvidenceMissing):
        MultimodalEngineeringService(provider=_MissingEvidenceProvider()).analyze(
            artifact()
        )


def test_context_projection_is_projected_and_preserves_existing_context() -> None:
    projected = MultimodalContextProjector().fuse(
        question=question(),
        observation=observation(),
        base_snapshot=base_context(),
    )
    assert projected.verification_status is ContextVerificationStatus.PROJECTED
    assert projected.knowledge_evidence == base_context().knowledge_evidence
    assert projected.multimodal_evidence
    assert all(
        item.verification_status is ContextVerificationStatus.PROJECTED
        and item.source_fingerprint == observation().fingerprint
        for item in projected.multimodal_evidence
    )
    assert base_context().multimodal_evidence == ()


def test_context_projection_supports_vision_only_and_rejects_project_drift() -> None:
    projector = MultimodalContextProjector()
    result = projector.fuse(
        question=question(), observation=observation(), base_snapshot=None
    )
    assert result.project_id == "project-1"
    assert result.multimodal_evidence
    with pytest.raises(MultimodalRejected):
        projector.fuse(
            question=question(),
            observation=observation(),
            base_snapshot=base_context().model_copy(update={"project_id": "project-2"}),
        )


def test_pcb_visual_projection_does_not_claim_canonical_verification() -> None:
    result = MultimodalContextProjector().project(observation())
    joined = " ".join(item.summary for item in result).casefold()
    assert "drc passed" not in joined
    assert "erc passed" not in joined
    assert all(item.verification_status.value == "PROJECTED" for item in result)
