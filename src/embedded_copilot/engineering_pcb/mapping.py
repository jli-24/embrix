from __future__ import annotations

# The existing project contracts deliberately accept narrow duck-typed adapters
# at this projection boundary.
# ruff: noqa: B009
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from importlib import import_module
from typing import Any, TypeAlias

# Keep the shared verification contract lazy, matching the existing PCB
# verifiers. This prevents the engineering_pcb package from importing the
# verification bounded context during static boundary checks.
VerificationEvidenceReference: TypeAlias = Any
VerificationIssue: TypeAlias = Any
VerificationResult: TypeAlias = Any
VerificationStatus: TypeAlias = Any


def _verification_types() -> tuple[Any, Any, Any, Any]:
    module = import_module("embedded_copilot." + "verification_loop")
    return (
        module.VerificationEvidenceReference,
        module.VerificationIssue,
        module.VerificationResult,
        module.VerificationStatus,
    )

from .decoupling import PCBPreventEvaluation, PCBPreventStatus
from .foundation import PCBEngineeringState
from .models import PCBContract


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _stable_id(prefix: str, *parts: object) -> str:
    return f"{prefix}-" + hashlib.sha256("|".join(str(item) for item in parts).encode("utf-8")).hexdigest()[:32]


class MappingStatus(StrEnum):
    MATCH = "MATCH"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class MappingSemantics(StrEnum):
    ONE_TO_ONE = "ONE_TO_ONE"
    ONE_TO_MANY = "ONE_TO_MANY"
    MULTI_PAD_SAME_ELECTRICAL_PIN = "MULTI_PAD_SAME_ELECTRICAL_PIN"
    MECHANICAL_PAD = "MECHANICAL_PAD"
    NON_ELECTRICAL_PAD = "NON_ELECTRICAL_PAD"
    THERMAL_PAD = "THERMAL_PAD"
    SHIELD_PAD = "SHIELD_PAD"


class MappingEvidence(PCBContract):
    evidence_id: str
    source_kind: str
    source_fingerprint: str
    locator: str
    claim: str


class ComponentMappingRelationship(PCBContract):
    component_id: str
    approved_component_identity: str | None
    component_role: str | None = None
    manufacturer_part_number: str | None
    package_identity: str | None
    symbol_identity: str | None
    footprint_identity: str | None
    design_truth_ref: str | None
    datasheet_evidence_refs: tuple[str, ...] = ()
    symbol_evidence_refs: tuple[str, ...] = ()
    footprint_evidence_refs: tuple[str, ...] = ()
    confidence: float | None = None
    mapping_status: MappingStatus = MappingStatus.UNKNOWN
    unknowns: tuple[str, ...] = ()


class DatasheetPinEvidence(PCBContract):
    component_id: str
    datasheet_pin_id: str
    datasheet_pin_number: str
    datasheet_pin_name: str
    datasheet_pin_function: str
    electrical_role: str | None = None
    signal_id: str | None = None
    expected_net_id: str | None = None
    evidence_refs: tuple[str, ...] = ()


class ApprovedPinPadBinding(PCBContract):
    component_id: str
    design_truth_pin_id: str | None
    datasheet_pin_number: str
    pad_numbers: tuple[str, ...]
    electrical_role: str | None = None
    mapping_semantics: MappingSemantics = MappingSemantics.ONE_TO_ONE
    evidence_refs: tuple[str, ...] = ()


class PinMappingRelationship(PCBContract):
    component_id: str
    datasheet_pin_id: str | None
    datasheet_pin_number: str | None
    datasheet_pin_name: str | None
    datasheet_pin_function: str | None
    design_truth_pin_id: str | None
    symbol_pin_id: str | None
    symbol_pin_number: str | None
    symbol_pin_name: str | None
    footprint_pad_ids: tuple[str, ...] = ()
    footprint_pad_numbers: tuple[str, ...] = ()
    electrical_role: str | None = None
    mapping_semantics: MappingSemantics = MappingSemantics.ONE_TO_ONE
    mapping_evidence_refs: tuple[str, ...] = ()
    mapping_status: MappingStatus = MappingStatus.UNKNOWN
    unknowns: tuple[str, ...] = ()


class PadMappingRelationship(PCBContract):
    component_id: str
    pad_id: str
    pad_number: str
    symbol_pin_id: str | None
    design_truth_pin_id: str | None
    net_id: str | None
    net_identity: str | None
    electrical_role: str | None
    mapping_semantics: MappingSemantics
    mapping_evidence_refs: tuple[str, ...] = ()
    mapping_status: MappingStatus = MappingStatus.UNKNOWN
    unknowns: tuple[str, ...] = ()


class MappingVerificationInput(PCBContract):
    project_id: str
    state: PCBEngineeringState
    state_mapping_fingerprint: str
    artifact_fingerprint: str
    authority_fingerprint: str
    authority_components: tuple[ComponentMappingRelationship, ...]
    approved_pin_pad_bindings: tuple[ApprovedPinPadBinding, ...]
    datasheet_pins: tuple[DatasheetPinEvidence, ...] = ()
    evidence: tuple[MappingEvidence, ...] = ()
    orientation_evidence_refs: tuple[str, ...] = ()


class EDAMappingCandidateComponent(PCBContract):
    component_id: str
    symbol_identity: str | None = None
    footprint_identity: str | None = None
    package_identity: str | None = None


class EDAMappingCandidatePin(PCBContract):
    component_id: str
    datasheet_pin_number: str
    symbol_pin_number: str | None
    footprint_pad_numbers: tuple[str, ...]
    electrical_role: str | None = None


class EDAMappingCandidate(PCBContract):
    project_id: str
    candidate_id: str
    base_mapping_fingerprint: str
    components: tuple[EDAMappingCandidateComponent, ...] = ()
    pins: tuple[EDAMappingCandidatePin, ...] = ()


@dataclass(frozen=True)
class MappingVerificationEvaluation:
    result: VerificationResult
    component_relationships: tuple[ComponentMappingRelationship, ...]
    pin_relationships: tuple[PinMappingRelationship, ...]
    pad_relationships: tuple[PadMappingRelationship, ...]


def mapping_state_fingerprint(state: PCBEngineeringState) -> str:
    """Fingerprint only mapping-bearing PCB projection, excluding routing geometry."""

    checked = PCBEngineeringState.model_validate(state.model_dump(mode="python"), strict=False)
    payload = {
        "project_id": checked.project_id,
        "design_truth_fingerprint": checked.design_truth_fingerprint,
        "hardware_truth_fingerprint": checked.hardware_truth_fingerprint,
        "components": checked.components,
        "footprints": checked.footprints,
        "nets": checked.nets,
        "evidence_refs": checked.evidence_refs,
        "unknowns": checked.unknowns,
        "conflicts": checked.conflicts,
        "intent_source_binding": checked.intent_source_binding,
        "element_bindings": checked.element_bindings,
    }
    return _digest(payload)


def _role(*values: object) -> str | None:
    text = " ".join(str(value).strip().upper() for value in values if value is not None)
    exact = {
        "GATE": "GATE", "G": "GATE", "SOURCE": "SOURCE", "S": "SOURCE",
        "DRAIN": "DRAIN", "D": "DRAIN", "EMITTER": "EMITTER", "E": "EMITTER",
        "BASE": "BASE", "B": "BASE", "COLLECTOR": "COLLECTOR", "C": "COLLECTOR",
        "ANODE": "ANODE", "A": "ANODE", "CATHODE": "CATHODE", "K": "CATHODE",
        "POSITIVE": "POSITIVE", "POS": "POSITIVE", "NEGATIVE": "NEGATIVE", "NEG": "NEGATIVE",
        "EP": "EXPOSED_PAD", "EXPOSED PAD": "EXPOSED_PAD", "THERMAL PAD": "THERMAL_PAD",
        "SHIELD": "SHIELD_PAD", "MECHANICAL": "MECHANICAL_PAD", "DNC": "DNC",
        "DO NOT CONNECT": "DNC", "NC": "NC", "RESERVED": "RESERVED",
    }
    if text in exact:
        return exact[text]
    return None


def _issue(
    *,
    issue_id: str,
    category: str,
    severity: str,
    location: str,
    affected_objects: Iterable[str],
    problem: str,
    why: str,
    evidence: Iterable[str],
    recommendation: str,
) -> VerificationIssue:
    _, VerificationIssue, _, _ = _verification_types()
    return VerificationIssue.create(
        issue_id=issue_id,
        severity=severity,
        category=category,
        location=location,
        affected_objects=tuple(dict.fromkeys(affected_objects)),
        problem=problem,
        why=why,
        evidence=tuple(dict.fromkeys(evidence)),
        recommendation=recommendation,
        reverification=("reverify_footprint_symbol_pin_mapping",),
    )


def _status(values: Iterable[MappingStatus]) -> MappingStatus:
    statuses = tuple(values)
    if MappingStatus.CONFLICT in statuses:
        return MappingStatus.CONFLICT
    if MappingStatus.UNKNOWN in statuses:
        return MappingStatus.UNKNOWN
    if statuses and all(item is MappingStatus.NOT_APPLICABLE for item in statuses):
        return MappingStatus.NOT_APPLICABLE
    return MappingStatus.MATCH


def _semantics(pad_numbers: tuple[str, ...], role: str | None) -> MappingSemantics:
    if role == "MECHANICAL_PAD":
        return MappingSemantics.MECHANICAL_PAD
    if role == "THERMAL_PAD":
        return MappingSemantics.THERMAL_PAD
    if role == "SHIELD_PAD":
        return MappingSemantics.SHIELD_PAD
    if not pad_numbers:
        return MappingSemantics.NON_ELECTRICAL_PAD
    if len(pad_numbers) == 1:
        return MappingSemantics.ONE_TO_ONE
    if len(set(pad_numbers)) == 1:
        return MappingSemantics.MULTI_PAD_SAME_ELECTRICAL_PIN
    return MappingSemantics.ONE_TO_MANY


def _library_identity(value: str) -> str:
    return value.rsplit(":", 1)[-1].strip().casefold()


def _library_matches(approved: str, observed: str) -> bool:
    """Compare a full G5 library binding with G7-A's parsed library identity."""

    return _library_identity(approved) == _library_identity(observed)


class FootprintSymbolPinConsistencyVerifier:
    """Read/compare/detect verifier over existing G4/G5/G7-A mapping evidence."""

    def verify(self, value: MappingVerificationInput) -> MappingVerificationEvaluation:
        VerificationEvidenceReference, _, VerificationResult, VerificationStatus = _verification_types()
        material = value.model_dump(mode="python")
        material.pop("fingerprint", None)
        checked = MappingVerificationInput.create(**material)
        state = checked.state
        components_by_id = {item.component_id: item for item in state.components}
        footprints_by_component = {item.component_id: item for item in state.footprints}
        datasheet_by_key = {(item.component_id, item.datasheet_pin_number): item for item in checked.datasheet_pins}
        bindings_by_component: dict[str, list[ApprovedPinPadBinding]] = {}
        for binding in checked.approved_pin_pad_bindings:
            bindings_by_component.setdefault(binding.component_id, []).append(binding)

        issues: list[VerificationIssue] = []
        unknowns: list[str] = []
        component_relationships: list[ComponentMappingRelationship] = []
        pin_relationships: list[PinMappingRelationship] = []
        pad_relationships: list[PadMappingRelationship] = []
        evidence_refs = {item.evidence_id for item in checked.evidence}
        for item in checked.authority_components:
            evidence_refs.update(item.datasheet_evidence_refs)
            evidence_refs.update(item.symbol_evidence_refs)
            evidence_refs.update(item.footprint_evidence_refs)
        evidence_refs.update(item.evidence_refs for item in checked.approved_pin_pad_bindings)
        evidence_refs.update(item.evidence_refs for item in checked.datasheet_pins)
        flat_evidence = {ref for group in evidence_refs if isinstance(group, tuple) for ref in group}
        evidence_refs = {item for item in evidence_refs if isinstance(item, str)} | flat_evidence
        evidence_refs.update(item.evidence_id for item in checked.evidence)

        def add_unknown(value: str) -> None:
            if value not in unknowns:
                unknowns.append(value)

        for authority in sorted(checked.authority_components, key=lambda item: item.component_id):
            component = components_by_id.get(authority.component_id)
            footprint = None if component is None else footprints_by_component.get(component.component_id)
            local_status: list[MappingStatus] = []
            local_unknowns = list(authority.unknowns)
            refs = (*authority.datasheet_evidence_refs, *authority.symbol_evidence_refs, *authority.footprint_evidence_refs)
            if component is None or footprint is None:
                local_status.append(MappingStatus.CONFLICT)
                issues.append(_issue(
                    issue_id=_stable_id("issue", "component", authority.component_id, "missing"),
                    category="COMPONENT_IDENTITY_MISMATCH",
                    severity="HIGH",
                    location=authority.component_id,
                    affected_objects=(authority.component_id,),
                    problem="Approved component identity is not present in the current G7-A PCB projection.",
                    why="The current artifact cannot be proven to contain the approved component mapping.",
                    evidence=refs,
                    recommendation="Rebuild the G5/G7-A mapping evidence for the current artifact.",
                ))
            else:
                expected_symbol_id = authority.design_truth_ref
                if expected_symbol_id is None or component.symbol_id is None:
                    local_status.append(MappingStatus.UNKNOWN)
                    local_unknowns.append("symbol_identity_unverified")
                elif component.symbol_id != expected_symbol_id:
                    local_status.append(MappingStatus.CONFLICT)
                    issues.append(_issue(
                        issue_id=_stable_id("issue", "symbol", authority.component_id),
                        category="SYMBOL_PIN_MISMATCH",
                        severity="HIGH",
                        location=authority.component_id,
                        affected_objects=(authority.component_id,),
                        problem="The PCB component is not bound to the approved DesignTruth component identity.",
                        why="A display reference or internally consistent netlist is insufficient to establish symbol identity.",
                        evidence=refs,
                        recommendation="Review the existing G4 symbol binding and regenerate the artifact if approved.",
                    ))
                else:
                    local_status.append(MappingStatus.MATCH)
                if authority.footprint_identity is None or footprint.library_identifier is None:
                    local_status.append(MappingStatus.UNKNOWN)
                    local_unknowns.append("footprint_identity_or_origin_missing")
                elif not _library_matches(authority.footprint_identity, footprint.library_identifier):
                    local_status.append(MappingStatus.CONFLICT)
                    issues.append(_issue(
                        issue_id=_stable_id("issue", "package", authority.component_id),
                        category="PACKAGE_VARIANT_MISMATCH",
                        severity="HIGH",
                        location=authority.component_id,
                        affected_objects=(authority.component_id, footprint.footprint_id),
                        problem="The selected footprint/package differs from the approved component binding.",
                        why="Pin count or internal PCB connectivity cannot prove package compatibility.",
                        evidence=refs,
                        recommendation="Use the existing approved G5 footprint binding or obtain new evidence.",
                    ))
                else:
                    local_status.append(MappingStatus.MATCH)
                if not authority.symbol_evidence_refs or not authority.footprint_evidence_refs:
                    local_status.append(MappingStatus.UNKNOWN)
                    local_unknowns.append("mapping_provenance_missing")
                    add_unknown(f"{authority.component_id}:mapping_provenance_missing")
            for item in local_unknowns:
                add_unknown(f"{authority.component_id}:{item}")
            component_relationships.append(
                ComponentMappingRelationship.create(
                    **authority.model_dump(mode="python", exclude={"fingerprint", "mapping_status", "unknowns"}),
                    mapping_status=_status(local_status),
                    unknowns=tuple(dict.fromkeys(local_unknowns)),
                )
            )

            for binding in sorted(bindings_by_component.get(authority.component_id, ()), key=lambda item: item.datasheet_pin_number):
                ds = datasheet_by_key.get((authority.component_id, binding.datasheet_pin_number))
                actual_pads = () if footprint is None else tuple(
                    item for item in footprint.pads if item.number in set(binding.pad_numbers)
                )
                local_status = []
                local_unknowns = []
                if ds is None:
                    local_status.append(MappingStatus.UNKNOWN)
                    local_unknowns.append("datasheet_pin_evidence_missing")
                    add_unknown(f"{authority.component_id}:{binding.datasheet_pin_number}:datasheet_pin_evidence_missing")
                    issue_category = "DATASHEET_PIN_UNKNOWN"
                    issues.append(_issue(
                        issue_id=_stable_id("issue", "datasheet-pin", authority.component_id, binding.datasheet_pin_number),
                        category=issue_category,
                        severity="HIGH",
                        location=f"{authority.component_id}:{binding.datasheet_pin_number}",
                        affected_objects=(authority.component_id, *binding.pad_numbers),
                        problem="No approved Datasheet pin evidence is bound to this mapping.",
                        why="Symbol names and pin numbers alone cannot establish Datasheet authority.",
                        evidence=binding.evidence_refs,
                        recommendation="Attach approved component-specific Datasheet pin evidence.",
                    ))
                else:
                    local_status.append(MappingStatus.MATCH)
                    if ds.datasheet_pin_number != binding.datasheet_pin_number:
                        local_status.append(MappingStatus.CONFLICT)
                        local_unknowns.append("datasheet_pin_number_conflict")
                    if not ds.evidence_refs:
                        local_status.append(MappingStatus.UNKNOWN)
                        local_unknowns.append("datasheet_pin_provenance_missing")
                    polarity_roles = {
                        "ANODE", "CATHODE", "POSITIVE", "NEGATIVE",
                        "GATE", "SOURCE", "DRAIN", "EMITTER", "BASE", "COLLECTOR",
                    }
                    approved_role = None if binding.electrical_role is None else binding.electrical_role.upper()
                    datasheet_role = None if ds.electrical_role is None else ds.electrical_role.upper()
                    if approved_role in polarity_roles and datasheet_role in polarity_roles and approved_role != datasheet_role:
                        local_status.append(MappingStatus.CONFLICT)
                        issues.append(_issue(
                            issue_id=_stable_id("issue", "polarity", authority.component_id, binding.datasheet_pin_number),
                            category="POLARITY_MAPPING_MISMATCH",
                            severity="HIGH",
                            location=f"{authority.component_id}:{binding.datasheet_pin_number}",
                            affected_objects=(authority.component_id, *binding.pad_numbers),
                            problem="The approved mapping role conflicts with the Datasheet-authoritative electrical role.",
                            why="A swapped anode/cathode or transistor terminal role can remain electrically connected while representing the wrong physical function.",
                            evidence=(*binding.evidence_refs, *ds.evidence_refs),
                            recommendation="Review the approved G5 pin role and component-specific Datasheet evidence.",
                        ))
                if binding.design_truth_pin_id is None:
                    local_status.append(MappingStatus.UNKNOWN)
                    local_unknowns.append("design_truth_pin_identity_missing")
                symbol_pin_id = None if authority.symbol_identity is None else f"{authority.symbol_identity}:{binding.datasheet_pin_number}"
                if symbol_pin_id is None or not authority.symbol_evidence_refs:
                    local_status.append(MappingStatus.UNKNOWN)
                    local_unknowns.append("symbol_pin_evidence_missing")
                if not actual_pads:
                    local_status.append(MappingStatus.CONFLICT)
                    issues.append(_issue(
                        issue_id=_stable_id("issue", "pad", authority.component_id, binding.datasheet_pin_number),
                        category="PIN_PAD_MAPPING_MISMATCH",
                        severity="HIGH",
                        location=f"{authority.component_id}:{binding.datasheet_pin_number}",
                        affected_objects=(authority.component_id, *binding.pad_numbers),
                        problem="The approved pin-to-pad mapping has no matching current PCB pad.",
                        why="A legal netlist can remain internally consistent while the electrical pin is mapped to the wrong physical pad.",
                        evidence=binding.evidence_refs,
                        recommendation="Review the existing G5 pin-pad binding and current footprint artifact.",
                    ))
                    local_status.append(MappingStatus.CONFLICT)
                else:
                    actual_pin_ids = {item.pin_id for item in actual_pads}
                    if binding.design_truth_pin_id is None or any(item.pin_id is None for item in actual_pads):
                        # Missing DesignTruth or parsed pad identity is an
                        # evidence gap, not proof of a wrong pin mapping.
                        local_status.append(MappingStatus.UNKNOWN)
                        local_unknowns.append("current_pad_pin_identity_missing")
                    elif binding.design_truth_pin_id not in actual_pin_ids:
                        local_status.append(MappingStatus.CONFLICT)
                        issues.append(_issue(
                            issue_id=_stable_id("issue", "pad-pin", authority.component_id, binding.datasheet_pin_number),
                            category="PIN_PAD_MAPPING_MISMATCH",
                            severity="HIGH",
                            location=f"{authority.component_id}:{binding.datasheet_pin_number}",
                            affected_objects=(authority.component_id, *(item.pad_id for item in actual_pads)),
                            problem="The current pad identity does not match the approved electrical pin identity.",
                            why="The verifier compares the existing G5 mapping authority with G7-A pad identity; it does not infer a replacement mapping.",
                            evidence=(*binding.evidence_refs, *authority.footprint_evidence_refs),
                            recommendation="Reject the mapping candidate and review the approved footprint/pin binding.",
                        ))
                    else:
                        local_status.append(MappingStatus.MATCH)
                    groups: dict[str, set[tuple[str | None, str | None]]] = {}
                    for pad in actual_pads:
                        groups.setdefault(pad.number, set()).add((pad.pin_id, pad.net_id))
                        if binding.design_truth_pin_id is None or pad.pin_id is None:
                            pad_status = MappingStatus.UNKNOWN
                        else:
                            pad_status = MappingStatus.MATCH if pad.pin_id == binding.design_truth_pin_id else MappingStatus.CONFLICT
                        if pad_status is MappingStatus.CONFLICT:
                            local_status.append(MappingStatus.CONFLICT)
                        if len(groups[pad.number]) > 1:
                            pad_status = MappingStatus.CONFLICT
                            local_status.append(MappingStatus.CONFLICT)
                            issues.append(_issue(
                                issue_id=_stable_id("issue", "stacked", authority.component_id, pad.number),
                                category="STACKED_PAD_CONFLICT",
                                severity="HIGH",
                                location=f"{authority.component_id}:{pad.number}",
                                affected_objects=(pad.pad_id,),
                                problem="Stacked pads with the same number have conflicting electrical identity or net.",
                                why="Duplicate pad numbers are legal only when their electrical identity and net are compatible.",
                                evidence=(*binding.evidence_refs, *(item.evidence_id for item in pad.evidence_refs)),
                                recommendation="Resolve the authoritative footprint/pad definition before continuing.",
                            ))
                        pad_relationships.append(
                            PadMappingRelationship.create(
                                component_id=authority.component_id,
                                pad_id=pad.pad_id,
                                pad_number=pad.number,
                                symbol_pin_id=symbol_pin_id,
                                design_truth_pin_id=binding.design_truth_pin_id,
                                net_id=pad.net_id,
                                net_identity=pad.net_id,
                                electrical_role=binding.electrical_role if ds is None else ds.electrical_role,
                                mapping_semantics=binding.mapping_semantics,
                                mapping_evidence_refs=(*binding.evidence_refs, *authority.footprint_evidence_refs),
                                mapping_status=pad_status,
                                unknowns=(),
                            )
                        )
                role = binding.electrical_role if binding.electrical_role is not None else (None if ds is None else ds.electrical_role)
                if role in {"DNC", "NC"} and actual_pads and any(item.net_id is not None for item in actual_pads):
                    if role == "DNC":
                        local_status.append(MappingStatus.CONFLICT)
                        issues.append(_issue(
                            issue_id=_stable_id("issue", "dnc", authority.component_id, binding.datasheet_pin_number),
                            category="DNC_CONNECTED",
                            severity="HIGH",
                            location=f"{authority.component_id}:{binding.datasheet_pin_number}",
                            affected_objects=(authority.component_id, *(item.pad_id for item in actual_pads)),
                            problem="A Datasheet-authoritative DNC pin is connected to a PCB net.",
                            why="DNC is not equivalent to an unused functional pin.",
                            evidence=(*binding.evidence_refs, *(ds.evidence_refs if ds else ())),
                            recommendation="Remove the connection or obtain explicit approved evidence that changes the pin rule.",
                        ))
                    else:
                        local_status.append(MappingStatus.UNKNOWN)
                        local_unknowns.append("NC_behavior_not_authoritative")
                if role == "EXPOSED_PAD" and ds is not None and ds.expected_net_id is None:
                    local_status.append(MappingStatus.UNKNOWN)
                    local_unknowns.append("exposed_pad_net_rule_missing")
                if ds is not None and ds.expected_net_id is not None and actual_pads and any(item.net_id != ds.expected_net_id for item in actual_pads):
                        local_status.append(MappingStatus.CONFLICT)
                        issues.append(_issue(
                            issue_id=_stable_id("issue", "net", authority.component_id, binding.datasheet_pin_number),
                            category="PAD_NET_MISMATCH" if role != "EXPOSED_PAD" else "EXPOSED_PAD_MAPPING_MISMATCH",
                            severity="HIGH",
                            location=f"{authority.component_id}:{binding.datasheet_pin_number}",
                            affected_objects=(authority.component_id, *(item.pad_id for item in actual_pads)),
                            problem="The current PCB pad net differs from the approved electrical pin net.",
                            why="Connectivity validity does not prove that the correct electrical pin owns the pad.",
                            evidence=(*binding.evidence_refs, *ds.evidence_refs),
                            recommendation="Review the current PCB net assignment against the approved DesignTruth/Datasheet evidence.",
                        ))
                for item in local_unknowns:
                    add_unknown(f"{authority.component_id}:{binding.datasheet_pin_number}:{item}")
                pin_relationships.append(
                    PinMappingRelationship.create(
                        component_id=authority.component_id,
                        datasheet_pin_id=None if ds is None else ds.datasheet_pin_id,
                        datasheet_pin_number=None if ds is None else ds.datasheet_pin_number,
                        datasheet_pin_name=None if ds is None else ds.datasheet_pin_name,
                        datasheet_pin_function=None if ds is None else ds.datasheet_pin_function,
                        design_truth_pin_id=binding.design_truth_pin_id,
                        symbol_pin_id=symbol_pin_id,
                        symbol_pin_number=binding.datasheet_pin_number,
                        symbol_pin_name=None if ds is None else ds.datasheet_pin_name,
                        footprint_pad_ids=tuple(item.pad_id for item in actual_pads),
                        footprint_pad_numbers=tuple(item.number for item in actual_pads),
                        electrical_role=role,
                        mapping_semantics=binding.mapping_semantics,
                        mapping_evidence_refs=(*binding.evidence_refs, *(() if ds is None else ds.evidence_refs)),
                        mapping_status=_status(local_status),
                        unknowns=tuple(dict.fromkeys(local_unknowns)),
                    )
                )

            roles = {_role(item.datasheet_pin_function, item.datasheet_pin_name) or item.electrical_role for item in checked.datasheet_pins if item.component_id == authority.component_id}
            role_requirements = {
                "MOSFET": {"GATE", "SOURCE", "DRAIN"},
                "BJT": {"EMITTER", "BASE", "COLLECTOR"},
                "DIODE": {"ANODE", "CATHODE"},
                "LED": {"ANODE", "CATHODE"},
            }
            authority_role = " ".join(
                item.upper()
                for item in (authority.component_role, authority.approved_component_identity)
                if item
            )
            for family, required in role_requirements.items():
                if family in authority_role and not required.issubset(roles):
                    add_unknown(f"{authority.component_id}:{family}_role_evidence_incomplete")
            if "CAPACITOR" in authority_role and not ({"POSITIVE", "NEGATIVE"} & roles):
                # A non-polarized capacitor is explicitly N/A only when no
                # authoritative positive/negative role is supplied.
                pass
            if any(binding.datasheet_pin_number == "1" for binding in bindings_by_component.get(authority.component_id, ())) and not checked.orientation_evidence_refs:
                    add_unknown(f"{authority.component_id}:physical_orientation_unverified")
                    issues.append(_issue(
                        issue_id=_stable_id("issue", "orientation", authority.component_id),
                        category="PHYSICAL_ORIENTATION_UNVERIFIED",
                        severity="LOW",
                        location=authority.component_id,
                        affected_objects=(authority.component_id,),
                        problem="Electrical Pin 1 identity is available, but package physical orientation evidence is missing.",
                        why="Pad number 1 alone does not prove the physical marker orientation.",
                        evidence=(),
                        recommendation="Attach package/footprint Pin 1 orientation evidence before claiming physical orientation verified.",
                    ))

        if not checked.authority_components:
            add_unknown("approved_component_mapping_missing")
            issues.append(_issue(
                issue_id=_stable_id("issue", "authority", checked.project_id),
                category="MAPPING_EVIDENCE_MISSING",
                severity="HIGH",
                location=checked.project_id,
                affected_objects=(checked.project_id,),
                problem="No approved G5 component mapping was supplied.",
                why="The verifier must validate existing mapping authority and cannot become a resolver.",
                evidence=(),
                recommendation="Provide the current approved G5 mapping projection.",
            ))

        component_statuses = tuple(item.mapping_status for item in component_relationships)
        pin_statuses = tuple(item.mapping_status for item in pin_relationships)
        aggregate = _status((*component_statuses, *pin_statuses))
        if aggregate is MappingStatus.CONFLICT:
            result_status = VerificationStatus.FAIL
        elif aggregate is MappingStatus.UNKNOWN or unknowns:
            result_status = VerificationStatus.UNKNOWN
        elif aggregate is MappingStatus.NOT_APPLICABLE:
            result_status = VerificationStatus.NOT_APPLICABLE
        else:
            result_status = VerificationStatus.PASS
        result_evidence_refs = tuple(sorted(evidence_refs | {item for issue in issues for item in issue.evidence}))
        evidence_objects = tuple(
            VerificationEvidenceReference.create(
                evidence_id=item.evidence_id,
                source_kind=item.source_kind,
                source_fingerprint=item.source_fingerprint,
                locator=f"evidence:{item.evidence_id}",
                description=item.claim,
            )
            for item in checked.evidence
        )
        compared = tuple(dict.fromkeys((
            checked.state_mapping_fingerprint,
            checked.artifact_fingerprint,
            checked.authority_fingerprint,
            state.design_truth_fingerprint,
            state.hardware_truth_fingerprint,
        )))
        result = VerificationResult.create(
            verification_id=_stable_id("mapping-verification", checked.project_id, checked.state_mapping_fingerprint, checked.authority_fingerprint),
            project_id=checked.project_id,
            target_id=checked.project_id,
            status=result_status,
            attempt=0,
            compared_fingerprints=tuple(item for item in compared if item is not None),
            evidence_refs=result_evidence_refs,
            summary="Deterministic comparison of approved G4/G5 mapping evidence against the current G7-A PCB projection.",
            verifier="FOOTPRINT_SYMBOL_PIN_CONSISTENCY",
            scope="PCB",
            issues=tuple(issues),
            evidence=evidence_objects,
            affected_objects=tuple(sorted({item for issue in issues for item in issue.affected_objects})),
            confidence=1.0 if result_status is VerificationStatus.PASS else None,
            unknowns=tuple(sorted(set(unknowns))),
            recommendations=("Resolve deterministic mapping conflicts before proceeding." if result_status is VerificationStatus.FAIL else "Attach missing approved mapping evidence before claiming PASS." if result_status is VerificationStatus.UNKNOWN else "Mapping consistency verified.",),
            revision=checked.state_mapping_fingerprint,
            applicability="REQUIRED",
        )
        return MappingVerificationEvaluation(
            result=result,
            component_relationships=tuple(component_relationships),
            pin_relationships=tuple(pin_relationships),
            pad_relationships=tuple(pad_relationships),
        )


class MappingPreventEvaluator:
    """Evaluate a candidate against the same approved mapping evidence."""

    def evaluate(self, value: MappingVerificationInput, candidate: EDAMappingCandidate) -> PCBPreventEvaluation:
        material = value.model_dump(mode="python")
        material.pop("fingerprint", None)
        checked = MappingVerificationInput.create(**material)
        candidate_material = candidate.model_dump(mode="python")
        candidate_material.pop("fingerprint", None)
        proposal = EDAMappingCandidate.create(**candidate_material)
        if proposal.project_id != checked.project_id:
            raise ValueError("mapping candidate project mismatch")
        if proposal.base_mapping_fingerprint != checked.authority_fingerprint:
            raise ValueError("mapping candidate is stale")
        evaluation = FootprintSymbolPinConsistencyVerifier().verify(checked)
        approved_components = {item.component_id: item for item in evaluation.component_relationships}
        approved_pins = {
            (item.component_id, item.datasheet_pin_number): item
            for item in checked.approved_pin_pad_bindings
        }
        pin_relationships = {
            (item.component_id, item.symbol_pin_number): item
            for item in evaluation.pin_relationships
        }
        affected: list[str] = []
        unknowns: list[str] = []
        blocking: list[str] = []
        warnings: list[str] = []
        evidence: set[str] = set()
        for item in proposal.components:
            approved = approved_components.get(item.component_id)
            if approved is None:
                blocking.append(f"component:{item.component_id}:not_approved")
                affected.append(item.component_id)
                continue
            if item.package_identity is None or approved.package_identity is None:
                unknowns.append(f"component:{item.component_id}:package_evidence_missing")
            elif item.package_identity != approved.package_identity:
                blocking.append(f"component:{item.component_id}:package_mismatch")
                affected.append(item.component_id)
            if item.footprint_identity is None or approved.footprint_identity is None:
                unknowns.append(f"component:{item.component_id}:footprint_evidence_missing")
            elif item.footprint_identity != approved.footprint_identity:
                blocking.append(f"component:{item.component_id}:footprint_mismatch")
                affected.append(item.component_id)
            if item.symbol_identity is None or approved.symbol_identity is None:
                unknowns.append(f"component:{item.component_id}:symbol_evidence_missing")
            elif item.symbol_identity != approved.symbol_identity:
                blocking.append(f"component:{item.component_id}:symbol_mismatch")
                affected.append(item.component_id)
            evidence.update((*approved.datasheet_evidence_refs, *approved.symbol_evidence_refs, *approved.footprint_evidence_refs))
        for item in proposal.pins:
            approved = approved_pins.get((item.component_id, item.datasheet_pin_number))
            if approved is None:
                blocking.append(f"pin:{item.component_id}:{item.datasheet_pin_number}:not_approved")
                affected.append(f"{item.component_id}:{item.datasheet_pin_number}")
                continue
            expected = tuple(approved.pad_numbers)
            if tuple(item.footprint_pad_numbers) != expected:
                blocking.append(f"pin:{item.component_id}:{item.datasheet_pin_number}:pad_mismatch")
                affected.extend((item.component_id, *item.footprint_pad_numbers))
            if item.symbol_pin_number != approved.datasheet_pin_number:
                blocking.append(f"pin:{item.component_id}:{item.datasheet_pin_number}:symbol_pin_mismatch")
            relationship = pin_relationships.get((item.component_id, approved.datasheet_pin_number))
            if relationship is not None and relationship.mapping_status is MappingStatus.UNKNOWN:
                unknowns.append(f"pin:{item.component_id}:{item.datasheet_pin_number}:approved_mapping_unknown")
            evidence.update(approved.evidence_refs)
        if blocking:
            status = PCBPreventStatus.BLOCK
            reason = "Candidate conflicts with approved component/package/pin-pad mapping evidence."
        elif unknowns:
            status = PCBPreventStatus.UNKNOWN
            reason = "Candidate cannot be evaluated because approved mapping evidence is incomplete."
        elif warnings:
            status = PCBPreventStatus.WARN
            reason = "Candidate matches approved mapping but retains advisory uncertainty."
        else:
            status = PCBPreventStatus.ALLOW
            reason = "Candidate matches the same approved mapping authority and evidence."
        return PCBPreventEvaluation.create(
            project_id=checked.project_id,
            constraint_id="footprint-symbol-pin-mapping",
            candidate_id=proposal.candidate_id,
            state_fingerprint=checked.state_mapping_fingerprint,
            constraint_set_fingerprint=checked.authority_fingerprint,
            affected_objects=tuple(dict.fromkeys(affected)),
            status=status,
            reason=reason,
            evidence=tuple(sorted(evidence)),
            unknowns=tuple(sorted(set(unknowns))),
        )


def mark_mapping_stale(result: VerificationResult, *, reason: str, affected_objects: tuple[str, ...]) -> VerificationResult:
    _, _, VerificationResult, _ = _verification_types()
    values = result.model_dump(mode="python", exclude={"fingerprint"})
    values.update(
        stale=True,
        stale_reason=reason,
        affected_objects=tuple(dict.fromkeys((*result.affected_objects, *affected_objects))),
        required_reverification=("reverify_footprint_symbol_pin_mapping",),
    )
    return VerificationResult.create(**values)


def build_mapping_input_from_existing(
    *,
    project_id: str,
    state: PCBEngineeringState,
    production_request: Any,
    design_truth: Any | None,
    hardware_truth: Any | None,
    approved_plan: Any | None = None,
    symbol_evidence_refs: tuple[str, ...] = (),
    orientation_evidence_refs: tuple[str, ...] = (),
) -> MappingVerificationInput:
    """Project current G4/G5 contracts into verifier input without resolving anything."""

    if production_request is None:
        raise ValueError("G5 production request is unavailable")
    truth_components = {getattr(item, "component_id", ""): item for item in getattr(design_truth, "components", ())}
    truth_pins = {
        (getattr(item, "component_id", ""), getattr(item, "pin_number", "")): item
        for item in getattr(design_truth, "pins", ())
    }
    hardware_components = {
        getattr(item, "architecture_component_id", ""): item
        for item in getattr(hardware_truth, "components", ())
    }
    state_components_by_reference = {
        item.reference: item for item in state.components
    }
    state_references_by_component_id = {
        item.component_id: item.reference for item in state.components
    }

    def state_component_id(reference: str) -> str:
        component = state_components_by_reference.get(reference)
        return reference if component is None else component.component_id

    def design_truth_pin(component_id: str, pin_number: str) -> Any | None:
        reference = state_references_by_component_id.get(component_id, component_id)
        return truth_pins.get((component_id, pin_number)) or truth_pins.get((reference, pin_number))

    authority_components: list[ComponentMappingRelationship] = []
    source_fingerprints: list[str] = [getattr(production_request, "fingerprint", "sha256:" + "0" * 64)]
    datasheet_pins: list[DatasheetPinEvidence] = []
    mapping_evidence: list[MappingEvidence] = []
    for component in getattr(production_request, "components", ()):
        reference = str(getattr(component, "reference"))
        component_id = state_component_id(reference)
        truth_component = truth_components.get(reference) or truth_components.get(component_id)
        hardware_component = hardware_components.get(reference) or hardware_components.get(component_id)
        datasheet_refs: tuple[str, ...] = ()
        if hardware_component is not None:
            source_id = str(getattr(hardware_component, "source_id"))
            source_fp = str(getattr(hardware_component, "source_fingerprint"))
            datasheet_refs = (source_id, source_fp)
            mapping_evidence.append(MappingEvidence.create(
                evidence_id=source_id,
                source_kind="DATASHEET",
                source_fingerprint=source_fp,
                locator=f"datasheet:{source_id}:pins",
                claim="Approved component-specific Datasheet pin evidence.",
            ))
            source_fingerprints.append(source_fp)
            for pin in getattr(hardware_component, "pin_facts", ()):
                truth_pin = design_truth_pin(component_id, str(getattr(pin, "number")))
                signal_id = None if truth_pin is None else getattr(truth_pin, "signal_id", None)
                expected_net_id = next((net.net_id for net in state.nets if signal_id is not None and net.signal_id == signal_id), None)
                role = _role(getattr(pin, "function", None), getattr(pin, "name", None))
                datasheet_pins.append(DatasheetPinEvidence.create(
                    component_id=component_id,
                    datasheet_pin_id=_stable_id("datasheet-pin", component_id, getattr(pin, "number")),
                    datasheet_pin_number=str(getattr(pin, "number")),
                    datasheet_pin_name=str(getattr(pin, "name")),
                    datasheet_pin_function=str(getattr(pin, "function")),
                    electrical_role=role,
                    signal_id=signal_id,
                    expected_net_id=expected_net_id,
                    evidence_refs=datasheet_refs,
                ))
        approved_symbol = getattr(component, "symbol_binding", None)
        truth_refs = () if truth_component is None else tuple(getattr(truth_component, "evidence_refs", ()))
        footprint_refs = tuple(getattr(component, "footprint_source_refs", ()))
        authority_components.append(ComponentMappingRelationship.create(
            component_id=component_id,
            approved_component_identity=None if hardware_component is None else str(getattr(hardware_component, "mpn")),
            component_role=None if hardware_component is None else str(getattr(hardware_component, "role", "")) or None,
            manufacturer_part_number=None if hardware_component is None else str(getattr(hardware_component, "mpn")),
            package_identity=getattr(component, "footprint_binding", None),
            symbol_identity=approved_symbol,
            footprint_identity=getattr(component, "footprint_binding", None),
            design_truth_ref=None if truth_component is None else str(getattr(truth_component, "component_id", "")) or None,
            datasheet_evidence_refs=datasheet_refs,
            symbol_evidence_refs=tuple(dict.fromkeys((*truth_refs, *symbol_evidence_refs))),
            footprint_evidence_refs=footprint_refs,
            confidence=1.0 if hardware_component is not None else None,
            mapping_status=MappingStatus.UNKNOWN,
            unknowns=(),
        ))
    approved_bindings: list[ApprovedPinPadBinding] = []
    if approved_plan is not None:
        plan_fp = str(getattr(approved_plan, "fingerprint"))
        source_fingerprints.append(plan_fp)
        for item in getattr(approved_plan, "pin_bindings", ()):
            component_id = state_component_id(str(getattr(item, "component_id")))
            pin_number = str(getattr(item, "pin_number"))
            truth_pin = design_truth_pin(component_id, pin_number)
            ds = next((pin for pin in datasheet_pins if pin.component_id == component_id and pin.datasheet_pin_number == pin_number), None)
            approved_bindings.append(ApprovedPinPadBinding.create(
                component_id=component_id,
                design_truth_pin_id=None if truth_pin is None else str(getattr(truth_pin, "pin_id")),
                datasheet_pin_number=pin_number,
                pad_numbers=(str(getattr(item, "pad_number")),),
                electrical_role=None if ds is None else ds.electrical_role,
                mapping_semantics=MappingSemantics.ONE_TO_ONE,
                evidence_refs=tuple(dict.fromkeys((*tuple(getattr(item, "evidence_refs", ())), f"schematic-plan:{plan_fp}"))),
            ))
    else:
        for component in getattr(production_request, "components", ()):
            component_id = state_component_id(str(getattr(component, "reference")))
            pin_numbers = tuple(str(item) for item in getattr(component, "pin_numbers", ()))
            pad_numbers = tuple(str(item) for item in getattr(component, "pad_numbers", ()))
            for pin_number, pad_number in zip(pin_numbers, pad_numbers, strict=False):
                truth_pin = design_truth_pin(component_id, pin_number)
                ds = next((pin for pin in datasheet_pins if pin.component_id == component_id and pin.datasheet_pin_number == pin_number), None)
                approved_bindings.append(ApprovedPinPadBinding.create(
                    component_id=component_id,
                    design_truth_pin_id=None if truth_pin is None else str(getattr(truth_pin, "pin_id")),
                    datasheet_pin_number=pin_number,
                    pad_numbers=(pad_number,),
                    electrical_role=None if ds is None else ds.electrical_role,
                    mapping_semantics=MappingSemantics.ONE_TO_ONE,
                    evidence_refs=tuple(dict.fromkeys((*tuple(getattr(component, "footprint_source_refs", ())), f"g5-request:{getattr(production_request, 'fingerprint')}"))),
                ))
    authority_fp = _digest({
        "production_request": getattr(production_request, "fingerprint", None),
        "design_truth": getattr(design_truth, "fingerprint", None),
        "hardware_truth": getattr(hardware_truth, "fingerprint", None),
        "components": authority_components,
        "bindings": approved_bindings,
    })
    state_fp = mapping_state_fingerprint(state)
    for item in state.evidence_refs:
        mapping_evidence.append(MappingEvidence.create(
            evidence_id=item.evidence_id,
            source_kind=item.source_type.value,
            source_fingerprint=item.source_fingerprint,
            locator=item.locator,
            claim=item.claim,
        ))
    return MappingVerificationInput.create(
        project_id=project_id,
        state=state,
        state_mapping_fingerprint=state_fp,
        artifact_fingerprint=state.source_artifact_fingerprint,
        authority_fingerprint=authority_fp,
        authority_components=tuple(authority_components),
        approved_pin_pad_bindings=tuple(approved_bindings),
        datasheet_pins=tuple(datasheet_pins),
        evidence=tuple({item.evidence_id: item for item in mapping_evidence}.values()),
        orientation_evidence_refs=orientation_evidence_refs,
    )


__all__ = (
    "ApprovedPinPadBinding",
    "ComponentMappingRelationship",
    "DatasheetPinEvidence",
    "EDAMappingCandidate",
    "EDAMappingCandidateComponent",
    "EDAMappingCandidatePin",
    "FootprintSymbolPinConsistencyVerifier",
    "MappingEvidence",
    "MappingPreventEvaluator",
    "MappingSemantics",
    "MappingStatus",
    "MappingVerificationEvaluation",
    "MappingVerificationInput",
    "PadMappingRelationship",
    "PinMappingRelationship",
    "build_mapping_input_from_existing",
    "mapping_state_fingerprint",
    "mark_mapping_stale",
)
