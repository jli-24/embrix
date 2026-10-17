from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot import (
    ArchitectureConfidence,
    ArchitectureDecisionType,
    ArchitectureDesign,
    ArchitectureDesignStatus,
    ArchitectureLevel,
    ArchitectureReviewDecision,
    ArchitectureReviewFinding,
    ArchitectureReviewFindingStatus,
    ArchitectureReviewStatus,
    approve_architecture_design,
    approve_engineering_plan,
    transition_architecture_design,
)
from embedded_copilot.engineering_requirements import (
    generate_architecture_design,
    generate_engineering_plan,
    merge_requirement_context,
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


def _approved_plan():
    context = _confirmed_context()
    proposed = generate_engineering_plan(context)
    plan, review = approve_engineering_plan(
        proposed,
        reviewer="engineer-1",
        decided_at=datetime(2026, 9, 2, tzinfo=UTC),
    )
    return context, plan, review


def test_architecture_is_traceable_to_requirements_and_acceptance() -> None:
    context, plan, _ = _approved_plan()
    design = generate_architecture_design(context, plan)

    assert design.status is ArchitectureDesignStatus.PROPOSED
    assert design.plan_id == plan.plan_id
    assert design.plan_fingerprint == plan.fingerprint
    assert design.system_components
    assert design.boundary.hardware_component_ids
    assert design.boundary.software_component_ids
    assert any(item.name == "STM32" for item in design.system_components)
    assert any(item.decision_type is ArchitectureDecisionType.HARDWARE_SELECTION for item in design.decisions)
    assert all(item.alternatives for item in design.decisions)
    assert all(item.level in set(ArchitectureLevel) for item in design.system_components)
    assert all(item.confidence in set(ArchitectureConfidence) for item in design.system_components)

    binding_ids = {item.binding_id for item in design.evidence_bindings}
    requirement_ids = {item.requirement_id for item in (*context.requirements, *context.constraints)}
    criterion_ids = {item.criterion_id for item in context.acceptance_criteria}
    assert binding_ids
    assert all(
        set(element.evidence_binding_ids) <= binding_ids
        for element in (
            *design.system_components,
            *design.interfaces,
            design.boundary,
            *design.data_flow,
            *design.control_flow,
            *design.decisions,
            *design.verification,
        )
    )
    assert all(set(binding.requirement_ids) <= requirement_ids for binding in design.evidence_bindings)
    assert all(set(binding.acceptance_criterion_ids) <= criterion_ids for binding in design.evidence_bindings)
    assert all(binding.requirement_ids and binding.acceptance_criterion_ids for binding in design.evidence_bindings)


def test_architecture_children_are_frozen_and_tamper_rejected() -> None:
    context, plan, _ = _approved_plan()
    design = generate_architecture_design(context, plan)

    with pytest.raises((TypeError, ValidationError)):
        design.title = "changed"

    tampered = design.model_dump(mode="python")
    tampered["system_components"][0]["name"] = "unbound component"
    with pytest.raises(ValidationError, match="fingerprint"):
        ArchitectureDesign.model_validate(tampered, strict=False)


def test_architecture_contract_rejects_invalid_references_and_level_overlap() -> None:
    context, plan, _ = _approved_plan()
    design = generate_architecture_design(context, plan)

    invalid = design.model_dump(mode="python")
    invalid["interfaces"][0]["source_component_id"] = "missing-component"
    invalid["fingerprint"] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError, match="component reference"):
        ArchitectureDesign.model_validate(invalid, strict=False)

    invalid = design.model_dump(mode="python")
    invalid["boundary"]["software_component_ids"] = invalid["boundary"]["hardware_component_ids"]
    invalid["fingerprint"] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError, match="boundary"):
        ArchitectureDesign.model_validate(invalid, strict=False)


def test_architecture_state_machine_and_review_findings_are_fail_closed() -> None:
    context, plan, _ = _approved_plan()
    design = generate_architecture_design(context, plan)

    draft_data = design.model_dump(mode="python")
    draft_data["status"] = ArchitectureDesignStatus.DRAFT
    draft_data["fingerprint"] = "sha256:" + "0" * 64
    draft = ArchitectureDesign.model_validate(draft_data, strict=False)
    with pytest.raises(ValueError, match="invalid architecture design status transition"):
        transition_architecture_design(draft, ArchitectureDesignStatus.APPROVED)

    review_finding = ArchitectureReviewFinding.create(
        finding_id="finding-1",
        architecture_id=design.architecture_id,
        project_id=design.project_id,
        category="POWER",
        description="power budget is unclear",
        status=ArchitectureReviewFindingStatus.OPEN,
        evidence_binding_ids=(design.evidence_bindings[0].binding_id,),
        provenance=design.provenance,
    )
    review = ArchitectureReviewDecision.create(
        architecture_id=design.architecture_id,
        plan_id=design.plan_id,
        project_id=design.project_id,
        proposal_fingerprint=design.content_fingerprint,
        status=ArchitectureReviewStatus.APPROVED,
        reviewer="engineer-1",
        decided_at=datetime(2026, 9, 2, tzinfo=UTC),
        findings=(review_finding,),
    )
    with pytest.raises(ValueError, match="open architecture review findings"):
        approve_architecture_design(design, review)

    approved, approved_review = approve_architecture_design(
        design,
        reviewer="engineer-1",
        decided_at=datetime(2026, 9, 2, tzinfo=UTC),
    )
    assert approved.status is ArchitectureDesignStatus.APPROVED
    assert approved_review.proposal_fingerprint == design.content_fingerprint


def test_architecture_generation_requires_approved_plan() -> None:
    context = _confirmed_context()
    plan = generate_engineering_plan(context)
    with pytest.raises(ValueError, match="approved engineering plan"):
        generate_architecture_design(context, plan)


def test_invalidated_is_reserved_without_an_application_transition() -> None:
    assert ArchitectureDesignStatus.INVALIDATED.value == "INVALIDATED"
    context, plan, _ = _approved_plan()
    design = generate_architecture_design(context, plan)
    with pytest.raises(ValueError, match="invalid architecture design status transition"):
        transition_architecture_design(design, ArchitectureDesignStatus.INVALIDATED)
