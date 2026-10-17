from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from embedded_copilot.component_recommendation import (
    ComponentElectricalFact,
    ComponentInterfaceFact,
    ComponentPinFact,
    ComponentPowerFact,
    ComponentSourceRecord,
    StaticComponentSource,
)
from embedded_copilot.engineering_copilot import (
    HardwareSelectionDecision,
    HardwareSelectionService,
    HardwareSelectionStatus,
    SchematicReadinessStatus,
    approve_architecture_design,
    approve_engineering_plan,
    evaluate_schematic_readiness,
    generate_design_truth,
    generate_engineering_workflow,
)
from embedded_copilot.engineering_requirements import (
    generate_architecture_design,
    generate_engineering_plan,
    merge_requirement_context,
)
from embedded_copilot.project_catalog import (
    ProjectCatalogRuntime,
    ProjectCreationMode,
    ProjectCreationRequest,
    ProjectLocationReference,
    ProjectType,
)

from .test_p0_architecture_design_contract import _approved_plan

NOW = datetime(2026, 9, 16, tzinfo=UTC)


def _source(role: str, manufacturer: str, mpn: str, symbol: str, footprint: str) -> ComponentSourceRecord:
    return ComponentSourceRecord.create(
        source_id=f"catalog-{role.casefold()}",
        source_uri=f"catalog://golden/{mpn}",
        source_revision="2026-09-16",
        manufacturer=manufacturer,
        mpn=mpn,
        category=role,
        package=footprint.rsplit(":", 1)[-1],
        description=f"Synthetic offline {role} fixture, not a product source",
        symbol=symbol,
        footprint=footprint,
        supported_roles=(role,),
        pin_facts=(
            ComponentPinFact.create(
                number="1", name="VOUT" if role == "POWER" else "VDD",
                function="output" if role == "POWER" else "supply",
                pad_number="1", is_power=True,
            ),
            ComponentPinFact.create(number="2", name="GND", function="ground", pad_number="2", is_power=True),
        ),
        electrical_facts=(
            ComponentElectricalFact.create(
                parameter="operating_voltage", minimum=3.0, typical=3.3, maximum=3.6, unit="V"
            ),
        ),
        interface_facts=(
            ComponentInterfaceFact.create(protocol="GPIO", pin_names=("1",)),
        ),
        power_facts=(
            ComponentPowerFact.create(
                domain="3V3", role="source", nominal_voltage=3.3, pin_numbers=("1",),
            ),
        ) if role == "POWER" else (),
    )


def _catalog() -> StaticComponentSource:
    return StaticComponentSource(
        (
            _source("CONTROLLER", "STMicroelectronics", "STM32F103C8T6", "MCU_ST_STM32F1:STM32F103C8Tx", "Package_QFP:LQFP-48_7x7mm_P0.5mm"),
            _source("POWER", "Offline Test", "SYNTHETIC-POWER-G1", "Test:PowerSource", "Test:PowerSource"),
            _source("SENSOR", "Bosch", "BNO055", "Sensor_Motion:BNO055", "Package_LGA:LGA-28_5.2x3.8mm_P0.5mm"),
            _source("ACTUATOR", "Texas Instruments", "DRV8833", "Driver_Motor:DRV8833", "Package_DFN_QFN:WQFN-16-1EP_3x3mm_P0.5mm"),
        )
    )


def _approved_architecture():
    context, plan, _ = _approved_plan()
    proposed = generate_architecture_design(context, plan)
    architecture, review = approve_architecture_design(
        proposed, reviewer="engineer-1", decided_at=NOW
    )
    return context, architecture, review


def test_g1_production_selection_builds_bom_truth_and_design_projection() -> None:
    context, architecture, architecture_review = _approved_architecture()
    workflow = generate_engineering_workflow(architecture, _approved_plan()[1])
    service = HardwareSelectionService(_catalog())

    proposal = service.create_proposal(
        architecture,
        context,
        workflow=workflow,
        conversation_id=context.conversation_id,
    )
    assert proposal.status is HardwareSelectionStatus.REVIEW
    assert {item.role for item in proposal.selected_components} == {
        "CONTROLLER", "POWER", "SENSOR", "ACTUATOR"
    }
    assert all(item.mpn != "UNKNOWN" for item in proposal.selected_components)

    state = service.approve(proposal, reviewer="engineer-1", decided_at=NOW)
    assert state.status is HardwareSelectionStatus.APPROVED
    assert state.bom is not None
    assert state.hardware_truth is not None
    assert state.approval is not None
    assert state.bom.fingerprint == state.approval.bom_fingerprint
    assert all(item.manufacturer and item.mpn and item.symbol and item.footprint for item in state.bom.items)
    assert all(item.source_fingerprint.startswith("sha256:") for item in state.hardware_truth.components)
    assert all(pin.pad_number for item in state.hardware_truth.components for pin in item.pin_facts)
    assert all(
        value.casefold() != "unknown"
        for item in state.hardware_truth.components
        for value in (item.manufacturer, item.mpn, item.symbol, item.footprint)
    )

    base_truth = generate_design_truth(architecture, conversation_id=context.conversation_id)
    projected = service.project_design_truth(
        base_truth,
        state.hardware_truth,
        architecture_review=architecture_review,
    )
    assert projected.components
    assert projected.pins
    assert all(item.symbol_binding for item in projected.components)
    readiness = evaluate_schematic_readiness(
        projected,
        SimpleNamespace(
            project_id=context.project_id,
            hardware_spec_id="hardware-g1",
            schematic_plan=object(),
        ),
        SimpleNamespace(
            project_id=context.project_id,
            hardware_design_id="hardware-g1",
            expected_artifacts=(),
            tasks=(SimpleNamespace(task_id="schematic-g1", expected_artifacts=("SCHEMATIC",)),),
        ),
    )
    assert readiness.status is SchematicReadinessStatus.BLOCKED_MISSING_NET
    assert "pin_signal_binding" in readiness.missing_facts


def test_g1_missing_evidence_fails_closed_before_approval() -> None:
    context, architecture, _ = _approved_architecture()
    workflow = generate_engineering_workflow(architecture, _approved_plan()[1])
    records = list(_catalog().records)
    record_values = records[0].model_dump(mode="python")
    record_values.update({"source_uri": "catalog://missing/STM32F103C8T6", "electrical_facts": ()})
    records[0] = ComponentSourceRecord.create(**record_values)
    source = StaticComponentSource(tuple(records))

    with pytest.raises(ValueError, match="evidence"):
        HardwareSelectionService(source).create_proposal(
            architecture,
            context,
            workflow=workflow,
            conversation_id=context.conversation_id,
        )


def test_g1_missing_eda_mapping_fails_closed() -> None:
    context, architecture, _ = _approved_architecture()
    workflow = generate_engineering_workflow(architecture, _approved_plan()[1])
    records = list(_catalog().records)
    record_values = records[0].model_dump(mode="python")
    record_values.update({"symbol": None, "footprint": None})
    records[0] = ComponentSourceRecord.create(**record_values)

    with pytest.raises(ValueError, match="mapping"):
        HardwareSelectionService(StaticComponentSource(tuple(records))).create_proposal(
            architecture,
            context,
            workflow=workflow,
            conversation_id=context.conversation_id,
        )


def test_g1_draft_does_not_produce_approved_truth_and_tamper_is_rejected() -> None:
    context, architecture, _ = _approved_architecture()
    workflow = generate_engineering_workflow(architecture, _approved_plan()[1])
    service = HardwareSelectionService(_catalog())
    proposal = service.create_proposal(
        architecture, context, workflow=workflow, conversation_id=context.conversation_id
    )
    rejected = service.decide(proposal, HardwareSelectionDecision.REJECTED, reviewer="engineer-1", decided_at=NOW)
    assert rejected.status is HardwareSelectionStatus.REJECTED
    assert rejected.hardware_truth is None

    tampered = proposal.model_dump(mode="python")
    tampered["architecture_fingerprint"] = "sha256:" + "b" * 64
    tampered["fingerprint"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="fingerprint"):
        type(proposal).model_validate(tampered, strict=False)

    approved = service.approve(proposal, reviewer="engineer-1", decided_at=NOW)
    tampered_state = approved.model_dump(mode="python")
    tampered_state["approval"]["evidence_fingerprints"] = ["sha256:" + "b" * 64]
    tampered_state["approval"]["fingerprint"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="fingerprint"):
        type(approved).model_validate(tampered_state, strict=False)


def test_g1_project_catalog_entry_persists_review_and_approved_truth(tmp_path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    runtime = ProjectCatalogRuntime(
        project_root=root,
        state_database=tmp_path / "desktop.sqlite3",
        clock=type("Clock", (), {"now": lambda self: NOW})(),
        initialize_async=False,
        hardware_component_source=_catalog(),
    )
    location = runtime.locations(tenant_id="tenant-1", requester="engineer-1")[0]
    preview = runtime.preview(
        ProjectCreationRequest(
            name="G1 Hardware Selection",
            project_type=ProjectType.STM32,
            creation_mode=ProjectCreationMode.CREATE_NEW,
            relative_path=None,
            location=ProjectLocationReference(
                location_id=location.location_id,
                fingerprint=location.fingerprint,
            ),
        ),
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    project = runtime.create(
        preview.preview_id,
        preview.fingerprint,
        confirmed_at=NOW,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    session = runtime.create_conversation(
        project.project_id, tenant_id="tenant-1", requester="engineer-1"
    )
    conversation = session.conversation
    partial = merge_requirement_context(
        conversation.requirement_context,
        project_id=project.project_id,
        conversation_id=conversation.conversation_id,
        turn_id="requirements-1",
        utterance="我要做一个 STM32 小车，巡线，电池供电，直流电机",
    )
    requirements = merge_requirement_context(
        partial,
        project_id=project.project_id,
        conversation_id=conversation.conversation_id,
        turn_id="requirements-2",
        utterance="开始设计",
        confirmation_requested=True,
    )
    proposed_plan = generate_engineering_plan(requirements)
    plan, plan_review = approve_engineering_plan(
        proposed_plan, reviewer="engineer-1", decided_at=NOW
    )
    architecture_proposed = generate_architecture_design(requirements, plan)
    architecture, architecture_review = approve_architecture_design(
        architecture_proposed, reviewer="engineer-1", decided_at=NOW
    )
    workflow = generate_engineering_workflow(architecture, plan)
    seeded = conversation.model_copy(
        update={
            "requirement_context": requirements,
            "engineering_plan": plan,
            "engineering_plan_review": plan_review,
            "architecture_design": architecture,
            "architecture_review": architecture_review,
            "engineering_workflow": workflow,
            "fingerprint": "sha256:" + "0" * 64,
        }
    )
    runtime.persist_conversation_detail(seeded)

    review = runtime.create_hardware_selection_proposal(
        project.project_id,
        conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert review.hardware_selection is not None
    assert review.hardware_selection.status is HardwareSelectionStatus.REVIEW

    restarted = ProjectCatalogRuntime(
        project_root=root,
        state_database=tmp_path / "desktop.sqlite3",
        clock=type("Clock", (), {"now": lambda self: NOW})(),
        initialize_async=False,
        hardware_component_source=_catalog(),
    )
    recovered = restarted.conversation(
        project.project_id,
        conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert recovered.hardware_selection is not None
    approved = restarted.decide_hardware_selection(
        project.project_id,
        conversation.conversation_id,
        HardwareSelectionDecision.APPROVED,
        tenant_id="tenant-1",
        requester="engineer-1",
        decided_at=NOW,
    )
    assert approved.hardware_selection is not None
    assert approved.hardware_selection.status is HardwareSelectionStatus.APPROVED
    assert approved.hardware_selection.bom is not None
    assert approved.design_truth is not None
    assert approved.design_truth.components

    final_runtime = ProjectCatalogRuntime(
        project_root=root,
        state_database=tmp_path / "desktop.sqlite3",
        clock=type("Clock", (), {"now": lambda self: NOW})(),
        initialize_async=False,
        hardware_component_source=_catalog(),
    )
    final = final_runtime.conversation(
        project.project_id,
        conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert final.hardware_selection is not None
    assert final.hardware_selection.hardware_truth is not None
