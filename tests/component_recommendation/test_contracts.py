from __future__ import annotations

import pytest
from pydantic import ValidationError

from embedded_copilot.component_recommendation import ComponentRecommendation
from embedded_copilot.component_recommendation.contracts import (
    ComponentPowerFact,
    ComponentSourceRecord,
    SupportingComponentRequirement,
    SupportingConnectionExpectation,
)


def test_legacy_numeric_power_source_fingerprint_and_json_unchanged() -> None:
    power = ComponentPowerFact.create(
        domain="3V3", role="consumer", nominal_voltage=3.3,
        minimum_voltage=2.0, maximum_voltage=3.6, pin_numbers=("1",),
    )
    source = ComponentSourceRecord.create(
        source_id="legacy-power", source_uri="catalog://fixture/legacy",
        source_revision="r1", manufacturer="Fixture", mpn="LEGACY-1",
        category="CONTROLLER", package="QFN", description="Legacy numeric power",
        symbol=None, footprint=None, supported_roles=("CONTROLLER",),
        power_facts=(power,),
    )
    assert source.fingerprint == "sha256:841d1ac96828b3d234c67eacfcf2c3eacead793330787835ccd215b7ca41c994"
    assert source.power_facts[0].nominal_voltage == 3.3
    assert ComponentSourceRecord.model_validate_json(source.model_dump_json()) == source


def test_unresolved_consumer_power_requires_finite_sourced_range() -> None:
    power = ComponentPowerFact.create(
        domain="VDD", role="consumer", nominal_voltage=None,
        minimum_voltage=2.0, maximum_voltage=3.6, pin_numbers=("24",),
    )
    assert power.nominal_voltage is None
    for changes in (
        {"role": "source"}, {"role": "ground"},
        {"minimum_voltage": None}, {"maximum_voltage": None},
        {"minimum_voltage": 4.0}, {"maximum_voltage": float("inf")},
    ):
        with pytest.raises(ValidationError):
            ComponentPowerFact.create(**{
                "domain": "VDD", "role": "consumer", "nominal_voltage": None,
                "minimum_voltage": 2.0, "maximum_voltage": 3.6,
                "pin_numbers": ("24",), **changes,
            })
    with pytest.raises(ValidationError):
        ComponentPowerFact.create(
            domain="VDD", role="consumer", minimum_voltage=2.0,
            maximum_voltage=3.6, pin_numbers=("24",),
        )


def test_component_recommendation_is_safe_projection() -> None:
    item = ComponentRecommendation(
        part_number="ESP32-S3",
        manufacturer="Espressif",
        reason="Verified engineering fit.",
        datasheet_reference="datasheet:esp32-s3",
        supplier_links=("https://supplier.example/esp32-s3",),
        alternatives=("STM32F4",),
    )
    assert item.model_config["strict"] is True
    with pytest.raises(ValidationError):
        ComponentRecommendation.model_validate(
            {**item.model_dump(), "supplier_links": ["https://supplier.example"]}
        )
    with pytest.raises(ValidationError):
        ComponentRecommendation.model_validate(
            {**item.model_dump(), "reason": "password=secret"}
        )


def _source(requirement: SupportingComponentRequirement) -> ComponentSourceRecord:
    return ComponentSourceRecord.create(
        source_id="source-parent", source_uri="catalog://fixture/parent",
        source_revision="r1", manufacturer="Fixture", mpn="PARENT-1",
        category="SENSOR", package="QFN", description="Fixture parent",
        symbol=None, footprint=None, supported_roles=("SENSOR",),
        supporting_requirements=(requirement,),
    )


def _expectation(**changes: object) -> SupportingConnectionExpectation:
    values = {
        "target_pin_number": "12", "target_net_name": "I2C_SDA",
        "signal_pin_number": "1", "signal_net_name": "I2C_SDA",
        "return_pin_number": "2", "return_net_name": "VDD_3V3",
    }
    return SupportingConnectionExpectation.model_validate({**values, **changes})


def test_legacy_supporting_requirement_source_fingerprint_and_json_unchanged() -> None:
    requirement = SupportingComponentRequirement.create(
        role="I2C_PULLUPS", description="Two pullups", quantity=2,
    )
    source = _source(requirement)
    assert "connection_expectations" not in requirement.model_dump(mode="json")
    assert "connection_expectations" not in source.model_dump_json()
    assert source.fingerprint == "sha256:56da5b113db4fbd562aedd2b9e306e02bf4f0a0a0b91e12a75e9f5db27c22ac3"
    assert ComponentSourceRecord.model_validate_json(source.model_dump_json()) == source


def test_nonempty_connection_expectations_change_source_fingerprint() -> None:
    legacy = _source(SupportingComponentRequirement.create(
        role="I2C_PULLUPS", description="Two pullups", quantity=2,
    ))
    enriched = _source(SupportingComponentRequirement.create(
        role="I2C_PULLUPS", description="Two pullups", quantity=2,
        connection_expectations=(
            _expectation(),
            _expectation(target_pin_number="13", target_net_name="I2C_SCL", signal_net_name="I2C_SCL"),
        ),
    ))
    assert len(enriched.supporting_requirements[0].connection_expectations) == 2
    assert enriched.fingerprint != legacy.fingerprint
    assert ComponentSourceRecord.model_validate_json(enriched.model_dump_json()) == enriched
    assert enriched.model_dump(mode="json")["supporting_requirements"][0]["connection_expectations"]


@pytest.mark.parametrize("changes", [
    {"target_pin_number": " "},
    {"target_net_name": "password=secret"},
    {"signal_pin_number": "2"},
    {"return_net_name": "I2C_SDA"},
    {"target_pin_number": 12},
])
def test_connection_expectation_rejects_invalid_endpoint(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _expectation(**changes)
