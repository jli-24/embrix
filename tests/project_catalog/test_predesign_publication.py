from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from embedded_copilot.application.circuit_publication import (
    CircuitPublicationService,
    PredesignDecisionCommand,
    PredesignPreviewCommand,
    PredesignSourceExpectations,
)
from embedded_copilot.application.engineering_runtime_integration import (
    CircuitIntentSpec,
)
from embedded_copilot.engineering_copilot import (
    DesignTruthBuilder,
    HardwareSelectionDecision,
    HardwareSelectionService,
    approve_engineering_plan,
    generate_engineering_workflow,
)
from embedded_copilot.engineering_requirements import generate_engineering_plan
from embedded_copilot.engineering_runtime import (
    CalculationRecord,
    CircuitBlock,
    CircuitDecision,
    VerificationRequirement,
)
from embedded_copilot.project_catalog import (
    ProjectCatalogConflict,
    ProjectCatalogForbidden,
    ProjectCatalogRejected,
    ProjectCreationRequest,
    ProjectType,
)
from embedded_copilot.project_catalog.models import (
    ConversationDetail,
    ConversationTurn,
    ConversationTurnStatus,
    predesign_source_snapshot,
)
from tests.engineering_copilot.test_schematic_planning_g2 import (
    _catalog,
    _golden_architecture,
)
from tests.project_catalog.test_circuit_catalog_persistence import (
    NOW,
    OWNER,
    TENANT,
    _runtime,
    _seed,
)


def _approved_catalog(tmp_path):
    catalog, project_id, detail = _seed(tmp_path)
    requirements, architecture, architecture_review = _golden_architecture(
        project_id, detail.conversation_id,
    )
    plan, plan_review = approve_engineering_plan(
        generate_engineering_plan(requirements), reviewer=OWNER, decided_at=NOW,
    )
    workflow = generate_engineering_workflow(architecture, plan)
    selection_service = HardwareSelectionService(_catalog())
    selection = selection_service.decide(
        selection_service.create_proposal(
            architecture, requirements, workflow=workflow,
            conversation_id=detail.conversation_id,
        ),
        HardwareSelectionDecision.APPROVED, reviewer=OWNER, decided_at=NOW,
    )
    design_truth = selection_service.project_design_truth(
        DesignTruthBuilder().build_from_approved_architecture(
            architecture, tenant_id=TENANT, conversation_id=detail.conversation_id,
            architecture_review=architecture_review,
        ),
        selection.hardware_truth, architecture_review=architecture_review,
    )
    detail = detail.model_copy(update={
        "requirement_context": requirements, "engineering_plan": plan,
        "engineering_plan_review": plan_review, "architecture_design": architecture,
        "architecture_review": architecture_review, "engineering_workflow": workflow,
        "hardware_selection": selection, "design_truth": design_truth,
        "fingerprint": "sha256:" + "0" * 64,
    })
    catalog.persist_conversation_detail(ConversationDetail.model_validate(detail.model_dump(mode="python")))
    return catalog, project_id, catalog.conversation(project_id, detail.conversation_id, tenant_id=TENANT, requester=OWNER)


def _service(catalog):
    def forbidden(*args, **kwargs):
        raise AssertionError("predesign must not access ERC or model flow")

    application = SimpleNamespace(classify=forbidden, run_engineering_object_flow=forbidden)
    execution = SimpleNamespace(get_operation=forbidden, get_status=forbidden, get_receipt=forbidden)
    return CircuitPublicationService(catalog=catalog, application=application, execution=execution)


def _other_project(catalog) -> str:
    preview = catalog.preview(
        ProjectCreationRequest(
            name="Other Design Sources", relative_path="other-design-sources",
            project_type=ProjectType.STM32,
        ), tenant_id=TENANT, requester=OWNER,
    )
    return catalog.create(
        preview.preview_id, preview.fingerprint, confirmed_at=NOW,
        tenant_id=TENANT, requester=OWNER,
    ).project_id


def _foreign_source_detail(detail, category: str, foreign_project_id: str):
    if category == "requirement_or_constraint":
        return detail.model_copy(update={
            "requirement_context": detail.requirement_context.model_copy(update={
                "project_id": foreign_project_id,
            }),
        })
    if category in {"architecture", "architecture_review"}:
        field = "architecture_design" if category == "architecture" else "architecture_review"
        return detail.model_copy(update={
            field: getattr(detail, field).model_copy(update={"project_id": foreign_project_id}),
        })
    field = {"hardware_approval": "approval", "hardware_bom": "bom", "hardware_truth": "hardware_truth"}[category]
    selection = detail.hardware_selection
    return detail.model_copy(update={
        "hardware_selection": selection.model_copy(update={
            field: getattr(selection, field).model_copy(update={"project_id": foreign_project_id}),
        }),
    })


def _command(detail):
    source = predesign_source_snapshot(detail, "turn-design")
    requirement_id = next(
        item.requirement_id for item in (*detail.requirement_context.requirements, *detail.requirement_context.constraints)
        if item.status.value == "CONFIRMED"
    )
    architecture_id = detail.architecture_design.system_components[0].component_id
    return PredesignPreviewCommand(
        purpose="PREDESIGN_APPROVAL", conversation_id=detail.conversation_id,
        turn_id="turn-design", circuit_intent=CircuitIntentSpec(
            function="STM32 power", topology=("VIN -> 3V3",),
            blocks=(CircuitBlock(
                block_id="block-power", function="Power conversion",
                requirement_refs=(requirement_id,), architecture_refs=(architecture_id,),
                verification_refs=("verify-power",),
            ),),
            verification_requirements=(VerificationRequirement(
                verification_id="verify-power", owner_ref="block-power",
                claim="Power rail is valid", verification_type="DESIGN_REVIEW",
                method="Review the produced schematic and source evidence",
            ),),
        ), idempotency_key="predesign-1",
        source_expectations=PredesignSourceExpectations(
            requirement_context_fingerprint=source.requirement_fingerprint,
            architecture_id=source.architecture_id,
            architecture_fingerprint=source.architecture_fingerprint,
            hardware_selection_approval_id=source.hardware_selection_approval_id,
            hardware_truth_fingerprint=source.hardware_truth_fingerprint,
        ),
    )


@pytest.mark.parametrize("field,refs", [
    ("requirement_refs", ()),
    ("architecture_refs", ()),
    ("requirement_refs", ("missing-requirement",)),
    ("architecture_refs", ("missing-architecture",)),
])
def test_predesign_rejects_unbound_block_source(tmp_path, field, refs) -> None:
    catalog, project_id, detail = _approved_catalog(tmp_path)
    command = _command(detail)
    block = CircuitBlock.model_validate({
        **command.circuit_intent.blocks[0].model_dump(mode="python"),
        field: refs, "fingerprint": "sha256:" + "0" * 64,
    })
    with pytest.raises(ProjectCatalogRejected, match="block source"):
        _service(catalog).preview(
            project_id=project_id, tenant_id=TENANT, requester=OWNER,
            command=command.model_copy(update={
                "circuit_intent": command.circuit_intent.model_copy(update={"blocks": (block,)}),
            }),
        )


@pytest.mark.parametrize("record_kind", [
    "dangling_decision", "dangling_calculation", "dangling_claim", "wrong_decision_owner",
    "wrong_calculation_owner", "cross_block_claim", "cross_block_decision",
    "cross_block_calculation", "orphan_claim",
])
def test_predesign_rejects_dangling_or_cross_block_links(tmp_path, record_kind) -> None:
    catalog, project_id, detail = _approved_catalog(tmp_path)
    command = _command(detail)
    block = command.circuit_intent.blocks[0]
    updates = {}
    extra_blocks = ()
    if record_kind == "dangling_decision":
        block = CircuitBlock.model_validate({**block.model_dump(mode="python"), "decision_refs": ("missing-decision",), "fingerprint": "sha256:" + "0" * 64})
    elif record_kind == "dangling_calculation":
        block = CircuitBlock.model_validate({**block.model_dump(mode="python"), "calculation_refs": ("missing-calculation",), "fingerprint": "sha256:" + "0" * 64})
    elif record_kind == "dangling_claim":
        block = CircuitBlock.model_validate({
            **block.model_dump(mode="python"),
            "verification_refs": ("verify-power", "missing-claim"),
            "fingerprint": "sha256:" + "0" * 64,
        })
    elif record_kind == "wrong_decision_owner":
        updates["decisions"] = (CircuitDecision(
            decision_id="decision-1", block_ref="missing-block", decision_type="PART",
            selected_option="STM32", rationale="Review",
        ),)
    elif record_kind == "wrong_calculation_owner":
        updates["calculations"] = (CalculationRecord(
            calculation_id="calculation-1", owner_ref="missing-block", equation="P = V * I",
        ),)
    elif record_kind == "orphan_claim":
        updates["verification_requirements"] = (
            *command.circuit_intent.verification_requirements,
            VerificationRequirement(
                verification_id="verify-orphan", owner_ref="block-power", claim="Check orphan",
                verification_type="DESIGN_REVIEW", method="Review",
            ),
        )
    else:
        second = CircuitBlock(
            block_id="block-two", function="Second block",
            requirement_refs=block.requirement_refs, architecture_refs=block.architecture_refs,
            verification_refs=("verify-two",),
        )
        extra_blocks = (second,)
        updates["verification_requirements"] = (
            *command.circuit_intent.verification_requirements,
            VerificationRequirement(
                verification_id="verify-two", owner_ref="block-two", claim="Check second",
                verification_type="DESIGN_REVIEW", method="Review",
            ),
        )
        if record_kind == "cross_block_claim":
            block = CircuitBlock.model_validate({
                **block.model_dump(mode="python"), "verification_refs": ("verify-two",),
                "fingerprint": "sha256:" + "0" * 64,
            })
        elif record_kind == "cross_block_decision":
            block = CircuitBlock.model_validate({
                **block.model_dump(mode="python"), "decision_refs": ("decision-two",),
                "fingerprint": "sha256:" + "0" * 64,
            })
            second = CircuitBlock.model_validate({
                **second.model_dump(mode="python"), "decision_refs": ("decision-two",),
                "fingerprint": "sha256:" + "0" * 64,
            })
            extra_blocks = (second,)
            updates["decisions"] = (CircuitDecision(
                decision_id="decision-two", block_ref="block-two", decision_type="PART",
                selected_option="STM32", rationale="Review",
            ),)
        else:
            block = CircuitBlock.model_validate({
                **block.model_dump(mode="python"), "calculation_refs": ("calculation-two",),
                "fingerprint": "sha256:" + "0" * 64,
            })
            second = CircuitBlock.model_validate({
                **second.model_dump(mode="python"), "calculation_refs": ("calculation-two",),
                "fingerprint": "sha256:" + "0" * 64,
            })
            extra_blocks = (second,)
            updates["calculations"] = (CalculationRecord(
                calculation_id="calculation-two", owner_ref="block-two", equation="P = V * I",
            ),)
    spec = command.circuit_intent.model_copy(update={"blocks": (block, *extra_blocks), **updates})
    with pytest.raises(ProjectCatalogRejected, match="claim bindings"):
        _service(catalog).preview(
            project_id=project_id, tenant_id=TENANT, requester=OWNER,
            command=command.model_copy(update={"circuit_intent": spec}),
        )


def test_predesign_link_recheck_rejects_dangling_block_claim(tmp_path) -> None:
    _catalog, _project_id, detail = _approved_catalog(tmp_path)
    spec = _command(detail).circuit_intent
    block = CircuitBlock.model_validate({
        **spec.blocks[0].model_dump(mode="python"),
        "verification_refs": ("verify-power", "missing-claim"),
        "fingerprint": "sha256:" + "0" * 64,
    })
    with pytest.raises(ProjectCatalogRejected, match="claim bindings"):
        CircuitPublicationService._check_predesign_links(
            detail, spec.model_copy(update={"blocks": (block,)}),
        )


@pytest.mark.parametrize("field,value", [
    ("answer_text", "Revised circuit answer"),
    ("evidence", ({"source": "revised-fixture"},)),
])
def test_predesign_selected_turn_content_change_stales_review(tmp_path, field, value) -> None:
    catalog, project_id, detail = _approved_catalog(tmp_path)
    service = _service(catalog)
    candidate = service.preview(
        project_id=project_id, tenant_id=TENANT, requester=OWNER, command=_command(detail),
    )
    changed_turn = ConversationTurn.model_validate({
        **detail.turns[0].model_dump(mode="python"), field: value,
        "fingerprint": "sha256:" + "0" * 64,
    })
    changed = ConversationDetail.model_validate(detail.model_copy(update={
        "turns": (changed_turn,), "fingerprint": "sha256:" + "0" * 64,
    }).model_dump(mode="python"))
    catalog.persist_conversation_detail(changed)
    with pytest.raises(ProjectCatalogConflict, match="source changed"):
        service.decide(
            project_id=project_id, tenant_id=TENANT, requester=OWNER,
            command=PredesignDecisionCommand(
                purpose="PREDESIGN_APPROVAL", proposal_id=candidate.proposal.proposal_id,
                proposal_fingerprint=candidate.proposal.fingerprint,
                source_fingerprint=candidate.proposal.context_fingerprint,
                decision="APPROVE",
            ),
        )


def test_legacy_weak_turn_snapshot_remains_readable_but_stale(tmp_path, monkeypatch) -> None:
    from embedded_copilot.application import circuit_publication as publication_module
    from embedded_copilot.project_catalog import store as catalog_store

    catalog, project_id, detail = _approved_catalog(tmp_path)
    current_snapshot = predesign_source_snapshot

    def legacy_snapshot(source_detail, turn_id):
        source = current_snapshot(source_detail, turn_id)
        turn = next(item for item in source_detail.turns if item.turn_id == turn_id)
        material = {
            "turn_id": turn.turn_id, "request_id": turn.request_id,
            "route": turn.route, "status": turn.status.value, "question": turn.question,
        }
        legacy_fingerprint = "sha256:" + hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return source.model_copy(update={"turn_fingerprint": legacy_fingerprint})

    with monkeypatch.context() as patch:
        patch.setattr(publication_module, "predesign_source_snapshot", legacy_snapshot)
        patch.setattr(catalog_store, "predesign_source_snapshot", legacy_snapshot)
        candidate = _service(catalog).preview(
            project_id=project_id, tenant_id=TENANT, requester=OWNER, command=_command(detail),
        )
        published = _service(catalog).decide(
            project_id=project_id, tenant_id=TENANT, requester=OWNER,
            command=PredesignDecisionCommand(
                purpose="PREDESIGN_APPROVAL", proposal_id=candidate.proposal.proposal_id,
                proposal_fingerprint=candidate.proposal.fingerprint,
                source_fingerprint=candidate.proposal.context_fingerprint,
                decision="APPROVE",
            ),
        )
    reopened = _runtime(tmp_path)
    assert reopened.current_circuit_intents(project_id, tenant_id=TENANT, requester=OWNER) == (published.circuit,)
    assert not reopened.circuit_source_current(
        project_id, published.circuit.object_id, published.circuit.revision,
        tenant_id=TENANT, requester=OWNER,
    )


def test_predesign_decision_rechecks_legacy_unbound_block(tmp_path, monkeypatch) -> None:
    catalog, project_id, detail = _approved_catalog(tmp_path)
    service = _service(catalog)
    command = _command(detail)
    block = command.circuit_intent.blocks[0]
    unbound = CircuitBlock.model_validate({
        **block.model_dump(mode="python"), "requirement_refs": (),
        "fingerprint": "sha256:" + "0" * 64,
    })
    old_command = command.model_copy(update={
        "circuit_intent": command.circuit_intent.model_copy(update={"blocks": (unbound,)}),
    })
    with monkeypatch.context() as patch:
        patch.setattr(CircuitPublicationService, "_check_predesign_links", staticmethod(lambda _detail, _spec: None))
        candidate = service.preview(
            project_id=project_id, tenant_id=TENANT, requester=OWNER, command=old_command,
        )
    with pytest.raises(ProjectCatalogRejected, match="block source"):
        service.decide(
            project_id=project_id, tenant_id=TENANT, requester=OWNER,
            command=PredesignDecisionCommand(
                purpose="PREDESIGN_APPROVAL", proposal_id=candidate.proposal.proposal_id,
                proposal_fingerprint=candidate.proposal.fingerprint,
                source_fingerprint=candidate.proposal.context_fingerprint,
                decision="APPROVE",
            ),
        )


def test_predesign_first_approval_persists_without_erc_and_survives_restart(tmp_path) -> None:
    catalog, project_id, detail = _approved_catalog(tmp_path)
    candidate = _service(catalog).preview(
        project_id=project_id, tenant_id=TENANT, requester=OWNER, command=_command(detail),
    )
    assert candidate.source_snapshot.hardware_selection_approval_id
    assert catalog.current_circuit_intents(project_id, tenant_id=TENANT, requester=OWNER) == ()
    decision = PredesignDecisionCommand(
        purpose="PREDESIGN_APPROVAL", proposal_id=candidate.proposal.proposal_id,
        proposal_fingerprint=candidate.proposal.fingerprint,
        source_fingerprint=candidate.proposal.context_fingerprint,
        decision="APPROVE",
    )
    published = _service(catalog).decide(
        project_id=project_id, tenant_id=TENANT, requester=OWNER, command=decision,
    )
    reopened = _runtime(tmp_path)
    assert reopened.current_circuit_intents(project_id, tenant_id=TENANT, requester=OWNER) == (published.circuit,)
    assert reopened.circuit_source_current(
        project_id, published.circuit.object_id, "r1", tenant_id=TENANT, requester=OWNER,
    )
    from embedded_copilot.engineering_copilot.schematic_planning import (
        SchematicPlanningBlocked,
    )

    # Predesign approval alone does not approve the separately governed P1 supply.
    with pytest.raises(SchematicPlanningBlocked, match="POWER_SOURCE_UNRESOLVED"):
        reopened.create_schematic_plan(
            project_id, detail.conversation_id, tenant_id=TENANT, requester=OWNER,
            circuit_intent_selector=(published.circuit.object_id, "r1", published.circuit.fingerprint),
        )
    assert reopened.conversation(project_id, detail.conversation_id,
        tenant_id=TENANT, requester=OWNER).schematic_planning is None
    assert _service(reopened).decide(
        project_id=project_id, tenant_id=TENANT, requester=OWNER, command=decision,
    ) == published
    with pytest.raises(ProjectCatalogForbidden):
        reopened.current_circuit_intents(project_id, tenant_id=TENANT, requester="other-user")

    unrelated = ConversationTurn(
        turn_id="unrelated", request_id="unrelated-request", route="OTHER",
        status=ConversationTurnStatus.COMPLETED, question="Unrelated note", answer_text="Okay",
        created_at=NOW, completed_at=NOW,
    )
    current_detail = reopened.conversation(
        project_id, detail.conversation_id, tenant_id=TENANT, requester=OWNER,
    )
    updated = current_detail.model_copy(update={
        "turns": (*current_detail.turns, unrelated), "fingerprint": "sha256:" + "0" * 64,
    })
    reopened.persist_conversation_detail(ConversationDetail.model_validate(updated.model_dump(mode="python")))
    assert reopened.circuit_source_current(
        project_id, published.circuit.object_id, "r1", tenant_id=TENANT, requester=OWNER,
    )
    changed_turn = detail.turns[0].model_copy(update={"question": "Changed design request", "fingerprint": "sha256:" + "0" * 64})
    changed = updated.model_copy(update={"turns": (changed_turn, unrelated), "fingerprint": "sha256:" + "0" * 64})
    reopened.persist_conversation_detail(ConversationDetail.model_validate(changed.model_dump(mode="python")))
    assert not reopened.circuit_source_current(
        project_id, published.circuit.object_id, "r1", tenant_id=TENANT, requester=OWNER,
    )
    with pytest.raises(ProjectCatalogConflict, match="stale"):
        reopened.create_schematic_plan(
            project_id, detail.conversation_id, tenant_id=TENANT, requester=OWNER,
            circuit_intent_selector=(published.circuit.object_id, "r1", published.circuit.fingerprint),
        )


def test_predesign_rejects_wrong_expected_source_and_stale_review(tmp_path) -> None:
    catalog, project_id, detail = _approved_catalog(tmp_path)
    service = _service(catalog)
    command = _command(detail)
    bad_expectation = command.source_expectations.model_copy(update={
        "hardware_truth_fingerprint": "sha256:" + "f" * 64,
    })
    with pytest.raises(ProjectCatalogConflict, match="expectation"):
        service.preview(
            project_id=project_id, tenant_id=TENANT, requester=OWNER,
            command=command.model_copy(update={"source_expectations": bad_expectation}),
        )
    candidate = service.preview(
        project_id=project_id, tenant_id=TENANT, requester=OWNER, command=command,
    )
    decision = PredesignDecisionCommand(
        purpose="PREDESIGN_APPROVAL", proposal_id=candidate.proposal.proposal_id,
        proposal_fingerprint=candidate.proposal.fingerprint,
        source_fingerprint=candidate.proposal.context_fingerprint, decision="APPROVE",
    )
    changed_turn = detail.turns[0].model_copy(update={
        "question": "Different design request", "fingerprint": "sha256:" + "0" * 64,
    })
    changed = detail.model_copy(update={
        "turns": (changed_turn,), "fingerprint": "sha256:" + "0" * 64,
    })
    catalog.persist_conversation_detail(ConversationDetail.model_validate(changed.model_dump(mode="python")))
    with pytest.raises(ProjectCatalogConflict, match="source changed"):
        service.decide(
            project_id=project_id, tenant_id=TENANT, requester=OWNER, command=decision,
        )
    assert catalog.current_circuit_intents(project_id, tenant_id=TENANT, requester=OWNER) == ()


def test_predesign_decision_rejects_lost_approval_after_preview(tmp_path) -> None:
    catalog, project_id, detail = _approved_catalog(tmp_path)
    service = _service(catalog)
    candidate = service.preview(
        project_id=project_id, tenant_id=TENANT, requester=OWNER, command=_command(detail),
    )
    withdrawn = ConversationDetail.model_validate(detail.model_copy(update={
        "engineering_plan": None, "engineering_plan_review": None,
        "architecture_design": None, "architecture_review": None,
        "engineering_workflow": None, "hardware_selection": None,
        "design_truth": None, "fingerprint": "sha256:" + "0" * 64,
    }).model_dump(mode="python"))
    catalog.persist_conversation_detail(withdrawn)
    with pytest.raises(ProjectCatalogRejected, match="approved design sources"):
        service.decide(
            project_id=project_id, tenant_id=TENANT, requester=OWNER,
            command=PredesignDecisionCommand(
                purpose="PREDESIGN_APPROVAL", proposal_id=candidate.proposal.proposal_id,
                proposal_fingerprint=candidate.proposal.fingerprint,
                source_fingerprint=candidate.proposal.context_fingerprint,
                decision="APPROVE",
            ),
        )
    assert catalog.current_circuit_intents(project_id, tenant_id=TENANT, requester=OWNER) == ()
    assert catalog.predesign_proposal(
        project_id, candidate.proposal.proposal_id, tenant_id=TENANT, requester=OWNER,
    ).decision is None


def test_predesign_catalog_rejects_foreign_project_design_truth(tmp_path) -> None:
    catalog, project_id, detail = _approved_catalog(tmp_path)
    assert detail.design_truth is not None
    foreign_truth = detail.design_truth.model_copy(update={
        "project_id": "project-other", "fingerprint": "sha256:" + "0" * 64,
    })
    with pytest.raises(ValueError, match="design evidence identity mismatch"):
        catalog.persist_conversation_detail(ConversationDetail.model_validate(
            detail.model_copy(update={
                "design_truth": foreign_truth, "fingerprint": "sha256:" + "0" * 64,
            }).model_dump(mode="python"),
        ))
    assert catalog.current_circuit_intents(project_id, tenant_id=TENANT, requester=OWNER) == ()


@pytest.mark.parametrize("category", [
    "requirement_or_constraint", "architecture", "architecture_review",
    "hardware_approval", "hardware_bom", "hardware_truth",
])
@pytest.mark.parametrize("stage", ["preview", "decision"])
def test_predesign_service_rejects_foreign_owner_source_from_controlled_port(
    tmp_path, monkeypatch, category, stage,
) -> None:
    catalog, project_id, detail = _approved_catalog(tmp_path)
    foreign_project_id = _other_project(catalog)
    assert foreign_project_id != project_id
    service = _service(catalog)
    command = _command(detail)
    candidate = None
    if stage == "decision":
        candidate = service.preview(
            project_id=project_id, tenant_id=TENANT, requester=OWNER, command=command,
        )
    corrupted = _foreign_source_detail(detail, category, foreign_project_id)

    def foreign_conversation(
        requested_project_id, requested_conversation_id, *, tenant_id, requester,
    ):
        assert (requested_project_id, requested_conversation_id, tenant_id, requester) == (
            project_id, detail.conversation_id, TENANT, OWNER,
        )
        # This fake Port deliberately bypasses ConversationDetail validation.
        return corrupted

    with monkeypatch.context() as patch:
        patch.setattr(catalog, "conversation", foreign_conversation)
        with pytest.raises(ProjectCatalogRejected, match="approved design sources"):
            if stage == "preview":
                service.preview(
                    project_id=project_id, tenant_id=TENANT, requester=OWNER,
                    command=command,
                )
            else:
                service.decide(
                    project_id=project_id, tenant_id=TENANT, requester=OWNER,
                    command=PredesignDecisionCommand(
                        purpose="PREDESIGN_APPROVAL",
                        proposal_id=candidate.proposal.proposal_id,
                        proposal_fingerprint=candidate.proposal.fingerprint,
                        source_fingerprint=candidate.proposal.context_fingerprint,
                        decision="APPROVE",
                    ),
                )
    assert catalog.current_circuit_intents(project_id, tenant_id=TENANT, requester=OWNER) == ()
    assert catalog.current_circuit_intents(foreign_project_id, tenant_id=TENANT, requester=OWNER) == ()
    if candidate is not None:
        assert catalog.predesign_proposal(
            project_id, candidate.proposal.proposal_id, tenant_id=TENANT, requester=OWNER,
        ).decision is None


def test_predesign_rejects_empty_or_prematurely_verified_claims(tmp_path) -> None:
    catalog, project_id, detail = _approved_catalog(tmp_path)
    service = _service(catalog)
    command = _command(detail)
    empty_spec = command.circuit_intent.model_copy(update={
        "blocks": (), "verification_requirements": (),
    })
    with pytest.raises(ProjectCatalogRejected, match="blocks and verification"):
        service.preview(
            project_id=project_id, tenant_id=TENANT, requester=OWNER,
            command=command.model_copy(update={"circuit_intent": empty_spec}),
        )
    claim = command.circuit_intent.verification_requirements[0]
    verified = VerificationRequirement.model_validate({
        **claim.model_dump(mode="python"), "status": "VERIFIED",
        "fingerprint": "sha256:" + "0" * 64,
    })
    false_spec = command.circuit_intent.model_copy(update={"verification_requirements": (verified,)})
    with pytest.raises(ProjectCatalogRejected, match="claim bindings"):
        service.preview(
            project_id=project_id, tenant_id=TENANT, requester=OWNER,
            command=command.model_copy(update={"circuit_intent": false_spec}),
        )
    produced = VerificationRequirement.model_validate({
        **claim.model_dump(mode="python"), "produced_evidence_refs": ("claimed-receipt",),
        "fingerprint": "sha256:" + "0" * 64,
    })
    with pytest.raises(ProjectCatalogRejected, match="claim bindings"):
        service.preview(
            project_id=project_id, tenant_id=TENANT, requester=OWNER,
            command=command.model_copy(update={"circuit_intent": command.circuit_intent.model_copy(
                update={"verification_requirements": (produced,)},
            )}),
        )
    fake_currentness = VerificationRequirement.model_validate({
        **claim.model_dump(mode="python"), "currentness": "VERIFIED",
        "fingerprint": "sha256:" + "0" * 64,
    })
    with pytest.raises(ProjectCatalogRejected, match="claim bindings"):
        service.preview(
            project_id=project_id, tenant_id=TENANT, requester=OWNER,
            command=command.model_copy(update={"circuit_intent": command.circuit_intent.model_copy(
                update={"verification_requirements": (fake_currentness,)},
            )}),
        )
    executed = CalculationRecord(
        calculation_id="calculation-1", owner_ref="block-power", equation="Vout = Vin",
        status="EXECUTED", evidence_refs=("client-claimed",),
    )
    with pytest.raises(ProjectCatalogRejected, match="claim bindings"):
        service.preview(
            project_id=project_id, tenant_id=TENANT, requester=OWNER,
            command=command.model_copy(update={"circuit_intent": command.circuit_intent.model_copy(
                update={"calculations": (executed,)},
            )}),
        )
