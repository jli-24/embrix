from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime
from typing import Any

from .contracts import (
    PCBBoardDefinition,
    PCBComponentTruth,
    PCBEndpointTruth,
    PCBFootprint,
    PCBFootprintAvailabilityPort,
    PCBNetAssignment,
    PCBNetTruth,
    PCBPlacementConstraint,
    PCBProductionRequest,
    PCBProductionState,
    PCBProductionStatus,
    PCBProposal,
    PCBProposalDecision,
    PCBProposalDecisionStatus,
    PCBReadinessResult,
    PCBReadinessStatus,
    PCBRouteSegment,
    PCBRoutingConstraint,
)
from .geometry import pad_world_position


class PCBProductionService:
    """Deterministic readiness and proposal service; it never mutates a workspace."""

    def __init__(self, *, footprints: PCBFootprintAvailabilityPort, plane_nets: frozenset[str] = frozenset()) -> None:
        if not callable(getattr(footprints, "is_available", None)):
            raise TypeError("footprint availability port is required")
        if not isinstance(plane_nets, frozenset) or not all(isinstance(item, str) and item for item in plane_nets):
            raise TypeError("plane_nets must be a frozenset of non-empty net names")
        self._footprints = footprints
        self._plane_nets = plane_nets

    def evaluate_readiness(self, request: PCBProductionRequest) -> PCBReadinessResult:
        checked = PCBProductionRequest.model_validate(request.model_dump(mode="python"), strict=False)
        status = PCBReadinessStatus.READY
        missing: list[str] = []
        affected_components: list[str] = []
        affected_nets: list[str] = []
        if not (checked.real_schematic_exists and checked.schematic_hash_matches_artifact and checked.schematic_erc_executed and checked.schematic_erc_status == "ERC_PASS"):
            status = PCBReadinessStatus.BLOCKED_SCHEMATIC_NOT_VERIFIED
            missing.append("real schematic artifact and ERC PASS")
        elif any(item.footprint_binding is None for item in checked.components):
            status = PCBReadinessStatus.BLOCKED_MISSING_FOOTPRINT
            affected_components.extend(item.reference for item in checked.components if item.footprint_binding is None)
            missing.append("footprint binding")
        elif any(not self._footprints.is_available(item.footprint_binding or "") for item in checked.components):
            status = PCBReadinessStatus.BLOCKED_FOOTPRINT_UNAVAILABLE
            affected_components.extend(item.reference for item in checked.components if not self._footprints.is_available(item.footprint_binding or ""))
            missing.append("KiCad footprint availability")
        elif any(set(item.pin_numbers) - set(item.pad_numbers) for item in checked.components):
            status = PCBReadinessStatus.BLOCKED_PIN_PAD_MISMATCH
            affected_components.extend(item.reference for item in checked.components if set(item.pin_numbers) - set(item.pad_numbers))
            missing.append("schematic pin to footprint pad compatibility")
        elif any(
            (endpoint.reference, endpoint.pin_number)
            not in {
                (component.reference, pin)
                for component in checked.components
                for pin in component.pin_numbers
            }
            for net in checked.nets
            for endpoint in net.endpoints
        ):
            status = PCBReadinessStatus.BLOCKED_AMBIGUOUS_DESIGN_TRUTH
            missing.append("net endpoints bound to schematic pins")
        elif checked.board.width_mm <= 0 or checked.board.height_mm <= 0 or not checked.board.source_refs:
            status = PCBReadinessStatus.BLOCKED_MISSING_BOARD_OUTLINE
            missing.append("board outline")
        elif checked.board.layer_count != 2 or checked.board.copper_layers != ("F.Cu", "B.Cu"):
            status = PCBReadinessStatus.BLOCKED_UNSUPPORTED_STACKUP
            missing.append("supported 2-layer stackup")
        elif checked.board.outline_kind != "RECTANGLE":
            status = PCBReadinessStatus.BLOCKED_UNSUPPORTED_BOARD_GEOMETRY
            missing.append("rectangular board outline")
        elif not checked.routing.source_refs:
            status = PCBReadinessStatus.BLOCKED_MISSING_ROUTING_RULE
            missing.append("routing rules")
        elif {item.reference for item in checked.placements} != {item.reference for item in checked.components}:
            status = PCBReadinessStatus.BLOCKED_MISSING_PLACEMENT_FACT
            missing.append("placement facts for every component")
            affected_components.extend(sorted({item.reference for item in checked.components} - {item.reference for item in checked.placements}))
        elif any(item.side != "F.Cu" for item in checked.placements):
            status = PCBReadinessStatus.BLOCKED_UNSUPPORTED_CONSTRAINT
            missing.append("front-side-only placement")
        elif any(not _inside_board(checked.board, item.x_mm, item.y_mm) for item in checked.placements):
            status = PCBReadinessStatus.BLOCKED_UNSUPPORTED_CONSTRAINT
            missing.append("placement inside board outline")
        elif {item.net_name for item in checked.routes} != {item.name for item in checked.nets}:
            status = PCBReadinessStatus.BLOCKED_UNSUPPORTED_CONSTRAINT
            missing.append("deterministic route for every net")
            affected_nets.extend(sorted({item.name for item in checked.nets} - {item.net_name for item in checked.routes}))
        elif any(
            route.width_mm != checked.routing.trace_width_mm
            or any(not _inside_board(checked.board, x, y) for x, y in route.points)
            for route in checked.routes
        ):
            status = PCBReadinessStatus.BLOCKED_UNSUPPORTED_CONSTRAINT
            missing.append("route geometry and trace width")
        elif not _routes_bind_to_pads(
            checked,
            pad_world_position_provider=getattr(self._footprints, "pad_world_position", None),
        ):
            status = PCBReadinessStatus.BLOCKED_UNSUPPORTED_CONSTRAINT
            missing.append("route endpoints bound to footprint pads")
        elif _has_same_layer_collision(checked.routes, checked.routing.clearance_mm, excluded_net_names=self._plane_nets):
            status = PCBReadinessStatus.BLOCKED_UNSUPPORTED_CONSTRAINT
            missing.append("same-layer route clearance")
        action = "create structured PCB proposal" if status is PCBReadinessStatus.READY else "provide or correct the missing engineering facts"
        return PCBReadinessResult.create(
            status=status,
            missing_facts=tuple(missing),
            affected_components=tuple(sorted(set(affected_components))),
            affected_nets=tuple(sorted(set(affected_nets))),
            source_refs=checked.source_refs,
            required_next_action=("obtain a real schematic ERC PASS" if status is PCBReadinessStatus.BLOCKED_SCHEMATIC_NOT_VERIFIED else action),
            schematic_artifact_fingerprint=checked.schematic_artifact_fingerprint,
            design_truth_fingerprint=checked.design_truth_fingerprint,
            hardware_design_fingerprint=checked.hardware_design_fingerprint,
        )

    def create_proposal(self, request: PCBProductionRequest) -> PCBProposal:
        checked = PCBProductionRequest.model_validate(request.model_dump(mode="python"), strict=False)
        readiness = self.evaluate_readiness(checked)
        if readiness.status is not PCBReadinessStatus.READY:
            raise ValueError(f"PCB readiness is {readiness.status.value}: {readiness.missing_facts}")
        placements = {item.reference: item for item in checked.placements}
        footprints = tuple(
            PCBFootprint.create(
                component_ref=item.reference,
                footprint_binding=item.footprint_binding,
                pad_numbers=item.pad_numbers,
                placement=placements[item.reference],
            )
            for item in sorted(checked.components, key=lambda value: value.reference)
        )
        assignments = tuple(
            PCBNetAssignment.create(net_name=item.name, net_class=item.net_class, endpoints=item.endpoints)
            for item in sorted(checked.nets, key=lambda value: value.name)
        )
        proposal_id = "pcb-proposal-" + hashlib.sha256((checked.request_id + checked.fingerprint).encode()).hexdigest()[:32]
        return PCBProposal.create(
            proposal_id=proposal_id,
            request_id=checked.request_id,
            project_id=checked.project_id,
            target_relative_path=checked.target_relative_path,
            board=checked.board,
            footprints=footprints,
            net_assignments=assignments,
            routing=tuple(sorted(checked.routes, key=lambda value: (value.net_name, value.fingerprint))),
            vias=(), zones=(), keepouts=(),
            source_refs=checked.source_refs,
            verification_refs=(checked.schematic_erc_result_id, checked.schematic_receipt_id),
            schematic_fingerprint=checked.schematic_artifact_fingerprint,
            schematic_connectivity_fingerprint=checked.schematic_connectivity_fingerprint,
            design_truth_fingerprint=checked.design_truth_fingerprint,
            hardware_design_fingerprint=checked.hardware_design_fingerprint,
            authorization_fingerprint=checked.authorization_fingerprint,
            pcb_state_fingerprint=checked.pcb_state_fingerprint,
            pcb_source_binding_fingerprint=checked.pcb_source_binding_fingerprint,
            pcb_constraint_set_fingerprint=checked.pcb_constraint_set_fingerprint,
            pcb_verification_plan_fingerprint=checked.pcb_verification_plan_fingerprint,
            pcb_element_binding_fingerprint=checked.pcb_element_binding_fingerprint,
        )

    @staticmethod
    def build_request_from_g4(
        *,
        schematic_production: object,
        schematic_plan: object,
        design_truth: object,
        hardware_selection: object,
        hardware_design: object,
        implementation: object,
        authorization: object,
        schematic_artifact_id: str,
        schematic_artifact_fingerprint: str,
        schematic_content_fingerprint: str,
        workspace_identity: str,
        target_relative_path: str,
        board: PCBBoardDefinition,
        placements: tuple[PCBPlacementConstraint, ...],
        routes: tuple[PCBRouteSegment, ...],
        routing: PCBRoutingConstraint,
        requester: str,
        requested_at: datetime,
        source_request_id: str | None = None,
        trace_id: str | None = None,
    ) -> PCBProductionRequest:
        """Project the current G4 authority into a typed PCB request.

        Electrical identity is read exclusively from the approved DesignTruth
        and schematic plan.  Board, placement, and routing are separate typed
        physical facts supplied by the project/user boundary.  This function
        deliberately refuses to derive missing dimensions, footprints, pads,
        or connectivity from names or model text.
        """

        def value(source: object, name: str, *, required: bool = True) -> Any:
            result = getattr(source, name, None)
            if required and result is None:
                raise ValueError(f"G5 authoritative field is missing: {name}")
            return result

        def state_value(source: object, name: str) -> str:
            if isinstance(source, str):
                return source
            result = value(source, name)
            return getattr(result, "value", result)

        production = schematic_production
        generation = value(production, "generation_receipt")
        erc = value(production, "erc_receipt")
        erc_result = value(erc, "result")
        erc_gate = value(production, "erc_gate_status")
        if state_value(production, "status") != "GENERATED":
            raise ValueError("G5 requires the current generated G4 schematic state")
        if state_value(generation, "validation_status") != "PARSE_VERIFIED" or value(generation, "parse_validation") is None:
            raise ValueError("G5 requires a parse-verified schematic artifact")
        if state_value(erc_gate, "value") != "PASS" or state_value(erc_result, "status") != "ERC_PASS":
            raise ValueError("G5 requires current G4 ERC PASS")
        if value(generation, "artifact_fingerprint") != schematic_artifact_fingerprint:
            raise ValueError("G4 schematic artifact fingerprint is stale")

        truth = design_truth
        if state_value(truth, "status") != "APPROVED" or state_value(truth, "origin") == "REVERSE":
            raise ValueError("G5 requires approved non-reverse DesignTruth")
        selection = hardware_selection
        if state_value(selection, "status") != "APPROVED":
            raise ValueError("G5 requires approved HardwareTruth and BOM")
        hardware_truth = value(selection, "hardware_truth")
        bom = value(selection, "bom")
        approval = value(selection, "approval")
        if state_value(approval, "decision") != "APPROVED":
            raise ValueError("G5 requires approved BOM evidence")
        if value(bom, "fingerprint") != value(hardware_truth, "bom_fingerprint"):
            raise ValueError("HardwareTruth/BOM binding is stale")

        auth_status = state_value(authorization, "status")
        if auth_status != "AUTHORIZED":
            raise ValueError("G5 artifact creation authorization is unavailable")
        schematic_request = value(production, "request")
        authorization_request_id = value(schematic_request, "authorization_request_id")
        auth_requests = tuple(value(authorization, "requests"))
        auth_request = next((item for item in auth_requests if value(item, "request_id") == authorization_request_id), None)
        if auth_request is None or state_value(auth_request, "capability_id") != "ARTIFACT_CREATION" or state_value(auth_request, "status") != "AUTHORIZED":
            raise ValueError("G5 artifact creation authorization request is stale")
        auth_decisions = tuple(value(authorization, "decisions"))
        auth_decision = next((item for item in auth_decisions if value(item, "request_id") == authorization_request_id), None)
        if auth_decision is None or state_value(auth_decision, "decision") != "APPROVED":
            raise ValueError("G5 artifact creation approval is unavailable")

        approved_plan = value(schematic_plan, "approved_plan", required=False)
        if approved_plan is None or state_value(approved_plan, "status") not in {"APPROVED", "REVIEW"}:
            raise ValueError("G5 requires the approved schematic pin-pad plan")
        pin_bindings = tuple(value(approved_plan, "pin_bindings"))
        pin_map = {(value(item, "component_id"), value(item, "pin_number")): value(item, "pad_number") for item in pin_bindings}
        if not pin_map:
            raise ValueError("G5 schematic pin-pad mapping is missing")

        truth_components = tuple(value(truth, "components"))
        hardware_components = {value(item, "architecture_component_id"): item for item in tuple(value(hardware_truth, "components"))}
        bom_by_slot = {value(item, "slot_id"): item for item in tuple(value(bom, "items"))}
        component_slots = {value(item, "architecture_component_id"): value(item, "slot_id") for item in tuple(value(selection, "proposal").selected_components)}
        components: list[PCBComponentTruth] = []
        for component in truth_components:
            reference = value(component, "component_id")
            hardware_component = hardware_components.get(reference)
            slot_id = component_slots.get(reference)
            bom_item = bom_by_slot.get(slot_id)
            if hardware_component is None or slot_id is None or bom_item is None:
                raise ValueError(f"G5 hardware/BOM mapping is missing for {reference}")
            footprint = value(hardware_component, "footprint", required=False) or value(bom_item, "footprint", required=False)
            if not footprint:
                raise ValueError(f"G5 footprint mapping is missing for {reference}")
            pin_numbers = tuple(value(pin, "pin_number") for pin in tuple(value(truth, "pins")) if value(pin, "component_id") == reference)
            pad_numbers = tuple(pin_map[(reference, pin)] for pin in pin_numbers if (reference, pin) in pin_map)
            if len(pin_numbers) != len(pad_numbers):
                raise ValueError(f"G5 pin-pad mapping is incomplete for {reference}")
            source_refs = tuple(dict.fromkeys((value(truth, "truth_id"), value(hardware_truth, "truth_id"), value(bom, "bom_id"), value(hardware_component, "source_id"), *tuple(value(component, "evidence_refs")))))
            components.append(PCBComponentTruth.create(reference=reference, symbol_binding=value(component, "symbol_binding"), footprint_binding=footprint, footprint_source_refs=source_refs, pin_numbers=pin_numbers, pad_numbers=pad_numbers))

        truth_pins = {(value(pin, "component_id"), value(pin, "pin_number")): pin for pin in tuple(value(truth, "pins"))}
        power_domains = {value(domain, "name"): domain for domain in tuple(value(truth, "power_domains"))}
        nets: list[PCBNetTruth] = []
        for net in tuple(value(truth, "nets")):
            endpoints: list[PCBEndpointTruth] = []
            for node in tuple(value(net, "nodes")):
                parts = node.split(".", 1)
                if len(parts) != 2 or (parts[0], parts[1]) not in truth_pins:
                    raise ValueError(f"G5 DesignTruth net endpoint is invalid: {node}")
                endpoints.append(PCBEndpointTruth.create(reference=parts[0], pin_number=parts[1]))
            net_name = value(net, "net_name")
            domain = power_domains.get(net_name)
            net_class = "ground" if (domain is not None and state_value(domain, "ground_net") == net_name) or net_name.casefold() in {"gnd", "ground"} else "power" if domain is not None else "default"
            nets.append(PCBNetTruth.create(name=net_name, endpoints=tuple(endpoints), net_class=net_class, source_refs=tuple(dict.fromkeys((value(truth, "truth_id"), value(net, "binding_id"), *tuple(value(net, "evidence_refs")))))))

        if not isinstance(board, PCBBoardDefinition) or not isinstance(routing, PCBRoutingConstraint) or type(placements) is not tuple or type(routes) is not tuple:
            raise TypeError("G5 physical PCB facts must be typed immutable tuples")
        request_id = "pcb-request-" + hashlib.sha256("|".join((value(production, "conversation_id"), value(generation, "fingerprint"), value(erc, "fingerprint"), value(truth, "fingerprint"), value(hardware_truth, "fingerprint"), value(board, "fingerprint"), value(routing, "fingerprint"), *(item.fingerprint for item in placements), *(item.fingerprint for item in routes))).encode()).hexdigest()[:32]
        if source_request_id is not None:
            source_request_id = _id(source_request_id, "source_request_id")
        if trace_id is not None:
            trace_id = _id(trace_id, "trace_id")
        source_refs = tuple(dict.fromkeys((schematic_artifact_id, value(generation, "operation_id"), value(erc, "operation_id"), value(truth, "truth_id"), value(hardware_truth, "truth_id"), value(bom, "bom_id"), value(hardware_design, "hardware_spec_id"), value(implementation, "implementation_id"), value(schematic_request, "implementation_task_id"), value(authorization, "authorization_id"), authorization_request_id, *tuple(value(board, "source_refs")), *tuple(value(routing, "source_refs")))))
        return PCBProductionRequest.create(
            request_id=request_id,
            source_request_id=source_request_id,
            trace_id=trace_id,
            project_id=value(production, "project_id"),
            conversation_id=value(production, "conversation_id"),
            requester=requester,
            schematic_artifact_id=schematic_artifact_id,
            schematic_artifact_fingerprint=schematic_artifact_fingerprint,
            schematic_receipt_id=value(generation, "operation_id"),
            schematic_receipt_fingerprint=value(generation, "operation_receipt_fingerprint"),
            schematic_erc_result_id=value(erc_result, "operation_id"),
            schematic_erc_fingerprint=value(erc_result, "fingerprint"),
            schematic_erc_status="ERC_PASS",
            schematic_content_fingerprint=schematic_content_fingerprint,
            real_schematic_exists=True,
            schematic_hash_matches_artifact=schematic_content_fingerprint == schematic_artifact_fingerprint,
            schematic_erc_executed=True,
            schematic_connectivity_fingerprint=value(value(production, "proposal"), "connectivity_fingerprint"),
            design_truth_id=value(truth, "truth_id"),
            design_truth_fingerprint=value(truth, "fingerprint"),
            hardware_design_id=value(hardware_design, "hardware_spec_id"),
            hardware_design_fingerprint=value(hardware_design, "fingerprint"),
            implementation_id=value(implementation, "implementation_id"),
            implementation_fingerprint=value(implementation, "fingerprint"),
            implementation_task_id=value(schematic_request, "implementation_task_id"),
            capability_id="ARTIFACT_CREATION",
            capability_status="AVAILABLE",
            authorization_id=value(authorization, "authorization_id"),
            authorization_request_id=authorization_request_id,
            authorization_fingerprint=value(authorization, "fingerprint"),
            authorization_status="APPROVED",
            workspace_identity=workspace_identity,
            target_relative_path=target_relative_path,
            board=board,
            components=tuple(components),
            nets=tuple(nets),
            placements=placements,
            routes=routes,
            routing=routing,
            source_refs=source_refs,
            requested_at=requested_at,
        )


def decide_pcb_proposal(
    proposal: PCBProposal,
    *,
    decision: PCBProposalDecisionStatus,
    reviewer: str,
    decided_at: datetime,
) -> PCBProposalDecision:
    checked = PCBProposal.model_validate(proposal.model_dump(mode="python"), strict=False)
    decision = decision if isinstance(decision, PCBProposalDecisionStatus) else PCBProposalDecisionStatus(decision)
    if decision is PCBProposalDecisionStatus.PENDING:
        raise ValueError("PCB proposal decision must be terminal")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("PCB proposal reviewer is required")
    if not isinstance(decided_at, datetime) or decided_at.tzinfo is None:
        raise ValueError("PCB proposal decision time must be timezone-aware")
    reason = "PCB proposal explicitly approved." if decision is PCBProposalDecisionStatus.APPROVED else "PCB proposal explicitly rejected."
    return PCBProposalDecision.create(
        decision_id="pcb-decision-" + hashlib.sha256(f"{checked.proposal_id}|{checked.fingerprint}".encode()).hexdigest()[:24],
        proposal_id=checked.proposal_id,
        proposal_fingerprint=checked.fingerprint,
        decision=decision,
        reviewer=reviewer.strip(),
        decided_at=decided_at.astimezone(UTC),
        reason=reason,
    )


def create_pcb_repair_proposal(
    *,
    issues: tuple[object, ...],
    base_proposal: PCBProposal,
    source_fingerprint: str,
    classification: object,
    root_cause: str,
    proposed_change: str,
    target_authority: str,
    affected_constraints: tuple[str, ...] = (),
    affected_placements: tuple[str, ...] = (),
    affected_routing: tuple[str, ...] = (),
    supporting_evidence: tuple[str, ...] = (),
    expected_effect: str = "",
    risk: str = "",
    corrected_proposal: PCBProposal | None = None,
) -> object:
    """Create a review-only physical repair proposal.

    The returned object contains an optional regenerated structured proposal;
    it never contains PCB text and never writes the board. Electrical-input
    classifications are intentionally not auto-converted into physical fixes.
    """
    from .contracts import (
        DrcIssue,
        PCBDrcIssueClassification,
        PCBRepairProposal,
    )

    checked_proposal = PCBProposal.model_validate(base_proposal.model_dump(mode="python"), strict=False)
    checked_issues = tuple(DrcIssue.model_validate(item.model_dump(mode="python") if isinstance(item, DrcIssue) else item, strict=False) for item in issues)
    if not checked_issues:
        raise ValueError("PCB repair requires at least one structured DRC issue")
    if any(item.status.value not in {"OPEN", "STALE"} for item in checked_issues):
        raise ValueError("PCB repair requires open DRC issues")
    classification_value = classification if isinstance(classification, PCBDrcIssueClassification) else PCBDrcIssueClassification(classification)
    if any(item.classification is not classification_value for item in checked_issues):
        raise ValueError("PCB repair classification is not evidence-bound")
    if corrected_proposal is not None:
        corrected_proposal = PCBProposal.model_validate(corrected_proposal.model_dump(mode="python"), strict=False)
        if corrected_proposal.project_id != checked_proposal.project_id or corrected_proposal.request_id != checked_proposal.request_id:
            raise ValueError("corrected PCB proposal identity mismatch")
    if not isinstance(source_fingerprint, str) or not source_fingerprint.startswith("sha256:"):
        raise ValueError("PCB repair source fingerprint is invalid")
    repair_id = "pcb-repair-" + hashlib.sha256("|".join((checked_proposal.proposal_id, checked_proposal.fingerprint, source_fingerprint, *(item.issue_id for item in checked_issues))).encode()).hexdigest()[:32]
    return PCBRepairProposal.create(
        repair_id=repair_id,
        issue_refs=tuple(item.issue_id for item in checked_issues),
        classification=classification_value,
        root_cause=root_cause,
        proposed_change=proposed_change,
        target_authority=target_authority,
        affected_constraints=affected_constraints,
        affected_placements=affected_placements,
        affected_routing=affected_routing,
        supporting_evidence=supporting_evidence,
        expected_effect=expected_effect,
        risk=risk,
        source_fingerprint=source_fingerprint,
        base_proposal_id=checked_proposal.proposal_id,
        base_proposal_fingerprint=checked_proposal.fingerprint,
        corrected_proposal=corrected_proposal,
    )


def decide_pcb_repair(
    proposal: object,
    *,
    action: object,
    reviewer: str,
    artifact_fingerprint: str,
    decided_at: datetime,
    comment: str = "",
    edited_proposal_fingerprint: str | None = None,
) -> object:
    from .contracts import PCBRepairDecision, PCBRepairDecisionAction, PCBRepairProposal

    checked = PCBRepairProposal.model_validate(proposal.model_dump(mode="python") if isinstance(proposal, PCBRepairProposal) else proposal, strict=False)
    selected = action if isinstance(action, PCBRepairDecisionAction) else PCBRepairDecisionAction(action)
    return PCBRepairDecision.create(
        decision_id="pcb-repair-decision-" + hashlib.sha256(f"{checked.repair_id}|{checked.fingerprint}|{selected.value}|{reviewer}|{decided_at.isoformat()}".encode()).hexdigest()[:24],
        repair_id=checked.repair_id,
        proposal_fingerprint=checked.fingerprint,
        artifact_fingerprint=artifact_fingerprint,
        action=selected,
        reviewer=reviewer,
        decided_at=decided_at,
        comment=comment,
        edited_proposal_fingerprint=edited_proposal_fingerprint,
    )


def create_pcb_production_state(request: PCBProductionRequest, proposal: PCBProposal) -> PCBProductionState:
    checked_request = PCBProductionRequest.model_validate(request.model_dump(mode="python"), strict=False)
    checked_proposal = PCBProposal.model_validate(proposal.model_dump(mode="python"), strict=False)
    decision = PCBProposalDecision.create(
        decision_id="pcb-decision-" + hashlib.sha256(f"{checked_proposal.proposal_id}|{checked_proposal.fingerprint}".encode()).hexdigest()[:24],
        proposal_id=checked_proposal.proposal_id,
        proposal_fingerprint=checked_proposal.fingerprint,
        decision=PCBProposalDecisionStatus.PENDING,
        reason="Awaiting explicit PCB proposal approval.",
    )
    return PCBProductionState.create(
        production_id="pcb-production-" + hashlib.sha256(f"{checked_request.request_id}|{checked_proposal.fingerprint}".encode()).hexdigest()[:24],
        project_id=checked_request.project_id,
        conversation_id=checked_request.conversation_id,
        request=checked_request,
        proposal=checked_proposal,
        decision=decision,
        status=PCBProductionStatus.AWAITING_APPROVAL,
    )


def _inside_board(board, x: float, y: float) -> bool:
    return board.origin_x_mm <= x <= board.origin_x_mm + board.width_mm and board.origin_y_mm <= y <= board.origin_y_mm + board.height_mm


def _routes_bind_to_pads(request: PCBProductionRequest, *, pad_world_position_provider: object = None) -> bool:
    placements = {item.reference: item for item in request.placements}
    components = {item.reference: item for item in request.components}
    routes_by_net: dict[str, list[tuple[float, float]]] = {}
    for route in request.routes:
        routes_by_net.setdefault(route.net_name, []).extend(route.points)
    for net in request.nets:
        actual = routes_by_net.get(net.name, [])
        for endpoint in net.endpoints:
            component = components.get(endpoint.reference)
            placement = placements.get(endpoint.reference)
            if component is None or placement is None:
                return False
            try:
                if callable(pad_world_position_provider):
                    expected = pad_world_position_provider(
                        placement,
                        component.footprint_binding,
                        endpoint.pin_number,
                    )
                else:
                    expected = pad_world_position(placement, component.pad_numbers, endpoint.pin_number)
            except ValueError:
                return False
            if not any(math.dist(expected, point) <= 1e-6 for point in actual):
                return False
    return True


def _has_same_layer_collision(routes, clearance: float, *, excluded_net_names: frozenset[str] = frozenset()) -> bool:
    segments: list[tuple[int, str, tuple[float, float], tuple[float, float]]] = []
    for route_index, route in enumerate(routes):
        if route.net_name in excluded_net_names:
            continue
        segments.extend((route_index, route.layer, start, end) for start, end in zip(route.points, route.points[1:]))
    for index, (route_index, layer, start, end) in enumerate(segments):
        for other_route_index, other_layer, other_start, other_end in segments[index + 1 :]:
            if route_index != other_route_index and layer == other_layer and _segment_distance(start, end, other_start, other_end) < clearance:
                return True
    return False


def _segment_distance(a, b, c, d) -> float:
    if _segments_intersect(a, b, c, d):
        return 0.0
    return min(
        _point_segment_distance(point, start, end)
        for point, start, end in ((a, c, d), (b, c, d), (c, a, b), (d, a, b))
    )


def _segments_intersect(a, b, c, d) -> bool:
    def orientation(p, q, r):
        value = (q[1] - p[1]) * (r[0] - q[0]) - (q[0] - p[0]) * (r[1] - q[1])
        return 0 if abs(value) < 1e-9 else (1 if value > 0 else 2)

    o1, o2, o3, o4 = orientation(a, b, c), orientation(a, b, d), orientation(c, d, a), orientation(c, d, b)
    return o1 != o2 and o3 != o4


def _point_segment_distance(point, start, end) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    if dx == 0 and dy == 0:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    t = max(0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / (dx * dx + dy * dy)))
    return math.hypot(point[0] - (start[0] + t * dx), point[1] - (start[1] + t * dy))


__all__ = (
    "PCBProductionService",
    "create_pcb_production_state",
    "create_pcb_repair_proposal",
    "decide_pcb_proposal",
    "decide_pcb_repair",
)
