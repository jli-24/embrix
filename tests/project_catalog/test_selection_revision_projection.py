from __future__ import annotations

import sqlite3

import pytest

from embedded_copilot.engineering_copilot import (
    DesignTruthBuilder,
    HardwareSelectionState,
    HardwareSelectionStatus,
    generate_knowledge_model,
    generate_teaching_context,
)
from embedded_copilot.engineering_copilot.common import ZERO
from embedded_copilot.project_catalog.models import ConversationDetail
from embedded_copilot.project_catalog.store import SQLiteProjectCatalogRepository
from tests.project_catalog.test_source_replan_contract import _approved_v3_legacy_plan


def _detail(tmp_path, monkeypatch):
    catalog, detail, *_ = _approved_v3_legacy_plan(tmp_path, monkeypatch)
    return catalog, catalog.conversation(
        detail.project_id,
        detail.conversation_id,
        tenant_id=detail.tenant_id,
        requester=detail.requester,
    )


def _replacement(detail):
    proposal = type(detail.hardware_selection.proposal).create(
        **{
            **detail.hardware_selection.proposal.model_dump(mode="python"),
            "proposal_id": "new-selection-proposal",
            "fingerprint": ZERO,
        }
    )
    return HardwareSelectionState(
        proposal=proposal, status=HardwareSelectionStatus.REVIEW
    )


@pytest.mark.parametrize("replacement", [True, False], ids=["replace", "revoke"])
def test_old_selection_truth_and_plan_withdraw_as_one_valid_projection(
    tmp_path, monkeypatch, replacement
):
    from embedded_copilot.project_catalog._selection_revision_projection import (
        selection_revision_lost_bindings,
        withdraw_selection_revision_projection,
    )

    catalog, detail = _detail(tmp_path, monkeypatch)
    knowledge = generate_knowledge_model(detail.design_truth)
    teaching = generate_teaching_context(knowledge)
    detail = ConversationDetail.model_validate(
        {
            **detail.model_dump(mode="python"),
            "engineering_knowledge_model": knowledge,
            "teaching_context": teaching,
            "fingerprint": ZERO,
        }
    )
    old_raw = detail.model_dump_json()
    lost = selection_revision_lost_bindings(detail)
    assert {
        "design_truth",
        "engineering_knowledge_model",
        "teaching_context",
        "schematic_planning",
    } <= set(lost)
    state = _replacement(detail) if replacement else None
    updated = withdraw_selection_revision_projection(detail, hardware_selection=state)
    assert updated.hardware_selection == state
    assert updated.design_truth is None
    assert (
        updated.engineering_knowledge_model is None and updated.teaching_context is None
    )
    assert updated.schematic_planning is None and updated.schematic_production is None
    assert updated.architecture_design == detail.architecture_design
    assert updated.architecture_review == detail.architecture_review
    assert updated.engineering_plan == detail.engineering_plan
    assert updated.engineering_plan_review == detail.engineering_plan_review
    assert updated.engineering_workflow == detail.engineering_workflow
    assert updated.turns == detail.turns
    assert detail.model_dump_json() == old_raw
    assert detail.design_truth.status.value == "APPROVED"
    catalog.persist_conversation_detail(updated)
    reopened = SQLiteProjectCatalogRepository(catalog._repository._database)
    # Decode the exact persisted aggregate independently of the runtime cache.
    with sqlite3.connect(reopened._database) as db:
        saved = db.execute(
            "SELECT body FROM conversations WHERE conversation_id=?",
            (detail.conversation_id,),
        ).fetchone()[0]
    restored = ConversationDetail.model_validate_json(saved)
    assert restored == updated and restored.design_truth is None


def test_architecture_only_truth_knowledge_and_workflow_are_preserved(
    tmp_path, monkeypatch
):
    from embedded_copilot.project_catalog._selection_revision_projection import (
        selection_revision_lost_bindings,
        withdraw_selection_revision_projection,
    )

    _, detail = _detail(tmp_path, monkeypatch)
    truth = DesignTruthBuilder().build_from_approved_architecture(
        detail.architecture_design,
        tenant_id=detail.tenant_id,
        conversation_id=detail.conversation_id,
        architecture_review=detail.architecture_review,
    )
    knowledge = generate_knowledge_model(truth)
    teaching = generate_teaching_context(knowledge)
    detail = ConversationDetail.model_validate(
        {
            **detail.model_dump(mode="python"),
            "schematic_planning": None,
            "design_truth": truth,
            "engineering_knowledge_model": knowledge,
            "teaching_context": teaching,
            "fingerprint": ZERO,
        }
    )
    lost = selection_revision_lost_bindings(detail)
    assert "design_truth" not in lost and "engineering_knowledge_model" not in lost
    updated = withdraw_selection_revision_projection(detail, hardware_selection=None)
    assert updated.design_truth == truth
    assert updated.engineering_knowledge_model == knowledge
    assert updated.teaching_context == teaching
    assert updated.engineering_workflow == detail.engineering_workflow
    assert updated.architecture_design == detail.architecture_design


def _bound_stack(detail, *, selection_bound_hardware=False):
    from embedded_copilot.engineering_copilot import (
        evaluate_authorization,
        evaluate_capabilities,
        generate_execution_intent,
        generate_execution_requests,
        generate_firmware_spec,
        generate_hardware_design_spec,
        generate_implementation_spec,
        generate_simulation_spec,
        generate_verification_spec,
    )
    from embedded_copilot.firmware_engineering import FirmwareBuildConfiguration
    from embedded_copilot.firmware_engineering.models import canonical_fingerprint

    config = FirmwareBuildConfiguration(
        target_kind="STM32F103C8T6",
        build_system="CMAKE",
        toolchain_id="ARM_NONE_EABI_GCC",
        toolchain_version="13",
        profile="DEBUG",
    )
    hardware = generate_hardware_design_spec(
        detail.architecture_design, detail.engineering_workflow
    )
    if selection_bound_hardware:
        from embedded_copilot.engineering_copilot.contracts import PlanProvenance

        selection = detail.hardware_selection.proposal
        extra = PlanProvenance.create(
            project_id=detail.project_id,
            source_type="HARDWARE_SELECTION",
            source_id=selection.proposal_id,
            source_fingerprint=selection.fingerprint,
        )
        hardware = type(hardware).create(
            **{
                **{
                    name: getattr(hardware, name)
                    for name in type(hardware).model_fields
                    if name != "fingerprint"
                },
                "provenance": hardware.provenance + (extra,),
            }
        )
    firmware = generate_firmware_spec(hardware, detail.engineering_workflow)
    firmware = type(firmware).create(
        **{
            **firmware.model_dump(mode="python", exclude={"fingerprint"}),
            "target_platform": config.target_kind,
            "mcu_family": "STM32F1",
            "architecture": "ARM_CORTEX_M3",
            "firmware_framework": "CMSIS",
            "mcu_exact_part": config.target_kind,
            "build_system": config.build_system,
            "toolchain_family": config.toolchain_id,
            "design_truth_id": detail.design_truth.truth_id,
            "design_truth_fingerprint": detail.design_truth.fingerprint,
        }
    )
    simulation = generate_simulation_spec(
        hardware, firmware, detail.engineering_workflow
    )
    verification = generate_verification_spec(
        hardware, firmware, simulation, detail.engineering_workflow
    )
    implementation = generate_implementation_spec(
        hardware, firmware, simulation, verification, detail.engineering_workflow
    )
    implementation = type(implementation).create(
        **{
            **implementation.model_dump(mode="python", exclude={"fingerprint"}),
            "design_truth_id": detail.design_truth.truth_id,
            "design_truth_fingerprint": detail.design_truth.fingerprint,
            "build_configuration_id": "selection-projection-build-config",
            "build_configuration_fingerprint": canonical_fingerprint(config),
        }
    )
    capabilities = evaluate_capabilities(implementation)
    intents = tuple(generate_execution_intent(task) for task in implementation.tasks)
    requests = generate_execution_requests(
        capabilities,
        intents,
        conversation_id=detail.conversation_id,
        requested_by=detail.requester,
    )
    authorization = evaluate_authorization(capabilities, intents, requests)
    knowledge = generate_knowledge_model(detail.design_truth)
    return ConversationDetail.model_validate(
        {
            **detail.model_dump(mode="python"),
            "hardware_design_spec": hardware,
            "firmware_spec": firmware,
            "simulation_spec": simulation,
            "verification_spec": verification,
            "implementation_spec": implementation,
            "build_configuration": config,
            "capability_evaluation": capabilities,
            "execution_intents": intents,
            "execution_authorization": authorization,
            "engineering_knowledge_model": knowledge,
            "teaching_context": generate_teaching_context(knowledge),
            "fingerprint": ZERO,
        }
    )


@pytest.mark.parametrize("replacement", [True, False], ids=["replace", "revoke"])
def test_truth_bound_build_and_execution_chain_withdraws_without_rewriting_history(
    tmp_path, monkeypatch, replacement
):
    from embedded_copilot.project_catalog._selection_revision_projection import (
        withdraw_selection_revision_projection,
    )

    catalog, detail = _detail(tmp_path, monkeypatch)
    detail = _bound_stack(detail)
    original = detail.model_dump_json()
    updated = withdraw_selection_revision_projection(
        detail, hardware_selection=_replacement(detail) if replacement else None
    )
    for field in (
        "design_truth",
        "engineering_knowledge_model",
        "teaching_context",
        "build_configuration",
        "firmware_spec",
        "simulation_spec",
        "verification_spec",
        "implementation_spec",
        "capability_evaluation",
        "execution_authorization",
        "firmware_source_generation",
        "firmware_production",
        "schematic_production",
    ):
        assert getattr(updated, field) is None, field
    assert updated.execution_intents == ()
    assert updated.hardware_design_spec == detail.hardware_design_spec
    assert updated.engineering_workflow == detail.engineering_workflow
    assert detail.model_dump_json() == original
    catalog.persist_conversation_detail(updated)
    with sqlite3.connect(catalog._repository._database) as db:
        saved = db.execute(
            "SELECT body FROM conversations WHERE conversation_id=?",
            (detail.conversation_id,),
        ).fetchone()[0]
    assert ConversationDetail.model_validate_json(saved) == updated


def test_helper_rejects_untyped_input_without_mutating_original(tmp_path, monkeypatch):
    from embedded_copilot.project_catalog._selection_revision_projection import (
        withdraw_selection_revision_projection,
    )

    _, detail = _detail(tmp_path, monkeypatch)
    raw = detail.model_dump_json()
    with pytest.raises(TypeError, match="HardwareSelectionState"):
        withdraw_selection_revision_projection(detail, hardware_selection={})
    with pytest.raises(TypeError, match="ConversationDetail"):
        withdraw_selection_revision_projection({}, hardware_selection=None)
    assert detail.model_dump_json() == raw
