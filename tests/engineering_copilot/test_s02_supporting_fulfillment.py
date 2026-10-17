"""Synthetic support-unit selection and local I2C/decoupling fulfillment only."""

from __future__ import annotations

import pytest

from embedded_copilot.component_recommendation import (
    ComponentPinFact,
    ComponentSourceRecord,
    StaticComponentSource,
    SupportingComponentRequirement,
)
from embedded_copilot.component_recommendation.contracts import (
    SupportingConnectionExpectation,
)
from embedded_copilot.engineering_copilot import (
    HardwareSelectionService,
    SchematicPlanningService,
    SchematicPlanningStatus,
    generate_design_truth,
)
from embedded_copilot.engineering_copilot.schematic_planning import (
    SupportingConnectionStatus,
)

from .test_s02_shtc3_i2c_semantics import _shtc3_catalog, _shtc3_source
from .test_schematic_planning_g2 import _approved_inputs, _catalog, _proposal, _source


def _expectation(target_pin: str, target_net: str, return_net: str) -> SupportingConnectionExpectation:
    return SupportingConnectionExpectation(
        target_pin_number=target_pin, target_net_name=target_net,
        signal_pin_number="1", signal_net_name=target_net,
        return_pin_number="2", return_net_name=return_net,
    )


def _source_with_expectations(
    *, quantity: int = 2, wrong_target_pin: bool = False,
    wrong_signal_net: bool = False, wrong_return_net: bool = False,
) -> ComponentSourceRecord:
    original = _shtc3_source()
    payload = original.model_dump(mode="python")
    payload["supporting_requirements"] = (
        SupportingComponentRequirement.create(
            role="I2C_PULLUPS", description="Synthetic SCL/SDA pullups",
            quantity=quantity, evidence_refs=("synthetic-shtc3-i2c",),
            connection_expectations=(
                SupportingConnectionExpectation(
                    target_pin_number="4" if wrong_target_pin else "2",
                    target_net_name="I2C1_SCL",
                    signal_pin_number="1",
                    signal_net_name="I2C1_SDA" if wrong_signal_net else "I2C1_SCL",
                    return_pin_number="2",
                    return_net_name="GND" if wrong_return_net else "3V3",
                ),
                _expectation("3", "I2C1_SDA", "3V3"),
            ),
        ),
        SupportingComponentRequirement.create(
            role="DECOUPLING", description="Synthetic local bypass",
            evidence_refs=("synthetic-shtc3-bypass",),
            connection_expectations=(_expectation("1", "3V3", "GND"),),
        ),
    )
    return ComponentSourceRecord.create(**payload)


def _support_part(role: str, mpn: str) -> ComponentSourceRecord:
    return _source(
        role, "Synthetic Parts", mpn,
        (
            ComponentPinFact.create(number="1", name="A", function="passive", pad_number="1"),
            ComponentPinFact.create(number="2", name="B", function="passive", pad_number="2"),
        ),
    )


def _catalog_with_support(
    *, quantity: int = 2, resistor: bool = True, wrong_target_pin: bool = False,
    wrong_signal_net: bool = False, wrong_return_net: bool = False,
) -> StaticComponentSource:
    base = tuple(
        _source_with_expectations(
            quantity=quantity, wrong_target_pin=wrong_target_pin,
            wrong_signal_net=wrong_signal_net, wrong_return_net=wrong_return_net,
        ) if item.category == "SENSOR" else item
        for item in _catalog().records
    )
    parts = (
        (_support_part("I2C_PULLUPS", "R-TEST-4K7"),) if resistor else ()
    ) + (_support_part("DECOUPLING", "C-TEST-100N"),)
    return StaticComponentSource((*base, *parts))


def _selection_and_plan(source: StaticComponentSource):
    context, architecture, review, workflow, _unused_selection, _base = _approved_inputs()
    service = HardwareSelectionService(source)
    proposal = service.create_proposal(
        architecture, context, workflow=workflow,
        conversation_id=context.conversation_id,
    )
    selection = service.approve(proposal, reviewer="test-reviewer", decided_at=review.decided_at)
    assert selection.hardware_truth is not None and selection.bom is not None
    base = generate_design_truth(architecture, conversation_id=context.conversation_id)
    base = service.project_design_truth(base, selection.hardware_truth, architecture_review=review)
    plan = SchematicPlanningService().create_proposal(
        architecture, context, selection.hardware_truth, selection.bom,
        design_truth=base, conversation_id=context.conversation_id,
        selection=selection,
    )
    return proposal, selection, plan


def test_exact_support_parts_get_three_independent_approved_units_and_connections() -> None:
    proposal, selection, plan = _selection_and_plan(_catalog_with_support())
    support = tuple(item for item in proposal.selected_components if item.role in {"I2C_PULLUPS", "DECOUPLING"})
    assert len(support) == 3
    assert len({item.slot_id for item in support}) == 3
    assert sorted((item.role, item.mpn) for item in support) == [
        ("DECOUPLING", "C-TEST-100N"),
        ("I2C_PULLUPS", "R-TEST-4K7"),
        ("I2C_PULLUPS", "R-TEST-4K7"),
    ]
    assert all(item.quantity == 1 for item in support)
    assert selection.bom is not None and selection.hardware_truth is not None
    assert {item.slot_id for item in support} <= {item.slot_id for item in selection.bom.items}
    assert {item.slot_id for item in support} <= {item.slot_id for item in selection.hardware_truth.components}
    assert plan.status is SchematicPlanningStatus.REVIEW
    assert "BLOCKED_SUPPORTING_COMPONENT_SELECTION" not in plan.blocking_reasons
    assert len([item for item in plan.supporting_connections if item.status.value == "RESOLVED"]) == 2
    assert sorted(len(item.fulfillments) for item in plan.supporting_connections) == [1, 2]
    all_proofs = tuple(
        proof for item in plan.supporting_connections for proof in item.fulfillments
    )
    assert len({item.support_slot_id for item in all_proofs}) == 3
    assert len({item.support_instance_id for item in all_proofs}) == 3
    for proof in all_proofs:
        signal_net = next(item for item in plan.nets if item.name == proof.signal_net_name)
        return_net = next(item for item in plan.nets if item.name == proof.return_net_name)
        assert (proof.support_instance_id, proof.signal_pin_number) in {
            (item.component_id, item.pin_number) for item in signal_net.endpoints
        }
        assert (proof.support_instance_id, proof.return_pin_number) in {
            (item.component_id, item.pin_number) for item in return_net.endpoints
        }


def test_mismatched_requirement_quantity_keeps_support_blocked() -> None:
    _proposal, _selection, plan = _selection_and_plan(_catalog_with_support(quantity=1))
    assert plan.status is SchematicPlanningStatus.BLOCKED
    assert "BLOCKED_SUPPORTING_COMPONENT_SELECTION" in plan.blocking_reasons
    assert any(item.status.value == "REQUIREMENT_ONLY" for item in plan.supporting_connections)


def test_missing_support_part_stops_selection_before_approval() -> None:
    context, architecture, _review, workflow, _selection, _base = _approved_inputs()
    service = HardwareSelectionService(_catalog_with_support(resistor=False))
    with pytest.raises(ValueError, match="component evidence unavailable for I2C_PULLUPS"):
        service.create_proposal(
            architecture, context, workflow=workflow,
            conversation_id=context.conversation_id,
        )


@pytest.mark.parametrize("change", ["wrong_target_pin", "wrong_signal_net", "wrong_return_net"])
def test_wrong_explicit_parent_pin_or_net_keeps_requirement_only(change: str) -> None:
    _proposal, _selection, plan = _selection_and_plan(_catalog_with_support(**{change: True}))
    assert plan.status is SchematicPlanningStatus.BLOCKED
    assert "BLOCKED_SUPPORTING_COMPONENT_SELECTION" in plan.blocking_reasons
    assert next(
        item for item in plan.supporting_connections if item.requirement_role == "I2C_PULLUPS"
    ).status.value == "REQUIREMENT_ONLY"


def test_source_port_drift_rejects_decision_and_currentness() -> None:
    class MutablePort:
        def __init__(self, current: StaticComponentSource) -> None:
            self.current = current

        def search(self, project_id, architecture_id, requirement):
            return self.current.search(project_id, architecture_id, requirement)

    context, architecture, review, workflow, _selection, _base = _approved_inputs()
    port = MutablePort(_catalog_with_support())
    service = HardwareSelectionService(port)
    proposal = service.create_proposal(
        architecture, context, workflow=workflow, conversation_id=context.conversation_id,
    )
    approved = service.approve(proposal, reviewer="test-reviewer", decided_at=review.decided_at)
    assert service.selection_sources_current(approved)
    port.current = _catalog_with_support(resistor=False)
    assert not service.selection_sources_current(approved)
    with pytest.raises(ValueError, match="source changed"):
        service.approve(proposal, reviewer="test-reviewer", decided_at=review.decided_at)


def test_support_slot_rejects_port_candidate_with_wrong_supported_role() -> None:
    catalog = _catalog_with_support()

    class WrongRolePort:
        def search(self, project_id, architecture_id, requirement):
            if requirement.role == "I2C_PULLUPS":
                return tuple(item for item in catalog.records if item.category == "DECOUPLING")
            return catalog.search(project_id, architecture_id, requirement)

    context, architecture, _review, workflow, _selection, _base = _approved_inputs()
    with pytest.raises(ValueError, match="component evidence insufficient for I2C_PULLUPS"):
        HardwareSelectionService(WrongRolePort()).create_proposal(
            architecture, context, workflow=workflow,
            conversation_id=context.conversation_id,
        )


def test_replaced_selection_approval_cannot_supply_fulfillment() -> None:
    _proposal, selection, _plan = _selection_and_plan(_catalog_with_support())
    context, architecture, review, _workflow, _other_selection, _base = _approved_inputs()
    service = HardwareSelectionService(_catalog_with_support())
    base = service.project_design_truth(
        generate_design_truth(architecture, conversation_id=context.conversation_id),
        selection.hardware_truth, architecture_review=review,
    )
    replaced_approval = selection.approval.model_copy(update={"approval_id": "replacement"})
    replaced = selection.model_copy(update={"approval": replaced_approval})
    with pytest.raises(ValueError):
        SchematicPlanningService().create_proposal(
            architecture, context, selection.hardware_truth, selection.bom,
            design_truth=base, conversation_id=context.conversation_id,
            selection=replaced,
        )


def test_source_port_error_is_distinct_from_stale_selection() -> None:
    _proposal, selection, _plan = _selection_and_plan(_catalog_with_support())

    class UnavailablePort:
        def search(self, project_id, architecture_id, requirement):
            raise OSError("offline source unavailable")

    with pytest.raises(OSError, match="source unavailable"):
        HardwareSelectionService(UnavailablePort()).selection_sources_current(selection)


def test_forged_resolved_projection_or_reused_unit_is_rejected() -> None:
    _proposal, _selection, plan = _selection_and_plan(_catalog_with_support())
    connection = next(
        item for item in plan.supporting_connections if item.requirement_role == "I2C_PULLUPS"
    )
    payload = dict(connection.__dict__)
    payload.update(status=SupportingConnectionStatus.RESOLVED, fulfillments=())
    with pytest.raises(ValueError, match="status and fulfillment"):
        type(connection).create(**payload)
    payload["fulfillments"] = (connection.fulfillments[0], connection.fulfillments[0])
    with pytest.raises(ValueError, match="reuses a physical unit"):
        type(connection).create(**payload)


def test_forged_fulfillment_endpoint_is_rejected_even_with_rehashed_plan() -> None:
    _proposal, _selection, plan = _selection_and_plan(_catalog_with_support())
    connection = next(
        item for item in plan.supporting_connections if item.requirement_role == "I2C_PULLUPS"
    )
    proof = connection.fulfillments[0]
    forged_proof = type(proof).create(**{
        **proof.__dict__, "target_pin_number": "4",
    })
    forged_connection = type(connection).create(**{
        **connection.__dict__,
        "fulfillments": (forged_proof, *connection.fulfillments[1:]),
    })
    with pytest.raises(ValueError, match="fulfillment endpoint binding"):
        type(plan).create(**{
            **plan.__dict__,
            "supporting_connections": tuple(
                forged_connection if item.connection_id == connection.connection_id else item
                for item in plan.supporting_connections
            ),
        })


def test_old_selection_and_requirement_only_payload_omit_new_fields() -> None:
    _context, _architecture, _review, _workflow, selection, _base = _approved_inputs()
    assert selection.bom is not None and selection.hardware_truth is not None
    assert all("supporting_for" not in item.model_dump(mode="json") for item in selection.proposal.requirements)
    assert all("supporting_for" not in item.model_dump(mode="json") for item in selection.proposal.selected_components)
    assert all("supporting_for" not in item.model_dump(mode="json") for item in selection.bom.items)
    assert all("supporting_for" not in item.model_dump(mode="json") for item in selection.hardware_truth.components)
    legacy_plan, *_ = _proposal(_shtc3_catalog())
    assert all(item.status.value == "REQUIREMENT_ONLY" for item in legacy_plan.supporting_connections)
    assert all("fulfillments" not in item.model_dump(mode="json") for item in legacy_plan.supporting_connections)
