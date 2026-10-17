from __future__ import annotations

from datetime import UTC, datetime

from embedded_copilot.engineering_requirements import (
    RequirementContext,
    RequirementItem,
    RequirementItemStatus,
    RequirementLifecycle,
    RequirementPriority,
    RequirementProvenance,
    RequirementStatus,
)
from embedded_copilot.manufacturing import (
    FabricationMode,
    ManufacturingParameterApprovalState,
    ManufacturingParameterDecision,
    ManufacturingReadinessStatus,
    ManufacturingSpecification,
    audit_manufacturing_parameters,
    close_manufacturing_parameters,
)

NOW = datetime(2026, 9, 9, tzinfo=UTC)


def _spec() -> ManufacturingSpecification:
    return ManufacturingSpecification.create(
        specification_id="spec-closure",
        project_id="project-closure",
        project_revision="revision-1",
        fabrication_mode=FabricationMode.PCB_ONLY,
        layer_count=2,
        copper_layers=("F.Cu", "B.Cu"),
        source_refs=("requirement-1",),
    )


def _decision(parameter: str, value: str, *, state: ManufacturingParameterApprovalState = ManufacturingParameterApprovalState.APPROVED) -> ManufacturingParameterDecision:
    return ManufacturingParameterDecision.create(
        id=f"decision-{parameter}",
        project_id="project-closure",
        parameter=parameter,
        value=value,
        source="user-approval-1",
        approval_state=state,
        created_at=NOW,
    )


def test_audit_keeps_unspecified_values_and_never_uses_pcb_or_bom_defaults() -> None:
    audits, decisions = audit_manufacturing_parameters(
        specification=_spec(),
        pcb_artifact={"board_thickness_mm": 1.6, "surface_finish": "HASL"},
        bom_artifact={"copper_weight": "1oz"},
        project_id="project-closure",
        now=NOW,
    )

    assert {item.parameter for item in audits if item.status is ManufacturingParameterApprovalState.UNSPECIFIED} == {
        "board_thickness", "surface_finish", "copper_weight", "solder_mask", "silkscreen"
    }
    assert all(item.value == "UNSPECIFIED" for item in decisions)


def test_audit_does_not_promote_design_truth_or_existing_evidence_to_approval() -> None:
    audits, _ = audit_manufacturing_parameters(
        specification=_spec(),
        design_truth={"surface_finish": "ENIG"},
        existing_evidence=({"copper_weight": "2 oz"},),
        project_id="project-closure",
        now=NOW,
    )

    assert all(item.status is ManufacturingParameterApprovalState.UNSPECIFIED for item in audits)


def test_audit_rejects_conflicting_explicit_sources() -> None:
    audits, _ = audit_manufacturing_parameters(
        specification=_spec(),
        requirement_specification={"surface_finish": "ENIG"},
        engineering_decision={"surface_finish": "HASL"},
        project_id="project-closure",
        now=NOW,
    )

    finish = next(item for item in audits if item.parameter == "surface_finish")
    assert finish.status is ManufacturingParameterApprovalState.REJECTED


def test_existing_specification_value_conflicting_with_requirement_is_rejected() -> None:
    values = _spec().model_dump(mode="python")
    values.update({"surface_finish": "ENIG"})
    values.pop("fingerprint", None)
    specification = ManufacturingSpecification.create(**values)

    audits, _ = audit_manufacturing_parameters(
        specification=specification,
        requirement_specification={"surface_finish": "HASL"},
        project_id="project-closure",
        now=NOW,
    )

    finish = next(item for item in audits if item.parameter == "surface_finish")
    assert finish.status is ManufacturingParameterApprovalState.REJECTED


def test_audit_accepts_explicit_structured_parameter_entries() -> None:
    audits, decisions = audit_manufacturing_parameters(
        specification=_spec(),
        requirement_specification={
            "manufacturing_parameters": [
                {"parameter": "surface_finish", "value": "ENIG"},
            ],
        },
        project_id="project-closure",
        now=NOW,
    )

    finish = next(item for item in audits if item.parameter == "surface_finish")
    assert finish.status is ManufacturingParameterApprovalState.APPROVED
    assert any(item.parameter == "surface_finish" and item.value == "ENIG" for item in decisions)


def test_confirmed_requirement_items_are_explicit_parameter_sources() -> None:
    requirement_items = tuple(
        RequirementItem(
            requirement_id=f"requirement-{parameter}",
            project_id="project-closure",
            conversation_id="conversation-closure",
            category=parameter,
            normalized_value=value,
            status=RequirementItemStatus.CONFIRMED,
            priority=RequirementPriority.MUST,
            source_turn_id="turn-closure",
            provenance=RequirementProvenance.USER_UTTERANCE,
        )
        for parameter, value in (
            ("board_thickness", "1.2 mm"),
            ("surface_finish", "ENIG"),
            ("copper_weight", "2 oz"),
            ("solder_mask", "black"),
            ("silkscreen", "white"),
        )
    )
    requirement = RequirementContext(
        project_id="project-closure",
        conversation_id="conversation-closure",
        requirements=requirement_items,
        status=RequirementStatus.CONFIRMED,
        lifecycle=RequirementLifecycle.REQUIREMENTS_READY,
    )

    audits, _ = audit_manufacturing_parameters(
        specification=_spec(),
        requirement_specification=requirement,
        project_id="project-closure",
        now=NOW,
    )

    assert all(item.status is ManufacturingParameterApprovalState.APPROVED for item in audits)


def test_conflicting_confirmed_requirement_items_are_rejected() -> None:
    requirement_items = tuple(
        RequirementItem(
            requirement_id=f"requirement-conflict-{value.casefold()}",
            project_id="project-closure",
            conversation_id="conversation-closure",
            category="surface_finish",
            normalized_value=value,
            status=RequirementItemStatus.CONFIRMED,
            priority=RequirementPriority.MUST,
            source_turn_id="turn-closure",
            provenance=RequirementProvenance.USER_UTTERANCE,
        )
        for value in ("ENIG", "HASL")
    )
    requirement = RequirementContext(
        project_id="project-closure",
        conversation_id="conversation-closure",
        requirements=requirement_items,
        status=RequirementStatus.CONFIRMED,
        lifecycle=RequirementLifecycle.REQUIREMENTS_READY,
    )

    audits, _ = audit_manufacturing_parameters(
        specification=_spec(),
        requirement_specification=requirement,
        project_id="project-closure",
        now=NOW,
    )

    finish = next(item for item in audits if item.parameter == "surface_finish")
    assert finish.status is ManufacturingParameterApprovalState.REJECTED


def test_parameter_entry_without_value_remains_unspecified() -> None:
    audits, _ = audit_manufacturing_parameters(
        specification=_spec(),
        requirement_specification={"parameter": "surface_finish"},
        project_id="project-closure",
        now=NOW,
    )

    finish = next(item for item in audits if item.parameter == "surface_finish")
    assert finish.status is ManufacturingParameterApprovalState.UNSPECIFIED


def test_proposed_requirement_item_does_not_close_parameter() -> None:
    requirement = RequirementItem(
        requirement_id="requirement-proposed-finish",
        project_id="project-closure",
        conversation_id="conversation-closure",
        category="surface_finish",
        normalized_value="ENIG",
        status=RequirementItemStatus.PROPOSED,
        priority=RequirementPriority.MUST,
        source_turn_id="turn-closure",
        provenance=RequirementProvenance.USER_UTTERANCE,
    )

    audits, _ = audit_manufacturing_parameters(
        specification=_spec(),
        requirement_specification=requirement,
        project_id="project-closure",
        now=NOW,
    )

    finish = next(item for item in audits if item.parameter == "surface_finish")
    assert finish.status is ManufacturingParameterApprovalState.UNSPECIFIED


def test_proposed_parameter_entry_does_not_close_parameter() -> None:
    audits, _ = audit_manufacturing_parameters(
        specification=_spec(),
        requirement_specification={
            "manufacturing_parameters": [
                {
                    "parameter": "surface_finish",
                    "value": "ENIG",
                    "approval_state": "PROPOSED",
                },
            ],
        },
        project_id="project-closure",
        now=NOW,
    )

    finish = next(item for item in audits if item.parameter == "surface_finish")
    assert finish.status is ManufacturingParameterApprovalState.UNSPECIFIED


def test_nested_proposed_parameter_value_does_not_close_parameter() -> None:
    audits, _ = audit_manufacturing_parameters(
        specification=_spec(),
        requirement_specification={
            "surface_finish": {
                "value": "ENIG",
                "approval_state": "PROPOSED",
            },
        },
        project_id="project-closure",
        now=NOW,
    )

    finish = next(item for item in audits if item.parameter == "surface_finish")
    assert finish.status is ManufacturingParameterApprovalState.UNSPECIFIED


def test_blank_requirement_parameter_remains_unspecified() -> None:
    audits, _ = audit_manufacturing_parameters(
        specification=_spec(),
        requirement_specification={"surface_finish": "  "},
        project_id="project-closure",
        now=NOW,
    )

    finish = next(item for item in audits if item.parameter == "surface_finish")
    assert finish.status is ManufacturingParameterApprovalState.UNSPECIFIED


def test_requirement_values_create_lineage_decisions_that_can_close_specification() -> None:
    audits, decisions = audit_manufacturing_parameters(
        specification=_spec(),
        requirement_specification={
            "board_thickness_mm": 1.6,
            "surface_finish": "ENIG",
            "copper_weight": "1 oz",
            "solder_mask": "green",
            "silkscreen": "white",
        },
        project_id="project-closure",
        now=NOW,
    )

    assert all(item.status is ManufacturingParameterApprovalState.APPROVED for item in audits)
    closed = close_manufacturing_parameters(_spec(), decisions)
    assert closed.status.value == "COMPLETE"
    assert set(closed.source_refs) >= {"RequirementSpecification"}


def test_approved_parameter_decisions_close_specification_with_lineage() -> None:
    decisions = tuple(
        _decision(parameter, value)
        for parameter, value in (
            ("board_thickness", "1.2"),
            ("surface_finish", "ENIG"),
            ("copper_weight", "2 oz"),
            ("solder_mask", "black"),
            ("silkscreen", "white"),
        )
    )
    closed = close_manufacturing_parameters(_spec(), decisions)

    assert closed.status.value == "COMPLETE"
    assert closed.board_thickness_mm == 1.2
    assert "user-approval-1" in closed.source_refs
    assert all(item.value != "UNSPECIFIED" for item in decisions)


def test_parameter_readiness_projection_is_blocked_until_all_decisions_are_approved() -> None:
    audits, _ = audit_manufacturing_parameters(specification=_spec(), project_id="project-closure", now=NOW)
    missing = tuple(item.parameter for item in audits if item.status is not ManufacturingParameterApprovalState.APPROVED)

    from embedded_copilot.manufacturing import ManufacturingReadinessResult

    result = ManufacturingReadinessResult.create(
        fabrication_mode=FabricationMode.PCB_ONLY,
        parameter_completeness=False,
        missing_parameters=missing,
        pcb_revision="revision-1",
        artifact_fingerprint="sha256:" + "a" * 64,
        status=ManufacturingReadinessStatus.BLOCKED_MANUFACTURING_PARAMETER_REQUIRED,
    )
    assert result.status is ManufacturingReadinessStatus.BLOCKED_MANUFACTURING_PARAMETER_REQUIRED


def test_factory_defaults_flag_cannot_close_missing_parameters() -> None:
    values = _spec().model_dump(mode="python")
    values["allow_factory_defaults"] = True
    values.pop("fingerprint", None)
    specification = ManufacturingSpecification.create(**values)

    assert specification.status.value == "INCOMPLETE"


def test_explicit_thickness_unit_is_normalized_without_using_a_factory_default() -> None:
    values = _spec().model_dump(mode="python")
    values["board_thickness_mm"] = "UNSPECIFIED"
    values.pop("fingerprint", None)
    unspecified = ManufacturingSpecification.create(**values)
    assert unspecified.status.value == "INCOMPLETE"

    closed = close_manufacturing_parameters(
        unspecified,
        (_decision("board_thickness", "1.60 mm"),),
    )
    assert closed.board_thickness_mm == 1.6
    assert closed.status.value == "INCOMPLETE"


def test_numeric_user_decision_is_canonicalized_as_explicit_lineage() -> None:
    decision = _decision("board_thickness", 1.6)  # type: ignore[arg-type]
    assert decision.value == "1.6"
    assert close_manufacturing_parameters(_spec(), (decision,)).board_thickness_mm == 1.6
