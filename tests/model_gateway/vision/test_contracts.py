from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.model_gateway.vision import (
    VisionObservation,
    VisionRequest,
    VisionResponse,
)


def _context() -> EngineeringContextSnapshot:
    return EngineeringContextSnapshot.create(
        project_id="project-1",
        task_description="inspect board",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.5,
        provenance=(),
        verification_status="UNVERIFIED",
    )


def _request(**overrides: object) -> VisionRequest:
    values: dict[str, object] = {
        "request_id": "vision-request-1",
        "prompt": "inspect board components",
        "image_reference": "artifact://image/123",
        "context": _context(),
        "metadata": (("source", "camera-1"),),
    }
    values.update(overrides)
    return VisionRequest(**values)


def test_contracts_are_immutable_normalized_and_fingerprinted() -> None:
    request = _request(prompt="caf" + chr(0x65) + chr(0x301))
    observation = VisionObservation(
        observation_id="observation-1",
        objects=("microcontroller",),
        components=("STM32",),
        interfaces=("UART",),
        issues=("possible_missing_decoupling",),
        confidence=0.85,
        evidence=("fake-observation",),
    )
    response = VisionResponse(
        provider="fake",
        model="fake-vision",
        observation=observation,
        metadata=(("source", "fake"),),
    )

    assert request.prompt == "caf" + chr(0xE9)
    assert request.fingerprint == _request(
        prompt="caf" + chr(0x65) + chr(0x301)
    ).fingerprint
    assert response.fingerprint.startswith("sha256:")
    with pytest.raises((TypeError, ValidationError)):
        request.prompt = "changed"
    with pytest.raises((TypeError, ValidationError)):
        VisionObservation(
            observation_id="observation-2",
            objects=["microcontroller"],
            components=(),
            interfaces=(),
            issues=(),
            confidence=0.5,
            evidence=(),
        )


def test_request_rejects_unowned_image_content_and_unsafe_metadata() -> None:
    with pytest.raises(ValidationError):
        _request(image_reference=b"raw-image")
    with pytest.raises(ValidationError):
        _request(image_reference="data:image/png;base64,AAAA")
    with pytest.raises(ValidationError):
        _request(image_reference="C:\\private\\board.png")
    with pytest.raises(ValidationError):
        _request(metadata=(("endpoint", "https://private.example"),))
    with pytest.raises(ValidationError):
        _request(metadata=[("source", "camera-1")])


def test_fingerprint_cannot_be_supplied() -> None:
    values = _request().model_dump(mode="python")
    values["fingerprint"] = "abc"
    with pytest.raises(ValidationError):
        VisionRequest(**values)


def test_metadata_and_nested_context_are_deep_copied_and_revalidated() -> None:
    request = _request()
    copied = VisionRequest.model_validate(copy.deepcopy(request.model_dump(mode="python")))
    assert copied == request
    assert copied.metadata == (("source", "camera-1"),)
    with pytest.raises(ValidationError):
        VisionResponse(
            provider="fake",
            model="fake-vision",
            observation=VisionObservation(
                observation_id="observation-1",
                objects=(),
                components=(),
                interfaces=(),
                issues=(),
                confidence=0.5,
                evidence=(),
            ),
            metadata=(("api_key", "secret"),),
        )
