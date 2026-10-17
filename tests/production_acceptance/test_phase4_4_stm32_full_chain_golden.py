from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import pytest

from embedded_copilot.application import (
    EngineeringObjectFlowError,
    EngineeringObjectFlowErrorCode,
)
from embedded_copilot.component_recommendation import (
    ComponentInterfaceFact,
    ComponentSourceRecord,
    SupportingComponentRequirement,
    load_approved_component_source,
)
from embedded_copilot.engineering_copilot import (
    ArchitectureDesignStatus,
    ArchitectureLevel,
    HardwareSelectionService,
    SchematicComponentProposal,
    SchematicNetProposal,
    SchematicPinProposal,
    SchematicPlan,
    SchematicPlanDecision,
    SchematicPlanningService,
    SchematicPowerProposal,
    SchematicProductionRequest,
    SchematicProposal,
    approve_engineering_plan,
    generate_design_truth,
    generate_engineering_workflow,
)
from embedded_copilot.engineering_requirements import (
    generate_engineering_plan,
)
from embedded_copilot.engineering_runtime import (
    CircuitBlock,
    CircuitIntentObject,
    LifecycleStatus,
)
from tests.application.test_engineering_runtime_integration import (
    FixtureQualifiedReceiptReferencePort,
    _evidence,
    _runtime,
)
from tests.application.test_phase4_3_1_binding import _intent
from tests.engineering_copilot.test_schematic_planning_g2 import (
    _golden_architecture,
    _pin,
    _power,
    _source,
)

NOW = datetime(2026, 9, 22, 12, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return NOW


def _project_intent(project_id: str) -> CircuitIntentObject:
    values = _intent().model_dump(mode="python")
    values["project_id"] = project_id
    provenance = dict(values["provenance"])
    provenance["source_ref"] = f"{project_id}:phase4-4-approved-intent"
    values["provenance"] = provenance
    values.pop("fingerprint", None)
    return CircuitIntentObject.create(**values)


def _proposal_from_plan(plan) -> SchematicProposal:
    components = tuple(
        SchematicComponentProposal.create(
            component_id=item.instance_id,
            reference=item.instance_id,
            symbol_binding=item.symbol,
            value=item.mpn,
            source_refs=item.source_refs,
        )
        for item in plan.component_instances
    )
    pins = tuple(
        SchematicPinProposal.create(
            pin_id=item.binding_id,
            component_id=item.component_id,
            pin_number=item.pin_number,
            pin_name=item.pin_name,
            signal_id=item.signal_id,
            source_refs=item.source_refs,
        )
        for item in plan.pin_bindings
    )
    nets = tuple(
        SchematicNetProposal.create(
            net_id=item.net_id,
            net_name=item.name,
            signal_id=item.signal_id,
            nodes=tuple(
                f"{endpoint.component_id}.{endpoint.pin_number}"
                for endpoint in item.endpoints
            ),
            source_refs=item.source_refs,
        )
        for item in plan.nets
    )
    power_symbols = tuple(
        SchematicPowerProposal.create(
            power_domain_id=item.domain_id,
            name=item.name,
            voltage=item.nominal_voltage,
            ground_net="GND",
            component_refs=(
                tuple(
                    dict.fromkeys(
                        (*item.source_component_ids, *item.consumer_component_ids)
                    )
                )
                or ("mcu",)
            ),
            source_refs=item.source_refs,
        )
        for item in plan.power_domains
    )
    return SchematicProposal.create(
        proposal_id="schematic-proposal-stm32-phase4-4",
        request_id="schematic-request-stm32-phase4-4",
        project_id=plan.project_id,
        conversation_id=plan.conversation_id,
        target_relative_path=plan.target.relative_path,
        components=components,
        pins=pins,
        nets=nets,
        power_symbols=power_symbols,
        source_refs=plan.source_refs,
        design_truth_fingerprint="sha256:" + "1" * 64,
        connectivity_fingerprint="sha256:" + "2" * 64,
        schematic_plan_id=plan.plan_id,
        schematic_plan_fingerprint=plan.fingerprint,
    )


def _rebuild(value, **updates):
    fields = value.model_dump(mode="python")
    fields.pop("fingerprint", None)
    fields.update(updates)
    return type(value).create(**fields)


def _stm32_catalog() -> tuple[ComponentSourceRecord, ...]:
    approved_pins = load_approved_component_source().records[0].pin_facts
    mcu_pin_overrides = {
        "7": ("NRST", ("SWD",)),
        "32": ("CAN_RX", ("CAN_CONTROLLER",)),
        "33": ("CAN_TX", ("CAN_CONTROLLER",)),
        "34": ("SWDIO", ("SWD",)),
        "37": ("SWCLK", ("SWD",)),
    }
    mcu_pins = tuple(
        _rebuild(pin, function=mcu_pin_overrides[pin.number][0],
                 interface_protocols=mcu_pin_overrides[pin.number][1])
        if pin.number in mcu_pin_overrides else pin
        for pin in approved_pins
    )
    def record(
        role: str,
        source_id: str,
        manufacturer: str,
        mpn: str,
        symbol: str,
        footprint: str,
        pins: tuple,
        interfaces: tuple[ComponentInterfaceFact, ...],
        power: tuple,
    ) -> ComponentSourceRecord:
        base = _source(
            role,
            manufacturer,
            mpn,
            pins,
            interfaces=interfaces,
            power=power,
        )
        if mpn == "AMS1117-3.3":
            base = _rebuild(base, supporting_requirements=(
                SupportingComponentRequirement.create(
                    role="OUTPUT_STABILIZATION",
                    description="Output capacitor required for regulator stability",
                    evidence_refs=("datasheet:p4",),
                ),
            ))
        return _rebuild(
            base,
            source_id=source_id,
            source_uri=(
                "https://www.st.com/resource/en/datasheet/stm32f103c8.pdf"
                if mpn == "STM32F103C8T6" else
                "https://www.ti.com/lit/ds/symlink/sn65hvd230.pdf"
                if mpn == "SN65HVD230D" else f"catalog://phase4-4/{mpn}"
            ),
            source_revision=(
                "DS5319 Rev 20" if mpn == "STM32F103C8T6" else
                "SLOS346 Rev O" if mpn == "SN65HVD230D" else
                "2026-09-22-phase4-4"
            ),
            package=(
                "LQFP48" if mpn == "STM32F103C8T6" else
                "SOIC8" if mpn == "SN65HVD230D" else
                "SOT-223" if mpn == "AMS1117-3.3" else base.package
            ),
            symbol=symbol,
            footprint=footprint,
        )

    return (
        record(
            "CONTROLLER",
            "catalog-stm32-controller",
            "STMicroelectronics",
            "STM32F103C8T6",
            "MCU_ST_STM32F1:STM32F103C8Tx",
            "Package_QFP:LQFP-48_7x7mm_P0.5mm",
            mcu_pins,
            (
                ComponentInterfaceFact.create(protocol="POWER", pin_names=("23", "24")),
                ComponentInterfaceFact.create(protocol="CAN_CONTROLLER", pin_names=("32", "33")),
                ComponentInterfaceFact.create(protocol="SWD", pin_names=("34", "37", "7")),
            ),
            (
                _power("consumer", "3V3", "1", minimum=1.8, maximum=3.6),
                _power("consumer", "3V3", "24", minimum=2.0, maximum=3.6),
                _power("consumer", "3V3", "9", minimum=2.0, maximum=3.6),
                _power("consumer", "3V3", "36", minimum=2.0, maximum=3.6),
                _power("consumer", "3V3", "48", minimum=2.0, maximum=3.6),
                _power("ground", "GND", "23", nominal=0.0),
                _power("ground", "GND", "8", nominal=0.0),
                _power("ground", "GND", "35", nominal=0.0),
                _power("ground", "GND", "47", nominal=0.0),
            ),
        ),
        record(
            "POWER",
            "catalog-stm32-regulator",
            "Advanced Monolithic Systems",
            "AMS1117-3.3",
            "Regulator_Linear:AMS1117-3.3",
            "Package_TO_SOT_SMD:SOT-223-3_TabPin2",
            (
                _pin("1", "GND", "ground", power=True),
                _pin("2", "VOUT", "power_output", power=True),
                _pin("3", "VIN", "power_input", power=True),
            ),
            (ComponentInterfaceFact.create(protocol="POWER", pin_names=("1", "2")),),
            (
                _power("source", "3V3", "2"),
                _power("ground", "GND", "1", nominal=0.0),
                _power("consumer", "VIN", "3", nominal=5.0, minimum=4.75, maximum=12.0),
            ),
        ),
        record(
            "COMMUNICATION",
            "catalog-stm32-can",
            "Texas Instruments",
            "SN65HVD230D",
            "Interface_CAN_LIN:SN65HVD230",
            "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
            (
                _pin("1", "D", "CAN_TX", protocols=("CAN_CONTROLLER",)),
                _pin("2", "GND", "ground", power=True),
                _pin("3", "VCC", "supply", power=True),
                _pin("4", "R", "CAN_RX", protocols=("CAN_CONTROLLER",)),
                _pin("6", "CANL", "CANL", protocols=("CAN",)),
                _pin("7", "CANH", "CANH", protocols=("CAN",)),
            ),
            (
                ComponentInterfaceFact.create(protocol="CAN_CONTROLLER", pin_names=("1", "4")),
                ComponentInterfaceFact.create(protocol="CAN", pin_names=("6", "7")),
            ),
            (
                _power("consumer", "3V3", "3"),
                _power("ground", "GND", "2", nominal=0.0),
            ),
        ),
        record(
            "OTHER",
            "catalog-stm32-can-connector",
            "Embrix Golden",
            "CAN-TERMINAL-2P",
            "Connector_Generic:Conn_01x02",
            "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
            (
                _pin("1", "CANH", "CANH", protocols=("CAN",)),
                _pin("2", "CANL", "CANL", protocols=("CAN",)),
            ),
            (ComponentInterfaceFact.create(protocol="CAN", pin_names=("1", "2")),),
            (),
        ),
        record(
            "OTHER",
            "catalog-stm32-swd-header",
            "Embrix Golden",
            "SWD-HEADER-4P",
            "Connector_Generic:Conn_01x04",
            "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
            (
                _pin("1", "SWDIO", "SWDIO", protocols=("SWD",)),
                _pin("2", "SWCLK", "SWCLK", protocols=("SWD",)),
                _pin("3", "GND", "ground", power=True),
                _pin("4", "NRST", "NRST", protocols=("SWD",)),
            ),
            (ComponentInterfaceFact.create(protocol="SWD", pin_names=("1", "2", "4")),),
            (_power("ground", "GND", "3", nominal=0.0),),
        ),
    )


def _approved_stm32_plan(
    intent: CircuitIntentObject,
    *,
    catalog: tuple[ComponentSourceRecord, ...] | None = None,
    on_hardware_selection: Callable[[object], None] | None = None,
):
    context, base_architecture, _base_review = _golden_architecture()
    engineering_plan_proposal = generate_engineering_plan(context)
    engineering_plan, _plan_review = approve_engineering_plan(
        engineering_plan_proposal,
        reviewer="engineer-phase4-4",
        decided_at=NOW,
    )
    component_updates = {
        "stm32": {"name": "STM32F103C8T6", "category": "MCU", "responsibility": "STM32 control and CAN/SWD endpoints"},
        "power": {"name": "3V3 regulator", "category": "POWER", "responsibility": "Generate the MCU 3V3 rail"},
        "sensor/input": {"name": "Communication CAN transceiver", "category": "COMMUNICATION", "responsibility": "Translate MCU CAN signals to physical CAN"},
        "motor/actuator": {"name": "CAN connector", "category": "CONNECTOR", "responsibility": "Expose CANH and CANL"},
        "driver": {"name": "SWD header", "category": "DEBUG", "responsibility": "Expose SWDIO, SWCLK, and GND", "level": ArchitectureLevel.HARDWARE},
    }
    components = tuple(
        _rebuild(
            item,
            **component_updates.get(item.name.casefold(), {}),
        )
        for item in base_architecture.system_components
    )
    ids = {item.name.casefold(): item.component_id for item in components}
    interface_updates = (
        {"protocol": "CAN_CONTROLLER", "source_component_id": ids["stm32f103c8t6"], "target_component_id": ids["communication can transceiver"]},
        {"protocol": "POWER"},
        {"protocol": "SWD", "source_component_id": ids["stm32f103c8t6"], "target_component_id": ids["swd header"]},
        {"source_component_id": ids["control"], "target_component_id": ids["application"]},
        {"source_component_id": ids["control"], "target_component_id": ids["application"]},
    )
    interfaces = tuple(
        _rebuild(item, **interface_updates[index])
        for index, item in enumerate(base_architecture.interfaces)
    )
    interfaces += (
        _rebuild(
            base_architecture.interfaces[0],
            interface_id="architecture-interface-can-physical",
            protocol="CAN",
            source_component_id=ids["communication can transceiver"],
            target_component_id=ids["can connector"],
        ),
    )
    boundary = _rebuild(
        base_architecture.boundary,
        hardware_component_ids=tuple(
            item.component_id
            for item in components
            if item.level is ArchitectureLevel.HARDWARE and item.category.casefold() != "system"
        ),
        software_component_ids=tuple(
            item.component_id
            for item in components
            if item.level is ArchitectureLevel.SOFTWARE
        ),
    )
    architecture = _rebuild(
        base_architecture,
        system_components=tuple(item.model_dump(mode="python") for item in components),
        interfaces=tuple(item.model_dump(mode="python") for item in interfaces),
        boundary=boundary.model_dump(mode="python"),
        status=ArchitectureDesignStatus.PROPOSED,
    )
    from embedded_copilot.engineering_copilot import approve_architecture_design

    architecture, architecture_review = approve_architecture_design(
        architecture,
        reviewer="engineer-phase4-4",
        decided_at=NOW,
    )
    workflow = generate_engineering_workflow(architecture, engineering_plan)
    selection_service = HardwareSelectionService(
        _static_source(_stm32_catalog() if catalog is None else catalog)
    )
    selection_proposal = selection_service.create_proposal(
        architecture,
        context,
        workflow=workflow,
        conversation_id=context.conversation_id,
    )
    selection = selection_service.approve(
        selection_proposal,
        reviewer="engineer-phase4-4",
        decided_at=NOW,
    )
    assert selection.hardware_truth is not None and selection.bom is not None
    if on_hardware_selection is not None:
        on_hardware_selection(selection)
    design_truth = selection_service.project_design_truth(
        generate_design_truth(architecture, conversation_id=context.conversation_id),
        selection.hardware_truth,
        architecture_review=architecture_review,
    )
    plan_proposal = SchematicPlanningService().create_proposal(
        architecture,
        context,
        selection.hardware_truth,
        selection.bom,
        design_truth=design_truth,
        conversation_id=context.conversation_id,
        circuit_intent=intent,
    )
    state = SchematicPlanningService().decide(
        plan_proposal,
        SchematicPlanDecision.APPROVED,
        reviewer="engineer-phase4-4",
        decided_at=NOW,
    )
    assert state.approved_plan is not None
    return context, architecture, design_truth, state.approved_plan


def _intent_bound_to_plan(intent: CircuitIntentObject, plan: SchematicPlan) -> CircuitIntentObject:
    components = {item.mpn: item for item in plan.component_instances}
    mcu = components["STM32F103C8T6"]
    regulator = components["AMS1117-3.3"]
    can = components["SN65HVD230D"]
    connector = components["CAN-TERMINAL-2P"]
    swd_header = components["SWD-HEADER-4P"]
    pins = {
        (item.component_id, item.pin_name): item
        for item in plan.pin_bindings
    }
    nets = {item.name: item for item in plan.nets}

    def pin(component, name: str) -> str:
        return f"pin:{pins[(component.instance_id, name)].binding_id}"

    def net(name: str) -> str:
        if name not in nets:
            raise AssertionError(f"planned net is missing: {name}")
        semantic_name = {
            "SWD1_SWDIO": "SWDIO",
            "SWD1_SWCLK": "SWCLK",
            "SWD1_NRST": "NRST",
        }.get(name, name)
        return f"net:{semantic_name}"

    def block(template: CircuitBlock, role: str, refs: tuple[str, ...]) -> CircuitBlock:
        values = template.model_dump(mode="python")
        values.pop("fingerprint", None)
        values["schematic_binding_refs"] = (
            f"binding:binding-{template.block_id}",
            f"role:{role}",
            *refs,
        )
        return CircuitBlock.model_validate(values)

    templates = {item.block_id: item for item in intent.blocks}
    blocks = (
        block(
            templates["block-power"],
            "POWER",
            (
                f"component:POWER_SOURCE:{regulator.instance_id}",
                f"component:POWER_CONSUMER:{mcu.instance_id}",
                f"component:POWER_CONSUMER:{can.instance_id}",
                f"component:POWER_CONSUMER:{swd_header.instance_id}",
                pin(regulator, "VOUT"),
                pin(regulator, "GND"),
                *(pin(mcu, name) for name in (
                    "VSSA", "VDDA", "VSS_1", "VDD_1",
                    "VSS_2", "VDD_2", "VSS_3", "VDD_3",
                )),
                pin(can, "VCC"),
                pin(can, "GND"),
                pin(swd_header, "GND"),
                net("3V3"),
                net("GND"),
                "power:power-3v3",
                "power:power-gnd",
            ),
        ),
        block(
            templates["block-can"],
            "CAN",
            (
                f"component:MCU:{mcu.instance_id}",
                f"component:CAN_TRANSCEIVER:{can.instance_id}",
                f"component:CONNECTOR:{connector.instance_id}",
                pin(mcu, "PA12"),
                pin(mcu, "PA11"),
                pin(can, "D"),
                pin(can, "R"),
                pin(can, "CANH"),
                pin(can, "CANL"),
                pin(connector, "CANH"),
                pin(connector, "CANL"),
                net("CAN_TX"),
                net("CAN_RX"),
                net("CANH"),
                net("CANL"),
            ),
        ),
        block(
            templates["block-swd"],
            "SWD",
            (
                f"component:MCU:{mcu.instance_id}",
                f"component:SWD_HEADER:{swd_header.instance_id}",
                pin(mcu, "SWDIO"),
                pin(mcu, "SWCLK"),
                pin(mcu, "NRST"),
                pin(swd_header, "SWDIO"),
                pin(swd_header, "SWCLK"),
                pin(swd_header, "NRST"),
                pin(mcu, "VSS_1"),
                pin(swd_header, "GND"),
                net("SWD1_SWDIO"),
                net("SWD1_SWCLK"),
                net("SWD1_NRST"),
                net("GND"),
            ),
        ),
    )
    values = intent.model_dump(mode="python")
    values.pop("fingerprint", None)
    values["blocks"] = blocks
    return CircuitIntentObject.create(**values)


def _static_source(records):
    from embedded_copilot.component_recommendation import StaticComponentSource

    return StaticComponentSource(records)


def _request(proposal: SchematicProposal) -> SchematicProductionRequest:
    return SchematicProductionRequest.create(
        request_id=proposal.request_id,
        project_id=proposal.project_id,
        conversation_id=proposal.conversation_id,
        requester="engineer-phase4-4",
        architecture_id="architecture-stm32-phase4-4",
        design_truth_id="design-truth-stm32-phase4-4",
        hardware_design_id="hardware-design-stm32-phase4-4",
        implementation_id="implementation-stm32-phase4-4",
        implementation_task_id="implementation-task-stm32-phase4-4",
        authorization_id="authorization-stm32-phase4-4",
        authorization_request_id="authorization-request-stm32-phase4-4",
        design_truth_fingerprint=proposal.design_truth_fingerprint,
        hardware_design_fingerprint="sha256:" + "3" * 64,
        implementation_fingerprint="sha256:" + "4" * 64,
        authorization_fingerprint="sha256:" + "5" * 64,
        workspace_identity="project-root",
        target_relative_path=proposal.target_relative_path,
        source_refs=proposal.source_refs,
        readiness_fingerprint="sha256:" + "6" * 64,
        schematic_plan_id=proposal.schematic_plan_id,
        schematic_plan_fingerprint=proposal.schematic_plan_fingerprint,
    )


def test_phase4_4_stm32_blocks_unresolved_vin_before_execution(tmp_path: Path) -> None:
    from embedded_copilot.engineering_copilot.schematic_planning import (
        SchematicPlanningBlocked,
    )

    with pytest.raises(SchematicPlanningBlocked) as error:
        _approved_stm32_plan(_project_intent("project-1"))

    assert error.value.reason_code == "POWER_SOURCE_UNRESOLVED"
    assert not (tmp_path / "execution.sqlite3").exists()
    assert not (tmp_path / "workspace.sqlite3").exists()
    assert not (tmp_path / "stm32-control-board").exists()


def test_legacy_numeric_hardware_truth_and_approval_fingerprints_remain_readable() -> None:
    captured: list[object] = []
    with pytest.raises(ValueError):
        _approved_stm32_plan(_project_intent("project-1"), on_hardware_selection=captured.append)
    assert len(captured) == 1
    selection = captured[0]
    assert selection.hardware_truth.fingerprint == "sha256:3abf17838fe32e86fe7fe06326c9a57c8efe7903fa765848abe8a2c19c25849c"
    assert selection.approval.fingerprint == "sha256:5588d16eb1a77c1187028250071da45ef42d246e65e21d4a2701ea2a30c8f310"
    assert type(selection.hardware_truth).model_validate_json(selection.hardware_truth.model_dump_json()).fingerprint == selection.hardware_truth.fingerprint
    assert type(selection.approval).model_validate_json(selection.approval.model_dump_json()).fingerprint == selection.approval.fingerprint

def test_phase4_4_approved_intent_project_mismatch_fails_closed() -> None:
    application, integration, request, routing, spec, _port, _context = _runtime(
        receipt_port=FixtureQualifiedReceiptReferencePort()
    )
    approved_other_project = _intent()
    assert approved_other_project.status is LifecycleStatus.APPROVED

    with pytest.raises(EngineeringObjectFlowError) as error:
        application.run_engineering_object_flow(
            request,
            routing_context=routing,
            circuit_intent=spec,
            approved_circuit_intent=approved_other_project,
            evidence_reference=_evidence(),
            integration=integration,
            reviewer="engineer-reviewer",
        )

    assert error.value.code is EngineeringObjectFlowErrorCode.PROJECT_IDENTITY_MISMATCH

