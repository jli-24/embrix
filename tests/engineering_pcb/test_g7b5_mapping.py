from __future__ import annotations

from datetime import UTC, datetime

from embedded_copilot.engineering_pcb import (
    ApprovedPinPadBinding,
    ComponentMappingRelationship,
    DatasheetPinEvidence,
    EDAMappingCandidate,
    EDAMappingCandidateComponent,
    EDAMappingCandidatePin,
    FootprintSymbolPinConsistencyVerifier,
    MappingEvidence,
    MappingPreventEvaluator,
    MappingSemantics,
    MappingStatus,
    MappingVerificationInput,
    PCBArtifactReference,
    PCBArtifactType,
    PCBEngineeringProjector,
    PCBFoundationEvidence,
    PCBFoundationEvidenceSource,
    PCBSourceFormat,
    mapping_state_fingerprint,
)
from embedded_copilot.pcb.models import (
    PCBComponent,
    PCBLayer,
    PCBNet,
    PCBNetNode,
    PCBPin,
    PCBPosition,
    UnifiedPCBModel,
)
from embedded_copilot.verification_loop import VerificationStatus

ZERO = "sha256:" + "0" * 64
NOW = datetime(2026, 9, 19, tzinfo=UTC)


def _replace(value: MappingVerificationInput, **updates: object) -> MappingVerificationInput:
    material = value.model_dump(mode="python", exclude={"fingerprint"})
    material.update(updates)
    return MappingVerificationInput.create(**material)


def _input(*, wrong_pad: bool = False, datasheet: bool = True, stacked: str | None = None) -> MappingVerificationInput:
    artifact = PCBArtifactReference.create(
        project_id="g7b5-project",
        artifact_id="g7b5-pcb",
        artifact_type=PCBArtifactType.BOARD,
        source_format=PCBSourceFormat.KICAD_PCB,
        source_reference="artifact://g7b5-project/g7b5-pcb",
        filename="main.kicad_pcb",
        byte_size=1,
        content_fingerprint="sha256:" + "1" * 64,
        captured_at=NOW,
    )
    evidence = PCBFoundationEvidence.create(
        evidence_id="g7b5-artifact-evidence",
        project_id=artifact.project_id,
        source_type=PCBFoundationEvidenceSource.ARTIFACT,
        source_id=artifact.artifact_id,
        source_fingerprint=artifact.content_fingerprint,
        claim="Current parsed G5 PCB artifact.",
        locator="artifact://g7b5-project/g7b5-pcb",
    )
    board = UnifiedPCBModel(
        board_name="main",
        source_format="kicad_pcb",
        layers=(PCBLayer(name="F.Cu", index=0, type="signal"),),
        components=(
            PCBComponent(
                reference="U1",
                value="MCU",
                footprint="Package_QFN:QFN-2",
                library="Package_QFN",
                position=PCBPosition(x_mm=1.0, y_mm=1.0),
                layer="F.Cu",
                pins=(
                    PCBPin(number="1", pad_type="smd", net_name="A", position=PCBPosition(x_mm=1.0, y_mm=1.0)),
                    PCBPin(number="2", pad_type="smd", net_name="GND", position=PCBPosition(x_mm=2.0, y_mm=1.0)),
                ),
            ),
        ),
        nets=(
            PCBNet(name="A", net_type="signal", nodes=(PCBNetNode(reference="U1", pin="1"),)),
            PCBNet(name="GND", net_type="ground", nodes=(PCBNetNode(reference="U1", pin="2"),)),
        ),
    )
    state = PCBEngineeringProjector().project(
        project_id=artifact.project_id,
        revision=artifact.content_fingerprint,
        source_artifact=artifact,
        parsed=board,
        evidence_refs=(evidence,),
    )
    component = state.components[0]
    component_id = component.component_id
    pads = tuple(
        type(pad).create(
            **{
                **pad.model_dump(mode="python", exclude={"fingerprint"}),
                "pin_id": f"pin-u1-{pad.number}",
            }
        )
        for pad in component.pads
    )
    if stacked is not None:
        base = next(item for item in pads if item.number == "1")
        duplicate = type(base).create(
            **{
                **base.model_dump(mode="python", exclude={"fingerprint"}),
                "pad_id": "pad-u1-1-stacked",
                "pin_id": "pin-u1-1" if stacked == "valid" else "pin-u1-2",
            }
        )
        pads = (base, duplicate, *(item for item in pads if item.number != "1"))
    components = (type(component).model_validate({**component.model_dump(mode="python", exclude={"fingerprint"}), "symbol_id": "truth-1", "pads": pads}),)
    footprints = (
        type(state.footprints[0]).model_validate({
            **state.footprints[0].model_dump(mode="python", exclude={"fingerprint"}),
            "pads": pads,
        }),
    )
    material = state.model_dump(mode="python", exclude={"fingerprint"})
    material.update(components=components, footprints=footprints)
    state = type(state).create(**material)
    ds = (
        DatasheetPinEvidence.create(
            component_id=component_id,
            datasheet_pin_id="ds-pin-1",
            datasheet_pin_number="1",
            datasheet_pin_name="A",
            datasheet_pin_function="ANODE",
            electrical_role="ANODE",
            expected_net_id=next(item.net_id for item in state.nets if item.name == "A"),
            evidence_refs=("datasheet-1",),
        ),
        DatasheetPinEvidence.create(
            component_id=component_id,
            datasheet_pin_id="ds-pin-2",
            datasheet_pin_number="2",
            datasheet_pin_name="K",
            datasheet_pin_function="CATHODE",
            electrical_role="CATHODE",
            expected_net_id=next(item.net_id for item in state.nets if item.name == "GND"),
            evidence_refs=("datasheet-1",),
        ),
    ) if datasheet else ()
    pad_numbers = ("2",) if wrong_pad else (("1", "1") if stacked else ("1",))
    bindings = (
        ApprovedPinPadBinding.create(
            component_id=component_id,
            design_truth_pin_id="pin-u1-1",
            datasheet_pin_number="1",
            pad_numbers=pad_numbers,
            electrical_role="ANODE",
            mapping_semantics=MappingSemantics.MULTI_PAD_SAME_ELECTRICAL_PIN if len(pad_numbers) > 1 else MappingSemantics.ONE_TO_ONE,
            evidence_refs=("g5-plan-1",),
        ),
        ApprovedPinPadBinding.create(
            component_id=component_id,
            design_truth_pin_id="pin-u1-2",
            datasheet_pin_number="2",
            pad_numbers=("2",),
            electrical_role="CATHODE",
            mapping_semantics=MappingSemantics.ONE_TO_ONE,
            evidence_refs=("g5-plan-1",),
        ),
    )
    authority = ComponentMappingRelationship.create(
        component_id=component_id,
        approved_component_identity="DIODE-1",
        manufacturer_part_number="DIODE-1",
        package_identity="Package_QFN:QFN-2",
        symbol_identity="Device:D",
        footprint_identity="Package_QFN:QFN-2",
        design_truth_ref="truth-1",
        datasheet_evidence_refs=("datasheet-1",),
        symbol_evidence_refs=("g4-symbol-1",),
        footprint_evidence_refs=("g5-footprint-1",),
        confidence=1.0,
    )
    mapping_evidence = (
        MappingEvidence.create(evidence_id="datasheet-1", source_kind="DATASHEET", source_fingerprint="sha256:" + "2" * 64, locator="datasheet:diode-1:pins", claim="Approved diode pin evidence."),
        MappingEvidence.create(evidence_id=evidence.evidence_id, source_kind=evidence.source_type.value, source_fingerprint=evidence.source_fingerprint, locator=evidence.locator, claim=evidence.claim),
    )
    return MappingVerificationInput.create(
        project_id=state.project_id,
        state=state,
        state_mapping_fingerprint=mapping_state_fingerprint(state),
        artifact_fingerprint=state.source_artifact_fingerprint,
        authority_fingerprint="sha256:" + "3" * 64,
        authority_components=(authority,),
        approved_pin_pad_bindings=bindings,
        datasheet_pins=ds,
        evidence=mapping_evidence,
        orientation_evidence_refs=("orientation-1",),
    )


def test_correct_mapping_reuses_unified_result_and_passes() -> None:
    evaluation = FootprintSymbolPinConsistencyVerifier().verify(_input())
    assert evaluation.result.status is VerificationStatus.PASS
    assert evaluation.result.verifier == "FOOTPRINT_SYMBOL_PIN_CONSISTENCY"
    assert evaluation.result.issues == ()
    assert evaluation.component_relationships[0].mapping_status is MappingStatus.MATCH
    assert all(item.mapping_status is MappingStatus.MATCH for item in evaluation.pin_relationships)


def test_wrong_pin_pad_fails_and_prevent_blocks_without_mutation() -> None:
    value = _input()
    wrong_bindings = tuple(
        ApprovedPinPadBinding.create(
            **{
                **item.model_dump(mode="python", exclude={"fingerprint"}),
                "pad_numbers": ("2",) if item.datasheet_pin_number == "1" else item.pad_numbers,
            }
        )
        for item in value.approved_pin_pad_bindings
    )
    evaluation = FootprintSymbolPinConsistencyVerifier().verify(
        MappingVerificationInput.create(
            **{
                **value.model_dump(mode="python", exclude={"fingerprint"}),
                "approved_pin_pad_bindings": wrong_bindings,
            }
        )
    )
    assert evaluation.result.status is VerificationStatus.FAIL
    candidate = EDAMappingCandidate.create(
        project_id=value.project_id,
        candidate_id="candidate-wrong-pad",
        base_mapping_fingerprint=value.authority_fingerprint,
        components=(EDAMappingCandidateComponent.create(component_id=evaluation.component_relationships[0].component_id, symbol_identity="Device:D", footprint_identity="Package_QFN:QFN-2", package_identity="Package_QFN:QFN-2"),),
        pins=(EDAMappingCandidatePin.create(component_id=evaluation.component_relationships[0].component_id, datasheet_pin_number="1", symbol_pin_number="1", footprint_pad_numbers=("2",), electrical_role="ANODE"),),
    )
    prevent = MappingPreventEvaluator().evaluate(value, candidate)
    assert prevent.status.value == "BLOCK"

    correct_candidate = EDAMappingCandidate.create(
        project_id=value.project_id,
        candidate_id="candidate-correct",
        base_mapping_fingerprint=value.authority_fingerprint,
        components=candidate.components,
        pins=tuple(
            EDAMappingCandidatePin.create(
                component_id=item.component_id,
                datasheet_pin_number=item.datasheet_pin_number,
                symbol_pin_number=item.datasheet_pin_number,
                footprint_pad_numbers=item.pad_numbers,
                electrical_role=item.electrical_role,
            )
            for item in value.approved_pin_pad_bindings
        ),
    )
    assert MappingPreventEvaluator().evaluate(value, correct_candidate).status.value == "ALLOW"
    assert value.state.fingerprint == _input().state.fingerprint


def test_polarity_role_mismatch_fails_without_guessing() -> None:
    value = _input()
    bindings = tuple(
        ApprovedPinPadBinding.create(
            **{
                **item.model_dump(mode="python", exclude={"fingerprint"}),
                "electrical_role": "CATHODE" if item.datasheet_pin_number == "1" else item.electrical_role,
            }
        )
        for item in value.approved_pin_pad_bindings
    )
    result = FootprintSymbolPinConsistencyVerifier().verify(
        _replace(value, approved_pin_pad_bindings=bindings)
    ).result
    assert result.status is VerificationStatus.FAIL
    assert "POLARITY_MAPPING_MISMATCH" in {item.category for item in result.issues}


def test_missing_datasheet_is_unknown_not_pass_and_package_mismatch_blocks() -> None:
    missing = FootprintSymbolPinConsistencyVerifier().verify(_input(datasheet=False))
    assert missing.result.status is VerificationStatus.UNKNOWN
    assert "DATASHEET_PIN_UNKNOWN" in {item.category for item in missing.result.issues}
    value = _input()
    candidate = EDAMappingCandidate.create(
        project_id=value.project_id,
        candidate_id="candidate-package",
        base_mapping_fingerprint=value.authority_fingerprint,
        components=(EDAMappingCandidateComponent.create(component_id=value.authority_components[0].component_id, symbol_identity="Device:D", footprint_identity="Package_QFN:QFN-4", package_identity="Package_QFN:QFN-4"),),
    )
    assert MappingPreventEvaluator().evaluate(value, candidate).status.value == "BLOCK"


def test_stacked_pad_same_identity_passes_and_conflicting_identity_fails() -> None:
    valid = FootprintSymbolPinConsistencyVerifier().verify(_input(stacked="valid"))
    assert valid.result.status is VerificationStatus.PASS
    conflict = FootprintSymbolPinConsistencyVerifier().verify(_input(stacked="conflict"))
    assert conflict.result.status is VerificationStatus.FAIL
    assert "STACKED_PAD_CONFLICT" in {item.category for item in conflict.result.issues}


def test_mapping_fingerprint_ignores_unrelated_routing_state() -> None:
    value = _input()
    material = value.state.model_dump(mode="python", exclude={"fingerprint"})
    material["routing"] = ()
    changed = type(value.state).create(**material)
    assert mapping_state_fingerprint(value.state) == mapping_state_fingerprint(changed)


def test_dnc_connected_is_a_deterministic_conflict_and_nc_is_unknown() -> None:
    value = _input()
    dnc_bindings = tuple(
        ApprovedPinPadBinding.create(
            **{
                **item.model_dump(mode="python", exclude={"fingerprint"}),
                "electrical_role": "DNC" if item.datasheet_pin_number == "1" else item.electrical_role,
            }
        )
        for item in value.approved_pin_pad_bindings
    )
    dnc = FootprintSymbolPinConsistencyVerifier().verify(
        _replace(value, approved_pin_pad_bindings=dnc_bindings)
    )
    assert dnc.result.status is VerificationStatus.FAIL
    assert "DNC_CONNECTED" in {item.category for item in dnc.result.issues}

    nc_bindings = tuple(
        ApprovedPinPadBinding.create(
            **{
                **item.model_dump(mode="python", exclude={"fingerprint"}),
                "electrical_role": "NC" if item.datasheet_pin_number == "1" else item.electrical_role,
            }
        )
        for item in value.approved_pin_pad_bindings
    )
    nc = FootprintSymbolPinConsistencyVerifier().verify(
        _replace(value, approved_pin_pad_bindings=nc_bindings)
    )
    assert nc.result.status is VerificationStatus.UNKNOWN
    assert any("NC_behavior_not_authoritative" in item for item in nc.result.unknowns)


def test_exposed_pad_without_net_rule_and_missing_pin_one_orientation_remain_unknown() -> None:
    value = _replace(_input(), orientation_evidence_refs=())
    datasheet = tuple(
        DatasheetPinEvidence.create(
            **{
                **item.model_dump(mode="python", exclude={"fingerprint"}),
                "electrical_role": "EXPOSED_PAD" if item.datasheet_pin_number == "1" else item.electrical_role,
                "expected_net_id": None if item.datasheet_pin_number == "1" else item.expected_net_id,
            }
        )
        for item in value.datasheet_pins
    )
    bindings = tuple(
        ApprovedPinPadBinding.create(
            **{
                **item.model_dump(mode="python", exclude={"fingerprint"}),
                "electrical_role": "EXPOSED_PAD" if item.datasheet_pin_number == "1" else item.electrical_role,
            }
        )
        for item in value.approved_pin_pad_bindings
    )
    result = FootprintSymbolPinConsistencyVerifier().verify(
        _replace(value, datasheet_pins=datasheet, approved_pin_pad_bindings=bindings)
    ).result
    assert result.status is VerificationStatus.UNKNOWN
    assert "PHYSICAL_ORIENTATION_UNVERIFIED" in {item.category for item in result.issues}
    assert any("exposed_pad_net_rule_missing" in item for item in result.unknowns)


def test_component_family_role_evidence_is_not_inferred() -> None:
    value = _input()
    datasheet = tuple(
        DatasheetPinEvidence.create(
            **{
                **item.model_dump(mode="python", exclude={"fingerprint"}),
                "datasheet_pin_name": "UNSPECIFIED" if item.datasheet_pin_number == "2" else item.datasheet_pin_name,
                "datasheet_pin_function": "UNSPECIFIED" if item.datasheet_pin_number == "2" else item.datasheet_pin_function,
                "electrical_role": None if item.datasheet_pin_number == "2" else item.electrical_role,
            }
        )
        for item in value.datasheet_pins
    )
    result = FootprintSymbolPinConsistencyVerifier().verify(
        _replace(value, datasheet_pins=datasheet)
    ).result
    assert result.status is VerificationStatus.UNKNOWN
    assert any("DIODE_role_evidence_incomplete" in item for item in result.unknowns)
