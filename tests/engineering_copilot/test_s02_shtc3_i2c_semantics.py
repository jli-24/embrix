"""Source-backed SHTC3 I2C slice in the existing offline G2 planner fixture.

The other G2 parts are synthetic; this is a domain test, not a production E2E.
"""

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
from embedded_copilot.engineering_copilot import SchematicPlanningStatus

from .test_schematic_planning_g2 import _catalog, _proposal

SHTC3_DATASHEET = (
    "https://sensirion.com/en/media/documents/643F9C8E/"
    "63A5A436/Datasheet_SHTC3.pdf"
)


def _shtc3_source(*, wrong_sda_role: bool = False) -> ComponentSourceRecord:
    # Sensirion SHTC3 datasheet v4 (2022-12), Table 3, Table 7, Figure 6.
    pins = (
        ComponentPinFact.create(number="1", name="VDD", function="supply", pad_number="1", is_power=True),
        ComponentPinFact.create(
            number="2", name="SCL", function="I2C_SCL", pad_number="2", interface_protocols=("I2C",)
        ),
        ComponentPinFact.create(
            number="3", name="SDA", function="I2C_SCL" if wrong_sda_role else "I2C_SDA",
            pad_number="3", interface_protocols=("I2C",)
        ),
        ComponentPinFact.create(number="4", name="VSS", function="ground", pad_number="4", is_power=True),
    )
    return ComponentSourceRecord.create(
        source_id="sensirion-shtc3-v4",
        source_uri=SHTC3_DATASHEET,
        source_revision="Version 4 - December 2022",
        manufacturer="Sensirion",
        mpn="SHTC3",
        category="SENSOR",
        package="DFN-4 2x2 mm",
        description="I2C humidity and temperature sensor; external pullups and 100 nF bypass required",
        symbol="Fixture:SHTC3",
        footprint="Fixture:SHTC3-DFN4",
        supported_roles=("SENSOR",),
        pin_facts=pins,
        electrical_facts=(
            ComponentElectricalFact.create(
                parameter="operating_voltage", minimum=1.62, typical=3.3, maximum=3.6, unit="V"
            ),
        ),
        interface_facts=(ComponentInterfaceFact.create(protocol="I2C", pin_names=("2", "3")),),
        power_facts=(
            ComponentPowerFact.create(
                domain="3V3", role="consumer", nominal_voltage=3.3,
                minimum_voltage=1.62, maximum_voltage=3.6, pin_numbers=("1",)
            ),
            ComponentPowerFact.create(domain="GND", role="ground", nominal_voltage=0.0, pin_numbers=("4",)),
        ),
        supporting_requirements=(
            SupportingComponentRequirement.create(
                role="I2C_PULLUPS", description="SCL and SDA pullups; size from bus capacitance",
                quantity=2, evidence_refs=("sensirion-shtc3-v4-section-4",)
            ),
            SupportingComponentRequirement.create(
                role="DECOUPLING", description="100 nF close to VDD/VSS",
                evidence_refs=("sensirion-shtc3-v4-table-7",)
            ),
        ),
    )


def _shtc3_catalog(*, wrong_sda_role: bool = False) -> StaticComponentSource:
    # Retain G2's synthetic controller/power/actuator only as an offline harness.
    records = tuple(
        _shtc3_source(wrong_sda_role=wrong_sda_role) if record.category == "SENSOR" else record
        for record in _catalog().records
    )
    return StaticComponentSource(records)


def test_s02_shtc3_i2c_source_projects_real_pin_roles_but_blocks_missing_peripherals() -> None:
    proposal, *_ = _proposal(_shtc3_catalog())
    assert proposal.status is SchematicPlanningStatus.BLOCKED
    assert {item.name for item in proposal.nets} >= {"I2C1_SCL", "I2C1_SDA", "3V3", "GND"}
    assert "BLOCKED_SUPPORTING_COMPONENT_SELECTION" in proposal.blocking_reasons
    assert {item.requirement_role for item in proposal.supporting_connections} >= {
        "I2C_PULLUPS", "DECOUPLING"
    }
    assert all(item.status.value == "REQUIREMENT_ONLY" for item in proposal.supporting_connections)


def test_s02_shtc3_i2c_wrong_sda_role_rejected_before_plan() -> None:
    with pytest.raises(ValueError, match="required interface pin"):
        _proposal(_shtc3_catalog(wrong_sda_role=True))
