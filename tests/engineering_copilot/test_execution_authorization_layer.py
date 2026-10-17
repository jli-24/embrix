from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot import (
    AuthorizationDecision,
    AuthorizationDecisionStatus,
    CapabilityApprovalRequirement,
    CapabilityApprovalStatus,
    CapabilityCategory,
    CapabilityEvaluation,
    CapabilityRiskLevel,
    EngineeringCapability,
    ExecutionAuthorization,
    ExecutionAuthorizationStatus,
    ExecutionIntent,
    ExecutionIntentStatus,
    ExecutionIntentType,
    ExecutionRequestStatus,
    ImplementationCapabilityBinding,
    evaluate_authorization,
    evaluate_capabilities,
    generate_execution_intent,
    generate_execution_requests,
    generate_implementation_spec,
    decide_execution_authorization_request,
)
from embedded_copilot.engineering_copilot.common import ZERO

from .test_implementation_planning_layer import _with_explicit_sources


def _authorization():
    specification = generate_implementation_spec(*_with_explicit_sources())
    evaluation = evaluate_capabilities(specification)
    intents = tuple(generate_execution_intent(task) for task in specification.tasks)
    requests = generate_execution_requests(
        evaluation,
        intents,
        conversation_id=specification.conversation_id,
        requested_by="engineer-1",
    )
    authorization = evaluate_authorization(evaluation, intents, requests)
    return specification, evaluation, intents, requests, authorization


def test_authorization_contract_is_strict_frozen_tuple_and_round_trips() -> None:
    _, _, _, _, authorization = _authorization()

    assert type(authorization.requests) is tuple
    assert type(authorization.policies) is tuple
    assert type(authorization.decisions) is tuple
    assert all(type(item.source_refs) is tuple for item in authorization.requests)
    assert (
        ExecutionAuthorization.model_validate_json(authorization.model_dump_json())
        == authorization
    )

    with pytest.raises((TypeError, ValidationError)):
        authorization.project_id = "project-other"

    extra = authorization.model_dump(mode="python")
    extra["unexpected"] = "forbidden"
    extra["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="unexpected|extra"):
        ExecutionAuthorization.model_validate(extra, strict=False)

    tampered = authorization.model_dump(mode="python")
    tampered["requests"][0]["request_reason"] = "tampered"
    tampered["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="fingerprint"):
        ExecutionAuthorization.model_validate(tampered, strict=False)

    invalid_state = authorization.model_dump(mode="python")
    invalid_state["requests"][0]["status"] = "AUTHORIZED"
    invalid_state["requests"][0]["fingerprint"] = ZERO
    invalid_state["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="pending authorization"):
        ExecutionAuthorization.model_validate(invalid_state, strict=False)


def test_pending_decision_has_no_reviewer_or_timestamp() -> None:
    _, _, _, requests, authorization = _authorization()

    assert authorization.status is ExecutionAuthorizationStatus.PENDING
    assert all(item.status is ExecutionRequestStatus.REQUESTED for item in requests)
    assert tuple(item.request_id for item in requests) == tuple(
        item.request_id for item in authorization.decisions
    )
    assert all(
        item.decision is AuthorizationDecisionStatus.PENDING
        and item.approved_by is None
        and item.timestamp_reference is None
        for item in authorization.decisions
    )

    with pytest.raises(ValidationError, match="reviewer|timestamp"):
        AuthorizationDecision.create(
            decision_id="authorization-decision-terminal",
            request_id=requests[0].request_id,
            decision=AuthorizationDecisionStatus.APPROVED,
            reason="Approved for future controlled processing.",
            approved_by=None,
            timestamp_reference=None,
        )

    terminal = AuthorizationDecision.create(
        decision_id="authorization-decision-terminal",
        request_id=requests[0].request_id,
        decision=AuthorizationDecisionStatus.APPROVED,
        reason="Approved for future controlled processing.",
        approved_by="reviewer-1",
        timestamp_reference=datetime(2026, 9, 4, tzinfo=UTC),
    )
    assert terminal.timestamp_reference == datetime(2026, 9, 4, tzinfo=UTC)


def test_service_is_deterministic_and_uses_fixed_policy_semantics() -> None:
    specification, evaluation, intents, requests, authorization = _authorization()
    repeated = generate_execution_requests(
        evaluation,
        intents,
        conversation_id=specification.conversation_id,
        requested_by="engineer-1",
    )

    assert requests == repeated
    assert len(requests) == sum(len(item.required_capabilities) for item in intents)
    assert tuple(item.intent_id for item in requests) == tuple(
        intent.intent_id
        for intent in intents
        for _ in intent.required_capabilities
    )
    assert authorization == evaluate_authorization(evaluation, intents, requests)
    policies = {item.capability_id: item for item in authorization.policies}
    assert policies["ARTIFACT_CREATION"].allowed is True
    assert policies["ARTIFACT_CREATION"].requires_user_confirmation is False
    assert policies["ARTIFACT_CREATION"].requires_review is False
    assert policies["FIRMWARE_CODE_GENERATION"].allowed is True
    assert policies["FIRMWARE_CODE_GENERATION"].requires_user_confirmation is True
    assert policies["FIRMWARE_CODE_GENERATION"].requires_review is False
    assert all(
        task.title not in request.request_reason
        for task in specification.tasks
        for request in requests
    )


def test_critical_capability_remains_pending_and_requires_review() -> None:
    capability = EngineeringCapability.create(
        capability_id="FLASH_DEVICE",
        name="Flash Device",
        category=CapabilityCategory.FIRMWARE,
        description="Restricted future device flash operation.",
        risk_level=CapabilityRiskLevel.CRITICAL,
        requires_approval=True,
        status="RESTRICTED",
        source_refs=("implementation-task-flash",),
    )
    binding = ImplementationCapabilityBinding.create(
        binding_id="binding-flash",
        implementation_task_id="implementation-task-flash",
        capability_id="FLASH_DEVICE",
        required=True,
        reason="Explicit test binding.",
    )
    intent = ExecutionIntent.create(
        intent_id="intent-flash",
        intent_type=ExecutionIntentType.FLASH_DEVICE,
        source_task_refs=("implementation-task-flash",),
        required_capabilities=("FLASH_DEVICE",),
        status=ExecutionIntentStatus.PLANNED,
    )
    approval = CapabilityApprovalRequirement.create(
        approval_id="approval-flash",
        intent_id=intent.intent_id,
        required_capabilities=("FLASH_DEVICE",),
        risk_level=CapabilityRiskLevel.CRITICAL,
        approval_status=CapabilityApprovalStatus.PENDING,
    )
    evaluation = CapabilityEvaluation.create(
        evaluation_id="evaluation-flash",
        project_id="project-flash",
        implementation_id="implementation-flash",
        implementation_fingerprint=f"sha256:{'b' * 64}",
        capabilities=(capability,),
        bindings=(binding,),
        approval_requirements=(approval,),
    )

    requests = generate_execution_requests(
        evaluation,
        (intent,),
        conversation_id="conversation-flash",
        requested_by="engineer-1",
    )
    authorization = evaluate_authorization(evaluation, (intent,), requests)

    assert authorization.status is ExecutionAuthorizationStatus.PENDING
    assert authorization.policies[0].allowed is True
    assert authorization.policies[0].requires_user_confirmation is True
    assert authorization.policies[0].requires_review is True
    assert authorization.decisions[0].decision is AuthorizationDecisionStatus.PENDING


def test_service_rejects_missing_intent_and_non_deterministic_request() -> None:
    _, evaluation, intents, requests, _ = _authorization()

    with pytest.raises(ValueError, match="intent|request"):
        generate_execution_requests(
            evaluation,
            intents[:-1],
            conversation_id="conversation-1",
            requested_by="engineer-1",
        )

    duplicate = ExecutionIntent.create(
        **{
            **intents[0].model_dump(mode="python"),
            "intent_id": "intent-duplicate-binding",
            "fingerprint": ZERO,
        }
    )
    with pytest.raises(ValueError, match="unique|binding"):
        generate_execution_requests(
            evaluation,
            (intents[0], duplicate, *intents[1:]),
            conversation_id="conversation-1",
            requested_by="engineer-1",
        )

    changed = requests[0].model_copy(
        update={"project_id": "project-foreign", "fingerprint": ZERO}
    )
    with pytest.raises(ValueError, match="project|deterministic|binding"):
        evaluate_authorization(evaluation, intents, (changed, *requests[1:]))


def test_single_request_decision_is_bound_and_preserves_pending_aggregate() -> None:
    _, evaluation, intents, requests, authorization = _authorization()
    decided_at = datetime(2026, 9, 4, 8, 30, tzinfo=UTC)

    approved = decide_execution_authorization_request(
        evaluation,
        intents,
        authorization,
        request_id=requests[0].request_id,
        decision=AuthorizationDecisionStatus.APPROVED,
        reviewer="engineer-1",
        decided_at=decided_at,
    )

    assert approved.requests[0].status is ExecutionRequestStatus.AUTHORIZED
    assert approved.decisions[0].decision is AuthorizationDecisionStatus.APPROVED
    assert approved.decisions[0].approved_by == "engineer-1"
    assert approved.decisions[0].timestamp_reference == decided_at
    assert approved.status is ExecutionAuthorizationStatus.PENDING
    assert all(
        request.status is ExecutionRequestStatus.REQUESTED
        for request in approved.requests[1:]
    )

    with pytest.raises(ValueError, match="request|binding"):
        decide_execution_authorization_request(
            evaluation,
            intents,
            authorization,
            request_id="execution-request-unknown",
            decision=AuthorizationDecisionStatus.APPROVED,
            reviewer="engineer-1",
            decided_at=decided_at,
        )


def test_all_request_decisions_reach_authorized_or_blocked() -> None:
    _, evaluation, intents, requests, authorization = _authorization()
    decided_at = datetime(2026, 9, 4, 8, 30, tzinfo=UTC)

    current = authorization
    for request in requests:
        current = decide_execution_authorization_request(
            evaluation,
            intents,
            current,
            request_id=request.request_id,
            decision=AuthorizationDecisionStatus.APPROVED,
            reviewer="engineer-1",
            decided_at=decided_at,
        )
    assert current.status is ExecutionAuthorizationStatus.AUTHORIZED

    rejected = decide_execution_authorization_request(
        evaluation,
        intents,
        authorization,
        request_id=requests[0].request_id,
        decision=AuthorizationDecisionStatus.REJECTED,
        reviewer="engineer-1",
        decided_at=decided_at,
    )
    for request in requests[1:]:
        rejected = decide_execution_authorization_request(
            evaluation,
            intents,
            rejected,
            request_id=request.request_id,
            decision=AuthorizationDecisionStatus.APPROVED,
            reviewer="engineer-1",
            decided_at=decided_at,
        )
    assert rejected.status is ExecutionAuthorizationStatus.BLOCKED
