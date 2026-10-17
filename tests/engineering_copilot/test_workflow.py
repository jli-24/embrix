from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot import (
    ArchitectureDesignStatus,
    EngineeringWorkflow,
    EngineeringWorkflowItemStatus,
    EngineeringWorkflowStatus,
    EngineeringWorkflowTaskType,
    PlanStatus,
    approve_architecture_design,
    approve_engineering_plan,
    generate_engineering_workflow,
    transition_workflow_status,
)
from embedded_copilot.engineering_requirements import (
    generate_architecture_design,
    generate_engineering_plan,
    merge_requirement_context,
)

NOW = datetime(2026, 9, 3, tzinfo=UTC)


def _approved_sources():
    partial = merge_requirement_context(
        None,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        utterance="我要做一个 STM32 小车，巡线，电池供电，直流电机",
    )
    context = merge_requirement_context(
        partial,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-2",
        utterance="开始设计",
        confirmation_requested=True,
    )
    proposed_plan = generate_engineering_plan(context)
    plan, _ = approve_engineering_plan(proposed_plan, reviewer="engineer-1", decided_at=NOW)
    proposed_architecture = generate_architecture_design(context, plan)
    architecture, _ = approve_architecture_design(
        proposed_architecture,
        reviewer="engineer-1",
        decided_at=NOW,
    )
    return plan, architecture


def test_workflow_is_deterministic_and_source_driven() -> None:
    plan, architecture = _approved_sources()
    workflow = generate_engineering_workflow(architecture, plan)
    again = generate_engineering_workflow(architecture, plan)

    assert workflow == again
    assert workflow.status is EngineeringWorkflowStatus.PLANNED
    assert workflow.current_phase_id == next(
        phase.phase_id
        for phase in workflow.phases
        if phase.status not in {
            EngineeringWorkflowItemStatus.COMPLETED,
            EngineeringWorkflowItemStatus.CANCELLED,
        }
    )
    phase_types = {phase.phase_type.value for phase in workflow.phases}
    assert phase_types == {
        "REQUIREMENT_CONFIRMATION",
        "SYSTEM_DESIGN",
        "HARDWARE_DESIGN",
        "FIRMWARE_DEVELOPMENT",
        "SIMULATION",
        "VERIFICATION",
    }
    assert all(task.description for phase in workflow.phases for task in phase.tasks)
    assert sum(
        task.task_type is EngineeringWorkflowTaskType.TEST
        for phase in workflow.phases
        for task in phase.tasks
    ) == 3
    assert all(output.artifact_type.value != "PCB" for phase in workflow.phases for output in phase.outputs)
    assert all(
        output.source_refs == (source_ref,)
        for phase in workflow.phases
        for output in phase.outputs
        for source_ref in output.source_refs
    )
    assert all(
        task.expected_outputs == task.output_refs
        and task.verification_requirements == task.verification_refs
        for phase in workflow.phases
        for task in phase.tasks
    )
    assert any(phase.verification_refs for phase in workflow.phases)


def test_workflow_accepts_json_arrays_and_rejects_nested_tampering() -> None:
    plan, architecture = _approved_sources()
    workflow = generate_engineering_workflow(architecture, plan)
    restored = type(workflow).model_validate(json.loads(workflow.model_dump_json()), strict=False)
    assert restored == workflow
    assert type(restored.phases) is tuple
    assert all(type(phase.tasks) is tuple for phase in restored.phases)

    tampered = workflow.model_dump(mode="python")
    tampered["phases"][0]["tasks"][0]["title"] = "执行自动构建"
    tampered["fingerprint"] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError, match="fingerprint"):
        type(workflow).model_validate(tampered, strict=False)

    extra = workflow.model_dump(mode="python")
    extra["unexpected"] = "forbidden"
    extra["fingerprint"] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError, match="extra"):
        EngineeringWorkflow.model_validate(extra, strict=False)
    with pytest.raises((TypeError, ValidationError)):
        workflow.status = EngineeringWorkflowStatus.IN_PROGRESS


def test_workflow_rejects_unapproved_or_inconsistent_sources() -> None:
    plan, architecture = _approved_sources()
    proposed_plan_data = plan.model_dump(mode="python")
    proposed_plan_data["status"] = PlanStatus.PROPOSED
    proposed_plan_data["fingerprint"] = "sha256:" + "0" * 64
    proposed_plan = type(plan).model_validate(proposed_plan_data, strict=False)
    with pytest.raises(ValueError, match="approved"):
        generate_engineering_workflow(architecture, proposed_plan)

    wrong_project = architecture.model_copy(
        update={"project_id": "project-2", "fingerprint": "sha256:" + "0" * 64}
    )
    with pytest.raises((ValueError, ValidationError)):
        generate_engineering_workflow(wrong_project, plan)

    wrong_plan_binding = architecture.model_copy(
        update={"plan_fingerprint": "sha256:" + "1" * 64, "fingerprint": "sha256:" + "0" * 64}
    )
    with pytest.raises((ValueError, ValidationError)):
        generate_engineering_workflow(wrong_plan_binding, plan)


def test_workflow_status_machine_is_top_level_only() -> None:
    plan, architecture = _approved_sources()
    workflow = generate_engineering_workflow(architecture, plan)
    started = transition_workflow_status(workflow, EngineeringWorkflowStatus.IN_PROGRESS)
    assert started.status is EngineeringWorkflowStatus.IN_PROGRESS
    assert started.phases == workflow.phases

    blocked = transition_workflow_status(started, EngineeringWorkflowStatus.BLOCKED)
    resumed = transition_workflow_status(blocked, EngineeringWorkflowStatus.IN_PROGRESS)
    assert resumed.status is EngineeringWorkflowStatus.IN_PROGRESS
    with pytest.raises(ValueError, match="phase"):
        transition_workflow_status(resumed, EngineeringWorkflowStatus.COMPLETED)
    cancelled = transition_workflow_status(resumed, EngineeringWorkflowStatus.CANCELLED)
    with pytest.raises(ValueError, match="transition"):
        transition_workflow_status(cancelled, EngineeringWorkflowStatus.IN_PROGRESS)

    assert architecture.status is ArchitectureDesignStatus.APPROVED
