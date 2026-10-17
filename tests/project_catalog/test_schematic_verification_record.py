from __future__ import annotations

import hashlib
from datetime import timedelta
from types import SimpleNamespace

import pytest

from embedded_copilot.application.circuit_publication import PredesignDecisionCommand
from embedded_copilot.engineering_copilot import (
    SchematicErcGateStatus,
    SchematicErcReceipt,
    SchematicErcResult,
    SchematicErcStatus,
    SchematicGenerationReceipt,
    SchematicGenerationStatus,
    SchematicParseValidationEvidence,
    SchematicProductionState,
    SchematicProductionStatus,
    SchematicProposalDecisionStatus,
    decide_schematic_proposal,
)
from embedded_copilot.engineering_runtime import (
    CalculationRecord,
    CircuitBlock,
    VerificationRequirement,
)
from embedded_copilot.execution_worker import (
    OperationReceipt,
    OperationStatus,
    OperationType,
    SchematicErcOperation,
)
from embedded_copilot.project_catalog import (
    ProjectCatalogConflict,
    ProjectCatalogForbidden,
    ProjectCatalogRejected,
)
from embedded_copilot.project_catalog.models import (
    ConversationDetail,
    ConversationTurn,
    ConversationTurnStatus,
    SchematicVerificationRecord,
)
from embedded_copilot.project_catalog.runtime import ProjectCatalogRuntime
from tests.engineering_copilot.test_schematic_production_g3 import (
    _runtime_with_approved_plan,
)
from tests.project_catalog.test_circuit_catalog_persistence import NOW
from tests.project_catalog.test_predesign_publication import _command, _service

FP = "sha256:" + "a" * 64


def _port(
    *, erc_status: str = "ERC_PASS", plan_fingerprint: str | None = FP,
    erc_plan_fingerprint: str | None = FP,
    claims: tuple[VerificationRequirement, ...] | None = None,
):
    plan = SimpleNamespace(
        plan_id="plan-1", project_id="project-1", conversation_id="conversation-1",
        fingerprint=FP, status=SimpleNamespace(value="APPROVED"),
        circuit_intent_id="circuit-1", circuit_intent_revision="r1",
        circuit_intent_fingerprint=FP,
    )
    generation = SimpleNamespace(
        project_id="project-1", conversation_id="conversation-1",
        operation_id="write-1", schematic_plan_id="plan-1",
        schematic_plan_fingerprint=plan_fingerprint,
        artifact_path="hardware/main.kicad_sch", artifact_fingerprint=FP,
        fingerprint=FP, status=SimpleNamespace(value="SUCCESS"),
        validation_status="PARSE_VERIFIED",
        parse_validation=SimpleNamespace(
            project_id="project-1", conversation_id="conversation-1",
            operation_id="write-1", artifact_path="hardware/main.kicad_sch",
            fingerprint=FP, artifact_fingerprint=FP, validation_status="PARSE_VERIFIED",
            artifact_unchanged=True, exit_code=0,
        ),
    )
    erc = SimpleNamespace(
        project_id="project-1", conversation_id="conversation-1", receipt_id="erc-receipt-1",
        tool_execution_status="SUCCEEDED",
        operation_id="erc-1", schematic_plan_fingerprint=erc_plan_fingerprint,
        artifact_fingerprint=FP, artifact_path="hardware/main.kicad_sch",
        fingerprint=FP, engineering_status=SimpleNamespace(value="PASS"),
        result=SimpleNamespace(operation_id="erc-1", status=SimpleNamespace(value=erc_status),
                               schematic_content_fingerprint=FP,
                               schematic_artifact_ref="hardware/main.kicad_sch"),
    )
    detail = SimpleNamespace(
        project_id="project-1", conversation_id="conversation-1", fingerprint=FP,
        schematic_planning=SimpleNamespace(approved_plan=plan),
        schematic_production=SimpleNamespace(
            project_id="project-1", conversation_id="conversation-1",
            status=SimpleNamespace(value="GENERATED"), operation_id="write-1",
            generation_receipt=generation, erc_receipt=erc,
        ),
    )
    intent = SimpleNamespace(
        project_id="project-1", object_id="circuit-1", revision="r1", fingerprint=FP,
        verification_requirements=claims if claims is not None else (
            VerificationRequirement(
                verification_id="claim-thermal", owner_ref="block-power",
                claim="Power thermal margin", verification_type="CALCULATION",
                method="Calculate thermal margin",
            ),
        ),
    )

    class Repository:
        record = None

        def save_schematic_verification(self, record, *, requester, expected_conversation_fingerprint,
                                        expected_source_publications=()):
            self.record = record
            return record

        def schematic_verification(self, project_id, record_id, *, tenant_id, requester):
            return self.record

    class Port(ProjectCatalogRuntime):
        _repository = Repository()

        def __init__(self):
            self.detail = detail
            self.intent = intent
            self.current_bytes = True
            self.source_current = True

        def _now(self):
            return NOW

        def _schematic_plan_source_currentness(self, detail, plan, **_scope):
            # Pure claim-rule unit seam. Formal Owner/P1V3 currentness is covered by real Runtime fixtures.
            return "CURRENT" if self.source_current else "STALE"

        @staticmethod
        def _approved_source_heads(_detail):
            # This synthetic rule fixture has no persisted P1 Owner; it is not temporal authority evidence.
            return ()

        def get_project_for_requester(self, project_id, *, tenant_id, requester):
            if requester != "owner":
                raise ValueError("owner required")

        def conversation(self, project_id, conversation_id, *, tenant_id, requester):
            return self.detail

        def circuit_intent_revision(self, project_id, object_id, revision, fingerprint, *, tenant_id, requester):
            return self.intent

        def circuit_source_current(self, project_id, object_id, revision, *, tenant_id, requester):
            return self.source_current

        def _current_schematic_bytes(self, project_id, artifact_path, artifact_fingerprint, *, tenant_id):
            return self.current_bytes

    return Port()


def _restart_with_published_source(catalog, tmp_path):
    from embedded_copilot.project_catalog.source_publication import PublishedComponentSourcePort

    restarted = ProjectCatalogRuntime(
        project_root=tmp_path / "projects", state_database=tmp_path / "desktop.sqlite3",
        initialize_async=False,
    )
    source = catalog._hardware_component_source
    restarted.set_hardware_component_source(PublishedComponentSourcePort(
        restarted._repository, tenant_id=source.tenant_id, publisher_id=source.publisher_id,
    ))
    return restarted


def test_verification_records_only_system_erc_and_blocks_user_claims() -> None:
    port = _port()
    record = ProjectCatalogRuntime.record_schematic_verification(
        port, "project-1", "conversation-1", tenant_id="tenant-1", requester="owner",
        intent_id="circuit-1", intent_revision="r1", intent_fingerprint=FP,
        plan_id="plan-1", plan_fingerprint=FP, artifact_fingerprint=FP,
        generation_operation_id="write-1", erc_operation_id="erc-1",
    )
    assert record.erc_status == "PASS"
    assert record.aggregate_status == "BLOCKED"
    assert record.claims[0].status == "BLOCKED"
    assert record.claims[0].evidence_ids == ()


def test_legacy_blocked_verification_record_keeps_canonical_fingerprint() -> None:
    record = _record_from_port(_port())
    assert record.verification_id == "schematic-verification-47bea6f641fa5a81e3a98ac2"
    assert record.fingerprint == "sha256:5edca79969c1118f3558a92e58794e51886bfed8e77627348ced1b7e26e5d6da"
    assert "proof" not in record.claims[0].model_dump(mode="json")
    assert SchematicVerificationRecord.model_validate_json(record.model_dump_json()) == record


def test_changed_blocked_reason_appends_record_and_repeated_source_is_idempotent(
    tmp_path, monkeypatch,
) -> None:
    import embedded_copilot.project_catalog.runtime as catalog_runtime
    from embedded_copilot.engineering_copilot.schematic_claim_rules import (
        ClaimRuleResult,
    )

    claim = _claim(
        "claim-erc", "SCHEMATIC_ERC", "CURRENT_SCHEMATIC_ARTIFACT_ERC_PASS",
        "CANONICAL_ERC_RECEIPT",
    )
    catalog, project_id, conversation_id, selector, plan, artifact_fp, _ = _s02_generated_catalog(
        tmp_path, claims=(claim,),
    )
    args = {
        "tenant_id": "tenant-1", "requester": "engineer-1",
        "intent_id": selector[0], "intent_revision": selector[1],
        "intent_fingerprint": selector[2], "plan_id": plan.plan_id,
        "plan_fingerprint": plan.fingerprint, "artifact_fingerprint": artifact_fp,
        "generation_operation_id": "s02-write", "erc_operation_id": None,
    }
    original = catalog.record_schematic_verification(project_id, conversation_id, **args)
    assert original.claims[0].status == "BLOCKED"
    assert original.claims[0].reason == "EVIDENCE_UNAVAILABLE"
    assert catalog.record_schematic_verification(project_id, conversation_id, **args) == original

    monkeypatch.setattr(
        catalog_runtime, "resolve_schematic_claim",
        lambda *_args, **_kwargs: ClaimRuleResult(
            status="BLOCKED", reason="EVIDENCE_MISMATCH",
        ),
    )
    revised = catalog.record_schematic_verification(project_id, conversation_id, **args)
    assert revised.claims[0].reason == "EVIDENCE_MISMATCH"
    assert revised.verification_id != original.verification_id
    assert catalog.record_schematic_verification(project_id, conversation_id, **args) == revised

    restarted = _restart_with_published_source(catalog, tmp_path)
    listed = restarted.schematic_verifications(
        project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1",
    )
    assert {item.verification_id for item, _ in listed} == {
        original.verification_id, revised.verification_id,
    }
    assert (original, "STALE") in listed
    assert (revised, "CURRENT") in listed


def _claim(
    verification_id: str, verification_type: str, claim: str, method: str,
    *, required_evidence_refs: tuple[str, ...] = (),
) -> VerificationRequirement:
    return VerificationRequirement(
        verification_id=verification_id, owner_ref="block-power", claim=claim,
        verification_type=verification_type, method=method,
        required_evidence_refs=required_evidence_refs,
    )


def _record_from_port(port, *, erc_operation_id: str | None = "erc-1"):
    return port.record_schematic_verification(
        "project-1", "conversation-1", tenant_id="tenant-1", requester="owner",
        intent_id="circuit-1", intent_revision="r1", intent_fingerprint=FP,
        plan_id="plan-1", plan_fingerprint=FP, artifact_fingerprint=FP,
        generation_operation_id="write-1", erc_operation_id=erc_operation_id,
    )


def test_canonical_parse_and_erc_claims_get_local_typed_proof_only(tmp_path) -> None:
    claims = (
        _claim("claim-parse", "SCHEMATIC_PARSE", "CURRENT_SCHEMATIC_ARTIFACT_PARSE", "CATALOG_PARSE_RECEIPT"),
        _claim("claim-erc", "SCHEMATIC_ERC", "CURRENT_SCHEMATIC_ARTIFACT_ERC_PASS", "CANONICAL_ERC_RECEIPT"),
        _claim("claim-power", "CALCULATION", "Power rail has margin", "Evaluate input sources"),
    )
    record = _record_generated_catalog(tmp_path, claims)
    assert [(item.claim_id, item.status) for item in record.claims] == [
        ("claim-parse", "PASS"), ("claim-erc", "PASS"), ("claim-power", "BLOCKED"),
    ]
    assert record.aggregate_status == "BLOCKED"
    for claim, scope, kind in zip(
        record.claims[:2], ("SCHEMATIC_PARSE", "SCHEMATIC_ERC"),
        ("CATALOG_PARSE_RECEIPT", "CANONICAL_ERC_RECEIPT"), strict=True,
    ):
        evidence = claim.proof
        assert evidence is not None
        assert evidence.kind == kind
        assert evidence.scope == scope
        assert evidence.project_id == record.project_id
        assert evidence.conversation_id == record.conversation_id
        assert evidence.intent_id == record.intent_id
        assert evidence.intent_revision == record.intent_revision
        assert evidence.plan_id == record.plan_id
        assert evidence.artifact_fingerprint == record.artifact_fingerprint
        assert evidence.verification_record_id == record.verification_id
        assert evidence.currentness_at_recording == "CURRENT"
        assert evidence.fingerprint.startswith("sha256:")


def test_parse_and_erc_only_claims_still_do_not_complete_schematic(tmp_path) -> None:
    claims = (
        _claim("claim-parse", "SCHEMATIC_PARSE", "CURRENT_SCHEMATIC_ARTIFACT_PARSE", "CATALOG_PARSE_RECEIPT"),
        _claim("claim-erc", "SCHEMATIC_ERC", "CURRENT_SCHEMATIC_ARTIFACT_ERC_PASS", "CANONICAL_ERC_RECEIPT"),
    )
    record = _record_generated_catalog(tmp_path, claims)
    assert [item.status for item in record.claims] == ["PASS", "PASS"]
    assert record.aggregate_status == "BLOCKED"


@pytest.mark.parametrize("verification_type,claim,method,refs", [
    ("SCHEMATIC_PARSE", "Power rail passes", "CATALOG_PARSE_RECEIPT", ()),
    ("SCHEMATIC_PARSE", "CURRENT_SCHEMATIC_ARTIFACT_PARSE", "user check", ("parse-receipt",)),
    ("SCHEMATIC_ERC", "Power rail passes", "CANONICAL_ERC_RECEIPT", ("erc-receipt-1",)),
    ("SCHEMATIC_ERC", "CURRENT_SCHEMATIC_ARTIFACT_ERC_PASS", "CATALOG_PARSE_RECEIPT", ()),
    ("CALCULATION", "CURRENT_SCHEMATIC_ARTIFACT_PARSE", "CATALOG_PARSE_RECEIPT", ()),
])
def test_similar_or_self_reported_claim_cannot_borrow_parse_or_erc_proof(
    verification_type, claim, method, refs,
) -> None:
    port = _port(claims=(
        _claim("claim-spoof", verification_type, claim, method, required_evidence_refs=refs),
    ))
    record = _record_from_port(port)
    assert record.claims[0].status == "BLOCKED"
    assert record.claims[0].evidence_ids == ()
    assert record.aggregate_status == "BLOCKED"


@pytest.mark.parametrize("source_digest", ["a", "b"])
def test_calculation_candidate_source_digest_never_promotes_claim(source_digest) -> None:
    port = _port(claims=(
        _claim("claim-calculation", "CALCULATION", "Power margin", "Evaluate inputs",
               required_evidence_refs=("candidate-input-source",)),
    ))
    port.intent.calculations = (CalculationRecord(
        calculation_id="calc-power", owner_ref="block-power", equation="P = V * I",
        inputs={"Vin": 5.0}, corner="VIN_MAX", status="PLANNED",
        input_sources=({
            "input_name": "Vin", "value": 5.0, "unit": "V",
            "source_kind": "HARDWARE_TRUTH", "source_id": "truth-power",
            "source_fingerprint": "sha256:" + source_digest * 64,
            "content_fingerprint": "sha256:" + source_digest * 64,
            "locator": "power:Vin", "condition": "VIN_MAX",
        },),
    ),)
    record = _record_from_port(port)
    assert record.claims[0].status == "BLOCKED"
    assert record.claims[0].evidence_ids == ()
    assert record.aggregate_status == "BLOCKED"


def test_canonical_claims_without_owner_receipts_or_current_sources_remain_blocked(tmp_path) -> None:
    parse_claim = _claim("claim-parse", "SCHEMATIC_PARSE", "CURRENT_SCHEMATIC_ARTIFACT_PARSE", "CATALOG_PARSE_RECEIPT")
    erc_claim = _claim("claim-erc", "SCHEMATIC_ERC", "CURRENT_SCHEMATIC_ARTIFACT_ERC_PASS", "CANONICAL_ERC_RECEIPT")
    without_erc = _record_generated_catalog(
        tmp_path, (parse_claim, erc_claim), erc_operation_id=None,
    )
    assert [item.status for item in without_erc.claims] == ["PASS", "BLOCKED"]
    assert without_erc.aggregate_status == "BLOCKED"
    port = _port(claims=(parse_claim, erc_claim), erc_status="ERC_FAIL")
    failed = _record_from_port(port)
    assert failed.claims[1].status == "BLOCKED"
    assert failed.aggregate_status == "FAILED"
    port = _port(claims=(parse_claim, erc_claim))
    port.current_bytes = False
    with pytest.raises(ProjectCatalogConflict, match="bytes"):
        _record_from_port(port)
    port = _port(claims=(parse_claim, erc_claim))
    port.source_current = False
    with pytest.raises(ProjectCatalogConflict, match="source"):
        _record_from_port(port)


@pytest.mark.parametrize("binding", [
    "generation_project", "generation_plan", "generation_artifact",
    "parse_project", "parse_conversation", "parse_operation", "parse_artifact",
    "erc_project", "erc_plan", "erc_artifact", "intent_revision",
])
def test_canonical_claims_reject_wrong_owner_subject_or_artifact(binding) -> None:
    port = _port(claims=(
        _claim("claim-parse", "SCHEMATIC_PARSE", "CURRENT_SCHEMATIC_ARTIFACT_PARSE", "CATALOG_PARSE_RECEIPT"),
        _claim("claim-erc", "SCHEMATIC_ERC", "CURRENT_SCHEMATIC_ARTIFACT_ERC_PASS", "CANONICAL_ERC_RECEIPT"),
    ))
    generation = port.detail.schematic_production.generation_receipt
    parse = generation.parse_validation
    erc = port.detail.schematic_production.erc_receipt
    changes = {
        "generation_project": (generation, "project_id", "foreign-project"),
        "generation_plan": (generation, "schematic_plan_id", "other-plan"),
        "generation_artifact": (generation, "artifact_fingerprint", "sha256:" + "b" * 64),
        "parse_project": (parse, "project_id", "foreign-project"),
        "parse_conversation": (parse, "conversation_id", "other-conversation"),
        "parse_operation": (parse, "operation_id", "other-write"),
        "parse_artifact": (parse, "artifact_fingerprint", "sha256:" + "b" * 64),
        "erc_project": (erc, "project_id", "foreign-project"),
        "erc_plan": (erc, "schematic_plan_fingerprint", "sha256:" + "b" * 64),
        "erc_artifact": (erc, "artifact_fingerprint", "sha256:" + "b" * 64),
        "intent_revision": (port.detail.schematic_planning.approved_plan, "circuit_intent_revision", "r2"),
    }
    target, field, value = changes[binding]
    setattr(target, field, value)
    with pytest.raises(ProjectCatalogConflict):
        _record_from_port(port)


def test_verification_rejects_legacy_unbound_plan_receipt() -> None:
    port = _port(plan_fingerprint=None)
    with pytest.raises(ProjectCatalogConflict, match="plan"):
        ProjectCatalogRuntime.record_schematic_verification(
            port, "project-1", "conversation-1", tenant_id="tenant-1", requester="owner",
            intent_id="circuit-1", intent_revision="r1", intent_fingerprint=FP,
            plan_id="plan-1", plan_fingerprint=FP, artifact_fingerprint=FP,
            generation_operation_id="write-1", erc_operation_id="erc-1",
        )


def test_verification_rejects_legacy_unbound_erc_receipt() -> None:
    port = _port(erc_plan_fingerprint=None)
    with pytest.raises(ProjectCatalogConflict, match="ERC receipt plan"):
        port.record_schematic_verification(
            "project-1", "conversation-1", tenant_id="tenant-1", requester="owner",
            intent_id="circuit-1", intent_revision="r1", intent_fingerprint=FP,
            plan_id="plan-1", plan_fingerprint=FP, artifact_fingerprint=FP,
            generation_operation_id="write-1", erc_operation_id="erc-1",
        )


def test_erc_failure_is_local_and_aggregate_failed() -> None:
    record = _port(erc_status="ERC_FAIL").record_schematic_verification(
        "project-1", "conversation-1", tenant_id="tenant-1", requester="owner",
        intent_id="circuit-1", intent_revision="r1", intent_fingerprint=FP,
        plan_id="plan-1", plan_fingerprint=FP, artifact_fingerprint=FP,
        generation_operation_id="write-1", erc_operation_id="erc-1",
    )
    assert record.erc_status == "FAILED"
    assert record.aggregate_status == "FAILED"
    assert record.claims[0].status == "BLOCKED"


def _s02_generated_catalog(
    tmp_path, *, claims: tuple[VerificationRequirement, ...] | None = None,
):
    selector = None

    def approve_intent(catalog, project, session):
        nonlocal selector
        detail = catalog.conversation(
            project.project_id, session.conversation.conversation_id,
            tenant_id="tenant-1", requester="engineer-1",
        )
        turn = ConversationTurn(
            turn_id="turn-design", request_id="request-design", route="GREENFIELD_DESIGN",
            status=ConversationTurnStatus.COMPLETED, question="Circuit intent",
            answer_text="Design analysis", created_at=NOW, completed_at=NOW,
        )
        detail = ConversationDetail.model_validate(detail.model_copy(update={
            "turns": (*detail.turns, turn), "fingerprint": "sha256:" + "0" * 64,
        }).model_dump(mode="python"))
        catalog.persist_conversation_detail(detail)
        command = _command(detail)
        if claims is not None:
            block = CircuitBlock.model_validate({
                **command.circuit_intent.blocks[0].model_dump(mode="python"),
                "verification_refs": tuple(item.verification_id for item in claims),
                "fingerprint": "sha256:" + "0" * 64,
            })
            command = command.model_copy(update={
                "circuit_intent": command.circuit_intent.model_copy(update={
                    "blocks": (block,), "verification_requirements": claims,
                }),
            })
        candidate = _service(catalog).preview(
            project_id=project.project_id, tenant_id="tenant-1", requester="engineer-1",
            command=command,
        )
        published = _service(catalog).decide(
            project_id=project.project_id, tenant_id="tenant-1", requester="engineer-1",
            command=PredesignDecisionCommand(
                purpose="PREDESIGN_APPROVAL", proposal_id=candidate.proposal.proposal_id,
                proposal_fingerprint=candidate.proposal.fingerprint,
                source_fingerprint=candidate.proposal.context_fingerprint, decision="APPROVE",
            ),
        )
        selector = (
            published.circuit.object_id, published.circuit.revision,
            published.circuit.fingerprint,
        )
        return selector

    catalog, project, _session, detail = _runtime_with_approved_plan(
        tmp_path, before_schematic_plan=approve_intent,
    )
    assert selector is not None
    production = detail.schematic_production
    assert production is not None
    plan = detail.schematic_planning.approved_plan
    assert plan is not None
    artifact_path = "hardware/main.kicad_sch"
    artifact = catalog.knowledge_snapshot_root(project.project_id, tenant_id="tenant-1") / artifact_path
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"offline schematic fixture")
    artifact_fp = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
    parse = SchematicParseValidationEvidence.create(
        validation_id="s02-parse", project_id=project.project_id,
        conversation_id=detail.conversation_id, operation_id="s02-write",
        artifact_path=artifact_path, artifact_fingerprint=artifact_fp,
        kicad_cli_path="offline-fixture", kicad_version="fixture-version",
        command_identity="offline-parse-fixture", exit_code=0,
        validation_status="PARSE_VERIFIED", artifact_unchanged=True,
        stdout_summary="fixture", stderr_summary="", validated_at=NOW,
    )
    generation = SchematicGenerationReceipt.create(
        receipt_id="s02-generation", project_id=project.project_id,
        conversation_id=detail.conversation_id, operation_id="s02-write",
        operation_fingerprint="sha256:" + "8" * 64,
        operation_receipt_fingerprint="sha256:" + "a" * 64,
        workspace_apply_receipt_fingerprint="sha256:" + "9" * 64,
        artifact_path=artifact_path, schematic_plan_id=plan.plan_id,
        schematic_plan_fingerprint=plan.fingerprint,
        design_truth_fingerprint=production.proposal.design_truth_fingerprint,
        artifact_fingerprint=artifact_fp, generator="offline-fixture",
        generator_version="1", validation_status="PARSE_VERIFIED",
        parse_validation=parse, status=SchematicGenerationStatus.SUCCESS,
        source_refs=("s02-write",), created_at=NOW,
    )
    result = SchematicErcResult.create(
        operation_id="s02-erc", schematic_artifact_ref=artifact_path,
        schematic_content_fingerprint=artifact_fp, kicad_version="fixture-version",
        exit_status=0, started_at=NOW, finished_at=NOW,
        receipt_ref="sha256:" + "a" * 64, status=SchematicErcStatus.ERC_PASS,
    )
    erc = SchematicErcReceipt.create(
        receipt_id="s02-erc-receipt", project_id=project.project_id,
        conversation_id=detail.conversation_id, operation_id=result.operation_id,
        operation_receipt_fingerprint="sha256:" + "b" * 64,
        artifact_path=artifact_path, artifact_fingerprint=artifact_fp,
        schematic_plan_fingerprint=plan.fingerprint,
        design_truth_fingerprint=production.proposal.design_truth_fingerprint,
        kicad_version=result.kicad_version, command_identity=result.command_identity,
        tool_execution_status="SUCCEEDED", engineering_status=result.engineering_status,
        result=result, created_at=NOW,
    )
    decision = decide_schematic_proposal(
        production.proposal, decision=SchematicProposalDecisionStatus.APPROVED,
        reviewer="engineer-1", decided_at=NOW,
    )
    generated = SchematicProductionState.create(
        production_id=production.production_id, project_id=project.project_id,
        conversation_id=detail.conversation_id, request=production.request,
        proposal=production.proposal, decision=decision, operation_id="s02-write",
        operation_fingerprint=generation.operation_fingerprint,
        status=SchematicProductionStatus.GENERATED, generation_receipt=generation,
        erc_receipt=erc, erc_gate_status=SchematicErcGateStatus.PASS,
    )
    detail = ConversationDetail.model_validate(detail.model_copy(update={
        "schematic_production": generated, "fingerprint": "sha256:" + "0" * 64,
    }).model_dump(mode="python"))
    catalog.persist_conversation_detail(detail)
    return catalog, project.project_id, detail.conversation_id, selector, plan, artifact_fp, artifact


def _record_generated_catalog(
    tmp_path, claims: tuple[VerificationRequirement, ...],
    *, erc_operation_id: str | None = "s02-erc",
):
    catalog, project_id, conversation_id, selector, plan, artifact_fp, _ = _s02_generated_catalog(
        tmp_path, claims=claims,
    )
    return catalog.record_schematic_verification(
        project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1",
        intent_id=selector[0], intent_revision=selector[1], intent_fingerprint=selector[2],
        plan_id=plan.plan_id, plan_fingerprint=plan.fingerprint,
        artifact_fingerprint=artifact_fp, generation_operation_id="s02-write",
        erc_operation_id=erc_operation_id,
    )


def test_verification_sqlite_restart_owner_and_stale_bytes(tmp_path) -> None:
    catalog, project_id, conversation_id, selector, plan, artifact_fp, artifact = _s02_generated_catalog(tmp_path)
    args = {
        "tenant_id": "tenant-1", "requester": "engineer-1", "intent_id": selector[0],
        "intent_revision": selector[1], "intent_fingerprint": selector[2],
        "plan_id": plan.plan_id, "plan_fingerprint": plan.fingerprint,
        "artifact_fingerprint": artifact_fp, "generation_operation_id": "s02-write",
        "erc_operation_id": "s02-erc",
    }
    record = catalog.record_schematic_verification(project_id, conversation_id, **args)
    assert record.erc_status == "PASS"
    assert record.aggregate_status == "BLOCKED"
    assert all(claim.status == "BLOCKED" for claim in record.claims)
    assert catalog.record_schematic_verification(project_id, conversation_id, **args) == record
    no_source = ProjectCatalogRuntime(
        project_root=tmp_path / "projects", state_database=tmp_path / "desktop.sqlite3",
        initialize_async=False,
    )
    assert no_source.schematic_verification(
        project_id, record.verification_id, tenant_id="tenant-1", requester="engineer-1",
    ) == (record, "STALE")
    restarted = _restart_with_published_source(catalog, tmp_path)
    assert restarted.schematic_verification(
        project_id, record.verification_id, tenant_id="tenant-1", requester="engineer-1",
    ) == (record, "CURRENT")
    with pytest.raises(ProjectCatalogForbidden):
        restarted.schematic_verification(
            project_id, record.verification_id, tenant_id="tenant-1", requester="other-user",
        )
    with pytest.raises(ProjectCatalogRejected):
        restarted.schematic_verification(
            "wrong-project", record.verification_id, tenant_id="tenant-1", requester="engineer-1",
        )
    artifact.write_bytes(b"changed fixture bytes")
    assert restarted.schematic_verification(
        project_id, record.verification_id, tenant_id="tenant-1", requester="engineer-1",
    ) == (record, "STALE")
    with pytest.raises(ProjectCatalogConflict, match="stale"):
        restarted.record_schematic_verification(project_id, conversation_id, **args)
    artifact.write_bytes(b"offline schematic fixture")
    detail = restarted.conversation(
        project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1",
    )
    changed_turns = tuple(
        item.model_copy(update={
            "question": "Changed design request", "fingerprint": "sha256:" + "0" * 64,
        }) if item.turn_id == "turn-design" else item for item in detail.turns
    )
    changed_detail = ConversationDetail.model_validate(detail.model_copy(update={
        "turns": changed_turns, "fingerprint": "sha256:" + "0" * 64,
    }).model_dump(mode="python"))
    restarted.persist_conversation_detail(changed_detail)
    assert restarted.schematic_verification(
        project_id, record.verification_id, tenant_id="tenant-1", requester="engineer-1",
    ) == (record, "STALE")
    with pytest.raises(ProjectCatalogConflict, match="stale"):
        restarted.record_schematic_verification(project_id, conversation_id, **args)


def test_canonical_claim_proof_survives_restart_but_stale_history_is_not_current(tmp_path) -> None:
    claims = (
        _claim("claim-parse", "SCHEMATIC_PARSE", "CURRENT_SCHEMATIC_ARTIFACT_PARSE", "CATALOG_PARSE_RECEIPT"),
        _claim("claim-erc", "SCHEMATIC_ERC", "CURRENT_SCHEMATIC_ARTIFACT_ERC_PASS", "CANONICAL_ERC_RECEIPT"),
    )
    catalog, project_id, conversation_id, selector, plan, artifact_fp, artifact = _s02_generated_catalog(
        tmp_path, claims=claims,
    )
    record = catalog.record_schematic_verification(
        project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1",
        intent_id=selector[0], intent_revision=selector[1], intent_fingerprint=selector[2],
        plan_id=plan.plan_id, plan_fingerprint=plan.fingerprint,
        artifact_fingerprint=artifact_fp, generation_operation_id="s02-write",
        erc_operation_id="s02-erc",
    )
    assert [item.status for item in record.claims] == ["PASS", "PASS"]
    assert record.aggregate_status == "BLOCKED"
    restarted = _restart_with_published_source(catalog, tmp_path)
    assert restarted.schematic_verification(
        project_id, record.verification_id, tenant_id="tenant-1", requester="engineer-1",
    ) == (record, "CURRENT")
    artifact.write_bytes(b"changed fixture bytes")
    historical, currentness = restarted.schematic_verification(
        project_id, record.verification_id, tenant_id="tenant-1", requester="engineer-1",
    )
    assert currentness == "STALE"
    assert historical == record
    assert historical.aggregate_status == "BLOCKED"
    assert all(
        item.proof.currentness_at_recording == "CURRENT"
        for item in historical.claims if item.proof is not None
    )


def test_historical_pass_becomes_stale_when_current_rule_no_longer_accepts_it(
    tmp_path, monkeypatch,
) -> None:
    import embedded_copilot.project_catalog.runtime as catalog_runtime
    from embedded_copilot.engineering_copilot.schematic_claim_rules import (
        ClaimRuleResult,
    )

    claim = _claim(
        "claim-parse", "SCHEMATIC_PARSE", "CURRENT_SCHEMATIC_ARTIFACT_PARSE",
        "CATALOG_PARSE_RECEIPT",
    )
    catalog, project_id, conversation_id, selector, plan, artifact_fp, _ = _s02_generated_catalog(
        tmp_path, claims=(claim,),
    )
    record = catalog.record_schematic_verification(
        project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1",
        intent_id=selector[0], intent_revision=selector[1], intent_fingerprint=selector[2],
        plan_id=plan.plan_id, plan_fingerprint=plan.fingerprint,
        artifact_fingerprint=artifact_fp, generation_operation_id="s02-write",
        erc_operation_id="s02-erc",
    )
    assert record.claims[0].status == "PASS"
    monkeypatch.setattr(
        catalog_runtime, "resolve_schematic_claim",
        lambda *_args, **_kwargs: ClaimRuleResult(
            status="BLOCKED", reason="NO_TRUSTED_RESOLVER",
        ),
    )
    assert catalog.schematic_verification(
        project_id, record.verification_id, tenant_id="tenant-1", requester="engineer-1",
    ) == (record, "STALE")
    assert record.claims[0].proof is not None
    assert record.claims[0].proof.currentness_at_recording == "CURRENT"


def test_catalog_revalidates_constructed_owner_erc_before_recording(tmp_path, monkeypatch) -> None:
    claims = (
        _claim("claim-erc", "SCHEMATIC_ERC", "CURRENT_SCHEMATIC_ARTIFACT_ERC_PASS", "CANONICAL_ERC_RECEIPT"),
    )
    catalog, project_id, conversation_id, selector, plan, artifact_fp, _ = _s02_generated_catalog(
        tmp_path, claims=claims,
    )
    detail = catalog.conversation(
        project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1",
    )
    production = detail.schematic_production
    assert production is not None and production.erc_receipt is not None
    erc = production.erc_receipt
    invalid_result = erc.result.model_copy(update={"exit_status": 1})
    invalid_erc = erc.model_copy(update={"result": invalid_result})
    invalid_detail = detail.model_copy(update={
        "schematic_production": production.model_copy(update={"erc_receipt": invalid_erc}),
    })
    monkeypatch.setattr(catalog, "conversation", lambda *_args, **_kwargs: invalid_detail)
    with pytest.raises(ProjectCatalogConflict, match="Owner source"):
        catalog.record_schematic_verification(
            project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1",
            intent_id=selector[0], intent_revision=selector[1], intent_fingerprint=selector[2],
            plan_id=plan.plan_id, plan_fingerprint=plan.fingerprint,
            artifact_fingerprint=artifact_fp, generation_operation_id="s02-write",
            erc_operation_id="s02-erc",
        )
    assert catalog.schematic_verifications(
        project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1",
    ) == ()


def test_failed_record_is_retained_after_new_erc_receipt(tmp_path) -> None:
    catalog, project_id, conversation_id, selector, plan, artifact_fp, _ = _s02_generated_catalog(tmp_path)
    detail = catalog.conversation(project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1")
    production = detail.schematic_production
    assert production is not None and production.erc_receipt is not None
    passed = production.erc_receipt
    failed_result = SchematicErcResult.create(
        operation_id="s02-erc-failed", schematic_artifact_ref=passed.artifact_path,
        schematic_content_fingerprint=artifact_fp, kicad_version="fixture-version",
        exit_status=-1, started_at=NOW, finished_at=NOW,
        receipt_ref="sha256:" + "c" * 64, status=SchematicErcStatus.ERC_BLOCKED,
    )
    failed = SchematicErcReceipt.create(**{
        **passed.model_dump(mode="python", exclude={
            "fingerprint", "result", "engineering_status", "operation_id", "receipt_id",
        }),
        "receipt_id": "s02-erc-failed-receipt", "operation_id": failed_result.operation_id,
        "result": failed_result, "engineering_status": failed_result.engineering_status,
    })
    failed_production = SchematicProductionState.create(
        **{
            **production.model_dump(mode="python", exclude={"fingerprint", "erc_receipt", "erc_gate_status"}),
            "erc_receipt": failed, "erc_gate_status": SchematicErcGateStatus.BLOCKED,
        }
    )
    failed_detail = ConversationDetail.model_validate(detail.model_copy(update={
        "schematic_production": failed_production, "fingerprint": "sha256:" + "0" * 64,
    }).model_dump(mode="python"))
    catalog.persist_conversation_detail(failed_detail)
    args = {
        "tenant_id": "tenant-1", "requester": "engineer-1", "intent_id": selector[0],
        "intent_revision": selector[1], "intent_fingerprint": selector[2],
        "plan_id": plan.plan_id, "plan_fingerprint": plan.fingerprint,
        "artifact_fingerprint": artifact_fp, "generation_operation_id": "s02-write",
    }
    old = catalog.record_schematic_verification(
        project_id, conversation_id, **args, erc_operation_id="s02-erc-failed",
    )
    assert old.erc_status == "FAILED" and old.aggregate_status == "FAILED"
    catalog.persist_conversation_detail(detail)
    new = catalog.record_schematic_verification(
        project_id, conversation_id, **args, erc_operation_id="s02-erc",
    )
    assert new.verification_id != old.verification_id
    assert new.erc_status == "PASS" and new.aggregate_status == "BLOCKED"
    restarted = _restart_with_published_source(catalog, tmp_path)
    listed = restarted.schematic_verifications(
        project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1",
    )
    assert {item.verification_id for item, _ in listed} == {old.verification_id, new.verification_id}
    assert (old, "STALE") in listed
    assert (new, "CURRENT") in listed
    with pytest.raises(ProjectCatalogForbidden):
        restarted.schematic_verifications(
            project_id, conversation_id, tenant_id="tenant-1", requester="other-user",
        )
    with pytest.raises(ProjectCatalogRejected):
        restarted.schematic_verifications(
            "wrong-project", conversation_id, tenant_id="tenant-1", requester="engineer-1",
        )
    assert len(restarted.schematic_verifications(
        project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1", limit=1,
    )) == 1


def test_erc_receipt_registration_is_idempotent_and_stale_bytes_reject_replay(tmp_path) -> None:
    catalog, project_id, conversation_id, _selector, _plan, artifact_fp, artifact = (
        _s02_generated_catalog(tmp_path)
    )
    detail = catalog.conversation(
        project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1",
    )
    production = detail.schematic_production
    assert production is not None and production.generation_receipt is not None
    generation = production.generation_receipt
    operation = SchematicErcOperation(
        operation_id="s02-erc", tenant_id="tenant-1", project_id=project_id,
        requester_id="engineer-1", target_id=production.request.workspace_identity,
        target_fingerprint=FP, workspace_snapshot_id="snapshot-1",
        workspace_fingerprint=FP, proposal_id=production.proposal.proposal_id,
        proposal_fingerprint=production.proposal.fingerprint,
        operation_type=OperationType.KICAD_SCHEMATIC_ERC,
        created_at=NOW, expires_at=NOW + timedelta(minutes=15), nonce="s02-erc-nonce",
        conversation_id=conversation_id, schematic_artifact_ref=generation.artifact_path,
        schematic_artifact_fingerprint=artifact_fp,
        source_operation_id=generation.operation_id,
        source_operation_receipt_fingerprint=generation.operation_receipt_fingerprint,
        target_relative_path=generation.artifact_path,
    )
    result = production.erc_receipt.result
    receipt = OperationReceipt(
        operation_id=operation.operation_id, tenant_id="tenant-1", project_id=project_id,
        operation_type=OperationType.KICAD_SCHEMATIC_ERC,
        status=OperationStatus.SUCCEEDED, tool_execution_status="SUCCEEDED",
        flash_verification_status="NOT_APPLICABLE",
        target_runtime_verification_status="NOT_APPLICABLE",
        evidence_references=("offline-erc-report",),
        safe_diagnostics_summary="KICAD_ERC_PASS",
        schematic_erc_status="ERC_PASS",
        schematic_erc_result_fingerprint=result.fingerprint,
        schematic_erc_result=result, created_at=NOW,
    )
    unrecorded = SchematicProductionState.create(
        production_id=production.production_id, project_id=project_id,
        conversation_id=conversation_id, request=production.request,
        proposal=production.proposal, decision=production.decision,
        operation_id=production.operation_id,
        operation_fingerprint=production.operation_fingerprint,
        status=production.status, generation_receipt=generation,
    )
    catalog.persist_conversation_detail(ConversationDetail.model_validate(
        detail.model_copy(update={
            "schematic_production": unrecorded, "fingerprint": "sha256:" + "0" * 64,
        }).model_dump(mode="python")
    ))
    first = catalog.record_schematic_erc_validation(operation=operation, receipt=receipt)
    assert first.schematic_production.erc_receipt.operation_receipt_fingerprint == receipt.fingerprint
    assert catalog.record_schematic_erc_validation(operation=operation, receipt=receipt) == first
    conflicting_receipt = OperationReceipt.model_validate({
        **receipt.model_dump(mode="python", exclude={"fingerprint"}),
        "evidence_references": ("different-erc-report",),
    })
    with pytest.raises(ProjectCatalogRejected, match="identity conflict"):
        catalog.record_schematic_erc_validation(
            operation=operation, receipt=conflicting_receipt,
        )
    restarted = ProjectCatalogRuntime(
        project_root=tmp_path / "projects", state_database=tmp_path / "desktop.sqlite3",
        initialize_async=False,
    )
    assert restarted.record_schematic_erc_validation(operation=operation, receipt=receipt) == first
    artifact.write_bytes(b"stale schematic")
    with pytest.raises(ProjectCatalogRejected, match="artifact fingerprint mismatch"):
        restarted.record_schematic_erc_validation(operation=operation, receipt=receipt)
