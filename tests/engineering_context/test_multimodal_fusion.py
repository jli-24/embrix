from __future__ import annotations

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_context import (
    ContextSourceType,
    EngineeringContextEvidence,
    EngineeringContextSnapshot,
    MultimodalContextFusionRequest,
    MultimodalContextFusionService,
)
from embedded_copilot.engineering_qa.contracts import EngineeringQuestion
from embedded_copilot.engineering_qa.prompt import PromptBuilder
from embedded_copilot.model_gateway.vision import VisionObservation


def _observation(
    observation_id: str = "vision-1", *, confidence: float = 0.85
) -> VisionObservation:
    return VisionObservation(
        observation_id=observation_id,
        objects=("microcontroller",),
        components=("STM32",),
        interfaces=("UART",),
        issues=("possible_missing_decoupling",),
        confidence=confidence,
        evidence=("visible-label",),
    )


def _evidence(*, value: str = "SPI") -> EngineeringContextEvidence:
    return EngineeringContextEvidence.create(
        evidence_id="graph-interface-1",
        project_id="project-1",
        source_type=ContextSourceType.KNOWLEDGE_GRAPH,
        subject="interface-target-1",
        summary="configured interface",
        fact_key="target-1.interface",
        fact_value=value,
        source_reference="graph:interface-1",
        source_fingerprint="sha256:" + "1" * 64,
        confidence=0.95,
        verification_status="VERIFIED",
    )


def _snapshot(*, graph: tuple[EngineeringContextEvidence, ...] = ()) -> EngineeringContextSnapshot:
    return EngineeringContextSnapshot.create(
        project_id="project-1",
        task_description="inspect interface",
        memory_evidence=(),
        graph_evidence=graph,
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.5,
        provenance=tuple(item.source() for item in graph),
        verification_status="VERIFIED" if graph else "UNVERIFIED",
    )


def _request(
    *,
    snapshot: EngineeringContextSnapshot | None = None,
    observation: VisionObservation | None = None,
    target_id: str | None = None,
) -> MultimodalContextFusionRequest:
    return MultimodalContextFusionRequest.from_observation(
        project_id="project-1",
        snapshot=_snapshot() if snapshot is None else snapshot,
        observation=_observation() if observation is None else observation,
        source_reference="artifact://camera-1",
        target_id=target_id,
    )


def test_vision_observation_is_projected_to_immutable_context_evidence() -> None:
    request = _request()
    result = MultimodalContextFusionService().fuse(request)

    assert result is not None
    assert request.evidence.source_type == "vision"
    assert request.evidence.components == ("STM32",)
    assert request.evidence.observation_fingerprint == _observation().fingerprint
    assert len(result.multimodal_evidence) == 6
    assert {item.source_reference for item in result.multimodal_evidence} == {
        "artifact://camera-1"
    }
    with pytest.raises((TypeError, ValidationError)):
        request.evidence.components = ()


def test_fusion_is_deterministic_and_higher_confidence_vision_evidence_ranks_first() -> None:
    service = MultimodalContextFusionService()
    first = service.fuse(
        _request(observation=_observation("vision-low", confidence=0.7))
    )
    assert first is not None
    result = service.fuse(
        _request(
            snapshot=first,
            observation=_observation("vision-high", confidence=0.9),
        )
    )

    assert result is not None
    summary_evidence = tuple(
        item
        for item in result.multimodal_evidence
        if item.summary.startswith("vision observation")
    )
    assert tuple(item.evidence_id for item in summary_evidence) == (
        "vision-vision-high",
        "vision-vision-low",
    )
    repeat = service.fuse(
        _request(
            snapshot=first,
            observation=_observation("vision-high", confidence=0.9),
        )
    )
    assert repeat is not None
    assert (result.context_id, result.fingerprint) == (
        repeat.context_id,
        repeat.fingerprint,
    )


def test_target_bound_interface_conflicts_but_unbound_observation_does_not() -> None:
    snapshot = _snapshot(graph=(_evidence(),))
    service = MultimodalContextFusionService()

    target_bound = service.fuse(_request(snapshot=snapshot, target_id="target-1"))
    unbound = service.fuse(_request(snapshot=snapshot))

    assert target_bound is not None and len(target_bound.conflicts) == 1
    assert target_bound.conflicts[0].resolution_required is True
    assert unbound is not None and unbound.conflicts == ()


def test_fusion_revalidates_bindings_without_mutating_input_or_sources() -> None:
    snapshot = _snapshot(graph=(_evidence(),))
    observation = _observation()
    request = _request(snapshot=snapshot, observation=observation, target_id="target-1")

    result = MultimodalContextFusionService().fuse(request)

    assert result is not None
    assert snapshot.multimodal_evidence == ()
    assert observation.interfaces == ("UART",)
    assert snapshot.graph_evidence[0].fact_value == "SPI"
    material = request.model_dump(mode="python")
    material["project_id"] = "project-2"
    with pytest.raises(ValidationError):
        MultimodalContextFusionRequest.model_validate(material)


def test_fusion_rejects_project_mismatch_and_unsafe_source_reference() -> None:
    with pytest.raises(ValidationError):
        MultimodalContextFusionRequest.from_observation(
            project_id="project-1",
            snapshot=EngineeringContextSnapshot.create(
                project_id="project-2",
                task_description="inspect interface",
                memory_evidence=(),
                graph_evidence=(),
                knowledge_evidence=(),
                multimodal_evidence=(),
                conflicts=(),
                confidence=0.0,
                provenance=(),
                verification_status="UNVERIFIED",
            ),
            observation=_observation(),
            source_reference="artifact://camera-1",
        )
    with pytest.raises(ValidationError):
        MultimodalContextFusionRequest.from_observation(
            project_id="project-1",
            snapshot=_snapshot(),
            observation=_observation(),
            source_reference="http://untrusted.example/image",
        )


def test_q_and_a_prompt_serializes_fused_multimodal_evidence() -> None:
    snapshot = MultimodalContextFusionService().fuse(_request())
    assert snapshot is not None

    model_request = PromptBuilder().build(
        EngineeringQuestion(
            question_id="question-1",
            project_id="project-1",
            question="What component is visible?",
        ),
        snapshot,
    )

    assert "observed component: STM32" in model_request.system_instruction
