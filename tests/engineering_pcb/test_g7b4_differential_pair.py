from __future__ import annotations

from datetime import UTC, datetime

import pytest

from embedded_copilot.engineering_pcb import (
    DifferentialPairEvaluationEngine,
    DifferentialPairMeasurementMethod,
    DifferentialPairPreventEvaluator,
    DifferentialPairRouteCandidate,
    DifferentialPairRuleMetric,
    DifferentialPairVerifier,
    PCBApplicability,
    PCBConstraint,
    PCBConstraintCompiler,
    PCBConstraintEnforcementMode,
    PCBConstraintSet,
    PCBConstraintSource,
    PCBConstraintType,
    PCBDifferentialPairBinding,
    PCBDifferentialPairProjection,
    PCBDifferentialPairRule,
    PCBEngineeringProjector,
    PCBInterfaceProjection,
    PCBNetProjection,
    PCBRoutePathProjection,
    PCBRouteSegmentProjection,
    PCBSignalClass,
    PCBSignalClassProjection,
    PCBViaProjection,
)
from embedded_copilot.verification_loop import (
    VerificationEvidenceReference,
    VerificationResult,
    VerificationStatus,
)
from tests.engineering_pcb import test_g7b2_return_path as base_fixture

ZERO = "sha256:" + "0" * 64


def _state(*, length_delta: float = 0.0, wrong_pair: bool = False, pair_evidence=True):
    state, _, _, evidence = base_fixture._base()
    nets = tuple(state.nets) + (
        PCBNetProjection.create(
            net_id="net-dp",
            name="D_P",
            member_pad_ids=(),
            signal_id="signal-dp",
            design_truth_status="MATCH",
            evidence_refs=(evidence,),
        ),
        PCBNetProjection.create(
            net_id="net-dm",
            name="D_N",
            member_pad_ids=(),
            signal_id="signal-dm",
            design_truth_status="MATCH",
            evidence_refs=(evidence,),
        ),
        PCBNetProjection.create(
            net_id="net-wrong",
            name="UNRELATED",
            member_pad_ids=(),
            signal_id="signal-wrong",
            design_truth_status="MATCH",
            evidence_refs=(evidence,),
        ),
    )
    negative_net = "net-wrong" if wrong_pair else "net-dm"
    pair_evidence_refs = (evidence,) if pair_evidence else ()
    pairs = (
        PCBDifferentialPairProjection.create(
            pair_id="pair-usb",
            positive_net="net-dp",
            negative_net=negative_net,
            interface_id="interface-usb",
            evidence_refs=pair_evidence_refs,
        ),
    )
    signal_classes = tuple(state.signal_classes) + (
        PCBSignalClassProjection.create(
            signal_id="signal-dp",
            net_id="net-dp",
            signal_class=PCBSignalClass.DIFFERENTIAL,
            protocol="USB",
            evidence_refs=(evidence,),
        ),
        PCBSignalClassProjection.create(
            signal_id="signal-dm",
            net_id="net-dm",
            signal_class=PCBSignalClass.DIFFERENTIAL,
            protocol="USB",
            evidence_refs=(evidence,),
        ),
        PCBSignalClassProjection.create(
            signal_id="signal-wrong",
            net_id="net-wrong",
            signal_class=PCBSignalClass.DIGITAL,
            protocol=None,
            evidence_refs=(evidence,),
        ),
    )
    interface = PCBInterfaceProjection.create(
        interface_id="interface-usb",
        protocol="USB",
        signal_ids=("signal-dm", "signal-dp"),
        net_ids=("net-dm", "net-dp"),
        evidence_refs=(evidence,),
    )
    positive = PCBRouteSegmentProjection.create(
        segment_id="dp-1",
        net_id="net-dp",
        layer="F.Cu",
        width_mm=0.2,
        start=(1.0, 1.0),
        end=(9.0 + length_delta, 1.0),
        via_ids=(),
        evidence_refs=(evidence,),
    )
    negative = PCBRouteSegmentProjection.create(
        segment_id="dm-1",
        net_id=negative_net,
        layer="F.Cu",
        width_mm=0.2,
        start=(1.0, 0.0),
        end=(9.0, 0.0),
        via_ids=(),
        evidence_refs=(evidence,),
    )
    values = state.model_dump(mode="python", exclude={"fingerprint"})
    values.update(
        nets=nets,
        signal_classes=signal_classes,
        differential_pairs=pairs,
        interfaces=tuple(state.interfaces) + (interface,),
        routing=tuple(state.routing) + (positive, negative),
        route_paths=tuple(state.route_paths)
        + (
            PCBRoutePathProjection.create(
                route_id="dp-path",
                net_id="net-dp",
                segment_ids=("dp-1",),
                evidence_refs=(evidence,),
            ),
            PCBRoutePathProjection.create(
                route_id="dm-path",
                net_id=negative_net,
                segment_ids=("dm-1",),
                evidence_refs=(evidence,),
            ),
        ),
    )
    return type(state).create(**values), evidence


def _constraint_set(
    state,
    evidence,
    *,
    rules=(),
    enforcement=PCBConstraintEnforcementMode.HARD,
    bindings=None,
    reference=None,
    impedance=None,
    applicability=PCBApplicability.REQUIRED,
    requirements=("positive_negative_net_pair", "interface_bound", "length_matching", "pair_spacing"),
):
    if bindings is None:
        bindings = (
            PCBDifferentialPairBinding.create(
                pair_id="pair-usb",
                interface_id="interface-usb",
                positive_net_id="net-dp",
                negative_net_id="net-dm",
                positive_signal_id="signal-dp",
                negative_signal_id="signal-dm",
                evidence_refs=(evidence,),
            ),
        )
    constraint = PCBConstraint.create(
        constraint_id="constraint-differential-usb",
        constraint_type=PCBConstraintType.DIFFERENTIAL_PAIR,
        scope="PCB",
        target_ids=("pair-usb",),
        requirements=tuple(requirements),
        source=PCBConstraintSource.INTERFACE_SPEC,
        evidence_refs=(evidence,),
        confidence=1.0,
        applicability=applicability,
        revision=state.revision,
        source_fingerprint=evidence.source_fingerprint,
        enforcement_mode=enforcement,
        differential_pair_bindings=tuple(bindings),
        differential_pair_rules=tuple(rules),
        differential_reference_requirement=reference,
        differential_impedance_ohms=impedance,
    )
    constraint_set = PCBConstraintSet.create(
        project_id=state.project_id,
        revision=state.revision,
        state_fingerprint=state.fingerprint,
        constraints=(constraint,),
        evidence_refs=(evidence,),
    )
    plan = __import__("embedded_copilot.engineering_pcb", fromlist=["PCBConstraintCompiler"]).PCBConstraintCompiler().build_plan(state, constraint_set)
    return constraint_set, plan


def _return_path_result(state, constraints, plan, status, evidence=None, *, project_id=None):
    evidence_ref = ()
    evidence_refs = ()
    if evidence is not None:
        evidence_ref = (
            VerificationEvidenceReference.create(
                evidence_id=evidence.evidence_id,
                source_kind=evidence.source_type.value,
                source_fingerprint=evidence.source_fingerprint,
                locator=f"evidence:{evidence.evidence_id}",
                description=evidence.claim,
            ),
        )
        evidence_refs = (evidence.evidence_id,)
    return VerificationResult.create(
        verification_id="return-path-dependency",
        project_id=state.project_id if project_id is None else project_id,
        target_id=state.fingerprint,
        status=status,
        attempt=0,
        compared_fingerprints=(state.fingerprint, constraints.fingerprint, plan.fingerprint),
        evidence_refs=evidence_refs,
        summary="Synthetic ReturnPath dependency for the differential-pair contract test.",
        verifier="RETURN_PATH",
        scope="PCB",
        evidence=evidence_ref,
        revision=state.revision,
        applicability=PCBApplicability.REQUIRED.value,
    )


def _rules(evidence):
    return (
        PCBDifferentialPairRule.create(
            rule_id="usb-max-skew",
            metric=DifferentialPairRuleMetric.LENGTH_DIFFERENCE,
            comparison="LE",
            limit=0.5,
            unit="mm",
            evidence_refs=(evidence,),
        ),
        PCBDifferentialPairRule.create(
            rule_id="usb-min-spacing",
            metric=DifferentialPairRuleMetric.PAIR_SPACING_MIN,
            comparison="GE",
            limit=0.5,
            unit="mm",
            evidence_refs=(evidence,),
        ),
    )


def test_differential_pair_not_applicable_without_explicit_pair():
    state, constraints, plan, _ = base_fixture._base()
    result = DifferentialPairVerifier().verify(state, constraints, plan)
    assert result.status is VerificationStatus.NOT_APPLICABLE
    assert result.applicability == PCBApplicability.NOT_APPLICABLE.value


def test_explicit_pair_geometry_passes_and_uses_mm_planar_measurement():
    state, evidence = _state()
    constraints, plan = _constraint_set(state, evidence, rules=_rules(evidence))
    detailed = DifferentialPairVerifier().verify_detailed(state, constraints, plan)
    assert detailed.result.status is VerificationStatus.PASS
    assert detailed.relationships[0].positive_net_id == "net-dp"
    assert detailed.relationships[0].negative_net_id == "net-dm"
    measurement = detailed.measurements[0]
    assert measurement.positive_length_mm == 8.0
    assert measurement.negative_length_mm == 8.0
    assert measurement.length_difference_mm == 0.0
    assert measurement.length_method is DifferentialPairMeasurementMethod.PLANAR_ROUTE_LENGTH
    assert detailed.result.verifier == "DIFFERENTIAL_PAIR"


def test_length_violation_fails_and_same_hard_constraint_blocks_candidate():
    state, evidence = _state(length_delta=1.0)
    constraints, plan = _constraint_set(state, evidence, rules=_rules(evidence))
    result = DifferentialPairVerifier().verify(state, constraints, plan)
    assert result.status is VerificationStatus.FAIL
    assert any(issue.category == "LENGTH_MATCH_RULE_VIOLATION" for issue in result.issues)
    candidate = DifferentialPairRouteCandidate.create(
        candidate_id="candidate-skew",
        base_state_fingerprint=state.fingerprint,
        pair_id="pair-usb",
        constraint_id="constraint-differential-usb",
        positive_route_segments=(next(item for item in state.routing if item.segment_id == "dp-1"),),
        negative_route_segments=(next(item for item in state.routing if item.segment_id == "dm-1"),),
        positive_route_path=next(item for item in state.route_paths if item.route_id == "dp-path"),
        negative_route_path=next(item for item in state.route_paths if item.route_id == "dm-path"),
    )
    prevent = DifferentialPairPreventEvaluator().evaluate(state, constraints, candidate, plan)
    assert prevent.status.value == "BLOCK"
    assert prevent.constraint_id == constraints.constraints[0].constraint_id


def test_wrong_pair_identity_fails_even_when_geometry_is_close():
    state, evidence = _state(wrong_pair=True)
    constraints, plan = _constraint_set(state, evidence, rules=_rules(evidence))
    result = DifferentialPairVerifier().verify(state, constraints, plan)
    assert result.status is VerificationStatus.FAIL
    assert any(issue.category == "DIFFERENTIAL_PAIR_IDENTITY_MISMATCH" for issue in result.issues)


def test_missing_machine_rule_is_unknown_and_never_passes():
    state, evidence = _state()
    constraints, plan = _constraint_set(state, evidence, rules=())
    result = DifferentialPairVerifier().verify(state, constraints, plan)
    assert result.status is VerificationStatus.UNKNOWN
    assert "MISSING_MACHINE_EVALUABLE_RULE" in result.unknowns


def test_missing_pair_evidence_is_unknown():
    state, evidence = _state(pair_evidence=False)
    constraints, plan = _constraint_set(state, evidence, rules=_rules(evidence))
    result = DifferentialPairVerifier().verify(state, constraints, plan)
    assert result.status is VerificationStatus.UNKNOWN
    assert "PAIR_IDENTITY_EVIDENCE_UNKNOWN" in result.unknowns


def test_prevent_allow_is_read_only_and_uses_same_constraint():
    state, evidence = _state()
    constraints, plan = _constraint_set(state, evidence, rules=_rules(evidence))
    before = state.fingerprint
    candidate = DifferentialPairRouteCandidate.create(
        candidate_id="candidate-valid",
        base_state_fingerprint=state.fingerprint,
        pair_id="pair-usb",
        constraint_id="constraint-differential-usb",
        positive_route_segments=(next(item for item in state.routing if item.segment_id == "dp-1"),),
        negative_route_segments=(next(item for item in state.routing if item.segment_id == "dm-1"),),
        positive_route_path=next(item for item in state.route_paths if item.route_id == "dp-path"),
        negative_route_path=next(item for item in state.route_paths if item.route_id == "dm-path"),
    )
    evaluation = DifferentialPairPreventEvaluator().evaluate(state, constraints, candidate, plan)
    assert evaluation.status.value == "ALLOW"
    assert evaluation.constraint_id == constraints.constraints[0].constraint_id
    assert state.fingerprint == before


def test_return_path_dependency_unknown_and_failure_are_fail_closed():
    state, evidence = _state()
    constraints, plan = _constraint_set(state, evidence, rules=_rules(evidence), reference="RETURN_PATH_REQUIRED")
    unknown_dependency = _return_path_result(state, constraints, plan, VerificationStatus.UNKNOWN, evidence)
    unknown = DifferentialPairVerifier().verify_detailed(
        state,
        constraints,
        plan,
        return_path_result=unknown_dependency,
    )
    assert unknown.result.status is VerificationStatus.UNKNOWN
    assert "REFERENCE_DEPENDENCY_UNKNOWN" in unknown.result.unknowns
    assert evidence.evidence_id in unknown.result.evidence_refs

    failed_dependency = _return_path_result(state, constraints, plan, VerificationStatus.FAIL)
    failed = DifferentialPairVerifier().verify_detailed(
        state,
        constraints,
        plan,
        return_path_result=failed_dependency,
    )
    assert failed.result.status is VerificationStatus.FAIL
    assert any(issue.category == "REFERENCE_DEPENDENCY_FAILED" for issue in failed.result.issues)


def test_return_path_dependency_identity_mismatch_is_unknown():
    state, evidence = _state()
    constraints, plan = _constraint_set(state, evidence, rules=_rules(evidence), reference="RETURN_PATH_REQUIRED")
    dependency = _return_path_result(state, constraints, plan, VerificationStatus.FAIL, project_id="other-project")
    result = DifferentialPairVerifier().verify_detailed(
        state,
        constraints,
        plan,
        return_path_result=dependency,
    ).result
    assert result.status is VerificationStatus.UNKNOWN
    assert "REFERENCE_DEPENDENCY_IDENTITY_UNKNOWN" in result.unknowns


def test_impedance_requirement_without_solver_evidence_is_unknown():
    state, evidence = _state()
    constraints, plan = _constraint_set(state, evidence, rules=_rules(evidence), impedance=90.0)
    result = DifferentialPairVerifier().verify(state, constraints, plan)
    assert result.status is VerificationStatus.UNKNOWN
    assert "IMPEDANCE_NOT_VERIFIED" in result.unknowns
    assert not result.issues


def test_candidate_pair_identity_mismatch_is_unknown_and_never_allowed():
    state, evidence = _state()
    constraints, plan = _constraint_set(state, evidence, rules=_rules(evidence))
    candidate = DifferentialPairRouteCandidate.create(
        candidate_id="candidate-unknown-pair",
        base_state_fingerprint=state.fingerprint,
        pair_id="pair-not-in-state",
        constraint_id="constraint-differential-usb",
        positive_route_segments=(),
        negative_route_segments=(),
    )
    evaluation = DifferentialPairPreventEvaluator().evaluate(state, constraints, candidate, plan)
    assert evaluation.status.value == "UNKNOWN"
    assert "CANDIDATE_PAIR_ID_UNKNOWN" in evaluation.unknowns


def test_prevent_candidate_must_bind_the_same_constraint():
    state, evidence = _state()
    constraints, plan = _constraint_set(state, evidence, rules=_rules(evidence))
    candidate = DifferentialPairRouteCandidate.create(
        candidate_id="candidate-wrong-constraint",
        base_state_fingerprint=state.fingerprint,
        pair_id="pair-usb",
        constraint_id="constraint-from-another-review",
        positive_route_segments=(),
        negative_route_segments=(),
    )
    evaluation = DifferentialPairPreventEvaluator().evaluate(state, constraints, candidate, plan)
    assert evaluation.status.value == "UNKNOWN"
    assert "CANDIDATE_CONSTRAINT_MISMATCH" in evaluation.unknowns


def test_prevent_candidate_without_any_approved_pair_is_unknown_not_allow():
    state, _ = _state()
    state_values = state.model_dump(mode="python", exclude={"fingerprint"})
    state_values["differential_pairs"] = ()
    no_pair_state = type(state).create(**state_values)
    constraints = PCBConstraintCompiler().compile(no_pair_state)
    plan = PCBConstraintCompiler().build_plan(no_pair_state, constraints)
    differential_constraint = next(
        item for item in constraints.constraints if item.constraint_type is PCBConstraintType.DIFFERENTIAL_PAIR
    )
    candidate = DifferentialPairRouteCandidate.create(
        candidate_id="candidate-without-approved-pair",
        base_state_fingerprint=no_pair_state.fingerprint,
        pair_id="pair-usb",
        constraint_id=differential_constraint.constraint_id,
        positive_route_segments=(),
        negative_route_segments=(),
    )
    evaluation = DifferentialPairPreventEvaluator().evaluate(no_pair_state, constraints, candidate, plan)
    assert evaluation.status.value == "UNKNOWN"
    assert "CANDIDATE_PAIR_ID_UNKNOWN" in evaluation.unknowns


def test_transition_asymmetry_requires_explicit_rule():
    state, evidence = _state()
    positive_via = PCBViaProjection.create(
        via_id="via-dp",
        net_id="net-dp",
        x=5.0,
        y=1.0,
        from_layer="F.Cu",
        to_layer="In1.Cu",
        diameter_mm=0.6,
        drill_mm=0.3,
        evidence_refs=(evidence,),
    )
    negative_via = PCBViaProjection.create(
        via_id="via-dm",
        net_id="net-dm",
        x=5.0,
        y=0.0,
        from_layer="F.Cu",
        to_layer="In1.Cu",
        diameter_mm=0.6,
        drill_mm=0.3,
        evidence_refs=(evidence,),
    )
    values = state.model_dump(mode="python", exclude={"fingerprint"})
    routing = tuple(
        type(item).create(
            **{
                **item.model_dump(mode="python", exclude={"fingerprint"}),
                "via_ids": ("via-dp",) if item.segment_id == "dp-1" else ("via-dm",),
            }
        )
        for item in state.routing
        if item.segment_id in {"dp-1", "dm-1"}
    )
    values.update(routing=routing, vias=(positive_via, negative_via))
    matched_state = type(state).create(**values)
    matched_rules = (*_rules(evidence), PCBDifferentialPairRule.create(
        rule_id="usb-transition-match",
        metric=DifferentialPairRuleMetric.MATCHED_TRANSITIONS,
        comparison=None,
        limit=None,
        unit=None,
        evidence_refs=(evidence,),
    ))
    constraints, plan = _constraint_set(matched_state, evidence, rules=matched_rules)
    assert DifferentialPairVerifier().verify(matched_state, constraints, plan).status is VerificationStatus.PASS

    asymmetric_values = matched_state.model_dump(mode="python", exclude={"fingerprint"})
    asymmetric_values["vias"] = (
        positive_via,
        PCBViaProjection.create(
            **{
                **negative_via.model_dump(mode="python", exclude={"fingerprint"}),
                "to_layer": "B.Cu",
            }
        ),
    )
    asymmetric_state = type(state).create(**asymmetric_values)
    constraints, plan = _constraint_set(asymmetric_state, evidence, rules=_rules(evidence))
    without_rule = DifferentialPairVerifier().verify(asymmetric_state, constraints, plan)
    assert without_rule.status is VerificationStatus.PASS
    explicit_constraints, explicit_plan = _constraint_set(asymmetric_state, evidence, rules=matched_rules)
    explicit = DifferentialPairVerifier().verify(asymmetric_state, explicit_constraints, explicit_plan)
    assert explicit.status is VerificationStatus.FAIL
    assert any(issue.category == "LAYER_TRANSITION_ASYMMETRY" for issue in explicit.issues)

    electrical_rules = (
        *matched_rules,
        PCBDifferentialPairRule.create(
            rule_id="usb-electrical-skew",
            metric=DifferentialPairRuleMetric.ELECTRICAL_LENGTH_DIFFERENCE,
            comparison="LE",
            limit=0.5,
            unit="mm",
            evidence_refs=(evidence,),
        ),
    )
    electrical_constraints, electrical_plan = _constraint_set(
        matched_state,
        evidence,
        rules=electrical_rules,
    )
    electrical = DifferentialPairVerifier().verify_detailed(
        matched_state,
        electrical_constraints,
        electrical_plan,
    )
    assert electrical.result.status is VerificationStatus.UNKNOWN
    assert "VIA_VERTICAL_LENGTH_UNKNOWN" in electrical.result.unknowns
    assert electrical.measurements[0].length_method is DifferentialPairMeasurementMethod.PLANAR_ROUTE_LENGTH_ONLY
    assert electrical.measurements[0].via_vertical_length_status == "UNKNOWN"


def test_spacing_violation_is_reported_and_advisory_prevent_warns():
    state, evidence = _state()
    values = state.model_dump(mode="python", exclude={"fingerprint"})
    routing = []
    for item in state.routing:
        if item.segment_id != "dm-1":
            routing.append(item)
            continue
        routing.append(
            type(item).create(
                **{
                    **item.model_dump(mode="python", exclude={"fingerprint"}),
                    "start": (1.0, 0.3),
                    "end": (9.0, 0.3),
                }
            )
        )
    values["routing"] = tuple(routing)
    close_state = type(state).create(**values)
    constraints, plan = _constraint_set(
        close_state,
        evidence,
        rules=_rules(evidence),
        enforcement=PCBConstraintEnforcementMode.ADVISORY,
    )
    result = DifferentialPairVerifier().verify(close_state, constraints, plan)
    assert result.status is VerificationStatus.FAIL
    assert any(issue.category == "PAIR_SPACING_RULE_VIOLATION" for issue in result.issues)
    candidate = DifferentialPairRouteCandidate.create(
        candidate_id="candidate-close-spacing",
        base_state_fingerprint=close_state.fingerprint,
        pair_id="pair-usb",
        constraint_id="constraint-differential-usb",
        positive_route_segments=(next(item for item in close_state.routing if item.segment_id == "dp-1"),),
        negative_route_segments=(next(item for item in close_state.routing if item.segment_id == "dm-1"),),
        positive_route_path=next(item for item in close_state.route_paths if item.route_id == "dp-path"),
        negative_route_path=next(item for item in close_state.route_paths if item.route_id == "dm-path"),
    )
    prevent = DifferentialPairPreventEvaluator().evaluate(close_state, constraints, candidate, plan)
    assert prevent.status.value == "WARN"


def test_unknown_applicability_is_not_promoted_to_pass():
    state, evidence = _state()
    constraints, plan = _constraint_set(
        state,
        evidence,
        rules=_rules(evidence),
        applicability=PCBApplicability.UNKNOWN,
    )
    result = DifferentialPairVerifier().verify(state, constraints, plan)
    assert result.status is VerificationStatus.UNKNOWN
    assert result.applicability == PCBApplicability.UNKNOWN.value
    assert "DIFFERENTIAL_PAIR_APPLICABILITY_UNKNOWN" in result.unknowns


def test_qualitative_length_and_spacing_requirements_remain_unknown_without_thresholds():
    state, evidence = _state()
    length_constraints, length_plan = _constraint_set(
        state,
        evidence,
        requirements=("length_matching",),
    )
    length_result = DifferentialPairVerifier().verify(state, length_constraints, length_plan)
    assert length_result.status is VerificationStatus.UNKNOWN
    assert "MISSING_MACHINE_EVALUABLE_RULE" in length_result.unknowns

    spacing_constraints, spacing_plan = _constraint_set(
        state,
        evidence,
        requirements=("pair_spacing",),
    )
    spacing_result = DifferentialPairVerifier().verify(state, spacing_constraints, spacing_plan)
    assert spacing_result.status is VerificationStatus.UNKNOWN
    assert "MISSING_MACHINE_EVALUABLE_RULE" in spacing_result.unknowns


def test_segment_scope_candidate_is_explicitly_unknown():
    state, evidence = _state()
    constraints, plan = _constraint_set(state, evidence, rules=_rules(evidence))
    candidate = DifferentialPairRouteCandidate.create(
        candidate_id="candidate-segment-only",
        base_state_fingerprint=state.fingerprint,
        pair_id="pair-usb",
        constraint_id="constraint-differential-usb",
        positive_route_segments=(next(item for item in state.routing if item.segment_id == "dp-1"),),
        negative_route_segments=(next(item for item in state.routing if item.segment_id == "dm-1"),),
    )
    detailed = DifferentialPairEvaluationEngine().evaluate(
        state,
        constraints,
        plan,
        candidate=candidate,
    )
    assert detailed.result.status is VerificationStatus.UNKNOWN
    assert detailed.measurements[0].route_scope.value == "SEGMENT_SCOPE"
    assert "PAIR_ROUTE_INCOMPLETE" in detailed.result.unknowns


def test_stub_rule_uses_explicit_branch_evidence():
    state, evidence = _state()
    values = state.model_dump(mode="python", exclude={"fingerprint"})
    routing = []
    for item in state.routing:
        if item.segment_id != "dp-1":
            routing.append(item)
            continue
        base = item.model_dump(mode="python", exclude={"fingerprint"})
        routing.extend(
            (
                type(item).create(**{**base, "segment_id": "dp-main-1", "start": (1.0, 1.0), "end": (5.0, 1.0)}),
                type(item).create(**{**base, "segment_id": "dp-main-2", "start": (5.0, 1.0), "end": (9.0, 1.0)}),
                type(item).create(**{**base, "segment_id": "dp-stub", "start": (5.0, 1.0), "end": (5.0, 2.0)}),
            )
        )
    path = next(item for item in state.route_paths if item.route_id == "dp-path")
    new_path = type(path).create(
        **{
            **path.model_dump(mode="python", exclude={"fingerprint"}),
            "segment_ids": ("dp-main-1", "dp-main-2"),
        }
    )
    values["routing"] = tuple(routing)
    values["route_paths"] = tuple(new_path if item.route_id == "dp-path" else item for item in state.route_paths)
    branched_state = type(state).create(**values)
    stub_rule = PCBDifferentialPairRule.create(
        rule_id="usb-max-stub",
        metric=DifferentialPairRuleMetric.MAX_STUB_LENGTH,
        comparison="LE",
        limit=0.5,
        unit="mm",
        evidence_refs=(evidence,),
    )
    constraints, plan = _constraint_set(branched_state, evidence, rules=(*_rules(evidence), stub_rule))
    detailed = DifferentialPairVerifier().verify_detailed(branched_state, constraints, plan)
    measurement = detailed.measurements[0]
    assert measurement.stub_count == 1
    assert measurement.stub_ids == ("dp-stub",)
    assert detailed.result.status is VerificationStatus.FAIL
    assert any(issue.category == "STUB_RULE_VIOLATION" for issue in detailed.result.issues)


def test_repeated_identical_inputs_produce_identical_result():
    state, evidence = _state()
    constraints, plan = _constraint_set(state, evidence, rules=_rules(evidence))
    first = DifferentialPairVerifier().verify(state, constraints, plan)
    second = DifferentialPairVerifier().verify(state, constraints, plan)
    assert first.fingerprint == second.fingerprint


def test_projector_only_marks_explicit_pair_authority_as_required():
    from embedded_copilot.engineering_pcb import (
        PCBArtifactReference,
        PCBArtifactType,
        PCBFoundationEvidence,
        PCBFoundationEvidenceSource,
        PCBSourceFormat,
    )
    from embedded_copilot.pcb.models import PCBNet as ParsedNet
    from tests.engineering_pcb.test_g7a_foundation import _parsed_board

    evidence = PCBFoundationEvidence.create(
        evidence_id="evidence-g7b4-projector",
        project_id="project-g7b4-projector",
        source_type=PCBFoundationEvidenceSource.ARTIFACT,
        source_id="artifact-g7b4-projector",
        source_fingerprint=ZERO,
        claim="Explicit differential-pair authority for the projector test.",
        locator="artifact://pcb/g7b4-projector",
    )
    artifact = PCBArtifactReference.create(
        project_id="project-g7b4-projector",
        artifact_id="artifact-g7b4-projector",
        artifact_type=PCBArtifactType.BOARD,
        source_format=PCBSourceFormat.KICAD_PCB,
        source_reference="artifact://pcb/g7b4-projector",
        filename="main.kicad_pcb",
        byte_size=1,
        content_fingerprint=ZERO,
        captured_at=datetime(2026, 9, 18, tzinfo=UTC),
    )
    parsed = _parsed_board()
    parsed = parsed.model_copy(
        update={
            "nets": (
                *parsed.nets,
                ParsedNet(
                    name="DATA",
                    net_type="signal",
                    nodes=(),
                ),
            )
        }
    )
    projector = PCBEngineeringProjector()
    baseline = projector.project(
        project_id=artifact.project_id,
        revision=ZERO,
        source_artifact=artifact,
        parsed=parsed,
        evidence_refs=(evidence,),
    )
    pair = PCBDifferentialPairProjection.create(
        pair_id="pair-explicit-projector",
        positive_net=baseline.nets[0].net_id,
        negative_net=baseline.nets[1].net_id,
        interface_id=None,
        evidence_refs=(evidence,),
    )
    projected = projector.project(
        project_id=artifact.project_id,
        revision=ZERO,
        source_artifact=artifact,
        parsed=parsed,
        evidence_refs=(evidence,),
        differential_pairs=(pair,),
    )
    constraints = PCBConstraintCompiler().compile(projected)
    plan = PCBConstraintCompiler().build_plan(projected, constraints)
    differential = next(
        item for item in constraints.constraints if item.constraint_type is PCBConstraintType.DIFFERENTIAL_PAIR
    )
    assert projected.differential_pairs == (pair,)
    assert differential.applicability is PCBApplicability.REQUIRED
    assert "DifferentialPairVerifier" in plan.required_verifiers


def test_differential_pair_rule_rejects_unknown_comparison_operator():
    _, evidence = _state()
    with pytest.raises(ValueError, match="comparison"):
        PCBDifferentialPairRule.create(
            rule_id="invalid-comparison",
            metric=DifferentialPairRuleMetric.LENGTH_DIFFERENCE,
            comparison="BETWEEN",
            limit=0.5,
            unit="mm",
            evidence_refs=(evidence,),
        )
