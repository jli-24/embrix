"""Offline Owner currentness checks for selection and schematic decisions."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from embedded_copilot.api.project_catalog_routes import router
from embedded_copilot.component_recommendation import (
    ComponentSourceRecord,
    StaticComponentSource,
)
from embedded_copilot.engineering_copilot import (
    HardwareSelectionDecision,
    HardwareSelectionStatus,
    SchematicPlanDecision,
    SchematicPlanningStatus,
    approve_engineering_plan,
    generate_engineering_workflow,
)
from embedded_copilot.engineering_copilot.schematic_planning import (
    SchematicPlanningBlocked,
)
from embedded_copilot.engineering_requirements import generate_engineering_plan
from embedded_copilot.project_catalog import (
    HardwareSelectionRejected,
    ProjectCatalogConflict,
    ProjectCatalogForbidden,
    ProjectCatalogNotFound,
    ProjectCatalogRuntime,
    ProjectCreationMode,
    ProjectCreationRequest,
    ProjectLocationReference,
    ProjectType,
    SchematicPlanningRejected,
)
from tests.engineering_copilot.test_hardware_selection_g1 import NOW
from tests.engineering_copilot.test_s02_supporting_fulfillment import (
    _catalog_with_support,
)
from tests.engineering_copilot.test_schematic_planning_g2 import (
    _catalog,
    _golden_architecture,
)
from tests.project_catalog.test_p1_project_supply import _approved_runtime

ZERO = "sha256:" + "0" * 64


class MutableSource:
    def __init__(self, *, with_support: bool = True) -> None:
        self.records = (_catalog_with_support() if with_support else _catalog()).records

    def search(self, project_id: str, architecture_id: str, requirement: object):
        return StaticComponentSource(self.records).search(project_id, architecture_id, requirement)

    def change_selected_source(self) -> None:
        values = self.records[0].model_dump(mode="python", exclude={"fingerprint"})
        values["description"] = "Current source content changed"
        changed = ComponentSourceRecord.create(**values)
        self.records = (changed, *self.records[1:])


def _reviewed_catalog(tmp_path, *, with_support: bool = True):
    source = MutableSource(with_support=with_support)
    (tmp_path / "projects").mkdir()
    catalog = ProjectCatalogRuntime(
        project_root=tmp_path / "projects", state_database=tmp_path / "desktop.sqlite3",
        clock=type("Clock", (), {"now": lambda self: NOW})(),
        initialize_async=False, hardware_component_source=source,
    )
    location = catalog.locations(tenant_id="tenant-1", requester="engineer-1")[0]
    preview = catalog.preview(
        ProjectCreationRequest(
            name="S02 Supporting Owner", project_type=ProjectType.STM32,
            creation_mode=ProjectCreationMode.CREATE_NEW,
            location=ProjectLocationReference(
                location_id=location.location_id, fingerprint=location.fingerprint,
            ),
        ),
        tenant_id="tenant-1", requester="engineer-1",
    )
    project = catalog.create(
        preview.preview_id, preview.fingerprint, confirmed_at=NOW,
        tenant_id="tenant-1", requester="engineer-1",
    )
    conversation = catalog.create_conversation(
        project.project_id, tenant_id="tenant-1", requester="engineer-1",
    ).conversation
    context, architecture, architecture_review = _golden_architecture(
        project.project_id, conversation.conversation_id,
    )
    plan_proposal = generate_engineering_plan(context)
    plan, plan_review = approve_engineering_plan(
        plan_proposal, reviewer="engineer-1", decided_at=NOW,
    )
    workflow = generate_engineering_workflow(architecture, plan)
    seeded = conversation.model_copy(update={
        "requirement_context": context,
        "engineering_plan": plan,
        "engineering_plan_review": plan_review,
        "architecture_design": architecture,
        "architecture_review": architecture_review,
        "engineering_workflow": workflow,
        "fingerprint": ZERO,
    })
    catalog.persist_conversation_detail(seeded)
    reviewed = catalog.create_hardware_selection_proposal(
        project.project_id, conversation.conversation_id,
        tenant_id="tenant-1", requester="engineer-1",
    )
    assert reviewed.hardware_selection is not None
    return catalog, source, project.project_id, conversation.conversation_id


def _reviewed_p1_plan(tmp_path, monkeypatch):
    catalog, detail, relation, selectors, circuit, original_source = _approved_runtime(tmp_path, monkeypatch)
    from types import SimpleNamespace
    from embedded_copilot.project_catalog.source_publication import (
        ComponentSourcePublishCommandV1, TrustedComponentSourcePublisher,
    )

    publisher = TrustedComponentSourcePublisher(catalog._repository,
        tenant_id=detail.tenant_id, project_id=detail.project_id,
        publisher_id="p1-fixture-publisher")

    def change_selected_source():
        selected = catalog.conversation(detail.project_id, detail.conversation_id,
            tenant_id=detail.tenant_id, requester=detail.requester).hardware_selection.proposal.selected_components[0]
        current = catalog._hardware_component_source.current_publication(detail.project_id, selected.source.source_id)
        values = current.record.model_dump(mode="python", exclude={"fingerprint"})
        values["description"] = "Current source content changed"
        changed = ComponentSourceRecord.create(**values)
        publisher.publish(ComponentSourcePublishCommandV1(schema_version=1,
            request_id="drift-selected-source", source_id=current.source_id,
            expected_head_publication_fingerprint=current.fingerprint, record=changed))

    source = SimpleNamespace(change_selected_source=change_selected_source)
    state = catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        selectors=selectors, relations=(relation,))
    catalog.review_project_supply_candidate(detail.project_id, detail.conversation_id,
        state.candidate.candidate_id, state.candidate.fingerprint, "APPROVED",
        tenant_id=detail.tenant_id, requester=detail.requester)
    reviewed = catalog.create_schematic_plan(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)
    return catalog, source, detail.project_id, detail.conversation_id, circuit, reviewed


def test_selection_decision_rejects_selected_source_drift_without_persisting(tmp_path) -> None:
    catalog, source, project_id, conversation_id = _reviewed_catalog(tmp_path)
    before = catalog.conversation(
        project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1",
    )
    source.change_selected_source()
    with pytest.raises(HardwareSelectionRejected):
        catalog.decide_hardware_selection(
            project_id, conversation_id, HardwareSelectionDecision.APPROVED,
            tenant_id="tenant-1", requester="engineer-1", decided_at=NOW,
        )
    after = catalog.conversation(
        project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1",
    )
    assert after.fingerprint == before.fingerprint
    assert after.hardware_selection is not None
    assert after.hardware_selection.status is HardwareSelectionStatus.REVIEW


def test_plan_decision_rejects_source_drift_and_preserves_review_history(tmp_path, monkeypatch) -> None:
    catalog, source, project_id, conversation_id, _circuit, reviewed = _reviewed_p1_plan(tmp_path, monkeypatch)
    assert reviewed.schematic_planning is not None
    assert reviewed.schematic_planning.status is SchematicPlanningStatus.REVIEW
    source.change_selected_source()
    with pytest.raises(SchematicPlanningRejected):
        catalog.decide_schematic_plan(
            project_id, conversation_id, SchematicPlanDecision.APPROVED,
            tenant_id="tenant-1", requester="engineer-1", decided_at=NOW,
        )
    after = catalog.conversation(
        project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1",
    )
    assert after.fingerprint == reviewed.fingerprint
    assert after.schematic_planning is not None
    assert after.schematic_planning.status is SchematicPlanningStatus.REVIEW


def test_plan_preview_rejects_approved_selection_source_drift(tmp_path, monkeypatch) -> None:
    catalog, source, project_id, conversation_id, _circuit, reviewed = _reviewed_p1_plan(tmp_path, monkeypatch)
    source.change_selected_source()
    with pytest.raises(SchematicPlanningRejected):
        catalog.create_schematic_plan(
            project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1",
        )
    after = catalog.conversation(
        project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1",
    )
    assert after.fingerprint == reviewed.fingerprint
    assert after.schematic_planning is not None


def test_plan_currentness_is_read_only_and_restarts_as_stale_after_source_change(tmp_path, monkeypatch) -> None:
    catalog, source, project_id, conversation_id, circuit, _reviewed = _reviewed_p1_plan(tmp_path, monkeypatch)
    approved = catalog.decide_schematic_plan(
        project_id, conversation_id, SchematicPlanDecision.APPROVED,
        tenant_id="tenant-1", requester="engineer-1", decided_at=NOW,
    )
    assert approved.schematic_planning is not None
    plan = approved.schematic_planning.approved_plan
    assert plan is not None
    # The two supporting connections and three fulfillments remain covered by
    # test_s02_supporting_fulfillment's pure mapper contract.
    assert catalog.schematic_verifications(
        project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1",
    ) == ()
    selector = (plan.plan_id, plan.fingerprint)
    assert catalog.schematic_plan_currentness(
        project_id, conversation_id, *selector,
        tenant_id="tenant-1", requester="engineer-1",
    ) == (plan.plan_id, plan.fingerprint, "CURRENT")
    app = FastAPI()
    app.state.project_catalog_runtime = catalog

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-support-currentness"
        return await call_next(request)

    app.include_router(router)
    url = f"/api/v1/copilot/projects/{project_id}/conversations/{conversation_id}/schematic-plan/currentness"
    headers = {"X-Copilot-Requester": "engineer-1", "X-Environment-Tenant": "tenant-1"}
    with TestClient(app) as client:
        assert client.get(url, headers=headers).status_code == 422
        response = client.get(
            url, params={"plan_id": plan.plan_id, "plan_fingerprint": plan.fingerprint},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert response.json() == {
            "plan_id": plan.plan_id, "plan_fingerprint": plan.fingerprint,
            "currentness": "CURRENT",
        }
        assert client.get(
            url, params={"plan_id": plan.plan_id, "plan_fingerprint": plan.fingerprint},
            headers={**headers, "X-Copilot-Requester": "other-user"},
        ).status_code == 403
    without_port = ProjectCatalogRuntime(
        project_root=tmp_path / "projects", state_database=tmp_path / "catalog.sqlite",
        initialize_async=False,
    )
    assert without_port.schematic_plan_currentness(
        project_id, conversation_id, *selector,
        tenant_id="tenant-1", requester="engineer-1",
    ) == (plan.plan_id, plan.fingerprint, "UNAVAILABLE")
    with pytest.raises(ProjectCatalogConflict):
        catalog.schematic_plan_currentness(
            project_id, conversation_id, "wrong-plan", plan.fingerprint,
            tenant_id="tenant-1", requester="engineer-1",
        )
    with pytest.raises(ProjectCatalogNotFound):
        catalog.schematic_plan_currentness(
            "wrong-project", conversation_id, *selector,
            tenant_id="tenant-1", requester="engineer-1",
        )
    location = catalog.locations(tenant_id="tenant-1", requester="engineer-1")[0]
    other_preview = catalog.preview(
        ProjectCreationRequest(
            name="Other S02 Project", project_type=ProjectType.STM32,
            creation_mode=ProjectCreationMode.CREATE_NEW,
            location=ProjectLocationReference(
                location_id=location.location_id, fingerprint=location.fingerprint,
            ),
        ),
        tenant_id="tenant-1", requester="engineer-1",
    )
    other_project = catalog.create(
        other_preview.preview_id, other_preview.fingerprint,
        confirmed_at=catalog._now(), tenant_id="tenant-1", requester="engineer-1",
    )
    with pytest.raises(ProjectCatalogNotFound):
        catalog.schematic_plan_currentness(
            other_project.project_id, conversation_id, *selector,
            tenant_id="tenant-1", requester="engineer-1",
        )
    with pytest.raises(ProjectCatalogForbidden):
        catalog.schematic_plan_currentness(
            project_id, conversation_id, *selector,
            tenant_id="tenant-1", requester="other-user",
        )
    source.change_selected_source()
    restarted = ProjectCatalogRuntime(
        project_root=tmp_path / "projects", state_database=tmp_path / "catalog.sqlite",
        initialize_async=False,
    )
    from embedded_copilot.project_catalog.source_publication import PublishedComponentSourcePort
    restarted.set_hardware_component_source(PublishedComponentSourcePort(
        restarted._repository, tenant_id="tenant-1", publisher_id="p1-fixture-publisher"))
    monkeypatch.setattr(restarted, "current_circuit_intents", lambda *args, **kwargs: (circuit,))
    monkeypatch.setattr(restarted, "require_current_circuit_binding", lambda *args, **kwargs: None)
    historical = restarted.conversation(
        project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1",
    )
    assert historical.schematic_planning is not None
    assert historical.schematic_planning.approved_plan == plan
    assert restarted.schematic_plan_currentness(
        project_id, conversation_id, *selector,
        tenant_id="tenant-1", requester="engineer-1",
    ) == (plan.plan_id, plan.fingerprint, "STALE")
    assert restarted.conversation(
        project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1",
    ).fingerprint == approved.fingerprint


def test_legacy_selection_cannot_create_or_approve_new_plan_without_p1(tmp_path) -> None:
    catalog, _source, project_id, conversation_id = _reviewed_catalog(
        tmp_path, with_support=False,
    )
    selected = catalog.decide_hardware_selection(
        project_id, conversation_id, HardwareSelectionDecision.APPROVED,
        tenant_id="tenant-1", requester="engineer-1", decided_at=NOW,
    )
    with pytest.raises(SchematicPlanningBlocked, match="POWER_SOURCE_UNRESOLVED"):
        catalog.create_schematic_plan(
            project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1",
        )
    restarted = ProjectCatalogRuntime(
        project_root=tmp_path / "projects", state_database=tmp_path / "desktop.sqlite3",
        initialize_async=False,
    )
    historical = restarted.conversation(
        project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1",
    )
    assert historical.hardware_selection == selected.hardware_selection
    assert historical.schematic_planning is None
    with pytest.raises(SchematicPlanningRejected):
        restarted.decide_schematic_plan(
            project_id, conversation_id, SchematicPlanDecision.APPROVED,
            tenant_id="tenant-1", requester="engineer-1", decided_at=NOW,
        )
