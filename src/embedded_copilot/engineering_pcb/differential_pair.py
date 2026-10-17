from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .decoupling import PCBPreventEvaluation, PCBPreventStatus, _verification_types
from .foundation import (
    PCBApplicability,
    PCBConstraint,
    PCBConstraintEnforcementMode,
    PCBConstraintSet,
    PCBDifferentialPairRuleMetric,
    PCBEngineeringState,
    PCBFoundationEvidence,
    PCBRoutePathProjection,
    PCBRouteSegmentProjection,
    PCBVerificationPlan,
    PCBViaProjection,
)
from .models import PCBContract, deterministic_id


class DifferentialPairRouteScope(StrEnum):
    FULL_PATH = "FULL_PATH"
    SEGMENT_SCOPE = "SEGMENT_SCOPE"
    PARTIAL_PATH = "PARTIAL_PATH"
    UNKNOWN = "UNKNOWN"


class DifferentialPairMeasurementQuality(StrEnum):
    EXACT = "EXACT"
    APPROXIMATE = "APPROXIMATE"
    UNAVAILABLE = "UNAVAILABLE"


class DifferentialPairMeasurementMethod(StrEnum):
    PLANAR_ROUTE_LENGTH = "PLANAR_ROUTE_LENGTH"
    PLANAR_ROUTE_LENGTH_ONLY = "PLANAR_ROUTE_LENGTH_ONLY"
    TRACE_EDGE_TO_EDGE = "TRACE_EDGE_TO_EDGE"
    CENTERLINE_TO_CENTERLINE = "CENTERLINE_TO_CENTERLINE"
    NOT_MEASURED = "NOT_MEASURED"


class DifferentialPairTransitionSymmetry(StrEnum):
    MATCHED = "MATCHED"
    ASYMMETRIC = "ASYMMETRIC"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


DifferentialPairRuleMetric = PCBDifferentialPairRuleMetric


class DifferentialRouteCandidate(PCBContract):
    candidate_id: str
    base_state_fingerprint: str
    pair_id: str
    constraint_id: str
    positive_route_segments: tuple[PCBRouteSegmentProjection, ...]
    negative_route_segments: tuple[PCBRouteSegmentProjection, ...]
    positive_route_path: PCBRoutePathProjection | None = None
    negative_route_path: PCBRoutePathProjection | None = None
    vias: tuple[PCBViaProjection, ...] = ()

    @classmethod
    def create(cls, **values: object) -> DifferentialRouteCandidate:
        return cls.model_validate(values)


DifferentialPairRouteCandidate = DifferentialRouteCandidate


class DifferentialPairRelationship(PCBContract):
    pair_id: str
    interface_id: str | None
    positive_net_id: str
    negative_net_id: str
    positive_signal_id: str | None
    negative_signal_id: str | None
    positive_route_path_id: str | None
    negative_route_path_id: str | None
    positive_route_segment_ids: tuple[str, ...]
    negative_route_segment_ids: tuple[str, ...]
    constraint_id: str
    expected_reference_dependency: str | None
    evidence_refs: tuple[PCBFoundationEvidence, ...]
    confidence: float
    unknowns: tuple[str, ...] = ()

    @classmethod
    def create(cls, **values: object) -> DifferentialPairRelationship:
        return cls.model_validate(values)


class DifferentialPairMeasurement(PCBContract):
    measurement_id: str
    pair_id: str
    positive_length_mm: float | None
    negative_length_mm: float | None
    length_difference_mm: float | None
    length_method: DifferentialPairMeasurementMethod
    spacing_method: DifferentialPairMeasurementMethod
    minimum_spacing_mm: float | None
    maximum_spacing_mm: float | None
    representative_spacing_mm: float | None
    paired_length_mm: float | None
    unpaired_length_mm: float | None
    positive_via_count: int
    negative_via_count: int
    positive_layers: tuple[str, ...]
    negative_layers: tuple[str, ...]
    positive_transition_sequence: tuple[str, ...]
    negative_transition_sequence: tuple[str, ...]
    transition_symmetry: DifferentialPairTransitionSymmetry
    stub_count: int | None
    stub_ids: tuple[str, ...]
    stub_lengths_mm: tuple[float, ...]
    route_scope: DifferentialPairRouteScope
    measurement_quality: DifferentialPairMeasurementQuality
    via_vertical_length_status: str
    unknowns: tuple[str, ...] = ()

    @classmethod
    def create(cls, **values: object) -> DifferentialPairMeasurement:
        return cls.model_validate(values)


@dataclass(frozen=True)
class DifferentialPairEvaluation:
    result: Any
    relationships: tuple[DifferentialPairRelationship, ...]
    measurements: tuple[DifferentialPairMeasurement, ...]
    dependency_fingerprints: tuple[str, ...]


@dataclass(frozen=True)
class _RouteEvidence:
    segments: tuple[PCBRouteSegmentProjection, ...]
    path: PCBRoutePathProjection | None
    scope: DifferentialPairRouteScope
    unknowns: tuple[str, ...]
    topology_segments: tuple[PCBRouteSegmentProjection, ...] | None = None


def _unique(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _length(segment: PCBRouteSegmentProjection) -> float:
    return math.hypot(segment.end[0] - segment.start[0], segment.end[1] - segment.start[1])


def _orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> bool:
    return (
        min(a[0], b[0]) - 1e-9 <= c[0] <= max(a[0], b[0]) + 1e-9
        and min(a[1], b[1]) - 1e-9 <= c[1] <= max(a[1], b[1]) + 1e-9
    )


def _intersects(a, b, c, d) -> bool:
    values = (_orientation(a, b, c), _orientation(a, b, d), _orientation(c, d, a), _orientation(c, d, b))
    if values[0] == values[1] == values[2] == values[3] == 0:
        return _on_segment(a, b, c) or _on_segment(a, b, d) or _on_segment(c, d, a) or _on_segment(c, d, b)
    return (values[0] * values[1] <= 0 and values[2] * values[3] <= 0)


def _point_distance(point, a, b) -> float:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    denominator = dx * dx + dy * dy
    if denominator == 0:
        return math.hypot(point[0] - a[0], point[1] - a[1])
    t = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / denominator))
    return math.hypot(point[0] - (a[0] + t * dx), point[1] - (a[1] + t * dy))


def _centerline_distance(left: PCBRouteSegmentProjection, right: PCBRouteSegmentProjection) -> float:
    if _intersects(left.start, left.end, right.start, right.end):
        return 0.0
    return min(
        _point_distance(left.start, right.start, right.end),
        _point_distance(left.end, right.start, right.end),
        _point_distance(right.start, left.start, left.end),
        _point_distance(right.end, left.start, left.end),
    )


def _endpoint(point: tuple[float, float]) -> tuple[float, float]:
    return (round(point[0], 9), round(point[1], 9))


def _transition_sequence(segments: tuple[PCBRouteSegmentProjection, ...], vias: dict[str, PCBViaProjection]) -> tuple[str, ...]:
    result: list[str] = []
    for segment in segments:
        for via_id in segment.via_ids:
            via = vias.get(via_id)
            if via is not None:
                result.append(f"{via.from_layer}>{via.to_layer}")
    return tuple(result)


def _stub_info(route: _RouteEvidence) -> tuple[int | None, tuple[str, ...], tuple[float, ...], tuple[str, ...]]:
    if route.scope is not DifferentialPairRouteScope.FULL_PATH or route.path is None:
        return None, (), (), ("STUB_ANALYSIS_UNKNOWN",)
    topology_segments = route.topology_segments or route.segments
    segments = {item.segment_id: item for item in topology_segments}
    adjacency: dict[tuple[float, float], list[str]] = {}
    for segment in topology_segments:
        adjacency.setdefault(_endpoint(segment.start), []).append(segment.segment_id)
        adjacency.setdefault(_endpoint(segment.end), []).append(segment.segment_id)
    path_ids = route.path.segment_ids
    path_neighbors: set[str] = set()
    for index, segment_id in enumerate(path_ids):
        if index:
            path_neighbors.add(path_ids[index - 1])
        if index + 1 < len(path_ids):
            path_neighbors.add(path_ids[index + 1])
    stubs: list[str] = []
    for node_segments in adjacency.values():
        if len(node_segments) <= 2:
            continue
        for segment_id in node_segments:
            if segment_id not in path_neighbors and segment_id not in stubs:
                stubs.append(segment_id)
    stubs = sorted(stubs)
    return len(stubs), tuple(stubs), tuple(_length(segments[item]) for item in stubs), ()


def _constraint(constraint_set: PCBConstraintSet) -> PCBConstraint | None:
    values = tuple(item for item in constraint_set.constraints if item.constraint_type.value == "DIFFERENTIAL_PAIR")
    return values[0] if len(values) == 1 else None


def _dependency_status(value: Any) -> str:
    raw = getattr(value, "status", None)
    return getattr(raw, "value", str(raw))


class DifferentialPairRelationshipResolver:
    def resolve(
        self,
        state: PCBEngineeringState,
        constraint_set: PCBConstraintSet,
        *,
        candidate: DifferentialRouteCandidate | None = None,
    ) -> tuple[tuple[DifferentialPairRelationship, ...], tuple[_RouteEvidence, ...], tuple[str, ...]]:
        constraint = _constraint(constraint_set)
        if constraint is None:
            return (), (), ("DIFFERENTIAL_PAIR_CONSTRAINT_MISSING_OR_AMBIGUOUS",)
        bindings = {item.pair_id: item for item in constraint.differential_pair_bindings}
        projections = tuple(state.differential_pairs)
        if candidate is not None:
            if candidate.pair_id not in {item.pair_id for item in projections}:
                return (), (), ("CANDIDATE_PAIR_ID_UNKNOWN",)
            projections = tuple(item for item in projections if item.pair_id == candidate.pair_id)
        relationships: list[DifferentialPairRelationship] = []
        routes: list[_RouteEvidence] = []
        all_unknowns: list[str] = []
        for pair in sorted(projections, key=lambda item: item.pair_id):
            binding = bindings.get(pair.pair_id)
            unknowns: list[str] = []
            if binding is None:
                unknowns.append("PAIR_CONSTRAINT_BINDING_MISSING")
            if not pair.evidence_refs:
                unknowns.append("PAIR_IDENTITY_EVIDENCE_UNKNOWN")
            if binding is not None and (
                pair.positive_net != binding.positive_net_id
                or pair.negative_net != binding.negative_net_id
                or pair.interface_id != binding.interface_id
            ):
                unknowns.append("PAIR_IDENTITY_CONFLICT")
            known_nets = {item.net_id for item in state.nets}
            if pair.positive_net not in known_nets or pair.negative_net not in known_nets:
                unknowns.append("PAIR_NET_IDENTITY_UNKNOWN")
            interfaces = {item.interface_id: item for item in state.interfaces}
            if pair.interface_id is not None:
                interface = interfaces.get(pair.interface_id)
                if interface is None or not {pair.positive_net, pair.negative_net}.issubset(set(interface.net_ids)):
                    unknowns.append("PAIR_INTERFACE_BINDING_CONFLICT")
            signal_ids = {item.net_id: item.signal_id for item in state.nets}
            positive_net = pair.positive_net if binding is None else binding.positive_net_id
            negative_net = pair.negative_net if binding is None else binding.negative_net_id
            positive_route = self._route(state, positive_net, candidate, positive=True)
            negative_route = self._route(state, negative_net, candidate, positive=False)
            routes.extend((positive_route, negative_route))
            unknowns.extend(positive_route.unknowns)
            unknowns.extend(negative_route.unknowns)
            evidence = tuple(dict.fromkeys((*pair.evidence_refs, *(() if binding is None else binding.evidence_refs), *constraint.evidence_refs)))
            relationship = DifferentialPairRelationship.create(
                pair_id=pair.pair_id,
                interface_id=pair.interface_id if binding is None else binding.interface_id,
                positive_net_id=pair.positive_net,
                negative_net_id=pair.negative_net,
                positive_signal_id=signal_ids.get(pair.positive_net),
                negative_signal_id=signal_ids.get(pair.negative_net),
                positive_route_path_id=None if positive_route.path is None else positive_route.path.route_id,
                negative_route_path_id=None if negative_route.path is None else negative_route.path.route_id,
                positive_route_segment_ids=tuple(item.segment_id for item in positive_route.segments),
                negative_route_segment_ids=tuple(item.segment_id for item in negative_route.segments),
                constraint_id=constraint.constraint_id,
                expected_reference_dependency=constraint.differential_reference_requirement,
                evidence_refs=evidence,
                confidence=1.0 if binding is not None and pair.evidence_refs else 0.5,
                unknowns=_unique(unknowns),
            )
            relationships.append(relationship)
            all_unknowns.extend(unknowns)
        if not projections:
            all_unknowns.append("DIFFERENTIAL_PAIR_RELATION_MISSING")
        return tuple(relationships), tuple(routes), _unique(all_unknowns)

    @staticmethod
    def _route(state, net_id: str, candidate: DifferentialRouteCandidate | None, *, positive: bool) -> _RouteEvidence:
        if candidate is not None:
            candidate_segments = candidate.positive_route_segments if positive else candidate.negative_route_segments
            path = candidate.positive_route_path if positive else candidate.negative_route_path
            candidate_segments = tuple(item for item in candidate_segments if item.net_id == net_id)
            if path is None:
                return _RouteEvidence(candidate_segments, None, DifferentialPairRouteScope.SEGMENT_SCOPE if candidate_segments else DifferentialPairRouteScope.UNKNOWN, () if candidate_segments else ("ROUTE_PATH_UNKNOWN",), candidate_segments)
            segments = tuple(item for item in candidate_segments if item.segment_id in set(path.segment_ids))
            expected = set(path.segment_ids)
            actual = {item.segment_id for item in segments}
            if path.net_id != net_id:
                return _RouteEvidence(segments, path, DifferentialPairRouteScope.UNKNOWN, ("ROUTE_PATH_NET_IDENTITY_UNKNOWN",), candidate_segments)
            if expected and expected == actual and len(path.segment_ids) == len(segments):
                by_id = {item.segment_id: item for item in segments}
                disconnected = any(
                    not {
                        _endpoint(left.start),
                        _endpoint(left.end),
                    }.intersection(
                        {_endpoint(right.start), _endpoint(right.end)}
                    )
                    for left_id, right_id in zip(path.segment_ids, path.segment_ids[1:])
                    for left, right in ((by_id[left_id], by_id[right_id]),)
                )
                if not disconnected:
                    return _RouteEvidence(segments, path, DifferentialPairRouteScope.FULL_PATH, (), candidate_segments)
                return _RouteEvidence(segments, path, DifferentialPairRouteScope.PARTIAL_PATH, ("PAIR_ROUTE_DISCONNECTED",), candidate_segments)
            return _RouteEvidence(segments, path, DifferentialPairRouteScope.PARTIAL_PATH, ("PAIR_ROUTE_INCOMPLETE",), candidate_segments)
        paths = tuple(item for item in state.route_paths if item.net_id == net_id)
        if len(paths) == 1:
            path = paths[0]
            by_id = {item.segment_id: item for item in state.routing}
            segments = tuple(by_id[item] for item in path.segment_ids if item in by_id)
            topology_segments = tuple(item for item in state.routing if item.net_id == net_id)
            if not segments:
                return _RouteEvidence((), path, DifferentialPairRouteScope.UNKNOWN, ("ROUTE_PATH_UNKNOWN",), topology_segments)
            if len(segments) != len(path.segment_ids):
                return _RouteEvidence(segments, path, DifferentialPairRouteScope.PARTIAL_PATH, ("PAIR_ROUTE_INCOMPLETE",), topology_segments)
            return _RouteEvidence(segments, path, DifferentialPairRouteScope.FULL_PATH, (), topology_segments)
        segments = tuple(item for item in state.routing if item.net_id == net_id)
        if len(paths) > 1:
            return _RouteEvidence(segments, None, DifferentialPairRouteScope.PARTIAL_PATH, ("ROUTE_PATH_IDENTITY_AMBIGUOUS",))
        return _RouteEvidence(segments, None, DifferentialPairRouteScope.SEGMENT_SCOPE if segments else DifferentialPairRouteScope.UNKNOWN, () if segments else ("ROUTE_PATH_UNKNOWN",))


class DifferentialPairEvaluationEngine:
    def evaluate(
        self,
        state: PCBEngineeringState,
        constraint_set: PCBConstraintSet,
        plan: PCBVerificationPlan,
        *,
        candidate: DifferentialRouteCandidate | None = None,
        return_path_result: Any | None = None,
    ) -> DifferentialPairEvaluation:
        checked_state = PCBEngineeringState.model_validate(state.model_dump(mode="python"))
        checked_set = PCBConstraintSet.model_validate(constraint_set.model_dump(mode="python"))
        checked_plan = PCBVerificationPlan.model_validate(plan.model_dump(mode="python"))
        constraint = _constraint(checked_set)
        compared = [checked_state.fingerprint, checked_set.fingerprint, checked_plan.fingerprint, checked_state.source_artifact_fingerprint]
        if return_path_result is not None:
            compared.append(return_path_result.fingerprint)
        if candidate is not None:
            checked_candidate = DifferentialRouteCandidate.model_validate(candidate.model_dump(mode="python"))
            if checked_candidate.base_state_fingerprint != checked_state.fingerprint:
                raise ValueError("DIFFERENTIAL_PAIR Prevent candidate is stale")
            compared.append(checked_candidate.fingerprint)
        else:
            checked_candidate = None
        if checked_candidate is not None and constraint is not None and checked_candidate.constraint_id != constraint.constraint_id:
            return self._finish(
                checked_state,
                checked_set,
                checked_plan,
                PCBApplicability.UNKNOWN,
                (),
                (),
                (),
                "The candidate is bound to a different differential-pair constraint.",
                ("CANDIDATE_CONSTRAINT_MISMATCH",),
                tuple(compared),
                return_path_result,
            )
        relationships, route_evidence, relation_unknowns = DifferentialPairRelationshipResolver().resolve(
            checked_state,
            checked_set,
            candidate=checked_candidate,
        )
        applicability = PCBApplicability.NOT_APPLICABLE
        if constraint is not None:
            applicability = constraint.applicability
        if not checked_state.differential_pairs and applicability is not PCBApplicability.REQUIRED:
            if checked_candidate is not None:
                return self._finish(
                    checked_state,
                    checked_set,
                    checked_plan,
                    PCBApplicability.UNKNOWN,
                    (),
                    (),
                    (),
                    "The candidate has no evidence-bound differential-pair authority.",
                    ("CANDIDATE_PAIR_ID_UNKNOWN",),
                    tuple(compared),
                    return_path_result,
                )
            return self._finish(
                checked_state,
                checked_set,
                checked_plan,
                applicability,
                (),
                (),
                (),
                "No evidence-bound differential pair is applicable to this PCB.",
                (),
                tuple(compared),
                return_path_result,
            )
        if checked_state.status.value == "STALE" or checked_set.status.value == "STALE" or checked_plan.status.value == "STALE":
            return self._finish(
                checked_state,
                checked_set,
                checked_plan,
                PCBApplicability.UNKNOWN,
                relationships,
                (),
                (),
                "Differential-pair inputs are stale and must be reverified.",
                ("STALE_INPUTS",),
                tuple(compared),
                return_path_result,
            )
        if constraint is None:
            return self._finish(
                checked_state,
                checked_set,
                checked_plan,
                PCBApplicability.UNKNOWN,
                relationships,
                (),
                (),
                "The differential-pair constraint is missing or ambiguous.",
                ("DIFFERENTIAL_PAIR_CONSTRAINT_MISSING_OR_AMBIGUOUS",),
                tuple(compared),
                return_path_result,
            )
        if applicability is PCBApplicability.NOT_APPLICABLE:
            return self._finish(
                checked_state,
                checked_set,
                checked_plan,
                applicability,
                relationships,
                (),
                (),
                "The approved differential-pair applicability is NOT_APPLICABLE.",
                (),
                tuple(compared),
                return_path_result,
            )
        if applicability is PCBApplicability.UNKNOWN:
            return self._finish(
                checked_state,
                checked_set,
                checked_plan,
                applicability,
                relationships,
                (),
                (),
                "Differential-pair applicability is UNKNOWN.",
                ("DIFFERENTIAL_PAIR_APPLICABILITY_UNKNOWN",),
                tuple(compared),
                return_path_result,
            )
        measurements: list[DifferentialPairMeasurement] = []
        unknowns = list(relation_unknowns)
        issues: list[Any] = []
        for relationship, pair_routes in zip(relationships, _pair_route_groups(route_evidence)):
            measurement = self._measure(checked_state, relationship, pair_routes, checked_candidate)
            measurements.append(measurement)
            unknowns.extend(measurement.unknowns)
            if "PAIR_IDENTITY_CONFLICT" in relationship.unknowns:
                issues.append(self._issue(
                    constraint,
                    "DIFFERENTIAL_PAIR_IDENTITY_MISMATCH",
                    relationship.pair_id,
                    (relationship.positive_net_id, relationship.negative_net_id),
                    "The state pair mapping conflicts with the approved P/N binding.",
                    "Resolve the approved differential-pair identity and rerun verification.",
                    ("DIFFERENTIAL_PAIR_IDENTITY_MISMATCH",),
                ))
            pair_issues, pair_unknowns = self._rules(constraint, relationship, measurement)
            issues.extend(pair_issues)
            unknowns.extend(pair_unknowns)
        dependency_issues, dependency_unknowns = self._dependency(checked_state, constraint, return_path_result)
        issues.extend(dependency_issues)
        unknowns.extend(dependency_unknowns)
        if constraint.differential_impedance_ohms is not None and not any(item.metric.value == "IMPEDANCE" for item in constraint.differential_pair_rules):
            unknowns.append("IMPEDANCE_NOT_VERIFIED")
        status = "FAIL" if issues else "UNKNOWN" if unknowns else "PASS"
        return self._finish(
            checked_state,
            checked_set,
            checked_plan,
            applicability,
            relationships,
            tuple(measurements),
            tuple(issues),
            "Differential-pair geometry was evaluated against the evidence-bound constraint.",
            tuple(_unique(unknowns)),
            tuple(compared),
            return_path_result,
            forced_status=status,
        )

    def _measure(self, state, relationship, routes, candidate):
        positive, negative = routes
        positive_length = sum(_length(item) for item in positive.segments) if positive.segments else None
        negative_length = sum(_length(item) for item in negative.segments) if negative.segments else None
        length_difference = None if positive_length is None or negative_length is None else abs(positive_length - negative_length)
        spacings: list[float] = []
        paired_positive = 0.0
        paired_negative = 0.0
        for left in positive.segments:
            matches = [right for right in negative.segments if right.layer == left.layer]
            if matches:
                paired_positive += _length(left)
                for right in matches:
                    spacings.append(max(0.0, _centerline_distance(left, right) - (left.width_mm + right.width_mm) / 2.0))
        for right in negative.segments:
            if any(left.layer == right.layer for left in positive.segments):
                paired_negative += _length(right)
        vias = {item.via_id: item for item in state.vias}
        if candidate is not None:
            vias.update({item.via_id: item for item in candidate.vias})
        positive_via_ids = tuple(dict.fromkeys(item for segment in positive.segments for item in segment.via_ids))
        negative_via_ids = tuple(dict.fromkeys(item for segment in negative.segments for item in segment.via_ids))
        transition_positive = _transition_sequence(positive.segments, vias)
        transition_negative = _transition_sequence(negative.segments, vias)
        transition_symmetry = (
            DifferentialPairTransitionSymmetry.NOT_APPLICABLE
            if not transition_positive and not transition_negative
            else DifferentialPairTransitionSymmetry.UNKNOWN
            if len(transition_positive) != len(positive_via_ids) or len(transition_negative) != len(negative_via_ids)
            else DifferentialPairTransitionSymmetry.MATCHED
            if transition_positive == transition_negative
            else DifferentialPairTransitionSymmetry.ASYMMETRIC
        )
        positive_stubs = _stub_info(positive)
        negative_stubs = _stub_info(negative)
        stub_unknowns = (*positive_stubs[3], *negative_stubs[3])
        stub_ids = tuple(dict.fromkeys((*positive_stubs[1], *negative_stubs[1])))
        stub_lengths = (*positive_stubs[2], *negative_stubs[2])
        route_scope = positive.scope if positive.scope == negative.scope else DifferentialPairRouteScope.PARTIAL_PATH
        unknowns = tuple(dict.fromkeys((*positive.unknowns, *negative.unknowns, *stub_unknowns)))
        return DifferentialPairMeasurement.create(
            measurement_id=deterministic_id("differential-measurement", relationship.pair_id, *relationship.positive_route_segment_ids, *relationship.negative_route_segment_ids),
            pair_id=relationship.pair_id,
            positive_length_mm=positive_length,
            negative_length_mm=negative_length,
            length_difference_mm=length_difference,
            length_method=(
                DifferentialPairMeasurementMethod.PLANAR_ROUTE_LENGTH_ONLY
                if positive_via_ids or negative_via_ids
                else DifferentialPairMeasurementMethod.PLANAR_ROUTE_LENGTH
            ),
            spacing_method=DifferentialPairMeasurementMethod.TRACE_EDGE_TO_EDGE if spacings else DifferentialPairMeasurementMethod.NOT_MEASURED,
            minimum_spacing_mm=min(spacings) if spacings else None,
            maximum_spacing_mm=max(spacings) if spacings else None,
            representative_spacing_mm=sum(spacings) / len(spacings) if spacings else None,
            paired_length_mm=min(paired_positive, paired_negative) if spacings else None,
            unpaired_length_mm=None if positive_length is None or negative_length is None else max(positive_length, negative_length) - min(paired_positive, paired_negative),
            positive_via_count=len(positive_via_ids),
            negative_via_count=len(negative_via_ids),
            positive_layers=tuple(dict.fromkeys(item.layer for item in positive.segments)),
            negative_layers=tuple(dict.fromkeys(item.layer for item in negative.segments)),
            positive_transition_sequence=transition_positive,
            negative_transition_sequence=transition_negative,
            transition_symmetry=transition_symmetry,
            stub_count=None if positive_stubs[0] is None or negative_stubs[0] is None else positive_stubs[0] + negative_stubs[0],
            stub_ids=stub_ids,
            stub_lengths_mm=tuple(stub_lengths),
            route_scope=route_scope,
            measurement_quality=DifferentialPairMeasurementQuality.EXACT if positive_length is not None and negative_length is not None else DifferentialPairMeasurementQuality.UNAVAILABLE,
            via_vertical_length_status="UNKNOWN" if positive_via_ids or negative_via_ids else "NOT_REQUIRED",
            unknowns=unknowns,
        )

    def _rules(self, constraint, relationship, measurement):
        issues: list[Any] = []
        unknowns: list[str] = []
        required = set(constraint.requirements)
        rules = {item.metric.value: item for item in constraint.differential_pair_rules}
        if required & {"length_matching", "electrical_length_matching"} and not any(item in rules for item in ("LENGTH_DIFFERENCE", "ELECTRICAL_LENGTH_DIFFERENCE")):
            unknowns.append("MISSING_MACHINE_EVALUABLE_RULE")
        if "pair_spacing" in required and not any(item in rules for item in ("PAIR_SPACING_MIN", "PAIR_SPACING_MAX")):
            unknowns.append("MISSING_MACHINE_EVALUABLE_RULE")
        if "matched_transitions" in required and "MATCHED_TRANSITIONS" not in rules:
            unknowns.append("MISSING_MACHINE_EVALUABLE_RULE")
        if "stub_limit" in required and "MAX_STUB_LENGTH" not in rules:
            unknowns.append("MISSING_MACHINE_EVALUABLE_RULE")
        for rule in constraint.differential_pair_rules:
            metric = rule.metric.value
            value: float | None = None
            violation = False
            if metric in {"LENGTH_DIFFERENCE", "ELECTRICAL_LENGTH_DIFFERENCE"}:
                if measurement.route_scope is not DifferentialPairRouteScope.FULL_PATH or measurement.length_difference_mm is None:
                    unknowns.append("PAIR_ROUTE_INCOMPLETE")
                    continue
                if metric == "ELECTRICAL_LENGTH_DIFFERENCE" and measurement.via_vertical_length_status != "AVAILABLE":
                    unknowns.append("VIA_VERTICAL_LENGTH_UNKNOWN")
                    continue
                value = measurement.length_difference_mm
            elif metric == "PAIR_SPACING_MIN":
                value = measurement.minimum_spacing_mm
                if value is None:
                    unknowns.append("PAIR_SPACING_MEASUREMENT_UNKNOWN")
                    continue
            elif metric == "PAIR_SPACING_MAX":
                value = measurement.maximum_spacing_mm
                if value is None:
                    unknowns.append("PAIR_SPACING_MEASUREMENT_UNKNOWN")
                    continue
            elif metric == "MATCHED_TRANSITIONS":
                if measurement.transition_symmetry is DifferentialPairTransitionSymmetry.UNKNOWN:
                    unknowns.append("VIA_TRANSITION_EVIDENCE_UNKNOWN")
                    continue
                violation = measurement.transition_symmetry is not DifferentialPairTransitionSymmetry.MATCHED
            elif metric == "VIA_COUNT_MATCH":
                value = abs(measurement.positive_via_count - measurement.negative_via_count)
            elif metric == "MAX_STUB_LENGTH":
                if measurement.stub_count is None:
                    unknowns.append("STUB_ANALYSIS_UNKNOWN")
                    continue
                value = max(measurement.stub_lengths_mm, default=0.0)
            elif metric == "IMPEDANCE":
                unknowns.append("IMPEDANCE_NOT_VERIFIED")
                continue
            if value is not None and rule.limit is not None:
                violation = rule.comparison == "LE" and value > rule.limit or rule.comparison == "GE" and value < rule.limit or rule.comparison == "EQ" and not math.isclose(value, rule.limit, rel_tol=0.0, abs_tol=1e-9)
            if violation:
                category = {
                    "LENGTH_DIFFERENCE": "LENGTH_MATCH_RULE_VIOLATION",
                    "ELECTRICAL_LENGTH_DIFFERENCE": "LENGTH_MATCH_RULE_VIOLATION",
                    "PAIR_SPACING_MIN": "PAIR_SPACING_RULE_VIOLATION",
                    "PAIR_SPACING_MAX": "PAIR_SPACING_RULE_VIOLATION",
                    "MATCHED_TRANSITIONS": "LAYER_TRANSITION_ASYMMETRY",
                    "VIA_COUNT_MATCH": "VIA_TRANSITION_RULE_VIOLATION",
                    "MAX_STUB_LENGTH": "STUB_RULE_VIOLATION",
                }.get(metric, "DIFFERENTIAL_PAIR_RULE_VIOLATION")
                issues.append(self._issue(constraint, category, rule.rule_id, (relationship.pair_id,), f"Measured differential-pair value violates the approved {metric} rule.", "Revise the differential route under human review and rerun verification.", (category,)))
        return issues, tuple(unknowns)

    def _dependency(self, state, constraint, dependency):
        if constraint.differential_reference_requirement is None:
            return (), ()
        if dependency is None:
            return (), ("REFERENCE_DEPENDENCY_UNKNOWN",)
        compared = set(getattr(dependency, "compared_fingerprints", ()))
        if (
            getattr(dependency, "project_id", None) != state.project_id
            or getattr(dependency, "verifier", None) != "RETURN_PATH"
            or getattr(dependency, "scope", None) != "PCB"
            or state.fingerprint not in compared
        ):
            return (), ("REFERENCE_DEPENDENCY_IDENTITY_UNKNOWN",)
        status = _dependency_status(dependency)
        if status == "FAIL":
            return (self._issue(constraint, "REFERENCE_DEPENDENCY_FAILED", constraint.constraint_id, (), "The required ReturnPath verification failed.", "Repair the approved reference path and rerun the dependency verifier.", ("REFERENCE_DEPENDENCY_FAILED",)),), ()
        if status != "PASS":
            return (), ("REFERENCE_DEPENDENCY_UNKNOWN",)
        return (), ()

    @staticmethod
    def _issue(constraint, category, location, affected, problem, recommendation, effects):
        _, VerificationIssue, _, _ = _verification_types()
        severity = "ERROR" if constraint.enforcement_mode is PCBConstraintEnforcementMode.HARD else "WARNING"
        return VerificationIssue.create(
            issue_id=deterministic_id("differential-issue", category, location, *affected),
            severity=severity,
            category=category,
            location=location,
            affected_objects=tuple(affected),
            problem=problem,
            why="The evidence-bound differential-pair constraint is not satisfied or cannot be evaluated.",
            possible_effects=tuple(effects),
            evidence=tuple(item.evidence_id for item in constraint.evidence_refs),
            recommendation=recommendation,
            reverification=("DifferentialPairVerifier",),
        )

    def _finish(
        self,
        state,
        constraint_set,
        plan,
        applicability,
        relationships,
        measurements,
        issues,
        summary,
        result_unknowns,
        result_compared,
        dependency,
        *,
        forced_status=None,
    ):
        EvidenceReference, _, VerificationResult, VerificationStatus = _verification_types()
        # Build the stable evidence set explicitly to keep issue references bounded.
        evidence_source: dict[str, PCBFoundationEvidence] = {}
        differential_constraint = _constraint(constraint_set)
        rule_evidence = () if differential_constraint is None else tuple(
            item
            for rule in differential_constraint.differential_pair_rules
            for item in rule.evidence_refs
        )
        for source in (
            *state.evidence_refs,
            *constraint_set.evidence_refs,
            *rule_evidence,
            *(item for relation in relationships for item in relation.evidence_refs),
        ):
            evidence_source[source.evidence_id] = source
        evidence_objects = [
            EvidenceReference.create(
                evidence_id=item.evidence_id,
                source_kind=item.source_type.value,
                source_fingerprint=item.source_fingerprint,
                locator=f"evidence:{item.evidence_id}",
                description=item.claim,
            )
            for item in sorted(evidence_source.values(), key=lambda item: item.evidence_id)
        ]
        dependency_evidence = () if dependency is None else tuple(getattr(dependency, "evidence", ()))
        evidence_by_id = {item.evidence_id: item for item in evidence_objects}
        for item in dependency_evidence:
            evidence_by_id[item.evidence_id] = item
        evidence_objects = tuple(evidence_by_id.values())
        evidence_ids = tuple(item.evidence_id for item in evidence_objects)
        checked_issues = tuple(
            type(issue).create(**{**issue.model_dump(mode="python", exclude={"fingerprint"}), "evidence": tuple(item for item in issue.evidence if item in evidence_ids)})
            for issue in issues
        )
        status = VerificationStatus[forced_status] if forced_status else VerificationStatus.UNKNOWN if result_unknowns else VerificationStatus.NOT_APPLICABLE if applicability is PCBApplicability.NOT_APPLICABLE else VerificationStatus.PASS
        if checked_issues:
            status = VerificationStatus.FAIL
        comparison = tuple(dict.fromkeys(result_compared))
        result = VerificationResult.create(
            verification_id=deterministic_id("verification", state.project_id, state.fingerprint, constraint_set.fingerprint, plan.fingerprint, "DIFFERENTIAL_PAIR"),
            project_id=state.project_id,
            target_id=state.fingerprint,
            status=status,
            attempt=0,
            compared_fingerprints=comparison,
            evidence_refs=evidence_ids,
            summary=summary,
            verifier="DIFFERENTIAL_PAIR",
            scope="PCB",
            issues=checked_issues,
            evidence=evidence_objects,
            affected_objects=tuple(dict.fromkeys((
                *(item.pair_id for item in relationships),
                *(item.positive_net_id for item in relationships),
                *(item.negative_net_id for item in relationships),
                *(segment_id for item in relationships for segment_id in item.positive_route_segment_ids),
                *(segment_id for item in relationships for segment_id in item.negative_route_segment_ids),
            ))),
            confidence=1.0 if status in {VerificationStatus.PASS, VerificationStatus.FAIL, VerificationStatus.NOT_APPLICABLE} else 0.5,
            assumptions=("Pair identity is consumed only from explicit evidence-bound mappings; names and geometry are not promoted to truth.",),
            unknowns=tuple(dict.fromkeys(result_unknowns)),
            recommendations=tuple(dict.fromkeys(item.recommendation for item in checked_issues)),
            measurement_refs=tuple(item.measurement_id for item in measurements),
            revision=state.revision,
            applicability=applicability.value,
        )
        dependency_fingerprints = () if dependency is None else (dependency.fingerprint,)
        return DifferentialPairEvaluation(result, tuple(relationships), tuple(measurements), dependency_fingerprints)


def _pair_route_groups(routes: tuple[_RouteEvidence, ...]) -> tuple[tuple[_RouteEvidence, _RouteEvidence], ...]:
    return tuple((routes[index], routes[index + 1]) for index in range(0, len(routes), 2) if index + 1 < len(routes))


class DifferentialPairVerifier:
    def __init__(self, engine: DifferentialPairEvaluationEngine | None = None) -> None:
        self._engine = engine or DifferentialPairEvaluationEngine()

    def verify(self, state, constraint_set, plan, *, return_path_result=None):
        return self._engine.evaluate(state, constraint_set, plan, return_path_result=return_path_result).result

    def verify_detailed(self, state, constraint_set, plan, *, return_path_result=None):
        return self._engine.evaluate(state, constraint_set, plan, return_path_result=return_path_result)


class DifferentialPairPreventEvaluator:
    def __init__(self, engine: DifferentialPairEvaluationEngine | None = None) -> None:
        self._engine = engine or DifferentialPairEvaluationEngine()

    def evaluate(self, state, constraint_set, candidate, plan=None, *, return_path_result=None) -> PCBPreventEvaluation:
        from .foundation import PCBConstraintCompiler

        checked_candidate = DifferentialRouteCandidate.model_validate(candidate.model_dump(mode="python"))
        if checked_candidate.base_state_fingerprint != state.fingerprint:
            raise ValueError("DIFFERENTIAL_PAIR Prevent candidate is stale")
        checked_plan = plan or PCBConstraintCompiler().build_plan(state, constraint_set)
        detailed = self._engine.evaluate(state, constraint_set, checked_plan, candidate=checked_candidate, return_path_result=return_path_result)
        constraint = _constraint(constraint_set)
        status = PCBPreventStatus.UNKNOWN
        if detailed.result.status.value == "PASS":
            status = PCBPreventStatus.ALLOW
        elif detailed.result.status.value == "NOT_APPLICABLE":
            status = PCBPreventStatus.ALLOW if checked_candidate is None else PCBPreventStatus.UNKNOWN
        elif detailed.result.status.value == "FAIL":
            status = PCBPreventStatus.BLOCK if constraint is not None and constraint.enforcement_mode is PCBConstraintEnforcementMode.HARD else PCBPreventStatus.WARN
        elif detailed.result.status.value == "WARNING":
            status = PCBPreventStatus.WARN
        return PCBPreventEvaluation.create(
            project_id=state.project_id,
            constraint_id="missing" if constraint is None else constraint.constraint_id,
            candidate_id=checked_candidate.candidate_id,
            state_fingerprint=state.fingerprint,
            constraint_set_fingerprint=constraint_set.fingerprint,
            affected_objects=detailed.result.affected_objects,
            geometry_measurements=detailed.measurements,
            status=status,
            reason=detailed.result.summary,
            evidence=detailed.result.evidence_refs,
            unknowns=detailed.result.unknowns,
        )


def mark_differential_pair_stale(result, *, reason: str, affected_objects: tuple[str, ...]) -> Any:
    _, _, VerificationResult, VerificationStatus = _verification_types()
    values = result.model_dump(mode="python", exclude={"fingerprint"})
    values.update(
        status=VerificationStatus.UNKNOWN,
        applicability=PCBApplicability.UNKNOWN.value,
        stale=True,
        stale_reason=reason,
        unknowns=tuple(dict.fromkeys((*result.unknowns, "STALE_INPUTS"))),
        affected_objects=tuple(dict.fromkeys((*result.affected_objects, *affected_objects))),
        required_reverification=tuple(dict.fromkeys((*result.required_reverification, "reverify_differential_pair"))),
        summary="The previous DIFFERENTIAL_PAIR verification is stale and must be rerun.",
    )
    return VerificationResult.create(**values)


# Keep the public rule name close to the verifier surface while the authority
# remains the foundation contract used by PCBConstraint.
__all__ = (
    "DifferentialPairEvaluation",
    "DifferentialPairEvaluationEngine",
    "DifferentialPairMeasurement",
    "DifferentialPairMeasurementMethod",
    "DifferentialPairMeasurementQuality",
    "DifferentialPairPreventEvaluator",
    "DifferentialPairRelationship",
    "DifferentialPairRelationshipResolver",
    "DifferentialPairRouteCandidate",
    "DifferentialPairRouteScope",
    "DifferentialPairRuleMetric",
    "DifferentialPairTransitionSymmetry",
    "DifferentialPairVerifier",
    "DifferentialRouteCandidate",
    "mark_differential_pair_stale",
)
