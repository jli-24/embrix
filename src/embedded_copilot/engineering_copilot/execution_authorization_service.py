from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from .common import ident, validate_copy
from .contracts import (
    AuthorizationDecision,
    AuthorizationDecisionStatus,
    CapabilityEvaluation,
    CapabilityRiskLevel,
    ExecutionAuthorization,
    ExecutionAuthorizationStatus,
    ExecutionIntent,
    ExecutionPolicy,
    ExecutionRequest,
    ExecutionRequestStatus,
)


def _id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _intents(value: object) -> tuple[ExecutionIntent, ...]:
    if type(value) is not tuple:
        raise TypeError("execution_intents must be a tuple")
    checked = tuple(validate_copy(item, ExecutionIntent) for item in value)
    intent_ids = tuple(item.intent_id for item in checked)
    if not checked or len(intent_ids) != len(set(intent_ids)):
        raise ValueError("execution intent set is empty or contains duplicates")
    return checked


def _validate_intent_bindings(
    evaluation: CapabilityEvaluation,
    intents: tuple[ExecutionIntent, ...],
) -> None:
    capability_ids = {item.capability_id for item in evaluation.capabilities}
    expected_pairs = {
        (item.implementation_task_id, item.capability_id)
        for item in evaluation.bindings
    }
    actual_pairs = tuple(
        (task_id, capability_id)
        for intent in intents
        for task_id in intent.source_task_refs
        for capability_id in intent.required_capabilities
    )
    if len(actual_pairs) != len(set(actual_pairs)):
        raise ValueError("execution intent capability bindings must be unique")
    if expected_pairs != set(actual_pairs):
        raise ValueError("execution intent capability binding mismatch")
    if any(
        capability_id not in capability_ids
        for intent in intents
        for capability_id in intent.required_capabilities
    ):
        raise ValueError("execution intent references unknown capability")


def generate_execution_requests(
    capability_evaluation: CapabilityEvaluation,
    execution_intents: tuple[ExecutionIntent, ...],
    *,
    conversation_id: str,
    requested_by: str,
) -> tuple[ExecutionRequest, ...]:
    """Create declaration-only authorization requests from explicit capability state."""

    evaluation = validate_copy(capability_evaluation, CapabilityEvaluation)
    intents = _intents(execution_intents)
    conversation_id = ident(conversation_id, "conversation_id")
    requested_by = ident(requested_by, "requested_by")
    _validate_intent_bindings(evaluation, intents)
    capabilities = {item.capability_id: item for item in evaluation.capabilities}

    return tuple(
        ExecutionRequest.create(
            request_id=_id(
                "execution-request",
                evaluation.evaluation_id,
                evaluation.fingerprint,
                intent.intent_id,
                intent.fingerprint,
                capability_id,
                capabilities[capability_id].fingerprint,
                conversation_id,
                requested_by,
            ),
            intent_id=intent.intent_id,
            capability_id=capability_id,
            project_id=evaluation.project_id,
            conversation_id=conversation_id,
            requested_by=requested_by,
            request_reason=(
                f"Authorization requested for {intent.intent_type.value} using "
                f"{capability_id}."
            ),
            risk_level=capabilities[capability_id].risk_level,
            status=ExecutionRequestStatus.REQUESTED,
            source_refs=(
                evaluation.evaluation_id,
                intent.intent_id,
                capability_id,
            ),
        )
        for intent in intents
        for capability_id in intent.required_capabilities
    )


def _policy(
    evaluation: CapabilityEvaluation,
    capability_id: str,
) -> ExecutionPolicy:
    capability = next(
        item for item in evaluation.capabilities if item.capability_id == capability_id
    )
    confirmation = capability.risk_level is not CapabilityRiskLevel.LOW
    review = capability.risk_level is CapabilityRiskLevel.CRITICAL
    return ExecutionPolicy.create(
        policy_id=_id(
            "execution-policy",
            evaluation.evaluation_id,
            evaluation.fingerprint,
            capability.capability_id,
            capability.fingerprint,
            str(confirmation).lower(),
            str(review).lower(),
            "true",
        ),
        capability_id=capability.capability_id,
        risk_level=capability.risk_level,
        requires_user_confirmation=confirmation,
        requires_review=review,
        allowed=True,
    )


def evaluate_authorization(
    capability_evaluation: CapabilityEvaluation,
    execution_intents: tuple[ExecutionIntent, ...],
    execution_requests: tuple[ExecutionRequest, ...],
) -> ExecutionAuthorization:
    """Evaluate policy eligibility and stop at a deterministic pending decision."""

    evaluation = validate_copy(capability_evaluation, CapabilityEvaluation)
    intents = _intents(execution_intents)
    if type(execution_requests) is not tuple or not execution_requests:
        raise TypeError("execution_requests must be a non-empty tuple")
    requests = tuple(validate_copy(item, ExecutionRequest) for item in execution_requests)
    conversation_ids = {item.conversation_id for item in requests}
    requesters = {item.requested_by for item in requests}
    if len(conversation_ids) != 1 or len(requesters) != 1:
        raise ValueError("execution request conversation or requester binding mismatch")
    expected = generate_execution_requests(
        evaluation,
        intents,
        conversation_id=requests[0].conversation_id,
        requested_by=requests[0].requested_by,
    )
    if requests != expected:
        raise ValueError("execution requests are not deterministic or correctly bound")

    capability_ids = tuple(dict.fromkeys(item.capability_id for item in requests))
    policies = tuple(_policy(evaluation, capability_id) for capability_id in capability_ids)
    decisions = tuple(
        AuthorizationDecision.create(
            decision_id=_id(
                "authorization-decision",
                evaluation.evaluation_id,
                request.request_id,
                request.fingerprint,
            ),
            request_id=request.request_id,
            decision=AuthorizationDecisionStatus.PENDING,
            reason="Awaiting an explicit future authorization decision.",
            approved_by=None,
            timestamp_reference=None,
        )
        for request in requests
    )
    return ExecutionAuthorization.create(
        authorization_id=_id(
            "execution-authorization",
            evaluation.project_id,
            requests[0].conversation_id,
            evaluation.evaluation_id,
            evaluation.fingerprint,
            *(item.fingerprint for item in requests),
        ),
        project_id=evaluation.project_id,
        conversation_id=requests[0].conversation_id,
        capability_evaluation_id=evaluation.evaluation_id,
        requests=requests,
        policies=policies,
        decisions=decisions,
        status=ExecutionAuthorizationStatus.PENDING,
    )


def _validate_existing_authorization(
    evaluation: CapabilityEvaluation,
    intents: tuple[ExecutionIntent, ...],
    authorization: ExecutionAuthorization,
) -> None:
    expected_requests = generate_execution_requests(
        evaluation,
        intents,
        conversation_id=authorization.conversation_id,
        requested_by=authorization.requests[0].requested_by,
    )
    expected = evaluate_authorization(evaluation, intents, expected_requests)
    if (
        authorization.authorization_id != expected.authorization_id
        or authorization.project_id != expected.project_id
        or authorization.capability_evaluation_id != expected.capability_evaluation_id
        or authorization.policies != expected.policies
        or tuple(item.request_id for item in authorization.requests)
        != tuple(item.request_id for item in expected.requests)
        or tuple(item.request_id for item in authorization.decisions)
        != tuple(item.request_id for item in expected.decisions)
    ):
        raise ValueError("execution authorization binding mismatch")
    for current, base in zip(authorization.requests, expected.requests, strict=True):
        current_payload = current.model_dump(mode="python", exclude={"status", "fingerprint"})
        base_payload = base.model_dump(mode="python", exclude={"status", "fingerprint"})
        if current_payload != base_payload:
            raise ValueError("execution authorization request binding mismatch")
    expected_decisions = evaluate_authorization(evaluation, intents, expected_requests).decisions
    for current, base in zip(authorization.decisions, expected_decisions, strict=True):
        if current.decision_id != base.decision_id or current.request_id != base.request_id:
            raise ValueError("execution authorization decision identity mismatch")
        if current.decision is AuthorizationDecisionStatus.PENDING:
            if current.reason != base.reason:
                raise ValueError("pending authorization decision was modified")
        elif current.reason not in {
            "Explicit authorization approved by the trusted reviewer.",
            "Explicit authorization rejected by the trusted reviewer.",
        }:
            raise ValueError("authorization decision reason is not controlled")


def decide_execution_authorization_request(
    capability_evaluation: CapabilityEvaluation,
    execution_intents: tuple[ExecutionIntent, ...],
    authorization: ExecutionAuthorization,
    *,
    request_id: str,
    decision: AuthorizationDecisionStatus,
    reviewer: str,
    decided_at: datetime,
) -> ExecutionAuthorization:
    """Apply one explicit human decision without granting any execution by itself."""

    evaluation = validate_copy(capability_evaluation, CapabilityEvaluation)
    intents = _intents(execution_intents)
    current = validate_copy(authorization, ExecutionAuthorization)
    request_id = ident(request_id, "request_id")
    reviewer = ident(reviewer, "reviewer")
    if not isinstance(decision, AuthorizationDecisionStatus):
        decision = AuthorizationDecisionStatus(decision)
    if decision is AuthorizationDecisionStatus.PENDING:
        raise ValueError("terminal authorization decision is required")
    if not isinstance(decided_at, datetime) or decided_at.tzinfo is None or decided_at.utcoffset() is None:
        raise ValueError("decided_at must be timezone-aware")
    decided_at = decided_at.astimezone(UTC)
    _validate_existing_authorization(evaluation, intents, current)
    indices = tuple(index for index, item in enumerate(current.requests) if item.request_id == request_id)
    if len(indices) != 1:
        raise ValueError("authorization request binding mismatch")
    index = indices[0]
    old_decision = current.decisions[index]
    if old_decision.decision is not AuthorizationDecisionStatus.PENDING:
        if (
            old_decision.decision is decision
            and old_decision.approved_by == reviewer
            and old_decision.timestamp_reference == decided_at
        ):
            return current
        raise ValueError("authorization request already decided")

    request_status = (
        ExecutionRequestStatus.AUTHORIZED
        if decision is AuthorizationDecisionStatus.APPROVED
        else ExecutionRequestStatus.DENIED
    )
    requests = tuple(
        item
        if item.request_id != request_id
        else item.model_copy(update={"status": request_status, "fingerprint": "sha256:" + "0" * 64})
        for item in current.requests
    )
    decisions = tuple(
        item
        if item.request_id != request_id
        else AuthorizationDecision.create(
            decision_id=item.decision_id,
            request_id=item.request_id,
            decision=decision,
            reason=(
                "Explicit authorization approved by the trusted reviewer."
                if decision is AuthorizationDecisionStatus.APPROVED
                else "Explicit authorization rejected by the trusted reviewer."
            ),
            approved_by=reviewer,
            timestamp_reference=decided_at,
        )
        for item in current.decisions
    )
    if any(item.decision is AuthorizationDecisionStatus.PENDING for item in decisions):
        aggregate = ExecutionAuthorizationStatus.PENDING
    elif all(item.decision is AuthorizationDecisionStatus.APPROVED for item in decisions):
        aggregate = ExecutionAuthorizationStatus.AUTHORIZED
    else:
        aggregate = ExecutionAuthorizationStatus.BLOCKED
    return ExecutionAuthorization.create(
        authorization_id=current.authorization_id,
        project_id=current.project_id,
        conversation_id=current.conversation_id,
        capability_evaluation_id=current.capability_evaluation_id,
        requests=requests,
        policies=current.policies,
        decisions=decisions,
        status=aggregate,
    )


def validate_execution_authorization(
    capability_evaluation: CapabilityEvaluation,
    execution_intents: tuple[ExecutionIntent, ...],
    authorization: ExecutionAuthorization,
) -> ExecutionAuthorization:
    evaluation = validate_copy(capability_evaluation, CapabilityEvaluation)
    intents = _intents(execution_intents)
    checked = validate_copy(authorization, ExecutionAuthorization)
    _validate_existing_authorization(evaluation, intents, checked)
    return checked


__all__ = (
    "decide_execution_authorization_request",
    "evaluate_authorization",
    "generate_execution_requests",
    "validate_execution_authorization",
)
