from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.model_gateway import (
    ModelPolicy,
    ModelRequest,
    ModelRequestMetadata,
    ProviderCapability,
)


def _context() -> EngineeringContextSnapshot:
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


def test_legacy_request_derives_provider_fields_and_metadata_is_bound() -> None:
    metadata = ModelRequestMetadata(
        request_id="request-1",
        trace_id="trace-1",
        created_at=datetime(2026, 8, 9, tzinfo=UTC),
    )
    request = ModelRequest(
        request_id="request-1",
        task="check GPIO12",
        engineering_context=_context(),
        system_instruction="Use supplied evidence only.",
        model_policy=ModelPolicy(
            primary_provider="openai",
            model_name="gpt-test",
            temperature=0.2,
            max_output_tokens=64,
        ),
        metadata=metadata,
    )

    assert request.prompt == request.task
    assert request.system_prompt == request.system_instruction
    assert request.context == request.engineering_context
    assert request.temperature == 0.2
    assert request.max_tokens == 64
    assert request.metadata == metadata
    assert ProviderCapability.TEXT.value == "text"
    assert ProviderCapability.VISION.value == "vision"


def test_request_rejects_conflicting_or_unbound_provider_metadata() -> None:
    values = {
        "request_id": "request-1",
        "task": "check GPIO12",
        "engineering_context": _context(),
        "system_instruction": "Use supplied evidence only.",
        "model_policy": ModelPolicy(primary_provider="openai", model_name="gpt-test"),
    }
    with pytest.raises(ValidationError):
        ModelRequest(**values, prompt="a different task")
    with pytest.raises(ValidationError):
        ModelRequest(
            **values,
            metadata=ModelRequestMetadata(
                request_id="other-request",
                trace_id="trace-1",
                created_at=datetime(2026, 8, 9, tzinfo=UTC),
            ),
        )
