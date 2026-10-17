from __future__ import annotations

from datetime import UTC, datetime

import pytest

from embedded_copilot.application.engineering_runtime_integration import (
    CircuitIntentSpec,
    EngineeringObjectFlowError,
    EngineeringObjectFlowErrorCode,
    EngineeringObjectIntegrationService,
)
from embedded_copilot.engineering_copilot import (
    SchematicComponentInstance,
    SchematicEndpoint,
    SchematicNet,
    SchematicPinBinding,
    SchematicPlan,
    SchematicPlanningStatus,
    SchematicPowerDomain,
    SchematicSignal,
    SchematicTarget,
)
from embedded_copilot.engineering_runtime import (
    CircuitBlock,
    CircuitIntentObject,
    EngineeringObjectRuntime,
    LifecycleStatus,
    Provenance,
    SchematicDesignObject,
)

from .test_engineering_runtime_integration import (
    FixtureQualifiedReceiptReferencePort,
    _evidence,
    _runtime,
)

FP = "sha256:" + "a" * 64


class _EvidencePort:
    def resolve(self, request, reference):
        return reference


class _ContextProvider:
    def get_context(self, request):
        return None


def _provenance(project_id: str = "project-stm32") -> Provenance:
    return Provenance.create(
        source="TEST",
        source_ref=f"{project_id}:phase4-3-1",
        created_by="test-user",
        created_at=datetime(2026, 9, 22, tzinfo=UTC),
    )


def _component(instance_id: str, symbol: str) -> SchematicComponentInstance:
    return SchematicComponentInstance(
        instance_id=instance_id,
        architecture_component_id=f"architecture-{instance_id}",
        hardware_slot_id=f"slot-{instance_id}",
        design_truth_component_id=instance_id,
        manufacturer="approved-source",
        mpn=f"approved-{instance_id}",
        symbol=symbol,
        footprint=f"approved-footprint-{instance_id}",
        source_refs=(f"source-{instance_id}",),
        evidence_fingerprints=(FP,),
    )


def _pin(
    component_id: str,
    name: str,
    signal_id: str,
) -> SchematicPinBinding:
    return SchematicPinBinding.create(
        binding_id=f"pin-{component_id}-{name.casefold()}",
        component_id=component_id,
        architecture_component_id=f"architecture-{component_id}",
        pin_number=name,
        pin_name=name,
        function=signal_id,
        pad_number=name,
        signal_id=signal_id,
        source_refs=(f"source-{component_id}",),
        evidence_fingerprints=(FP,),
    )


def _signal(signal_id: str, protocol: str, source: str, target: str) -> SchematicSignal:
    return SchematicSignal.create(
        signal_id=signal_id,
        name=signal_id,
        semantic_role=protocol,
        direction="BIDIRECTIONAL",
        voltage_domain="3V3",
        protocol=protocol,
        source_component_id=source,
        target_component_ids=(target,),
        source_requirement_refs=(f"requirement-{signal_id}",),
        source_refs=(f"source-{signal_id}",),
    )


def _net(
    net_id: str,
    signal_id: str,
    first: SchematicPinBinding,
    second: SchematicPinBinding,
    power_domain: str | None = None,
) -> SchematicNet:
    return SchematicNet.create(
        net_id=net_id,
        name=net_id,
        signal_id=signal_id,
        endpoints=(
            SchematicEndpoint(
                component_id=first.component_id,
                pin_number=first.pin_number,
                binding_id=first.binding_id,
            ),
            SchematicEndpoint(
                component_id=second.component_id,
                pin_number=second.pin_number,
                binding_id=second.binding_id,
            ),
        ),
        power_domain=power_domain,
        source_refs=(f"source-{net_id}",),
    )


def _intent() -> CircuitIntentObject:
    def block(block_id: str, role: str, refs: tuple[str, ...]) -> CircuitBlock:
        return CircuitBlock(
            block_id=block_id,
            function=f"STM32 {role} block",
            schematic_binding_refs=(
                f"binding:binding-{role.casefold()}",
                f"role:{role}",
                *refs,
            ),
        )

    return CircuitIntentObject.create(
        object_id="circuit-intent-stm32",
        project_id="project-stm32",
        status=LifecycleStatus.APPROVED,
        function="STM32 Power CAN SWD design intent",
        topology=("5V to 3V3", "MCU to CAN transceiver", "SWD header"),
        blocks=(
            block(
                "block-power",
                "POWER",
                (
                    "component:POWER_SOURCE:regulator",
                    "component:POWER_CONSUMER:mcu",
                    "pin:pin-regulator-vout",
                    "pin:pin-mcu-vdd",
                    "net:3V3",
                    "net:GND",
                    "power:power-3v3",
                    "power:power-gnd",
                ),
            ),
            block(
                "block-can",
                "CAN",
                (
                    "component:MCU:mcu",
                    "component:CAN_TRANSCEIVER:can",
                    "component:CONNECTOR:can-connector",
                    "pin:pin-mcu-can_tx",
                    "pin:pin-mcu-can_rx",
                    "pin:pin-can-txd",
                    "pin:pin-can-rxd",
                    "pin:pin-can-canh",
                    "pin:pin-can-canl",
                    "pin:pin-can-connector-canh",
                    "pin:pin-can-connector-canl",
                    "net:CAN_TX",
                    "net:CAN_RX",
                    "net:CANH",
                    "net:CANL",
                ),
            ),
            block(
                "block-swd",
                "SWD",
                (
                    "component:MCU:mcu",
                    "component:SWD_HEADER:swd-header",
                    "pin:pin-mcu-swdio",
                    "pin:pin-mcu-swclk",
                    "pin:pin-swd-header-swdio",
                    "pin:pin-swd-header-swclk",
                    "pin:pin-mcu-gnd",
                    "pin:pin-swd-header-gnd",
                    "net:SWDIO",
                    "net:SWCLK",
                    "net:GND",
                ),
            ),
        ),
        provenance=_provenance(),
    )


def _plan(intent: CircuitIntentObject) -> SchematicPlan:
    components = (
        _component("mcu", "STM32"),
        _component("regulator", "REGULATOR"),
        _component("can", "CAN_TRANSCEIVER"),
        _component("can-connector", "CAN_CONNECTOR"),
        _component("swd-header", "SWD_HEADER"),
    )
    pins = (
        _pin("mcu", "VDD", "signal-3v3"),
        _pin("regulator", "VOUT", "signal-3v3"),
        _pin("mcu", "GND", "signal-gnd"),
        _pin("regulator", "GND", "signal-gnd"),
        _pin("can", "GND", "signal-gnd"),
        _pin("mcu", "CAN_TX", "signal-can-tx"),
        _pin("mcu", "CAN_RX", "signal-can-rx"),
        _pin("can", "TXD", "signal-can-tx"),
        _pin("can", "RXD", "signal-can-rx"),
        _pin("can", "CANH", "signal-canh"),
        _pin("can", "CANL", "signal-canl"),
        _pin("can-connector", "CANH", "signal-canh"),
        _pin("can-connector", "CANL", "signal-canl"),
        _pin("mcu", "SWDIO", "signal-swdio"),
        _pin("mcu", "SWCLK", "signal-swdclk"),
        _pin("swd-header", "SWDIO", "signal-swdio"),
        _pin("swd-header", "SWCLK", "signal-swdclk"),
        _pin("swd-header", "GND", "signal-gnd"),
    )
    by_key = {(item.component_id, item.pin_name): item for item in pins}
    signals = (
        _signal("signal-3v3", "POWER", "regulator", "mcu"),
        _signal("signal-gnd", "POWER", "regulator", "can"),
        _signal("signal-gnd-swd", "POWER", "mcu", "swd-header"),
        _signal("signal-can-tx", "CAN", "mcu", "can"),
        _signal("signal-can-rx", "CAN", "mcu", "can"),
        _signal("signal-canh", "CAN", "can", "can-connector"),
        _signal("signal-canl", "CAN", "can", "can-connector"),
        _signal("signal-swdio", "SWD", "mcu", "swd-header"),
        _signal("signal-swdclk", "SWD", "mcu", "swd-header"),
    )
    nets = (
        _net("3V3", "signal-3v3", by_key[("regulator", "VOUT")], by_key[("mcu", "VDD")], "3V3"),
        SchematicNet.create(
            net_id="GND",
            name="GND",
            signal_id="signal-gnd",
            endpoints=tuple(
                SchematicEndpoint(
                    component_id=item.component_id,
                    pin_number=item.pin_number,
                    binding_id=item.binding_id,
                )
                for item in (
                    by_key[("regulator", "GND")],
                    by_key[("can", "GND")],
                    by_key[("mcu", "GND")],
                    by_key[("swd-header", "GND")],
                )
            ),
            power_domain="GND",
            source_refs=("source-GND",),
        ),
        _net("CAN_TX", "signal-can-tx", by_key[("mcu", "CAN_TX")], by_key[("can", "TXD")]),
        _net("CAN_RX", "signal-can-rx", by_key[("mcu", "CAN_RX")], by_key[("can", "RXD")]),
        _net("CANH", "signal-canh", by_key[("can", "CANH")], by_key[("can-connector", "CANH")]),
        _net("CANL", "signal-canl", by_key[("can", "CANL")], by_key[("can-connector", "CANL")]),
        _net("SWDIO", "signal-swdio", by_key[("mcu", "SWDIO")], by_key[("swd-header", "SWDIO")]),
        _net("SWCLK", "signal-swdclk", by_key[("mcu", "SWCLK")], by_key[("swd-header", "SWCLK")]),
    )
    domains = (
        SchematicPowerDomain.create(
            domain_id="power-3v3",
            name="3V3",
            nominal_voltage="3V3",
            source_component_ids=("regulator",),
            consumer_component_ids=("mcu", "can", "swd-header"),
            ground_reference="power-gnd",
            source_refs=("source-power-3v3",),
        ),
        SchematicPowerDomain.create(
            domain_id="power-gnd",
            name="GND",
            nominal_voltage="0V",
            source_component_ids=(),
            consumer_component_ids=(),
            ground_reference="power-gnd",
            source_refs=("source-power-gnd",),
        ),
    )
    return SchematicPlan.create(
        plan_id="schematic-plan-stm32",
        project_id=intent.project_id,
        conversation_id="conversation-stm32",
        architecture_id="architecture-stm32",
        architecture_fingerprint=FP,
        requirements_fingerprint=FP,
        hardware_truth_id="hardware-truth-stm32",
        hardware_truth_fingerprint=FP,
        bom_id="bom-stm32",
        bom_fingerprint=FP,
        circuit_intent_id=intent.object_id,
        circuit_intent_revision=intent.revision,
        circuit_intent_fingerprint=intent.fingerprint,
        target=SchematicTarget.create(
            project_id=intent.project_id,
            relative_path="hardware/main.kicad_sch",
            sheet_name="main",
            eda_format="KICAD_SCHEMATIC",
            component_instance_ids=tuple(item.instance_id for item in components),
            source_refs=("source-schematic-plan",),
        ),
        component_instances=components,
        signals=signals,
        pin_bindings=pins,
        nets=nets,
        power_domains=domains,
        interfaces=(),
        source_refs=("source-schematic-plan",),
        evidence_fingerprints=(FP,),
        status=SchematicPlanningStatus.APPROVED,
        blocking_reasons=(),
    )


def _schematic() -> SchematicDesignObject:
    return SchematicDesignObject.create(
        object_id="schematic-stm32",
        project_id="project-stm32",
        status=LifecycleStatus.ACTIVE,
        symbols=(
            "mcu:STM32",
            "regulator:REGULATOR",
            "can:CAN_TRANSCEIVER",
            "can-connector:CAN_CONNECTOR",
            "swd-header:SWD_HEADER",
        ),
        pins=(
            "mcu:VDD",
            "regulator:VOUT",
            "mcu:GND",
            "regulator:GND",
            "can:GND",
            "mcu:CAN_TX",
            "mcu:CAN_RX",
            "can:TXD",
            "can:RXD",
            "can:CANH",
            "can:CANL",
            "can-connector:CANH",
            "can-connector:CANL",
            "mcu:SWDIO",
            "mcu:SWCLK",
            "swd-header:SWDIO",
            "swd-header:SWCLK",
            "swd-header:GND",
        ),
        nets=("3V3", "GND", "CAN_TX", "CAN_RX", "CANH", "CANL", "SWDIO", "SWCLK"),
        provenance=_provenance(),
    )


def _service() -> EngineeringObjectIntegrationService:
    return EngineeringObjectIntegrationService(
        EngineeringObjectRuntime(project_id="project-stm32"),
        evidence_port=_EvidencePort(),
        context_provider=_ContextProvider(),
    )


def test_application_binding_materialization_generates_power_can_and_swd() -> None:
    service = _service()
    intent = _intent()
    plan = _plan(intent)

    projected = service.materialize_circuit_bindings(intent, plan, _schematic())

    assert {item.circuit_block_id for item in projected.circuit_bindings} == {
        "block-power",
        "block-can",
        "block-swd",
    }
    by_block = {item.circuit_block_id: item for item in projected.circuit_bindings}
    assert {"3V3", "GND"} <= set(by_block["block-power"].net_ids)
    assert {"CAN_TX", "CAN_RX", "CANH", "CANL"} <= set(by_block["block-can"].net_ids)
    assert {"SWDIO", "SWCLK", "GND"} <= set(by_block["block-swd"].net_ids)
    assert {"power-3v3", "power-gnd"} <= set(by_block["block-power"].power_domain_ids)
    assert all(item.circuit_intent_fingerprint == intent.fingerprint for item in by_block.values())


def test_can_controller_net_alias_requires_explicit_can_protocol_authority() -> None:
    plan = _plan(_intent())
    source = next(item for item in plan.nets if item.net_id == "CAN_TX")
    explicit_can_net = source.model_copy(
        update={"net_id": "net-mcu-can-tx", "name": "MCU_CAN_TX"}
    )

    resolved = EngineeringObjectIntegrationService._resolve_net_ref(
        "CAN_TX",
        (explicit_can_net,),
        tuple(plan.signals),
    )

    assert resolved == "net-mcu-can-tx"


@pytest.mark.parametrize("name,protocol", (("UART_TX", "UART"), ("DEBUG_TX", "UNKNOWN")))
def test_non_can_tx_suffix_is_not_promoted_to_can(
    name: str,
    protocol: str,
) -> None:
    plan = _plan(_intent())
    source = next(item for item in plan.nets if item.net_id == "CAN_TX")
    non_can_net = source.model_copy(update={"net_id": f"net-{name.casefold()}", "name": name})
    source_signal = next(item for item in plan.signals if item.signal_id == source.signal_id)
    non_can_signal = source_signal.model_copy(update={"protocol": protocol})

    with pytest.raises(ValueError, match="net ref is missing or ambiguous"):
        EngineeringObjectIntegrationService._resolve_net_ref(
            "CAN_TX",
            (non_can_net,),
            (non_can_signal,),
        )


def test_canh_is_not_controller_side_can_tx_authority() -> None:
    plan = _plan(_intent())
    canh = next(item for item in plan.nets if item.net_id == "CANH")

    with pytest.raises(ValueError, match="net ref is missing or ambiguous"):
        EngineeringObjectIntegrationService._resolve_net_ref(
            "CAN_TX",
            (canh,),
            tuple(plan.signals),
        )


def test_application_runtime_flow_materializes_bindings_into_projection() -> None:
    baseline_application, baseline_integration, request, routing, _, _, _ = _runtime(
        receipt_port=FixtureQualifiedReceiptReferencePort()
    )
    intent = _intent()
    spec = CircuitIntentSpec(
        function=intent.function,
        topology=intent.topology,
        parameters=intent.parameters,
        calculation=intent.calculation,
        assumptions=intent.assumptions,
        risks=intent.risks,
        blocks=intent.blocks,
    )
    baseline = baseline_application.run_engineering_object_flow(
        request,
        routing_context=routing,
        circuit_intent=spec,
        evidence_reference=_evidence(),
        integration=baseline_integration,
    )
    plan = _plan(baseline.circuit_intent)

    application, integration, request, routing, _, _, _ = _runtime(
        receipt_port=FixtureQualifiedReceiptReferencePort()
    )
    result = application.run_engineering_object_flow(
        request,
        routing_context=routing,
        circuit_intent=spec,
        evidence_reference=_evidence(),
        integration=integration,
        schematic_plan=plan,
    )

    assert len(result.schematic_design.circuit_bindings) == 3
    assert {
        item.circuit_block_id for item in result.schematic_design.circuit_bindings
    } == {"block-power", "block-can", "block-swd"}


@pytest.mark.parametrize(
    ("block_id", "missing_ref"),
    [("block-power", "net:3V3"), ("block-can", "component:CAN_TRANSCEIVER:can")],
)
def test_binding_materialization_blocks_missing_net_or_component(
    block_id: str, missing_ref: str
) -> None:
    service = _service()
    intent = _intent()
    values = intent.blocks
    changed = tuple(
        CircuitBlock.model_validate(
            {
                **block.model_dump(mode="python"),
                "fingerprint": "sha256:" + "0" * 64,
                "schematic_binding_refs": tuple(
                    ref for ref in block.schematic_binding_refs if ref != missing_ref
                ),
            },
            strict=False,
        )
        if block.block_id == block_id
        else block
        for block in values
    )
    changed_intent = CircuitIntentObject.create(
        **{**intent.model_dump(mode="python"), "fingerprint": "sha256:" + "0" * 64, "blocks": changed}
    )
    changed_plan = _plan(changed_intent)

    with pytest.raises(EngineeringObjectFlowError) as error:
        service.materialize_circuit_bindings(changed_intent, changed_plan, _schematic())

    assert error.value.code is EngineeringObjectFlowErrorCode.BINDING_BLOCKED
    assert "BLOCKED" in str(error.value)


def test_binding_materialization_rejects_cross_project_inputs() -> None:
    service = _service()
    intent = _intent()
    other = SchematicDesignObject.create(
        **{
            **_schematic().model_dump(mode="python"),
            "project_id": "project-other",
            "fingerprint": "sha256:" + "0" * 64,
            "provenance": _provenance("project-other"),
        }
    )

    with pytest.raises(EngineeringObjectFlowError) as error:
        service.materialize_circuit_bindings(intent, _plan(intent), other)

    assert error.value.code is EngineeringObjectFlowErrorCode.PROJECT_IDENTITY_MISMATCH


def test_binding_materialization_rejects_intent_fingerprint_mismatch() -> None:
    service = _service()
    intent = _intent()
    plan = _plan(intent).model_copy(
        update={"circuit_intent_fingerprint": "sha256:" + "b" * 64}
    )

    with pytest.raises(EngineeringObjectFlowError) as error:
        service.materialize_circuit_bindings(intent, plan, _schematic())

    assert error.value.code is EngineeringObjectFlowErrorCode.BINDING_BLOCKED


def test_binding_materialization_does_not_change_circuit_intent_fingerprint() -> None:
    service = _service()
    intent = _intent()
    fingerprint = intent.fingerprint

    service.materialize_circuit_bindings(intent, _plan(intent), _schematic())

    assert intent.fingerprint == fingerprint
