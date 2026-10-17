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
    PCBPlacementProjection,
    PCBPowerLoopMetric,
    PCBPowerPinEvidence,
    PCBPowerPinRole,
    PCBPowerStageEvidence,
    PCBPowerTopology,
    PCBRoutePathProjection,
    PCBRouteSegmentProjection,
    PCBVerificationPlan,
    PCBViaProjection,
)
from .models import PCBContract


def _stable_id(prefix: str, *parts: object) -> str:
    payload = "|".join(str(item) for item in parts).encode("utf-8")
    return f"{prefix}-" + hashlib.sha256(payload).hexdigest()[:32]


class PowerLoopType(StrEnum):
    INPUT_HOT_LOOP = "INPUT_HOT_LOOP"
    OUTPUT_POWER_PATH = "OUTPUT_POWER_PATH"


class PowerLoopMeasurementQuality(StrEnum):
    EXACT = "EXACT"
    PARTIAL = "PARTIAL"
    APPROXIMATE = "APPROXIMATE"
    UNAVAILABLE = "UNAVAILABLE"


class PowerLoopMeasurementMethod(StrEnum):
    ROUTED_LOOP_PERIMETER = "ROUTED_LOOP_PERIMETER"
    POLYGONAL_LOOP_AREA = "POLYGONAL_LOOP_AREA"
    BOUNDING_BOX_AREA = "BOUNDING_BOX_AREA"
    PAD_TO_PAD_DISTANCE = "PAD_TO_PAD_DISTANCE"
    ROUTED_PATH_LENGTH = "ROUTED_PATH_LENGTH"


class PowerLayoutCandidate(PCBContract):
    candidate_id: str
    base_state_fingerprint: str
    placements: tuple[PCBPlacementProjection, ...] = ()
    route_segments: tuple[PCBRouteSegmentProjection, ...] = ()
    route_paths: tuple[PCBRoutePathProjection, ...] = ()
    vias: tuple[PCBViaProjection, ...] = ()

    @classmethod
    def create(cls, **values: object) -> PowerLayoutCandidate:
        return cls.model_validate(values)


class PowerStageRelationship(PCBContract):
    power_stage_id: str
    topology: PCBPowerTopology
    controller_component_id: str | None
    input_net_id: str | None
    output_net_id: str | None
    return_net_id: str | None
    switch_node_net_id: str | None
    input_capacitor_ids: tuple[str, ...]
    output_capacitor_ids: tuple[str, ...]
    inductor_ids: tuple[str, ...]
    feedback_component_ids: tuple[str, ...]
    feedback_net_ids: tuple[str, ...]
    switching_component_ids: tuple[str, ...]
    pin_roles: tuple[PCBPowerPinEvidence, ...]
    hot_loop_route_segment_ids: tuple[str, ...]
    hot_loop_via_ids: tuple[str, ...]
    output_route_segment_ids: tuple[str, ...]
    constraint_ids: tuple[str, ...]
    evidence_refs: tuple[PCBFoundationEvidence, ...]
    confidence: float
    unknowns: tuple[str, ...] = ()

    @classmethod
    def create(cls, **values: object) -> PowerStageRelationship:
        return cls.model_validate(values)


class PowerLoopRelationship(PCBContract):
    loop_id: str
    power_stage_id: str
    loop_type: PowerLoopType
    member_component_ids: tuple[str, ...]
    member_pin_ids: tuple[str, ...]
    member_pad_ids: tuple[str, ...]
    member_net_ids: tuple[str, ...]
    route_segment_ids: tuple[str, ...]
    via_ids: tuple[str, ...]
    constraint_id: str
    evidence_refs: tuple[PCBFoundationEvidence, ...]
    geometry_quality: PowerLoopMeasurementQuality
    unknowns: tuple[str, ...] = ()

    @classmethod
    def create(cls, **values: object) -> PowerLoopRelationship:
        return cls.model_validate(values)


class PowerLoopMeasurement(PCBContract):
    measurement_id: str
    loop_id: str
    method: PowerLoopMeasurementMethod
    value: float
    unit: str
    quality: PowerLoopMeasurementQuality
    source_objects: tuple[str, ...]

    @classmethod
    def create(cls, **values: object) -> PowerLoopMeasurement:
        return cls.model_validate(values)


class SwitchNodeRegion(PCBContract):
    region_id: str
    power_stage_id: str
    net_id: str
    route_segment_ids: tuple[str, ...]
    layer_ids: tuple[str, ...]
    via_ids: tuple[str, ...]
    route_length_mm: float | None
    area_mm2: float | None
    geometry_quality: PowerLoopMeasurementQuality
    evidence_refs: tuple[PCBFoundationEvidence, ...]
    unknowns: tuple[str, ...] = ()

    @classmethod
    def create(cls, **values: object) -> SwitchNodeRegion:
        return cls.model_validate(values)


class FeedbackRelationship(PCBContract):
    relationship_id: str
    power_stage_id: str
    feedback_component_ids: tuple[str, ...]
    feedback_net_ids: tuple[str, ...]
    switch_node_net_id: str | None
    distance_mm: float | None
    geometry_quality: PowerLoopMeasurementQuality
    evidence_refs: tuple[PCBFoundationEvidence, ...]
    unknowns: tuple[str, ...] = ()

    @classmethod
    def create(cls, **values: object) -> FeedbackRelationship:
        return cls.model_validate(values)


@dataclass(frozen=True)
class PowerLoopEvaluation:
    result: Any
    applicability: PCBApplicability
    power_stages: tuple[PowerStageRelationship, ...]
    loops: tuple[PowerLoopRelationship, ...]
    measurements: tuple[PowerLoopMeasurement, ...]
    switch_nodes: tuple[SwitchNodeRegion, ...]
    feedback: tuple[FeedbackRelationship, ...]


class PowerStageRelationshipResolver:
    """Resolve only explicit power-stage evidence from one constraint."""

    def resolve(
        self,
        state: PCBEngineeringState,
        constraint_set: PCBConstraintSet,
        *,
        constraint: Any | None = None,
    ) -> tuple[PowerStageRelationship, ...]:
        checked_state = PCBEngineeringState.model_validate(state.model_dump(mode="python"))
        checked_set = PCBConstraintSet.model_validate(constraint_set.model_dump(mode="python"))
        power_constraint = constraint or next(
            (item for item in checked_set.constraints if item.constraint_type.value == "SWITCHING_POWER"),
            None,
        )
        if power_constraint is None:
            return ()
        components = {item.component_id: item for item in checked_state.components}
        nets = {item.net_id for item in checked_state.nets}
        relationships: list[PowerStageRelationship] = []
        for evidence in power_constraint.power_stage_evidence:
            unknowns = list(evidence.unknowns)
            if power_constraint.constraint_id not in evidence.constraint_ids:
                unknowns.append("POWER_STAGE_CONSTRAINT_BINDING_MISSING")
            if not evidence.evidence_refs:
                unknowns.append("POWER_STAGE_EVIDENCE_MISSING")
            if evidence.topology is PCBPowerTopology.UNKNOWN:
                unknowns.append("POWER_TOPOLOGY_UNKNOWN")
            if evidence.topology is PCBPowerTopology.OTHER:
                unknowns.append("POWER_TOPOLOGY_UNSUPPORTED")
            for component_id in _stage_component_ids(evidence):
                if component_id not in components:
                    unknowns.append(f"MISSING_COMPONENT:{component_id}")
            for net_id in _stage_net_ids(evidence):
                if net_id is not None and net_id not in nets:
                    unknowns.append(f"MISSING_NET:{net_id}")
            relationships.append(
                PowerStageRelationship.create(
                    power_stage_id=evidence.power_stage_id,
                    topology=evidence.topology,
                    controller_component_id=evidence.controller_component_id,
                    input_net_id=evidence.input_net_id,
                    output_net_id=evidence.output_net_id,
                    return_net_id=evidence.return_net_id,
                    switch_node_net_id=evidence.switch_node_net_id,
                    input_capacitor_ids=evidence.input_capacitor_ids,
                    output_capacitor_ids=evidence.output_capacitor_ids,
                    inductor_ids=evidence.inductor_ids,
                    feedback_component_ids=evidence.feedback_component_ids,
                    feedback_net_ids=evidence.feedback_net_ids,
                    switching_component_ids=evidence.switching_component_ids,
                    pin_roles=evidence.pin_roles,
                    hot_loop_route_segment_ids=evidence.hot_loop_route_segment_ids,
                    hot_loop_via_ids=evidence.hot_loop_via_ids,
                    output_route_segment_ids=evidence.output_route_segment_ids,
                    constraint_ids=evidence.constraint_ids,
                    evidence_refs=evidence.evidence_refs,
                    confidence=evidence.confidence,
                    unknowns=tuple(dict.fromkeys(unknowns)),
                )
            )
        return tuple(sorted(relationships, key=lambda item: item.power_stage_id))


class PowerLoopEvaluationEngine:
    """Deterministic power-loop evaluator shared by Verify and Prevent."""

    def evaluate(
        self,
        state: PCBEngineeringState,
        constraint_set: PCBConstraintSet,
        plan: PCBVerificationPlan,
        *,
        candidate: PowerLayoutCandidate | None = None,
    ) -> PowerLoopEvaluation:
        _, VerificationIssue, _, VerificationStatus = _verification_types()
        checked_state = PCBEngineeringState.model_validate(state.model_dump(mode="python"))
        checked_set = PCBConstraintSet.model_validate(constraint_set.model_dump(mode="python"))
        checked_plan = PCBVerificationPlan.model_validate(plan.model_dump(mode="python"))
        constraint = next(
            (item for item in checked_set.constraints if item.constraint_type.value == "SWITCHING_POWER"),
            None,
        )
        compared = (
            checked_state.fingerprint,
            checked_set.fingerprint,
            checked_plan.fingerprint,
            checked_state.source_artifact_fingerprint,
        )
        evidence = _all_evidence(checked_state, constraint)
        affected = _affected_objects(checked_state, constraint)
        if constraint is None:
            return self._finish(
                checked_state, compared, evidence, PCBApplicability.UNKNOWN,
                (), (), (), (), VerificationStatus.UNKNOWN,
                "SWITCHING_POWER constraint is unavailable.",
                ("MISSING_POWER_LOOP_CONSTRAINT",), (), affected,
            )
        if (
            checked_state.status is PCBFoundationStatus.STALE
            or checked_set.status is PCBFoundationStatus.STALE
            or checked_plan.status is PCBFoundationStatus.STALE
            or checked_set.state_fingerprint != checked_state.fingerprint
            or checked_plan.pcb_state_fingerprint != checked_state.fingerprint
            or checked_plan.constraint_set_fingerprint != checked_set.fingerprint
        ):
            return self._finish(
                checked_state, compared, evidence, PCBApplicability.UNKNOWN,
                (), (), (), (), VerificationStatus.UNKNOWN,
                "Power-loop inputs are stale or fingerprint-mismatched.",
                ("STALE_INPUTS",), (), affected,
            )
        if constraint.applicability is PCBApplicability.NOT_APPLICABLE:
            return self._finish(
                checked_state, compared, evidence, PCBApplicability.NOT_APPLICABLE,
                (), (), (), (), VerificationStatus.NOT_APPLICABLE,
                "No evidence-backed switching power stage is applicable to this PCB.",
                (), (), affected,
            )
        if not constraint.power_stage_evidence:
            return self._finish(
                checked_state, compared, evidence, PCBApplicability.UNKNOWN,
                (), (), (), (), VerificationStatus.UNKNOWN,
                "Switching-power applicability or topology evidence is incomplete.",
                ("POWER_STAGE_RELATION_UNKNOWN",), (), affected,
            )

        stages = PowerStageRelationshipResolver().resolve(checked_state, checked_set, constraint=constraint)
        candidate_state = _candidate_state(checked_state, candidate)
        loops: list[PowerLoopRelationship] = []
        measurements: list[PowerLoopMeasurement] = []
        switch_nodes: list[SwitchNodeRegion] = []
        feedback: list[FeedbackRelationship] = []
        issues: list[Any] = []
        unknowns: list[str] = []
        statuses: list[Any] = []
        for stage in stages:
            stage_status, stage_unknowns, stage_issues = self._validate_stage(
                candidate_state, constraint, stage, VerificationIssue
            )
            statuses.append(stage_status)
            unknowns.extend(stage_unknowns)
            issues.extend(stage_issues)
            loop, loop_measurements, loop_unknowns, loop_status, loop_issues = self._input_hot_loop(
                candidate_state, constraint, stage, VerificationIssue
            )
            loops.append(loop)
            measurements.extend(loop_measurements)
            unknowns.extend(loop_unknowns)
            statuses.append(loop_status)
            issues.extend(loop_issues)
            node, node_status, node_unknowns = self._switch_node(candidate_state, stage)
            if node is not None:
                switch_nodes.append(node)
            statuses.append(node_status)
            unknowns.extend(node_unknowns)
            relation, relation_status, relation_unknowns = self._feedback(candidate_state, constraint, stage)
            if relation is not None:
                feedback.append(relation)
            statuses.append(relation_status)
            unknowns.extend(relation_unknowns)

        rules_status, rule_unknowns, rule_issues = self._evaluate_rules(
            constraint, measurements, switch_nodes, feedback, VerificationIssue
        )
        statuses.append(rules_status)
        unknowns.extend(rule_unknowns)
        issues.extend(rule_issues)
        status = _worst(statuses, VerificationStatus)
        if status is VerificationStatus.PASS:
            summary = "Evidence-backed power-stage and power-loop geometry satisfy the current constraint."
        elif status is VerificationStatus.FAIL:
            summary = "One or more evidence-backed power-loop constraints are violated."
        else:
            summary = "Power-loop verification is unknown because topology, relation, geometry, or machine rules are incomplete."
        return self._finish(
            checked_state, compared, evidence, PCBApplicability.REQUIRED,
            tuple(stages), tuple(loops), tuple(measurements), tuple(switch_nodes),
            status, summary, tuple(dict.fromkeys(unknowns)), tuple(issues), affected,
            feedback=tuple(feedback),
        )

    def _validate_stage(self, state, constraint, stage, issue_type):
        _, _, _, VerificationStatus = _verification_types()
        unknowns = list(stage.unknowns)
        issues: list[Any] = []
        if stage.topology not in {PCBPowerTopology.BUCK, PCBPowerTopology.BOOST, PCBPowerTopology.BUCK_BOOST}:
            unknowns.append("POWER_TOPOLOGY_UNKNOWN")
        required_ids = {
            "input_net": stage.input_net_id,
            "output_net": stage.output_net_id,
            "return_net": stage.return_net_id,
            "switch_node_net": stage.switch_node_net_id,
        }
        known_nets = {item.net_id for item in state.nets}
        for label, net_id in required_ids.items():
            if net_id is None or net_id not in known_nets:
                unknowns.append(f"MISSING_{label.upper()}_IDENTITY")
        if not stage.input_capacitor_ids:
            unknowns.append("MISSING_INPUT_CAPACITOR_RELATION")
        if not stage.inductor_ids:
            unknowns.append("MISSING_INDUCTOR_RELATION")
        for capacitor_id in stage.input_capacitor_ids:
            pin_roles = _pins_for_component(stage.pin_roles, capacitor_id)
            power = _first_role(pin_roles, PCBPowerPinRole.CIN_POWER)
            return_pin = _first_role(pin_roles, PCBPowerPinRole.CIN_RETURN)
            if power is None or return_pin is None:
                unknowns.append(f"MISSING_CIN_PIN_RELATION:{capacitor_id}")
            elif (
                power.net_id != stage.input_net_id
                or return_pin.net_id != stage.return_net_id
                or _pad_net(state, power.pad_id) != stage.input_net_id
                or _pad_net(state, return_pin.pad_id) != stage.return_net_id
            ):
                issues.append(_issue(
                    issue_type, "INPUT_CAPACITOR_RELATION_INVALID", capacitor_id,
                    (capacitor_id, *(item.pad_id for item in (power, return_pin) if item.pad_id)),
                    _evidence_ids(stage.evidence_refs),
                    "The approved input capacitor pins do not bind to the stage VIN and return nets.",
                    ("INPUT_CAPACITOR_RELATION_INVALID",),
                    "Bind Cin power and return pads to the approved stage nets.",
                ))
        if stage.feedback_component_ids and not any(
            item.component_id in stage.feedback_component_ids
            and item.role in {PCBPowerPinRole.FB, PCBPowerPinRole.SENSE}
            for item in stage.pin_roles
        ):
            unknowns.append("MISSING_FEEDBACK_PIN_RELATION")
        if issues:
            return VerificationStatus.FAIL, tuple(dict.fromkeys(unknowns)), issues
        if unknowns:
            return VerificationStatus.UNKNOWN, tuple(dict.fromkeys(unknowns)), issues
        return VerificationStatus.PASS, (), issues

    def _input_hot_loop(self, state, constraint, stage, issue_type):
        _, _, _, VerificationStatus = _verification_types()
        route_ids = stage.hot_loop_route_segment_ids
        routes = tuple(item for item in state.routing if item.segment_id in route_ids)
        route_by_id = {item.segment_id: item for item in routes}
        unknowns: list[str] = []
        issues: list[Any] = []
        if len(routes) != len(route_ids):
            unknowns.append("POWER_PATH_GEOMETRY_UNKNOWN")
        if not route_ids:
            unknowns.append("MISSING_HOT_LOOP_ROUTE_IDENTITY")
        ordered = tuple(route_by_id[item] for item in route_ids if item in route_by_id)
        quality = PowerLoopMeasurementQuality.EXACT
        measurements: list[PowerLoopMeasurement] = []
        perimeter, area, closed = _closed_route_measurements(ordered)
        if not closed:
            unknowns.append("CURRENT_PATH_AMBIGUOUS")
            quality = PowerLoopMeasurementQuality.PARTIAL
        if closed and perimeter is not None:
            measurements.append(PowerLoopMeasurement.create(
                measurement_id=_stable_id("power-measurement", stage.power_stage_id, "perimeter"),
                loop_id=_stable_id("power-loop", stage.power_stage_id, PowerLoopType.INPUT_HOT_LOOP.value),
                method=PowerLoopMeasurementMethod.ROUTED_LOOP_PERIMETER,
                value=perimeter,
                unit="mm",
                quality=quality,
                source_objects=tuple(item.segment_id for item in ordered),
            ))
        if closed and area is not None:
            measurements.append(PowerLoopMeasurement.create(
                measurement_id=_stable_id("power-measurement", stage.power_stage_id, "area"),
                loop_id=_stable_id("power-loop", stage.power_stage_id, PowerLoopType.INPUT_HOT_LOOP.value),
                method=PowerLoopMeasurementMethod.POLYGONAL_LOOP_AREA,
                value=area,
                unit="mm2",
                quality=quality,
                source_objects=tuple(item.segment_id for item in ordered),
            ))
        for capacitor_id in stage.input_capacitor_ids:
            power_pin = _first_role(_pins_for_component(stage.pin_roles, capacitor_id), PCBPowerPinRole.CIN_POWER)
            vin_pin = _first_role(stage.pin_roles, PCBPowerPinRole.VIN)
            return_pin = _first_role(_pins_for_component(stage.pin_roles, capacitor_id), PCBPowerPinRole.CIN_RETURN)
            stage_return = _first_role(stage.pin_roles, PCBPowerPinRole.RETURN)
            for left, right, suffix in ((vin_pin, power_pin, "vin"), (stage_return, return_pin, "return")):
                if left is None or right is None:
                    unknowns.append(f"MISSING_CIN_PAD_GEOMETRY:{capacitor_id}")
                    continue
                distance = _pad_distance(state, left.pad_id, right.pad_id)
                if distance is None:
                    unknowns.append(f"MISSING_CIN_PAD_GEOMETRY:{capacitor_id}")
                    continue
                measurements.append(PowerLoopMeasurement.create(
                    measurement_id=_stable_id("power-measurement", stage.power_stage_id, capacitor_id, suffix),
                    loop_id=_stable_id("power-loop", stage.power_stage_id, PowerLoopType.INPUT_HOT_LOOP.value),
                    method=PowerLoopMeasurementMethod.PAD_TO_PAD_DISTANCE,
                    value=distance,
                    unit="mm",
                    quality=PowerLoopMeasurementQuality.EXACT,
                    source_objects=tuple(item for item in (left.pad_id, right.pad_id) if item is not None),
                ))
        member_pins = tuple(
            item.pin_id for item in stage.pin_roles
            if item.role in {
                PCBPowerPinRole.VIN, PCBPowerPinRole.RETURN, PCBPowerPinRole.SW,
                PCBPowerPinRole.CIN_POWER, PCBPowerPinRole.CIN_RETURN,
            }
            and item.pin_id is not None
        )
        member_pads = tuple(
            item.pad_id for item in stage.pin_roles
            if item.role in {
                PCBPowerPinRole.VIN, PCBPowerPinRole.RETURN, PCBPowerPinRole.SW,
                PCBPowerPinRole.CIN_POWER, PCBPowerPinRole.CIN_RETURN,
            }
            and item.pad_id is not None
        )
        member_components = tuple(dict.fromkeys((
            *(item for item in (stage.controller_component_id,)),
            *stage.switching_component_ids,
            *stage.input_capacitor_ids,
        )))
        loop = PowerLoopRelationship.create(
            loop_id=_stable_id("power-loop", stage.power_stage_id, PowerLoopType.INPUT_HOT_LOOP.value),
            power_stage_id=stage.power_stage_id,
            loop_type=PowerLoopType.INPUT_HOT_LOOP,
            member_component_ids=member_components,
            member_pin_ids=member_pins,
            member_pad_ids=member_pads,
            member_net_ids=tuple(item for item in (
                stage.input_net_id, stage.return_net_id, stage.switch_node_net_id,
            ) if item is not None),
            route_segment_ids=route_ids,
            via_ids=stage.hot_loop_via_ids,
            constraint_id=constraint.constraint_id,
            evidence_refs=stage.evidence_refs,
            geometry_quality=quality,
            unknowns=tuple(dict.fromkeys(unknowns)),
        )
        status = VerificationStatus.UNKNOWN if unknowns else VerificationStatus.PASS
        return loop, tuple(measurements), tuple(dict.fromkeys(unknowns)), status, issues

    def _switch_node(self, state, stage):
        _, _, _, VerificationStatus = _verification_types()
        if stage.switch_node_net_id is None:
            return None, VerificationStatus.NOT_APPLICABLE, ()
        routes = tuple(item for item in state.routing if item.net_id == stage.switch_node_net_id)
        if not routes:
            node = SwitchNodeRegion.create(
                region_id=_stable_id("switch-node", stage.power_stage_id),
                power_stage_id=stage.power_stage_id,
                net_id=stage.switch_node_net_id,
                route_segment_ids=(),
                layer_ids=(),
                via_ids=(),
                route_length_mm=None,
                area_mm2=None,
                geometry_quality=PowerLoopMeasurementQuality.UNAVAILABLE,
                evidence_refs=stage.evidence_refs,
                unknowns=("SW_NODE_GEOMETRY_UNKNOWN",),
            )
            return node, VerificationStatus.PASS, ("SW_NODE_GEOMETRY_UNKNOWN",)
        length = sum(_segment_length(item) for item in routes)
        node = SwitchNodeRegion.create(
            region_id=_stable_id("switch-node", stage.power_stage_id),
            power_stage_id=stage.power_stage_id,
            net_id=stage.switch_node_net_id,
            route_segment_ids=tuple(item.segment_id for item in routes),
            layer_ids=tuple(sorted({item.layer for item in routes})),
            via_ids=stage.hot_loop_via_ids,
            route_length_mm=length,
            area_mm2=None,
            geometry_quality=PowerLoopMeasurementQuality.PARTIAL,
            evidence_refs=stage.evidence_refs,
            unknowns=("SW_NODE_AREA_GEOMETRY_UNAVAILABLE",),
        )
        return node, VerificationStatus.PASS, ("SW_NODE_AREA_GEOMETRY_UNAVAILABLE",)

    def _feedback(self, state, constraint, stage):
        _, _, _, VerificationStatus = _verification_types()
        if not stage.feedback_component_ids and not stage.feedback_net_ids:
            return None, VerificationStatus.NOT_APPLICABLE, ()
        feedback_pads = tuple(
            item.pad_id for item in stage.pin_roles
            if item.role in {PCBPowerPinRole.FB, PCBPowerPinRole.SENSE} and item.pad_id is not None
        )
        switch_routes = tuple(item for item in state.routing if item.net_id == stage.switch_node_net_id)
        distance = _minimum_pad_to_routes(state, feedback_pads, switch_routes)
        unknowns = () if distance is not None else ("FB_SW_GEOMETRY_UNKNOWN",)
        relation = FeedbackRelationship.create(
            relationship_id=_stable_id("feedback", stage.power_stage_id),
            power_stage_id=stage.power_stage_id,
            feedback_component_ids=stage.feedback_component_ids,
            feedback_net_ids=stage.feedback_net_ids,
            switch_node_net_id=stage.switch_node_net_id,
            distance_mm=distance,
            geometry_quality=PowerLoopMeasurementQuality.EXACT if distance is not None else PowerLoopMeasurementQuality.UNAVAILABLE,
            evidence_refs=stage.evidence_refs,
            unknowns=unknowns,
        )
        return relation, VerificationStatus.PASS, unknowns

    @staticmethod
    def _evaluate_rules(constraint, measurements, switch_nodes, feedback, issue_type):
        _, _, _, VerificationStatus = _verification_types()
        rules = constraint.power_loop_rules
        if not rules:
            return VerificationStatus.UNKNOWN, ("MISSING_MACHINE_EVALUABLE_RULE",), ()
        issues: list[Any] = []
        unknowns: list[str] = []
        status = VerificationStatus.PASS
        for rule in rules:
            if rule.metric is PCBPowerLoopMetric.SW_NODE_AREA:
                values = tuple(item.area_mm2 for item in switch_nodes if item.area_mm2 is not None)
                qualities = tuple(item.geometry_quality for item in switch_nodes)
            elif rule.metric is PCBPowerLoopMetric.FB_KEEP_OUT_DISTANCE:
                values = tuple(item.distance_mm for item in feedback if item.distance_mm is not None)
                qualities = tuple(item.geometry_quality for item in feedback)
            else:
                method = {
                    PCBPowerLoopMetric.HOT_LOOP_PERIMETER: PowerLoopMeasurementMethod.ROUTED_LOOP_PERIMETER,
                    PCBPowerLoopMetric.HOT_LOOP_AREA: PowerLoopMeasurementMethod.POLYGONAL_LOOP_AREA,
                }[rule.metric]
                selected = tuple(item for item in measurements if item.method is method)
                values = tuple(item.value for item in selected)
                qualities = tuple(item.quality for item in selected)
            if not values or any(item is not PowerLoopMeasurementQuality.EXACT for item in qualities):
                unknowns.append(f"RULE_GEOMETRY_UNKNOWN:{rule.rule_id}")
                status = VerificationStatus.UNKNOWN
                continue
            for value in values:
                violated = value > rule.limit if rule.comparison == "LE" else value < rule.limit
                if violated:
                    category = {
                        PCBPowerLoopMetric.HOT_LOOP_PERIMETER: "HOT_LOOP_RULE_VIOLATION",
                        PCBPowerLoopMetric.HOT_LOOP_AREA: "HOT_LOOP_RULE_VIOLATION",
                        PCBPowerLoopMetric.SW_NODE_AREA: "SW_NODE_RULE_VIOLATION",
                        PCBPowerLoopMetric.FB_KEEP_OUT_DISTANCE: "FB_KEEP_OUT_VIOLATION",
                    }[rule.metric]
                    issues.append(_issue(
                        issue_type, category, rule.rule_id, (rule.rule_id,),
                        _evidence_ids(rule.evidence_refs),
                        f"Measured {rule.metric.value} value {value:g}{rule.unit} violates the approved rule.",
                        (category,),
                        "Revise the power layout candidate under the approved power-loop constraint.",
                    ))
                    status = VerificationStatus.FAIL
        return status, tuple(dict.fromkeys(unknowns)), issues

    @staticmethod
    def _finish(
        state,
        compared,
        evidence,
        applicability,
        stages,
        loops,
        measurements,
        switch_nodes,
        status,
        summary,
        unknowns,
        issues,
        affected,
        *,
        feedback=(),
    ):
        VerificationEvidenceReference, _, VerificationResult, VerificationStatus = _verification_types()
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
            verifier="POWER_LOOP",
            scope="PCB",
            applicability=applicability.value,
            issues=tuple(issues),
            evidence=evidence_objects,
            affected_objects=tuple(affected),
            confidence=1.0 if status in {VerificationStatus.PASS, VerificationStatus.NOT_APPLICABLE, VerificationStatus.FAIL} else None,
            assumptions=("Power topology and layout memberships are consumed only from evidence-bound constraint fields.",),
            unknowns=tuple(dict.fromkeys(unknowns)),
            recommendations=tuple(dict.fromkeys(item.recommendation for item in issues)),
            measurement_refs=tuple(item.measurement_id for item in measurements),
            revision=state.revision,
        )
        return PowerLoopEvaluation(
            result=result,
            applicability=applicability,
            power_stages=tuple(stages),
            loops=tuple(loops),
            measurements=tuple(measurements),
            switch_nodes=tuple(switch_nodes),
            feedback=tuple(feedback),
        )


class PowerLoopVerifier:
    def __init__(self, engine: PowerLoopEvaluationEngine | None = None) -> None:
        self._engine = engine or PowerLoopEvaluationEngine()

    def verify(self, state, constraint_set, plan):
        return self._engine.evaluate(state, constraint_set, plan).result

    def verify_detailed(self, state, constraint_set, plan):
        return self._engine.evaluate(state, constraint_set, plan)


class PowerLoopPreventEvaluator:
    def __init__(self, engine: PowerLoopEvaluationEngine | None = None) -> None:
        self._engine = engine or PowerLoopEvaluationEngine()

    def evaluate(self, state, constraint_set, candidate, plan=None) -> PCBPreventEvaluation:
        _, _, _, VerificationStatus = _verification_types()
        checked_candidate = PowerLayoutCandidate.model_validate(candidate.model_dump(mode="python"))
        if checked_candidate.base_state_fingerprint != state.fingerprint:
            raise ValueError("POWER_LOOP Prevent candidate is stale")
        checked_plan = plan or _build_plan(state, constraint_set)
        detailed = self._engine.evaluate(state, constraint_set, checked_plan, candidate=checked_candidate)
        constraint = next(
            (item for item in constraint_set.constraints if item.constraint_type.value == "SWITCHING_POWER"),
            None,
        )
        if detailed.result.status in {VerificationStatus.PASS, VerificationStatus.NOT_APPLICABLE}:
            status = PCBPreventStatus.ALLOW
        elif detailed.result.status is VerificationStatus.FAIL:
            status = (
                PCBPreventStatus.BLOCK
                if constraint is not None and constraint.enforcement_mode is PCBConstraintEnforcementMode.HARD
                else PCBPreventStatus.WARN
            )
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
            measurements=(),
            geometry_measurements=detailed.measurements,
            status=status,
            reason=detailed.result.summary,
            evidence=detailed.result.evidence_refs,
            unknowns=detailed.result.unknowns,
        )


def mark_power_loop_stale(result, *, reason: str, affected_objects: tuple[str, ...]) -> Any:
    _, _, VerificationResult, VerificationStatus = _verification_types()
    values = result.model_dump(mode="python", exclude={"fingerprint"})
    values.update(
        status=VerificationStatus.UNKNOWN,
        applicability=PCBApplicability.UNKNOWN.value,
        stale=True,
        stale_reason=str(reason),
        affected_objects=tuple(affected_objects),
        unknowns=tuple(dict.fromkeys((*result.unknowns, "STALE_INPUTS"))),
        required_reverification=tuple(dict.fromkeys((*result.required_reverification, "reverify_power_loop"))),
        summary="The previous POWER_LOOP verification is stale and must be rerun.",
    )
    return VerificationResult.create(**values)


def _build_plan(state, constraint_set):
    from .foundation import PCBConstraintCompiler

    return PCBConstraintCompiler().build_plan(state, constraint_set)


def _candidate_state(state, candidate):
    if candidate is None:
        return state
    values = state.model_dump(mode="python", exclude={"fingerprint"})
    if candidate.placements:
        values["placement"] = candidate.placements
    if candidate.route_segments:
        values["routing"] = candidate.route_segments
    if candidate.route_paths:
        values["route_paths"] = candidate.route_paths
    if candidate.vias:
        values["vias"] = candidate.vias
    return PCBEngineeringState.create(**values)


def _stage_component_ids(stage: PCBPowerStageEvidence) -> tuple[str, ...]:
    return tuple(dict.fromkeys((
        *(item for item in (stage.controller_component_id,) if item is not None),
        *stage.input_capacitor_ids,
        *stage.output_capacitor_ids,
        *stage.inductor_ids,
        *stage.feedback_component_ids,
        *stage.switching_component_ids,
        *(item.component_id for item in stage.pin_roles),
    )))


def _stage_net_ids(stage: PCBPowerStageEvidence) -> tuple[str | None, ...]:
    return tuple(dict.fromkeys((
        stage.input_net_id, stage.output_net_id, stage.return_net_id, stage.switch_node_net_id,
        *stage.feedback_net_ids,
        *(item.net_id for item in stage.pin_roles),
    )))


def _pins_for_component(pin_roles, component_id):
    return tuple(item for item in pin_roles if item.component_id == component_id)


def _first_role(pin_roles, role):
    return next((item for item in pin_roles if item.role is role), None)


def _evidence_ids(evidence):
    return tuple(item.evidence_id for item in evidence)


def _all_evidence(state, constraint):
    values = list(state.evidence_refs)
    if constraint is not None:
        values.extend(constraint.evidence_refs)
        values.extend(item for stage in constraint.power_stage_evidence for item in stage.evidence_refs)
        values.extend(item for rule in constraint.power_loop_rules for item in rule.evidence_refs)
    return tuple(sorted(set(values), key=lambda item: item.evidence_id))


def _affected_objects(state, constraint):
    values = [item.net_id for item in state.nets]
    if constraint is not None:
        for stage in constraint.power_stage_evidence:
            values.extend(_stage_component_ids(stage))
            values.extend(item for item in _stage_net_ids(stage) if item is not None)
            values.extend(stage.hot_loop_route_segment_ids)
            values.extend(stage.hot_loop_via_ids)
            values.extend(stage.output_route_segment_ids)
            values.append(stage.power_stage_id)
    return tuple(sorted(set(values)))


def _issue(issue_type, category, location, affected, evidence, problem, effects, recommendation):
    return issue_type.create(
        issue_id=_stable_id("power-issue", category, location, *affected),
        severity="ERROR",
        category=category,
        location=location,
        affected_objects=tuple(affected),
        problem=problem,
        why="The approved power-stage or power-loop evidence is inconsistent with the current PCB projection.",
        possible_effects=tuple(effects),
        evidence=tuple(evidence),
        recommendation=recommendation,
        reverification=("PowerLoopVerifier",),
    )


def _segment_length(segment):
    return math.hypot(segment.end[0] - segment.start[0], segment.end[1] - segment.start[1])


def _closed_route_measurements(segments):
    if not segments:
        return None, None, False
    tolerance = 1e-9
    for left, right in pairwise(segments):
        if math.hypot(left.end[0] - right.start[0], left.end[1] - right.start[1]) > tolerance:
            return None, None, False
    if math.hypot(segments[-1].end[0] - segments[0].start[0], segments[-1].end[1] - segments[0].start[1]) > tolerance:
        return None, None, False
    points = [segments[0].start, *(item.end for item in segments)]
    perimeter = sum(_segment_length(item) for item in segments)
    area = abs(sum(
        left[0] * right[1] - right[0] * left[1]
        for left, right in pairwise(points)
    )) / 2.0
    return perimeter, area, True


def _pad_distance(state, left_pad_id, right_pad_id):
    if left_pad_id is None or right_pad_id is None:
        return None
    pads = {
        pad.pad_id: pad
        for component in state.components
        for pad in component.pads
    }
    left = pads.get(left_pad_id)
    right = pads.get(right_pad_id)
    if left is None or right is None or left.x_mm is None or left.y_mm is None or right.x_mm is None or right.y_mm is None:
        return None
    return math.hypot(left.x_mm - right.x_mm, left.y_mm - right.y_mm)


def _pad_net(state, pad_id):
    if pad_id is None:
        return None
    for component in state.components:
        for pad in component.pads:
            if pad.pad_id == pad_id:
                return pad.net_id
    return None


def _minimum_pad_to_routes(state, pad_ids, routes):
    pads = {
        pad.pad_id: pad
        for component in state.components
        for pad in component.pads
    }
    points = [
        (pads[item].x_mm, pads[item].y_mm)
        for item in pad_ids
        if item in pads and pads[item].x_mm is not None and pads[item].y_mm is not None
    ]
    if not points or not routes:
        return None
    return min(
        _point_segment_distance(point, segment.start, segment.end)
        for point in points
        for segment in routes
    )


def _point_segment_distance(point, start, end):
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if dx == 0 and dy == 0:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    parameter = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy)))
    projection = (start[0] + parameter * dx, start[1] + parameter * dy)
    return math.hypot(point[0] - projection[0], point[1] - projection[1])


def _worst(statuses, status_type):
    order = {
        status_type.FAIL: 5,
        status_type.UNKNOWN: 4,
        status_type.WARNING: 3,
        status_type.PASS: 2,
        status_type.NOT_APPLICABLE: 1,
    }
    return max(statuses, key=lambda item: order.get(item, 4), default=status_type.UNKNOWN)


__all__ = (
    "FeedbackRelationship",
    "PowerLayoutCandidate",
    "PowerLoopEvaluation",
    "PowerLoopEvaluationEngine",
    "PowerLoopMeasurement",
    "PowerLoopMeasurementMethod",
    "PowerLoopMeasurementQuality",
    "PowerLoopPreventEvaluator",
    "PowerLoopRelationship",
    "PowerLoopType",
    "PowerLoopVerifier",
    "PowerStageRelationship",
    "PowerStageRelationshipResolver",
    "SwitchNodeRegion",
    "mark_power_loop_stale",
)
