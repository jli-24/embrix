from __future__ import annotations

from datetime import UTC, datetime

from embedded_copilot.engineering_pcb import (
    PCBApplicability,
    PCBArtifactReference,
    PCBArtifactType,
    PCBConstraint,
    PCBConstraintEnforcementMode,
    PCBConstraintSet,
    PCBConstraintSource,
    PCBConstraintType,
    PCBEngineeringProjector,
    PCBFoundationEvidence,
    PCBFoundationEvidenceSource,
    PCBGeometryQuality,
    PCBKeepoutProjection,
    PCBPlaneProjection,
    PCBPolygonProjection,
    PCBReferenceDiscontinuityType,
    PCBRouteCandidate,
    PCBRoutePathProjection,
    PCBRouteSegmentProjection,
    PCBSourceFormat,
    PCBViaProjection,
    ReturnPathPreventEvaluator,
    ReturnPathVerifier,
)
from embedded_copilot.pcb.models import (
    PCBLayer,
    PCBNet,
    PCBNetNode,
    PCBPosition,
    PCBTrack,
    UnifiedPCBModel,
)
from embedded_copilot.verification_loop import VerificationStatus

ZERO = "sha256:" + "0" * 64
NOW = datetime(2026, 9, 18, tzinfo=UTC)


def _base() -> tuple[object, PCBConstraintSet, object, PCBFoundationEvidence]:
    artifact = PCBArtifactReference.create(
        project_id="g7b2-project",
        artifact_id="g7b2-pcb",
        artifact_type=PCBArtifactType.BOARD,
        source_format=PCBSourceFormat.KICAD_PCB,
        source_reference="artifact://g7b2-project/g7b2-pcb",
        filename="main.kicad_pcb",
        byte_size=1,
        content_fingerprint="sha256:" + "1" * 64,
        captured_at=NOW,
    )
    evidence = PCBFoundationEvidence.create(
        evidence_id="g7b2-evidence",
        project_id=artifact.project_id,
        source_type=PCBFoundationEvidenceSource.DESIGN_DECISION,
        source_id="return-path-decision",
        source_fingerprint="sha256:" + "2" * 64,
        claim="Approved continuous reference-plane assignment for the controlled fixture.",
        locator="pcb://g7b2/return-path",
    )
    parsed = UnifiedPCBModel(
        board_name="main",
        source_format="kicad_pcb",
        layers=(
            PCBLayer(name="F.Cu", index=0, type="signal"),
            PCBLayer(name="B.Cu", index=31, type="signal"),
        ),
        nets=(
            PCBNet(name="SIG", net_type="signal", nodes=(PCBNetNode(reference="U1", pin="1"),)),
            PCBNet(name="GND", net_type="ground"),
            PCBNet(name="3V3", net_type="power"),
        ),
        tracks=(
            PCBTrack(
                start=PCBPosition(x_mm=1.0, y_mm=1.0),
                end=PCBPosition(x_mm=9.0, y_mm=1.0),
                width_mm=0.2,
                layer="F.Cu",
                net_name="SIG",
            ),
        ),
    )
    state = PCBEngineeringProjector().project(
        project_id=artifact.project_id,
        revision=artifact.content_fingerprint,
        source_artifact=artifact,
        parsed=parsed,
        evidence_refs=(evidence,),
    )
    signal_net = next(item.net_id for item in state.nets if item.name == "SIG")
    gnd_net = next(item.net_id for item in state.nets if item.name == "GND")
    constraint = PCBConstraint.create(
        constraint_id="g7b2-return-path",
        constraint_type=PCBConstraintType.RETURN_PATH,
        scope="PCB",
        target_ids=(signal_net,),
        requirements=("continuous_reference_plane", "valid_layer_transition_return"),
        source=PCBConstraintSource.DESIGN_DECISION,
        evidence_refs=(evidence,),
        confidence=1.0,
        applicability=PCBApplicability.REQUIRED,
        revision=state.revision,
        source_fingerprint=evidence.source_fingerprint,
        return_path_net_ids=(signal_net,),
        return_path_reference_net_id=gnd_net,
        return_path_reference_layers=("B.Cu",),
        return_path_requirement="CONTINUOUS_REFERENCE",
        enforcement_mode=PCBConstraintEnforcementMode.HARD,
    )
    constraints = PCBConstraintSet.create(
        project_id=state.project_id,
        revision=state.revision,
        state_fingerprint=state.fingerprint,
        constraints=(constraint,),
        evidence_refs=(evidence,),
    )
    plan = __import__("embedded_copilot.engineering_pcb", fromlist=["PCBConstraintCompiler"]).PCBConstraintCompiler().build_plan(state, constraints)
    return state, constraints, plan, evidence


def _polygon(plane_id: str, points: tuple[tuple[float, float], ...]):
    return PCBPolygonProjection.create(
        polygon_id=f"{plane_id}-polygon",
        points=points,
        holes=(),
    )


def _with_planes(state, planes, *, keepouts=()):
    values = state.model_dump(mode="python", exclude={"fingerprint"})
    values["planes"] = tuple(planes)
    values["keepouts"] = tuple(keepouts)
    return type(state).create(**values)


def _plane(state, evidence, *, net_id, polygons, layer="B.Cu", discontinuity=None):
    return PCBPlaneProjection.create(
        plane_id=f"plane-{len(polygons)}-{net_id}",
        net_id=net_id,
        layer=layer,
        geometry_ref="pcb://g7b2/plane",
        polygons=tuple(polygons),
        geometry_quality=PCBGeometryQuality.EXACT,
        discontinuity_type=discontinuity,
        evidence_refs=(evidence,),
    )


def test_return_path_relationship_is_typed_and_assignment_is_constraint_bound():
    state, constraints, plan, evidence = _base()
    verifier = ReturnPathVerifier()
    state = _with_planes(
        state,
        (
            _plane(
                state,
                evidence,
                net_id=next(item.net_id for item in state.nets if item.name == "GND"),
                polygons=(_polygon("continuous", ((0.0, 0.0), (10.0, 0.0), (10.0, 2.0), (0.0, 2.0))),),
            ),
        ),
    )
    constraints = PCBConstraintSet.create(
        project_id=constraints.project_id,
        revision=constraints.revision,
        state_fingerprint=state.fingerprint,
        constraints=(
            constraints.constraints[0].model_copy(update={"fingerprint": ZERO}),
        ),
        evidence_refs=constraints.evidence_refs,
    )
    constraints = PCBConstraintSet.create(
        project_id=constraints.project_id,
        revision=constraints.revision,
        state_fingerprint=state.fingerprint,
        constraints=(
            type(constraints.constraints[0]).create(
                **{
                    **constraints.constraints[0].model_dump(mode="python", exclude={"fingerprint"}),
                    "revision": state.revision,
                    "fingerprint": ZERO,
                }
            ),
        ),
        evidence_refs=constraints.evidence_refs,
    )
    plan = __import__("embedded_copilot.engineering_pcb", fromlist=["PCBConstraintCompiler"]).PCBConstraintCompiler().build_plan(state, constraints)
    result = verifier.verify(state, constraints, plan)
    assert result.status is VerificationStatus.PASS
    assert result.verifier == "RETURN_PATH"
    assert result.scope == "PCB"
    assert result.measurement_refs
    assert result.evidence_refs


def test_split_crossing_fails_and_hard_same_constraint_blocks_candidate():
    state, constraints, plan, evidence = _base()
    gnd = next(item.net_id for item in state.nets if item.name == "GND")
    state = _with_planes(
        state,
        (
            _plane(state, evidence, net_id=gnd, polygons=(_polygon("left", ((0.0, 0.0), (4.0, 0.0), (4.0, 2.0), (0.0, 2.0))),), discontinuity=PCBReferenceDiscontinuityType.SPLIT),
            _plane(state, evidence, net_id=gnd, polygons=(_polygon("right", ((6.0, 0.0), (10.0, 0.0), (10.0, 2.0), (6.0, 2.0))),), discontinuity=PCBReferenceDiscontinuityType.SPLIT),
        ),
    )
    constraint = type(constraints.constraints[0]).create(
        **{**constraints.constraints[0].model_dump(mode="python", exclude={"fingerprint"}), "revision": state.revision}
    )
    constraints = type(constraints).create(
        project_id=constraints.project_id,
        revision=state.revision,
        state_fingerprint=state.fingerprint,
        constraints=(constraint,),
        evidence_refs=constraints.evidence_refs,
    )
    plan = __import__("embedded_copilot.engineering_pcb", fromlist=["PCBConstraintCompiler"]).PCBConstraintCompiler().build_plan(state, constraints)
    result = ReturnPathVerifier().verify(state, constraints, plan)
    assert result.status is VerificationStatus.FAIL
    assert any(issue.category == "PLANE_SPLIT_CROSSING" for issue in result.issues)
    candidate = PCBRouteCandidate.create(
        candidate_id="split-candidate",
        base_state_fingerprint=state.fingerprint,
        route_segments=state.routing,
        route_paths=state.route_paths,
        vias=state.vias,
    )
    prevent = ReturnPathPreventEvaluator().evaluate(state, constraints, candidate, plan)
    assert prevent.status.value == "BLOCK"
    assert prevent.constraint_id == constraint.constraint_id
    assert prevent.geometry_measurements

    corrected_values = state.routing[0].model_dump(mode="python", exclude={"fingerprint"})
    corrected_values["end"] = (3.0, 1.0)
    corrected_segment = type(state.routing[0]).create(**corrected_values)
    corrected_candidate = PCBRouteCandidate.create(
        candidate_id="corrected-candidate",
        base_state_fingerprint=state.fingerprint,
        route_segments=(corrected_segment,),
        route_paths=state.route_paths,
        vias=state.vias,
    )
    corrected = ReturnPathPreventEvaluator().evaluate(state, constraints, corrected_candidate, plan)
    assert corrected.status.value == "ALLOW"


def test_missing_assignment_is_unknown_and_never_guesses_nearest_ground():
    state, constraints, plan, evidence = _base()
    state = _with_planes(
        state,
        (
            _plane(
                state,
                evidence,
                net_id=next(item.net_id for item in state.nets if item.name == "GND"),
                polygons=(_polygon("continuous", ((0.0, 0.0), (10.0, 0.0), (10.0, 2.0), (0.0, 2.0))),),
            ),
        ),
    )
    raw = constraints.constraints[0].model_dump(mode="python", exclude={"fingerprint"})
    raw.update(return_path_reference_net_id=None, return_path_reference_layers=())
    constraint = PCBConstraint.create(**raw)
    constraints = PCBConstraintSet.create(
        project_id=state.project_id,
        revision=state.revision,
        state_fingerprint=state.fingerprint,
        constraints=(constraint,),
        evidence_refs=(evidence,),
    )
    plan = __import__("embedded_copilot.engineering_pcb", fromlist=["PCBConstraintCompiler"]).PCBConstraintCompiler().build_plan(state, constraints)
    result = ReturnPathVerifier().verify(state, constraints, plan)
    assert result.status is VerificationStatus.UNKNOWN
    assert "MISSING_REFERENCE_ASSIGNMENT" in result.unknowns


def test_wrong_reference_net_and_void_keepout_are_deterministic():
    state, constraints, plan, evidence = _base()
    wrong = next(item.net_id for item in state.nets if item.name == "3V3")
    state = _with_planes(
        state,
        (
            _plane(state, evidence, net_id=wrong, polygons=(_polygon("wrong", ((0.0, 0.0), (10.0, 0.0), (10.0, 2.0), (0.0, 2.0))),),),
        ),
    )
    constraints = PCBConstraintSet.create(
        project_id=state.project_id,
        revision=state.revision,
        state_fingerprint=state.fingerprint,
        constraints=(constraints.constraints[0].model_copy(update={"fingerprint": ZERO}),),
        evidence_refs=constraints.evidence_refs,
    )
    constraints = PCBConstraintSet.create(
        project_id=state.project_id,
        revision=state.revision,
        state_fingerprint=state.fingerprint,
        constraints=(PCBConstraint.create(**{**constraints.constraints[0].model_dump(mode="python", exclude={"fingerprint"}), "revision": state.revision}),),
        evidence_refs=constraints.evidence_refs,
    )
    plan = __import__("embedded_copilot.engineering_pcb", fromlist=["PCBConstraintCompiler"]).PCBConstraintCompiler().build_plan(state, constraints)
    result = ReturnPathVerifier().verify(state, constraints, plan)
    assert result.status is VerificationStatus.FAIL
    assert any(issue.category == "REFERENCE_NET_MISMATCH" for issue in result.issues)

    gnd = next(item.net_id for item in state.nets if item.name == "GND")
    keepout = PCBKeepoutProjection.create(
        keepout_id="keepout-1",
        layer="B.Cu",
        polygons=(_polygon("keepout", ((4.0, 0.0), (6.0, 0.0), (6.0, 2.0), (4.0, 2.0))),),
        geometry_quality=PCBGeometryQuality.EXACT,
        evidence_refs=(evidence,),
    )
    state = _with_planes(
        state,
        (_plane(state, evidence, net_id=gnd, polygons=(_polygon("ground", ((0.0, 0.0), (10.0, 0.0), (10.0, 2.0), (0.0, 2.0))),),),),
        keepouts=(keepout,),
    )
    constraint = PCBConstraint.create(**{**constraints.constraints[0].model_dump(mode="python", exclude={"fingerprint"}), "revision": state.revision})
    constraints = PCBConstraintSet.create(project_id=state.project_id, revision=state.revision, state_fingerprint=state.fingerprint, constraints=(constraint,), evidence_refs=(evidence,))
    plan = __import__("embedded_copilot.engineering_pcb", fromlist=["PCBConstraintCompiler"]).PCBConstraintCompiler().build_plan(state, constraints)
    keepout_result = ReturnPathVerifier().verify(state, constraints, plan)
    assert keepout_result.status is VerificationStatus.FAIL
    assert any(issue.category == "KEEPOUT_CROSSING" for issue in keepout_result.issues)

    void_polygon = PCBPolygonProjection.create(
        polygon_id="void-plane",
        points=((0.0, 0.0), (10.0, 0.0), (10.0, 2.0), (0.0, 2.0)),
        holes=(((4.0, 0.5), (6.0, 0.5), (6.0, 1.5), (4.0, 1.5)),),
    )
    state = _with_planes(
        state,
        (_plane(state, evidence, net_id=gnd, polygons=(void_polygon,), discontinuity=PCBReferenceDiscontinuityType.VOID),),
    )
    constraint = PCBConstraint.create(**{**constraints.constraints[0].model_dump(mode="python", exclude={"fingerprint"}), "revision": state.revision})
    constraints = PCBConstraintSet.create(project_id=state.project_id, revision=state.revision, state_fingerprint=state.fingerprint, constraints=(constraint,), evidence_refs=(evidence,))
    plan = __import__("embedded_copilot.engineering_pcb", fromlist=["PCBConstraintCompiler"]).PCBConstraintCompiler().build_plan(state, constraints)
    void_result = ReturnPathVerifier().verify(state, constraints, plan)
    assert void_result.status is VerificationStatus.FAIL
    assert any(issue.category == "VOID_CROSSING" for issue in void_result.issues)


def test_layer_transition_requires_explicit_via_evidence_and_is_not_required_same_layer():
    state, constraints, _, evidence = _base()
    signal_net = next(item.net_id for item in state.nets if item.name == "SIG")
    gnd = next(item.net_id for item in state.nets if item.name == "GND")
    first = state.routing[0].model_copy(update={
        "end": (5.0, 1.0),
        "via_ids": ("via-transition",),
        "fingerprint": ZERO,
    })
    second = PCBRouteSegmentProjection.create(
        segment_id="route-second-layer",
        net_id=signal_net,
        layer="B.Cu",
        width_mm=0.2,
        start=(5.0, 1.0),
        end=(9.0, 1.0),
        via_ids=("via-transition",),
        evidence_refs=(evidence,),
    )
    values = state.model_dump(mode="python", exclude={"fingerprint"})
    values["routing"] = (first, second)
    values["route_paths"] = (PCBRoutePathProjection.create(
        route_id="route-transition",
        net_id=signal_net,
        segment_ids=(first.segment_id, second.segment_id),
        evidence_refs=(evidence,),
    ),)
    values["planes"] = (
        _plane(state, evidence, net_id=gnd, layer="F.Cu", polygons=(_polygon("front-ref", ((0.0, 0.0), (10.0, 0.0), (10.0, 2.0), (0.0, 2.0))),)),
        _plane(state, evidence, net_id=gnd, layer="B.Cu", polygons=(_polygon("back-ref", ((0.0, 0.0), (10.0, 0.0), (10.0, 2.0), (0.0, 2.0))),)),
    )
    values["vias"] = (PCBViaProjection.create(
        via_id="via-transition",
        net_id=signal_net,
        x=5.0,
        y=1.0,
        from_layer="F.Cu",
        to_layer="B.Cu",
        diameter_mm=0.6,
        drill_mm=0.3,
        evidence_refs=(evidence,),
    ),)
    state = type(state).create(**values)
    raw = constraints.constraints[0].model_dump(mode="python", exclude={"fingerprint"})
    raw.update(return_path_reference_layers=("F.Cu", "B.Cu"), revision=state.revision)
    constraint = PCBConstraint.create(**raw)
    constraints = PCBConstraintSet.create(project_id=state.project_id, revision=state.revision, state_fingerprint=state.fingerprint, constraints=(constraint,), evidence_refs=(evidence,))
    plan = __import__("embedded_copilot.engineering_pcb", fromlist=["PCBConstraintCompiler"]).PCBConstraintCompiler().build_plan(state, constraints)
    result = ReturnPathVerifier().verify(state, constraints, plan)
    assert result.status is VerificationStatus.PASS
    assert result.measurement_refs


def test_result_is_deterministic_and_approximate_geometry_is_not_pass():
    state, constraints, plan, evidence = _base()
    gnd = next(item.net_id for item in state.nets if item.name == "GND")
    plane = PCBPlaneProjection.create(
        plane_id="approximate-plane",
        net_id=gnd,
        layer="B.Cu",
        geometry_ref="pcb://g7b2/approximate",
        polygons=(_polygon("approx", ((0.0, 0.0), (10.0, 0.0), (10.0, 2.0), (0.0, 2.0))),),
        geometry_quality=PCBGeometryQuality.APPROXIMATE,
        evidence_refs=(evidence,),
    )
    state = _with_planes(state, (plane,))
    constraint = PCBConstraint.create(**{**constraints.constraints[0].model_dump(mode="python", exclude={"fingerprint"}), "revision": state.revision})
    constraints = PCBConstraintSet.create(project_id=state.project_id, revision=state.revision, state_fingerprint=state.fingerprint, constraints=(constraint,), evidence_refs=(evidence,))
    plan = __import__("embedded_copilot.engineering_pcb", fromlist=["PCBConstraintCompiler"]).PCBConstraintCompiler().build_plan(state, constraints)
    verifier = ReturnPathVerifier()
    first = verifier.verify(state, constraints, plan)
    second = verifier.verify(state, constraints, plan)
    assert first.fingerprint == second.fingerprint
    assert first.status is VerificationStatus.UNKNOWN
    assert "APPROXIMATE_GEOMETRY" in first.unknowns


def test_missing_stackup_evidence_is_unknown():
    state, constraints, _, evidence = _base()
    gnd = next(item.net_id for item in state.nets if item.name == "GND")
    state = _with_planes(
        state,
        (_plane(state, evidence, net_id=gnd, polygons=(_polygon("continuous", ((0.0, 0.0), (10.0, 0.0), (10.0, 2.0), (0.0, 2.0))),),),),
    )
    values = state.model_dump(mode="python", exclude={"fingerprint"})
    stackup_values = state.stackup.model_dump(mode="python", exclude={"fingerprint"})
    stackup_values.update(layers=(), evidence_refs=())
    values["stackup"] = type(state.stackup).create(**stackup_values)
    state = type(state).create(**values)
    raw = constraints.constraints[0].model_dump(mode="python", exclude={"fingerprint"})
    raw["revision"] = state.revision
    constraint = PCBConstraint.create(**raw)
    constraints = PCBConstraintSet.create(
        project_id=state.project_id,
        revision=state.revision,
        state_fingerprint=state.fingerprint,
        constraints=(constraint,),
        evidence_refs=(evidence,),
    )
    plan = __import__("embedded_copilot.engineering_pcb", fromlist=["PCBConstraintCompiler"]).PCBConstraintCompiler().build_plan(state, constraints)
    result = ReturnPathVerifier().verify(state, constraints, plan)
    assert result.status is VerificationStatus.UNKNOWN
    assert "MISSING_STACKUP_EVIDENCE" in result.unknowns
