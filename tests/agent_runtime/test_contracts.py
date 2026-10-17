from __future__ import annotations

import copy
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.agent_runtime import (
    AgentCapabilityRequest,
    CapabilityAuditEvent,
    CapabilityBindingReference,
    CapabilityPolicyGrant,
    CapabilityRouteDecision,
    CapabilityType,
    KnowledgeInput,
)

from .conftest import application_request, knowledge_request


def test_knowledge_request_is_strict_frozen_and_fingerprinted() -> None:
    request = knowledge_request()
    assert request.payload.capability is CapabilityType.KNOWLEDGE
    assert request.fingerprint.startswith("sha256:")
    with pytest.raises(ValidationError):
        request.payload = KnowledgeInput()  # type: ignore[misc]


def test_contract_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        KnowledgeInput(unknown="value")  # type: ignore[call-arg]


def test_contract_rejects_list_for_tuple() -> None:
    with pytest.raises(ValidationError):
        CapabilityPolicyGrant(
            project_id="project-1",
            requester="engineer-1",
            capabilities=[CapabilityType.KNOWLEDGE],  # type: ignore[arg-type]
        )


def test_nested_application_request_is_revalidated_and_isolated() -> None:
    application = application_request()
    request = AgentCapabilityRequest(
        application_request=application,
        payload=KnowledgeInput(),
    )
    assert request.application_request is not application
    assert request.application_request == application


def test_request_fingerprint_is_deterministic() -> None:
    first = knowledge_request()
    second = knowledge_request()
    assert first.fingerprint == second.fingerprint


def test_request_rejects_fingerprint_tampering() -> None:
    request = knowledge_request()
    material = request.model_dump(mode="python")
    material["fingerprint"] = "sha256:" + "f" * 64
    with pytest.raises(ValidationError):
        AgentCapabilityRequest.model_validate(material)


def test_request_normalizes_nested_question_to_nfc_and_trim() -> None:
    request = AgentCapabilityRequest(
        application_request=application_request("  What is cafe\u0301?  "),
        payload=KnowledgeInput(),
    )
    assert request.application_request.question == "What is café?"


def test_application_timestamp_is_normalized_to_utc() -> None:
    request = AgentCapabilityRequest(
        application_request=application_request(
            requested_at=datetime.fromisoformat("2026-08-11T00:00:00+08:00")
        ),
        payload=KnowledgeInput(),
    )
    assert request.application_request.requested_at.tzinfo is UTC


def test_route_decision_rejects_duplicate_candidates() -> None:
    request = knowledge_request()
    with pytest.raises(ValidationError):
        CapabilityRouteDecision(
            request_id=request.application_request.request_id,
            request_fingerprint=request.fingerprint,
            candidates=(CapabilityType.KNOWLEDGE, CapabilityType.KNOWLEDGE),
            selected_capability=CapabilityType.KNOWLEDGE,
            matched_rule_ids=("knowledge-1",),
            reason_code="priority_match",
        )


def test_binding_reference_rejects_non_enum_async_mode() -> None:
    with pytest.raises(ValidationError):
        CapabilityBindingReference(
            capability=CapabilityType.KNOWLEDGE,
            binding_id="binding-1",
            binding_version="v4.3.2",
            async_mode="NATIVE_ASYNC",  # type: ignore[arg-type]
        )


def test_audit_contract_has_no_content_or_secret_fields() -> None:
    fields = set(CapabilityAuditEvent.model_fields)
    forbidden = {
        "question",
        "answer",
        "content",
        "prompt",
        "provider_payload",
        "credential",
        "api_key",
    }
    assert fields.isdisjoint(forbidden)


def test_model_dump_mutation_does_not_change_original() -> None:
    request = knowledge_request()
    dumped = copy.deepcopy(request.model_dump(mode="python"))
    dumped["application_request"]["project_id"] = "other-project"
    assert request.application_request.project_id == "project-1"
