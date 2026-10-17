from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from embedded_copilot.pcb_production import (
    PCBBoardDefinition,
    PCBComponentTruth,
    PCBEndpointTruth,
    PCBFootprintAvailabilityPort,
    PCBNetTruth,
    PCBPlacementConstraint,
    PCBProductionRequest,
    PCBReadinessStatus,
    PCBRouteSegment,
    PCBRoutingConstraint,
    PCBProductionService,
    PCBProposalDecisionStatus,
    PCBProductionStatus,
    DrcIssue,
    PCBDrcIssueClassification,
    PCBDrcIssueSeverity,
    create_pcb_production_state,
    create_pcb_repair_proposal,
    decide_pcb_repair,
    decide_pcb_proposal,
)


FP = "sha256:" + "1" * 64


class _Availability(PCBFootprintAvailabilityPort):
    def __init__(self, available: set[str] | None = None) -> None:
        self.available = {"Resistor_SMD:R_0603_1608Metric"} if available is None else available

    def is_available(self, footprint_binding: str) -> bool:
        return footprint_binding in self.available


def _request(**changes: object) -> PCBProductionRequest:
    components = (
        PCBComponentTruth.create(
            reference="R1",
            symbol_binding="Device:R",
            footprint_binding="Resistor_SMD:R_0603_1608Metric",
            footprint_source_refs=("truth-r1-footprint",),
            pin_numbers=("1", "2"),
            pad_numbers=("1", "2"),
        ),
        PCBComponentTruth.create(
            reference="R2",
            symbol_binding="Device:R",
            footprint_binding="Resistor_SMD:R_0603_1608Metric",
            footprint_source_refs=("truth-r2-footprint",),
            pin_numbers=("1", "2"),
            pad_numbers=("1", "2"),
        ),
    )
    nets = (
        PCBNetTruth.create(
            name="NET_A",
            endpoints=(PCBEndpointTruth.create(reference="R1", pin_number="1"), PCBEndpointTruth.create(reference="R2", pin_number="1")),
            net_class="default",
            source_refs=("truth-net-a",),
        ),
        PCBNetTruth.create(
            name="NET_B",
            endpoints=(PCBEndpointTruth.create(reference="R1", pin_number="2"), PCBEndpointTruth.create(reference="R2", pin_number="2")),
            net_class="default",
            source_refs=("truth-net-b",),
        ),
    )
    board = PCBBoardDefinition.create(
        width_mm=40.0,
        height_mm=30.0,
        origin_x_mm=100.0,
        origin_y_mm=100.0,
        layer_count=2,
        copper_layers=("F.Cu", "B.Cu"),
        source_refs=("golden-board-outline",),
    )
    placements = (
        PCBPlacementConstraint.create(reference="R1", x_mm=110.0, y_mm=110.0, rotation_deg=0.0, side="F.Cu", source_refs=("truth-r1-placement",)),
        PCBPlacementConstraint.create(reference="R2", x_mm=130.0, y_mm=110.0, rotation_deg=0.0, side="F.Cu", source_refs=("truth-r2-placement",)),
    )
    routes = (
        PCBRouteSegment.create(net_name="NET_A", layer="F.Cu", width_mm=0.25, points=((109.35, 109.45), (129.35, 109.45)), source_refs=("truth-route-a",)),
        PCBRouteSegment.create(net_name="NET_B", layer="F.Cu", width_mm=0.25, points=((109.35, 110.55), (129.35, 110.55)), source_refs=("truth-route-b",)),
    )
    material = dict(
        request_id="pcb-request-golden",
        source_request_id="application-request-pcb",
        trace_id="application-trace-pcb",
        project_id="project-golden",
        conversation_id="conversation-golden",
        requester="engineer-golden",
        schematic_artifact_id="artifact-schematic",
        schematic_artifact_fingerprint=FP,
        schematic_receipt_id="receipt-schematic-write",
        schematic_receipt_fingerprint=FP,
        schematic_erc_result_id="erc-result-golden",
        schematic_erc_fingerprint=FP,
        schematic_erc_status="ERC_PASS",
        schematic_content_fingerprint=FP,
        real_schematic_exists=True,
        schematic_hash_matches_artifact=True,
        schematic_erc_executed=True,
        schematic_connectivity_fingerprint=FP,
        design_truth_id="design-truth-golden",
        design_truth_fingerprint=FP,
        hardware_design_id="hardware-design-golden",
        hardware_design_fingerprint=FP,
        implementation_id="implementation-golden",
        implementation_fingerprint=FP,
        implementation_task_id="pcb-task-golden",
        capability_id="ARTIFACT_CREATION",
        capability_status="AVAILABLE",
        authorization_id="authorization-golden",
        authorization_request_id="authorization-request-golden",
        authorization_fingerprint=FP,
        authorization_status="APPROVED",
        workspace_identity="project-root",
        target_relative_path="hardware/main.kicad_pcb",
        board=board,
        components=components,
        nets=nets,
        placements=placements,
        routes=routes,
        routing=PCBRoutingConstraint.create(trace_width_mm=0.25, clearance_mm=0.2, source_refs=("golden-routing",)),
        source_refs=(
            "artifact-schematic",
            "receipt-schematic-write",
            "erc-result-golden",
            "design-truth-golden",
            "hardware-design-golden",
            "implementation-golden",
            "pcb-task-golden",
            "authorization-golden",
            "authorization-request-golden",
        ),
        requested_at=datetime(2026, 9, 5, tzinfo=UTC),
    )
    material.update(changes)
    return PCBProductionRequest.create(**material)


def test_readiness_requires_real_erc_pass() -> None:
    request = _request(schematic_erc_status="ERC_FAIL")
    result = PCBProductionService(footprints=_Availability()).evaluate_readiness(request)
    assert result.status is PCBReadinessStatus.BLOCKED_SCHEMATIC_NOT_VERIFIED
    assert result.required_next_action == "obtain a real schematic ERC PASS"


def test_readiness_does_not_invent_missing_footprint() -> None:
    request = _request(components=(PCBComponentTruth.create(reference="R1", symbol_binding="Device:R", footprint_binding=None, footprint_source_refs=(), pin_numbers=("1", "2"), pad_numbers=()),))
    result = PCBProductionService(footprints=_Availability()).evaluate_readiness(request)
    assert result.status is PCBReadinessStatus.BLOCKED_MISSING_FOOTPRINT
    assert "0603" not in str(result.model_dump())


def test_readiness_rejects_unavailable_footprint_and_pin_pad_mismatch() -> None:
    unavailable = PCBProductionService(footprints=_Availability(set()))
    result = unavailable.evaluate_readiness(_request())
    assert result.status is PCBReadinessStatus.BLOCKED_FOOTPRINT_UNAVAILABLE

    mismatch = _request(components=(PCBComponentTruth.create(reference="R1", symbol_binding="Device:R", footprint_binding="Resistor_SMD:R_0603_1608Metric", footprint_source_refs=("truth",), pin_numbers=("1", "2", "3"), pad_numbers=("1", "2")), _request().components[1]))
    result = PCBProductionService(footprints=_Availability()).evaluate_readiness(mismatch)
    assert result.status is PCBReadinessStatus.BLOCKED_PIN_PAD_MISMATCH


def test_complete_truth_generates_deterministic_proposal() -> None:
    service = PCBProductionService(footprints=_Availability())
    request = _request()
    first = service.create_proposal(request)
    second = service.create_proposal(PCBProductionRequest.model_validate(request.model_dump(mode="python")))
    assert first.fingerprint == second.fingerprint
    assert first.board.layer_count == 2
    assert tuple(item.component_ref for item in first.footprints) == ("R1", "R2")
    assert tuple(item.net_name for item in first.routing) == ("NET_A", "NET_B")


def test_proposal_changes_when_schematic_or_design_truth_changes() -> None:
    service = PCBProductionService(footprints=_Availability())
    base = service.create_proposal(_request())
    changed_schematic = service.create_proposal(_request(schematic_connectivity_fingerprint="sha256:" + "2" * 64))
    changed_truth = service.create_proposal(_request(design_truth_fingerprint="sha256:" + "3" * 64))
    assert base.fingerprint != changed_schematic.fingerprint
    assert base.fingerprint != changed_truth.fingerprint


def test_unsupported_stackup_is_blocked_without_downgrade() -> None:
    request = _request(board=PCBBoardDefinition.create(width_mm=40.0, height_mm=30.0, origin_x_mm=100.0, origin_y_mm=100.0, layer_count=4, copper_layers=("F.Cu", "In1.Cu", "In2.Cu", "B.Cu"), source_refs=("truth-stackup",)))
    result = PCBProductionService(footprints=_Availability()).evaluate_readiness(request)
    assert result.status is PCBReadinessStatus.BLOCKED_UNSUPPORTED_STACKUP


def test_proposal_requires_readiness() -> None:
    with pytest.raises(ValueError, match="PCB readiness"):
        PCBProductionService(footprints=_Availability()).create_proposal(_request(schematic_erc_status="ERC_BLOCKED"))


def test_readiness_rejects_placement_outside_board_and_route_width_drift() -> None:
    service = PCBProductionService(footprints=_Availability())
    outside = service.evaluate_readiness(
        _request(
            placements=(
                PCBPlacementConstraint.create(reference="R1", x_mm=99.0, y_mm=110.0, source_refs=("outside",)),
                _request().placements[1],
            )
        )
    )
    assert outside.status is PCBReadinessStatus.BLOCKED_UNSUPPORTED_CONSTRAINT
    width_drift = service.evaluate_readiness(
        _request(
            routes=(
                PCBRouteSegment.create(net_name="NET_A", layer="F.Cu", width_mm=0.3, points=((110.0, 110.0), (130.0, 110.0)), source_refs=("drift",)),
                _request().routes[1],
            )
        )
    )
    assert width_drift.status is PCBReadinessStatus.BLOCKED_UNSUPPORTED_CONSTRAINT


def test_readiness_rejects_same_layer_route_collision() -> None:
    request = _request(
        routes=(
            PCBRouteSegment.create(net_name="NET_A", layer="F.Cu", width_mm=0.25, points=((110.0, 110.0), (130.0, 110.0)), source_refs=("route-a",)),
            PCBRouteSegment.create(net_name="NET_B", layer="F.Cu", width_mm=0.25, points=((110.0, 110.1), (130.0, 110.1)), source_refs=("route-b",)),
        )
    )
    result = PCBProductionService(footprints=_Availability()).evaluate_readiness(request)
    assert result.status is PCBReadinessStatus.BLOCKED_UNSUPPORTED_CONSTRAINT


def test_readiness_accepts_a_routed_net_with_an_intermediate_endpoint() -> None:
    base = _request()
    r3 = PCBComponentTruth.create(
        reference="R3",
        symbol_binding="Device:R",
        footprint_binding="Resistor_SMD:R_0603_1608Metric",
        footprint_source_refs=("truth-r3-footprint",),
        pin_numbers=("1", "2"),
        pad_numbers=("1", "2"),
    )
    request = _request(
        components=(*base.components, r3),
        nets=(
            PCBNetTruth.create(
                name="NET_A",
                endpoints=(
                    PCBEndpointTruth.create(reference="R1", pin_number="1"),
                    PCBEndpointTruth.create(reference="R3", pin_number="1"),
                    PCBEndpointTruth.create(reference="R2", pin_number="1"),
                ),
                net_class="default",
                source_refs=("truth-net-a",),
            ),
            base.nets[1],
        ),
        placements=(*base.placements, PCBPlacementConstraint.create(reference="R3", x_mm=120.0, y_mm=120.0, source_refs=("truth-r3-placement",))),
        routes=(
            PCBRouteSegment.create(
                net_name="NET_A",
                layer="F.Cu",
                width_mm=0.25,
                points=((109.35, 109.45), (109.35, 102.0), (119.35, 102.0), (119.35, 119.45), (129.35, 102.0), (129.35, 109.45)),
                source_refs=("truth-route-a",),
            ),
            PCBRouteSegment.create(
                net_name="NET_B",
                layer="B.Cu",
                width_mm=0.25,
                points=base.routes[1].points,
                source_refs=("truth-route-b",),
            ),
        ),
    )

    result = PCBProductionService(footprints=_Availability()).evaluate_readiness(request)

    assert result.status is PCBReadinessStatus.READY


def test_pcb_production_state_requires_bound_terminal_decision() -> None:
    service = PCBProductionService(footprints=_Availability())
    request = _request()
    proposal = service.create_proposal(request)
    state = create_pcb_production_state(request, proposal)
    assert state.status is PCBProductionStatus.AWAITING_APPROVAL
    assert state.decision.decision is PCBProposalDecisionStatus.PENDING
    approved = decide_pcb_proposal(
        proposal,
        decision=PCBProposalDecisionStatus.APPROVED,
        reviewer="trusted-reviewer",
        decided_at=datetime(2026, 9, 5, tzinfo=UTC),
    )
    assert approved.proposal_fingerprint == proposal.fingerprint
    with pytest.raises(ValueError, match="terminal"):
        decide_pcb_proposal(
            proposal,
            decision=PCBProposalDecisionStatus.PENDING,
            reviewer="trusted-reviewer",
            decided_at=datetime(2026, 9, 5, tzinfo=UTC),
        )


def test_g5_request_projects_electrical_truth_from_current_g4() -> None:
    physical = _request()
    fp = FP
    components = tuple(
        SimpleNamespace(component_id=reference, symbol_binding="Device:R", evidence_refs=(f"truth-{reference}",))
        for reference in ("R1", "R2")
    )
    pins = tuple(
        SimpleNamespace(component_id=reference, pin_number=pin)
        for reference in ("R1", "R2")
        for pin in ("1", "2")
    )
    nets = tuple(
        SimpleNamespace(name=name, net_name=name, nodes=("R1.1", "R2.1") if name == "NET_A" else ("R1.2", "R2.2"), binding_id=f"binding-{name}", evidence_refs=(f"truth-{name}",))
        for name in ("NET_A", "NET_B")
    )
    truth = SimpleNamespace(
        truth_id="design-truth-g4", fingerprint=fp, status="APPROVED", origin="CREATE",
        components=components, pins=pins, nets=nets, power_domains=(),
    )
    hardware_truth = SimpleNamespace(
        truth_id="hardware-truth-g4", fingerprint=fp, bom_fingerprint=fp,
        components=tuple(
            SimpleNamespace(architecture_component_id=reference, slot_id=f"slot-{reference}", footprint="Resistor_SMD:R_0603_1608Metric", source_id=f"source-{reference}")
            for reference in ("R1", "R2")
        ),
    )
    bom = SimpleNamespace(
        bom_id="bom-g4", fingerprint=fp,
        items=tuple(SimpleNamespace(slot_id=f"slot-{reference}", footprint="Resistor_SMD:R_0603_1608Metric") for reference in ("R1", "R2")),
    )
    selection = SimpleNamespace(
        status="APPROVED", hardware_truth=hardware_truth, bom=bom,
        approval=SimpleNamespace(decision="APPROVED"),
        proposal=SimpleNamespace(selected_components=tuple(SimpleNamespace(architecture_component_id=reference, slot_id=f"slot-{reference}") for reference in ("R1", "R2"))),
    )
    schematic_production = SimpleNamespace(
        status="GENERATED", project_id="project-golden", conversation_id="conversation-golden",
        generation_receipt=SimpleNamespace(validation_status="PARSE_VERIFIED", parse_validation=object(), artifact_fingerprint=fp, operation_id="schematic-write", fingerprint=fp, operation_receipt_fingerprint=fp),
        erc_gate_status="PASS", erc_receipt=SimpleNamespace(fingerprint=fp, operation_id="erc-result", result=SimpleNamespace(status="ERC_PASS", operation_id="erc-result", fingerprint=fp)),
        request=SimpleNamespace(implementation_task_id="implementation-task", authorization_request_id="authorization-request"),
        proposal=SimpleNamespace(connectivity_fingerprint=fp),
    )
    schematic_plan = SimpleNamespace(
        approved_plan=SimpleNamespace(status="APPROVED", pin_bindings=tuple(SimpleNamespace(component_id=reference, pin_number=pin, pad_number=pin) for reference in ("R1", "R2") for pin in ("1", "2"))),
    )
    authorization = SimpleNamespace(
        authorization_id="authorization-g4", fingerprint=fp, status="AUTHORIZED",
        requests=(SimpleNamespace(request_id="authorization-request", capability_id="ARTIFACT_CREATION", status="AUTHORIZED"),),
        decisions=(SimpleNamespace(request_id="authorization-request", decision="APPROVED"),),
    )
    request = PCBProductionService.build_request_from_g4(
        schematic_production=schematic_production,
        schematic_plan=schematic_plan,
        design_truth=truth,
        hardware_selection=selection,
        hardware_design=SimpleNamespace(hardware_spec_id="hardware-design", fingerprint=fp),
        implementation=SimpleNamespace(implementation_id="implementation", fingerprint=fp),
        authorization=authorization,
        schematic_artifact_id="schematic-artifact",
        schematic_artifact_fingerprint=fp,
        schematic_content_fingerprint=fp,
        workspace_identity="project-root",
        target_relative_path="hardware/main.kicad_pcb",
        board=physical.board,
        placements=physical.placements,
        routes=physical.routes,
        routing=physical.routing,
        requester="engineer-golden",
        requested_at=datetime(2026, 9, 5, tzinfo=UTC),
    )

    assert tuple(item.reference for item in request.components) == ("R1", "R2")
    assert tuple(item.name for item in request.nets) == ("NET_A", "NET_B")
    assert request.schematic_erc_status == "ERC_PASS"
    assert request.design_truth_fingerprint == truth.fingerprint


def test_pcb_repair_is_physical_review_only_and_fingerprint_bound() -> None:
    proposal = PCBProductionService(footprints=_Availability()).create_proposal(_request())
    issue = DrcIssue.create(
        issue_id="drc-issue-clearance",
        severity=PCBDrcIssueSeverity.ERROR,
        rule_id="clearance",
        message="clearance violation",
        footprint_ref="R1",
        pad_ref="1",
        raw_evidence_ref="artifact://pcb-drc/report/item/0",
        pcb_artifact_fingerprint=FP,
        classification=PCBDrcIssueClassification.PCB_PHYSICAL_DESIGN_DEFECT,
    )
    repair = create_pcb_repair_proposal(
        issues=(issue,),
        base_proposal=proposal,
        source_fingerprint=FP,
        classification=PCBDrcIssueClassification.PCB_PHYSICAL_DESIGN_DEFECT,
        root_cause="track clearance is below the approved rule",
        proposed_change="move the physical route while preserving the net assignment",
        target_authority="PCB physical design proposal",
        affected_routing=("NET_A",),
        supporting_evidence=(issue.raw_evidence_ref,),
        expected_effect="clear the KiCad clearance issue",
        risk="placement trade-off requires user review",
    )
    decision = decide_pcb_repair(
        repair,
        action="DEFER",
        reviewer="engineer-golden",
        artifact_fingerprint=FP,
        decided_at=datetime(2026, 9, 5, tzinfo=UTC),
        comment="review later",
    )

    assert repair.requires_user_approval is True
    assert repair.status == "AWAITING_APPROVAL"
    assert decision.action.value == "DEFER"
    assert decision.artifact_fingerprint == FP
    assert "net_assignments" not in repair.model_dump(mode="python")
