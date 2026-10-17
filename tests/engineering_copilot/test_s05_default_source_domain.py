from __future__ import annotations

import pytest

from embedded_copilot.component_recommendation import (
    ComponentElectricalFact,
    ComponentPinFact,
    ComponentPowerFact,
    ComponentSourceRecord,
    StaticComponentSource,
)
from embedded_copilot.engineering_copilot.hardware_selection import (
    ComponentRequirement,
    _evaluate,
)
from embedded_copilot.engineering_copilot.schematic_planning import (
    SchematicPlanningBlocked,
)

from .test_schematic_planning_g2 import _catalog, _proposal

# ST DS5319 Rev 20, Figure 8 and Table 5; exact LQFP48 pad identities.
_PIN_NAMES = (
    "VBAT", "PC13-TAMPER-RTC", "PC14-OSC32_IN", "PC15-OSC32_OUT",
    "PD0-OSC_IN", "PD1-OSC_OUT", "NRST", "VSSA", "VDDA", "PA0-WKUP",
    "PA1", "PA2", "PA3", "PA4", "PA5", "PA6", "PA7", "PB0", "PB1",
    "PB2", "PB10", "PB11", "VSS_1", "VDD_1", "PB12", "PB13",
    "PB14", "PB15", "PA8", "PA9", "PA10", "PA11", "PA12", "PA13",
    "VSS_2", "VDD_2", "PA14", "PA15", "PB3", "PB4", "PB5", "PB6",
    "PB7", "BOOT0", "PB8", "PB9", "VSS_3", "VDD_3",
)
_GROUNDS = {"8", "23", "35", "47"}
_SUPPLIES = {"1", "9", "24", "36", "48"}


def _stm32_source() -> ComponentSourceRecord:
    pins = tuple(
        ComponentPinFact.create(
            number=str(index), name=name, function=name, pad_number=str(index),
            is_power=str(index) in _GROUNDS | _SUPPLIES,
        )
        for index, name in enumerate(_PIN_NAMES, 1)
    )
    power = tuple(
        ComponentPowerFact.create(
            domain=("GND" if number in _GROUNDS else "VBAT" if number == "1" else "VDD"),
            role=("ground" if number in _GROUNDS else "consumer"),
            nominal_voltage=(0.0 if number in _GROUNDS else None),
            minimum_voltage=(None if number in _GROUNDS else 1.8 if number == "1" else 2.0),
            maximum_voltage=(None if number in _GROUNDS else 3.6),
            pin_numbers=(number,),
        )
        for number in sorted(_GROUNDS | _SUPPLIES, key=int)
    )
    return ComponentSourceRecord.create(
        source_id="st-ds5319-r20-test", source_uri="https://www.st.com/resource/en/datasheet/stm32f103c8.pdf",
        source_revision="DS5319 Rev 20", manufacturer="STMicroelectronics",
        mpn="STM32F103C8T6", category="MCU", package="LQFP48",
        description="Offline exact pin evidence fixture; no project power decision",
        symbol="MCU_ST_STM32F1:STM32F103C8Tx",
        footprint="Package_QFP:LQFP-48_7x7mm_P0.5mm", supported_roles=("CONTROLLER",),
        pin_facts=pins,
        electrical_facts=(ComponentElectricalFact.create(
            parameter="operating_voltage", minimum=2.0, maximum=3.6, unit="V",
        ),),
        power_facts=power,
    )


def _requirement() -> ComponentRequirement:
    return ComponentRequirement(
        slot_id="mcu", architecture_component_id="mcu", role="CONTROLLER", category="MCU",
        functional_requirements=("MCU",),
    )


def _rebuild(record: ComponentSourceRecord, **changes: object) -> ComponentSourceRecord:
    return ComponentSourceRecord.create(**{**record.model_dump(mode="python", exclude={"fingerprint"}), **changes})


def test_stm32_full_pin_and_nine_power_fact_source_is_selectable() -> None:
    assert len(_PIN_NAMES) == 48
    assert _evaluate(_requirement(), _stm32_source()).selectable


@pytest.mark.parametrize(("change", "expected"), (
    ("missing_pin", "package_pin_map_incomplete"),
    ("wrong_name", "package_pin_name_mismatch:32"),
    ("wrong_pad", "package_pad_mismatch:32"),
    ("missing_vbat_range", "required_power_range_missing:1"),
    ("missing_vbat_fact", "required_power_pin_missing:1"),
))
def test_stm32_exact_package_rejects_incomplete_source(change: str, expected: str) -> None:
    source = _stm32_source()
    pins = list(source.pin_facts)
    facts = list(source.power_facts)
    if change == "missing_pin":
        pins = [pin for pin in pins if pin.number != "12"]
    elif change in {"wrong_name", "wrong_pad"}:
        index = next(i for i, pin in enumerate(pins) if pin.number == "32")
        values = pins[index].model_dump(mode="python")
        values["name" if change == "wrong_name" else "pad_number"] = "WRONG"
        pins[index] = ComponentPinFact.create(**values)
    elif change == "missing_vbat_fact":
        facts = [fact for fact in facts if "1" not in fact.pin_numbers]
    else:
        index = next(i for i, fact in enumerate(facts) if "1" in fact.pin_numbers)
        facts[index] = ComponentPowerFact.create(
            domain="VBAT", role="consumer", nominal_voltage=3.3, pin_numbers=("1",),
        )
    result = _evaluate(_requirement(), _rebuild(source, pin_facts=tuple(pins), power_facts=tuple(facts)))
    assert not result.selectable
    assert expected in result.rejection_reasons


def test_schematic_consumer_range_does_not_become_nominal() -> None:
    records = []
    for record in _catalog().records:
        facts = tuple(
            ComponentPowerFact.create(**{
                **fact.model_dump(mode="python"), "nominal_voltage": None,
                "minimum_voltage": fact.minimum_voltage or 3.0,
                "maximum_voltage": fact.maximum_voltage or 3.6,
            }) if fact.role == "consumer" else fact
            for fact in record.power_facts
        )
        records.append(_rebuild(record, power_facts=facts))
    proposal, *_ = _proposal(StaticComponentSource(tuple(records)))
    assert next(item for item in proposal.power_domains if item.name == "3V3").nominal_voltage == "3.3V"


def test_power_slot_rejects_component_without_source_capability() -> None:
    records = []
    for record in _catalog().records:
        if record.mpn == "POWER-G2":
            facts = tuple(
                ComponentPowerFact.create(**{
                    **fact.model_dump(mode="python"), "role": "consumer",
                }) if fact.role == "source" else fact
                for fact in record.power_facts
            )
            record = _rebuild(record, power_facts=facts)
        records.append(record)
    with pytest.raises(ValueError, match="required_power_source_missing"):
        _proposal(StaticComponentSource(tuple(records)))


def test_unassigned_required_pin_is_distinct_from_invalid_source() -> None:
    records = []
    for record in _catalog().records:
        if record.mpn == "ACTUATOR-G2":
            extra = ComponentPinFact.create(
                number="4", name="CONTROL", function="required_control", pad_number="4",
            )
            record = _rebuild(record, pin_facts=(*record.pin_facts, extra))
        records.append(record)
    with pytest.raises(SchematicPlanningBlocked) as error:
        _proposal(StaticComponentSource(tuple(records)))
    assert error.value.reason_code == "PIN_BINDING_UNRESOLVED"
