from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.model_gateway import (
    ModelMessage,
    ModelPolicy,
    ModelRequest,
    ModelResponse,
    ModelUsage,
)


def _snapshot() -> EngineeringContextSnapshot:
    return EngineeringContextSnapshot.create(
        project_id="project-1",
        task_description="check GPIO12",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.5,
        provenance=(),
        verification_status="UNVERIFIED",
    )


def test_contracts_are_immutable_strict_normalized_and_fingerprinted() -> None:
    policy = ModelPolicy(primary_provider="openai", model_name="gpt-test")
    request = ModelRequest(
        request_id="request-1",
        task="cafe\u0301",
        engineering_context=_snapshot(),
        system_instruction="Use supplied evidence only.",
        model_policy=policy,
    )
    response = ModelResponse(
        content="Unknown.",
        model_name="gpt-test",
        provider="openai",
        provenance=(),
    )
    assert request.task == "café"
    assert policy.fingerprint.startswith("sha256:")
    assert request.fingerprint.startswith("sha256:")
    assert response.response_id.startswith("response-")
    with pytest.raises((TypeError, ValidationError)):
        request.task = "changed"
    with pytest.raises(ValidationError):
        ModelRequest.model_validate({**request.model_dump(), "task": "changed"})
    with pytest.raises((TypeError, ValidationError)):
        ModelPolicy(
            primary_provider="openai",
            model_name="gpt-test",
            fallback_providers=["local"],
        )


def test_usage_and_nested_context_are_revalidated_and_deep_copied() -> None:
    request = ModelRequest(
        request_id="request-1",
        task="check GPIO12",
        engineering_context=_snapshot(),
        system_instruction="Use supplied evidence only.",
        constraints=("offline",),
        model_policy=ModelPolicy(primary_provider="local", model_name="test-model"),
    )
    checked = ModelRequest.model_validate(
        copy.deepcopy(request.model_dump(mode="python"))
    )
    assert checked == request
    assert ModelUsage(input_tokens=2, output_tokens=3, total_tokens=5).total_tokens == 5
    with pytest.raises(ValidationError):
        ModelUsage(input_tokens=2, output_tokens=3, total_tokens=4)
    with pytest.raises(ValidationError):
        ModelResponse(
            content="token: secret-value",
            model_name="test-model",
            provider="local",
            provenance=(),
        )


def test_request_can_carry_canonical_routing_metadata_and_artifact_refs() -> None:
    request = ModelRequest(
        request_id="request-1",
        task="check GPIO12",
        engineering_context=_snapshot(),
        system_instruction="Use supplied evidence only.",
        model_policy=ModelPolicy(primary_provider="openai", model_name="gpt-test"),
        project_id="project-1",
        conversation_id="conversation-1",
        task_type="FIRMWARE",
        messages=(ModelMessage(role="user", content="check GPIO12"),),
        attachments=("artifact://project-1/input-1",),
        required_capabilities=("TEXT", "TOOL_CALLING"),
        preferred_model="model-profile-1",
        preferred_provider="openai",
        routing_strategy="BEST_VALUE",
    )
    assert request.project_id == "project-1"
    assert request.messages[0].content == "check GPIO12"
    assert request.attachments == ("artifact://project-1/input-1",)
