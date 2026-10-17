from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from typing import Any

from .decoupling import PCBPreventEvaluation, PCBPreventStatus, _verification_types
from .foundation import (
    PCBApplicability,
    PCBConstraintEnforcementMode,
    PCBConstraintSet,
    PCBEngineeringState,
    PCBFoundationEvidence,
    PCBFoundationStatus,
    PCBGeometryQuality,
    PCBReferenceDiscontinuityType,
    PCBRoutePathProjection,
    PCBRouteSegmentProjection,
    PCBVerificationPlan,
    PCBViaProjection,
)
from .models import PCBContract


def _stable_id(prefix: str, *parts: object) -> str:
    payload = "|".join(str(item) for item in parts).encode("utf-8")
    return f"{prefix}-" + hashlib.sha256(payload).hexdigest()[:32]


class ReturnPathCoverageStatus(StrEnum):
    COVERED = "COVERED"
    UNCOVERED = "UNCOVERED"
    UNKNOWN = "UNKNOWN"


class ReturnPathVerificationScope(StrEnum):
    FULL_PATH = "FULL_PATH"
    SEGMENT_LEVEL = "SEGMENT_LEVEL"


class LayerTransitionReturnStatus(StrEnum):
    PASS = "PASS"
    UNKNOWN = "UNKNOWN"


class PCBRouteCandidate(PCBContract):
    candidate_id: str
    base_state_fingerprint: str
    route_segments: tuple[PCBRouteSegmentProjection, ...]
    route_paths: tuple[PCBRoutePathProjection, ...] = ()
    vias: tuple[PCBViaProjection, ...] = ()

    @classmethod
    def create(cls, **values: object) -> PCBRouteCandidate:
        return cls.model_validate(values)


class ReturnPathRelationship(PCBContract):
    relationship_id: str
    signal_id: str | None
    net_id: str
    route_path_id: str | None
    route_segment_ids: tuple[str, ...]
    signal_layers: tuple[str, ...]
    expected_reference_net_id: str | None
    expected_reference_layer_id: str | None
    expected_reference_layers: tuple[str, ...]
    constraint_id: str
    transition_ids: tuple[str, ...]
    evidence_refs: tuple[PCBFoundationEvidence, ...]
    verification_scope: ReturnPathVerificationScope
    confidence: float
    unknowns: tuple[str, ...] = ()

    @classmethod
    def create(cls, **values: object) -> ReturnPathRelationship:
        return cls.model_validate(values)


class ReferenceCoverageMeasurement(PCBContract):
    measurement_id: str
    route_segment_id: str
    signal_layer: str
    reference_layer: str | None
    reference_net: str | None
    checked_geometry: tuple[str, ...]
    coverage_status: ReturnPathCoverageStatus
    covered_length: float | None = None
    uncovered_length: float | None = None
    coverage_ratio: float | None = None
    geometry_quality: PCBGeometryQuality

    @classmethod
    def create(cls, **values: object) -> ReferenceCoverageMeasurement:
        return cls.model_validate(values)


class ReferenceDiscontinuity(PCBContract):
    discontinuity_id: str
    type: PCBReferenceDiscontinuityType
    location: tuple[float, float]
    affected_route_segment: str
    expected_reference: str
    geometry_evidence: tuple[str, ...]
    unknown_subtype: str | None = None

    @classmethod
    def create(cls, **values: object) -> ReferenceDiscontinuity:
        return cls.model_validate(values)


class LayerTransitionReturnAssessment(PCBContract):
    transition_id: str
    via_id: str | None
    from_signal_layer: str
    to_signal_layer: str
    from_reference_layer: str | None
    to_reference_layer: str | None
    reference_net: str | None
    supporting_evidence: tuple[str, ...]
    status: LayerTransitionReturnStatus
    unknowns: tuple[str, ...] = ()

    @classmethod
    def create(cls, **values: object) -> LayerTransitionReturnAssessment:
        return cls.model_validate(values)


@dataclass(frozen=True)
class ReturnPathEvaluation:
    result: Any
    relationships: tuple[ReturnPathRelationship, ...]
    measurements: tuple[ReferenceCoverageMeasurement, ...]
    discontinuities: tuple[ReferenceDiscontinuity, ...]
    transitions: tuple[LayerTransitionReturnAssessment, ...]


class ReturnPathRelationshipResolver:
    """Resolve only explicit route and reference assignments from one constraint."""

    def resolve(
        self,
        state: PCBEngineeringState,
        constraint_set: PCBConstraintSet,
        *,
        candidate: PCBRouteCandidate | None = None,
    ) -> tuple[ReturnPathRelationship, ...]:
        checked_state = PCBEngineeringState.model_validate(state.model_dump(mode="python"))
        checked_set = PCBConstraintSet.model_validate(constraint_set.model_dump(mode="python"))
        segments = checked_state.routing if candidate is None else candidate.route_segments
        paths = checked_state.route_paths if candidate is None else candidate.route_paths
        by_net = {item.net_id: item for item in checked_state.nets}
        routes_by_net: dict[str, tuple[PCBRouteSegmentProjection, ...]] = {}
        for segment in segments:
            routes_by_net.setdefault(segment.net_id, ())
            routes_by_net[segment.net_id] = (*routes_by_net[segment.net_id], segment)
        relationships: list[ReturnPathRelationship] = []
        for constraint in checked_set.constraints:
            if constraint.constraint_type.value != "RETURN_PATH":
                continue
            # G7-A's compiler already binds RETURN_PATH target_ids to the
            # current net identities.  Reuse that explicit binding; do not
            # infer a target from names, proximity, or a nearest plane.
            target_nets = tuple(dict.fromkeys(constraint.return_path_net_ids or constraint.target_ids))
            target_nets = tuple(item for item in target_nets if item in by_net)
            if constraint.return_path_signal_ids:
                target_nets = tuple(
                    item.net_id
                    for item in checked_state.nets
                    if item.signal_id in constraint.return_path_signal_ids
                )
            for net_id in target_nets:
                net = by_net[net_id]
                matching_paths = tuple(path for path in paths if path.net_id == net_id)
                path_items = matching_paths or (
                    PCBRoutePathProjection.create(
                        route_id=_stable_id("route-segment-scope", checked_state.project_id, net_id),
                        net_id=net_id,
                        segment_ids=tuple(item.segment_id for item in routes_by_net.get(net_id, ())),
                        evidence_refs=tuple(checked_state.evidence_refs),
                    ),
                )
                for path in path_items:
                    segment_ids = tuple(path.segment_ids)
                    route_segments = tuple(item for item in segments if item.segment_id in segment_ids)
                    unknowns: list[str] = []
                    if not route_segments:
                        unknowns.append("ROUTE_IDENTITY_INCOMPLETE")
                    if len(route_segments) != len(segment_ids):
                        unknowns.append("ROUTE_SEGMENT_IDENTITY_INCOMPLETE")
                    if constraint.return_path_reference_net_id is None or not constraint.return_path_reference_layers:
                        unknowns.append("MISSING_REFERENCE_ASSIGNMENT")
                    if not constraint.evidence_refs:
                        unknowns.append("MISSING_REFERENCE_ASSIGNMENT_EVIDENCE")
                    scope = (
                        ReturnPathVerificationScope.FULL_PATH
                        if matching_paths and not any(
                            item in {"ROUTE_IDENTITY_INCOMPLETE", "ROUTE_SEGMENT_IDENTITY_INCOMPLETE"}
                            for item in unknowns
                        )
                        else ReturnPathVerificationScope.SEGMENT_LEVEL
                    )
                    relationships.append(
                        ReturnPathRelationship.create(
                            relationship_id=_stable_id("return-path", checked_state.project_id, constraint.constraint_id, path.route_id),
                            signal_id=net.signal_id,
                            net_id=net_id,
                            route_path_id=path.route_id if matching_paths else None,
                            route_segment_ids=segment_ids,
                            signal_layers=tuple(sorted({item.layer for item in route_segments})),
                            expected_reference_net_id=constraint.return_path_reference_net_id,
                            expected_reference_layer_id=(constraint.return_path_reference_layers[0] if constraint.return_path_reference_layers else None),
                            expected_reference_layers=constraint.return_path_reference_layers,
                            constraint_id=constraint.constraint_id,
                            transition_ids=tuple(sorted({via_id for item in route_segments for via_id in item.via_ids})),
                            evidence_refs=tuple(dict.fromkeys((*checked_state.evidence_refs, *constraint.evidence_refs))),
                            verification_scope=scope,
                            confidence=constraint.confidence,
                            unknowns=tuple(dict.fromkeys(unknowns)),
                        )
                    )
        return tuple(sorted(relationships, key=lambda item: item.relationship_id))


class ReturnPathEvaluationEngine:
    """Deterministic geometry evaluator shared by Verify and Prevent."""

    def evaluate(
        self,
        state: PCBEngineeringState,
        constraint_set: PCBConstraintSet,
        plan: PCBVerificationPlan,
        *,
        candidate: PCBRouteCandidate | None = None,
    ) -> ReturnPathEvaluation:
        _, _, _, VerificationStatus = _verification_types()

        checked_state = PCBEngineeringState.model_validate(state.model_dump(mode="python"))
        checked_set = PCBConstraintSet.model_validate(constraint_set.model_dump(mode="python"))
        checked_plan = PCBVerificationPlan.model_validate(plan.model_dump(mode="python"))
        return_constraints = tuple(item for item in checked_set.constraints if item.constraint_type.value == "RETURN_PATH")
        relationships = ReturnPathRelationshipResolver().resolve(checked_state, checked_set, candidate=candidate)
        evidence = _evidence_objects(checked_state, checked_set, relationships)
        compared = (
            checked_state.fingerprint,
            checked_set.fingerprint,
            checked_plan.fingerprint,
            checked_state.source_artifact_fingerprint,
        )
        affected = tuple(sorted({item for relation in relationships for item in (
            relation.net_id,
            relation.route_path_id,
            *relation.route_segment_ids,
            relation.expected_reference_net_id,
            *relation.expected_reference_layers,
            *relation.transition_ids,
        ) if item is not None}))
        if not return_constraints:
            return self._finish(
                checked_state, compared, evidence, (), (), (), VerificationStatus.UNKNOWN,
                "RETURN_PATH constraint is unavailable.", ("MISSING_RETURN_PATH_CONSTRAINT",), (), affected,
                relationships=relationships,
            )
        constraint = return_constraints[0]
        if (
            checked_set.state_fingerprint != checked_state.fingerprint
            or checked_plan.pcb_state_fingerprint != checked_state.fingerprint
            or checked_plan.constraint_set_fingerprint != checked_set.fingerprint
            or checked_state.status is PCBFoundationStatus.STALE
            or checked_set.status is PCBFoundationStatus.STALE
            or checked_plan.status is PCBFoundationStatus.STALE
        ):
            return self._finish(
                checked_state, compared, evidence, (), (), (), VerificationStatus.UNKNOWN,
                "RETURN_PATH inputs are stale or fingerprint-mismatched.", ("STALE_INPUTS",), (), affected,
                relationships=relationships,
            )
        if constraint.applicability is PCBApplicability.NOT_APPLICABLE:
            return self._finish(
                checked_state, compared, evidence, (), (), (), VerificationStatus.NOT_APPLICABLE,
                "RETURN_PATH is not applicable to the current PCB state.", (), (), affected,
                relationships=relationships,
            )
        if constraint.return_path_requirement != "CONTINUOUS_REFERENCE":
            return self._finish(
                checked_state, compared, evidence, (), (), (), VerificationStatus.UNKNOWN,
                "RETURN_PATH constraint is qualitative or not machine-evaluable.",
                ("MISSING_MACHINE_EVALUABLE_RULE",), (), affected,
                relationships=relationships,
            )
        if not relationships:
            return self._finish(
                checked_state, compared, evidence, (), (), (), VerificationStatus.UNKNOWN,
                "No evidence-bound return-path relationship could be resolved.",
                ("MISSING_RETURN_PATH_RELATION",), (), affected,
                relationships=relationships,
            )
        stackup_layers = set(checked_state.stackup.layers)
        if not checked_state.stackup.layers or not checked_state.stackup.evidence_refs:
            return self._finish(
                checked_state, compared, evidence, (), (), (), VerificationStatus.UNKNOWN,
                "Stackup or layer evidence is unavailable for RETURN_PATH verification.",
                ("MISSING_STACKUP_EVIDENCE",), (), affected,
                relationships=relationships,
            )
        missing_stackup_layers = tuple(sorted({
            layer
            for relation in relationships
            for layer in (*relation.signal_layers, *relation.expected_reference_layers)
            if layer not in stackup_layers
        }))
        if missing_stackup_layers:
            return self._finish(
                checked_state, compared, evidence, (), (), (), VerificationStatus.UNKNOWN,
                "The route or assigned reference layer is absent from the evidence-backed stackup.",
                ("MISSING_STACKUP_LAYER_EVIDENCE",), (), affected,
                relationships=relationships,
            )

        issues: list[Any] = []
        unknowns: list[str] = []
        measurements: list[ReferenceCoverageMeasurement] = []
        discontinuities: list[ReferenceDiscontinuity] = []
        transitions: list[LayerTransitionReturnAssessment] = []
        statuses: list[Any] = []
        for relation in relationships:
            if relation.unknowns:
                unknowns.extend(relation.unknowns)
                statuses.append(VerificationStatus.UNKNOWN)
                continue
            relation_segments = tuple(
                item for item in (checked_state.routing if candidate is None else candidate.route_segments)
                if item.segment_id in relation.route_segment_ids
            )
            for segment in relation_segments:
                measurement, segment_discontinuities, segment_status, segment_issue, segment_unknowns = self._measure_segment(
                    checked_state, relation, segment
                )
                measurements.append(measurement)
                discontinuities.extend(segment_discontinuities)
                statuses.append(segment_status)
                if segment_issue is not None:
                    issues.append(segment_issue)
                unknowns.extend(segment_unknowns)
            relation_transitions, transition_status, transition_unknowns = self._transitions(
                checked_state,
                relation,
                relation_segments,
                candidate_vias=() if candidate is None else candidate.vias,
            )
            transitions.extend(relation_transitions)
            statuses.append(transition_status)
            unknowns.extend(transition_unknowns)

        status = _worst(statuses, VerificationStatus)
        if status is VerificationStatus.PASS:
            summary = "All evaluated route segments have an evidence-backed continuous return reference path."
        elif status is VerificationStatus.FAIL:
            summary = "One or more route segments have a deterministic return-reference violation."
        else:
            summary = "RETURN_PATH verification is unknown because required assignment or geometry evidence is unavailable."
        return self._finish(
            checked_state, compared, evidence, tuple(measurements), tuple(discontinuities), tuple(transitions),
            status, summary, tuple(dict.fromkeys(unknowns)), tuple(issues), affected,
            relationships=relationships,
        )

    def _measure_segment(self, state, relation, segment):
        _, VerificationIssue, _, VerificationStatus = _verification_types()

        expected_layers = set(relation.expected_reference_layers)
        candidate_planes = tuple(
            plane for plane in state.planes
            if plane.layer in expected_layers and plane.net_id == relation.expected_reference_net_id
        )
        wrong_planes = tuple(
            plane for plane in state.planes
            if plane.layer in expected_layers and plane.net_id != relation.expected_reference_net_id
        )
        qualities = tuple(item.geometry_quality for item in (*candidate_planes, *wrong_planes))
        geometry_ids = tuple(sorted(item.plane_id for item in candidate_planes))
        if not candidate_planes and _route_intersects_planes(segment, wrong_planes):
            if any(item is not PCBGeometryQuality.EXACT for item in qualities):
                return _measurement(
                    segment, relation, (), ReturnPathCoverageStatus.UNKNOWN, _quality(qualities)
                ), (), VerificationStatus.UNKNOWN, None, ("APPROXIMATE_GEOMETRY",)
            issue = _issue(
                VerificationIssue, "REFERENCE_NET_MISMATCH", segment.segment_id,
                (segment.segment_id, *[item.plane_id for item in wrong_planes]),
                tuple(item.evidence_id for plane in wrong_planes for item in plane.evidence_refs),
                "The route span is covered by a copper plane with the wrong reference net.",
                ("REFERENCE_NET_MISMATCH",),
                "Bind the route to the approved reference net and rerun verification.",
            )
            return _measurement(segment, relation, (), ReturnPathCoverageStatus.UNCOVERED, PCBGeometryQuality.EXACT), (), VerificationStatus.FAIL, issue, ()
        if not candidate_planes:
            return _measurement(segment, relation, (), ReturnPathCoverageStatus.UNKNOWN, PCBGeometryQuality.UNAVAILABLE), (), VerificationStatus.UNKNOWN, None, ("REFERENCE_PLANE_MISSING",)
        if any(item is not PCBGeometryQuality.EXACT for item in qualities) or any(not item.polygons for item in candidate_planes):
            return _measurement(segment, relation, geometry_ids, ReturnPathCoverageStatus.UNKNOWN, _quality(qualities)), (), VerificationStatus.UNKNOWN, None, ("APPROXIMATE_GEOMETRY" if any(item is PCBGeometryQuality.APPROXIMATE for item in qualities) else "UNKNOWN_GEOMETRY",)

        total = _length(segment)
        covered_intervals = _union_intervals(
            interval
            for plane in candidate_planes
            for polygon in plane.polygons
            for interval in _polygon_intervals(segment, polygon)
        )
        covered = total * sum(end - start for start, end in covered_intervals)
        uncovered = max(0.0, total - covered)
        discontinuities: list[ReferenceDiscontinuity] = []
        issues: list[Any] = []
        segment_status = VerificationStatus.PASS
        if uncovered > 1e-9:
            point = _point_at(segment, _first_uncovered_t(segment, covered_intervals))
            dtype = _discontinuity_type(state, relation, segment, point)
            discontinuity = ReferenceDiscontinuity.create(
                discontinuity_id=_stable_id("return-discontinuity", relation.relationship_id, segment.segment_id, dtype.value),
                type=dtype,
                location=point,
                affected_route_segment=segment.segment_id,
                expected_reference=f"{relation.expected_reference_net_id}@{','.join(relation.expected_reference_layers)}",
                geometry_evidence=tuple(dict.fromkeys((*geometry_ids, *[item.keepout_id for item in state.keepouts])),),
                unknown_subtype=("UNCLASSIFIED_REFERENCE_GAP" if dtype is PCBReferenceDiscontinuityType.REFERENCE_DISCONTINUITY else None),
            )
            discontinuities.append(discontinuity)
            segment_status = VerificationStatus.FAIL
            category = {
                PCBReferenceDiscontinuityType.SPLIT: "PLANE_SPLIT_CROSSING",
                PCBReferenceDiscontinuityType.VOID: "VOID_CROSSING",
                PCBReferenceDiscontinuityType.KEEPOUT: "KEEPOUT_CROSSING",
                PCBReferenceDiscontinuityType.MISSING_PLANE: "REFERENCE_PLANE_MISSING",
            }.get(dtype, "REFERENCE_PLANE_DISCONTINUITY")
            issues.append(_issue(
                VerificationIssue, category, segment.segment_id,
                (segment.segment_id, *geometry_ids),
                tuple(item.evidence_id for plane in candidate_planes for item in plane.evidence_refs),
                "The expected reference copper is discontinuous across the route span.",
                (category,),
                "Provide a continuous approved reference path or revise the route under human review.",
            ))
        if _route_intersects_keepout(segment, state.keepouts, relation.expected_reference_layers):
            segment_status = VerificationStatus.FAIL
            keepout_ids = tuple(
                item.keepout_id
                for item in state.keepouts
                if _route_intersects_keepout(segment, (item,), relation.expected_reference_layers)
            )
            issues.append(_issue(
                VerificationIssue, "KEEPOUT_CROSSING", segment.segment_id,
                (segment.segment_id, *keepout_ids),
                tuple(item.evidence_id for keepout in state.keepouts for item in keepout.evidence_refs),
                "The route span crosses an evidence-backed reference keepout.",
                ("KEEPOUT_CROSSING",),
                "Remove the crossing or obtain an approved constraint change.",
            ))
        issue = issues[0] if issues else None
        unknowns = ()
        return _measurement(
            segment, relation, geometry_ids,
            ReturnPathCoverageStatus.COVERED if segment_status is VerificationStatus.PASS else ReturnPathCoverageStatus.UNCOVERED,
            PCBGeometryQuality.EXACT, covered, uncovered, (covered / total if total else 1.0),
        ), tuple(discontinuities), segment_status, issue, unknowns

    @staticmethod
    def _transitions(state, relation, segments, *, candidate_vias=()):
        _, _, _, VerificationStatus = _verification_types()

        if len({item.layer for item in segments}) <= 1:
            return (), VerificationStatus.PASS, ()
        by_id = {item.via_id: item for item in (*state.vias, *candidate_vias)}
        assessments: list[LayerTransitionReturnAssessment] = []
        unknowns: list[str] = []
        for transition_id in relation.transition_ids:
            via = by_id.get(transition_id)
            if via is None:
                assessments.append(LayerTransitionReturnAssessment.create(
                    transition_id=transition_id,
                    via_id=transition_id,
                    from_signal_layer="UNKNOWN",
                    to_signal_layer="UNKNOWN",
                    from_reference_layer=None,
                    to_reference_layer=None,
                    reference_net=relation.expected_reference_net_id,
                    supporting_evidence=(),
                    status=LayerTransitionReturnStatus.UNKNOWN,
                    unknowns=("MISSING_TRANSITION_EVIDENCE",),
                ))
                unknowns.append("MISSING_TRANSITION_EVIDENCE")
                continue
            supported = via.from_layer in relation.expected_reference_layers and via.to_layer in relation.expected_reference_layers
            assessments.append(LayerTransitionReturnAssessment.create(
                transition_id=transition_id,
                via_id=via.via_id,
                from_signal_layer=via.from_layer,
                to_signal_layer=via.to_layer,
                from_reference_layer=via.from_layer if supported else None,
                to_reference_layer=via.to_layer if supported else None,
                reference_net=relation.expected_reference_net_id,
                supporting_evidence=tuple(item.evidence_id for item in via.evidence_refs),
                status=LayerTransitionReturnStatus.PASS if supported else LayerTransitionReturnStatus.UNKNOWN,
                unknowns=() if supported else ("REFERENCE_TRANSITION_RELATION_UNKNOWN",),
            ))
            if not supported:
                unknowns.append("REFERENCE_TRANSITION_RELATION_UNKNOWN")
        if not assessments:
            return (), VerificationStatus.UNKNOWN, ("LAYER_TRANSITION_UNVERIFIED",)
        return tuple(assessments), VerificationStatus.PASS if not unknowns else VerificationStatus.UNKNOWN, tuple(unknowns)

    @staticmethod
    def _finish(state, compared, evidence, measurements, discontinuities, transitions, status, summary, unknowns, issues, affected, *, relationships=()):
        VerificationEvidenceReference, _, VerificationResult, _ = _verification_types()

        evidence_objects = tuple(
            VerificationEvidenceReference.create(
                evidence_id=item.evidence_id,
                source_kind=item.source_type.value,
                source_fingerprint=item.source_fingerprint,
                locator=f"evidence:{item.evidence_id}",
                description=item.claim,
            )
            for item in evidence
        )
        result = VerificationResult.create(
            verification_id=_stable_id("verification", state.project_id, state.fingerprint, compared[1], compared[2]),
            project_id=state.project_id,
            target_id=state.fingerprint,
            status=status,
            attempt=0,
            compared_fingerprints=compared,
            evidence_refs=tuple(item.evidence_id for item in evidence),
            summary=summary,
            verifier="RETURN_PATH",
            scope="PCB",
            issues=tuple(issues),
            evidence=evidence_objects,
            affected_objects=tuple(affected),
            confidence=None if status.value == "UNKNOWN" else 1.0,
            assumptions=("The supplied PCBEngineeringState is the current G5/G7-A projection.",),
            unknowns=tuple(dict.fromkeys(unknowns)),
            recommendations=tuple(dict.fromkeys(item.recommendation for item in issues)),
            measurement_refs=tuple(item.measurement_id for item in measurements),
            revision=state.revision,
        )
        return ReturnPathEvaluation(result, tuple(relationships), measurements, discontinuities, transitions)


class ReturnPathVerifier:
    def __init__(self, engine: ReturnPathEvaluationEngine | None = None) -> None:
        self._engine = engine or ReturnPathEvaluationEngine()

    def verify(self, state, constraint_set, plan):
        return self._engine.evaluate(state, constraint_set, plan).result

    def verify_detailed(self, state, constraint_set, plan):
        return self._engine.evaluate(state, constraint_set, plan)


class ReturnPathPreventEvaluator:
    def __init__(self, engine: ReturnPathEvaluationEngine | None = None) -> None:
        self._engine = engine or ReturnPathEvaluationEngine()

    def evaluate(self, state, constraint_set, candidate, plan=None) -> PCBPreventEvaluation:
        from embedded_copilot.engineering_pcb.foundation import PCBConstraintCompiler
        _, _, _, VerificationStatus = _verification_types()

        checked_candidate = PCBRouteCandidate.model_validate(candidate.model_dump(mode="python"))
        if checked_candidate.base_state_fingerprint != state.fingerprint:
            raise ValueError("RETURN_PATH Prevent candidate is stale")
        checked_plan = plan or PCBConstraintCompiler().build_plan(state, constraint_set)
        detailed = self._engine.evaluate(state, constraint_set, checked_plan, candidate=checked_candidate)
        return_constraints = tuple(item for item in constraint_set.constraints if item.constraint_type.value == "RETURN_PATH")
        constraint = return_constraints[0] if return_constraints else None
        if detailed.result.status in {VerificationStatus.PASS, VerificationStatus.NOT_APPLICABLE}:
            status = PCBPreventStatus.ALLOW
        elif detailed.result.status is VerificationStatus.FAIL:
            status = PCBPreventStatus.BLOCK if constraint is not None and constraint.enforcement_mode is PCBConstraintEnforcementMode.HARD else PCBPreventStatus.WARN
        elif detailed.result.status is VerificationStatus.WARNING:
            status = PCBPreventStatus.WARN
        else:
            status = PCBPreventStatus.UNKNOWN
        return PCBPreventEvaluation.create(
            project_id=state.project_id,
            constraint_id="missing" if constraint is None else constraint.constraint_id,
            candidate_id=checked_candidate.candidate_id,
            state_fingerprint=state.fingerprint,
            constraint_set_fingerprint=constraint_set.fingerprint,
            affected_objects=detailed.result.affected_objects,
            status=status,
            reason=detailed.result.summary,
            evidence=detailed.result.evidence_refs,
            unknowns=detailed.result.unknowns,
            geometry_measurements=detailed.measurements,
        )


def mark_return_path_stale(result, *, reason: str, affected_objects: tuple[str, ...]) -> Any:
    _, _, VerificationResult, VerificationStatus = _verification_types()

    values = result.model_dump(mode="python", exclude={"fingerprint"})
    values.update(
        status=VerificationStatus.UNKNOWN,
        stale=True,
        stale_reason=reason,
        unknowns=tuple(dict.fromkeys((*result.unknowns, "STALE_INPUTS"))),
        affected_objects=tuple(dict.fromkeys((*result.affected_objects, *affected_objects))),
        required_reverification=tuple(dict.fromkeys((*result.required_reverification, "reverify_return_path"))),
        summary="The previous RETURN_PATH verification is stale and must be rerun.",
    )
    return VerificationResult.create(**values)


def _measurement(segment, relation, geometry_ids, status, quality, covered=None, uncovered=None, ratio=None):
    return ReferenceCoverageMeasurement.create(
        measurement_id=_stable_id("return-measurement", relation.relationship_id, segment.segment_id),
        route_segment_id=segment.segment_id,
        signal_layer=segment.layer,
        reference_layer=relation.expected_reference_layer_id,
        reference_net=relation.expected_reference_net_id,
        checked_geometry=tuple(geometry_ids),
        coverage_status=status,
        covered_length=covered,
        uncovered_length=uncovered,
        coverage_ratio=ratio,
        geometry_quality=quality,
    )


def _issue(issue_type, category, location, affected, evidence, problem, effects, recommendation):
    return issue_type.create(
        issue_id=_stable_id("issue", category, location),
        severity="P2",
        category=category,
        location=location,
        affected_objects=tuple(affected),
        problem=problem,
        why="The shared RETURN_PATH constraint and deterministic geometry establish this condition.",
        possible_effects=tuple(effects),
        evidence=tuple(evidence),
        recommendation=recommendation,
        reverification=("ReturnPathVerifier",),
    )


def _evidence_objects(state, constraint_set, relationships):
    values = {item.evidence_id: item for item in (*state.evidence_refs, *constraint_set.evidence_refs)}
    for relation in relationships:
        values.update({item.evidence_id: item for item in relation.evidence_refs})
    for plane in state.planes:
        for item in plane.evidence_refs:
            values[item.evidence_id] = item
    for keepout in state.keepouts:
        for item in keepout.evidence_refs:
            values[item.evidence_id] = item
    return tuple(values[key] for key in sorted(values))


def _length(segment):
    return math.hypot(segment.end[0] - segment.start[0], segment.end[1] - segment.start[1])


def _point_at(segment, t):
    return (
        segment.start[0] + (segment.end[0] - segment.start[0]) * t,
        segment.start[1] + (segment.end[1] - segment.start[1]) * t,
    )


def _point_in_ring(point, ring):
    x, y = point
    inside = False
    for index, (x1, y1) in enumerate(ring):
        x2, y2 = ring[(index + 1) % len(ring)]
        if (y1 > y) != (y2 > y):
            crossing = (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-30) + x1
            if x < crossing:
                inside = not inside
    return inside


def _polygon_contains(point, polygon):
    return _point_in_ring(point, polygon.points) and not any(_point_in_ring(point, hole) for hole in polygon.holes)


def _polygon_intervals(segment, polygon):
    values = [0.0, 1.0]
    for ring in (polygon.points, *polygon.holes):
        for index, first in enumerate(ring):
            second = ring[(index + 1) % len(ring)]
            values.extend(_edge_intersections(segment.start, segment.end, first, second))
    unique = sorted({max(0.0, min(1.0, round(item, 12))) for item in values})
    return tuple(
        (start, end)
        for start, end in pairwise(unique)
        if end > start and _polygon_contains(_point_at(segment, (start + end) / 2.0), polygon)
    )


def _edge_intersections(start, end, edge_start, edge_end):
    rx, ry = end[0] - start[0], end[1] - start[1]
    sx, sy = edge_end[0] - edge_start[0], edge_end[1] - edge_start[1]
    denominator = rx * sy - ry * sx
    qx, qy = edge_start[0] - start[0], edge_start[1] - start[1]
    if abs(denominator) < 1e-12:
        if abs(qx * ry - qy * rx) > 1e-12:
            return ()
        length_squared = rx * rx + ry * ry
        if length_squared == 0:
            return (0.0,)
        return ((qx * rx + qy * ry) / length_squared, ((edge_end[0] - start[0]) * rx + (edge_end[1] - start[1]) * ry) / length_squared)
    t = (qx * sy - qy * sx) / denominator
    u = (qx * ry - qy * rx) / denominator
    return (t,) if -1e-12 <= t <= 1 + 1e-12 and -1e-12 <= u <= 1 + 1e-12 else ()


def _union_intervals(intervals):
    ordered = sorted(intervals)
    result: list[tuple[float, float]] = []
    for start, end in ordered:
        if result and start <= result[-1][1] + 1e-12:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return tuple(result)


def _first_uncovered_t(segment, covered_intervals):
    cursor = 0.0
    for start, end in covered_intervals:
        if start > cursor + 1e-12:
            return (cursor + start) / 2.0
        cursor = max(cursor, end)
    return min(1.0, (cursor + 1.0) / 2.0)


def _route_intersects_planes(segment, planes):
    return any(_polygon_intervals(segment, polygon) for plane in planes for polygon in plane.polygons)


def _route_intersects_keepout(segment, keepouts, reference_layers):
    return any(
        _polygon_intervals(segment, polygon)
        for keepout in keepouts
        for polygon in keepout.polygons
        if keepout.layer in reference_layers
    )


def _discontinuity_type(state, relation, segment, point):
    if any(
        keepout.layer in relation.expected_reference_layers and any(_polygon_contains(point, polygon) for polygon in keepout.polygons)
        for keepout in state.keepouts
    ):
        return PCBReferenceDiscontinuityType.KEEPOUT
    for plane in state.planes:
        if plane.layer in relation.expected_reference_layers and plane.discontinuity_type is not None:
            if plane.discontinuity_type is PCBReferenceDiscontinuityType.VOID:
                return PCBReferenceDiscontinuityType.VOID
            if plane.discontinuity_type is PCBReferenceDiscontinuityType.SPLIT:
                return PCBReferenceDiscontinuityType.SPLIT
    return PCBReferenceDiscontinuityType.REFERENCE_DISCONTINUITY


def _quality(values):
    if not values:
        return PCBGeometryQuality.UNAVAILABLE
    if PCBGeometryQuality.APPROXIMATE in values:
        return PCBGeometryQuality.APPROXIMATE
    if PCBGeometryQuality.UNAVAILABLE in values:
        return PCBGeometryQuality.UNAVAILABLE
    return PCBGeometryQuality.EXACT


def _worst(statuses, status_type):
    if status_type.FAIL in statuses:
        return status_type.FAIL
    if status_type.WARNING in statuses:
        return status_type.WARNING
    if status_type.UNKNOWN in statuses:
        return status_type.UNKNOWN
    return status_type.PASS


__all__ = (
    "LayerTransitionReturnAssessment",
    "LayerTransitionReturnStatus",
    "PCBRouteCandidate",
    "ReferenceCoverageMeasurement",
    "ReferenceDiscontinuity",
    "ReturnPathCoverageStatus",
    "ReturnPathEvaluation",
    "ReturnPathEvaluationEngine",
    "ReturnPathPreventEvaluator",
    "ReturnPathRelationship",
    "ReturnPathRelationshipResolver",
    "ReturnPathVerificationScope",
    "ReturnPathVerifier",
    "mark_return_path_stale",
)
