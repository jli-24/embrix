from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.project_catalog.models import (
    ProjectSupplyCandidateV2,
    ProjectSupplyRelationV2,
    ProjectSupplyReviewV2,
    ProjectSupplyStateV2,
)

ZERO = "sha256:" + "0" * 64
NOW = datetime(2026, 10, 8, tzinfo=UTC)


def _relation(**changes):
    values = {"power_domain_id": "domain-3v3", "source_component_refs": ("component-j1",),
                  "consumer_component_refs": ("component-mcu",), "nominal_voltage_v": "3.3",
                  "supply_net_ref": "net-3v3", "ground_net_ref": "net-gnd",
                  "evidence_refs": ("evidence-j1", "evidence-mcu"), "fingerprint": ZERO}
    values.update(changes)
    return ProjectSupplyRelationV2(**values)


def test_p1_contract_fingerprint_and_rejects_invalid_relations():
    relation = _relation()
    assert relation.fingerprint.startswith("sha256:") and relation.fingerprint != ZERO
    assert _relation(nominal_voltage_v="3.30").nominal_voltage_v == "3.3"
    for bad in ("0", "-3.3", "NaN", "Infinity", "3.3 V"):
        with pytest.raises(ValidationError):
            _relation(nominal_voltage_v=bad)
    with pytest.raises(ValidationError):
        _relation(consumer_component_refs=("component-j1",))
    with pytest.raises(ValidationError):
        _relation(supply_net_ref="net-gnd")
    with pytest.raises(ValidationError):
        _relation(extra="forbidden")


def test_p1_candidate_review_state_schema_is_exact():
    relation = _relation()
    candidate = ProjectSupplyCandidateV2(schema_version=2, candidate_id="candidate-1",
        tenant_id="tenant-1", project_id="project-1", conversation_id="conversation-1",
        design_truth_id="truth-1", design_truth_fingerprint=ZERO,
        selection_id="selection-1", selection_fingerprint=ZERO,
        circuit_id="circuit-1", circuit_fingerprint=ZERO,
        plan_id="plan-1", plan_fingerprint=ZERO,
        relations=(relation,), created_at=NOW)
    review = ProjectSupplyReviewV2(schema_version=2, review_id="review-1",
        tenant_id="tenant-1", project_id="project-1", conversation_id="conversation-1",
        candidate_id=candidate.candidate_id, candidate_fingerprint=candidate.fingerprint,
        decision="APPROVED", reviewer_id="engineer-1", decided_at=NOW)
    assert ProjectSupplyStateV2(schema_version=2, candidate=candidate, review=review).review == review
    with pytest.raises(ValidationError):
        ProjectSupplyStateV2(schema_version=3, candidate=candidate, review=review)
    with pytest.raises(ValidationError):
        ProjectSupplyReviewV2.model_validate({**review.model_dump(), "owner": "spoofed"})


def _approved_runtime(tmp_path, monkeypatch, *, full_s02=False):
    from embedded_copilot.engineering_copilot import (
        ArchitectureDesign,
        ArchitectureDesignStatus,
        ArchitectureInterface,
        ArchitectureInterfaceType,
        DesignTruthBuilder,
        HardwareSelectionDecision,
        HardwareSelectionService,
        approve_architecture_design,
        approve_engineering_plan,
        generate_architecture_design,
        generate_engineering_workflow,
    )
    from embedded_copilot.engineering_copilot.schematic_planning import (
        SchematicPlanningService,
    )
    from embedded_copilot.engineering_requirements import (
        generate_engineering_plan,
        merge_requirement_context,
    )
    from embedded_copilot.engineering_requirements.models import (
        RequirementContext,
        RequirementItemStatus,
        RequirementProvenance,
        StandardRequirementField,
    )
    from embedded_copilot.engineering_runtime import (
        CircuitIntentObject,
        LifecycleStatus,
        Provenance,
    )
    from embedded_copilot.project_catalog.models import (
        ConversationDetail,
        ConversationTurn,
        ConversationTurnStatus,
    )
    from tests.engineering_copilot.test_schematic_planning_g2 import (
        _catalog as component_catalog,
    )
    from tests.engineering_copilot.test_schematic_planning_g2 import (
        _golden_architecture,
    )
    from tests.project_catalog.test_s05_model_candidate_runtime import _runtime

    source = component_catalog()
    catalog, conversation = _runtime(tmp_path, source)
    requirement, _architecture, _review = _golden_architecture(conversation.project_id, conversation.conversation_id)
    requirement = merge_requirement_context(requirement, project_id=conversation.project_id,
        conversation_id=conversation.conversation_id, turn_id="turn-schematic",
        utterance="需要原理图", confirmation_requested=True)
    extra_fields = (
        ("S02_TEMPERATURE_MIN_C", "20"), ("S02_TEMPERATURE_MAX_C", "30"),
        ("S02_INTERFACE_MODE", "I2C_STANDARD"),
    ) if full_s02 else ()
    fields = tuple(StandardRequirementField(
        category=category, value=value, status=RequirementItemStatus.CONFIRMED,
        source_turn_id="turn-design", provenance=RequirementProvenance.USER_UTTERANCE,
    ) for category, value in (("S02_VOLTAGE_MIN_V", "3.0"), ("S02_VOLTAGE_MAX_V", "3.6"), *extra_fields))
    requirement = RequirementContext.model_validate({**requirement.model_dump(mode="python"),
        "standard_fields": fields, "fingerprint": ZERO})
    plan, plan_review = approve_engineering_plan(generate_engineering_plan(requirement),
        reviewer=conversation.requester, decided_at=NOW)
    draft = generate_architecture_design(requirement, plan)
    protocols = {draft.interfaces[0].source_component_id: "I2C",
                 draft.interfaces[1].source_component_id: "POWER",
                 draft.interfaces[2].source_component_id: "GPIO"}
    interfaces = []
    for item in draft.interfaces:
        values = item.model_dump(mode="python")
        if item.interface_type is ArchitectureInterfaceType.HARDWARE:
            values["protocol"] = protocols[item.source_component_id]
        values["fingerprint"] = ZERO
        interfaces.append(ArchitectureInterface.create(**values))
    values = draft.model_dump(mode="python")
    values.update(interfaces=tuple(interfaces), status=ArchitectureDesignStatus.PROPOSED, fingerprint=ZERO)
    architecture, architecture_review = approve_architecture_design(ArchitectureDesign.create(**values),
        reviewer=conversation.requester, decided_at=NOW)
    workflow = generate_engineering_workflow(architecture, plan)
    service = HardwareSelectionService(source)
    selection = service.decide(service.create_proposal(architecture, requirement, workflow=workflow,
        conversation_id=conversation.conversation_id), HardwareSelectionDecision.APPROVED,
        reviewer=conversation.requester, decided_at=NOW)
    truth = service.project_design_truth(DesignTruthBuilder().build_from_approved_architecture(
        architecture, tenant_id=conversation.tenant_id, conversation_id=conversation.conversation_id,
        architecture_review=architecture_review), selection.hardware_truth,
        architecture_review=architecture_review)
    turn = ConversationTurn(turn_id="turn-design", request_id="request-design", status=ConversationTurnStatus.COMPLETED,
        question="3.0 to 3.6 V", answer_text="Confirmed range", created_at=NOW, completed_at=NOW)
    detail = ConversationDetail.model_validate({**conversation.model_dump(mode="python"),
        "turns": (turn,), "requirement_context": requirement,
        "engineering_plan": plan, "engineering_plan_review": plan_review,
        "architecture_design": architecture, "architecture_review": architecture_review,
        "engineering_workflow": workflow, "hardware_selection": selection,
        "design_truth": truth, "fingerprint": ZERO})
    catalog.persist_conversation_detail(detail)
    from embedded_copilot.project_catalog.source_publication import (
        ComponentSourcePublishCommandV1, PublishedComponentSourcePort,
        TrustedComponentSourcePublisher,
    )
    publisher = TrustedComponentSourcePublisher(
        catalog._repository, tenant_id=detail.tenant_id,
        project_id=detail.project_id, publisher_id="p1-fixture-publisher",
    )
    for number, record in enumerate(source.records, start=1):
        publisher.publish(ComponentSourcePublishCommandV1(
            schema_version=1, request_id=f"fixture-source-{number}",
            source_id=record.source_id,
            expected_head_publication_fingerprint=None, record=record,
        ))
    catalog.set_hardware_component_source(PublishedComponentSourcePort(
        catalog._repository, tenant_id=detail.tenant_id,
        publisher_id="p1-fixture-publisher",
    ))
    circuit = CircuitIntentObject.create(object_id="circuit-supply", project_id=detail.project_id,
        status=LifecycleStatus.APPROVED, function="Power rail", topology=("3V3 to consumers",),
        provenance=Provenance.create(source="USER_INPUT", source_ref=f"{detail.project_id}:conversation",
            created_by=detail.requester, created_at=NOW))
    monkeypatch.setattr(catalog, "current_circuit_intents", lambda *args, **kwargs: (circuit,))
    monkeypatch.setattr(catalog, "circuit_intent_revision", lambda *args, **kwargs: circuit)
    monkeypatch.setattr(catalog, "circuit_source_current", lambda *args, **kwargs: True)
    monkeypatch.setattr(catalog, "require_current_circuit_binding", lambda *args, **kwargs: None)
    proposal = SchematicPlanningService().create_proposal(architecture, requirement,
        selection.hardware_truth, selection.bom, design_truth=truth,
        conversation_id=detail.conversation_id, circuit_intent=circuit, selection=selection)
    domain = next(item for item in proposal.power_domains if item.name != "GND")
    supply_net = next(item for item in proposal.nets if item.name == domain.name)
    ground_net = next(item for item in proposal.nets if item.name == "GND")
    component_ids = domain.source_component_ids + domain.consumer_component_ids
    refs = tuple(next(item.binding_id for item in truth.evidence_bindings
        if item.entity_id == component_id and item.source.value == "DATASHEET") for component_id in component_ids)
    relation = {"power_domain_id": domain.domain_id,
        "source_component_refs": domain.source_component_ids,
        "consumer_component_refs": domain.consumer_component_ids,
        "nominal_voltage_v": "3.3", "supply_net_ref": supply_net.net_id,
        "ground_net_ref": ground_net.net_id, "evidence_refs": refs}
    selectors = {"design_truth_id": truth.truth_id, "design_truth_fingerprint": truth.fingerprint,
        "selection_id": selection.proposal.proposal_id, "selection_fingerprint": selection.proposal.fingerprint,
        "circuit_id": circuit.object_id, "circuit_fingerprint": circuit.fingerprint,
        "plan_id": plan.plan_id, "plan_fingerprint": plan.fingerprint}
    return catalog, detail, relation, selectors, circuit, source


def test_p1_candidate_review_current_s05_and_restart(tmp_path, monkeypatch):
    from embedded_copilot.project_catalog.runtime import ProjectCatalogRuntime
    from tests.project_catalog.test_s05_model_candidate_runtime import _source

    catalog, detail, relation, selectors, circuit, source = _approved_runtime(tmp_path, monkeypatch)
    state = catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        selectors=selectors, relations=(relation,))
    assert state.review is None
    state = catalog.review_project_supply_candidate(detail.project_id, detail.conversation_id,
        state.candidate.candidate_id, state.candidate.fingerprint, "APPROVED",
        tenant_id=detail.tenant_id, requester=detail.requester)
    assert catalog.project_supply(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)["status"] == "APPROVED"
    authority_source = _source(catalog, detail, "SCHEMATIC_DESIGN_PROPOSAL",
        "phase-pcb-design", "phase-pcb-design-task-1")
    authority, _stage = authority_source.resolve_current()
    context = catalog.model_operation_candidate_context(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        operation_id="SCHEMATIC_DESIGN_PROPOSAL", authority_source=authority_source,
        authority_reference=authority)
    assert context["project_supply_candidate_fingerprint"] == state.candidate.fingerprint
    planned, _review_items = catalog.create_schematic_model_candidate_proposal(
        detail.project_id, detail.conversation_id, tenant_id=detail.tenant_id,
        requester=detail.requester, review_items=(), authority_source=authority_source,
        authority_reference=authority, expected_project_supply_fingerprints=(
            state.candidate.fingerprint, state.review.fingerprint))
    assert planned.schematic_planning is not None
    reopened = ProjectCatalogRuntime(project_root=tmp_path / "projects",
        state_database=tmp_path / "catalog.sqlite", initialize_async=False,
        hardware_component_source=source, clock=type("Clock", (), {"now": lambda self: NOW})())
    from embedded_copilot.project_catalog.source_publication import PublishedComponentSourcePort
    reopened.set_hardware_component_source(PublishedComponentSourcePort(
        reopened._repository, tenant_id=detail.tenant_id, publisher_id="p1-fixture-publisher",
    ))
    monkeypatch.setattr(reopened, "current_circuit_intents", lambda *args, **kwargs: (circuit,))
    monkeypatch.setattr(reopened, "require_current_circuit_binding", lambda *args, **kwargs: None)
    assert reopened.project_supply(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)["status"] == "APPROVED"


@pytest.mark.parametrize("part,field,value", (
    ("relation", "nominal_voltage_v", "3.7"),
    ("relation", "nominal_voltage_v", "0"),
    ("relation", "power_domain_id", "wrong-domain"),
    ("relation", "supply_net_ref", "wrong-net"),
    ("relation", "ground_net_ref", "wrong-ground"),
    ("relation", "evidence_refs", ("unknown-evidence",)),
    ("relation", "source_component_refs", ("foreign-component",)),
    ("selector", "design_truth_fingerprint", ZERO),
    ("selector", "selection_id", "foreign-selection"),
    ("selector", "plan_id", "foreign-plan"),
    ("selector", "circuit_id", "foreign-circuit"),
))
def test_p1_invalid_or_stale_candidate_never_persists(tmp_path, monkeypatch, part, field, value):
    from embedded_copilot.engineering_copilot.schematic_planning import (
        SchematicPlanningBlocked,
    )
    from embedded_copilot.project_catalog import ProjectCatalogError

    catalog, detail, relation, selectors, _circuit, _source = _approved_runtime(tmp_path, monkeypatch)
    relation, selectors = dict(relation), dict(selectors)
    (relation if part == "relation" else selectors)[field] = value
    with pytest.raises((ProjectCatalogError, SchematicPlanningBlocked, ValidationError, ValueError)):
        catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
            tenant_id=detail.tenant_id, requester=detail.requester,
            selectors=selectors, relations=(relation,))
    assert catalog.conversation(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester).project_supply is None


def test_p1_wrong_owner_and_review_fingerprint_are_rejected(tmp_path, monkeypatch):
    from embedded_copilot.project_catalog import (
        ProjectCatalogConflict,
        ProjectCatalogForbidden,
    )

    catalog, detail, relation, selectors, _circuit, _source = _approved_runtime(tmp_path, monkeypatch)
    with pytest.raises(ProjectCatalogForbidden):
        catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
            tenant_id=detail.tenant_id, requester="other-user", selectors=selectors, relations=(relation,))
    state = catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester, selectors=selectors, relations=(relation,))
    with pytest.raises(ProjectCatalogConflict):
        catalog.review_project_supply_candidate(detail.project_id, detail.conversation_id,
            state.candidate.candidate_id, ZERO, "APPROVED",
            tenant_id=detail.tenant_id, requester=detail.requester)
    assert catalog.project_supply(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)["status"] == "REVIEW"


def test_p1_api_strict_body_and_review_currentness(tmp_path, monkeypatch):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from embedded_copilot.api.project_catalog_routes import router

    catalog, detail, relation, selectors, _circuit, _source = _approved_runtime(tmp_path, monkeypatch)
    app = FastAPI()
    app.state.project_catalog_runtime = catalog
    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-1"
        return await call_next(request)
    app.include_router(router)
    root = f"/api/v1/copilot/projects/{detail.project_id}/conversations/{detail.conversation_id}/project-supply"
    headers = {"X-Copilot-Requester": detail.requester, "X-Environment-Tenant": detail.tenant_id}
    body = {**selectors, "relations": [relation]}
    with TestClient(app) as client:
        initial = client.get(root, headers=headers).json()
        assert initial["status"] == "UNAVAILABLE"
        assert initial["current_options"] == [{
            "power_domain_id": relation["power_domain_id"], "domain_name": "3V3",
            "supply_net_ref": relation["supply_net_ref"],
            "ground_net_ref": relation["ground_net_ref"],
        }]
        assert catalog.conversation(detail.project_id, detail.conversation_id,
            tenant_id=detail.tenant_id, requester=detail.requester).project_supply is None
        for extra in ({"owner": "spoofed"}, {"schema_version": 3}, {"provider": "spoofed"}):
            response = client.post(root, json={**body, **extra}, headers=headers)
            assert response.status_code == 422
        response = client.post(root, json=body, headers=headers)
        assert response.status_code == 200, response.text
        candidate = response.json()["candidate"]
        assert client.get(root, headers=headers).json()["status"] == "REVIEW"
        wrong = client.post(f"{root}/{candidate['candidate_id']}/review",
            json={"candidate_fingerprint": ZERO, "decision": "APPROVED"}, headers=headers)
        assert wrong.status_code == 409
        review = client.post(f"{root}/{candidate['candidate_id']}/review",
            json={"candidate_fingerprint": candidate["fingerprint"], "decision": "APPROVED"}, headers=headers)
        assert review.status_code == 200, review.text
        assert client.get(root, headers=headers).json()["status"] == "APPROVED"


def test_p1_old_conversation_json_and_fingerprint_fixed_vector():
    import hashlib

    from embedded_copilot.project_catalog.models import ConversationDetail

    detail = ConversationDetail(conversation_id="conversation-vector", project_id="project-vector",
        tenant_id="tenant-vector", requester="engineer-vector", title="Legacy vector",
        turns=(), created_at=datetime(2026, 1, 2, tzinfo=UTC),
        updated_at=datetime(2026, 1, 2, tzinfo=UTC))
    encoded = detail.model_dump_json().encode("utf-8")
    assert detail.fingerprint == "sha256:e05f94c0b625658b0a288acf11d22c2b202671399dc372deaccaebb1bf6ae2b7"
    assert hashlib.sha256(encoded).hexdigest() == "03a1b346299b66f474bbfdc07a35215b013a20348a4c1b42ff3303041d1c65e7"
    assert b"project_supply" not in encoded
    assert ConversationDetail.model_validate_json(encoded).model_dump_json().encode("utf-8") == encoded


def test_p1_review_and_s05_fail_closed_after_circuit_drift(tmp_path, monkeypatch):
    from embedded_copilot.engineering_copilot.schematic_planning import (
        SchematicPlanningBlocked,
    )
    from embedded_copilot.project_catalog import ProjectCatalogConflict

    catalog, detail, relation, selectors, _circuit, _source_port = _approved_runtime(tmp_path, monkeypatch)
    state = catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester, selectors=selectors, relations=(relation,))
    monkeypatch.setattr(catalog, "current_circuit_intents", lambda *args, **kwargs: ())
    with pytest.raises(ProjectCatalogConflict):
        catalog.review_project_supply_candidate(detail.project_id, detail.conversation_id,
            state.candidate.candidate_id, state.candidate.fingerprint, "APPROVED",
            tenant_id=detail.tenant_id, requester=detail.requester)
    assert catalog.project_supply(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)["status"] == "STALE"
    class Prevalidated:
        def revalidate(self, value):
            return value
    with pytest.raises(SchematicPlanningBlocked, match="POWER_SOURCE_UNRESOLVED"):
        catalog.model_operation_candidate_context(detail.project_id, detail.conversation_id,
            tenant_id=detail.tenant_id, requester=detail.requester,
            operation_id="SCHEMATIC_DESIGN_PROPOSAL", authority_source=Prevalidated(),
            authority_reference=object())


def test_p1_deterministic_schematic_entry_requires_approved_supply(tmp_path, monkeypatch):
    from embedded_copilot.engineering_copilot.schematic_planning import (
        SchematicPlanningBlocked,
    )

    catalog, detail, _relation, _selectors, circuit, _source = _approved_runtime(tmp_path, monkeypatch)
    with pytest.raises(SchematicPlanningBlocked, match="POWER_SOURCE_UNRESOLVED"):
        catalog.create_schematic_plan(detail.project_id, detail.conversation_id,
            tenant_id=detail.tenant_id, requester=detail.requester,
            circuit_intent_selector=(circuit.object_id, circuit.revision, circuit.fingerprint))
    assert catalog.conversation(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester).schematic_planning is None
    state = catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        selectors=_selectors, relations=(_relation,))
    catalog.review_project_supply_candidate(detail.project_id, detail.conversation_id,
        state.candidate.candidate_id, state.candidate.fingerprint, "APPROVED",
        tenant_id=detail.tenant_id, requester=detail.requester)
    planned = catalog.create_schematic_plan(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        circuit_intent_selector=(circuit.object_id, circuit.revision, circuit.fingerprint))
    assert planned.schematic_planning is not None
    assert catalog.schematic_plan_currentness(detail.project_id, detail.conversation_id,
        planned.schematic_planning.proposal.plan_id, planned.schematic_planning.proposal.fingerprint,
        tenant_id=detail.tenant_id, requester=detail.requester)[2] == "CURRENT"


def test_p1_model_proposal_remains_current_for_human_plan_review(tmp_path, monkeypatch):
    from tests.project_catalog.test_s05_model_candidate_runtime import _source

    catalog, detail, relation, selectors, _circuit, _source_port = _approved_runtime(tmp_path, monkeypatch)
    state = catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        selectors=selectors, relations=(relation,))
    state = catalog.review_project_supply_candidate(detail.project_id, detail.conversation_id,
        state.candidate.candidate_id, state.candidate.fingerprint, "APPROVED",
        tenant_id=detail.tenant_id, requester=detail.requester)
    source = _source(catalog, detail, "SCHEMATIC_DESIGN_PROPOSAL",
        "phase-pcb-design", "phase-pcb-design-task-1")
    authority, _stage = source.resolve_current()
    planned, _review_items = catalog.create_schematic_model_candidate_proposal(
        detail.project_id, detail.conversation_id, tenant_id=detail.tenant_id,
        requester=detail.requester, review_items=(), authority_source=source,
        authority_reference=authority, expected_project_supply_fingerprints=(
            state.candidate.fingerprint, state.review.fingerprint))
    proposal = planned.schematic_planning.proposal
    assert catalog.schematic_plan_currentness(detail.project_id, detail.conversation_id,
        proposal.plan_id, proposal.fingerprint, tenant_id=detail.tenant_id,
        requester=detail.requester)[2] == "CURRENT"
    from embedded_copilot.engineering_copilot.schematic_planning import (
        SchematicPlanDecision,
    )
    approved = catalog.decide_schematic_plan(detail.project_id, detail.conversation_id,
        SchematicPlanDecision.APPROVED, tenant_id=detail.tenant_id,
        requester=detail.requester, decided_at=NOW)
    assert approved.schematic_planning.approved_plan is not None
    assert catalog.schematic_plan_currentness(detail.project_id, detail.conversation_id,
        approved.schematic_planning.approved_plan.plan_id,
        approved.schematic_planning.approved_plan.fingerprint,
        tenant_id=detail.tenant_id, requester=detail.requester)[2] == "CURRENT"


def test_p1_approved_plan_authority_drift_is_stale(tmp_path, monkeypatch):
    from decimal import Decimal

    from embedded_copilot.engineering_copilot import HardwareSelectionService
    from embedded_copilot.engineering_copilot.schematic_planning import (
        SchematicPlanApproval,
        SchematicPlanDecision,
        SchematicPlanningState,
    )
    from embedded_copilot.project_catalog.models import ConversationDetail

    catalog, detail, relation, selectors, _circuit, _source = _approved_runtime(tmp_path, monkeypatch)
    state = catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        selectors=selectors, relations=(relation,))
    catalog.review_project_supply_candidate(detail.project_id, detail.conversation_id,
        state.candidate.candidate_id, state.candidate.fingerprint, "APPROVED",
        tenant_id=detail.tenant_id, requester=detail.requester)
    catalog.create_schematic_plan(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)
    approved = catalog.decide_schematic_plan(detail.project_id, detail.conversation_id,
        SchematicPlanDecision.APPROVED, tenant_id=detail.tenant_id,
        requester=detail.requester, decided_at=NOW)
    plan = approved.schematic_planning.approved_plan
    def currentness():
        return catalog.schematic_plan_currentness(detail.project_id, detail.conversation_id,
            plan.plan_id, plan.fingerprint, tenant_id=detail.tenant_id,
            requester=detail.requester)[2]
    def persist(**changes):
        latest = catalog.conversation(detail.project_id, detail.conversation_id,
            tenant_id=detail.tenant_id, requester=detail.requester)
        updated = latest.model_copy(update={**changes, "fingerprint": ZERO})
        catalog.persist_conversation_detail(ConversationDetail.model_validate(updated.model_dump(mode="python")))
    assert currentness() == "CURRENT"
    # A valid, rebound review record cannot authorize the original approval.
    revoked = ProjectSupplyReviewV2(**{**approved.project_supply.review.model_dump(mode="python"),
        "decision": "REJECTED", "fingerprint": ZERO})
    persist(project_supply=type(approved.project_supply)(schema_version=approved.project_supply.schema_version,
        candidate=approved.project_supply.candidate, review=revoked))
    assert currentness() == "STALE"
    persist(project_supply=approved.project_supply)
    assert currentness() == "CURRENT"
    changed_relation = ProjectSupplyRelationV2(**{**approved.project_supply.candidate.relations[0].model_dump(mode="python"),
        "nominal_voltage_v": "3.4", "fingerprint": ZERO})
    changed_candidate = type(approved.project_supply.candidate)(**{**approved.project_supply.candidate.model_dump(mode="python"),
        "relations": (changed_relation,), "fingerprint": ZERO})
    changed_review = ProjectSupplyReviewV2(**{**approved.project_supply.review.model_dump(mode="python"),
        "candidate_fingerprint": changed_candidate.fingerprint, "fingerprint": ZERO})
    persist(project_supply=type(approved.project_supply)(schema_version=approved.project_supply.schema_version,
        candidate=changed_candidate, review=changed_review))
    assert currentness() == "STALE"
    persist(project_supply=approved.project_supply)
    assert currentness() == "CURRENT"
    # The approved nominal remains independent of the current reviewed range.
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "_project_supply_range",
            lambda _: (Decimal("3.4"), Decimal("3.6")))
        assert currentness() == "STALE"
    assert currentness() == "CURRENT"
    with monkeypatch.context() as patch:
        patch.setattr(HardwareSelectionService, "selection_sources_current", lambda self, value: False)
        assert currentness() == "STALE"
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "current_circuit_intents", lambda *args, **kwargs: ())
        assert currentness() == "STALE"
    original_approval = approved.schematic_planning.approval
    altered = SchematicPlanApproval.create(**{**original_approval.model_dump(mode="python"),
        "approval_id": "foreign-approval", "fingerprint": ZERO})
    persist(schematic_planning=SchematicPlanningState(
        proposal=approved.schematic_planning.proposal,
        status=approved.schematic_planning.status, approval=altered, approved_plan=plan))
    assert currentness() == "STALE"
    persist(schematic_planning=approved.schematic_planning)
    assert currentness() == "CURRENT"
    with pytest.raises(ValidationError, match="schematic DesignTruth binding mismatch"):
        persist(design_truth=detail.design_truth, engineering_knowledge_model=None)


def test_p1_new_legal_relation_can_be_reviewed_without_reviving_old_plan(tmp_path, monkeypatch):
    from embedded_copilot.engineering_copilot.schematic_planning import (
        SchematicPlanDecision,
    )

    catalog, detail, relation, selectors, _circuit, _source = _approved_runtime(tmp_path, monkeypatch)
    state = catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        selectors=selectors, relations=(relation,))
    catalog.review_project_supply_candidate(detail.project_id, detail.conversation_id,
        state.candidate.candidate_id, state.candidate.fingerprint, "APPROVED",
        tenant_id=detail.tenant_id, requester=detail.requester)
    catalog.create_schematic_plan(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)
    approved = catalog.decide_schematic_plan(detail.project_id, detail.conversation_id,
        SchematicPlanDecision.APPROVED, tenant_id=detail.tenant_id,
        requester=detail.requester, decided_at=NOW)
    old_plan = approved.schematic_planning.approved_plan
    changed = {**relation, "evidence_refs": tuple(reversed(relation["evidence_refs"]))}
    assert changed["evidence_refs"] != relation["evidence_refs"]
    replacement = catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        selectors=selectors, relations=(changed,))
    assert replacement.candidate.fingerprint != state.candidate.fingerprint
    assert catalog.schematic_plan_currentness(detail.project_id, detail.conversation_id,
        old_plan.plan_id, old_plan.fingerprint, tenant_id=detail.tenant_id,
        requester=detail.requester)[2] == "STALE"
    catalog.review_project_supply_candidate(detail.project_id, detail.conversation_id,
        replacement.candidate.candidate_id, replacement.candidate.fingerprint, "APPROVED",
        tenant_id=detail.tenant_id, requester=detail.requester)
    assert catalog.project_supply(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)["status"] == "APPROVED"
    assert catalog.schematic_plan_currentness(detail.project_id, detail.conversation_id,
        old_plan.plan_id, old_plan.fingerprint, tenant_id=detail.tenant_id,
        requester=detail.requester)[2] == "STALE"


@pytest.mark.parametrize("mutation", ("missing", "reordered", "foreign"))
def test_p1_formal_plan_rejects_nonexact_generation_evidence(tmp_path, monkeypatch, mutation):
    from embedded_copilot.project_catalog.exceptions import SchematicPlanningRejected
    from embedded_copilot.engineering_copilot.schematic_planning import (
        SchematicPlanDecision, SchematicPlanProposal,
        SchematicPlanningState,
    )
    from embedded_copilot.project_catalog.models import ConversationDetail

    catalog, detail, relation, selectors, _circuit, _source = _approved_runtime(tmp_path, monkeypatch)
    state = catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        selectors=selectors, relations=(relation,))
    state = catalog.review_project_supply_candidate(detail.project_id, detail.conversation_id,
        state.candidate.candidate_id, state.candidate.fingerprint, "APPROVED",
        tenant_id=detail.tenant_id, requester=detail.requester)
    planned = catalog.create_schematic_plan(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)
    proposal = planned.schematic_planning.proposal
    binding = tuple(item.fingerprint for item in state.candidate.relations) + (
        state.candidate.fingerprint, state.review.fingerprint)
    assert proposal.evidence_fingerprints[-len(binding):] == binding
    fingerprints = proposal.evidence_fingerprints
    if mutation == "missing":
        fingerprints = fingerprints[:-len(binding)]
    elif mutation == "reordered":
        fingerprints = fingerprints[:-len(binding)] + tuple(reversed(binding))
    else:
        fingerprints = fingerprints[:-1] + ("sha256:" + "a" * 64,)
    legacy = SchematicPlanProposal.create(**{
        **{name: getattr(proposal, name) for name in type(proposal).model_fields},
        "evidence_fingerprints": fingerprints, "fingerprint": ZERO})
    legacy_json = legacy.model_dump_json()
    altered = ConversationDetail.model_validate({**planned.model_dump(mode="python"),
        "schematic_planning": SchematicPlanningState(proposal=legacy, status=legacy.status),
        "fingerprint": ZERO})
    catalog.persist_conversation_detail(altered)
    assert catalog.schematic_plan_currentness(detail.project_id, detail.conversation_id,
        legacy.plan_id, legacy.fingerprint, tenant_id=detail.tenant_id,
        requester=detail.requester)[2] == "STALE"
    with pytest.raises(SchematicPlanningRejected, match="source is not current"):
        catalog.decide_schematic_plan(detail.project_id, detail.conversation_id,
            SchematicPlanDecision.APPROVED, tenant_id=detail.tenant_id,
            requester=detail.requester, decided_at=NOW)
    persisted = catalog.conversation(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester).schematic_planning.proposal
    assert persisted.plan_id == legacy.plan_id and persisted.fingerprint == legacy.fingerprint
    assert persisted.model_dump_json() == legacy_json


def test_p1_same_relations_new_candidate_never_revives_approved_plan(tmp_path, monkeypatch):
    from embedded_copilot.engineering_copilot.schematic_planning import SchematicPlanDecision

    catalog, detail, relation, selectors, _circuit, _source = _approved_runtime(tmp_path, monkeypatch)
    state = catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester, selectors=selectors, relations=(relation,))
    state = catalog.review_project_supply_candidate(detail.project_id, detail.conversation_id,
        state.candidate.candidate_id, state.candidate.fingerprint, "APPROVED",
        tenant_id=detail.tenant_id, requester=detail.requester)
    proposed = catalog.create_schematic_plan(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)
    approved = catalog.decide_schematic_plan(detail.project_id, detail.conversation_id,
        SchematicPlanDecision.APPROVED, tenant_id=detail.tenant_id,
        requester=detail.requester, decided_at=NOW)
    planning = approved.schematic_planning
    assert proposed.schematic_planning.proposal.evidence_fingerprints == planning.approval.evidence_fingerprints
    assert planning.approved_plan.evidence_fingerprints == planning.approval.evidence_fingerprints
    prior_json = planning.model_dump_json()
    replacement = catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester, selectors=selectors, relations=(relation,))
    assert replacement.candidate.relations == state.candidate.relations
    catalog.review_project_supply_candidate(detail.project_id, detail.conversation_id,
        replacement.candidate.candidate_id, replacement.candidate.fingerprint, "APPROVED",
        tenant_id=detail.tenant_id, requester=detail.requester)
    assert catalog.schematic_plan_currentness(detail.project_id, detail.conversation_id,
        planning.approved_plan.plan_id, planning.approved_plan.fingerprint,
        tenant_id=detail.tenant_id, requester=detail.requester)[2] == "STALE"
    assert catalog.conversation(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester).schematic_planning.model_dump_json() == prior_json


@pytest.mark.parametrize("first_decision", ("APPROVED", "REJECTED"))
def test_p1_same_candidate_review_generation_never_revives_old_plan(tmp_path, monkeypatch, first_decision):
    from embedded_copilot.engineering_copilot.schematic_planning import SchematicPlanDecision
    from embedded_copilot.project_catalog.models import ConversationDetail

    catalog, detail, relation, selectors, _circuit, _source = _approved_runtime(tmp_path, monkeypatch)
    state = catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester, selectors=selectors, relations=(relation,))
    state = catalog.review_project_supply_candidate(detail.project_id, detail.conversation_id,
        state.candidate.candidate_id, state.candidate.fingerprint, "APPROVED",
        tenant_id=detail.tenant_id, requester=detail.requester)
    catalog.create_schematic_plan(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)
    approved = catalog.decide_schematic_plan(detail.project_id, detail.conversation_id,
        SchematicPlanDecision.APPROVED, tenant_id=detail.tenant_id,
        requester=detail.requester, decided_at=NOW)
    plan = approved.schematic_planning.approved_plan
    prior_json = approved.schematic_planning.model_dump_json()
    for index, decision in enumerate((first_decision, "APPROVED")):
        review = ProjectSupplyReviewV2(**{**state.review.model_dump(mode="python"),
            "review_id": f"replacement-review-{index}", "decision": decision, "fingerprint": ZERO})
        replacement = type(state)(schema_version=state.schema_version, candidate=state.candidate, review=review)
        latest = catalog.conversation(detail.project_id, detail.conversation_id,
            tenant_id=detail.tenant_id, requester=detail.requester)
        catalog.persist_conversation_detail(ConversationDetail.model_validate({
            **latest.model_dump(mode="python"), "project_supply": replacement, "fingerprint": ZERO}))
        assert catalog.schematic_plan_currentness(detail.project_id, detail.conversation_id,
            plan.plan_id, plan.fingerprint, tenant_id=detail.tenant_id,
            requester=detail.requester)[2] == "STALE"
    assert catalog.conversation(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester).schematic_planning.model_dump_json() == prior_json


def test_p1_approved_plan_binding_survives_independent_python_restart(tmp_path, monkeypatch):
    import json
    import subprocess
    import sys
    from embedded_copilot.engineering_copilot.schematic_planning import SchematicPlanDecision

    catalog, detail, relation, selectors, circuit, _source = _approved_runtime(tmp_path, monkeypatch)
    # The preview and an unreviewed candidate do not create formal planning.
    catalog.project_supply(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)
    state = catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester, selectors=selectors, relations=(relation,))
    candidate_only = catalog.conversation(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)
    catalog.project_supply(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)
    assert candidate_only.schematic_planning is None
    assert catalog.conversation(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester).fingerprint == candidate_only.fingerprint
    catalog.review_project_supply_candidate(detail.project_id, detail.conversation_id,
        state.candidate.candidate_id, state.candidate.fingerprint, "APPROVED",
        tenant_id=detail.tenant_id, requester=detail.requester)
    catalog.create_schematic_plan(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)
    approved = catalog.decide_schematic_plan(detail.project_id, detail.conversation_id,
        SchematicPlanDecision.APPROVED, tenant_id=detail.tenant_id,
        requester=detail.requester, decided_at=NOW)
    plan = approved.schematic_planning.approved_plan
    bound_json = approved.schematic_planning.model_dump_json()
    payload = tmp_path / "restart-binding.json"
    payload.write_text(json.dumps({"project_root": str(tmp_path / "projects"),
        "state_database": str(tmp_path / "catalog.sqlite"),
        "project_id": detail.project_id, "conversation_id": detail.conversation_id,
        "tenant_id": detail.tenant_id, "requester": detail.requester,
        "plan_id": plan.plan_id, "plan_fingerprint": plan.fingerprint,
        "circuit": circuit.model_dump(mode="json")}), encoding="utf-8")
    script = """
import json, sys
from pathlib import Path
from tests.s02_offline_guard import pytest_configure, pytest_unconfigure
pytest_configure()
from embedded_copilot.project_catalog.runtime import ProjectCatalogRuntime
from embedded_copilot.engineering_runtime import CircuitIntentObject
from tests.engineering_copilot.test_schematic_planning_g2 import _catalog
args = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
from datetime import UTC, datetime
runtime = ProjectCatalogRuntime(project_root=Path(args['project_root']),
    state_database=Path(args['state_database']), initialize_async=False,
    hardware_component_source=_catalog(),
    clock=type('Clock', (), {'now': lambda self: datetime(2026,10,8,tzinfo=UTC)})())
from embedded_copilot.project_catalog.source_publication import PublishedComponentSourcePort
runtime.set_hardware_component_source(PublishedComponentSourcePort(runtime._repository,
    tenant_id=args['tenant_id'], publisher_id='p1-fixture-publisher'))
def immutable(value):
    if isinstance(value, list):
        return tuple(immutable(item) for item in value)
    if isinstance(value, dict):
        return {key: immutable(item) for key, item in value.items()}
    return value
circuit = CircuitIntentObject.model_validate(immutable(args['circuit']), strict=False)
runtime.current_circuit_intents = lambda *a, **k: (circuit,)
runtime.require_current_circuit_binding = lambda *a, **k: None
scope = {'tenant_id': args['tenant_id'], 'requester': args['requester']}
status = runtime.schematic_plan_currentness(args['project_id'], args['conversation_id'],
    args['plan_id'], args['plan_fingerprint'], **scope)[2]
planning = runtime.conversation(args['project_id'], args['conversation_id'], **scope).schematic_planning
print(json.dumps({'status': status, 'planning': planning.model_dump_json()}))
pytest_unconfigure()
"""
    child = subprocess.run([sys.executable, "-c", script, str(payload)],
        capture_output=True, text=True, encoding="utf-8", timeout=45)
    assert child.returncode == 0, child.stderr
    result = json.loads(child.stdout.splitlines()[-1])
    assert result == {"status": "CURRENT", "planning": bound_json}


def test_p1_formal_approved_plan_real_circuit_store_restart(tmp_path):
    import json
    import subprocess
    import sys

    from embedded_copilot.application.circuit_publication import (
        PredesignDecisionCommand,
    )
    from embedded_copilot.engineering_copilot.schematic_planning import (
        SchematicPlanDecision,
    )
    from tests.project_catalog.test_predesign_publication import _command, _service

    class NoPatch:
        def setattr(self, *args):
            pass

    catalog, detail, relation, selectors, _, _ = _approved_runtime(tmp_path, NoPatch())
    from embedded_copilot.project_catalog.models import ConversationDetail
    detail = ConversationDetail.model_validate({**detail.model_dump(mode="python"),
        "turns": (detail.turns[0].model_copy(update={"route":"GREENFIELD_DESIGN", "fingerprint":ZERO}),), "fingerprint": ZERO})
    catalog.persist_conversation_detail(detail)
    candidate = _service(catalog).preview(project_id=detail.project_id,
        tenant_id=detail.tenant_id, requester=detail.requester, command=_command(detail))
    publication = _service(catalog).decide(project_id=detail.project_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        command=PredesignDecisionCommand(purpose="PREDESIGN_APPROVAL",
            proposal_id=candidate.proposal.proposal_id, proposal_fingerprint=candidate.proposal.fingerprint,
            source_fingerprint=candidate.proposal.context_fingerprint, decision="APPROVE"))
    assert catalog.current_circuit_intents(detail.project_id, tenant_id=detail.tenant_id,
        requester=detail.requester) == (publication.circuit,)
    selectors["circuit_id"] = publication.circuit.object_id
    selectors["circuit_fingerprint"] = publication.circuit.fingerprint
    prior = catalog.conversation(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)
    catalog.project_supply(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)
    assert catalog.conversation(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester).fingerprint == prior.fingerprint
    state = catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester, selectors=selectors, relations=(relation,))
    assert catalog.conversation(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester).schematic_planning is None
    catalog.review_project_supply_candidate(detail.project_id, detail.conversation_id,
        state.candidate.candidate_id, state.candidate.fingerprint, "APPROVED",
        tenant_id=detail.tenant_id, requester=detail.requester)
    catalog.create_schematic_plan(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)
    approved = catalog.decide_schematic_plan(detail.project_id, detail.conversation_id,
        SchematicPlanDecision.APPROVED, tenant_id=detail.tenant_id,
        requester=detail.requester, decided_at=NOW)
    plan = approved.schematic_planning.approved_plan
    assert catalog.schematic_plan_currentness(detail.project_id, detail.conversation_id,
        plan.plan_id, plan.fingerprint, tenant_id=detail.tenant_id, requester=detail.requester)[2] == "CURRENT"
    script = """
import json, sys
from pathlib import Path
from datetime import UTC, datetime
from tests.s02_offline_guard import pytest_configure, pytest_unconfigure
pytest_configure()
from embedded_copilot.project_catalog import ProjectCatalogRuntime
from tests.engineering_copilot.test_schematic_planning_g2 import _catalog
root, project, conversation, tenant, owner, plan, fp = sys.argv[1:]
runtime = ProjectCatalogRuntime(project_root=Path(root)/'projects',
    state_database=Path(root)/'catalog.sqlite', initialize_async=False,
    hardware_component_source=_catalog(),
    clock=type('Clock', (), {'now':lambda self:datetime(2026,10,8,tzinfo=UTC)})())
from embedded_copilot.project_catalog.source_publication import PublishedComponentSourcePort
runtime.set_hardware_component_source(PublishedComponentSourcePort(runtime._repository,
    tenant_id=tenant, publisher_id='p1-fixture-publisher'))
scope={'tenant_id':tenant,'requester':owner}
result={'currentness':runtime.schematic_plan_currentness(project,conversation,plan,fp,**scope)[2],
    'circuits':len(runtime.current_circuit_intents(project,**scope)),
    'planning':runtime.conversation(project,conversation,**scope).schematic_planning.model_dump_json()}
print(json.dumps(result))
pytest_unconfigure()
"""
    child = subprocess.run([sys.executable, "-c", script, str(tmp_path), detail.project_id,
        detail.conversation_id, detail.tenant_id, detail.requester, plan.plan_id, plan.fingerprint],
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout.splitlines()[-1]) == {"currentness":"CURRENT", "circuits":1,
        "planning":approved.schematic_planning.model_dump_json()}

    replacement = catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester, selectors=selectors, relations=(relation,))
    assert replacement.candidate.candidate_id != state.candidate.candidate_id
    catalog.review_project_supply_candidate(detail.project_id, detail.conversation_id,
        replacement.candidate.candidate_id, replacement.candidate.fingerprint, "APPROVED",
        tenant_id=detail.tenant_id, requester=detail.requester)
    assert catalog.schematic_plan_currentness(detail.project_id, detail.conversation_id,
        plan.plan_id, plan.fingerprint, tenant_id=detail.tenant_id, requester=detail.requester)[2] == "STALE"
    assert catalog.conversation(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester).schematic_planning.model_dump_json() == approved.schematic_planning.model_dump_json()
    stale_child = subprocess.run([sys.executable, "-c", script, str(tmp_path), detail.project_id,
        detail.conversation_id, detail.tenant_id, detail.requester, plan.plan_id, plan.fingerprint],
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    assert stale_child.returncode == 0, stale_child.stderr
    assert json.loads(stale_child.stdout.splitlines()[-1]) == {"currentness":"STALE", "circuits":1,
        "planning":approved.schematic_planning.model_dump_json()}
