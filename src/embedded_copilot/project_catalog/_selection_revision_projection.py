"""Private, pure withdrawal of current projections for a Selection head change.

The caller must archive the original aggregate and Plan head in the same storage
transaction before saving this projection. This module never edits historical
objects, executes downstream work, or grants approval.
"""

from __future__ import annotations

from embedded_copilot.engineering_copilot import HardwareSelectionState

from .models import ConversationDetail

_ZERO = "sha256:" + "0" * 64

# The existing PCB aggregate has a fixed dependency chain; removing its source
# also removes the current measurements and gates, while archived raw stays intact.
_PCB_FIELDS = (
    "pcb_production_request",
    "pcb_production",
    "pcb_engineering_state",
    "pcb_constraint_set",
    "pcb_verification_plan",
    "pcb_decoupling_verification",
    "pcb_decoupling_measurements",
    "pcb_return_path_verification",
    "pcb_return_path_measurements",
    "pcb_return_path_relationships",
    "pcb_return_path_discontinuities",
    "pcb_return_path_transitions",
    "pcb_power_loop_verification",
    "pcb_power_stage_relationships",
    "pcb_power_loop_relationships",
    "pcb_power_loop_measurements",
    "pcb_power_switch_nodes",
    "pcb_power_feedback_relationships",
    "pcb_differential_pair_verification",
    "pcb_differential_pair_relationships",
    "pcb_differential_pair_measurements",
    "pcb_differential_pair_dependency_fingerprints",
    "pcb_mapping_verification",
    "pcb_component_mapping_relationships",
    "pcb_pin_mapping_relationships",
    "pcb_pad_mapping_relationships",
    "pcb_engineering_evidence",
    "pcb_manufacturer_profile",
    "pcb_manufacturing_measurements",
    "pcb_engineering_verification_summary",
    "pcb_ready_for_fab",
    "pcb_ready_for_fab_approval",
)


def _lost_fields(detail: ConversationDetail) -> set[str]:
    lost = {"schematic_planning"}
    selection = detail.hardware_selection
    old_sources: set[tuple[str, str]] = set()
    if selection is not None:
        old_sources.add(
            (selection.proposal.proposal_id, selection.proposal.fingerprint)
        )
        if selection.hardware_truth is not None:
            old_sources.add(
                (
                    selection.hardware_truth.truth_id,
                    selection.hardware_truth.fingerprint,
                )
            )
        if selection.bom is not None:
            old_sources.add((selection.bom.bom_id, selection.bom.fingerprint))
    planning = detail.schematic_planning
    if planning is not None:
        old_sources.add((planning.proposal.plan_id, planning.proposal.fingerprint))
        if planning.approved_plan is not None:
            old_sources.add(
                (planning.approved_plan.plan_id, planning.approved_plan.fingerprint)
            )
    truth = detail.design_truth
    truth_lost = truth is not None and any(
        (binding.source_id, binding.source_fingerprint) in old_sources
        for binding in (*truth.provenance, *truth.evidence_bindings)
    )
    if truth_lost:
        lost.update(
            (
                "design_truth",
                "engineering_knowledge_model",
                "teaching_context",
                "build_configuration",
            )
        )

    hardware = detail.hardware_design_spec
    hardware_sources = old_sources | (
        {(truth.truth_id, truth.fingerprint)} if truth_lost else set()
    )
    hardware_lost = hardware is not None and any(
        (item.source_id, item.source_fingerprint) in hardware_sources
        for item in hardware.provenance
    )
    if hardware_lost:
        # The canonical aggregate requires these specs to bind the exact current
        # hardware spec. Their original raw is archived by the transaction.
        lost.update(
            (
                "hardware_design_spec",
                "build_configuration",
                "firmware_spec",
                "simulation_spec",
                "verification_spec",
            )
        )

    firmware = detail.firmware_spec
    firmware_lost = hardware_lost or (
        truth_lost
        and firmware is not None
        and (
            (firmware.design_truth_id, firmware.design_truth_fingerprint)
            == (truth.truth_id, truth.fingerprint)
            or any(
                (item.source_id, item.source_fingerprint)
                == (truth.truth_id, truth.fingerprint)
                for item in firmware.provenance
            )
        )
    )
    if firmware_lost:
        lost.update(("firmware_spec", "simulation_spec", "verification_spec"))
    implementation = detail.implementation_spec
    implementation_lost = firmware_lost or (
        truth_lost
        and implementation is not None
        and (
            (implementation.design_truth_id, implementation.design_truth_fingerprint)
            == (truth.truth_id, truth.fingerprint)
            or any(
                (item.source_id, item.source_fingerprint)
                == (truth.truth_id, truth.fingerprint)
                for item in implementation.provenance
            )
        )
    )
    if implementation_lost:
        lost.update(
            (
                "implementation_spec",
                "capability_evaluation",
                "execution_intents",
                "execution_authorization",
                "firmware_source_generation",
                "firmware_production",
            )
        )
    if truth_lost or firmware_lost:
        lost.add("virtual_system_verification")
    if truth_lost:
        # Production always requires the current truth, even for legacy specs
        # whose optional design_truth fields have not yet been populated.
        lost.add("firmware_production")
    if selection is not None or planning is not None:
        lost.add("schematic_production")
    pcb_request = detail.pcb_production_request
    if ("schematic_production" in lost and detail.schematic_production is not None) or (
        truth_lost
        and pcb_request is not None
        and (pcb_request.design_truth_id, pcb_request.design_truth_fingerprint)
        == (truth.truth_id, truth.fingerprint)
    ):
        lost.update(_PCB_FIELDS)
    return lost


def selection_revision_lost_bindings(detail: ConversationDetail) -> tuple[str, ...]:
    """Return present current fields that lose authority; perform no writes."""
    if type(detail) is not ConversationDetail:
        raise TypeError("detail must be ConversationDetail")
    checked = ConversationDetail.model_validate(detail.model_dump(mode="python"))
    lost = _lost_fields(checked)
    return tuple(
        name
        for name in ConversationDetail.model_fields
        if name in lost and getattr(checked, name) not in (None, ())
    )


def withdraw_selection_revision_projection(
    detail: ConversationDetail,
    *,
    hardware_selection: HardwareSelectionState | None,
) -> ConversationDetail:
    """Replace/revoke only current projections, preserving the input aggregate."""
    if type(detail) is not ConversationDetail:
        raise TypeError("detail must be ConversationDetail")
    if (
        hardware_selection is not None
        and type(hardware_selection) is not HardwareSelectionState
    ):
        raise TypeError("hardware_selection must be HardwareSelectionState or None")
    checked = ConversationDetail.model_validate(detail.model_dump(mode="python"))
    payload = checked.model_dump(mode="python")
    for name in _lost_fields(checked):
        payload[name] = ConversationDetail.model_fields[name].get_default()
    payload.update(hardware_selection=hardware_selection, fingerprint=_ZERO)
    return ConversationDetail.model_validate(payload)
