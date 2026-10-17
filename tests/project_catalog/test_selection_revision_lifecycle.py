from __future__ import annotations

import sqlite3

import pytest

from tests.project_catalog.test_source_replan_contract import _approved_v3_legacy_plan


def choose(preview):
    return tuple(
        next(
            (x["slot_id"], x["source_id"], x["source_fingerprint"])
            for x in preview["choices"]
            if x["slot_id"] == slot
        )
        for slot in preview["required_slots"]
    )


def test_formal_selection_replacement_preserves_old_raw_and_requires_independent_approval(
    tmp_path, monkeypatch
):
    catalog, detail, *_ = _approved_v3_legacy_plan(tmp_path, monkeypatch)
    scope = {"tenant_id": detail.tenant_id, "requester": detail.requester}
    with sqlite3.connect(catalog._repository._database) as db:
        original = db.execute(
            "SELECT body FROM conversations WHERE conversation_id=?",
            (detail.conversation_id,),
        ).fetchone()[0]
    preview = catalog.selection_revision_view(
        detail.project_id, detail.conversation_id, view="PREVIEW", **scope
    )
    result = catalog.create_hardware_selection_proposal_v2(
        detail.project_id,
        detail.conversation_id,
        request_id="replace-1",
        expected_head=tuple(preview["expected_head"]),
        preview_fingerprint=preview["preview_fingerprint"],
        selections=choose(preview),
        **scope,
    )
    head = result["head"]
    current = catalog.conversation(detail.project_id, detail.conversation_id, **scope)
    assert current.hardware_selection.status.value == "REVIEW"
    assert current.schematic_planning is None and current.design_truth is None
    assert head["selection_id"] != detail.hardware_selection.proposal.proposal_id
    with sqlite3.connect(catalog._repository._database) as db:
        saved = db.execute(
            "SELECT predecessor_conversation_body FROM hardware_selection_revisions WHERE revision_id=?",
            (head["revision_id"],),
        ).fetchone()[0]
    assert saved == original
    approved = catalog.decide_hardware_selection_v2(
        detail.project_id,
        detail.conversation_id,
        revision_id=head["revision_id"],
        selection_id=head["selection_id"],
        selection_fingerprint=head["selection_fingerprint"],
        expected_decision_fingerprint=None,
        decision="APPROVED",
        **scope,
    )
    assert approved["head"]["status"] == "APPROVED"
    assert (
        catalog.conversation(
            detail.project_id, detail.conversation_id, **scope
        ).design_truth
        is not None
    )


def prepare(catalog, detail, request_id="replacement"):
    scope = {"tenant_id": detail.tenant_id, "requester": detail.requester}
    preview = catalog.selection_revision_view(
        detail.project_id, detail.conversation_id, view="PREVIEW", **scope
    )
    args = {
        "request_id": request_id,
        "expected_head": tuple(preview["expected_head"]),
        "preview_fingerprint": preview["preview_fingerprint"],
        "selections": choose(preview),
    }
    result = catalog.create_hardware_selection_proposal_v2(
        detail.project_id, detail.conversation_id, **args, **scope
    )
    return result, args, scope


def decision(catalog, detail, head, value):
    return catalog.decide_hardware_selection_v2(
        detail.project_id,
        detail.conversation_id,
        tenant_id=detail.tenant_id,
        requester=detail.requester,
        revision_id=head["revision_id"],
        selection_id=head["selection_id"],
        selection_fingerprint=head["selection_fingerprint"],
        expected_decision_fingerprint=head["decision_fingerprint"],
        decision=value,
    )


def test_revoke_preserves_approval_event_and_never_falls_back_to_old_approved(
    tmp_path, monkeypatch
):
    from datetime import UTC, datetime

    from embedded_copilot.engineering_copilot import HardwareSelectionDecision

    catalog, detail, *_ = _approved_v3_legacy_plan(tmp_path, monkeypatch)
    result, _args, scope = prepare(catalog, detail)
    with pytest.raises(Exception, match="SELECTION_REVISION_SELECTOR_REQUIRED"):
        catalog.decide_hardware_selection(
            detail.project_id,
            detail.conversation_id,
            HardwareSelectionDecision.APPROVED,
            decided_at=datetime.now(UTC),
            **scope,
        )
    approved = decision(catalog, detail, result["head"], "APPROVED")
    revoked = decision(catalog, detail, approved["head"], "REVOKED")
    assert revoked["head"]["status"] == "REVOKED" and revoked["selection"] is None
    assert (
        catalog.conversation(
            detail.project_id, detail.conversation_id, **scope
        ).hardware_selection
        is None
    )
    with sqlite3.connect(catalog._repository._database) as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM hardware_selection_revision_decisions"
            ).fetchone()[0]
            == 2
        )
        rows = db.execute(
            "SELECT decision FROM hardware_selection_revision_decisions ORDER BY rowid"
        ).fetchall()
        assert rows == [("APPROVED",), ("REVOKED",)]
    newer, _, _ = prepare(catalog, detail, "replacement-2")
    old = catalog.selection_revision_view(
        detail.project_id,
        detail.conversation_id,
        view="HISTORY",
        revision_id=revoked["head"]["revision_id"],
        **scope,
    )
    assert old["head"]["currentness"] == "STALE" and old["head"]["status"] == "REVOKED"
    assert newer["head"]["revision_number"] == 2


def test_preview_is_readonly_and_same_request_is_idempotent(tmp_path, monkeypatch):
    catalog, detail, *_ = _approved_v3_legacy_plan(tmp_path, monkeypatch)
    scope = {"tenant_id": detail.tenant_id, "requester": detail.requester}
    with sqlite3.connect(catalog._repository._database) as db:
        raw = db.execute(
            "SELECT body FROM conversations WHERE conversation_id=?",
            (detail.conversation_id,),
        ).fetchone()[0]
    catalog.selection_revision_view(
        detail.project_id, detail.conversation_id, view="PREVIEW", **scope
    )
    with sqlite3.connect(catalog._repository._database) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM hardware_selection_revisions").fetchone()[
                0
            ]
            == 0
        )
        assert (
            db.execute(
                "SELECT body FROM conversations WHERE conversation_id=?",
                (detail.conversation_id,),
            ).fetchone()[0]
            == raw
        )
    result, args, scope = prepare(catalog, detail)
    repeated = catalog.create_hardware_selection_proposal_v2(
        detail.project_id, detail.conversation_id, **args, **scope
    )
    assert repeated["head"]["revision_id"] == result["head"]["revision_id"]
    with pytest.raises(Exception, match="SELECTION_REQUEST_CONFLICT"):
        catalog.create_hardware_selection_proposal_v2(
            detail.project_id,
            detail.conversation_id,
            **{**args, "selections": args["selections"][:-1]},
            **scope,
        )
    with sqlite3.connect(catalog._repository._database) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM hardware_selection_revisions").fetchone()[
                0
            ]
            == 1
        )


def test_real_catalog_replacement_new_circuit_p1_replan_and_restore(tmp_path):
    from datetime import UTC, datetime

    from embedded_copilot.application.circuit_publication import (
        PredesignDecisionCommand,
    )
    from embedded_copilot.engineering_copilot import (
        SchematicPlanDecision,
        SchematicPlanningService,
    )
    from embedded_copilot.project_catalog.store import SQLiteProjectCatalogRepository
    from tests.engineering_copilot.test_schematic_production_g3 import (
        _runtime_with_approved_plan,
    )
    from tests.project_catalog.test_predesign_publication import _command, _service

    catalog, project, session, _ = _runtime_with_approved_plan(tmp_path)
    scope = {"tenant_id": "tenant-1", "requester": "engineer-1"}
    detail = catalog.conversation(
        project.project_id, session.conversation.conversation_id, **scope
    )
    old_plan = detail.schematic_planning.approved_plan
    proposed, _, _ = prepare(catalog, detail, "full-loop-selection")
    decision(catalog, detail, proposed["head"], "APPROVED")
    fresh = catalog.conversation(detail.project_id, detail.conversation_id, **scope)
    publication = _service(catalog)
    old_circuit = catalog.current_circuit_intents(detail.project_id, **scope)[0]
    command = _command(fresh).model_copy(
        update={
            "idempotency_key": "selection-revision-circuit",
            "object_id": old_circuit.object_id,
            "base_revision": old_circuit.revision,
            "base_fingerprint": old_circuit.fingerprint,
        }
    )
    preview = publication.preview(
        project_id=detail.project_id, **scope, command=command
    )
    published = publication.decide(
        project_id=detail.project_id,
        **scope,
        command=PredesignDecisionCommand(
            purpose="PREDESIGN_APPROVAL",
            proposal_id=preview.proposal.proposal_id,
            proposal_fingerprint=preview.proposal.fingerprint,
            source_fingerprint=preview.proposal.context_fingerprint,
            decision="APPROVE",
            base_revision=preview.base_revision,
            base_fingerprint=preview.base_fingerprint,
        ),
    )
    circuit = published.circuit
    fresh = catalog.conversation(detail.project_id, detail.conversation_id, **scope)
    selection = fresh.hardware_selection
    truth = fresh.design_truth
    layout = SchematicPlanningService().create_proposal(
        fresh.architecture_design,
        fresh.requirement_context,
        selection.hardware_truth,
        selection.bom,
        design_truth=truth,
        conversation_id=fresh.conversation_id,
        circuit_intent=circuit,
        selection=selection,
    )
    domain = next(x for x in layout.power_domains if x.name != "GND")
    supplynet = next(x for x in layout.nets if x.name == domain.name)
    ground = next(x for x in layout.nets if x.name == "GND")
    refs = tuple(
        next(
            x.binding_id
            for x in truth.evidence_bindings
            if x.entity_id == component and x.source.value == "DATASHEET"
        )
        for component in domain.source_component_ids + domain.consumer_component_ids
    )
    candidate = catalog.create_project_supply_candidate(
        detail.project_id,
        detail.conversation_id,
        **scope,
        selectors={
            "design_truth_id": truth.truth_id,
            "design_truth_fingerprint": truth.fingerprint,
            "selection_id": selection.proposal.proposal_id,
            "selection_fingerprint": selection.proposal.fingerprint,
            "circuit_id": circuit.object_id,
            "circuit_fingerprint": circuit.fingerprint,
            "plan_id": fresh.engineering_plan.plan_id,
            "plan_fingerprint": fresh.engineering_plan.fingerprint,
        },
        relations=(
            {
                "power_domain_id": domain.domain_id,
                "source_component_refs": domain.source_component_ids,
                "consumer_component_refs": domain.consumer_component_ids,
                "nominal_voltage_v": "3.3",
                "supply_net_ref": supplynet.net_id,
                "ground_net_ref": ground.net_id,
                "evidence_refs": refs,
            },
        ),
    )
    catalog.review_project_supply_candidate(
        detail.project_id,
        detail.conversation_id,
        candidate.candidate.candidate_id,
        candidate.candidate.fingerprint,
        "APPROVED",
        **scope,
    )
    replacement = catalog.create_schematic_plan_v2(
        detail.project_id,
        detail.conversation_id,
        **scope,
        request_id="full-loop-replan",
        expected_head=(None, old_plan.plan_id, old_plan.fingerprint),
    )
    approved = catalog.decide_schematic_plan_v2(
        detail.project_id,
        detail.conversation_id,
        SchematicPlanDecision.APPROVED,
        **scope,
        revision_id=replacement["revision_id"],
        plan_id=replacement["plan_id"],
        plan_fingerprint=replacement["plan_fingerprint"],
        decided_at=datetime.now(UTC),
    )
    assert approved["currentness"] == "CURRENT"
    assert approved["plan_id"] != old_plan.plan_id
    repo = SQLiteProjectCatalogRepository(catalog._repository._database)
    current = repo.schematic_plan_revision_head(
        tenant_id=detail.tenant_id,
        project_id=detail.project_id,
        conversation_id=detail.conversation_id,
        requester=detail.requester,
    )
    assert current["revision_id"] == replacement["revision_id"]
    legacy = repo.schematic_plan_legacy_snapshot(
        tenant_id=detail.tenant_id,
        project_id=detail.project_id,
        conversation_id=detail.conversation_id,
        requester=detail.requester,
    )
    assert legacy["plan_id"] == old_plan.plan_id and legacy["currentness"] == "STALE"


def test_publication_aba_stales_revision_and_blocks_old_approval_without_writes(
    tmp_path, monkeypatch
):
    from embedded_copilot.component_recommendation.contracts import (
        ComponentSourceRecord,
    )
    from embedded_copilot.project_catalog.exceptions import ProjectCatalogError
    from embedded_copilot.project_catalog.source_publication import (
        ComponentSourcePublishCommandV1,
        TrustedComponentSourcePublisher,
    )

    catalog, detail, *_ = _approved_v3_legacy_plan(tmp_path, monkeypatch)
    result, _, _ = prepare(catalog, detail)
    decision(catalog, detail, result["head"], "APPROVED")
    port = catalog._hardware_component_source
    old = port.current_publication(
        detail.project_id,
        result["selection"]["proposal"]["selected_components"][0]["source"][
            "source_id"
        ],
    )
    publisher = TrustedComponentSourcePublisher(
        catalog._repository,
        tenant_id=detail.tenant_id,
        project_id=detail.project_id,
        publisher_id=port.publisher_id,
    )
    different = ComponentSourceRecord.create(
        **{
            **{
                name: getattr(old.record, name)
                for name in type(old.record).model_fields
                if name != "fingerprint"
            },
            "description": "new applicability evidence",
        }
    )
    b, _ = publisher.publish(
        ComponentSourcePublishCommandV1(
            schema_version=1,
            request_id="selection-b",
            source_id=old.source_id,
            expected_head_publication_fingerprint=old.fingerprint,
            record=different,
        )
    )
    publisher.publish(
        ComponentSourcePublishCommandV1(
            schema_version=1,
            request_id="selection-a2",
            source_id=old.source_id,
            expected_head_publication_fingerprint=b.fingerprint,
            record=old.record,
        )
    )
    scope = {"tenant_id": detail.tenant_id, "requester": detail.requester}
    current = catalog.selection_revision_view(
        detail.project_id, detail.conversation_id, **scope
    )
    assert current["head"]["currentness"] == "STALE"
    raw = catalog._repository._database.read_bytes()
    state = catalog.conversation(
        detail.project_id, detail.conversation_id, **scope
    ).hardware_selection
    with pytest.raises(ProjectCatalogError):
        catalog._require_current_model_selection(state)
    assert catalog._repository._database.read_bytes() == raw
    new, _, _ = prepare(catalog, detail, "after-aba")
    assert new["head"]["selection_id"] != result["head"]["selection_id"]
    history = catalog.selection_revision_view(
        detail.project_id,
        detail.conversation_id,
        view="HISTORY",
        revision_id=result["head"]["revision_id"],
        **scope,
    )
    assert (
        history["head"]["currentness"] == "STALE"
        and history["head"]["reason"] == "NON_HEAD"
    )


@pytest.mark.parametrize("same_request", [True, False])
def test_independent_catalogs_concurrent_cas_one_revision(
    tmp_path, monkeypatch, same_request
):
    import copy
    from concurrent.futures import ThreadPoolExecutor

    from embedded_copilot.project_catalog.exceptions import ProjectCatalogError
    from embedded_copilot.project_catalog.source_publication import (
        PublishedComponentSourcePort,
    )
    from embedded_copilot.project_catalog.store import SQLiteProjectCatalogRepository

    catalog, detail, *_ = _approved_v3_legacy_plan(tmp_path, monkeypatch)
    other = copy.copy(catalog)
    other._repository = SQLiteProjectCatalogRepository(catalog._repository._database)
    source = catalog._hardware_component_source
    other.set_hardware_component_source(
        PublishedComponentSourcePort(
            other._repository,
            tenant_id=detail.tenant_id,
            publisher_id=source.publisher_id,
        )
    )
    scope = {"tenant_id": detail.tenant_id, "requester": detail.requester}
    preview = catalog.selection_revision_view(
        detail.project_id, detail.conversation_id, view="PREVIEW", **scope
    )

    def create(runtime, request):
        try:
            return runtime.create_hardware_selection_proposal_v2(
                detail.project_id,
                detail.conversation_id,
                request_id=request,
                expected_head=tuple(preview["expected_head"]),
                preview_fingerprint=preview["preview_fingerprint"],
                selections=choose(preview),
                **scope,
            )
        except ProjectCatalogError:
            return None

    with ThreadPoolExecutor(2) as executor:
        a = executor.submit(create, catalog, "race-a")
        b = executor.submit(create, other, "race-a" if same_request else "race-b")
        results = [a.result(), b.result()]
    assert sum(x is not None for x in results) == (2 if same_request else 1)
    if same_request:
        assert results[0]["head"]["revision_id"] == results[1]["head"]["revision_id"]
    with sqlite3.connect(catalog._repository._database) as db:
        assert (
            db.execute("SELECT COUNT(*) FROM hardware_selection_revisions").fetchone()[
                0
            ]
            == 1
        )


def test_owner_strict_scope_and_transaction_rollback_preserve_raw(
    tmp_path, monkeypatch
):
    from embedded_copilot.project_catalog.exceptions import ProjectCatalogError

    catalog, detail, *_ = _approved_v3_legacy_plan(tmp_path, monkeypatch)
    scope = {"tenant_id": detail.tenant_id, "requester": detail.requester}
    preview = catalog.selection_revision_view(
        detail.project_id, detail.conversation_id, view="PREVIEW", **scope
    )
    original = catalog._repository._database.read_bytes()
    for bad in (
        {"tenant_id": "other-tenant", "requester": detail.requester},
        {"tenant_id": detail.tenant_id, "requester": "other-owner"},
    ):
        with pytest.raises(ProjectCatalogError):
            catalog.selection_revision_view(
                detail.project_id, detail.conversation_id, view="PREVIEW", **bad
            )
    assert catalog._repository._database.read_bytes() == original
    with sqlite3.connect(catalog._repository._database) as db:
        raw = db.execute(
            "SELECT body FROM conversations WHERE conversation_id=?",
            (detail.conversation_id,),
        ).fetchone()[0]
        db.execute(
            "CREATE TRIGGER reject_selection_update BEFORE UPDATE ON conversations BEGIN SELECT RAISE(ABORT,'offline rollback probe'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        catalog.create_hardware_selection_proposal_v2(
            detail.project_id,
            detail.conversation_id,
            request_id="abort",
            expected_head=tuple(preview["expected_head"]),
            preview_fingerprint=preview["preview_fingerprint"],
            selections=choose(preview),
            **scope,
        )
    with sqlite3.connect(catalog._repository._database) as db:
        assert (
            db.execute(
                "SELECT body FROM conversations WHERE conversation_id=?",
                (detail.conversation_id,),
            ).fetchone()[0]
            == raw
        )
        assert (
            db.execute("SELECT COUNT(*) FROM hardware_selection_revisions").fetchone()[
                0
            ]
            == 0
        )
        assert (
            db.execute(
                "SELECT COUNT(*) FROM selection_schematic_plan_anchors"
            ).fetchone()[0]
            == 0
        )


def test_model_context_selector_and_late_response_reject_changed_head(
    tmp_path, monkeypatch
):
    from embedded_copilot.project_catalog.exceptions import ProjectCatalogError

    catalog, detail, *_ = _approved_v3_legacy_plan(tmp_path, monkeypatch)
    scope = {"tenant_id": detail.tenant_id, "requester": detail.requester}
    preview = catalog.selection_revision_view(
        detail.project_id, detail.conversation_id, view="PREVIEW", **scope
    )

    class Authority:
        def revalidate(self, reference):
            return reference

    source = Authority()
    kwargs = {
        "operation_id": "HARDWARE_SELECTION_PROPOSAL",
        "authority_source": source,
        "authority_reference": object(),
        "selection_revision_input": {
            "expected_head": preview["expected_head"],
            "preview_fingerprint": preview["preview_fingerprint"],
        },
    }
    context = catalog.model_operation_candidate_context(
        detail.project_id, detail.conversation_id, **scope, **kwargs
    )
    assert (
        context["selection_preview"]["preview_fingerprint"]
        == preview["preview_fingerprint"]
    )
    prepare(catalog, detail)
    before = catalog._repository._database.read_bytes()
    legacy_kwargs = {
        key: value for key, value in kwargs.items() if key != "selection_revision_input"
    }
    with pytest.raises(ProjectCatalogError):
        catalog.model_operation_candidate_context(
            detail.project_id, detail.conversation_id, **scope, **legacy_kwargs
        )
    with pytest.raises(ProjectCatalogError):
        catalog.model_operation_candidate_context(
            detail.project_id, detail.conversation_id, **scope, **kwargs
        )
    with pytest.raises(ProjectCatalogError):
        catalog.create_hardware_selection_proposal_v2(
            detail.project_id,
            detail.conversation_id,
            request_id="late-model",
            expected_head=tuple(preview["expected_head"]),
            preview_fingerprint=preview["preview_fingerprint"],
            selections=choose(preview),
            **scope,
        )
    assert catalog._repository._database.read_bytes() == before


def test_selection_must_exactly_match_frozen_preview_membership_before_proposal(
    tmp_path, monkeypatch
):
    from embedded_copilot.project_catalog.exceptions import ProjectCatalogConflict

    catalog, detail, *_ = _approved_v3_legacy_plan(tmp_path, monkeypatch)
    scope = {"tenant_id": detail.tenant_id, "requester": detail.requester}
    preview = catalog.selection_revision_view(
        detail.project_id, detail.conversation_id, view="PREVIEW", **scope
    )
    choices = choose(preview)
    before = catalog._repository._database.read_bytes()
    invalid = (
        choices[:-1],
        choices + (("unshown-support-slot", "unshown-source", "sha256:" + "1" * 64),),
        ((choices[0][0], "unshown-source", choices[0][2]),) + choices[1:],
    )
    for ordinal, selections in enumerate(invalid):
        with pytest.raises(
            ProjectCatalogConflict, match="SELECTION_PREVIEW_CHOICES_MISMATCH"
        ):
            catalog.create_hardware_selection_proposal_v2(
                detail.project_id,
                detail.conversation_id,
                request_id=f"invalid-preview-{ordinal}",
                expected_head=tuple(preview["expected_head"]),
                preview_fingerprint=preview["preview_fingerprint"],
                selections=selections,
                **scope,
            )
    assert catalog._repository._database.read_bytes() == before


@pytest.mark.parametrize("mode", ["replace", "revoke"])
def test_rich_aggregate_v2_transaction_history_and_repository_restart(
    tmp_path, monkeypatch, mode
):
    from embedded_copilot.project_catalog.store import SQLiteProjectCatalogRepository
    from tests.project_catalog.test_selection_revision_projection import _bound_stack

    catalog, detail, *_ = _approved_v3_legacy_plan(tmp_path, monkeypatch)
    if mode == "revoke":
        initial, _, _ = prepare(catalog, detail)
        decision(catalog, detail, initial["head"], "APPROVED")
        detail = catalog.conversation(
            detail.project_id,
            detail.conversation_id,
            tenant_id=detail.tenant_id,
            requester=detail.requester,
        )
    rich = _bound_stack(detail, selection_bound_hardware=True)
    catalog.persist_conversation_detail(rich)
    with sqlite3.connect(catalog._repository._database) as db:
        original = db.execute(
            "SELECT body FROM conversations WHERE conversation_id=?",
            (detail.conversation_id,),
        ).fetchone()[0]
    if mode == "replace":
        result, _, _ = prepare(catalog, rich, "rich-replacement")
    else:
        current = catalog.selection_revision_view(
            detail.project_id,
            detail.conversation_id,
            tenant_id=detail.tenant_id,
            requester=detail.requester,
        )
        result = decision(catalog, detail, current["head"], "REVOKED")
    reopened = SQLiteProjectCatalogRepository(catalog._repository._database)
    after = reopened.conversation(detail.conversation_id)
    for field in (
        "design_truth",
        "hardware_design_spec",
        "firmware_spec",
        "simulation_spec",
        "verification_spec",
        "implementation_spec",
        "build_configuration",
        "capability_evaluation",
        "execution_authorization",
        "engineering_knowledge_model",
        "teaching_context",
        "schematic_planning",
    ):
        assert getattr(after, field) is None, field
    assert (
        after.execution_intents == ()
        and after.turns == rich.turns
        and after.engineering_workflow == rich.engineering_workflow
    )
    with sqlite3.connect(reopened._database) as db:
        if mode == "replace":
            archived = db.execute(
                "SELECT predecessor_conversation_body FROM hardware_selection_revisions WHERE revision_id=?",
                (result["head"]["revision_id"],),
            ).fetchone()[0]
        else:
            archived = db.execute(
                "SELECT predecessor_conversation_body FROM hardware_selection_revision_decisions WHERE fingerprint=?",
                (result["head"]["decision_fingerprint"],),
            ).fetchone()[0]
    assert archived == original
