from __future__ import annotations

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
from embedded_copilot.engineering_copilot import generate_engineering_workflow
from embedded_copilot.engineering_copilot.hardware_selection import (
    ComponentRequirement,
    HardwareSelectionService,
    _evaluate,
)
from tests.engineering_copilot.test_hardware_selection_g1 import (
    _approved_architecture,
    _catalog,
)
from tests.engineering_copilot.test_p0_architecture_design_contract import (
    _approved_plan,
)


def _source(
    *, interface_protocol: str, power_pin: str = "3",
    pin_protocol: str = "CAN", mpn: str = "TEST-CAN-CONTROLLER",
) -> ComponentSourceRecord:
    return ComponentSourceRecord.create(
        source_id="s02-source-can-controller",
        source_uri="catalog://s02-consistency-example",
        source_revision="test-r1",
        manufacturer="Test manufacturer",
        mpn=mpn,
        category="CONTROLLER",
        package="TEST-PACKAGE",
        description="Offline consistency fixture, not a datasheet claim",
        symbol="Test:Controller",
        footprint="Test:Controller",
        supported_roles=("CONTROLLER",),
        pin_facts=(
            ComponentPinFact.create(
                number="1", name="CAN_TX", function="CAN_TX", pad_number="1",
                interface_protocols=(pin_protocol,),
            ),
            ComponentPinFact.create(
                number="2", name="CAN_RX", function="CAN_RX", pad_number="2",
                interface_protocols=(pin_protocol,),
            ),
            ComponentPinFact.create(
                number="3", name="VDD", function="supply", pad_number="3", is_power=True,
            ),
        ),
        electrical_facts=(
            ComponentElectricalFact.create(
                parameter="operating_voltage", minimum=3.0, typical=3.3,
                maximum=3.6, unit="V",
            ),
        ),
        interface_facts=(
            ComponentInterfaceFact.create(protocol=interface_protocol, pin_names=("1", "2")),
        ),
        power_facts=(
            ComponentPowerFact.create(
                domain="3V3", role="consumer", nominal_voltage=3.3,
                pin_numbers=(power_pin,),
            ),
        ),
    )


def _requirement(*, interface_protocol: str = "CAN") -> ComponentRequirement:
    return ComponentRequirement(
        slot_id="s02-controller-slot",
        architecture_component_id="s02-controller",
        role="CONTROLLER",
        category="CONTROLLER",
        functional_requirements=("CAN controller",),
        interface_requirements=(interface_protocol,),
    )


def test_s02_rejects_interface_fact_with_wrong_pin_protocol() -> None:
    result = _evaluate(_requirement(interface_protocol="UART"), _source(interface_protocol="UART"))

    assert result.selectable is False
    assert "interface_pin_protocol_mismatch:UART" in result.rejection_reasons


@pytest.mark.parametrize("power_pin", ("9", "1"))
def test_s02_rejects_power_fact_for_missing_or_non_power_pin(power_pin: str) -> None:
    result = _evaluate(_requirement(), _source(interface_protocol="CAN", power_pin=power_pin))

    assert result.selectable is False
    assert "power_pin_reference_invalid" in result.rejection_reasons


def test_s02_accepts_internally_consistent_source_facts() -> None:
    result = _evaluate(_requirement(), _source(interface_protocol="CAN"))

    assert result.selectable is True


def _ams1117_source(
    *, manufacturer: str = "Advanced Monolithic Systems",
    pins: tuple[ComponentPinFact, ...] | None = None,
    power_facts: tuple[ComponentPowerFact, ...] | None = None,
    stabilization: bool = True,
    stabilization_refs: tuple[str, ...] = ("datasheet:p4",),
    stabilization_description: str = "22uF solid tantalum COUT for stability",
    package: str = "SOT-223",
) -> ComponentSourceRecord:
    return ComponentSourceRecord.create(
        source_id="ams1117-source", source_uri="https://static.datasheets.com/doc/12260236-advanced-monolithic-systems-ams1117-ds.pdf",
        source_revision="undated-mirror", manufacturer=manufacturer, mpn="AMS1117-3.3",
        category="VOLTAGE_REGULATOR", package=package, description="Fixed 3.3 V regulator",
        symbol="Regulator_Linear:AMS1117-3.3", footprint="Package_TO_SOT_SMD:SOT-223",
        supported_roles=("POWER_REGULATOR",),
        pin_facts=pins or (
            ComponentPinFact.create(number="1", name="GND", function="GND", pad_number="1", is_power=True),
            ComponentPinFact.create(number="2", name="VOUT", function="output", pad_number="2", is_power=True),
            ComponentPinFact.create(number="3", name="VIN", function="input", pad_number="3", is_power=True),
        ),
        electrical_facts=(ComponentElectricalFact.create(parameter="input_voltage", minimum=4.75, maximum=12.0, unit="V"),),
        power_facts=power_facts or (
            ComponentPowerFact.create(domain="INPUT", role="consumer", nominal_voltage=5.0, minimum_voltage=4.75, maximum_voltage=12.0, pin_numbers=("3",)),
            ComponentPowerFact.create(domain="3V3", role="source", nominal_voltage=3.3, pin_numbers=("2",)),
            ComponentPowerFact.create(domain="GND", role="ground", nominal_voltage=0.0, pin_numbers=("1",)),
        ),
        supporting_requirements=(SupportingComponentRequirement.create(
            role="OUTPUT_STABILIZATION", description=stabilization_description,
            evidence_refs=stabilization_refs,
        ),) if stabilization else (),
    )


def _ams_requirement() -> ComponentRequirement:
    return ComponentRequirement(
        slot_id="s02-regulator", architecture_component_id="s02-regulator",
        role="POWER_REGULATOR", category="VOLTAGE_REGULATOR",
        functional_requirements=("3.3 V regulator",),
    )


@pytest.mark.parametrize(("case", "expected_reason"), (
    ("missing_vin", "ams1117_required_pin_missing:VIN"),
    ("wrong_manufacturer", "ams1117_manufacturer_mismatch"),
    ("missing_stabilization", "ams1117_output_stabilization_evidence_missing"),
    ("wrong_package", "ams1117_unsupported_package"),
    ("missing_vin_power_fact", "ams1117_power_fact_missing:VIN"),
))
def test_s02_rejects_incomplete_ams1117_source(case: str, expected_reason: str) -> None:
    source = _ams1117_source(
        manufacturer="Unknown Manufacturer" if case == "wrong_manufacturer" else "Advanced Monolithic Systems",
        pins=(
            ComponentPinFact.create(number="1", name="GND", function="GND", pad_number="1", is_power=True),
            ComponentPinFact.create(number="2", name="VOUT", function="output", pad_number="2", is_power=True),
        ) if case == "missing_vin" else None,
        power_facts=(
            ComponentPowerFact.create(domain="3V3", role="source", nominal_voltage=3.3, pin_numbers=("2",)),
            ComponentPowerFact.create(domain="GND", role="ground", nominal_voltage=0.0, pin_numbers=("1",)),
        ) if case == "missing_vin_power_fact" else None,
        stabilization=case != "missing_stabilization",
        stabilization_refs=() if case == "missing_stabilization" else ("datasheet:p4",),
        package="QFN" if case == "wrong_package" else "SOT-223",
    )

    result = _evaluate(_ams_requirement(), source)

    assert result.selectable is False
    assert expected_reason in result.rejection_reasons


def test_s02_accepts_complete_ams1117_datasheet_source_as_candidate() -> None:
    result = _evaluate(_ams_requirement(), _ams1117_source(manufacturer="Advanced Monolithic Systems, Inc."))

    assert result.selectable is True


def test_s02_accepts_stability_supported_output_capacitor_without_fixed_value_text() -> None:
    result = _evaluate(
        _ams_requirement(),
        _ams1117_source(
            stabilization_description="Output capacitor selected for the fixed-version stability conditions",
        ),
    )

    assert result.selectable is True


def test_s02_rejects_ambiguous_interface_pin_name() -> None:
    original = _source(interface_protocol="CAN")
    pins = original.pin_facts
    duplicate_name = ComponentPinFact.create(
        **{**pins[1].model_dump(mode="python"), "name": pins[0].name}
    )
    source = ComponentSourceRecord.create(
        **{
            **original.model_dump(mode="python", exclude={"fingerprint"}),
            "pin_facts": (pins[0], duplicate_name, pins[2]),
            "interface_facts": (
                ComponentInterfaceFact.create(protocol="CAN", pin_names=(pins[0].name,)),
            ),
        }
    )

    result = _evaluate(_requirement(), source)

    assert result.selectable is False
    assert "interface_pin_reference_ambiguous" in result.rejection_reasons


def test_s02_rejects_uart_claim_for_sn65hvd230() -> None:
    source = _source(
        interface_protocol="UART", pin_protocol="UART", mpn="SN65HVD230",
    )
    result = _evaluate(_requirement(interface_protocol="UART"), source)

    assert result.selectable is False
    assert "unsupported_part_protocol:UART" in result.rejection_reasons


def test_s02_invalid_power_pin_cannot_enter_hardware_selection_proposal() -> None:
    context, architecture, _ = _approved_architecture()
    workflow = generate_engineering_workflow(architecture, _approved_plan()[1])
    records = _catalog().records
    invalid_controller = ComponentSourceRecord.create(
        **{
            **records[0].model_dump(mode="python", exclude={"fingerprint"}),
            "power_facts": (
                ComponentPowerFact.create(
                    domain="3V3", role="consumer", nominal_voltage=3.3,
                    pin_numbers=("missing-pin",),
                ),
            ),
        }
    )
    source = StaticComponentSource((invalid_controller, *records[1:]))

    with pytest.raises(ValueError, match="power_pin_reference_invalid"):
        HardwareSelectionService(source).create_proposal(
            architecture, context, workflow=workflow,
            conversation_id=context.conversation_id,
        )
