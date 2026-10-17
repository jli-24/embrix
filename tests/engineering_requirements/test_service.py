import pytest

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.engineering_requirements import (
    RequirementContext,
    RequirementItemStatus,
    RequirementLifecycle,
    RequirementStatus,
    build_engineering_plan,
    merge_requirement_context,
    resolve_requirement_conflict,
    transition_requirement,
)


def test_user_facts_are_confirmed_and_unknown_is_allowed_without_form_completion() -> None:
    context = merge_requirement_context(
        None,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        utterance="STM32，只控制加热，精度 ±0.5°C",
    )

    assert context.lifecycle is RequirementLifecycle.REQUIREMENTS_PARTIAL
    assert {(item.category, item.normalized_value, item.status) for item in context.requirements} >= {
        ("TARGET_PLATFORM", "STM32", RequirementItemStatus.CONFIRMED),
        ("CONTROL_MODE", "HEATING", RequirementItemStatus.CONFIRMED),
        ("ACCURACY", "±0.5°C", RequirementItemStatus.CONFIRMED),
    }
    assert any(field.category == "PRODUCT_GOAL" and field.status is RequirementItemStatus.UNKNOWN for field in context.standard_fields)


def test_provider_proposal_is_not_confirmed_and_explicit_user_confirmation_supersedes_it() -> None:
    proposed = merge_requirement_context(
        None,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        utterance="stm32",
        proposals=({"category": "RTOS", "value": "FreeRTOS"},),
    )
    item = proposed.proposed_items[0]
    assert item.status is RequirementItemStatus.PROPOSED
    assert not any(value.category == "RTOS" and value.status is RequirementItemStatus.CONFIRMED for value in proposed.requirements)

    confirmed = merge_requirement_context(
        proposed,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-2",
        utterance="可以，就用 FreeRTOS",
    )
    assert any(value.category == "RTOS" and value.status is RequirementItemStatus.CONFIRMED for value in confirmed.requirements)
    assert any(value.requirement_id == item.requirement_id and value.status is RequirementItemStatus.SUPERSEDED for value in confirmed.proposed_items)


def test_replacing_a_confirmed_value_records_predecessor_and_replacement() -> None:
    first = merge_requirement_context(
        None,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        utterance="使用 FreeRTOS",
    )
    second = merge_requirement_context(
        first,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-2",
        utterance="改用 Zephyr",
    )
    old = next(item for item in second.requirements if item.status is RequirementItemStatus.SUPERSEDED)
    new = next(item for item in second.requirements if item.status is RequirementItemStatus.CONFIRMED and item.category == "RTOS")
    assert old.supersedes is None
    assert new.supersedes == old.requirement_id
    assert old.superseded_by == new.requirement_id


def test_smart_car_incremental_functions_generate_acceptance_criteria_and_questions() -> None:
    first = merge_requirement_context(
        None,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        utterance="我要做一个 STM32 智能小车",
    )
    second = merge_requirement_context(
        first,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-2",
        utterance="必须支持巡线和避障",
    )

    assert {item.normalized_value for item in second.requirements if item.category == "FUNCTIONAL"} == {
        "LINE_FOLLOWING",
        "OBSTACLE_AVOIDANCE",
    }
    assert len(second.acceptance_criteria) == 2
    assert second.status is RequirementStatus.CLARIFICATION_REQUIRED
    assert {item.category for item in second.open_questions} >= {"POWER_SOURCE", "MOTOR"}


def test_provider_proposal_and_moscow_priority_do_not_become_user_facts() -> None:
    context = merge_requirement_context(
        None,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        utterance="必须使用 STM32",
        proposals=({"category": "RTOS", "value": "FreeRTOS"},),
    )

    platform = next(item for item in context.requirements if item.category == "TARGET_PLATFORM")
    assert platform.priority.value == "MUST"
    assert context.proposed_items[0].status is RequirementItemStatus.PROPOSED


def test_conflict_confirmation_gate_and_invalid_transition_fail_closed() -> None:
    context = merge_requirement_context(
        None,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        utterance="我要做一个 STM32 小车，巡线，低功耗，续航 10 小时",
    )
    assert context.conflicts
    assert context.status is RequirementStatus.CLARIFICATION_REQUIRED
    with pytest.raises(ValueError, match="confirmation gate"):
        transition_requirement(context, RequirementStatus.CONFIRMED, explicit_confirmation=True)
    archived = transition_requirement(context, RequirementStatus.ARCHIVED)
    with pytest.raises(ValueError, match="invalid requirement status transition"):
        transition_requirement(archived, RequirementStatus.ANALYZING)


def test_user_resolution_closes_a_conflict_and_recomputes_fingerprints() -> None:
    context = merge_requirement_context(
        None,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        utterance="我要做一个 STM32 小车，低功耗，续航 10 小时",
    )
    resolved = resolve_requirement_conflict(
        context,
        context.conflicts[0].conflict_id,
        "以续航为优先，后续验证功耗预算",
        turn_id="turn-2",
    )
    assert resolved.conflicts[0].status.value == "RESOLVED"
    assert resolved.conflicts[0].resolution
    assert not resolved.fingerprint.endswith("0" * 64)


def test_confirmation_and_existing_engineering_plan_adapter_are_explicit() -> None:
    context = merge_requirement_context(
        None,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        utterance="我要做一个 STM32 小车，巡线，电池供电，直流电机",
    )
    confirmed = merge_requirement_context(
        context,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-2",
        utterance="开始设计",
        confirmation_requested=True,
    )
    assert confirmed.status is RequirementStatus.CONFIRMED
    plan = build_engineering_plan(confirmed, EngineeringContextSnapshot.create(
        project_id="project-1",
        task_description="STM32 小车",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.0,
        provenance=(),
        verification_status="APPROVED",
    ))
    assert plan.project_id == "project-1"
    assert plan.tasks[0].title == "分析工程需求"
