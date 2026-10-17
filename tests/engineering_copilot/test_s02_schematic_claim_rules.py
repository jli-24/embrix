from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache

import pytest

from embedded_copilot.engineering_copilot.contracts import (
    SchematicErcEngineeringStatus,
    SchematicErcIssue,
    SchematicErcIssueSeverity,
    SchematicErcReceipt,
    SchematicErcResult,
    SchematicErcStatus,
    SchematicErcViolation,
    SchematicGenerationReceipt,
    SchematicGenerationStatus,
    SchematicParseValidationEvidence,
)
from embedded_copilot.engineering_copilot.schematic_claim_rules import (
    resolve_schematic_claim,
)
from embedded_copilot.engineering_copilot.schematic_planning import (
    SchematicPlanDecision,
    SchematicPlanningService,
)
from embedded_copilot.engineering_runtime.objects.models import (
    CircuitIntentObject,
    LifecycleStatus,
    Provenance,
    VerificationRequirement,
)
from tests.engineering_copilot.test_schematic_planning_g2 import _approved_inputs

FP = "sha256:" + "a" * 64
OTHER_FP = "sha256:" + "b" * 64
NOW = datetime(2026, 9, 30, tzinfo=UTC)


def _claim(kind: str = "SCHEMATIC_PARSE", *, claim: str | None = None, method: str | None = None,
           refs: tuple[str, ...] = ()) -> VerificationRequirement:
    is_parse = kind == "SCHEMATIC_PARSE"
    return VerificationRequirement(
        verification_id="claim-parse" if is_parse else "claim-erc", owner_ref="block-1",
        claim=claim or ("CURRENT_SCHEMATIC_ARTIFACT_PARSE" if is_parse else "CURRENT_SCHEMATIC_ARTIFACT_ERC_PASS"),
        verification_type=kind,
        method=method or ("CATALOG_PARSE_RECEIPT" if is_parse else "CANONICAL_ERC_RECEIPT"),
        required_evidence_refs=refs,
        produced_evidence_refs=refs,
        status="PLANNED",
    )


@lru_cache(maxsize=1)
def _valid_facts() -> dict[str, object]:
    context, architecture, _review, _workflow, selection, base = _approved_inputs()
    assert selection.hardware_truth is not None and selection.bom is not None
    intent = CircuitIntentObject.create(
        object_id="circuit-1", project_id=context.project_id, revision="r1",
        status=LifecycleStatus.APPROVED, function="Offline schematic evidence scope",
        topology=("Current approved plan and artifact",),
        verification_requirements=(_claim(), _claim("SCHEMATIC_ERC")),
        provenance=Provenance.create(
            source="OFFLINE_TEST", source_ref=f"{context.project_id}:circuit-1",
            created_by="tester", created_at=NOW,
        ),
    )
    service = SchematicPlanningService()
    proposal = service.create_proposal(
        architecture, context, selection.hardware_truth, selection.bom,
        design_truth=base, conversation_id=context.conversation_id,
        circuit_intent=intent,
    )
    approved = service.decide(
        proposal, SchematicPlanDecision.APPROVED, reviewer="tester", decided_at=NOW,
    )
    plan = approved.approved_plan
    assert plan is not None
    parse = SchematicParseValidationEvidence.create(
        validation_id="parse-1", project_id="project-1", conversation_id="conversation-1",
        operation_id="write-1", artifact_path="hardware/main.kicad_sch",
        artifact_fingerprint=FP, validation_status="PARSE_VERIFIED",
        artifact_unchanged=True, exit_code=0, kicad_cli_path="offline-fixture",
        kicad_version="fixture-version", command_identity="offline-parse-fixture",
        validated_at=NOW,
    )
    generation = SchematicGenerationReceipt.create(
        receipt_id="generation-1", project_id="project-1", conversation_id="conversation-1",
        operation_id="write-1", operation_fingerprint=OTHER_FP,
        operation_receipt_fingerprint=OTHER_FP,
        workspace_apply_receipt_fingerprint=OTHER_FP,
        schematic_plan_id=plan.plan_id,
        schematic_plan_fingerprint=plan.fingerprint, artifact_path="hardware/main.kicad_sch",
        design_truth_fingerprint=base.fingerprint, artifact_fingerprint=FP,
        generator="offline-fixture", generator_version="1", source_refs=("write-1",),
        created_at=NOW,
        status=SchematicGenerationStatus.SUCCESS, validation_status="PARSE_VERIFIED",
        parse_validation=parse,
    )
    erc_result = SchematicErcResult.create(
        operation_id="erc-1", schematic_artifact_ref="hardware/main.kicad_sch",
        schematic_content_fingerprint=FP, status=SchematicErcStatus.ERC_PASS,
        kicad_version="fixture-version", exit_status=0,
        started_at=NOW, finished_at=NOW, receipt_ref=OTHER_FP,
    )
    erc = SchematicErcReceipt.create(
        receipt_id="erc-receipt-1", project_id="project-1", conversation_id="conversation-1",
        operation_id="erc-1", artifact_path="hardware/main.kicad_sch",
        operation_receipt_fingerprint=OTHER_FP,
        artifact_fingerprint=FP, schematic_plan_fingerprint=plan.fingerprint,
        design_truth_fingerprint=base.fingerprint,
        kicad_version=erc_result.kicad_version, command_identity=erc_result.command_identity,
        created_at=NOW,
        result=erc_result, tool_execution_status="SUCCEEDED",
        engineering_status=SchematicErcEngineeringStatus.PASS,
    )
    return {
        "intent": intent,
        "plan": plan,
        "generation": generation,
        "parse": parse,
        "erc": erc,
        "project_id": "project-1",
        "conversation_id": "conversation-1",
        "artifact_fingerprint": FP,
        "source_current": True,
    }


def _facts() -> dict[str, object]:
    return _valid_facts().copy()


@pytest.mark.parametrize("kind,scope,receipt_id", [
    ("SCHEMATIC_PARSE", "SCHEMATIC_PARSE", "parse-1"),
    ("SCHEMATIC_ERC", "SCHEMATIC_ERC", "erc-receipt-1"),
])
def test_exact_claim_uses_only_matching_current_receipt(kind, scope, receipt_id) -> None:
    result = resolve_schematic_claim(_claim(kind), **_facts())
    assert result.status == "PASS"
    assert result.proof is not None
    assert result.proof.proof_scope == scope
    assert result.proof.source_receipt_id == receipt_id
    facts = _facts()
    expected_receipt = facts["parse"] if kind == "SCHEMATIC_PARSE" else facts["erc"]
    assert result.proof.source_receipt_fingerprint == expected_receipt.fingerprint


@pytest.mark.parametrize("kind,claim,method", [
    ("SCHEMATIC_PARSE", "Power rail passes", "CATALOG_PARSE_RECEIPT"),
    ("SCHEMATIC_PARSE", "CURRENT_SCHEMATIC_ARTIFACT_PARSE", "User observation"),
    ("SCHEMATIC_ERC", "CAN mode verified", "CANONICAL_ERC_RECEIPT"),
    ("SCHEMATIC_ERC", "Connections verified", "CANONICAL_ERC_RECEIPT"),
    ("SCHEMATIC_ERC", "CURRENT_SCHEMATIC_ARTIFACT_ERC_PASS", "CATALOG_PARSE_RECEIPT"),
    ("CALCULATION", "CURRENT_SCHEMATIC_ARTIFACT_PARSE", "CATALOG_PARSE_RECEIPT"),
])
def test_client_claim_or_legacy_refs_cannot_borrow_receipt(kind, claim, method) -> None:
    result = resolve_schematic_claim(
        _claim(kind, claim=claim, method=method, refs=("erc-receipt-1",)), **_facts(),
    )
    assert (result.status, result.reason, result.proof) == (
        "BLOCKED", "NO_TRUSTED_RESOLVER", None,
    )


def test_canonical_text_cannot_impersonate_unapproved_claim_identity() -> None:
    unapproved = _claim().model_copy(update={"verification_id": "claim-unapproved"})
    result = resolve_schematic_claim(unapproved, **_facts())
    assert (result.status, result.reason, result.proof) == (
        "BLOCKED", "EVIDENCE_MISMATCH", None,
    )


@pytest.mark.parametrize("kind,mutated,reason", [
    ("SCHEMATIC_PARSE", {"parse": None}, "EVIDENCE_UNAVAILABLE"),
    ("SCHEMATIC_ERC", {"erc": None}, "EVIDENCE_UNAVAILABLE"),
    ("SCHEMATIC_PARSE", {"source_current": False}, "SOURCE_STALE"),
    ("SCHEMATIC_ERC", {"source_current": False}, "SOURCE_STALE"),
    ("SCHEMATIC_PARSE", {"project_id": "project-2"}, "EVIDENCE_MISMATCH"),
    ("SCHEMATIC_PARSE", {"conversation_id": "conversation-2"}, "EVIDENCE_MISMATCH"),
    ("SCHEMATIC_PARSE", {"artifact_fingerprint": OTHER_FP}, "EVIDENCE_MISMATCH"),
])
def test_missing_stale_or_wrong_scope_fails_closed(kind, mutated, reason) -> None:
    facts = _facts()
    facts.update(mutated)
    result = resolve_schematic_claim(_claim(kind), **facts)
    assert (result.status, result.reason, result.proof) == ("BLOCKED", reason, None)


@pytest.mark.parametrize("target,field,value", [
    ("intent", "project_id", "project-2"),
    ("intent", "revision", "r2"),
    ("plan", "project_id", "project-2"),
    ("plan", "conversation_id", "conversation-2"),
    ("plan", "circuit_intent_id", "other-circuit"),
    ("plan", "circuit_intent_revision", "r2"),
    ("plan", "circuit_intent_fingerprint", OTHER_FP),
    ("generation", "project_id", "project-2"),
    ("generation", "schematic_plan_fingerprint", OTHER_FP),
    ("generation", "artifact_fingerprint", OTHER_FP),
    ("generation", "status", SchematicGenerationStatus.STALE),
    ("parse", "project_id", "project-2"),
    ("parse", "conversation_id", "conversation-2"),
    ("parse", "operation_id", "other-write"),
    ("parse", "artifact_path", "hardware/other.kicad_sch"),
    ("parse", "artifact_fingerprint", OTHER_FP),
    ("parse", "artifact_unchanged", False),
    ("parse", "validation_status", "FAILED"),
])
def test_parse_proof_rechecks_owner_subject_and_artifact(target, field, value) -> None:
    facts = _facts()
    facts[target] = facts[target].model_copy(update={field: value})
    result = resolve_schematic_claim(_claim(), **facts)
    assert (result.status, result.reason, result.proof) == ("BLOCKED", "EVIDENCE_MISMATCH", None)


@pytest.mark.parametrize("target,field,value", [
    ("erc", "project_id", "project-2"),
    ("erc", "conversation_id", "conversation-2"),
    ("erc", "schematic_plan_fingerprint", OTHER_FP),
    ("erc", "artifact_path", "hardware/other.kicad_sch"),
    ("erc", "artifact_fingerprint", OTHER_FP),
    ("erc", "tool_execution_status", "FAILED"),
    ("erc", "engineering_status", SchematicErcEngineeringStatus.BLOCKED),
    ("erc_result", "status", SchematicErcStatus.ERC_FAIL),
    ("erc_result", "operation_id", "other-erc"),
    ("erc_result", "schematic_content_fingerprint", OTHER_FP),
])
def test_erc_proof_rechecks_canonical_pass_and_artifact(target, field, value) -> None:
    facts = _facts()
    if target == "erc_result":
        changed_result = facts["erc"].result.model_copy(update={field: value})
        facts["erc"] = facts["erc"].model_copy(update={"result": changed_result})
    else:
        facts[target] = facts[target].model_copy(update={field: value})
    result = resolve_schematic_claim(_claim("SCHEMATIC_ERC"), **facts)
    assert (result.status, result.reason, result.proof) == ("BLOCKED", "EVIDENCE_MISMATCH", None)


@pytest.mark.parametrize("field,value", [
    ("exit_status", 1),
    ("violations", (SchematicErcViolation.create(
        severity="ERROR", rule="offline-rule", message="offline violation",
        raw_source_ref="hardware/erc-report.txt",
    ),)),
    ("issues", (SchematicErcIssue.create(
        issue_id="issue-1", severity=SchematicErcIssueSeverity.ERROR,
        rule_id="offline-rule", message="offline error",
        raw_evidence_ref="hardware/erc-report.txt", artifact_fingerprint=FP,
    ),)),
    ("engineering_status", SchematicErcEngineeringStatus.BLOCKED),
])
def test_erc_pass_label_cannot_hide_failed_result_diagnostics(field, value) -> None:
    facts = _facts()
    changed_result = facts["erc"].result.model_copy(update={field: value})
    facts["erc"] = facts["erc"].model_copy(update={"result": changed_result})
    result = resolve_schematic_claim(_claim("SCHEMATIC_ERC"), **facts)
    assert (result.status, result.reason, result.proof) == (
        "BLOCKED", "EVIDENCE_MISMATCH", None,
    )


@pytest.mark.parametrize("target,field,value,kind", [
    ("intent", "function", "forged function", "SCHEMATIC_PARSE"),
    ("plan", "source_refs", ("forged-source",), "SCHEMATIC_PARSE"),
    ("generation", "generator", "forged-generator", "SCHEMATIC_PARSE"),
    ("parse", "kicad_version", "forged-version", "SCHEMATIC_PARSE"),
    ("erc", "created_at", NOW.replace(day=29), "SCHEMATIC_ERC"),
])
def test_model_construct_cannot_bypass_owner_fingerprint_validation(
    target, field, value, kind,
) -> None:
    facts = _facts()
    original = facts[target]
    facts[target] = type(original).model_construct(**{**original.__dict__, field: value})
    result = resolve_schematic_claim(_claim(kind), **facts)
    assert (result.status, result.reason, result.proof) == (
        "BLOCKED", "EVIDENCE_MISMATCH", None,
    )
