from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_runtime import (
    CalculationRecord,
    CircuitBlock,
    CircuitDecision,
    CircuitIntentObject,
    CircuitSchematicBinding,
    Provenance,
    SchematicDesignObject,
    VerificationRequirement,
)


def provenance(ref: str = "project-001:circuit-intent") -> Provenance:
    return Provenance.create(
        source="TEST",
        source_ref=ref,
        created_by="test-user",
        created_at=datetime(2026, 9, 21, tzinfo=UTC),
    )


def block() -> CircuitBlock:
    return CircuitBlock(
        block_id="block-power",
        function="3V3 power regulation",
        requirement_refs=("req-power",),
        architecture_refs=("hw-stm32",),
        topology_ref="decision-power",
        decision_refs=("decision-power",),
        calculation_refs=("calc-power",),
        verification_refs=("verification-power",),
        assumption_refs=("assumption-load",),
        risk_refs=("risk-thermal",),
        schematic_binding_refs=("mapping-power",),
    )


def decision() -> CircuitDecision:
    return CircuitDecision(
        decision_id="decision-power",
        block_ref="block-power",
        decision_type="TOPOLOGY",
        selected_option="DESIGN_CONSTRAINT:LDO",
        alternatives=("DESIGN_CONSTRAINT:BUCK",),
        rationale="The approved load constraint permits the simpler topology.",
        tradeoffs=("Lower complexity; less headroom for thermal margin.",),
        calculation_refs=("calc-power",),
        evidence_refs=("evidence-datasheet",),
        risk_refs=("risk-thermal",),
        status="DRAFT",
    )


def calculation() -> CalculationRecord:
    return CalculationRecord(
        calculation_id="calc-power",
        owner_ref="decision-power",
        equation="P_loss=(Vin-Vout)*Iload",
        inputs={"Vin": 5.0, "Vout": 3.3, "Iload": "I_MAX"},
        outputs={"P_loss": 0.17},
        units=("V", "A", "W"),
        corner="VIN_MIN__I_MAX__TA_MAX",
        margin=0.2,
        acceptance_limit=0.0,
        tool_reference="calculator:power-v1",
        model_reference="model:approved-load-constraint",
        evidence_refs=("evidence-calculation",),
        status="EXECUTED",
    )


def verification() -> VerificationRequirement:
    return VerificationRequirement(
        verification_id="verification-power",
        owner_ref="block-power",
        claim="Power block has positive dropout and thermal margin.",
        verification_type="CALCULATION",
        method="deterministic power calculator",
        acceptance_limit=0.0,
        required_evidence_refs=("evidence-calculation",),
        produced_evidence_refs=(),
        status="PLANNED",
        currentness="CURRENT",
    )


def test_circuit_block_is_strict_frozen_and_fingerprinted() -> None:
    value = block()

    assert value.fingerprint.startswith("sha256:")
    with pytest.raises((TypeError, ValidationError)):
        value.function = "mutated"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        CircuitBlock.model_validate({**value.model_dump(), "unknown": "rejected"})


def test_circuit_decision_fingerprint_is_deterministic_and_content_bound() -> None:
    first = decision()
    second = CircuitDecision.model_validate(first.model_dump(mode="python"))

    assert first.fingerprint == second.fingerprint
    with pytest.raises(ValidationError, match="fingerprint"):
        CircuitDecision.model_validate(
            {
                **first.model_dump(mode="python"),
                "rationale": "different rationale",
            }
        )


def test_calculation_record_validates_structured_inputs_and_outputs() -> None:
    value = calculation()

    assert value.inputs == (("Iload", "I_MAX"), ("Vin", 5.0), ("Vout", 3.3))
    assert value.outputs == (("P_loss", 0.17),)
    with pytest.raises(ValidationError):
        CalculationRecord.model_validate(
            {
                **value.model_dump(mode="python"),
                "inputs": {"Vin": {"unit": "V"}},
            }
        )


def _calculation_input_sources() -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "input_name": name, "value": value, "unit": unit,
            "source_kind": "CATALOG_REQUIREMENT", "source_id": "req-power",
            "source_fingerprint": "sha256:" + "a" * 64,
            "content_fingerprint": "sha256:" + "b" * 64,
            "locator": f"requirement:{name}", "condition": "VIN_MIN__I_MAX__TA_MAX",
        }
        for (name, value), unit in zip(
            calculation().inputs, ("A", "V", "V"), strict=True,
        )
    )


def test_calculation_legacy_payload_and_fingerprint_remain_exact() -> None:
    legacy = calculation()
    assert legacy.fingerprint == "sha256:027039dc4257cff4d0cedcb93e8dd047e2674808231239cd367e2a0a16d05d3d"
    assert "input_sources" not in legacy.model_dump(mode="json")
    assert CalculationRecord.model_validate_json(legacy.model_dump_json()) == legacy


def test_calculation_input_sources_bind_every_named_value_unit_and_condition() -> None:
    legacy = calculation()
    payload = legacy.model_dump(mode="python")
    payload.update(input_sources=_calculation_input_sources(), fingerprint="sha256:" + "0" * 64)
    qualified = CalculationRecord.model_validate(payload)
    assert tuple(source.input_name for source in qualified.input_sources) == (
        "Iload", "Vin", "Vout",
    )
    assert qualified.fingerprint != legacy.fingerprint


@pytest.mark.parametrize("change", [
    {"input_name": "unlisted"}, {"value": 99.0}, {"unit": ""},
    {"source_kind": "CLIENT_TEXT"}, {"source_id": ""},
    {"source_fingerprint": "sha256:bad"}, {"content_fingerprint": "sha256:bad"},
    {"locator": ""},
    {"condition": "NOMINAL"},
])
def test_calculation_input_source_rejects_unbound_or_incomplete_value(change) -> None:
    legacy = calculation()
    sources = list(_calculation_input_sources())
    sources[0] = {**sources[0], **change}
    payload = legacy.model_dump(mode="python")
    payload.update(input_sources=sources, fingerprint="sha256:" + "0" * 64)
    with pytest.raises(ValidationError):
        CalculationRecord.model_validate(payload)


def test_calculation_input_source_requires_separate_content_fingerprint() -> None:
    legacy = calculation()
    sources = list(_calculation_input_sources())
    sources[0] = {key: value for key, value in sources[0].items() if key != "content_fingerprint"}
    payload = legacy.model_dump(mode="python")
    payload.update(input_sources=sources, fingerprint="sha256:" + "0" * 64)
    with pytest.raises(ValidationError):
        CalculationRecord.model_validate(payload)


@pytest.mark.parametrize("changed_field", ["source_fingerprint", "content_fingerprint"])
def test_calculation_source_revision_changes_candidate_fingerprint(changed_field) -> None:
    legacy = calculation()
    payload = legacy.model_dump(mode="python")
    payload.update(
        input_sources=_calculation_input_sources(), status="PLANNED",
        fingerprint="sha256:" + "0" * 64,
    )
    original = CalculationRecord.model_validate(payload)
    revised_sources = list(_calculation_input_sources())
    revised_sources[0] = {**revised_sources[0], changed_field: "sha256:" + "c" * 64}
    revised = CalculationRecord.model_validate({
        **payload, "input_sources": revised_sources,
    })
    assert original.inputs == revised.inputs
    assert original.status == revised.status == "PLANNED"
    assert original.fingerprint != revised.fingerprint


def test_verification_requirement_can_move_through_lifecycle_without_new_revision() -> None:
    planned = verification()
    ready = VerificationRequirement.model_validate(
        {
            **planned.model_dump(mode="python"),
            "fingerprint": "sha256:" + "0" * 64,
            "status": "READY",
        }
    )

    assert planned.verification_id == ready.verification_id
    assert planned.fingerprint != ready.fingerprint
    assert ready.currentness == "CURRENT"


def test_circuit_intent_fingerprint_includes_structured_child_fingerprints() -> None:
    first = CircuitIntentObject.create(
        object_id="circuit-001",
        project_id="project-001",
        function="STM32 power, CAN, and SWD intent",
        topology=("protected 5V to 3V3", "MCU CAN to transceiver"),
        blocks=(block(),),
        decisions=(decision(),),
        calculations=(calculation(),),
        verification_requirements=(verification(),),
        provenance=provenance(),
    )
    changed_block = CircuitBlock.model_validate(
        {
            **block().model_dump(mode="python"),
            "fingerprint": "sha256:" + "0" * 64,
            "function": "3V3 low-noise power regulation",
        }
    )
    second = CircuitIntentObject.create(
        **{
            **first.model_dump(mode="python"),
            "fingerprint": "sha256:" + "0" * 64,
            "blocks": (changed_block,),
        }
    )

    assert first.blocks[0].fingerprint != second.blocks[0].fingerprint
    assert first.fingerprint != second.fingerprint


def test_legacy_parent_circuit_fingerprint_and_json_remain_exact() -> None:
    value = CircuitIntentObject.create(
        object_id="circuit-001", project_id="project-001",
        function="STM32 power, CAN, and SWD intent",
        topology=("protected 5V to 3V3", "MCU CAN to transceiver"),
        blocks=(block(),), decisions=(decision(),), calculations=(calculation(),),
        verification_requirements=(verification(),), provenance=provenance(),
    )
    assert value.fingerprint == "sha256:77c5f2705623edd71efb8ef98d08639d29fc2c9b214ea5c22638223edc3e4887"
    payload = value.model_dump_json()
    assert '"input_sources"' not in payload
    assert CircuitIntentObject.model_validate_json(payload, strict=False) == value


def test_calculation_input_source_is_exported_from_engineering_runtime() -> None:
    from embedded_copilot.engineering_runtime import CalculationInputSource

    assert CalculationInputSource.__name__ == "CalculationInputSource"


def test_legacy_string_fields_remain_compatible() -> None:
    value = CircuitIntentObject.create(
        object_id="circuit-legacy",
        project_id="project-001",
        function="legacy circuit intent",
        topology=("5V to 3V3",),
        parameters=("3V3",),
        calculation=("check thermal margin",),
        assumptions=("load is bounded",),
        risks=("thermal margin",),
        verification_requirements=("review",),
        provenance=provenance("project-001:legacy"),
    )

    assert value.topology == ("5V to 3V3",)
    assert value.blocks == ()


def test_stm32_control_board_golden_covers_power_can_and_swd() -> None:
    block_values = (
        ("block-power", "3V3 power regulation", "decision-power"),
        ("block-can", "CAN physical interface", "decision-can"),
        ("block-swd", "SWD debug interface", "decision-swd"),
    )
    blocks = tuple(
        CircuitBlock(
            block_id=block_id,
            function=function,
            requirement_refs=(f"req-{block_id.removeprefix('block-')}",),
            architecture_refs=("hw-stm32",),
            topology_ref=decision_id,
            decision_refs=(decision_id,),
            calculation_refs=(f"calc-{block_id.removeprefix('block-')}",),
            verification_refs=(f"verification-{block_id.removeprefix('block-')}",),
            schematic_binding_refs=(f"mapping-{block_id.removeprefix('block-')}",),
        )
        for block_id, function, decision_id in block_values
    )
    decisions = tuple(
        CircuitDecision(
            decision_id=decision_id,
            block_ref=block_id,
            decision_type="INTERFACE" if "can" in block_id or "swd" in block_id else "TOPOLOGY",
            selected_option=selected,
            alternatives=(alternative,),
            rationale="The selected logical role satisfies the approved STM32 board constraint.",
            tradeoffs=("Requires the referenced verification evidence.",),
            calculation_refs=(f"calc-{block_id.removeprefix('block-')}",),
            evidence_refs=(f"evidence-{block_id.removeprefix('block-')}",),
            status="DRAFT",
        )
        for (block_id, _, decision_id), selected, alternative in (
            (block_values[0], "DESIGN_CONSTRAINT:LDO", "DESIGN_CONSTRAINT:BUCK"),
            (block_values[1], "DESIGN_CONSTRAINT:CAN_TRANSCEIVER", "DIRECT_MCU_PINS"),
            (block_values[2], "DESIGN_CONSTRAINT:SWD_HEADER", "LEVEL_TRANSLATOR"),
        )
    )
    calculations = tuple(
        CalculationRecord(
            calculation_id=f"calc-{block_id.removeprefix('block-')}",
            owner_ref=decision_id,
            equation="margin = limit - result",
            inputs={"result": 1.0},
            outputs={"margin": 1.0},
            units=("ratio",),
            corner="NOMINAL",
            margin=1.0,
            acceptance_limit=0.0,
            tool_reference="calculator:stm32-golden-v1",
            model_reference="model:approved-design-constraint",
            evidence_refs=(f"evidence-{block_id.removeprefix('block-')}",),
            status="EXECUTED",
        )
        for block_id, _, decision_id in block_values
    )
    verifications = tuple(
        VerificationRequirement(
            verification_id=f"verification-{block_id.removeprefix('block-')}",
            owner_ref=block_id,
            claim=f"{function} satisfies the approved STM32 board constraint.",
            verification_type="CALCULATION",
            method="deterministic calculator and schematic mapping review",
            acceptance_limit=0.0,
            required_evidence_refs=(f"evidence-{block_id.removeprefix('block-')}",),
            status="PLANNED",
            currentness="CURRENT",
        )
        for block_id, function, _ in block_values
    )

    intent = CircuitIntentObject.create(
        object_id="circuit-stm32-golden",
        project_id="project-001",
        function="STM32 control board power, CAN, and SWD intent",
        topology=("protected 5V to 3V3", "MCU CAN to transceiver", "SWD header"),
        blocks=blocks,
        decisions=decisions,
        calculations=calculations,
        verification_requirements=verifications,
        provenance=provenance("project-001:stm32-golden"),
    )

    assert {item.block_id for item in intent.blocks} == {
        "block-power",
        "block-can",
        "block-swd",
    }
    assert {item.decision_id for item in intent.decisions} == {
        "decision-power",
        "decision-can",
        "decision-swd",
    }
    assert len(intent.calculations) == len(intent.verification_requirements) == 3
    assert intent.fingerprint.startswith("sha256:")


def _binding(**overrides: object) -> CircuitSchematicBinding:
    values: dict[str, object] = {
        "binding_id": "mapping-power",
        "circuit_intent_id": "circuit-stm32-golden",
        "circuit_intent_revision": "r1",
        "circuit_intent_fingerprint": "sha256:" + "1" * 64,
        "circuit_block_id": "block-power",
        "component_instance_ids": ("U1",),
        "pin_binding_ids": ("U1:3V3",),
        "net_ids": ("3V3",),
        "power_domain_ids": ("3V3",),
    }
    values.update(overrides)
    return CircuitSchematicBinding.create(**values)


def _schematic_with_binding(binding: CircuitSchematicBinding) -> SchematicDesignObject:
    return SchematicDesignObject.create(
        object_id="schematic-001",
        project_id="project-001",
        symbols=("U1:STM32F103", "U2:REGULATOR"),
        pins=("U1:3V3", "U2:IN"),
        nets=("3V3", "GND"),
        circuit_bindings=(binding,),
        provenance=provenance("project-001:schematic"),
    )


def test_schematic_binding_is_stable_and_design_owned() -> None:
    first = _binding(
        component_instance_ids=("U1", "U2"),
        pin_binding_ids=("U1:3V3", "U2:IN"),
        net_ids=("3V3", "GND"),
        power_domain_ids=("3V3", "GND"),
    )
    second = _binding(
        component_instance_ids=("U2", "U1"),
        pin_binding_ids=("U2:IN", "U1:3V3"),
        net_ids=("GND", "3V3"),
        power_domain_ids=("GND", "3V3"),
    )
    assert first.fingerprint == second.fingerprint
    assert first.component_instance_ids == ("U1", "U2")
    with pytest.raises((TypeError, ValidationError)):
        first.net_ids = ("GND",)  # type: ignore[misc]
    with pytest.raises(ValidationError):
        CircuitSchematicBinding.model_validate(
            {**first.model_dump(mode="python"), "unknown": "rejected"}
        )
    with pytest.raises(ValidationError):
        _binding(net_ids=("3V3", "3V3"))


def test_binding_changes_design_fingerprint_but_not_circuit_intent() -> None:
    intent = CircuitIntentObject.create(
        object_id="circuit-001",
        project_id="project-001",
        function="power intent",
        topology=("5V to 3V3",),
        provenance=provenance(),
    )
    changed_binding = _binding(
        circuit_intent_fingerprint=intent.fingerprint,
        net_ids=("GND",),
    )
    first = _schematic_with_binding(_binding(circuit_intent_fingerprint=intent.fingerprint))
    second = _schematic_with_binding(changed_binding)
    unchanged_intent = CircuitIntentObject.create(
        **{
            **intent.model_dump(mode="python"),
            "fingerprint": "sha256:" + "0" * 64,
        }
    )
    assert intent.fingerprint == unchanged_intent.fingerprint
    assert first.fingerprint != second.fingerprint


def test_binding_rejects_cross_project_and_invalid_elements_but_allows_shared_power() -> None:
    with pytest.raises(ValidationError, match="another project"):
        _schematic_with_binding(_binding(circuit_intent_id="project-002:circuit-001"))
    with pytest.raises(ValidationError, match="pin reference"):
        _schematic_with_binding(_binding(pin_binding_ids=("U1:UNKNOWN",)))
    with pytest.raises(ValidationError, match="net reference"):
        _schematic_with_binding(_binding(net_ids=("UNKNOWN",)))
    shared = _binding(net_ids=("3V3",), power_domain_ids=("3V3",))
    assert _schematic_with_binding(shared).circuit_bindings == (shared,)
