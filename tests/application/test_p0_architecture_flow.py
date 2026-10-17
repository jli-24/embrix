from __future__ import annotations

import pytest
from pydantic import ValidationError

from embedded_copilot.application import ApplicationRoutingContext, ApplicationRunStatus
from embedded_copilot.engineering_copilot import ArchitectureDesignStatus, DesignTruth
from embedded_copilot.engineering_requirements import generate_engineering_plan

from .fixtures import NOW, request
from .test_p0_plan_flow import _confirmed_requirement, _runtime


def test_approved_plan_generates_architecture_proposal_then_requires_architecture_approval() -> None:
    requirement = _confirmed_requirement()
    proposed = generate_engineering_plan(requirement)
    runtime = _runtime("开始系统设计")
    from embedded_copilot.engineering_copilot import approve_engineering_plan

    plan, plan_review = approve_engineering_plan(
        proposed, reviewer="engineer-1", decided_at=NOW
    )
    first = runtime.answer(
        request(question="开始系统设计", request_id="architecture-request-1"),
        routing_context=ApplicationRoutingContext(
            project_creation_mode="CREATE_NEW",
            conversation_id=requirement.conversation_id,
            requirement_context=requirement,
            engineering_plan=plan,
            engineering_plan_review=plan_review,
        ),
    )
    assert first.status is ApplicationRunStatus.COMPLETED
    assert first.engineering_plan is not None
    assert first.engineering_plan.status.value == "APPROVED"
    assert first.architecture_design is not None
    assert first.architecture_design.status is ArchitectureDesignStatus.PROPOSED
    assert first.architecture_review is None
    assert first.design_truth is None

    second = _runtime("批准架构").answer(
        request(question="批准架构", request_id="architecture-request-2"),
        routing_context=ApplicationRoutingContext(
            project_creation_mode="CREATE_NEW",
            conversation_id=requirement.conversation_id,
            requirement_context=requirement,
            engineering_plan=plan,
            engineering_plan_review=plan_review,
            architecture_design=first.architecture_design,
        ),
    )
    assert second.status is ApplicationRunStatus.COMPLETED
    assert second.architecture_design is not None
    assert second.architecture_design.status is ArchitectureDesignStatus.APPROVED
    assert second.architecture_review is not None
    assert second.design_truth is not None
    assert second.design_truth.project_id == requirement.project_id
    assert second.design_truth.architecture_id == second.architecture_design.architecture_id
    assert second.design_truth.pins == ()
    assert second.design_truth.firmware_bindings == ()
    assert second.gateway_invocation_count == 0
    assert second.provider_response_present is False

    mismatched_data = second.design_truth.model_dump(mode="python")
    mismatched_data["architecture_id"] = "different-architecture"
    mismatched_data["fingerprint"] = "sha256:" + "0" * 64
    mismatched_truth = DesignTruth.model_validate(mismatched_data, strict=False)
    with pytest.raises(ValidationError, match="architecture mismatch"):
        ApplicationRoutingContext(
            project_creation_mode="CREATE_NEW",
            conversation_id=requirement.conversation_id,
            requirement_context=requirement,
            engineering_plan=plan,
            engineering_plan_review=plan_review,
            architecture_design=second.architecture_design,
            architecture_review=second.architecture_review,
            design_truth=mismatched_truth,
        )


def test_architecture_approval_without_current_proposal_fails_closed_without_provider() -> None:
    requirement = _confirmed_requirement()
    proposed = generate_engineering_plan(requirement)
    from embedded_copilot.engineering_copilot import approve_engineering_plan

    plan, plan_review = approve_engineering_plan(
        proposed, reviewer="engineer-1", decided_at=NOW
    )
    result = _runtime("批准架构").answer(
        request(question="批准架构", request_id="architecture-request-4"),
        routing_context=ApplicationRoutingContext(
            project_creation_mode="CREATE_NEW",
            conversation_id=requirement.conversation_id,
            requirement_context=requirement,
            engineering_plan=plan,
            engineering_plan_review=plan_review,
        ),
    )
    assert result.status is ApplicationRunStatus.REJECTED
    assert result.error_code.value == "CLARIFICATION_REQUIRED"
    assert result.gateway_invocation_count == 0


def test_provider_failure_does_not_remove_deterministic_architecture_proposal() -> None:
    requirement = _confirmed_requirement()
    proposed = generate_engineering_plan(requirement)
    from embedded_copilot.engineering_copilot import approve_engineering_plan

    plan, plan_review = approve_engineering_plan(
        proposed, reviewer="engineer-1", decided_at=NOW
    )
    result = _runtime("开始系统设计", failure=RuntimeError("provider unavailable")).answer(
        request(question="开始系统设计", request_id="architecture-request-3"),
        routing_context=ApplicationRoutingContext(
            project_creation_mode="CREATE_NEW",
            conversation_id=requirement.conversation_id,
            requirement_context=requirement,
            engineering_plan=plan,
            engineering_plan_review=plan_review,
        ),
    )
    assert result.status is ApplicationRunStatus.FAILED
    assert result.architecture_design is not None
    assert result.architecture_design.status is ArchitectureDesignStatus.PROPOSED
    assert result.design_truth is None
