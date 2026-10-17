from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from enum import StrEnum
from importlib import import_module
from typing import Any, TypeAlias

from .foundation import (
    PCBApplicability,
    PCBConstraint,
    PCBConstraintEnforcementMode,
    PCBConstraintSet,
    PCBDecouplingRelation,
    PCBEngineeringState,
    PCBFoundationEvidence,
    PCBFoundationStatus,
    PCBNetProjection,
    PCBPadProjection,
    PCBPlacementProjection,
    PCBSignalClass,
    PCBVerificationPlan,
)
from .models import PCBContract


def _id(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is invalid")
    return value.strip()


def _fp(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        raise ValueError(f"{field} is invalid")
    return value


def _stable_id(prefix: str, *parts: object) -> str:
    payload = "|".join(str(item) for item in parts).encode("utf-8")
    return f"{prefix}-" + hashlib.sha256(payload).hexdigest()[:32]


def _verification_types() -> tuple[type[Any], type[Any], type[Any], type[Any]]:
    """Load shared verification contracts without a package import cycle."""

    module = import_module("embedded_copilot." + "verification_" + "loop")
    return (
        module.VerificationEvidenceReference,
        module.VerificationIssue,
        module.VerificationResult,
        module.VerificationStatus,
    )


# These aliases keep annotations honest at this import boundary: the concrete
# shared Pydantic contracts are loaded by _verification_types at call time.
VerificationEvidenceReference: TypeAlias = Any
VerificationIssue: TypeAlias = Any
VerificationResult: TypeAlias = Any
VerificationStatus: TypeAlias = Any


class PCBPreventStatus(StrEnum):
    ALLOW = "ALLOW"
    WARN = "WARN"
    BLOCK = "BLOCK"
    UNKNOWN = "UNKNOWN"


class PCBMeasurementQuality(StrEnum):
    PAD_LEVEL = "PAD_LEVEL"
    APPROXIMATE = "APPROXIMATE"
    UNAVAILABLE = "UNAVAILABLE"


class DecouplingMeasurement(PCBContract):
    measurement_id: str
    relation_id: str
    measurement_start: str
    measurement_end: str
    distance: float
    unit: str
    geometry_source: str
    quality: PCBMeasurementQuality

    @classmethod
    def create(cls, **values: object) -> DecouplingMeasurement:
        return cls.model_validate(values)


class PCBPlacementCandidate(PCBContract):
    candidate_id: str
    base_state_fingerprint: str
    placements: tuple[PCBPlacementProjection, ...]

    @classmethod
    def create(cls, **values: object) -> PCBPlacementCandidate:
        return cls.model_validate(values)

    @staticmethod
    def _validate_candidate(value: object) -> object:
        return value


class PCBPreventEvaluation(PCBContract):
    project_id: str
    constraint_id: str
    candidate_id: str
    state_fingerprint: str
    constraint_set_fingerprint: str
    affected_objects: tuple[str, ...]
    measurements: tuple[DecouplingMeasurement, ...] = ()
    # G7-B2 reuses this G7-B1 Prevent envelope.  Return-path geometry
    # measurements are kept as typed immutable contracts by the boundary
    # without creating a parallel Prevent result family.
    geometry_measurements: tuple[Any, ...] = ()
    status: PCBPreventStatus
    reason: str
    evidence: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()

    @classmethod
    def create(cls, **values: object) -> PCBPreventEvaluation:
        return cls.model_validate(values)


@dataclass(frozen=True)
class _DetailedEvaluation:
    result: VerificationResult
    measurements: tuple[DecouplingMeasurement, ...]
    constraint: PCBConstraint | None


def _text(value: object) -> str:
    return str(value).strip().upper()


def _component_role(component: Any) -> bool:
    component_type = _text(getattr(component, "type", ""))
    category = _text(getattr(component, "category", ""))
    description = _text(getattr(component, "description", ""))
    return component_type in {"MCU", "CONTROLLER"} or category in {"MCU", "CONTROLLER"} or "MICROCONTROLLER" in description


def _capacitor_role(component: Any) -> bool:
    component_type = _text(getattr(component, "type", ""))
    description = _text(getattr(component, "description", ""))
    return component_type in {"CAPACITOR", "C"} and "DECOUPL" in description


def _scope_matches(capacitor: Any, pin: Any, domain_name: str, net_name: str) -> bool:
    description = _text(getattr(capacitor, "description", ""))
    pin_name = _text(getattr(pin, "pin_name", ""))
    if "VDDA" in description:
        return "VDDA" in pin_name
    if "VDD" in description:
        return "VDD" in pin_name and "VDDA" not in pin_name
    return _text(domain_name) in description or _text(net_name) in description


class DecouplingRelationshipResolver:
    """Resolve typed relationships from approved truth and the G7-A projection."""

    def resolve_state(
        self,
        state: PCBEngineeringState,
        constraint_set: PCBConstraintSet,
        *,
        design_truth: Any | None,
        hardware_truth: Any | None = None,
    ) -> PCBEngineeringState:
        del hardware_truth
        constraint = _decoupling_constraint(constraint_set)
        if constraint is None or design_truth is None:
            return PCBEngineeringState.create(
                **{**state.model_dump(mode="python", exclude={"fingerprint"}), "decoupling_relations": ()}
            )

        state_by_truth_id: dict[str, Any] = {}
        for item in state.components:
            # G7-A may intentionally leave generic library symbols such as
            # Device:C unresolved when several DesignTruth components share
            # one symbol binding.  The artifact reference remains the
            # authoritative physical identity supplied by G5, so retain it
            # as a deterministic fallback without using value heuristics.
            if item.symbol_id is not None:
                state_by_truth_id.setdefault(item.symbol_id, item)
            state_by_truth_id.setdefault(item.reference, item)
        state_pads = {
            (component.reference, pad.number): pad
            for component in state.components
            for pad in component.pads
        }
        nets_by_name = {item.name: item for item in state.nets}
        truth_components = {item.component_id: item for item in getattr(design_truth, "components", ())}
        truth_pins = tuple(getattr(design_truth, "pins", ()))
        truth_signals = {item.signal_id: item for item in getattr(design_truth, "signals", ())}
        truth_nets = tuple(getattr(design_truth, "nets", ()))
        net_by_signal = {
            item.signal: item
            for item in truth_nets
            if getattr(item, "signal", None) is not None
        }
        nodes_by_net = {
            getattr(net, "net_name", ""): tuple(getattr(net, "nodes", ()))
            for net in truth_nets
        }
        domains = tuple(getattr(design_truth, "power_domains", ()))
        relations: list[PCBDecouplingRelation] = []

        for truth_component in truth_components.values():
            load = state_by_truth_id.get(truth_component.component_id)
            if load is None or not _component_role(truth_component):
                continue
            load_pins = tuple(
                item for item in truth_pins if item.component_id == truth_component.component_id
            )
            for pin in load_pins:
                signal = truth_signals.get(getattr(pin, "signal_id", None))
                truth_net = net_by_signal.get(getattr(pin, "signal_id", None))
                if signal is None or truth_net is None:
                    continue
                if _text(getattr(signal, "protocol", "")) != "POWER":
                    continue
                power_net_name = str(getattr(truth_net, "net_name", "")).strip()
                if not power_net_name or power_net_name.casefold() in {"gnd", "vss", "vssa"}:
                    continue
                load_pad = state_pads.get((load.reference, pin.pin_number))
                load_net = nets_by_name.get(power_net_name)
                for domain in domains:
                    component_refs = set(getattr(domain, "component_refs", ()))
                    if truth_component.component_id not in component_refs:
                        continue
                    if str(getattr(domain, "name", "")).strip().casefold() != power_net_name.casefold():
                        continue
                    return_net_name = getattr(domain, "ground_net", None)
                    return_net = nets_by_name.get(return_net_name) if return_net_name else None
                    for truth_cap in truth_components.values():
                        cap = state_by_truth_id.get(truth_cap.component_id)
                        if cap is None or truth_cap.component_id not in component_refs or not _capacitor_role(truth_cap):
                            continue
                        if not _scope_matches(truth_cap, pin, getattr(domain, "name", ""), power_net_name):
                            continue
                        power_pad_number = next(
                            (
                                node.split(".", 1)[1]
                                for node in nodes_by_net.get(power_net_name, ())
                                if node.startswith(f"{cap.reference}.")
                            ),
                            None,
                        )
                        return_pad_number = next(
                            (
                                node.split(".", 1)[1]
                                for node in nodes_by_net.get(str(return_net_name), ())
                                if node.startswith(f"{cap.reference}.")
                            ),
                            None,
                        )
                        power_pad = state_pads.get((cap.reference, power_pad_number)) if power_pad_number else None
                        return_pad = state_pads.get((cap.reference, return_pad_number)) if return_pad_number else None
                        unknowns = []
                        if load_pad is None:
                            unknowns.append("MISSING_LOAD_PIN_PAD_IDENTITY")
                        if power_pad is None:
                            unknowns.append("MISSING_CAPACITOR_POWER_PAD_IDENTITY")
                        if return_pad is None:
                            unknowns.append("MISSING_CAPACITOR_RETURN_PAD_IDENTITY")
                        if load_net is None:
                            unknowns.append("MISSING_POWER_NET_IDENTITY")
                        if return_net is None:
                            unknowns.append("MISSING_RETURN_NET_IDENTITY")
                        relation = PCBDecouplingRelation.create(
                            relation_id=_stable_id("decoupling", state.project_id, load.component_id, pin.pin_id, cap.component_id, constraint.constraint_id),
                            load_component_id=load.component_id,
                            load_power_pin_id=pin.pin_id,
                            load_pad_id=None if load_pad is None else load_pad.pad_id,
                            capacitor_component_id=cap.component_id,
                            capacitor_power_pad_id=None if power_pad is None else power_pad.pad_id,
                            capacitor_return_pad_id=None if return_pad is None else return_pad.pad_id,
                            power_net_id=None if load_net is None else load_net.net_id,
                            return_net_id=None if return_net is None else return_net.net_id,
                            power_domain_id=getattr(domain, "power_domain_id", None),
                            constraint_id=constraint.constraint_id,
                            evidence_refs=tuple(state.evidence_refs),
                            confidence=min(
                                float(getattr(pin, "confidence", 0.0)),
                                float(getattr(truth_cap, "confidence", 0.0)),
                                float(getattr(domain, "confidence", 0.0)),
                            ),
                            unknowns=tuple(unknowns),
                        )
                        relations.append(relation)

        values = state.model_dump(mode="python", exclude={"fingerprint"})
        values["decoupling_relations"] = tuple(sorted(relations, key=lambda item: item.relation_id))
        return PCBEngineeringState.create(**values)


class DecouplingEvaluationEngine:
    """One deterministic rule engine shared by verification and Prevent."""

    def evaluate(
        self,
        state: PCBEngineeringState,
        constraint_set: PCBConstraintSet,
        plan: PCBVerificationPlan,
        *,
        candidate: PCBPlacementCandidate | None = None,
    ) -> _DetailedEvaluation:
        _, _, _, VerificationStatus = _verification_types()
        checked_state = PCBEngineeringState.model_validate(state.model_dump(mode="python"))
        checked_set = PCBConstraintSet.model_validate(constraint_set.model_dump(mode="python"))
        checked_plan = PCBVerificationPlan.model_validate(plan.model_dump(mode="python"))
        constraint = _decoupling_constraint(checked_set)
        evidence = _evidence_objects(checked_state, checked_set)
        evidence_ids = tuple(item.evidence_id for item in evidence)
        evidence_objects = tuple(_verification_evidence(item) for item in evidence)
        compared = (checked_state.fingerprint, checked_set.fingerprint, checked_plan.fingerprint, checked_state.source_artifact_fingerprint)
        affected = tuple(sorted({item for relation in checked_state.decoupling_relations for item in (
            relation.load_component_id, relation.load_power_pin_id, relation.load_pad_id,
            relation.capacitor_component_id, relation.capacitor_power_pad_id,
            relation.capacitor_return_pad_id, relation.power_net_id, relation.return_net_id,
        ) if item is not None}))
        if constraint is None:
            return self._result(
                checked_state, compared, evidence_ids, evidence_objects, VerificationStatus.UNKNOWN,
                summary="DECOUPLING constraint is unavailable.", unknowns=("MISSING_DECOUPLING_CONSTRAINT",),
                affected=affected,
            )
        if (
            checked_set.state_fingerprint != checked_state.fingerprint
            or checked_plan.pcb_state_fingerprint != checked_state.fingerprint
            or checked_plan.constraint_set_fingerprint != checked_set.fingerprint
            or checked_state.status is PCBFoundationStatus.STALE
            or checked_set.status is PCBFoundationStatus.STALE
            or checked_plan.status is PCBFoundationStatus.STALE
        ):
            return self._result(
                checked_state, compared, evidence_ids, evidence_objects, VerificationStatus.UNKNOWN,
                summary="DECOUPLING inputs are stale or fingerprint-mismatched.",
                unknowns=("STALE_INPUTS",), affected=affected,
            )
        if constraint.applicability is PCBApplicability.NOT_APPLICABLE:
            return self._result(
                checked_state, compared, evidence_ids, evidence_objects, VerificationStatus.NOT_APPLICABLE,
                summary="DECOUPLING is not applicable to the current PCB state.",
                affected=affected, applicability=constraint.applicability.value,
            )
        relations = tuple(item for item in checked_state.decoupling_relations if item.constraint_id == constraint.constraint_id)
        if not relations:
            return self._result(
                checked_state, compared, evidence_ids, evidence_objects, VerificationStatus.UNKNOWN,
                summary="No evidence-bound decoupling relation could be resolved.",
                unknowns=("MISSING_DECOUPLING_RELATION",), affected=affected,
                applicability=constraint.applicability.value,
            )

        issues: list[VerificationIssue] = []
        measurements: list[DecouplingMeasurement] = []
        unknowns: list[str] = []
        recommendations: list[str] = []
        statuses: list[VerificationStatus] = []
        placement = None if candidate is None else candidate.placements
        for relation in relations:
            relation_evidence = tuple(item.evidence_id for item in relation.evidence_refs)
            if not relation_evidence:
                unknowns.append("MISSING_DECOUPLING_EVIDENCE")
                statuses.append(VerificationStatus.UNKNOWN)
                continue
            if relation.unknowns:
                unknowns.extend(relation.unknowns)
            load_pad = _pad(checked_state, relation.load_pad_id)
            cap_power_pad = _pad(checked_state, relation.capacitor_power_pad_id)
            cap_return_pad = _pad(checked_state, relation.capacitor_return_pad_id)
            power_result = _power_domain_check(checked_state, relation, load_pad, cap_power_pad)
            if power_result is False:
                statuses.append(VerificationStatus.FAIL)
                issues.append(self._issue(
                    relation, "FAIL", "SAME_POWER_DOMAIN",
                    "The load power pad and capacitor power pad are not on the approved power net.",
                    "Electrical identity conflicts with the approved decoupling relation; proximity cannot override it.",
                    "The capacitor may not decouple the intended rail.", evidence_ids + relation_evidence,
                    "Correct the power-net connectivity and rerun the verifier.",
                ))
                continue
            if power_result is None:
                unknowns.append("UNKNOWN_POWER_DOMAIN_RELATION")
                statuses.append(VerificationStatus.UNKNOWN)
            return_result = _return_check(checked_state, relation, cap_return_pad)
            if return_result is False:
                statuses.append(VerificationStatus.FAIL)
                issues.append(self._issue(
                    relation, "FAIL", "RETURN_PATH_ELECTRICALLY_CONNECTED",
                    "The capacitor return pad is not connected to the approved return net.",
                    "The return relation is electrically inconsistent with the approved power domain.",
                    "The decoupling loop may be open or connected to the wrong reference.", evidence_ids + relation_evidence,
                    "Correct the return-net connectivity and rerun the verifier.",
                ))
            elif return_result is None:
                unknowns.append("UNKNOWN_RETURN_RELATION")
                statuses.append(VerificationStatus.UNKNOWN)
            measurement, measurement_unknown = _measure(checked_state, relation, placement)
            if measurement is not None:
                measurements.append(measurement)
            if measurement_unknown is not None:
                unknowns.append(measurement_unknown)
                statuses.append(VerificationStatus.UNKNOWN)
            if constraint.threshold is None or constraint.evaluation_method != "MAX_PAD_DISTANCE" or constraint.comparison not in {"<=", "LE"}:
                unknowns.append("MISSING_MACHINE_EVALUABLE_RULE")
                statuses.append(VerificationStatus.UNKNOWN)
            elif measurement is not None:
                if measurement.distance > constraint.threshold:
                    status = VerificationStatus.FAIL if constraint.enforcement_mode is PCBConstraintEnforcementMode.HARD else VerificationStatus.WARNING
                    statuses.append(status)
                    issues.append(self._issue(
                        relation, "P1" if status is VerificationStatus.FAIL else "P2", "PAD_DISTANCE",
                        f"Pad-level decoupling distance exceeds the evidence-backed limit of {constraint.threshold:g} {constraint.threshold_unit}.",
                        "The shared DECOUPLING constraint defines a maximum pad distance for this placement.",
                        "Higher interconnect impedance or degraded transient response is possible.", evidence_ids + relation_evidence,
                        "Move the capacitor closer to the approved load power pad and rerun verification.",
                    ))
                else:
                    statuses.append(VerificationStatus.PASS)

        status = _worst(statuses)
        if status is VerificationStatus.UNKNOWN and not unknowns:
            unknowns.append("INSUFFICIENT_DECOUPLING_EVIDENCE")
        if status is VerificationStatus.PASS:
            summary = "All resolved decoupling relations satisfy the shared evidence-backed constraint."
        elif status is VerificationStatus.FAIL:
            summary = "One or more decoupling relations fail electrical or geometry checks."
        elif status is VerificationStatus.WARNING:
            summary = "Decoupling evidence is available but an advisory geometry rule is exceeded."
        else:
            summary = "Decoupling verification is unknown because required evidence or an executable rule is unavailable."
        recommendations.extend(issue.recommendation for issue in issues)
        return self._result(
            checked_state, compared, evidence_ids, evidence_objects, status,
            summary=summary,
            issues=tuple(issues), evidence=tuple(evidence),
            measurements=tuple(measurements), unknowns=tuple(sorted(set(unknowns))),
            recommendations=tuple(dict.fromkeys(recommendations)), affected=affected,
            confidence=(None if status is VerificationStatus.UNKNOWN else min(item.confidence for item in relations)),
            applicability=constraint.applicability.value,
        )

    @staticmethod
    def _issue(
        relation: PCBDecouplingRelation,
        severity: str,
        category: str,
        problem: str,
        why: str,
        effect: str,
        evidence: tuple[str, ...],
        recommendation: str,
    ) -> VerificationIssue:
        _, VerificationIssue, _, _ = _verification_types()
        return VerificationIssue.create(
            issue_id=_stable_id("issue", relation.relation_id, category),
            severity=severity,
            category=f"DECOUPLING:{category}",
            location=relation.relation_id,
            affected_objects=tuple(
                item for item in (
                    relation.load_component_id, relation.load_power_pin_id, relation.load_pad_id,
                    relation.capacitor_component_id, relation.capacitor_power_pad_id,
                    relation.capacitor_return_pad_id, relation.power_net_id, relation.return_net_id,
                ) if item is not None
            ),
            problem=problem,
            why=why,
            possible_effects=(effect,),
            evidence=tuple(sorted(set(evidence))),
            recommendation=recommendation,
            reverification=("DecouplingVerifier",),
        )

    @staticmethod
    def _result(
        state: PCBEngineeringState,
        compared: tuple[str, ...],
        evidence_ids: tuple[str, ...],
        evidence_objects: tuple[VerificationEvidenceReference, ...],
        status: VerificationStatus,
        *,
        summary: str,
        issues: tuple[VerificationIssue, ...] = (),
        evidence: tuple[PCBFoundationEvidence, ...] = (),
        measurements: tuple[DecouplingMeasurement, ...] = (),
        affected: tuple[str, ...] = (),
        confidence: float | None = None,
        unknowns: tuple[str, ...] = (),
        recommendations: tuple[str, ...] = (),
        applicability: str | None = None,
    ) -> _DetailedEvaluation:
        _, _, VerificationResult, _ = _verification_types()
        result = VerificationResult.create(
            verification_id=_stable_id("verification", state.project_id, state.fingerprint, compared[1], compared[2]),
            project_id=state.project_id,
            target_id=state.fingerprint,
            status=status,
            attempt=0,
            compared_fingerprints=compared,
            evidence_refs=evidence_ids,
            summary=summary,
            verifier="DECOUPLING",
            scope="PCB",
            issues=issues,
            evidence=evidence_objects,
            affected_objects=affected,
            confidence=confidence,
            assumptions=("The supplied PCBEngineeringState is the current G5 projection.",),
            unknowns=unknowns,
            recommendations=recommendations,
            measurement_refs=tuple(item.measurement_id for item in measurements),
            revision=state.revision,
            applicability=applicability,
        )
        return _DetailedEvaluation(result=result, measurements=measurements, constraint=None)


class DecouplingVerifier:
    def __init__(self, engine: DecouplingEvaluationEngine | None = None) -> None:
        self._engine = engine or DecouplingEvaluationEngine()

    def verify(self, state: PCBEngineeringState, constraint_set: PCBConstraintSet, plan: PCBVerificationPlan) -> VerificationResult:
        return self._engine.evaluate(state, constraint_set, plan).result

    def verify_detailed(self, state: PCBEngineeringState, constraint_set: PCBConstraintSet, plan: PCBVerificationPlan) -> _DetailedEvaluation:
        return self._engine.evaluate(state, constraint_set, plan)


class DecouplingPreventEvaluator:
    def __init__(self, engine: DecouplingEvaluationEngine | None = None) -> None:
        self._engine = engine or DecouplingEvaluationEngine()

    def evaluate(
        self,
        state: PCBEngineeringState,
        constraint_set: PCBConstraintSet,
        candidate: PCBPlacementCandidate,
        plan: PCBVerificationPlan | None = None,
    ) -> PCBPreventEvaluation:
        _, _, _, VerificationStatus = _verification_types()
        checked_plan = plan or _plan_for_candidate(state, constraint_set)
        detailed = self._engine.evaluate(state, constraint_set, checked_plan, candidate=candidate)
        constraint = _decoupling_constraint(constraint_set)
        if detailed.result.status is VerificationStatus.PASS or detailed.result.status is VerificationStatus.NOT_APPLICABLE:
            status = PCBPreventStatus.ALLOW
        elif detailed.result.status is VerificationStatus.WARNING:
            status = PCBPreventStatus.WARN
        elif detailed.result.status is VerificationStatus.FAIL:
            status = PCBPreventStatus.BLOCK if constraint is not None and constraint.enforcement_mode is PCBConstraintEnforcementMode.HARD else PCBPreventStatus.WARN
        else:
            status = PCBPreventStatus.UNKNOWN
        reason = detailed.result.summary
        return PCBPreventEvaluation.create(
            project_id=state.project_id,
            constraint_id="missing" if constraint is None else constraint.constraint_id,
            candidate_id=candidate.candidate_id,
            state_fingerprint=state.fingerprint,
            constraint_set_fingerprint=constraint_set.fingerprint,
            affected_objects=detailed.result.affected_objects,
            measurements=detailed.measurements,
            status=status,
            reason=reason,
            evidence=detailed.result.evidence_refs,
            unknowns=detailed.result.unknowns,
        )


def mark_decoupling_stale(
    result: VerificationResult,
    *,
    reason: str,
    affected_objects: tuple[str, ...],
    required_reverification: tuple[str, ...] = ("DecouplingVerifier",),
) -> VerificationResult:
    _, _, VerificationResult, VerificationStatus = _verification_types()
    values = result.model_dump(mode="python", exclude={"fingerprint"})
    values.update(
        status=VerificationStatus.UNKNOWN,
        stale=True,
        stale_reason=reason,
        unknowns=tuple(dict.fromkeys((*result.unknowns, "STALE_INPUTS"))),
        affected_objects=tuple(dict.fromkeys((*result.affected_objects, *affected_objects))),
        required_reverification=tuple(dict.fromkeys((*result.required_reverification, *required_reverification))),
        summary="The previous DECOUPLING verification is stale and must be rerun.",
    )
    return VerificationResult.create(**values)


def _decoupling_constraint(constraint_set: PCBConstraintSet) -> PCBConstraint | None:
    values = tuple(item for item in constraint_set.constraints if item.constraint_type.value == "DECOUPLING")
    return values[0] if len(values) == 1 else None


def _plan_for_candidate(state: PCBEngineeringState, constraint_set: PCBConstraintSet) -> PCBVerificationPlan:
    return PCBVerificationPlan.create(
        project_id=state.project_id,
        revision=state.revision,
        pcb_state_fingerprint=state.fingerprint,
        constraint_set_fingerprint=constraint_set.fingerprint,
        required_verifiers=("DecouplingVerifier",),
        optional_verifiers=(),
        not_applicable_verifiers=(),
        unknown_applicability=(),
        evidence_refs=tuple(state.evidence_refs) + tuple(constraint_set.evidence_refs),
    )


def _evidence_objects(state: PCBEngineeringState, constraint_set: PCBConstraintSet) -> tuple[PCBFoundationEvidence, ...]:
    values = {item.evidence_id: item for item in (*state.evidence_refs, *constraint_set.evidence_refs)}
    for relation in state.decoupling_relations:
        values.update({item.evidence_id: item for item in relation.evidence_refs})
    return tuple(values[key] for key in sorted(values))


def _verification_evidence(item: PCBFoundationEvidence) -> VerificationEvidenceReference:
    VerificationEvidenceReference, _, _, _ = _verification_types()
    return VerificationEvidenceReference.create(
        evidence_id=item.evidence_id,
        source_kind=item.source_type.value,
        source_fingerprint=item.source_fingerprint,
        # The shared verification contract accepts a stable evidence locator,
        # while the original PCB locator remains available on the foundation
        # evidence itself.  Keep the cross-layer reference opaque and safe.
        locator=f"evidence:{item.evidence_id}",
        description=item.claim,
    )


def _pad(state: PCBEngineeringState, pad_id: str | None) -> PCBPadProjection | None:
    if pad_id is None:
        return None
    return next((pad for component in state.components for pad in component.pads if pad.pad_id == pad_id), None)


def _net(state: PCBEngineeringState, net_id: str | None) -> PCBNetProjection | None:
    return None if net_id is None else next((item for item in state.nets if item.net_id == net_id), None)


def _power_domain_check(state: PCBEngineeringState, relation: PCBDecouplingRelation, load: PCBPadProjection | None, cap: PCBPadProjection | None) -> bool | None:
    if relation.power_net_id is None or load is None or cap is None or load.net_id is None or cap.net_id is None:
        return None
    return load.net_id == relation.power_net_id and cap.net_id == relation.power_net_id


def _return_check(state: PCBEngineeringState, relation: PCBDecouplingRelation, pad: PCBPadProjection | None) -> bool | None:
    if relation.return_net_id is None or pad is None or pad.net_id is None:
        return None
    net = _net(state, pad.net_id)
    if net is None:
        return False
    return pad.net_id == relation.return_net_id and (
        next((item.signal_class for item in state.signal_classes if item.net_id == net.net_id), PCBSignalClass.UNKNOWN)
        is PCBSignalClass.GROUND
        or net.name.casefold() in {"gnd", "vss", "vssa", "agnd", "dgnd", "pgnd"}
    )


def _measure(
    state: PCBEngineeringState,
    relation: PCBDecouplingRelation,
    candidate_placements: tuple[PCBPlacementProjection, ...] | None,
) -> tuple[DecouplingMeasurement | None, str | None]:
    start = _point(state, relation.load_pad_id, relation.load_component_id, candidate_placements)
    end = _point(state, relation.capacitor_power_pad_id, relation.capacitor_component_id, candidate_placements)
    if start is None or end is None:
        return None, "MISSING_PAD_GEOMETRY"
    (start_x, start_y, _, start_quality), (end_x, end_y, _, end_quality) = start, end
    distance = math.hypot(start_x - end_x, start_y - end_y)
    quality = PCBMeasurementQuality.PAD_LEVEL if start_quality is PCBMeasurementQuality.PAD_LEVEL and end_quality is PCBMeasurementQuality.PAD_LEVEL else PCBMeasurementQuality.APPROXIMATE
    source = "PAD_COORDINATE" if quality is PCBMeasurementQuality.PAD_LEVEL else "COMPONENT_CENTER_FALLBACK"
    return DecouplingMeasurement.create(
        measurement_id=_stable_id("measurement", relation.relation_id, candidate_placements or "current"),
        relation_id=relation.relation_id,
        measurement_start=relation.load_pad_id or relation.load_component_id,
        measurement_end=relation.capacitor_power_pad_id or relation.capacitor_component_id,
        distance=distance,
        unit="mm",
        geometry_source=source,
        quality=quality,
    ), None


def _point(
    state: PCBEngineeringState,
    pad_id: str | None,
    component_id: str,
    candidate_placements: tuple[PCBPlacementProjection, ...] | None,
) -> tuple[float, float, str, PCBMeasurementQuality] | None:
    pad = _pad(state, pad_id)
    current = next((item for item in state.placement if item.component_id == component_id), None)
    if current is None:
        return None
    candidate = current if candidate_placements is None else next((item for item in candidate_placements if item.component_id == component_id), current)
    if candidate.rotation != current.rotation:
        return None
    if pad is not None and pad.x_mm is not None and pad.y_mm is not None:
        return pad.x_mm + candidate.x - current.x, pad.y_mm + candidate.y - current.y, "PAD", PCBMeasurementQuality.PAD_LEVEL
    return candidate.x, candidate.y, "COMPONENT_CENTER", PCBMeasurementQuality.APPROXIMATE


def _worst(statuses: list[VerificationStatus]) -> VerificationStatus:
    _, _, _, VerificationStatus = _verification_types()
    if VerificationStatus.FAIL in statuses:
        return VerificationStatus.FAIL
    if VerificationStatus.WARNING in statuses:
        return VerificationStatus.WARNING
    if VerificationStatus.UNKNOWN in statuses:
        return VerificationStatus.UNKNOWN
    return VerificationStatus.PASS


__all__ = (
    "DecouplingEvaluationEngine",
    "DecouplingMeasurement",
    "DecouplingPreventEvaluator",
    "DecouplingRelationshipResolver",
    "DecouplingVerifier",
    "PCBMeasurementQuality",
    "PCBPlacementCandidate",
    "PCBPreventEvaluation",
    "PCBPreventStatus",
    "mark_decoupling_stale",
)
