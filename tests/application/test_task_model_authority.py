from __future__ import annotations

from datetime import UTC, datetime

import pytest

from embedded_copilot.application.task_model_authority import CatalogTaskAuthoritySource
from embedded_copilot.engineering_copilot import (
    approve_architecture_design,
    approve_engineering_plan,
    generate_engineering_workflow,
)
from embedded_copilot.engineering_copilot.contracts import (
    EngineeringPlan,
    PlanPhaseStatus,
    PlanReviewStatus,
    PlanStatus,
    PlanTaskStatus,
)
from embedded_copilot.engineering_requirements import (
    generate_architecture_design,
    generate_engineering_plan,
    merge_requirement_context,
)
from embedded_copilot.model_gateway.invocation.contracts import TaskAuthorityFailure
from embedded_copilot.project_catalog.models import ConversationDetail


def _detail() -> ConversationDetail:
    partial = merge_requirement_context(
        None, project_id="project-1", conversation_id="conversation-1", turn_id="turn-1",
        utterance="我要做一个 STM32 小车，巡线，电池供电，直流电机",
    )
    requirement = merge_requirement_context(
        partial, project_id="project-1", conversation_id="conversation-1", turn_id="turn-2",
        utterance="开始设计", confirmation_requested=True,
    )
    plan, review = approve_engineering_plan(
        generate_engineering_plan(requirement), reviewer="user-1", decided_at=datetime(2026, 9, 2, tzinfo=UTC),
    )
    architecture, architecture_review = approve_architecture_design(
        generate_architecture_design(requirement, plan), reviewer="user-1", decided_at=datetime(2026, 9, 2, tzinfo=UTC),
    )
    workflow = generate_engineering_workflow(architecture, plan)
    return ConversationDetail(
        conversation_id="conversation-1", project_id="project-1", tenant_id="tenant-1", requester="user-1",
        title="task", turns=(), created_at=datetime(2026, 9, 2, tzinfo=UTC),
        updated_at=datetime(2026, 9, 2, tzinfo=UTC), requirement_context=requirement,
        engineering_plan=plan, engineering_plan_review=review,
        architecture_design=architecture, architecture_review=architecture_review, engineering_workflow=workflow,
    )


class Catalog:
    def __init__(self, detail: ConversationDetail) -> None:
        self.detail = detail
        self.reads = 0

    def conversation(self, project_id, conversation_id, *, tenant_id, requester):
        self.reads += 1
        if (project_id, conversation_id, tenant_id, requester) != ("project-1", "conversation-1", "tenant-1", "user-1"):
            raise ValueError("owner mismatch")
        return self.detail


def _source(catalog: Catalog) -> CatalogTaskAuthoritySource:
    return CatalogTaskAuthoritySource(
        catalog, tenant_id="tenant-1", requester="user-1", project_id="project-1",
        conversation_id="conversation-1", plan_id=catalog.detail.engineering_plan.plan_id, phase_id="phase-hardware-design",
        plan_task_id="phase-hardware-design-task-1", operation_id="HARDWARE_SELECTION_PROPOSAL",
    )


def test_current_catalog_task_authority_is_rederived_on_every_validation() -> None:
    catalog = Catalog(_detail())
    source = _source(catalog)
    authority, stage = source.resolve_current()
    assert stage.value == "HARDWARE_SELECTION"
    assert authority.engineering_task_id.startswith("model-task-")
    before = catalog.reads
    assert source.revalidate(authority) == authority
    assert catalog.reads > before


def test_plan_replacement_or_requirement_change_invalidates_task_authority() -> None:
    catalog = Catalog(_detail())
    source = _source(catalog)
    authority, _ = source.resolve_current()
    catalog.detail = catalog.detail.model_copy(update={"engineering_plan": None})
    with pytest.raises(TaskAuthorityFailure):
        source.revalidate(authority)


def test_unlisted_operation_never_authorizes_model() -> None:
    catalog = Catalog(_detail())
    with pytest.raises(TaskAuthorityFailure, match="TASK_MODEL_OPERATION_UNAVAILABLE"):
        CatalogTaskAuthoritySource(
            catalog, tenant_id="tenant-1", requester="user-1", project_id="project-1",
            conversation_id="conversation-1", plan_id=catalog.detail.engineering_plan.plan_id, phase_id="phase-hardware-design",
            plan_task_id="phase-hardware-design-task-2", operation_id="HARDWARE_SELECTION_PROPOSAL",
        ).resolve_current()


def test_v1_plan_never_authorizes_model() -> None:
    detail = _detail()
    plan_payload = detail.engineering_plan.model_dump(
        mode="python",
        exclude={"fingerprint", "model_operation_schema_version", "model_operation_bindings"},
    )
    legacy_plan, legacy_review = approve_engineering_plan(
        EngineeringPlan.create(**{**plan_payload, "status": PlanStatus.PROPOSED}),
        reviewer="user-1", decided_at=datetime(2026, 9, 2, tzinfo=UTC),
    )
    legacy_detail = ConversationDetail(**{
        **detail.model_dump(mode="python", exclude={"fingerprint"}),
        "engineering_plan": legacy_plan,
        "engineering_plan_review": legacy_review,
        "architecture_design": None,
        "architecture_review": None,
        "engineering_workflow": None,
    })
    with pytest.raises(TaskAuthorityFailure, match="TASK_MODEL_OPERATION_UNAVAILABLE"):
        _source(Catalog(legacy_detail)).resolve_current()


@pytest.mark.parametrize("mutation", ["review", "phase", "task", "binding", "architecture", "workflow", "owner"])
def test_current_source_change_invalidates_existing_reference(mutation: str) -> None:
    catalog = Catalog(_detail())
    source = _source(catalog)
    authority, _ = source.resolve_current()
    detail = catalog.detail
    if mutation == "review":
        catalog.detail = detail.model_copy(update={"engineering_plan_review": detail.engineering_plan_review.model_copy(update={"status": PlanReviewStatus.REJECTED})})
    elif mutation in {"phase", "task", "binding"}:
        plan = detail.engineering_plan
        if mutation == "binding":
            plan = plan.model_copy(update={"model_operation_bindings": ()})
        else:
            phases = list(plan.phases)
            index = next(index for index, phase in enumerate(phases) if phase.phase_id == "phase-hardware-design")
            phase = phases[index]
            if mutation == "phase":
                phase = phase.model_copy(update={"status": PlanPhaseStatus.COMPLETED})
            else:
                tasks = list(phase.tasks)
                tasks[0] = tasks[0].model_copy(update={"status": PlanTaskStatus.COMPLETED})
                phase = phase.model_copy(update={"tasks": tuple(tasks)})
            phases[index] = phase
            plan = plan.model_copy(update={"phases": tuple(phases)})
        catalog.detail = detail.model_copy(update={"engineering_plan": plan})
    elif mutation in {"architecture", "workflow"}:
        if mutation == "architecture":
            catalog.detail = detail.model_copy(update={"architecture_design": detail.architecture_design.model_copy(update={"plan_fingerprint": "sha256:" + "a" * 64})})
        else:
            catalog.detail = detail.model_copy(update={"engineering_workflow": detail.engineering_workflow.model_copy(update={"plan_id": "obsolete-plan"})})
    else:
        catalog.detail = detail.model_copy(update={"requester": "another-owner"})
    with pytest.raises(TaskAuthorityFailure):
        source.revalidate(authority)
