from __future__ import annotations

from types import SimpleNamespace

import pytest

from embedded_copilot.component_recommendation import (
    ComponentPinFact,
    ComponentSourceRecord,
)
from embedded_copilot.engineering_copilot.schematic_planning import (
    SchematicPlanningBlocked,
    _pin_candidates,
)
from embedded_copilot.engineering_runtime import (
    CalculationRecord,
    CircuitIntentObject,
    VerificationRequirement,
)
from tests.production_acceptance.test_phase4_4_stm32_full_chain_golden import (
    _approved_stm32_plan,
    _project_intent,
    _stm32_catalog,
)


def test_s02_real_project_vin_remains_blocked() -> None:
    with pytest.raises(SchematicPlanningBlocked) as error:
        _approved_stm32_plan(_project_intent("project-1"))
    assert error.value.reason_code == "POWER_SOURCE_UNRESOLVED"


def test_s02_stm32_and_transceiver_sources_do_not_claim_uart_can_link() -> None:
    records = {record.mpn: record for record in _stm32_catalog()}
    mcu = records["STM32F103C8T6"]
    transceiver = records["SN65HVD230D"]
    mcu_pins = {pin.number: pin for pin in mcu.pin_facts}
    transceiver_pins = {pin.number: pin for pin in transceiver.pin_facts}

    assert mcu.source_uri == "https://www.st.com/resource/en/datasheet/stm32f103c8.pdf"
    assert mcu.source_revision == "DS5319 Rev 20"
    assert mcu.package == "LQFP48"
    assert transceiver.source_uri == "https://www.ti.com/lit/ds/symlink/sn65hvd230.pdf"
    assert transceiver.source_revision == "SLOS346 Rev O"
    assert transceiver.package == "SOIC8"
    assert mcu_pins["8"].name == "VSSA"
    assert mcu_pins["9"].name == "VDDA"
    assert not any(pin.name == "VREF" for pin in mcu.pin_facts)
    assert mcu_pins["32"].function == "CAN_RX"
    assert mcu_pins["33"].function == "CAN_TX"
    assert transceiver_pins["1"].function == "CAN_TX"
    assert transceiver_pins["4"].function == "CAN_RX"
    assert all(
        "CAN_CONTROLLER" in pin.interface_protocols
        for pin in (mcu_pins["32"], mcu_pins["33"], transceiver_pins["1"], transceiver_pins["4"])
    )
    assert not any(
        interface.protocol == "UART"
        for record in (mcu, transceiver)
        for interface in record.interface_facts
    )


def test_s02_stm32_catalog_has_nine_power_pins_with_ranges() -> None:
    mcu = next(record for record in _stm32_catalog() if record.mpn == "STM32F103C8T6")
    pins = {pin.number: pin for pin in mcu.pin_facts}
    fact_by_pin = {
        number: fact
        for fact in mcu.power_facts
        for number in fact.pin_numbers
    }
    expected = {"1", "8", "9", "23", "24", "35", "36", "47", "48"}

    assert set(fact_by_pin) == expected
    assert all(pins[number].is_power and pins[number].pad_number == number for number in expected)
    assert fact_by_pin["1"].minimum_voltage == 1.8
    assert fact_by_pin["1"].maximum_voltage == 3.6
    assert all(
        fact.minimum_voltage is not None and fact.maximum_voltage is not None
        for fact in fact_by_pin.values() if fact.role == "consumer"
    )


def test_s02_sources_separate_controller_and_physical_can_pins() -> None:
    records = {record.mpn: record for record in _stm32_catalog()}
    mcu = records["STM32F103C8T6"]
    transceiver = records["SN65HVD230D"]
    connector = records["CAN-TERMINAL-2P"]

    def candidates(record: ComponentSourceRecord, protocol: str, alias: str) -> tuple[str, ...]:
        projection = SimpleNamespace(
            pin_facts=record.pin_facts, interface_facts=record.interface_facts,
        )
        return tuple(pin.number for pin in _pin_candidates(projection, protocol, alias))

    assert candidates(mcu, "CAN_CONTROLLER", "TX") == ("33",)
    assert candidates(mcu, "CAN_CONTROLLER", "RX") == ("32",)
    assert candidates(transceiver, "CAN_CONTROLLER", "TX") == ("1",)
    assert candidates(transceiver, "CAN_CONTROLLER", "RX") == ("4",)
    assert candidates(transceiver, "CAN", "CANH") == ("7",)
    assert candidates(connector, "CAN", "CANH") == ("1",)
    assert candidates(mcu, "SWD", "SWDIO") == ("34",)
    assert candidates(mcu, "CAN_CONTROLLER", "SWDIO") == ()


def test_s02_can_controller_rejects_uart_function_even_with_can_protocol_tag() -> None:
    records = _stm32_catalog()
    mcu = next(record for record in records if record.mpn == "STM32F103C8T6")
    changed_pins = tuple(
        ComponentPinFact.create(**{
            **pin.model_dump(mode="python"),
            "function": "UART_TX",
        }) if pin.number == "33" else pin
        for pin in mcu.pin_facts
    )
    changed_mcu = ComponentSourceRecord.create(**{
        **mcu.model_dump(mode="python", exclude={"fingerprint"}),
        "pin_facts": changed_pins,
    })
    changed_catalog = tuple(
        changed_mcu if record.mpn == mcu.mpn else record
        for record in records
    )

    changed = next(record for record in changed_catalog if record.mpn == "STM32F103C8T6")
    projection = SimpleNamespace(pin_facts=changed.pin_facts, interface_facts=changed.interface_facts)
    assert _pin_candidates(projection, "CAN_CONTROLLER", "TX") == ()


def test_s02_stm32_source_cannot_omit_vdda_supply_pin() -> None:
    records = _stm32_catalog()
    mcu = next(record for record in records if record.mpn == "STM32F103C8T6")
    changed_mcu = ComponentSourceRecord.create(**{
        **mcu.model_dump(mode="python", exclude={"fingerprint"}),
        "pin_facts": tuple(pin for pin in mcu.pin_facts if pin.number != "9"),
        "power_facts": tuple(
            fact for fact in mcu.power_facts if "9" not in fact.pin_numbers
        ),
    })
    changed_catalog = tuple(
        changed_mcu if record.mpn == mcu.mpn else record
        for record in records
    )

    with pytest.raises(ValueError, match="required_power_pin_missing:9"):
        _approved_stm32_plan(
            _project_intent("project-1"), catalog=changed_catalog,
        )


@pytest.mark.parametrize("missing_field", ["inputs", "units", "evidence_refs"])
def test_s02_planner_rejects_executed_calculation_without_support(
    missing_field: str,
) -> None:
    seed = _project_intent("project-1")
    values: dict[str, object] = {
        "calculation_id": "calculation-power",
        "owner_ref": "block-power",
        "equation": "P_loss=(Vin-Vout)*Iload",
        "inputs": (("Vin", 5.0), ("Vout", 3.3), ("Iload", 0.1)),
        "outputs": (("P_loss", 0.17),),
        "units": ("V", "A", "W"),
        "tool_reference": "calculator:power-v1",
        "evidence_refs": ("evidence-calculation",),
        "status": "EXECUTED",
    }
    values[missing_field] = ()
    intent = CircuitIntentObject.create(**{
        **seed.model_dump(mode="python", exclude={"fingerprint"}),
        "calculations": (CalculationRecord(**values),),
    })

    with pytest.raises(ValueError, match="executed calculation is incomplete"):
        _approved_stm32_plan(intent)


def test_s02_planner_rejects_verified_claim_without_produced_evidence() -> None:
    seed = _project_intent("project-1")
    intent = CircuitIntentObject.create(**{
        **seed.model_dump(mode="python", exclude={"fingerprint"}),
        "verification_requirements": (
            VerificationRequirement(
                verification_id="verification-can",
                owner_ref="block-can",
                claim="CAN controller and bus nets have the approved pin mapping",
                verification_type="READ_BACK",
                method="native schematic read-back",
                required_evidence_refs=("evidence-read-back",),
                status="VERIFIED",
            ),
        ),
    })

    with pytest.raises(ValueError, match="verified claim has no produced evidence"):
        _approved_stm32_plan(intent)


def test_s02_planner_validates_supported_calculation_before_unresolved_power() -> None:
    seed = _project_intent("project-1")
    intent = CircuitIntentObject.create(**{
        **seed.model_dump(mode="python", exclude={"fingerprint"}),
        "calculations": (
            CalculationRecord(
                calculation_id="calculation-power",
                owner_ref="block-power",
                equation="P_loss=(Vin-Vout)*Iload",
                inputs=(("Vin", 5.0), ("Vout", 3.3), ("Iload", 0.1)),
                outputs=(("P_loss", 0.17),),
                units=("V", "A", "W"),
                evidence_refs=("evidence-calculation",),
                status="EXECUTED",
            ),
        ),
        "verification_requirements": (
            VerificationRequirement(
                verification_id="verification-power",
                owner_ref="block-power",
                claim="Power calculation has recorded inputs and result",
                verification_type="CALCULATION",
                method="deterministic calculation",
                required_evidence_refs=("evidence-calculation",),
                produced_evidence_refs=("evidence-calculation",),
                status="VERIFIED",
            ),
        ),
    })

    with pytest.raises(SchematicPlanningBlocked) as error:
        _approved_stm32_plan(intent)
    assert error.value.reason_code == "POWER_SOURCE_UNRESOLVED"


