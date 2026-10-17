from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot import (
    EngineeringPlan,
    PlanRisk,
    PlanRiskLevel,
    PlanStatus,
    approve_engineering_plan,
    transition_engineering_plan,
)
from embedded_copilot.engineering_requirements import (
    generate_engineering_plan,
    merge_requirement_context,
    RequirementStatus,
)


def _confirmed_context():
    partial = merge_requirement_context(
        None,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        utterance="我要做一个 STM32 小车，巡线，电池供电，直流电机",
    )
    return merge_requirement_context(
        partial,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-2",
        utterance="开始设计",
        confirmation_requested=True,
    )


def test_plan_is_phaseful_and_binds_risks_and_requirement_evidence() -> None:
    plan = generate_engineering_plan(_confirmed_context())

    assert plan.status is PlanStatus.PROPOSED
    assert {phase.name for phase in plan.phases} >= {
        "Requirement Confirmation",
        "System Architecture",
        "Hardware Design",
        "Firmware Development",
        "Simulation",
        "Verification",
    }
    assert any(risk.category == "POWER" and risk.impact is PlanRiskLevel.HIGH for risk in plan.plan_risks)
    hardware = next(phase for phase in plan.phases if phase.name == "Hardware Design")
    assert hardware.evidence_bindings
    assert all(binding.source_type == "REQUIREMENT_ITEM" for binding in hardware.evidence_bindings)
    assert all(binding.source_fingerprint.startswith("sha256:") for binding in hardware.evidence_bindings)
    assert plan.verification_strategy


def test_plan_children_are_strict_frozen_and_tamper_rejected() -> None:
    plan = generate_engineering_plan(_confirmed_context())
    with pytest.raises((TypeError, ValidationError)):
        plan.title = "changed"
    tampered = plan.model_dump(mode="python")
    tampered["plan_risks"][0]["description"] = "unbound fact"
    with pytest.raises(ValidationError, match="fingerprint"):
        EngineeringPlan.model_validate(tampered, strict=False)

    output_tampered = plan.model_dump(mode="python")
    output_tampered["phases"][1]["outputs"][0]["name"] = "unbound output"
    with pytest.raises(ValidationError, match="fingerprint"):
        EngineeringPlan.model_validate(output_tampered, strict=False)


def test_plan_completeness_gate_requires_goal_platform_function_and_criteria() -> None:
    partial = merge_requirement_context(
        None,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        utterance="STM32",
    )
    confirmed_without_scope = partial.model_copy(
        update={
            "status": RequirementStatus.CONFIRMED,
            "fingerprint": "sha256:" + "0" * 64,
        }
    )
    with pytest.raises(ValueError, match="completeness gate"):
        generate_engineering_plan(confirmed_without_scope)


def test_plan_status_machine_rejects_shortcuts_and_approval_binds_content() -> None:
    plan = generate_engineering_plan(_confirmed_context())
    draft_data = plan.model_dump(mode="python")
    draft_data["status"] = PlanStatus.DRAFT
    draft_data["fingerprint"] = "sha256:" + "0" * 64
    draft = EngineeringPlan.model_validate(draft_data, strict=False)
    with pytest.raises(ValueError, match="invalid engineering plan status transition"):
        transition_engineering_plan(draft, PlanStatus.EXECUTING)

    approved, review = approve_engineering_plan(
        plan,
        reviewer="user-1",
        decided_at=datetime(2026, 9, 2, tzinfo=UTC),
    )
    assert approved.status is PlanStatus.APPROVED
    assert review.proposal_fingerprint == plan.content_fingerprint
    assert review.project_id == plan.project_id

    approved_again, supplied_review = approve_engineering_plan(
        plan,
        review,
    )
    assert approved_again.status is PlanStatus.APPROVED
    assert supplied_review == review


def test_plan_risk_supports_product_id_alias_without_dropping_fingerprint() -> None:
    risk = PlanRisk(
        id="risk-1",
        plan_id="plan-1",
        project_id="project-1",
        category="POWER",
        description="motor current",
        impact=PlanRiskLevel.HIGH,
        probability=PlanRiskLevel.MEDIUM,
        mitigation="review regulator margin",
    )
    assert risk.id == "risk-1"
    assert risk.fingerprint.startswith("sha256:")
