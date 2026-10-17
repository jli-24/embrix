from __future__ import annotations

from types import SimpleNamespace

import pytest

from embedded_copilot.component_recommendation import (
    ComponentElectricalFact,
    ComponentInterfaceFact,
    ComponentPinFact,
    ComponentPowerFact,
    ComponentSourceRecord,
    StaticComponentSource,
    SupportingComponentRequirement,
)
from embedded_copilot.engineering_copilot import (
    ArchitectureDesign,
    ArchitectureDesignStatus,
    ArchitectureInterface,
    ArchitectureInterfaceType,
    HardwareSelectionDecision,
    HardwareSelectionService,
    SchematicPlanDecision,
    SchematicPlanningService,
    SchematicPlanningStatus,
    SchematicReadinessStatus,
    approve_architecture_design,
    approve_engineering_plan,
    evaluate_schematic_readiness,
    generate_design_truth,
    generate_engineering_workflow,
    generate_knowledge_model,
)
from embedded_copilot.engineering_copilot.schematic_planning import (
    SchematicPlanningBlocked,
    _canonical,
)
from embedded_copilot.engineering_requirements import (
    generate_architecture_design,
    generate_engineering_plan,
    merge_requirement_context,
)
from embedded_copilot.engineering_runtime import (
    CircuitIntentObject,
    LifecycleStatus,
    Provenance,
)
from embedded_copilot.project_catalog import (
    ProjectCatalogRuntime,
    ProjectCreationMode,
    ProjectCreationRequest,
    ProjectLocationReference,
    ProjectType,
)
from embedded_copilot.project_catalog.models import ConversationDetail

from .test_hardware_selection_g1 import NOW, _approved_architecture

ZERO = "sha256:" + "0" * 64


def _power(role: str, domain: str, pin: str, nominal: float = 3.3, *, minimum: float | None = None, maximum: float | None = None) -> ComponentPowerFact:
    return ComponentPowerFact.create(
        domain=domain,
        role=role,
        nominal_voltage=nominal,
        minimum_voltage=minimum,
        maximum_voltage=maximum,
        pin_numbers=(pin,),
    )


def _pin(number: str, name: str, function: str, *, protocols: tuple[str, ...] = (), power: bool = False) -> ComponentPinFact:
    return ComponentPinFact.create(
        number=number,
        name=name,
        function=function,
        pad_number=number,
        is_power=power,
        interface_protocols=protocols,
    )


def _source(
    role: str,
    manufacturer: str,
    mpn: str,
    pins: tuple[ComponentPinFact, ...],
    interfaces: tuple[ComponentInterfaceFact, ...] = (),
    power: tuple[ComponentPowerFact, ...] = (),
    supporting: tuple[SupportingComponentRequirement, ...] = (),
) -> ComponentSourceRecord:
    return ComponentSourceRecord.create(
        source_id=f"catalog-{role.casefold()}",
        source_uri=f"catalog://golden-g2/{mpn}",
        source_revision="2026-09-16-g2",
        manufacturer=manufacturer,
        mpn=mpn,
        category=role,
        package="QFN",
        description=f"Verified {role} component",
        symbol=f"Golden:{mpn}",
        footprint=f"Golden:{mpn}-Footprint",
        supported_roles=(role,),
        pin_facts=pins,
        electrical_facts=(
            ComponentElectricalFact.create(
                parameter="operating_voltage", minimum=3.0, typical=3.3, maximum=3.6, unit="V"
            ),
        ),
        interface_facts=interfaces,
        power_facts=power,
        supporting_requirements=supporting,
    )


def _catalog(*, conflict: bool = False, voltage_conflict: bool = False, missing_sda: bool = False, nc: bool = False, supporting: bool = False) -> StaticComponentSource:
    controller_scl_name = "GPIO_SCL" if conflict else "PB6_SCL"
    controller_gpio_pins = ("3",) if conflict else ("5",)
    controller_interface_facts = (
        ComponentInterfaceFact.create(protocol="POWER", pin_names=("1", "2")),
        ComponentInterfaceFact.create(protocol="I2C", pin_names=("3", "4")),
        ComponentInterfaceFact.create(protocol="GPIO", pin_names=controller_gpio_pins),
    )
    controller_pins = (
        _pin("1", "VDD", "supply", power=True),
        _pin("2", "GND", "ground", power=True),
        _pin("3", controller_scl_name, "I2C_SCL", protocols=("I2C", "GPIO") if conflict else ("I2C",)),
        _pin("4", "PB7_SDA", "I2C_SDA", protocols=("I2C",)),
        _pin("5", "GPIO_A", "GPIO", protocols=("GPIO",)),
    )
    controller = _source(
        "CONTROLLER",
        "Golden MCU",
        "MCU-G2",
        controller_pins,
        controller_interface_facts,
        (_power("consumer", "3V3", "1", minimum=3.0, maximum=3.6), _power("ground", "GND", "2", nominal=0.0)),
    )
    sensor_sda_protocols = () if missing_sda else ("I2C",)
    sensor = _source(
        "SENSOR",
        "Golden Sensor",
        "SENSOR-G2",
        (
            _pin("1", "VDD", "supply", power=True),
            _pin("2", "GND", "ground", power=True),
            _pin("3", "SCL", "I2C_SCL", protocols=("I2C",)),
            _pin("4", "SDA", "I2C_SDA", protocols=sensor_sda_protocols),
        ),
        (
            ComponentInterfaceFact.create(protocol="I2C", pin_names=("3", "4")),
        ),
        (
            _power("consumer", "3V3", "1", nominal=3.5 if voltage_conflict else 3.3, minimum=3.4 if voltage_conflict else 3.0, maximum=3.6),
            _power("ground", "GND", "2", nominal=0.0),
        ),
    )
    power = _source(
        "POWER",
        "Golden Power",
        "POWER-G2",
        (_pin("1", "VOUT", "power_output", power=True), _pin("2", "GND", "ground", power=True)),
        interfaces=(ComponentInterfaceFact.create(protocol="POWER", pin_names=("1", "2")),),
        power=(_power("source", "3V3", "1"), _power("ground", "GND", "2", nominal=0.0)),
    )
    actuator_pins = [
        _pin("1", "VDD", "supply", power=True),
        _pin("2", "GND", "ground", power=True),
        _pin("3", "GPIO_A", "GPIO", protocols=("GPIO",)),
    ]
    if nc:
        actuator_pins.append(_pin("4", "NC", "NC"))
    actuator = _source(
        "ACTUATOR",
        "Golden Actuator",
        "ACTUATOR-G2",
        tuple(actuator_pins),
        (ComponentInterfaceFact.create(protocol="GPIO", pin_names=("3",)),),
        (_power("consumer", "3V3", "1", minimum=3.0, maximum=3.6), _power("ground", "GND", "2", nominal=0.0)),
        (
            SupportingComponentRequirement.create(
                role="DECOUPLING",
                description="100nF local bypass capacitor",
                evidence_refs=("catalog-supporting-g2",),
            ),
        ) if supporting else (),
    )
    return StaticComponentSource((controller, power, sensor, actuator))


def _golden_architecture(project_id: str = "project-1", conversation_id: str = "conversation-1"):
    if project_id == "project-1" and conversation_id == "conversation-1":
        context, original, _ = _approved_architecture()
    else:
        partial = merge_requirement_context(
            None,
            project_id=project_id,
            conversation_id=conversation_id,
            turn_id="g2-requirements-1",
            utterance="我要做一个 STM32 小车，巡线，电池供电，直流电机",
        )
        context = merge_requirement_context(
            partial,
            project_id=project_id,
            conversation_id=conversation_id,
            turn_id="g2-requirements-2",
            utterance="开始设计",
            confirmation_requested=True,
        )
        plan_proposal = generate_engineering_plan(context)
        plan, _plan_review = approve_engineering_plan(
            plan_proposal, reviewer="engineer-1", decided_at=NOW
        )
        architecture_proposal = generate_architecture_design(context, plan)
        original, _ = approve_architecture_design(
            architecture_proposal, reviewer="engineer-1", decided_at=NOW
        )
    protocol_by_source = {
        original.interfaces[0].source_component_id: "I2C",
        original.interfaces[1].source_component_id: "POWER",
        original.interfaces[2].source_component_id: "GPIO",
    }
    interfaces = []
    for item in original.interfaces:
        values = item.model_dump(mode="python")
        if item.interface_type is ArchitectureInterfaceType.HARDWARE:
            values["protocol"] = protocol_by_source[item.source_component_id]
        values["fingerprint"] = ZERO
        interfaces.append(ArchitectureInterface.create(**values))
    design_values = original.model_dump(mode="python")
    design_values.update(
        {
            "interfaces": tuple(interfaces),
            "status": ArchitectureDesignStatus.PROPOSED,
            "fingerprint": ZERO,
        }
    )
    proposed = ArchitectureDesign.create(**design_values)
    architecture, review = approve_architecture_design(
        proposed, reviewer="engineer-1", decided_at=NOW
    )
    return context, architecture, review


def _approved_inputs(source: StaticComponentSource | None = None):
    context, architecture, architecture_review = _golden_architecture()
    plan_proposal = generate_engineering_plan(context)
    plan, _plan_review = approve_engineering_plan(
        plan_proposal, reviewer="engineer-1", decided_at=NOW
    )
    workflow = generate_engineering_workflow(architecture, plan)
    selection_service = HardwareSelectionService(source or _catalog())
    selection_proposal = selection_service.create_proposal(
        architecture, context, workflow=workflow, conversation_id=context.conversation_id
    )
    selection = selection_service.decide(
        selection_proposal,
        HardwareSelectionDecision.APPROVED,
        reviewer="engineer-1",
        decided_at=NOW,
    )
    assert selection.hardware_truth is not None and selection.bom is not None
    base = generate_design_truth(architecture, conversation_id=context.conversation_id)
    base = selection_service.project_design_truth(
        base, selection.hardware_truth, architecture_review=architecture_review
    )
    return context, architecture, architecture_review, workflow, selection, base


def _proposal(source: StaticComponentSource | None = None):
    context, architecture, architecture_review, _workflow, selection, base = _approved_inputs(source)
    assert selection.hardware_truth is not None and selection.bom is not None
    proposal = SchematicPlanningService().create_proposal(
        architecture,
        context,
        selection.hardware_truth,
        selection.bom,
        design_truth=base,
        conversation_id=context.conversation_id,
    )
    return proposal, context, architecture, architecture_review, selection, base


def test_g2_normal_plan_projects_explicit_connectivity_and_is_ready_for_generation() -> None:
    proposal, _context, architecture, _review, _selection, base = _proposal()
    assert proposal.status is SchematicPlanningStatus.REVIEW
    assert {item.protocol for item in proposal.interfaces} == {"I2C", "GPIO"}
    assert {item.name for item in proposal.nets} >= {"3V3", "GND", "I2C1_SCL", "I2C1_SDA"}
    assert {item.name for item in proposal.power_domains} == {"3V3", "GND"}
    approved_state = SchematicPlanningService().decide(
        proposal,
        SchematicPlanDecision.APPROVED,
        reviewer="engineer-1",
        decided_at=NOW,
    )
    assert approved_state.status is SchematicPlanningStatus.APPROVED
    assert approved_state.approved_plan is not None
    projected = SchematicPlanningService().project_design_truth(base, approved_state.approved_plan)
    assert projected.signals and projected.nets and projected.power_domains and projected.interfaces
    readiness = evaluate_schematic_readiness(
        projected,
        SimpleNamespace(
            project_id=projected.project_id,
            hardware_spec_id="hardware-g2",
            architecture_id=architecture.architecture_id,
            schematic_plan=object(),
        ),
        SimpleNamespace(
            project_id=projected.project_id,
            conversation_id=projected.conversation_id,
            hardware_design_id="hardware-g2",
            expected_artifacts=(),
            tasks=(SimpleNamespace(task_id="schematic-g2", expected_artifacts=("SCHEMATIC",)),),
        ),
    )
    assert readiness.status is SchematicReadinessStatus.READY_FOR_GENERATION
    assert readiness.status.value == "READY"


def test_g2_circuit_intent_identity_is_fingerprinted_as_a_complete_group() -> None:
    proposal, *_ = _proposal()
    bound = proposal.model_copy(
        update={
            "circuit_intent_id": "circuit-intent-001",
            "circuit_intent_revision": "r1",
            "circuit_intent_fingerprint": "sha256:" + "1" * 64,
        }
    )
    bound = type(proposal).model_validate(
        {
            **bound.model_dump(mode="python"),
            "fingerprint": _canonical(bound, exclude={"fingerprint", "status"}),
        }
    )
    assert bound.circuit_intent_id == "circuit-intent-001"
    assert bound.fingerprint != proposal.fingerprint

    incomplete = bound.model_copy(update={"circuit_intent_fingerprint": None})
    incomplete_values = {
        **incomplete.model_dump(mode="python"),
        "fingerprint": _canonical(incomplete, exclude={"fingerprint", "status"}),
    }
    with pytest.raises(ValueError, match="circuit intent identity is incomplete"):
        type(proposal).model_validate(incomplete_values)


def test_g2_planning_consumes_approved_circuit_intent_snapshot() -> None:
    _base_proposal, context, architecture, _review, selection, base = _proposal()
    assert selection.hardware_truth is not None and selection.bom is not None
    intent = CircuitIntentObject.create(
        object_id="circuit-intent-stm32-planning",
        project_id=context.project_id,
        status=LifecycleStatus.APPROVED,
        function="STM32 control-board power and interfaces",
        topology=("5V to 3V3", "MCU to CAN transceiver", "MCU to SWD header"),
        blocks=(),
        provenance=Provenance.create(
            source="TEST_APPROVED_CIRCUIT_INTENT",
            source_ref=f"{context.project_id}:circuit-intent-stm32-planning",
            created_by="engineer-1",
            created_at=NOW,
        ),
    )

    proposal = SchematicPlanningService().create_proposal(
        architecture,
        context,
        selection.hardware_truth,
        selection.bom,
        design_truth=base,
        conversation_id=context.conversation_id,
        circuit_intent=intent,
    )

    assert proposal.project_id == intent.project_id
    assert proposal.circuit_intent_id == intent.object_id
    assert proposal.circuit_intent_revision == intent.revision
    assert proposal.circuit_intent_fingerprint == intent.fingerprint


@pytest.mark.parametrize(
    "source, message",
    [
        (_catalog(missing_sda=True), "required interface pin"),
        (_catalog(conflict=True), "pin conflict"),
        (_catalog(voltage_conflict=True), "voltage conflict"),
    ],
)
def test_g2_invalid_connectivity_fails_closed(source: StaticComponentSource, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _proposal(source)


def test_g2_intentional_nc_is_typed_and_does_not_fabricate_a_signal() -> None:
    proposal, _context, _architecture, _review, _selection, base = _proposal(_catalog(nc=True))
    nc = proposal.intentional_unconnected
    assert len(nc) == 1
    binding = next(item for item in proposal.pin_bindings if item.pin_number == "4" and item.component_id == nc[0].component_id)
    assert binding.intentionally_unconnected is True
    assert binding.signal_id is None
    approved = SchematicPlanningService().decide(
        proposal, SchematicPlanDecision.APPROVED, reviewer="engineer-1", decided_at=NOW
    )
    assert approved.approved_plan is not None
    projected = SchematicPlanningService().project_design_truth(base, approved.approved_plan)
    projected_nc = next(
        item for item in projected.pins
        if item.component_id == nc[0].component_id and item.pin_number == "4"
    )
    assert projected_nc.intentionally_unconnected is True
    assert projected_nc.signal_id is None


def test_g2_supporting_component_requirement_blocks_approval() -> None:
    proposal, *_ = _proposal(_catalog(supporting=True))
    assert proposal.status is SchematicPlanningStatus.BLOCKED
    assert proposal.supporting_connections[0].status.value == "REQUIREMENT_ONLY"
    assert "BLOCKED_SUPPORTING_COMPONENT_SELECTION" in proposal.blocking_reasons
    with pytest.raises(ValueError, match="not reviewable"):
        SchematicPlanningService().decide(
            proposal, SchematicPlanDecision.APPROVED, reviewer="engineer-1", decided_at=NOW
        )


def test_g2_rejection_and_tamper_do_not_promote_design_truth() -> None:
    proposal, _context, _architecture, _review, _selection, base = _proposal()
    rejected = SchematicPlanningService().decide(
        proposal, SchematicPlanDecision.REJECTED, reviewer="engineer-1", decided_at=NOW
    )
    assert rejected.status is SchematicPlanningStatus.REJECTED
    assert rejected.approved_plan is None
    with pytest.raises(ValueError, match="fingerprint"):
        values = proposal.model_dump(mode="python")
        values["signals"][0]["name"] = "tampered"
        type(proposal).model_validate(values, strict=False)
    with pytest.raises(ValueError, match="approved"):
        SchematicPlanningService().project_design_truth(base, proposal)  # type: ignore[arg-type]


def test_g2_legacy_approved_plan_remains_readable_but_cannot_grant_new_p1_access(tmp_path) -> None:
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
            name="G2 Schematic Planning",
            project_type=ProjectType.STM32,
            creation_mode=ProjectCreationMode.CREATE_NEW,
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
    conversation = runtime.create_conversation(
        project.project_id, tenant_id="tenant-1", requester="engineer-1"
    ).conversation
    context, architecture, architecture_review = _golden_architecture(
        project.project_id, conversation.conversation_id
    )
    plan_proposal = generate_engineering_plan(context)
    plan, plan_review = approve_engineering_plan(
        plan_proposal, reviewer="engineer-1", decided_at=NOW
    )
    workflow = generate_engineering_workflow(architecture, plan)
    seeded = conversation.model_copy(
        update={
            "requirement_context": context,
            "engineering_plan": plan,
            "engineering_plan_review": plan_review,
            "architecture_design": architecture,
            "architecture_review": architecture_review,
            "engineering_workflow": workflow,
            "fingerprint": ZERO,
        }
    )
    runtime.persist_conversation_detail(seeded)
    reviewed = runtime.create_hardware_selection_proposal(
        project.project_id,
        conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert reviewed.hardware_selection is not None
    approved_selection = runtime.decide_hardware_selection(
        project.project_id,
        conversation.conversation_id,
        HardwareSelectionDecision.APPROVED,
        tenant_id="tenant-1",
        requester="engineer-1",
        decided_at=NOW,
    )
    assert approved_selection.hardware_selection is not None
    with pytest.raises(SchematicPlanningBlocked, match="POWER_SOURCE_UNRESOLVED"):
        runtime.create_schematic_plan(
            project.project_id, conversation.conversation_id,
            tenant_id="tenant-1", requester="engineer-1",
        )
    assert approved_selection.design_truth is not None
    service = SchematicPlanningService()
    legacy_proposal = service.create_proposal(
        architecture, context, approved_selection.hardware_selection.hardware_truth,
        approved_selection.hardware_selection.bom,
        design_truth=approved_selection.design_truth,
        conversation_id=conversation.conversation_id,
        selection=approved_selection.hardware_selection,
    )
    assert legacy_proposal.status is SchematicPlanningStatus.REVIEW
    legacy_state = service.decide(
        legacy_proposal, SchematicPlanDecision.APPROVED,
        reviewer="engineer-1", decided_at=NOW,
    )
    projected = service.project_design_truth(approved_selection.design_truth, legacy_state.approved_plan)
    historical = ConversationDetail.model_validate(approved_selection.model_copy(update={
        "schematic_planning": legacy_state,
        "design_truth": projected,
        "engineering_knowledge_model": generate_knowledge_model(projected),
        "fingerprint": ZERO,
    }).model_dump(mode="python"))
    runtime.persist_conversation_detail(historical)
    restarted = ProjectCatalogRuntime(
        project_root=root,
        state_database=tmp_path / "desktop.sqlite3",
        clock=type("Clock", (), {"now": lambda self: NOW})(),
        initialize_async=False,
        hardware_component_source=_catalog(),
    )
    recovered = restarted.conversation(
        project.project_id, conversation.conversation_id,
        tenant_id="tenant-1", requester="engineer-1",
    )
    assert recovered.schematic_planning == legacy_state
    assert recovered.design_truth == projected
    assert restarted.schematic_plan_currentness(
        project.project_id, conversation.conversation_id,
        legacy_state.approved_plan.plan_id, legacy_state.approved_plan.fingerprint,
        tenant_id="tenant-1", requester="engineer-1",
    )[2] == "STALE"

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
    assert final.schematic_planning is not None
    assert final.schematic_planning.approved_plan is not None
    assert final.design_truth is not None
    assert final.design_truth.fingerprint == projected.fingerprint
