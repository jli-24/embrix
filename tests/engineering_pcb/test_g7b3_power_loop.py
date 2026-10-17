from __future__ import annotations

from embedded_copilot.engineering_pcb import (
    PCBApplicability,
    PCBComponentProjection,
    PCBConnectivityStatus,
    PCBConstraintCompiler,
    PCBConstraintEnforcementMode,
    PCBFoundationEvidence,
    PCBFoundationEvidenceSource,
    PCBNetProjection,
    PCBPowerLoopMetric,
    PCBPowerPinEvidence,
    PCBPowerPinRole,
    PCBPowerStageEvidence,
    PCBPowerTopology,
    PCBRouteSegmentProjection,
    PowerLayoutCandidate,
    PowerLoopPreventEvaluator,
    PowerLoopVerifier,
)
from embedded_copilot.verification_loop import VerificationStatus
from tests.engineering_pcb import test_g7b2_return_path as base_fixture

ZERO = "sha256:" + "0" * 64


def _evidence() -> PCBFoundationEvidence:
    return PCBFoundationEvidence.create(
        evidence_id="g7b3-power-evidence",
        project_id="g7b2-project",
        source_type=PCBFoundationEvidenceSource.DESIGN_DECISION,
        source_id="buck-layout-rule",
        source_fingerprint="sha256:" + "3" * 64,
        claim="Approved BUCK input hot-loop geometry and machine-evaluable perimeter rule.",
        locator="datasheet://g7b3/buck-layout",
    )


def _pad(component_id: str, pad_id: str, pin_id: str, net_id: str, x: float, y: float, evidence):
    from embedded_copilot.engineering_pcb import PCBPadProjection

    return PCBPadProjection.create(
        pad_id=pad_id,
        pin_id=pin_id,
        number=pin_id,
        net_id=net_id,
        evidence_refs=(evidence,),
        x_mm=x,
        y_mm=y,
        geometry_source="PAD",
    )


def _fixture(*, wrong_cin: bool = False, with_rule: bool = True):
    base_state, _, _, _ = base_fixture._base()
    evidence = _evidence()
    net_ids = {
        name: f"net-{name.lower()}"
        for name in ("VIN", "GND", "VOUT", "SW", "FB")
    }
    nets = tuple(
        PCBNetProjection.create(
            net_id=net_id,
            name=name,
            member_pad_ids=(),
            signal_id=None,
            design_truth_status=PCBConnectivityStatus.MATCH,
            evidence_refs=(evidence,),
        )
        for name, net_id in net_ids.items()
    )
    components = (
        PCBComponentProjection.create(
            component_id="component-U1",
            reference="U1",
            value="BUCK_CONTROLLER",
            symbol_id="U1-symbol",
            footprint_id="U1-footprint",
            pads=(
                _pad("U1", "u1-vin", "VIN", net_ids["VIN"], 1.0, 1.0, evidence),
                _pad("U1", "u1-return", "RETURN", net_ids["GND"], 1.0, 0.0, evidence),
                _pad("U1", "u1-sw", "SW", net_ids["SW"], 3.0, 2.0, evidence),
                _pad("U1", "u1-fb", "FB", net_ids["FB"], 4.0, 3.0, evidence),
            ),
            evidence_refs=(evidence,),
        ),
        PCBComponentProjection.create(
            component_id="component-C1",
            reference="C1",
            value="10uF",
            symbol_id="C1-symbol",
            footprint_id="C1-footprint",
            pads=(
                _pad("C1", "c1-power", "1", net_ids["VOUT"] if wrong_cin else net_ids["VIN"], 2.0, 1.0, evidence),
                _pad("C1", "c1-return", "2", net_ids["GND"], 2.0, 0.0, evidence),
            ),
            evidence_refs=(evidence,),
        ),
        PCBComponentProjection.create(
            component_id="component-C2",
            reference="C2",
            value="22uF",
            symbol_id="C2-symbol",
            footprint_id="C2-footprint",
            pads=(
                _pad("C2", "c2-power", "1", net_ids["VOUT"], 5.0, 2.0, evidence),
                _pad("C2", "c2-return", "2", net_ids["GND"], 5.0, 1.0, evidence),
            ),
            evidence_refs=(evidence,),
        ),
        PCBComponentProjection.create(
            component_id="component-L1",
            reference="L1",
            value="4.7uH",
            symbol_id="L1-symbol",
            footprint_id="L1-footprint",
            pads=(
                _pad("L1", "l1-input", "1", net_ids["SW"], 3.0, 2.0, evidence),
                _pad("L1", "l1-output", "2", net_ids["VOUT"], 4.0, 2.0, evidence),
            ),
            evidence_refs=(evidence,),
        ),
        PCBComponentProjection.create(
            component_id="component-Q1",
            reference="Q1",
            value="SWITCH",
            symbol_id="Q1-symbol",
            footprint_id="Q1-footprint",
            pads=(),
            evidence_refs=(evidence,),
        ),
        PCBComponentProjection.create(
            component_id="component-R1",
            reference="R1",
            value="FB_DIVIDER",
            symbol_id="R1-symbol",
            footprint_id="R1-footprint",
            pads=(_pad("R1", "r1-fb", "1", net_ids["FB"], 4.0, 3.0, evidence),),
            evidence_refs=(evidence,),
        ),
    )
    route_data = (
        ("hot-1", net_ids["VIN"], (1.0, 1.0), (3.0, 1.0)),
        ("hot-2", net_ids["VIN"], (3.0, 1.0), (3.0, 0.0)),
        ("hot-3", net_ids["GND"], (3.0, 0.0), (1.0, 0.0)),
        ("hot-4", net_ids["GND"], (1.0, 0.0), (1.0, 1.0)),
        ("sw-1", net_ids["SW"], (3.0, 2.0), (4.0, 2.0)),
    )
    routes = tuple(
        PCBRouteSegmentProjection.create(
            segment_id=segment_id,
            net_id=net_id,
            layer="F.Cu",
            width_mm=0.5,
            start=start,
            end=end,
            via_ids=(),
            evidence_refs=(evidence,),
        )
        for segment_id, net_id, start, end in route_data
    )
    values = base_state.model_dump(mode="python", exclude={"fingerprint"})
    values.update(
        project_id=base_state.project_id,
        revision=base_state.revision,
        components=components,
        footprints=(),
        nets=nets,
        power_nets=tuple(net_ids.values()),
        signal_classes=(),
        high_current_nets=(),
        placement=(),
        routing=routes,
        route_paths=(),
        planes=(),
        keepouts=(),
        vias=(),
        constraints=(),
        unknowns=(),
        conflicts=(),
        evidence_refs=(evidence,),
        design_truth_fingerprint=None,
        hardware_truth_fingerprint=None,
    )
    state = type(base_state).create(**values)
    pin_roles = (
        PCBPowerPinEvidence.create(component_id="component-U1", pin_id="u1-vin-pin", pad_id="u1-vin", role=PCBPowerPinRole.VIN, net_id=net_ids["VIN"], evidence_refs=(evidence,)),
        PCBPowerPinEvidence.create(component_id="component-U1", pin_id="u1-return-pin", pad_id="u1-return", role=PCBPowerPinRole.RETURN, net_id=net_ids["GND"], evidence_refs=(evidence,)),
        PCBPowerPinEvidence.create(component_id="component-U1", pin_id="u1-sw-pin", pad_id="u1-sw", role=PCBPowerPinRole.SW, net_id=net_ids["SW"], evidence_refs=(evidence,)),
        PCBPowerPinEvidence.create(component_id="component-C1", pin_id="c1-power-pin", pad_id="c1-power", role=PCBPowerPinRole.CIN_POWER, net_id=net_ids["VIN"], evidence_refs=(evidence,)),
        PCBPowerPinEvidence.create(component_id="component-C1", pin_id="c1-return-pin", pad_id="c1-return", role=PCBPowerPinRole.CIN_RETURN, net_id=net_ids["GND"], evidence_refs=(evidence,)),
        PCBPowerPinEvidence.create(component_id="component-C2", pin_id="c2-power-pin", pad_id="c2-power", role=PCBPowerPinRole.COUT_POWER, net_id=net_ids["VOUT"], evidence_refs=(evidence,)),
        PCBPowerPinEvidence.create(component_id="component-C2", pin_id="c2-return-pin", pad_id="c2-return", role=PCBPowerPinRole.COUT_RETURN, net_id=net_ids["GND"], evidence_refs=(evidence,)),
        PCBPowerPinEvidence.create(component_id="component-L1", pin_id="l1-input-pin", pad_id="l1-input", role=PCBPowerPinRole.INDUCTOR_INPUT, net_id=net_ids["SW"], evidence_refs=(evidence,)),
        PCBPowerPinEvidence.create(component_id="component-L1", pin_id="l1-output-pin", pad_id="l1-output", role=PCBPowerPinRole.INDUCTOR_OUTPUT, net_id=net_ids["VOUT"], evidence_refs=(evidence,)),
        PCBPowerPinEvidence.create(component_id="component-R1", pin_id="r1-fb-pin", pad_id="r1-fb", role=PCBPowerPinRole.FB, net_id=net_ids["FB"], evidence_refs=(evidence,)),
    )
    stage = PCBPowerStageEvidence.create(
        power_stage_id="stage-1",
        topology=PCBPowerTopology.BUCK,
        controller_component_id="component-U1",
        input_net_id=net_ids["VIN"],
        output_net_id=net_ids["VOUT"],
        return_net_id=net_ids["GND"],
        switch_node_net_id=net_ids["SW"],
        input_capacitor_ids=("component-C1",),
        output_capacitor_ids=("component-C2",),
        inductor_ids=("component-L1",),
        feedback_component_ids=("component-R1",),
        feedback_net_ids=(net_ids["FB"],),
        switching_component_ids=("component-Q1",),
        pin_roles=pin_roles,
        hot_loop_route_segment_ids=("hot-1", "hot-2", "hot-3", "hot-4"),
        hot_loop_via_ids=(),
        output_route_segment_ids=(),
        constraint_ids=(),
        evidence_refs=(evidence,),
        confidence=1.0,
    )
    rules = () if not with_rule else (
        __import__("embedded_copilot.engineering_pcb", fromlist=["PCBPowerLoopRule"]).PCBPowerLoopRule.create(
            rule_id="hot-loop-perimeter-rule",
            metric=PCBPowerLoopMetric.HOT_LOOP_PERIMETER,
            limit=6.0,
            unit="mm",
            comparison="LE",
            evidence_refs=(evidence,),
        ),
    )
    constraint_set = PCBConstraintCompiler().compile(
        state,
        power_stage_evidence=(stage,),
        power_loop_rules=rules,
    )
    constraint = next(item for item in constraint_set.constraints if item.constraint_type.value == "SWITCHING_POWER")
    hard_constraint = type(constraint).create(
        **{
            **constraint.model_dump(mode="python", exclude={"fingerprint"}),
            "enforcement_mode": PCBConstraintEnforcementMode.HARD,
        }
    )
    constraint_set = type(constraint_set).create(
        project_id=constraint_set.project_id,
        revision=constraint_set.revision,
        state_fingerprint=constraint_set.state_fingerprint,
        constraints=tuple(
            hard_constraint if item.constraint_id == hard_constraint.constraint_id else item
            for item in constraint_set.constraints
        ),
        evidence_refs=constraint_set.evidence_refs,
    )
    plan = PCBConstraintCompiler().build_plan(state, constraint_set)
    return state, constraint_set, plan, evidence


def test_canonical_direct_input_is_not_applicable_without_switching_stage() -> None:
    state, _, _, _ = base_fixture._base()
    values = state.model_dump(mode="python", exclude={"fingerprint"})
    values["design_truth_fingerprint"] = ZERO
    state = type(state).create(**values)
    constraints = PCBConstraintCompiler().compile(state)
    plan = PCBConstraintCompiler().build_plan(state, constraints)
    detailed = PowerLoopVerifier().verify_detailed(state, constraints, plan)
    assert detailed.result.status is VerificationStatus.NOT_APPLICABLE
    assert detailed.result.applicability is PCBApplicability.NOT_APPLICABLE.value
    assert not detailed.power_stages


def test_controlled_buck_resolves_typed_relationship_and_passes_geometry_rule() -> None:
    state, constraints, plan, _ = _fixture()
    detailed = PowerLoopVerifier().verify_detailed(state, constraints, plan)
    assert detailed.result.status is VerificationStatus.PASS
    assert detailed.applicability is PCBApplicability.REQUIRED
    assert detailed.power_stages[0].topology is PCBPowerTopology.BUCK
    assert detailed.power_stages[0].input_capacitor_ids == ("component-C1",)
    assert detailed.loops[0].loop_type.value == "INPUT_HOT_LOOP"
    assert any(item.method.value == "ROUTED_LOOP_PERIMETER" and item.value == 6.0 for item in detailed.measurements)
    assert detailed.switch_nodes[0].net_id == "net-sw"
    assert detailed.feedback[0].feedback_net_ids == ("net-fb",)


def test_hot_loop_violation_blocks_same_constraint_prevent() -> None:
    state, constraints, plan, _ = _fixture()
    corrected = PowerLoopPreventEvaluator().evaluate(
        state,
        constraints,
        PowerLayoutCandidate.create(
            candidate_id="valid",
            base_state_fingerprint=state.fingerprint,
            route_segments=state.routing,
        ),
        plan,
    )
    assert corrected.status.value == "ALLOW"
    larger = tuple(
        type(route).create(
            **{
                **route.model_dump(mode="python", exclude={"fingerprint"}),
                "start": (route.start[0] * 2.0, route.start[1] * 2.0),
                "end": (route.end[0] * 2.0, route.end[1] * 2.0),
            }
        )
        if route.segment_id.startswith("hot-")
        else route
        for route in state.routing
    )
    blocked = PowerLoopPreventEvaluator().evaluate(
        state,
        constraints,
        PowerLayoutCandidate.create(
            candidate_id="larger-loop",
            base_state_fingerprint=state.fingerprint,
            route_segments=larger,
        ),
        plan,
    )
    assert blocked.status.value == "BLOCK"
    assert blocked.constraint_id == next(item for item in constraints.constraints if item.constraint_type.value == "SWITCHING_POWER").constraint_id
    assert blocked.geometry_measurements


def test_wrong_cin_relation_fails_even_when_capacitor_is_geometrically_close() -> None:
    state, constraints, plan, _ = _fixture(wrong_cin=True)
    result = PowerLoopVerifier().verify(state, constraints, plan)
    assert result.status is VerificationStatus.FAIL
    assert any(issue.category == "INPUT_CAPACITOR_RELATION_INVALID" for issue in result.issues)


def test_missing_machine_rule_is_unknown_and_missing_stage_evidence_is_unknown() -> None:
    state, constraints, plan, _ = _fixture(with_rule=False)
    result = PowerLoopVerifier().verify(state, constraints, plan)
    assert result.status is VerificationStatus.UNKNOWN
    assert "MISSING_MACHINE_EVALUABLE_RULE" in result.unknowns

    values = state.model_dump(mode="python", exclude={"fingerprint"})
    values["high_current_nets"] = ("net-vin",)
    state_without_stage = type(state).create(**values)
    constraints_without_stage = PCBConstraintCompiler().compile(state_without_stage)
    plan_without_stage = PCBConstraintCompiler().build_plan(state_without_stage, constraints_without_stage)
    missing = PowerLoopVerifier().verify(state_without_stage, constraints_without_stage, plan_without_stage)
    assert missing.status is VerificationStatus.UNKNOWN
    assert "POWER_STAGE_RELATION_UNKNOWN" in missing.unknowns


def test_topology_is_not_inferred_from_component_or_net_names() -> None:
    state, constraints, _, _ = _fixture()
    original = next(item for item in constraints.constraints if item.constraint_type.value == "SWITCHING_POWER")
    unknown_stage = type(original.power_stage_evidence[0]).create(
        **{
            **original.power_stage_evidence[0].model_dump(mode="python", exclude={"fingerprint"}),
            "topology": PCBPowerTopology.UNKNOWN,
        }
    )
    rules = original.power_loop_rules
    unknown_constraints = PCBConstraintCompiler().compile(
        state,
        power_stage_evidence=(unknown_stage,),
        power_loop_rules=rules,
    )
    power_constraint = next(
        item for item in unknown_constraints.constraints if item.constraint_type.value == "SWITCHING_POWER"
    )
    unknown_plan = PCBConstraintCompiler().build_plan(state, unknown_constraints)
    result = PowerLoopVerifier().verify(state, unknown_constraints, unknown_plan)
    assert power_constraint.applicability is PCBApplicability.UNKNOWN
    assert result.status is VerificationStatus.UNKNOWN
    assert "POWER_TOPOLOGY_UNKNOWN" in result.unknowns
