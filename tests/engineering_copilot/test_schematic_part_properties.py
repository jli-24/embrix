from __future__ import annotations

import hashlib

import pytest

from embedded_copilot.engineering_copilot import serialize_kicad_schematic
from embedded_copilot.engineering_copilot.hardware_selection import (
    HardwareSelectionState,
    HardwareSelectionStatus,
)
from embedded_copilot.engineering_copilot.schematic_planning import (
    SchematicPlanningStatus,
)
from embedded_copilot.pcb.parser.kicad import _parse_s_expression
from embedded_copilot.pcb.parser.models import PCBParserLimits

from .test_schematic_production import _proposal
from .test_schematic_production_g3 import _runtime_with_approved_plan


@pytest.fixture(scope="module")
def approved_parts(tmp_path_factory):
    _, _, _, conversation = _runtime_with_approved_plan(tmp_path_factory.mktemp("approved-parts"))
    return (
        conversation.schematic_production.proposal,
        conversation.schematic_planning.approved_plan,
        conversation.hardware_selection,
    )


def _recreate(model, **changes):
    material = {name: getattr(model, name) for name in type(model).model_fields if name != "fingerprint"}
    material.update(changes)
    return type(model).create(**material)


def _plan_proposal(proposal, plan):
    return _recreate(proposal, schematic_plan_fingerprint=plan.fingerprint)


def _replace_source(proposal, plan, selection, **changes):
    selected = selection.proposal.selected_components[0]
    source = _recreate(selected.source, **changes)
    selected = selected.model_copy(update={"source": source})
    selected_items = (selected, *selection.proposal.selected_components[1:])
    selection_proposal = _recreate(selection.proposal, selected_components=selected_items)
    bom_item = selection.bom.items[0].model_copy(update={
        "manufacturer": source.manufacturer, "mpn": source.mpn,
        "symbol": source.symbol, "footprint": source.footprint,
        "evidence_fingerprints": (source.fingerprint,),
    })
    bom = _recreate(selection.bom, items=(bom_item, *selection.bom.items[1:]))
    approval = _recreate(selection.approval, selection_fingerprint=selection_proposal.fingerprint,
        bom_fingerprint=bom.fingerprint,
        evidence_fingerprints=tuple(dict.fromkeys(item.source.fingerprint for item in selected_items)))
    truth_item = selection.hardware_truth.components[0].model_copy(update={
        "manufacturer": source.manufacturer, "mpn": source.mpn,
        "symbol": source.symbol, "footprint": source.footprint,
        "source_fingerprint": source.fingerprint,
    })
    truth = _recreate(selection.hardware_truth, bom_fingerprint=bom.fingerprint,
        approval_fingerprint=approval.fingerprint,
        components=(truth_item, *selection.hardware_truth.components[1:]))
    selection = HardwareSelectionState(proposal=selection_proposal,
        status=HardwareSelectionStatus.APPROVED, approval=approval, bom=bom, hardware_truth=truth)
    instance = plan.component_instances[0]
    instance = instance.model_copy(update={
        "manufacturer": source.manufacturer, "mpn": source.mpn,
        "symbol": source.symbol, "footprint": source.footprint,
        "evidence_fingerprints": (source.fingerprint,),
    })
    plan = _recreate(plan, component_instances=(instance, *plan.component_instances[1:]),
        bom_fingerprint=bom.fingerprint, hardware_truth_fingerprint=truth.fingerprint)
    return _plan_proposal(proposal, plan), plan, selection


def test_legacy_bytes_remain_identical():
    assert hashlib.sha256(serialize_kicad_schematic(_proposal())).hexdigest() == (
        "84e52e6d74ade8fb57273432ebd1bef6e283e43a8293efe74685d7ab8fc4e9da"
    )


def test_approved_properties_are_exact_on_placed_symbols(approved_parts):
    proposal, plan, selection = approved_parts
    content = serialize_kicad_schematic(proposal, approved_schematic_plan=plan, hardware_selection=selection)
    root = _parse_s_expression(content.decode("utf-8"), PCBParserLimits())
    placed = [node for node in root if isinstance(node, tuple) and node[0] == "symbol"]
    by_reference = {
        next(node[2] for node in symbol if isinstance(node, tuple) and node[:2] == ("property", "Reference")):
        {node[1]: node[2] for node in symbol if isinstance(node, tuple) and node[0] == "property"}
        for symbol in placed
    }
    selected_by_slot = {item.slot_id: item.source for item in selection.proposal.selected_components}
    assert len(by_reference) == len(proposal.components)
    for component, instance in zip(proposal.components, plan.component_instances, strict=True):
        source = selected_by_slot[instance.hardware_slot_id]
        assert by_reference[component.reference] == {
            "Reference": component.reference, "Value": component.value, "Datasheet": "~",
            "Footprint": source.footprint, "Manufacturer": source.manufacturer,
            "MPN": source.mpn, "Package": source.package,
        }


@pytest.mark.parametrize("only", ["plan", "selection"])
def test_partial_pair_is_rejected(approved_parts, only):
    proposal, plan, selection = approved_parts
    kwargs = {"approved_schematic_plan": plan} if only == "plan" else {"hardware_selection": selection}
    with pytest.raises(ValueError, match="together"):
        serialize_kicad_schematic(proposal, **kwargs)


@pytest.mark.parametrize("field,value", [
    ("project_id", "different-project"), ("conversation_id", "different-conversation"),
    ("schematic_plan_id", "different-plan"), ("schematic_plan_fingerprint", "sha256:" + "1" * 64),
    ("target_relative_path", "hardware/different.kicad_sch"),
])
def test_proposal_binding_mismatch_is_rejected(approved_parts, field, value):
    proposal, plan, selection = approved_parts
    proposal = _recreate(proposal, **{field: value})
    with pytest.raises(ValueError):
        serialize_kicad_schematic(proposal, approved_schematic_plan=plan, hardware_selection=selection)


@pytest.mark.parametrize("field,value", [
    ("hardware_truth_id", "other-truth"), ("hardware_truth_fingerprint", "sha256:" + "2" * 64),
    ("bom_id", "other-bom"), ("bom_fingerprint", "sha256:" + "2" * 64),
])
def test_plan_selection_binding_mismatch_is_rejected(approved_parts, field, value):
    proposal, plan, selection = approved_parts
    plan = _recreate(plan, **{field: value})
    proposal = _plan_proposal(proposal, plan)
    with pytest.raises(ValueError):
        serialize_kicad_schematic(proposal, approved_schematic_plan=plan, hardware_selection=selection)


@pytest.mark.parametrize("field,value", [
    ("manufacturer", "Different manufacturer"), ("mpn", "Different-MPN"),
    ("footprint", "Other:Footprint"), ("symbol", "Other:Symbol"),
    ("hardware_slot_id", "different-slot"), ("quantity", 2),
    ("source_refs", ("unbound-source",)), ("evidence_fingerprints", ("sha256:" + "3" * 64,)),
])
def test_instance_selection_mismatch_is_rejected(approved_parts, field, value):
    proposal, plan, selection = approved_parts
    instance = plan.component_instances[0].model_copy(update={field: value})
    plan = _recreate(plan, component_instances=(instance, *plan.component_instances[1:]))
    proposal = _plan_proposal(proposal, plan)
    with pytest.raises(ValueError):
        serialize_kicad_schematic(proposal, approved_schematic_plan=plan, hardware_selection=selection)


@pytest.mark.parametrize("package", ["UNKNOWN", "TBD", "N/A", "NONE", "-", "QFN\tunsafe"])
def test_unknown_or_control_character_package_is_rejected(approved_parts, package):
    proposal, plan, selection = _replace_source(*approved_parts, package=package)
    with pytest.raises(ValueError):
        serialize_kicad_schematic(proposal, approved_schematic_plan=plan, hardware_selection=selection)


@pytest.mark.parametrize("field", ["package", "footprint"])
def test_missing_source_property_is_rejected(approved_parts, field):
    proposal, plan, selection = approved_parts
    selection = selection.model_copy(deep=True)
    source = selection.proposal.selected_components[0].source
    object.__setattr__(source, field, "")
    with pytest.raises(ValueError):
        serialize_kicad_schematic(proposal, approved_schematic_plan=plan, hardware_selection=selection)


def test_tampered_selection_is_revalidated(approved_parts):
    proposal, plan, selection = approved_parts
    selection = selection.model_copy(deep=True)
    object.__setattr__(selection.proposal.selected_components[0].source, "manufacturer", "tampered")
    with pytest.raises(ValueError):
        serialize_kicad_schematic(proposal, approved_schematic_plan=plan, hardware_selection=selection)


@pytest.mark.parametrize("which", ["plan", "selection"])
def test_arbitrary_dict_is_rejected(approved_parts, which):
    proposal, plan, selection = approved_parts
    if which == "plan":
        plan = plan.model_dump(mode="python")
    else:
        selection = selection.model_dump(mode="python")
    with pytest.raises(TypeError):
        serialize_kicad_schematic(proposal, approved_schematic_plan=plan, hardware_selection=selection)


def test_unapproved_pair_is_rejected(approved_parts):
    proposal, plan, selection = approved_parts
    plan = plan.model_copy(deep=True)
    object.__setattr__(plan, "status", SchematicPlanningStatus.REVIEW)
    with pytest.raises(ValueError):
        serialize_kicad_schematic(proposal, approved_schematic_plan=plan, hardware_selection=selection)


def test_quoted_source_package_cannot_inject_schematic_properties(approved_parts):
    package = 'QFN "special" \\custom (property "Injected" "yes")'
    proposal, plan, selection = _replace_source(*approved_parts, package=package)
    content = serialize_kicad_schematic(proposal, approved_schematic_plan=plan, hardware_selection=selection)
    root = _parse_s_expression(content.decode("utf-8"), PCBParserLimits())
    placed = [node for node in root if isinstance(node, tuple) and node[0] == "symbol"]
    properties = [{node[1]: node[2] for node in symbol
        if isinstance(node, tuple) and node[0] == "property"} for symbol in placed]
    assert properties[0]["Package"] == package
    assert all("Injected" not in item for item in properties)


def test_unapproved_selection_is_rejected(approved_parts):
    proposal, plan, selection = approved_parts
    selection = HardwareSelectionState(proposal=selection.proposal, status=HardwareSelectionStatus.REVIEW)
    with pytest.raises(ValueError, match="approved inputs"):
        serialize_kicad_schematic(proposal, approved_schematic_plan=plan, hardware_selection=selection)


def test_plan_bound_proposal_cannot_omit_approved_property_pair(approved_parts):
    proposal, _, _ = approved_parts
    with pytest.raises(ValueError, match="approved"):
        serialize_kicad_schematic(proposal)
