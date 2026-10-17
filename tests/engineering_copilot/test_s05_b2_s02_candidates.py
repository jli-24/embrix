from __future__ import annotations

import pytest

from embedded_copilot.component_recommendation import (
    ComponentSourceRecord,
    StaticComponentSource,
)
from embedded_copilot.engineering_copilot import (
    HardwareSelectionService,
    HardwareSelectionStatus,
    SchematicPlanningService,
    SchematicPlanningStatus,
    generate_engineering_workflow,
)
from embedded_copilot.engineering_requirements import (
    generate_engineering_plan,
    merge_requirement_context,
)

from .test_hardware_selection_g1 import _approved_architecture, _catalog
from .test_p0_architecture_design_contract import _approved_plan
from .test_schematic_planning_g2 import _approved_inputs
from .test_schematic_planning_g2 import _catalog as _schematic_catalog


def test_generated_v2_plan_binds_only_actual_hardware_and_schematic_tasks() -> None:
    context, _old_plan, _ = _approved_plan()
    plan = generate_engineering_plan(context)
    assert plan.model_operation_schema_version == 2
    assert {(b.phase_id, b.plan_task_id, b.operation_id, b.stage_id.value) for b in plan.model_operation_bindings} == {
        ("phase-hardware-design", "phase-hardware-design-task-1", "HARDWARE_SELECTION_PROPOSAL", "HARDWARE_SELECTION"),
    }
    assert all(
        (binding.phase_id, binding.plan_task_id)
        in {(phase.phase_id, task.task_id) for phase in plan.phases for task in phase.tasks}
        for binding in plan.model_operation_bindings
    )
    with_schematic = merge_requirement_context(
        context,
        project_id=context.project_id,
        conversation_id=context.conversation_id,
        turn_id="turn-b2-schematic",
        utterance="需要原理图",
        confirmation_requested=True,
    )
    schematic_plan = generate_engineering_plan(with_schematic)
    assert {(b.phase_id, b.plan_task_id, b.operation_id, b.stage_id.value) for b in schematic_plan.model_operation_bindings} == {
        ("phase-hardware-design", "phase-hardware-design-task-1", "HARDWARE_SELECTION_PROPOSAL", "HARDWARE_SELECTION"),
        ("phase-pcb-design", "phase-pcb-design-task-1", "SCHEMATIC_DESIGN_PROPOSAL", "SCHEMATIC_DESIGN"),
    }


def test_hardware_model_candidate_selects_only_current_selectable_source() -> None:
    context, architecture, _ = _approved_architecture()
    workflow = generate_engineering_workflow(architecture, _approved_plan()[1])
    service = HardwareSelectionService(_catalog())
    base = service.create_proposal(architecture, context, workflow=workflow, conversation_id=context.conversation_id)
    selections = tuple((item.slot_id, item.source.source_id, item.source.fingerprint) for item in base.selected_components)
    proposal = service.create_proposal(
        architecture, context, workflow=workflow, conversation_id=context.conversation_id,
        candidate_selections=selections,
    )
    assert proposal.status is HardwareSelectionStatus.REVIEW
    assert proposal.selected_components == base.selected_components
    sensor = next(item for item in _catalog().records if "SENSOR" in item.supported_roles)
    alternate_values = sensor.model_dump(mode="python", exclude={"fingerprint"})
    alternate_values.update({
        "source_id": "catalog-sensor-alternate",
        "source_uri": "catalog://golden/alternate-sensor",
        "mpn": "ZZZ-SENSOR",
    })
    alternate = ComponentSourceRecord.create(**alternate_values)
    alternate_catalog = StaticComponentSource((*_catalog().records, alternate))
    alternative_selections = tuple(
        (slot, alternate.source_id, alternate.fingerprint)
        if source == sensor.source_id else (slot, source, fingerprint)
        for slot, source, fingerprint in selections
    )
    alternate_proposal = HardwareSelectionService(alternate_catalog).create_proposal(
        architecture, context, workflow=workflow, conversation_id=context.conversation_id,
        candidate_selections=alternative_selections,
    )
    assert next(item for item in alternate_proposal.selected_components if item.role == "SENSOR").mpn == "ZZZ-SENSOR"
    assert alternate_proposal.status is HardwareSelectionStatus.REVIEW
    for bad in (
        selections[:-1],
        (selections[0], *selections),
        ((selections[0][0], "unknown-source", selections[0][2]), *selections[1:]),
        ((selections[0][0], selections[0][1], "sha256:" + "0" * 64), *selections[1:]),
    ):
        with pytest.raises((TypeError, ValueError)):
            service.create_proposal(
                architecture, context, workflow=workflow, conversation_id=context.conversation_id,
                candidate_selections=bad,
            )
    records = _catalog().records
    sensor = next(item for item in records if "SENSOR" in item.supported_roles)
    invalid_values = sensor.model_dump(mode="python", exclude={"fingerprint"})
    invalid_values["electrical_facts"] = ()
    invalid = ComponentSourceRecord.create(**invalid_values)
    invalid_catalog = StaticComponentSource(tuple(
        invalid if item.source_id == sensor.source_id else item for item in records
    ))
    stale = tuple(
        (slot, source, invalid.fingerprint if source == sensor.source_id else fingerprint)
        for slot, source, fingerprint in selections
    )
    with pytest.raises(ValueError, match="not current and selectable"):
        HardwareSelectionService(invalid_catalog).create_proposal(
            architecture, context, workflow=workflow, conversation_id=context.conversation_id,
            candidate_selections=stale,
        )


def test_schematic_model_advisory_cannot_change_authoritative_plan() -> None:
    context, architecture, _review, _workflow, selection, base = _approved_inputs()
    original = SchematicPlanningService().create_proposal(
        architecture, context, selection.hardware_truth, selection.bom,
        design_truth=base, conversation_id=context.conversation_id,
    )
    net = original.nets[0]
    suggestion = ((net.net_id, tuple(endpoint.binding_id for endpoint in net.endpoints)),)
    proposal, checked = SchematicPlanningService().create_model_candidate_proposal(
        architecture, context, selection.hardware_truth, selection.bom,
        design_truth=base, conversation_id=context.conversation_id,
        review_items=suggestion,
    )
    assert proposal.fingerprint == original.fingerprint
    assert proposal.status is SchematicPlanningStatus.REVIEW
    assert checked == suggestion
    for bad in (
        (("unknown-net", suggestion[0][1]),),
        ((net.net_id, ("unknown-binding",)),),
        (suggestion[0], suggestion[0]),
    ):
        with pytest.raises((TypeError, ValueError)):
            SchematicPlanningService().create_model_candidate_proposal(
                architecture, context, selection.hardware_truth, selection.bom,
                design_truth=base, conversation_id=context.conversation_id,
                review_items=bad,
            )


def test_schematic_model_advisory_preserves_supporting_component_blocker() -> None:
    context, architecture, _review, _workflow, selection, base = _approved_inputs(_schematic_catalog(supporting=True))
    proposal, checked = SchematicPlanningService().create_model_candidate_proposal(
        architecture, context, selection.hardware_truth, selection.bom,
        design_truth=base, conversation_id=context.conversation_id,
        review_items=(),
    )
    assert checked == ()
    assert proposal.status is SchematicPlanningStatus.BLOCKED
    assert "BLOCKED_SUPPORTING_COMPONENT_SELECTION" in proposal.blocking_reasons
