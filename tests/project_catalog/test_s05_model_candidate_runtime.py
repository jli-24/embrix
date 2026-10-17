"""Offline Catalog persistence with the two real governed candidate validators."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from embedded_copilot.application.task_model_authority import CatalogTaskAuthoritySource
from embedded_copilot.engineering_copilot import (
    ArchitectureDesign,
    ArchitectureDesignStatus,
    ArchitectureInterface,
    ArchitectureInterfaceType,
    DesignTruthBuilder,
    HardwareSelectionDecision,
    HardwareSelectionService,
    approve_architecture_design,
    approve_engineering_plan,
    generate_engineering_workflow,
)
from embedded_copilot.engineering_copilot.schematic_planning import (
    SchematicPlanningBlocked,
)
from embedded_copilot.engineering_requirements import (
    generate_architecture_design,
    generate_engineering_plan,
    merge_requirement_context,
)
from embedded_copilot.project_catalog import (
    ProjectCreationMode,
    ProjectCreationRequest,
    ProjectLocationReference,
    ProjectType,
)
from embedded_copilot.project_catalog.exceptions import SchematicPlanningRejected
from embedded_copilot.project_catalog.models import ConversationDetail
from embedded_copilot.project_catalog.runtime import ProjectCatalogRuntime
from tests.engineering_copilot.test_hardware_selection_g1 import (
    _catalog as hardware_catalog,
)
from tests.engineering_copilot.test_schematic_planning_g2 import (
    _catalog as schematic_catalog,
)

NOW = datetime(2026, 9, 2, tzinfo=UTC)
ZERO = "sha256:" + "0" * 64


def _runtime(tmp_path, source):
    root = tmp_path / "projects"
    root.mkdir()
    catalog = ProjectCatalogRuntime(
        project_root=root, state_database=tmp_path / "catalog.sqlite", initialize_async=False,
        hardware_component_source=source, clock=type("Clock", (), {"now": lambda self: NOW})(),
    )
    location = catalog.locations(tenant_id="tenant-1", requester="engineer-1")[0]
    preview = catalog.preview(ProjectCreationRequest(
        name="S05 Model Candidate", project_type=ProjectType.STM32,
        creation_mode=ProjectCreationMode.CREATE_NEW,
        location=ProjectLocationReference(location_id=location.location_id, fingerprint=location.fingerprint),
    ), tenant_id="tenant-1", requester="engineer-1")
    project = catalog.create(preview.preview_id, preview.fingerprint, confirmed_at=NOW,
                             tenant_id="tenant-1", requester="engineer-1")
    conversation = catalog.create_conversation(project.project_id, tenant_id="tenant-1", requester="engineer-1").conversation
    return catalog, conversation


def _base_detail(conversation):
    partial = merge_requirement_context(
        None, project_id=conversation.project_id, conversation_id=conversation.conversation_id,
        turn_id="turn-1", utterance="我要做一个 STM32 小车，巡线，电池供电，直流电机",
    )
    requirement = merge_requirement_context(
        partial, project_id=conversation.project_id, conversation_id=conversation.conversation_id,
        turn_id="turn-2", utterance="开始设计", confirmation_requested=True,
    )
    plan, review = approve_engineering_plan(generate_engineering_plan(requirement), reviewer="engineer-1", decided_at=NOW)
    architecture, architecture_review = approve_architecture_design(
        generate_architecture_design(requirement, plan), reviewer="engineer-1", decided_at=NOW,
    )
    workflow = generate_engineering_workflow(architecture, plan)
    values = conversation.model_dump(mode="python")
    values.update(requirement_context=requirement, engineering_plan=plan, engineering_plan_review=review,
                  architecture_design=architecture, architecture_review=architecture_review,
                  engineering_workflow=workflow, fingerprint=ZERO)
    return ConversationDetail.model_validate(values)


def _source(catalog, detail, operation, phase, task):
    return CatalogTaskAuthoritySource(
        catalog, tenant_id=detail.tenant_id, requester=detail.requester,
        project_id=detail.project_id, conversation_id=detail.conversation_id,
        plan_id=detail.engineering_plan.plan_id, phase_id=phase, plan_task_id=task,
        operation_id=operation,
    )


def test_real_hardware_candidate_preview_validator_and_sqlite_persistence(tmp_path):
    catalog, conversation = _runtime(tmp_path, hardware_catalog())
    detail = _base_detail(conversation)
    catalog.persist_conversation_detail(detail)
    source = _source(catalog, detail, "HARDWARE_SELECTION_PROPOSAL", "phase-hardware-design", "phase-hardware-design-task-1")
    authority, _ = source.resolve_current()
    preview = catalog.model_operation_candidate_context(
        detail.project_id, detail.conversation_id, tenant_id=detail.tenant_id, requester=detail.requester,
        operation_id="HARDWARE_SELECTION_PROPOSAL", authority_source=source, authority_reference=authority,
    )
    assert preview["required_slots"] and preview["choices"]
    selected = tuple((choice["slot_id"], choice["source_id"], choice["source_fingerprint"])
                     for choice in preview["choices"] if choice["slot_id"] in preview["required_slots"])
    updated, validated = catalog.create_hardware_model_candidate_proposal(
        detail.project_id, detail.conversation_id, tenant_id=detail.tenant_id, requester=detail.requester,
        candidate_selections=selected, authority_source=source, authority_reference=authority,
    )
    assert validated == selected
    assert updated.hardware_selection.status.value == "REVIEW"
    assert catalog._repository.conversation(detail.conversation_id).hardware_selection.proposal.fingerprint == updated.hardware_selection.proposal.fingerprint


def _schematic_detail(base):
    requirement = merge_requirement_context(
        base.requirement_context, project_id=base.project_id, conversation_id=base.conversation_id,
        turn_id="turn-schematic", utterance="需要原理图", confirmation_requested=True,
    )
    plan, review = approve_engineering_plan(generate_engineering_plan(requirement), reviewer=base.requester, decided_at=NOW)
    proposal = generate_architecture_design(requirement, plan)
    protocols = {proposal.interfaces[0].source_component_id: "I2C",
                 proposal.interfaces[1].source_component_id: "POWER",
                 proposal.interfaces[2].source_component_id: "GPIO"}
    interfaces = []
    for item in proposal.interfaces:
        values = item.model_dump(mode="python")
        if item.interface_type is ArchitectureInterfaceType.HARDWARE:
            values["protocol"] = protocols[item.source_component_id]
        values["fingerprint"] = ZERO
        interfaces.append(ArchitectureInterface.create(**values))
    values = proposal.model_dump(mode="python")
    values.update(interfaces=tuple(interfaces), status=ArchitectureDesignStatus.PROPOSED, fingerprint=ZERO)
    architecture, architecture_review = approve_architecture_design(
        ArchitectureDesign.create(**values), reviewer=base.requester, decided_at=NOW,
    )
    workflow = generate_engineering_workflow(architecture, plan)
    service = HardwareSelectionService(schematic_catalog())
    selection = service.decide(service.create_proposal(
        architecture, requirement, workflow=workflow, conversation_id=base.conversation_id,
    ), HardwareSelectionDecision.APPROVED, reviewer=base.requester, decided_at=NOW)
    truth = service.project_design_truth(DesignTruthBuilder().build_from_approved_architecture(
        architecture, tenant_id=base.tenant_id, conversation_id=base.conversation_id,
        architecture_review=architecture_review), selection.hardware_truth, architecture_review=architecture_review)
    values = base.model_dump(mode="python")
    values.update(requirement_context=requirement, engineering_plan=plan, engineering_plan_review=review,
                  architecture_design=architecture, architecture_review=architecture_review,
                  engineering_workflow=workflow, hardware_selection=selection, design_truth=truth, fingerprint=ZERO)
    return ConversationDetail.model_validate(values)


def test_real_schematic_candidate_blocks_without_approved_project_supply(tmp_path):
    catalog, conversation = _runtime(tmp_path, schematic_catalog())
    detail = _schematic_detail(_base_detail(conversation))
    catalog.persist_conversation_detail(detail)
    source = _source(catalog, detail, "SCHEMATIC_DESIGN_PROPOSAL", "phase-pcb-design", "phase-pcb-design-task-1")
    authority, _ = source.resolve_current()
    with pytest.raises(SchematicPlanningBlocked) as preview_error:
        catalog.model_operation_candidate_context(
            detail.project_id, detail.conversation_id, tenant_id=detail.tenant_id, requester=detail.requester,
            operation_id="SCHEMATIC_DESIGN_PROPOSAL", authority_source=source, authority_reference=authority,
        )
    assert preview_error.value.reason_code == "POWER_SOURCE_UNRESOLVED"
    with pytest.raises(SchematicPlanningBlocked) as save_error:
        catalog.create_schematic_model_candidate_proposal(
            detail.project_id, detail.conversation_id, tenant_id=detail.tenant_id, requester=detail.requester,
            review_items=(), authority_source=source, authority_reference=authority,
        )
    assert save_error.value.reason_code == "POWER_SOURCE_UNRESOLVED"
    assert catalog._repository.conversation(detail.conversation_id).schematic_planning is None
    catalog._hardware_component_source = hardware_catalog()
    with pytest.raises(SchematicPlanningRejected):
        catalog.model_operation_candidate_context(
            detail.project_id, detail.conversation_id, tenant_id=detail.tenant_id, requester=detail.requester,
            operation_id="SCHEMATIC_DESIGN_PROPOSAL", authority_source=source, authority_reference=authority,
        )
