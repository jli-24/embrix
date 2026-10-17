from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from embedded_copilot.engineering_pcb import (
    DecouplingPreventEvaluator,
    DecouplingRelationshipResolver,
    DecouplingVerifier,
    PCBArtifactReference,
    PCBArtifactType,
    PCBConstraint,
    PCBConstraintCompiler,
    PCBConstraintEnforcementMode,
    PCBConstraintSet,
    PCBConstraintSource,
    PCBConstraintType,
    PCBFoundationEvidence,
    PCBFoundationEvidenceSource,
    PCBMeasurementQuality,
    PCBPlacementCandidate,
    PCBSourceFormat,
)
from embedded_copilot.pcb.models import (
    PCBComponent,
    PCBLayer,
    PCBNet,
    PCBNetNode,
    PCBNetType,
    PCBPin,
    PCBPosition,
    UnifiedPCBModel,
)
from embedded_copilot.verification_loop import VerificationStatus

FP = "sha256:" + "1" * 64


def _artifact() -> PCBArtifactReference:
    return PCBArtifactReference.create(
        project_id="project-g7b1",
        artifact_id="pcb-g7b1",
        artifact_type=PCBArtifactType.BOARD,
        source_format=PCBSourceFormat.KICAD_PCB,
        source_reference="artifact://pcb-g7b1",
        filename="main.kicad_pcb",
        byte_size=128,
        content_fingerprint=FP,
        captured_at=datetime(2026, 9, 18, tzinfo=UTC),
    )


def _evidence(source: PCBFoundationEvidenceSource = PCBFoundationEvidenceSource.ARTIFACT) -> PCBFoundationEvidence:
    return PCBFoundationEvidence.create(
        evidence_id=f"evidence-g7b1-{source.value.lower()}",
        project_id="project-g7b1",
        source_type=source,
        source_id="g7b1-source",
        source_fingerprint=FP,
        claim="Approved evidence-bound decoupling context.",
        locator="pcb://g7b1",
    )


def _design_truth() -> SimpleNamespace:
    return SimpleNamespace(
        fingerprint="sha256:" + "2" * 64,
        components=(
            SimpleNamespace(component_id="U1", symbol_binding="MCU", type="MCU", category="controller", description="MCU", confidence=1.0),
            SimpleNamespace(component_id="C1", symbol_binding="CAP", type="CAPACITOR", category="passive", description="VDD decoupling", confidence=1.0),
        ),
        pins=(
            SimpleNamespace(pin_id="U1-vdd", component_id="U1", pin_number="1", pin_name="VDD_1", signal_id="sig-3v3", confidence=1.0),
            SimpleNamespace(pin_id="U1-vss", component_id="U1", pin_number="2", pin_name="VSS_1", signal_id="sig-gnd", confidence=1.0),
        ),
        signals=(
            SimpleNamespace(signal_id="sig-3v3", protocol="POWER", type="ELECTRICAL", name="3V3"),
            SimpleNamespace(signal_id="sig-gnd", protocol="POWER", type="ELECTRICAL", name="GND"),
        ),
        nets=(
            SimpleNamespace(binding_id="net-3v3", signal="sig-3v3", net_name="3V3", nodes=("U1.1", "C1.1")),
            SimpleNamespace(binding_id="net-gnd", signal="sig-gnd", net_name="GND", nodes=("U1.2", "C1.2")),
        ),
        power_domains=(
            SimpleNamespace(power_domain_id="power-3v3", name="3V3", component_refs=("U1", "C1"), ground_net="GND", confidence=1.0),
        ),
    )


def _state():
    parsed = UnifiedPCBModel(
        board_name="g7b1",
        source_format="kicad_pcb",
        layers=(PCBLayer(name="F.Cu", index=0, type="signal"), PCBLayer(name="B.Cu", index=31, type="signal")),
        components=(
            PCBComponent(
                reference="U1", value="MCU", footprint="MCU:QFN", position=PCBPosition(x_mm=10, y_mm=10), layer="F.Cu",
                pins=(PCBPin(number="1", pad_type="smd", net_name="3V3", position=PCBPosition(x_mm=10, y_mm=10)), PCBPin(number="2", pad_type="smd", net_name="GND", position=PCBPosition(x_mm=10, y_mm=11))),
            ),
            PCBComponent(
                reference="C1", value="100nF", footprint="Cap:C", position=PCBPosition(x_mm=12, y_mm=10), layer="F.Cu",
                pins=(PCBPin(number="1", pad_type="smd", net_name="3V3", position=PCBPosition(x_mm=12, y_mm=10)), PCBPin(number="2", pad_type="smd", net_name="GND", position=PCBPosition(x_mm=12, y_mm=11))),
            ),
        ),
        nets=(
            PCBNet(name="3V3", net_type=PCBNetType.POWER, nodes=(PCBNetNode(reference="U1", pin="1"), PCBNetNode(reference="C1", pin="1"))),
            PCBNet(name="GND", net_type=PCBNetType.GROUND, nodes=(PCBNetNode(reference="U1", pin="2"), PCBNetNode(reference="C1", pin="2"))),
        ),
    )
    artifact = _artifact()
    evidence = _evidence()
    g5 = SimpleNamespace(components=(SimpleNamespace(reference="U1", symbol_binding="MCU"), SimpleNamespace(reference="C1", symbol_binding="CAP")))
    from embedded_copilot.engineering_pcb import PCBEngineeringProjector

    state = PCBEngineeringProjector().project(
        project_id="project-g7b1", revision=FP, source_artifact=artifact, parsed=parsed,
        evidence_refs=(evidence,), g5_request=g5, design_truth=_design_truth(),
    )
    compiler = PCBConstraintCompiler()
    constraints = compiler.compile(state)
    state = DecouplingRelationshipResolver().resolve_state(state, constraints, design_truth=_design_truth())
    constraints = compiler.compile(state)
    plan = compiler.build_plan(state, constraints)
    return state, constraints, plan


def _with_rule(state, constraint_set, *, threshold=5.0, enforcement=PCBConstraintEnforcementMode.HARD):
    rule_evidence = _evidence(PCBFoundationEvidenceSource.DATASHEET)
    base = next(item for item in constraint_set.constraints if item.constraint_type is PCBConstraintType.DECOUPLING)
    rule = PCBConstraint.create(
        **{
            **base.model_dump(mode="python", exclude={"fingerprint"}),
            "source": PCBConstraintSource.DATASHEET,
            "evidence_refs": (rule_evidence,),
            "source_fingerprint": rule_evidence.fingerprint,
            "evaluation_method": "MAX_PAD_DISTANCE",
            "threshold": threshold,
            "threshold_unit": "mm",
            "comparison": "<=",
            "enforcement_mode": enforcement,
        }
    )
    result = PCBConstraintSet.create(
        project_id=constraint_set.project_id,
        revision=constraint_set.revision,
        state_fingerprint=state.fingerprint,
        constraints=tuple(rule if item.constraint_id == base.constraint_id else item for item in constraint_set.constraints),
        evidence_refs=tuple(dict.fromkeys((*constraint_set.evidence_refs, rule_evidence))),
    )
    return result, PCBConstraintCompiler().build_plan(state, result)


def test_typed_multi_vdd_relation_is_not_value_heuristic_only() -> None:
    state, constraints, _ = _state()
    relations = state.decoupling_relations
    assert len(relations) == 1
    assert relations[0].constraint_id == next(item for item in constraints.constraints if item.constraint_type is PCBConstraintType.DECOUPLING).constraint_id
    assert relations[0].load_power_pin_id == "U1-vdd"
    assert relations[0].power_net_id is not None
    assert relations[0].capacitor_power_pad_id is not None
    assert relations[0].capacitor_return_pad_id is not None
    assert relations[0].evidence_refs


def test_qualitative_constraint_is_unknown_and_pad_measurement_is_explicit() -> None:
    state, constraints, plan = _state()
    result = DecouplingVerifier().verify(state, constraints, plan)
    assert result.status is VerificationStatus.UNKNOWN
    assert "MISSING_MACHINE_EVALUABLE_RULE" in result.unknowns
    assert result.measurement_refs


def test_same_constraint_drives_verify_and_prevent_allow_block_and_corrected_candidate() -> None:
    state, qualitative, _ = _state()
    constraints, plan = _with_rule(state, qualitative)
    verifier = DecouplingVerifier()
    assert verifier.verify(state, constraints, plan).status is VerificationStatus.PASS

    current = tuple(state.placement)
    evaluator = DecouplingPreventEvaluator()
    valid = evaluator.evaluate(state, constraints, PCBPlacementCandidate.create(candidate_id="candidate-valid", base_state_fingerprint=state.fingerprint, placements=current), plan)
    assert valid.status.value == "ALLOW"
    moved = tuple(item.model_copy(update={"x": 20.0, "fingerprint": "sha256:" + "0" * 64}) if item.component_id == state.decoupling_relations[0].capacitor_component_id else item for item in current)
    violating = evaluator.evaluate(state, constraints, PCBPlacementCandidate.create(candidate_id="candidate-violating", base_state_fingerprint=state.fingerprint, placements=moved), plan)
    assert violating.status.value == "BLOCK"
    assert violating.constraint_id == next(item for item in constraints.constraints if item.constraint_type is PCBConstraintType.DECOUPLING).constraint_id
    corrected = evaluator.evaluate(state, constraints, PCBPlacementCandidate.create(candidate_id="candidate-corrected", base_state_fingerprint=state.fingerprint, placements=current), plan)
    assert corrected.status.value == "ALLOW"


def test_wrong_power_domain_is_fail_even_when_geometry_is_close() -> None:
    state, qualitative, _ = _state()
    values = state.model_dump(mode="python", exclude={"fingerprint"})
    for component in values["components"]:
        if component["reference"] == "C1":
            component["pads"] = tuple(
                {**pad, "net_id": "net-wrong", "fingerprint": "sha256:" + "0" * 64}
                if pad["number"] == "1"
                else pad
                for pad in component["pads"]
            )
            component["fingerprint"] = "sha256:" + "0" * 64
    wrong = type(state).create(**values)
    constraints, plan = _with_rule(wrong, qualitative)
    result = DecouplingVerifier().verify(wrong, constraints, plan)
    assert result.status is VerificationStatus.FAIL
    assert any(issue.category == "DECOUPLING:SAME_POWER_DOMAIN" for issue in result.issues)


def test_missing_relation_is_unknown_and_never_pass() -> None:
    state, qualitative, _ = _state()
    values = state.model_dump(mode="python", exclude={"fingerprint"})
    values["decoupling_relations"] = ()
    missing = type(state).create(**values)
    constraints, plan = _with_rule(missing, qualitative)
    result = DecouplingVerifier().verify(missing, constraints, plan)
    assert result.status is VerificationStatus.UNKNOWN
    assert "MISSING_DECOUPLING_RELATION" in result.unknowns


def test_missing_relation_evidence_is_unknown_and_never_pass() -> None:
    state, _, _ = _state()
    values = state.model_dump(mode="python", exclude={"fingerprint"})
    values["evidence_refs"] = ()
    values["decoupling_relations"] = tuple(
        {
            **relation,
            "evidence_refs": (),
            "fingerprint": "sha256:" + "0" * 64,
        }
        for relation in values["decoupling_relations"]
    )
    missing = type(state).create(**values)
    constraints = PCBConstraintCompiler().compile(missing)
    plan = PCBConstraintCompiler().build_plan(missing, constraints)
    result = DecouplingVerifier().verify(missing, constraints, plan)
    assert result.status is VerificationStatus.UNKNOWN
    assert "MISSING_DECOUPLING_EVIDENCE" in result.unknowns


def test_advisory_rule_warns_and_center_fallback_is_approximate() -> None:
    state, qualitative, _ = _state()
    values = state.model_dump(mode="python", exclude={"fingerprint"})
    for component in values["components"]:
        component["pads"] = tuple(
            {**pad, "x_mm": None, "y_mm": None, "geometry_source": "UNAVAILABLE", "fingerprint": "sha256:" + "0" * 64}
            for pad in component["pads"]
        )
        component["fingerprint"] = "sha256:" + "0" * 64
    approximate = type(state).create(**values)
    constraints, plan = _with_rule(approximate, qualitative, threshold=1.0, enforcement=PCBConstraintEnforcementMode.ADVISORY)
    detailed = DecouplingVerifier().verify_detailed(approximate, constraints, plan)
    assert detailed.result.status is VerificationStatus.WARNING
    assert detailed.measurements[0].quality is PCBMeasurementQuality.APPROXIMATE
    assert detailed.measurements[0].geometry_source == "COMPONENT_CENTER_FALLBACK"
