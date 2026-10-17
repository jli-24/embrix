from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime

import pytest

from embedded_copilot.component_recommendation.contracts import ComponentSourceRecord
from embedded_copilot.engineering_copilot import SchematicPlanDecision
from embedded_copilot.project_catalog.source_publication import (
    ComponentSourcePublishCommandV1, PublishedComponentSourcePort,
    TrustedComponentSourcePublisher,
)
from embedded_copilot.project_catalog.store import SQLiteProjectCatalogRepository
from embedded_copilot.project_catalog.exceptions import ProjectCatalogConflict, ProjectCatalogUnavailable
from tests.project_catalog.test_p1_project_supply import _approved_runtime


def test_source_publication_same_content_aba_retry_replay_and_process_restart(tmp_path, monkeypatch):
    catalog, detail, _relation, _selectors, _circuit, source = _approved_runtime(tmp_path, monkeypatch)
    repository = catalog._repository
    path = repository._database
    record = source.records[0]
    publisher = TrustedComponentSourcePublisher(repository, tenant_id=detail.tenant_id,
        project_id=detail.project_id, publisher_id="p1-fixture-publisher")
    port = PublishedComponentSourcePort(repository, tenant_id=detail.tenant_id,
        publisher_id="p1-fixture-publisher")

    def publish(request, expected, value):
        return publisher.publish(ComponentSourcePublishCommandV1(schema_version=1,
            request_id=request, source_id=record.source_id,
            expected_head_publication_fingerprint=expected, record=value))

    a = port.current_publication(detail.project_id, record.source_id)
    assert a is not None and a.generation == 1
    same, current = publish("same", a.fingerprint, record)
    assert current and same.generation == 2 and same.fingerprint != a.fingerprint
    changed = ComponentSourceRecord.create(**{**record.model_dump(mode="python", exclude={"fingerprint"}),
        "description": "Changed source evidence"})
    b, _ = publish("b", same.fingerprint, changed)
    c, _ = publish("c", b.fingerprint, record)
    assert c.generation == 4 and c.record.fingerprint == a.record.fingerprint and c.fingerprint != a.fingerprint
    again, current = publish("same", a.fingerprint, record)
    assert again == same and not current
    assert PublishedComponentSourcePort(SQLiteProjectCatalogRepository(path),
        tenant_id=detail.tenant_id, publisher_id="p1-fixture-publisher").current_publication(detail.project_id, record.source_id) == c
    script = """import sys
from pathlib import Path
from tests.s02_offline_guard import pytest_configure,pytest_unconfigure
pytest_configure()
from embedded_copilot.project_catalog.store import SQLiteProjectCatalogRepository
from embedded_copilot.project_catalog.source_publication import PublishedComponentSourcePort
port=PublishedComponentSourcePort(SQLiteProjectCatalogRepository(Path(sys.argv[1])),tenant_id=sys.argv[2],publisher_id='p1-fixture-publisher')
head=port.current_publication(sys.argv[3],sys.argv[4])
print(head.fingerprint,head.generation)
pytest_unconfigure()
"""
    process = subprocess.run([sys.executable,"-B","-c",script,str(path),detail.tenant_id,
        detail.project_id,record.source_id],capture_output=True,text=True,encoding="utf-8",timeout=45)
    assert process.returncode == 0, process.stderr
    assert process.stdout.splitlines()[-1] == f"{c.fingerprint} 4"
    with pytest.raises(ProjectCatalogConflict):
        publish("conflicting-cas", a.fingerprint, record)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE component_source_heads SET publication_id=?,generation=?,fingerprint=? WHERE tenant_id=? AND project_id=? AND source_id=?",
            (a.publication_id, a.generation, a.fingerprint, detail.tenant_id, detail.project_id, record.source_id))
    with pytest.raises(ProjectCatalogUnavailable):
        port.current_publication(detail.project_id, record.source_id)
    with pytest.raises(ProjectCatalogUnavailable):
        publish("after-replay", a.fingerprint, record)


def test_unobserved_publication_aba_stales_p1_and_rejects_review(tmp_path, monkeypatch):
    catalog, detail, relation, selectors, _circuit, _source = _approved_runtime(tmp_path, monkeypatch)
    candidate = catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        selectors=selectors, relations=(relation,)).candidate
    source_id = candidate.source_publications[0].source_id
    old = catalog._hardware_component_source.current_publication(detail.project_id, source_id)
    publisher = TrustedComponentSourcePublisher(catalog._repository,
        tenant_id=detail.tenant_id, project_id=detail.project_id,
        publisher_id="p1-fixture-publisher")
    changed = ComponentSourceRecord.create(**{**old.record.model_dump(mode="python", exclude={"fingerprint"}),
        "description": "Unobserved successor"})
    b, _ = publisher.publish(ComponentSourcePublishCommandV1(schema_version=1,
        request_id="aba-b", source_id=source_id,
        expected_head_publication_fingerprint=old.fingerprint, record=changed))
    a2, _ = publisher.publish(ComponentSourcePublishCommandV1(schema_version=1,
        request_id="aba-a", source_id=source_id,
        expected_head_publication_fingerprint=b.fingerprint, record=old.record))
    assert a2.record.fingerprint == old.record.fingerprint and a2.fingerprint != old.fingerprint
    assert catalog.project_supply(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)["status"] == "STALE"
    with pytest.raises(ProjectCatalogConflict):
        catalog.review_project_supply_candidate(detail.project_id, detail.conversation_id,
            candidate.candidate_id, candidate.fingerprint, "APPROVED",
            tenant_id=detail.tenant_id, requester=detail.requester)


def test_replan_legacy_snapshot_unique_head_terminal_retry_and_raw_immutability(tmp_path, monkeypatch):
    catalog, detail, relation, selectors, _circuit, _source = _approved_runtime(tmp_path, monkeypatch)
    state = catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        selectors=selectors, relations=(relation,))
    catalog.review_project_supply_candidate(detail.project_id, detail.conversation_id,
        state.candidate.candidate_id, state.candidate.fingerprint, "APPROVED",
        tenant_id=detail.tenant_id, requester=detail.requester)
    catalog.create_schematic_plan(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)
    original = catalog.decide_schematic_plan(detail.project_id, detail.conversation_id,
        SchematicPlanDecision.APPROVED, tenant_id=detail.tenant_id,
        requester=detail.requester, decided_at=datetime(2026, 10, 8, tzinfo=UTC))
    old = original.schematic_planning.approved_plan
    with sqlite3.connect(catalog._repository._database) as db:
        raw = db.execute("SELECT body FROM conversations WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0]
    revision = catalog.create_schematic_plan_v2(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        request_id="revision-1", expected_head=(None, old.plan_id, old.fingerprint))
    assert revision["plan_id"] != old.plan_id and revision["currentness"] == "CURRENT"
    with sqlite3.connect(catalog._repository._database) as db:
        saved = db.execute("SELECT conversation_body FROM schematic_plan_legacy_snapshots WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0]
    assert saved == raw
    legacy = catalog.schematic_plan_legacy_snapshot(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        plan_id=old.plan_id, plan_fingerprint=old.fingerprint)
    assert legacy["planning"] == json.loads(raw)["schematic_planning"]
    assert legacy["currentness"] == "STALE"
    with pytest.raises(ProjectCatalogConflict):
        catalog.schematic_plan_currentness(detail.project_id, detail.conversation_id,
            old.plan_id, old.fingerprint, tenant_id=detail.tenant_id,
            requester=detail.requester)
    approved = catalog.decide_schematic_plan_v2(detail.project_id, detail.conversation_id,
        SchematicPlanDecision.APPROVED, tenant_id=detail.tenant_id,
        requester=detail.requester, revision_id=revision["revision_id"],
        plan_id=revision["plan_id"], plan_fingerprint=revision["plan_fingerprint"],
        decided_at=datetime(2026, 10, 9, tzinfo=UTC))
    assert approved["planning"]["status"] == "APPROVED"
    successor = catalog.create_schematic_plan_v2(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        request_id="revision-2", expected_head=(revision["revision_id"],revision["plan_id"],revision["plan_fingerprint"]))
    assert successor["plan_id"] not in (old.plan_id, revision["plan_id"])
    retry = catalog.create_schematic_plan_v2(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        request_id="revision-1", expected_head=(None,old.plan_id,old.fingerprint))
    assert retry["revision_id"] == revision["revision_id"] and retry["currentness"] == "STALE"
    decision_retry = catalog.decide_schematic_plan_v2(detail.project_id, detail.conversation_id,
        SchematicPlanDecision.APPROVED, tenant_id=detail.tenant_id,
        requester=detail.requester, revision_id=revision["revision_id"],
        plan_id=revision["plan_id"], plan_fingerprint=revision["plan_fingerprint"],
        decided_at=datetime(2026, 10, 10, tzinfo=UTC))
    assert decision_retry["revision_id"] == revision["revision_id"] and decision_retry["currentness"] == "STALE"
    assert len(catalog.schematic_plan_revision_history(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)) == 2


def test_nonempty_legacy_receipt_history_is_readonly_and_exact(tmp_path):
    from tests.project_catalog.test_schematic_verification_record import _s02_generated_catalog

    catalog, project_id, conversation_id, _selector, plan, _artifact_fp, _artifact = _s02_generated_catalog(tmp_path)
    repository = catalog._repository
    with sqlite3.connect(repository._database) as db:
        original = db.execute("SELECT body FROM conversations WHERE conversation_id=?",
            (conversation_id,)).fetchone()[0]
        db.execute("INSERT INTO schematic_plan_legacy_snapshots(tenant_id,project_id,conversation_id,plan_id,plan_fingerprint,conversation_body) VALUES(?,?,?,?,?,?)",
            ("tenant-1", project_id, conversation_id, plan.plan_id, plan.fingerprint, original))
    body = json.loads(original)
    generation = body["schematic_production"]["generation_receipt"]
    erc = body["schematic_production"]["erc_receipt"]
    assert generation["receipt_id"] and erc["receipt_id"]
    view = repository.schematic_plan_legacy_snapshot(tenant_id="tenant-1",
        project_id=project_id, conversation_id=conversation_id,
        requester="engineer-1", plan_id=plan.plan_id,
        plan_fingerprint=plan.fingerprint)
    assert view["planning"] == body["schematic_planning"]
    assert view["receipt_references"] == (
        {"kind": "GENERATION", "receipt_id": generation["receipt_id"],
         "fingerprint": generation["fingerprint"], "operation_id": generation["operation_id"],
         "operation_receipt_fingerprint": generation["operation_receipt_fingerprint"]},
        {"kind": "ERC", "receipt_id": erc["receipt_id"],
         "fingerprint": erc["fingerprint"], "operation_id": erc["operation_id"],
         "operation_receipt_fingerprint": erc["operation_receipt_fingerprint"]},
    )
    with sqlite3.connect(repository._database) as db:
        assert db.execute("SELECT body FROM conversations WHERE conversation_id=?",
            (conversation_id,)).fetchone()[0] == original
        assert db.execute("SELECT conversation_body FROM schematic_plan_legacy_snapshots WHERE conversation_id=?",
            (conversation_id,)).fetchone()[0] == original


def _seed_legacy_v2_with_plan(tmp_path, monkeypatch):
    from embedded_copilot.project_catalog.models import (
        ConversationDetail, ProjectSupplyCandidateV2, ProjectSupplyReviewV2,
        ProjectSupplyStateV2,
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
        requester=detail.requester, decided_at=datetime(2026, 10, 8, tzinfo=UTC))
    candidate_body = state.candidate.model_dump(mode="python", exclude={"source_publications", "fingerprint"})
    candidate = ProjectSupplyCandidateV2(**{**candidate_body,"schema_version":2,"fingerprint":"sha256:"+"0"*64})
    review = ProjectSupplyReviewV2(**{**approved.project_supply.review.model_dump(mode="python"),
        "candidate_fingerprint":candidate.fingerprint,"fingerprint":"sha256:"+"0"*64})
    old = ProjectSupplyStateV2(schema_version=2,candidate=candidate,review=review)
    legacy = ConversationDetail.model_validate({**approved.model_dump(mode="python"),
        "project_supply":old,"fingerprint":"sha256:"+"0"*64})
    catalog.persist_conversation_detail(legacy)
    with sqlite3.connect(catalog._repository._database) as db:
        raw = db.execute("SELECT body FROM conversations WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0]
    return catalog, detail, relation, selectors, approved.schematic_planning.approved_plan, old, raw


def test_v2_raw_candidate_and_plan_survive_explicit_v3_cutover_replan_and_new_python(tmp_path, monkeypatch):
    import hashlib
    import subprocess
    import sys
    catalog, detail, relation, selectors, old_plan, old_state, raw = _seed_legacy_v2_with_plan(tmp_path, monkeypatch)
    assert json.loads(raw)["project_supply"]["schema_version"] == 2
    next_state = catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        selectors=selectors, relations=(relation,))
    saved = catalog._repository.legacy_project_supply_raw(tenant_id=detail.tenant_id,
        project_id=detail.project_id, conversation_id=detail.conversation_id,
        requester=detail.requester, candidate_id=old_state.candidate.candidate_id,
        candidate_fingerprint=old_state.candidate.fingerprint)
    assert saved == raw
    assert catalog._repository.legacy_project_supply_raw(tenant_id=detail.tenant_id,
        project_id=detail.project_id, conversation_id=detail.conversation_id,
        requester=detail.requester, candidate_id=old_state.candidate.candidate_id,
        candidate_fingerprint="sha256:"+"f"*64) is None
    catalog.review_project_supply_candidate(detail.project_id, detail.conversation_id,
        next_state.candidate.candidate_id, next_state.candidate.fingerprint,
        "APPROVED", tenant_id=detail.tenant_id, requester=detail.requester)
    before_replan = catalog.conversation(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester).model_dump_json()
    revision = catalog.create_schematic_plan_v2(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        request_id="v2-after-legacy", expected_head=(None, old_plan.plan_id, old_plan.fingerprint))
    with sqlite3.connect(catalog._repository._database) as db:
        legacy_raw = db.execute("SELECT conversation_body FROM schematic_plan_legacy_snapshots WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0]
        predecessor = db.execute("SELECT predecessor_conversation_body FROM schematic_plan_revisions WHERE revision_id=?",
            (revision["revision_id"],)).fetchone()[0]
    assert legacy_raw == raw and predecessor == before_replan
    assert json.loads(legacy_raw)["project_supply"]["candidate"]["fingerprint"] == old_state.candidate.fingerprint
    script = """import hashlib,sys
from pathlib import Path
from tests.s02_offline_guard import pytest_configure,pytest_unconfigure
pytest_configure()
from embedded_copilot.project_catalog.store import SQLiteProjectCatalogRepository
repo=SQLiteProjectCatalogRepository(Path(sys.argv[1]))
raw=repo.legacy_project_supply_raw(tenant_id=sys.argv[2],project_id=sys.argv[3],conversation_id=sys.argv[4],requester=sys.argv[5],candidate_id=sys.argv[6],candidate_fingerprint=sys.argv[7])
print(hashlib.sha256(raw.encode('utf-8')).hexdigest())
pytest_unconfigure()
"""
    process = subprocess.run([sys.executable,"-B","-c",script,str(catalog._repository._database),
        detail.tenant_id,detail.project_id,detail.conversation_id,detail.requester,
        old_state.candidate.candidate_id,old_state.candidate.fingerprint],
        capture_output=True,text=True,encoding="utf-8",timeout=45)
    assert process.returncode == 0, process.stderr
    assert process.stdout.splitlines()[-1] == hashlib.sha256(raw.encode("utf-8")).hexdigest()


def test_v2_to_v3_raw_snapshot_and_conversation_roll_back_together(tmp_path, monkeypatch):
    catalog, detail, relation, selectors, _old_plan, old_state, raw = _seed_legacy_v2_with_plan(tmp_path, monkeypatch)
    with sqlite3.connect(catalog._repository._database) as db:
        db.execute("CREATE TRIGGER abort_v3_projection BEFORE UPDATE ON conversations BEGIN SELECT RAISE(ABORT,'interrupted'); END")
    with pytest.raises(sqlite3.IntegrityError):
        catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
            tenant_id=detail.tenant_id, requester=detail.requester,
            selectors=selectors, relations=(relation,))
    with sqlite3.connect(catalog._repository._database) as db:
        assert db.execute("SELECT body FROM conversations WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0] == raw
        assert db.execute("SELECT COUNT(*) FROM schematic_legacy_owner_snapshots WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM schematic_plan_legacy_snapshots WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0] == 0
        db.execute("DROP TRIGGER abort_v3_projection")
    assert catalog._repository.legacy_project_supply_raw(tenant_id=detail.tenant_id,
        project_id=detail.project_id, conversation_id=detail.conversation_id,
        requester=detail.requester, candidate_id=old_state.candidate.candidate_id,
        candidate_fingerprint=old_state.candidate.fingerprint) is None


def _reviewed_v2_revision(tmp_path, monkeypatch):
    catalog, detail, relation, selectors, _circuit, _source = _approved_runtime(tmp_path, monkeypatch)
    state = catalog.create_project_supply_candidate(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        selectors=selectors, relations=(relation,))
    catalog.review_project_supply_candidate(detail.project_id, detail.conversation_id,
        state.candidate.candidate_id, state.candidate.fingerprint, "APPROVED",
        tenant_id=detail.tenant_id, requester=detail.requester)
    catalog.create_schematic_plan(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester)
    old = catalog.decide_schematic_plan(detail.project_id, detail.conversation_id,
        SchematicPlanDecision.APPROVED, tenant_id=detail.tenant_id,
        requester=detail.requester, decided_at=datetime(2026,10,8,tzinfo=UTC))
    plan = old.schematic_planning.approved_plan
    revision = catalog.create_schematic_plan_v2(detail.project_id, detail.conversation_id,
        tenant_id=detail.tenant_id, requester=detail.requester,
        request_id="v2-review", expected_head=(None,plan.plan_id,plan.fingerprint))
    return catalog, detail, revision


@pytest.mark.parametrize("durable_status",("REVIEW","REJECTED"))
def test_durable_revision_decision_prevents_forged_conversation_approval(tmp_path, monkeypatch, durable_status):
    from embedded_copilot.engineering_copilot.schematic_planning import SchematicPlanningService
    from embedded_copilot.project_catalog.models import ConversationDetail
    catalog, detail, revision = _reviewed_v2_revision(tmp_path, monkeypatch)
    scope={"tenant_id":detail.tenant_id,"requester":detail.requester}
    if durable_status == "REJECTED":
        catalog.decide_schematic_plan_v2(detail.project_id, detail.conversation_id,
            SchematicPlanDecision.REJECTED, revision_id=revision["revision_id"],
            plan_id=revision["plan_id"], plan_fingerprint=revision["plan_fingerprint"],
            decided_at=datetime(2026,10,9,tzinfo=UTC), **scope)
    durable = catalog._repository.schematic_plan_revision_head(
        tenant_id=detail.tenant_id,project_id=detail.project_id,
        conversation_id=detail.conversation_id,requester=detail.requester)
    assert durable["planning"].status.value == durable_status
    service=SchematicPlanningService()
    fake=service.decide(durable["planning"].proposal,SchematicPlanDecision.APPROVED,
        reviewer=detail.requester,decided_at=datetime(2026,10,10,tzinfo=UTC))
    current=catalog.conversation(detail.project_id,detail.conversation_id,**scope)
    projected=service.project_design_truth(durable["base_design_truth"],fake.approved_plan)
    altered=ConversationDetail.model_validate({**current.model_dump(mode="python"),
        "schematic_planning":fake,"design_truth":projected,
        "engineering_knowledge_model":None,"fingerprint":"sha256:"+"0"*64})
    catalog.persist_conversation_detail(altered)
    latest=catalog.conversation(detail.project_id,detail.conversation_id,**scope)
    assert catalog._schematic_plan_source_currentness(latest,fake.approved_plan,
        project_id=detail.project_id,**scope)=="STALE"
    head=catalog.schematic_plan_revision_history(detail.project_id,detail.conversation_id,**scope)[-1]
    assert head["currentness"]=="STALE" and head["planning"]["status"]==durable_status


def test_durable_revision_base_truth_mismatch_is_stale(tmp_path, monkeypatch):
    catalog, detail, revision = _reviewed_v2_revision(tmp_path, monkeypatch)
    scope={"tenant_id":detail.tenant_id,"requester":detail.requester}
    current=catalog.conversation(detail.project_id,detail.conversation_id,**scope)
    durable=catalog._repository.schematic_plan_revision_head(
        tenant_id=detail.tenant_id,project_id=detail.project_id,
        conversation_id=detail.conversation_id,requester=detail.requester)
    assert current.design_truth.fingerprint != durable["base_design_truth"].fingerprint
    with sqlite3.connect(catalog._repository._database) as db:
        db.execute("UPDATE schematic_plan_revisions SET base_truth_body=? WHERE revision_id=?",
            (current.design_truth.model_dump_json(),revision["revision_id"]))
    latest=catalog.conversation(detail.project_id,detail.conversation_id,**scope)
    assert catalog._schematic_plan_source_currentness(latest,latest.schematic_planning.proposal,
        project_id=detail.project_id,**scope)=="STALE"


def test_v2_replan_uses_existing_http_paths_with_exact_selectors(tmp_path, monkeypatch):
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from embedded_copilot.api.project_catalog_routes import router
    catalog, detail, relation, selectors, _circuit, _source = _approved_runtime(tmp_path, monkeypatch)
    state=catalog.create_project_supply_candidate(detail.project_id,detail.conversation_id,
        tenant_id=detail.tenant_id,requester=detail.requester,selectors=selectors,relations=(relation,))
    catalog.review_project_supply_candidate(detail.project_id,detail.conversation_id,
        state.candidate.candidate_id,state.candidate.fingerprint,"APPROVED",
        tenant_id=detail.tenant_id,requester=detail.requester)
    catalog.create_schematic_plan(detail.project_id,detail.conversation_id,
        tenant_id=detail.tenant_id,requester=detail.requester)
    original=catalog.decide_schematic_plan(detail.project_id,detail.conversation_id,
        SchematicPlanDecision.APPROVED,tenant_id=detail.tenant_id,requester=detail.requester,
        decided_at=datetime(2026,10,8,tzinfo=UTC))
    old=original.schematic_planning.approved_plan
    old_candidate=original.project_supply.candidate
    source_id=old_candidate.source_publications[0].source_id
    published=catalog._hardware_component_source.current_publication(detail.project_id,source_id)
    changed=ComponentSourceRecord.create(**{**published.record.model_dump(mode="python",exclude={"fingerprint"}),
        "description":"Unobserved HTTP successor"})
    publisher=TrustedComponentSourcePublisher(catalog._repository,tenant_id=detail.tenant_id,
        project_id=detail.project_id,publisher_id="p1-fixture-publisher")
    b,_=publisher.publish(ComponentSourcePublishCommandV1(schema_version=1,request_id="http-aba-b",
        source_id=source_id,expected_head_publication_fingerprint=published.fingerprint,record=changed))
    a2,_=publisher.publish(ComponentSourcePublishCommandV1(schema_version=1,request_id="http-aba-a",
        source_id=source_id,expected_head_publication_fingerprint=b.fingerprint,record=published.record))
    assert a2.record.fingerprint==published.record.fingerprint and a2.fingerprint!=published.fingerprint
    with sqlite3.connect(catalog._repository._database) as db:
        old_raw=db.execute("SELECT body FROM conversations WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0]
    app=FastAPI();app.state.project_catalog_runtime=catalog
    @app.middleware("http")
    async def trace(request:Request,call_next):
        request.state.trace_id="replan-http-test"
        return await call_next(request)
    app.include_router(router)
    base=f"/api/v1/copilot/projects/{detail.project_id}/conversations/{detail.conversation_id}/schematic-plan"
    headers={"X-Copilot-Requester":detail.requester,"X-Environment-Tenant":detail.tenant_id}
    with TestClient(app) as client:
        supply_url=base.removesuffix("/schematic-plan")+"/project-supply"
        stale=client.get(supply_url,headers=headers)
        assert stale.status_code==200 and stale.json()["status"]=="STALE"
        assert client.get(base+"/currentness",params={"plan_id":old.plan_id,
            "plan_fingerprint":old.fingerprint},headers=headers).json()["currentness"]=="STALE"
        fresh=client.post(supply_url,json={
            "design_truth_id":old_candidate.design_truth_id,
            "design_truth_fingerprint":old_candidate.design_truth_fingerprint,
            "selection_id":old_candidate.selection_id,
            "selection_fingerprint":old_candidate.selection_fingerprint,
            "circuit_id":old_candidate.circuit_id,
            "circuit_fingerprint":old_candidate.circuit_fingerprint,
            "plan_id":old_candidate.plan_id,
            "plan_fingerprint":old_candidate.plan_fingerprint,
            "relations":[item.model_dump(mode="json",exclude={"fingerprint"}) for item in old_candidate.relations],
        },headers=headers)
        assert fresh.status_code==200,fresh.text
        assert fresh.json()["candidate"]["schema_version"]==3
        assert fresh.json()["candidate"]["source_publications"]!=old_candidate.model_dump(mode="json")["source_publications"]
        with sqlite3.connect(catalog._repository._database) as db:
            snapshot=db.execute("SELECT conversation_body FROM schematic_plan_legacy_snapshots WHERE conversation_id=?",
                (detail.conversation_id,)).fetchone()[0]
        assert snapshot==old_raw
        assert json.loads(snapshot)["project_supply"]["candidate"]["fingerprint"]==old_candidate.fingerprint
        candidate=fresh.json()["candidate"]
        review=client.post(supply_url+"/"+candidate["candidate_id"]+"/review",
            json={"candidate_fingerprint":candidate["fingerprint"],"decision":"APPROVED"},headers=headers)
        assert review.status_code==200,review.text
        assert client.get(supply_url,headers=headers).json()["status"]=="APPROVED"
        proposal=client.post(base+"/proposal",json={"schema_version":2,"request_id":"http-revision",
            "expected_head_revision_id":None,"expected_head_plan_id":old.plan_id,
            "expected_head_plan_fingerprint":old.fingerprint},headers=headers)
        assert proposal.status_code==200,proposal.text
        revision=proposal.json()
        assert revision["schema_version"]==2 and revision["planning"]["status"]=="REVIEW"
        assert revision["plan_id"]!=old.plan_id
        history=client.get(base+"/currentness",params={"schema_version":2},headers=headers)
        assert history.status_code==200,history.text
        assert history.json()["head"]["revision_id"]==revision["revision_id"]
        assert history.json()["legacy"]["plan_id"]==old.plan_id
        with sqlite3.connect(catalog._repository._database) as db:
            assert db.execute("SELECT conversation_body FROM schematic_plan_legacy_snapshots WHERE conversation_id=?",
                (detail.conversation_id,)).fetchone()[0]==old_raw
        assert client.get(base+"/currentness",params={"plan_id":old.plan_id,
            "plan_fingerprint":old.fingerprint},headers=headers).status_code==409
        decision=client.post(base+"/decision",json={"schema_version":2,
            "revision_id":revision["revision_id"],"plan_id":revision["plan_id"],
            "plan_fingerprint":revision["plan_fingerprint"],"decision":"APPROVED"},headers=headers)
        assert decision.status_code==200,decision.text
        assert decision.json()["planning"]["status"]=="APPROVED"
        exact=client.get(base+"/currentness",params={"schema_version":2,
            "revision_id":revision["revision_id"],"plan_id":revision["plan_id"],
            "plan_fingerprint":revision["plan_fingerprint"]},headers=headers)
        assert exact.status_code==200,exact.text
        assert exact.json()["currentness"]=="CURRENT"
        assert client.get(base+"/currentness",params={"schema_version":2,
            "revision_id":revision["revision_id"],"plan_id":old.plan_id,
            "plan_fingerprint":revision["plan_fingerprint"]},headers=headers).status_code==503


def test_real_predesign_store_publication_aba_replan_and_new_python_current_head(tmp_path):
    from tests.engineering_copilot.test_schematic_production_g3 import _runtime_with_approved_plan
    catalog, project, session, _proposed = _runtime_with_approved_plan(tmp_path)
    project_id=project.project_id
    conversation_id=session.conversation.conversation_id
    scope={"tenant_id":"tenant-1","requester":"engineer-1"}
    original=catalog.conversation(project_id,conversation_id,**scope)
    old=original.schematic_planning.approved_plan
    candidate=original.project_supply.candidate
    source_id=candidate.source_publications[0].source_id
    first=catalog._hardware_component_source.current_publication(project_id,source_id)
    changed=ComponentSourceRecord.create(**{**first.record.model_dump(mode="python",exclude={"fingerprint"}),
        "description":"Real Store unobserved successor"})
    publisher=TrustedComponentSourcePublisher(catalog._repository,tenant_id="tenant-1",
        project_id=project_id,publisher_id="g3-fixture-publisher")
    b,_=publisher.publish(ComponentSourcePublishCommandV1(schema_version=1,request_id="real-aba-b",
        source_id=source_id,expected_head_publication_fingerprint=first.fingerprint,record=changed))
    a2,_=publisher.publish(ComponentSourcePublishCommandV1(schema_version=1,request_id="real-aba-a",
        source_id=source_id,expected_head_publication_fingerprint=b.fingerprint,record=first.record))
    assert a2.record.fingerprint==first.record.fingerprint and a2.fingerprint!=first.fingerprint
    assert catalog.schematic_plan_currentness(project_id,conversation_id,old.plan_id,old.fingerprint,**scope)[2]=="STALE"
    selectors={key:getattr(candidate,key) for key in ("design_truth_id","design_truth_fingerprint",
        "selection_id","selection_fingerprint","circuit_id","circuit_fingerprint",
        "plan_id","plan_fingerprint")}
    fresh=catalog.create_project_supply_candidate(project_id,conversation_id,**scope,
        selectors=selectors,relations=tuple(item.model_dump(mode="python",exclude={"fingerprint"})
            for item in candidate.relations))
    assert fresh.candidate.source_publications!=candidate.source_publications
    catalog.review_project_supply_candidate(project_id,conversation_id,
        fresh.candidate.candidate_id,fresh.candidate.fingerprint,"APPROVED",**scope)
    assert catalog.schematic_plan_currentness(project_id,conversation_id,old.plan_id,old.fingerprint,**scope)[2]=="STALE"
    revision=catalog.create_schematic_plan_v2(project_id,conversation_id,**scope,
        request_id="real-replan-1",expected_head=(None,old.plan_id,old.fingerprint))
    approved=catalog.decide_schematic_plan_v2(project_id,conversation_id,
        SchematicPlanDecision.APPROVED,**scope,revision_id=revision["revision_id"],
        plan_id=revision["plan_id"],plan_fingerprint=revision["plan_fingerprint"],
        decided_at=datetime(2026,10,9,tzinfo=UTC))
    assert approved["currentness"]=="CURRENT" and approved["planning"]["status"]=="APPROVED"
    script="""import json,sys
from pathlib import Path
from tests.s02_offline_guard import pytest_configure,pytest_unconfigure
pytest_configure()
from embedded_copilot.project_catalog.runtime import ProjectCatalogRuntime
from embedded_copilot.project_catalog.source_publication import PublishedComponentSourcePort
runtime=ProjectCatalogRuntime(project_root=Path(sys.argv[1]),state_database=Path(sys.argv[2]),initialize_async=False)
runtime.set_hardware_component_source(PublishedComponentSourcePort(runtime._repository,tenant_id='tenant-1',publisher_id='g3-fixture-publisher'))
history=runtime.schematic_plan_revision_history(sys.argv[3],sys.argv[4],tenant_id='tenant-1',requester='engineer-1')
print(json.dumps({'revision_id':history[-1]['revision_id'],'status':history[-1]['planning']['status'],'currentness':history[-1]['currentness']}))
pytest_unconfigure()
"""
    process=subprocess.run([sys.executable,"-B","-c",script,str(tmp_path/"projects"),
        str(tmp_path/"desktop.sqlite3"),project_id,conversation_id],
        capture_output=True,text=True,encoding="utf-8",timeout=60)
    assert process.returncode==0,process.stderr
    assert json.loads(process.stdout.splitlines()[-1])=={
        "revision_id":revision["revision_id"],"status":"APPROVED","currentness":"CURRENT"}
    successor=catalog.create_schematic_plan_v2(project_id,conversation_id,**scope,
        request_id="real-replan-2",expected_head=(revision["revision_id"],
            revision["plan_id"],revision["plan_fingerprint"]))
    history=catalog.schematic_plan_revision_history(project_id,conversation_id,**scope)
    assert history[0]["currentness"]=="STALE"
    assert history[1]["revision_id"]==successor["revision_id"] and history[1]["planning"]["status"]=="REVIEW"
    latest=catalog.conversation(project_id,conversation_id,**scope)
    earlier=catalog._repository.schematic_plan_revision(tenant_id="tenant-1",
        project_id=project_id,conversation_id=conversation_id,requester="engineer-1",
        revision_id=revision["revision_id"],plan_id=revision["plan_id"],
        plan_fingerprint=revision["plan_fingerprint"])
    assert catalog._schematic_plan_source_currentness(latest,
        earlier["planning"].approved_plan,project_id=project_id,**scope)=="STALE"


def _approved_v3_legacy_plan(tmp_path, monkeypatch):
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
        requester=detail.requester, decided_at=datetime(2026,10,8,tzinfo=UTC))
    with sqlite3.connect(catalog._repository._database) as db:
        raw = db.execute("SELECT body FROM conversations WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0]
    return catalog, detail, relation, selectors, approved.schematic_planning.approved_plan, raw


def test_v3_legacy_plan_early_snapshot_is_insert_once_and_new_python_exact(tmp_path, monkeypatch):
    import hashlib
    catalog, detail, relation, selectors, old_plan, raw = _approved_v3_legacy_plan(tmp_path, monkeypatch)
    scope = {"tenant_id":detail.tenant_id, "requester":detail.requester}
    first = catalog.create_project_supply_candidate(detail.project_id,detail.conversation_id,
        selectors=selectors,relations=(relation,),**scope)
    with sqlite3.connect(catalog._repository._database) as db:
        row = db.execute("SELECT plan_id,plan_fingerprint,conversation_body FROM schematic_plan_legacy_snapshots "
            "WHERE conversation_id=?", (detail.conversation_id,)).fetchone()
    assert row == (old_plan.plan_id,old_plan.fingerprint,raw)
    second = catalog.create_project_supply_candidate(detail.project_id,detail.conversation_id,
        selectors=selectors,relations=(relation,),**scope)
    assert first.candidate.fingerprint != second.candidate.fingerprint
    with sqlite3.connect(catalog._repository._database) as db:
        assert db.execute("SELECT COUNT(*) FROM schematic_plan_legacy_snapshots WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0] == 1
        assert db.execute("SELECT conversation_body FROM schematic_plan_legacy_snapshots WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0] == raw
    catalog.review_project_supply_candidate(detail.project_id,detail.conversation_id,
        second.candidate.candidate_id,second.candidate.fingerprint,"APPROVED",**scope)
    before_replan = catalog.conversation(detail.project_id,detail.conversation_id,**scope).model_dump_json()
    revision = catalog.create_schematic_plan_v2(detail.project_id,detail.conversation_id,
        request_id="v3-early-snapshot",expected_head=(None,old_plan.plan_id,old_plan.fingerprint),**scope)
    with sqlite3.connect(catalog._repository._database) as db:
        assert db.execute("SELECT predecessor_conversation_body FROM schematic_plan_revisions WHERE revision_id=?",
            (revision["revision_id"],)).fetchone()[0] == before_replan
        assert db.execute("SELECT conversation_body FROM schematic_plan_legacy_snapshots WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0] == raw
    script = """import hashlib,sqlite3,sys
from pathlib import Path
from tests.s02_offline_guard import pytest_configure,pytest_unconfigure
pytest_configure()
from embedded_copilot.project_catalog.store import SQLiteProjectCatalogRepository
repo=SQLiteProjectCatalogRepository(Path(sys.argv[1]))
with sqlite3.connect(sys.argv[1]) as db:
    row=db.execute('SELECT plan_id,plan_fingerprint,conversation_body FROM schematic_plan_legacy_snapshots WHERE conversation_id=?',(sys.argv[2],)).fetchone()
    head=db.execute('SELECT revision_id FROM schematic_plan_revision_heads WHERE conversation_id=?',(sys.argv[2],)).fetchone()
    predecessor=db.execute('SELECT predecessor_conversation_body FROM schematic_plan_revisions WHERE revision_id=?',(head[0],)).fetchone()[0]
print(row[0],row[1],hashlib.sha256(row[2].encode()).hexdigest(),hashlib.sha256(predecessor.encode()).hexdigest())
pytest_unconfigure()
"""
    process = subprocess.run([sys.executable,"-B","-c",script,str(catalog._repository._database),
        detail.conversation_id],capture_output=True,text=True,encoding="utf-8",timeout=45)
    assert process.returncode == 0, process.stderr
    assert process.stdout.splitlines()[-1] == " ".join((old_plan.plan_id,old_plan.fingerprint,
        hashlib.sha256(raw.encode()).hexdigest(),hashlib.sha256(before_replan.encode()).hexdigest()))


def test_v3_legacy_snapshot_and_conversation_rollback_together(tmp_path, monkeypatch):
    catalog, detail, relation, selectors, old_plan, raw = _approved_v3_legacy_plan(tmp_path, monkeypatch)
    scope = {"tenant_id":detail.tenant_id, "requester":detail.requester}
    with sqlite3.connect(catalog._repository._database) as db:
        db.execute("CREATE TRIGGER abort_v3_old_plan_projection BEFORE UPDATE ON conversations "
            "BEGIN SELECT RAISE(ABORT,'interrupted'); END")
    with pytest.raises(sqlite3.IntegrityError):
        catalog.create_project_supply_candidate(detail.project_id,detail.conversation_id,
            selectors=selectors,relations=(relation,),**scope)
    with sqlite3.connect(catalog._repository._database) as db:
        assert db.execute("SELECT body FROM conversations WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0] == raw
        assert db.execute("SELECT COUNT(*) FROM schematic_plan_legacy_snapshots WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0] == 0
        db.execute("DROP TRIGGER abort_v3_old_plan_projection")
    assert catalog.create_project_supply_candidate(detail.project_id,detail.conversation_id,
        selectors=selectors,relations=(relation,),**scope).candidate.schema_version == 3
    with sqlite3.connect(catalog._repository._database) as db:
        assert db.execute("SELECT plan_id,plan_fingerprint,conversation_body FROM schematic_plan_legacy_snapshots "
            "WHERE conversation_id=?", (detail.conversation_id,)).fetchone() == (
                old_plan.plan_id,old_plan.fingerprint,raw)


def test_v3_legacy_snapshot_concurrent_candidate_cas_has_one_winner(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    catalog, detail, relation, selectors, old_plan, raw = _approved_v3_legacy_plan(tmp_path, monkeypatch)
    scope = {"tenant_id":detail.tenant_id, "requester":detail.requester}
    barrier = Barrier(2)
    original_save = catalog._repository.save_conversation_if_fingerprint
    def race_save(*args, **kwargs):
        barrier.wait(timeout=10)
        return original_save(*args, **kwargs)
    monkeypatch.setattr(catalog._repository, "save_conversation_if_fingerprint", race_save)
    def run():
        try:
            return catalog.create_project_supply_candidate(detail.project_id,detail.conversation_id,
                selectors=selectors,relations=(relation,),**scope)
        except ProjectCatalogConflict as error:
            return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _n: run(), range(2)))
    assert sum(isinstance(item,ProjectCatalogConflict) for item in results)==1
    assert sum(hasattr(item,"candidate") for item in results)==1
    with sqlite3.connect(catalog._repository._database) as db:
        assert db.execute("SELECT COUNT(*) FROM schematic_plan_legacy_snapshots WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0] == 1
        assert db.execute("SELECT plan_id,plan_fingerprint,conversation_body FROM schematic_plan_legacy_snapshots "
            "WHERE conversation_id=?", (detail.conversation_id,)).fetchone() == (
                old_plan.plan_id,old_plan.fingerprint,raw)


def test_source_publication_independent_repository_concurrent_cas_and_atomic_abort(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    catalog, detail, _relation, _selectors, _circuit, source = _approved_runtime(tmp_path, monkeypatch)
    path = catalog._repository._database
    record = source.records[0]
    head = catalog._hardware_component_source.current_publication(detail.project_id, record.source_id)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TRIGGER abort_source_head BEFORE UPDATE ON component_source_heads "
            "BEGIN SELECT RAISE(ABORT,'interrupted'); END")
        before_count = db.execute("SELECT COUNT(*) FROM component_source_publications WHERE source_id=?",
            (record.source_id,)).fetchone()[0]
    publisher = TrustedComponentSourcePublisher(catalog._repository, tenant_id=detail.tenant_id,
        project_id=detail.project_id, publisher_id="p1-fixture-publisher")
    def command(request_id):
        return ComponentSourcePublishCommandV1(schema_version=1, request_id=request_id,
            source_id=record.source_id, expected_head_publication_fingerprint=head.fingerprint,
            record=record)
    with pytest.raises(sqlite3.IntegrityError):
        publisher.publish(command("tx-abort"))
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM component_source_publications WHERE source_id=?",
            (record.source_id,)).fetchone()[0] == before_count
        assert db.execute("SELECT fingerprint FROM component_source_heads WHERE source_id=?",
            (record.source_id,)).fetchone()[0] == head.fingerprint
        db.execute("DROP TRIGGER abort_source_head")
    barrier = Barrier(2)
    def run(request_id):
        independent = TrustedComponentSourcePublisher(SQLiteProjectCatalogRepository(path),
            tenant_id=detail.tenant_id, project_id=detail.project_id,
            publisher_id="p1-fixture-publisher")
        barrier.wait(timeout=10)
        try:
            return independent.publish(command(request_id))
        except ProjectCatalogConflict as error:
            return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ("concurrent-left", "concurrent-right")))
    assert sum(isinstance(item,ProjectCatalogConflict) for item in results)==1
    successes = [item for item in results if isinstance(item,tuple)]
    assert len(successes)==1 and successes[0][0].generation==head.generation+1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM component_source_publications WHERE source_id=?",
            (record.source_id,)).fetchone()[0] == before_count+1


def test_replan_revision_and_conversation_atomic_abort(tmp_path, monkeypatch):
    catalog, detail, _relation, _selectors, old_plan, raw = _approved_v3_legacy_plan(tmp_path, monkeypatch)
    path = catalog._repository._database
    scope = {"tenant_id":detail.tenant_id,"requester":detail.requester}
    with sqlite3.connect(path) as db:
        db.execute("CREATE TRIGGER abort_revision_projection BEFORE UPDATE ON conversations "
            "BEGIN SELECT RAISE(ABORT,'interrupted'); END")
    with pytest.raises(sqlite3.IntegrityError):
        catalog.create_schematic_plan_v2(detail.project_id,detail.conversation_id,
            request_id="revision-aborted",expected_head=(None,old_plan.plan_id,old_plan.fingerprint),**scope)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT body FROM conversations WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0]==raw
        for table in ("schematic_plan_legacy_snapshots","schematic_plan_revisions",
                      "schematic_plan_revision_heads"):
            assert db.execute(f"SELECT COUNT(*) FROM {table} WHERE conversation_id=?",
                (detail.conversation_id,)).fetchone()[0]==0
        db.execute("DROP TRIGGER abort_revision_projection")
    revision = catalog.create_schematic_plan_v2(detail.project_id,detail.conversation_id,
        request_id="revision-aborted",expected_head=(None,old_plan.plan_id,old_plan.fingerprint),**scope)
    assert revision["revision_number"]==1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT conversation_body FROM schematic_plan_legacy_snapshots WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0]==raw


def test_replan_concurrent_proposals_have_one_head(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    catalog, detail, _relation, _selectors, old_plan, raw = _approved_v3_legacy_plan(tmp_path, monkeypatch)
    scope = {"tenant_id":detail.tenant_id,"requester":detail.requester}
    barrier = Barrier(2)
    original_append = catalog._repository.append_schematic_plan_revision
    def race_append(*args, **kwargs):
        barrier.wait(timeout=10)
        return original_append(*args, **kwargs)
    monkeypatch.setattr(catalog._repository,"append_schematic_plan_revision",race_append)
    def run(request_id):
        try:
            return catalog.create_schematic_plan_v2(detail.project_id,detail.conversation_id,
                request_id=request_id,expected_head=(None,old_plan.plan_id,old_plan.fingerprint),**scope)
        except ProjectCatalogConflict as error:
            return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ("revision-left","revision-right")))
    assert sum(isinstance(item,ProjectCatalogConflict) for item in results)==1
    assert sum(isinstance(item,dict) for item in results)==1
    with sqlite3.connect(catalog._repository._database) as db:
        assert db.execute("SELECT COUNT(*) FROM schematic_plan_revisions WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM schematic_plan_revision_heads WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0]==1
        assert db.execute("SELECT conversation_body FROM schematic_plan_legacy_snapshots WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0]==raw


@pytest.mark.parametrize("mode",("raw","split_repository","wrong_tenant","wrong_publisher","missing_head","split_record","head_race"))
def test_owner_rejects_raw_split_scope_publisher_and_missing_source_head(tmp_path, monkeypatch, mode):
    catalog, detail, relation, selectors, _circuit, static_source = _approved_runtime(tmp_path, monkeypatch)
    scope = {"tenant_id":detail.tenant_id,"requester":detail.requester}
    source_id = static_source.records[0].source_id
    if mode == "raw":
        catalog.set_hardware_component_source(static_source)
    elif mode == "split_repository":
        catalog.set_hardware_component_source(PublishedComponentSourcePort(
            SQLiteProjectCatalogRepository(catalog._repository._database),
            tenant_id=detail.tenant_id,publisher_id="p1-fixture-publisher"))
    elif mode == "wrong_tenant":
        catalog.set_hardware_component_source(PublishedComponentSourcePort(
            catalog._repository,tenant_id="different-tenant",publisher_id="p1-fixture-publisher"))
    elif mode == "wrong_publisher":
        catalog.set_hardware_component_source(PublishedComponentSourcePort(
            catalog._repository,tenant_id=detail.tenant_id,publisher_id="different-publisher"))
    elif mode == "missing_head":
        with sqlite3.connect(catalog._repository._database) as db:
            db.execute("DELETE FROM component_source_heads WHERE tenant_id=? AND project_id=? AND source_id=?",
                (detail.tenant_id,detail.project_id,source_id))
    elif mode == "split_record":
        current = catalog._hardware_component_source.current_publication(detail.project_id,source_id)
        changed = ComponentSourceRecord.create(**{**current.record.model_dump(mode="python",exclude={"fingerprint"}),
            "description":"Selected record no longer matches publication"})
        TrustedComponentSourcePublisher(catalog._repository,tenant_id=detail.tenant_id,
            project_id=detail.project_id,publisher_id="p1-fixture-publisher").publish(
                ComponentSourcePublishCommandV1(schema_version=1,request_id="split-record",
                    source_id=source_id,expected_head_publication_fingerprint=current.fingerprint,record=changed))
    elif mode == "head_race":
        current = catalog._hardware_component_source.current_publication(detail.project_id,source_id)
        publisher = TrustedComponentSourcePublisher(catalog._repository,tenant_id=detail.tenant_id,
            project_id=detail.project_id,publisher_id="p1-fixture-publisher")
        original_search = PublishedComponentSourcePort.search
        observed = {"published":False}
        def racing_search(port, project_id, architecture_id, requirement):
            result = original_search(port,project_id,architecture_id,requirement)
            if not observed["published"]:
                observed["published"]=True
                publisher.publish(ComponentSourcePublishCommandV1(schema_version=1,
                    request_id="between-source-reads",source_id=source_id,
                    expected_head_publication_fingerprint=current.fingerprint,record=current.record))
            return result
        monkeypatch.setattr(PublishedComponentSourcePort,"search",racing_search)
    expected = ProjectCatalogUnavailable if mode=="missing_head" else (
        ValueError if mode=="wrong_publisher" else ProjectCatalogConflict)
    with pytest.raises(expected):
        catalog.create_project_supply_candidate(detail.project_id,detail.conversation_id,
            selectors=selectors,relations=(relation,),**scope)
    latest=catalog.conversation(detail.project_id,detail.conversation_id,**scope)
    assert latest.project_supply is None


def test_owner_requires_exact_v3_binding_set_and_model_rejects_unsorted_or_empty(tmp_path, monkeypatch):
    from embedded_copilot.project_catalog.models import ProjectSupplyCandidateV3, SourcePublicationBindingV1
    catalog, detail, relation, selectors, _circuit, _source = _approved_runtime(tmp_path, monkeypatch)
    state = catalog.create_project_supply_candidate(detail.project_id,detail.conversation_id,
        tenant_id=detail.tenant_id,requester=detail.requester,
        selectors=selectors,relations=(relation,))
    candidate = state.candidate
    original = candidate.model_dump(mode="python",exclude={"fingerprint"})
    with pytest.raises(ValueError,match="nonempty"):
        ProjectSupplyCandidateV3(**{**original,"source_publications":()})
    extra = SourcePublicationBindingV1.model_validate({**candidate.source_publications[0].model_dump(mode="python"),
        "source_id":"zz-extra"})
    with pytest.raises(ValueError,match="sorted"):
        ProjectSupplyCandidateV3(**{**original,"source_publications":(extra,*candidate.source_publications)})
    expanded = ProjectSupplyCandidateV3(**{**original,"source_publications":(*candidate.source_publications,extra)})
    current = catalog.conversation(detail.project_id,detail.conversation_id,
        tenant_id=detail.tenant_id,requester=detail.requester)
    with pytest.raises(ProjectCatalogConflict,match="publication"):
        catalog._project_supply_checked(current,expanded)
    assert catalog.project_supply(detail.project_id,detail.conversation_id,
        tenant_id=detail.tenant_id,requester=detail.requester)["candidate"]["fingerprint"]==candidate.fingerprint


def test_existing_legacy_snapshot_row_body_mismatch_rejects_without_replan_progress(tmp_path, monkeypatch):
    catalog, detail, relation, selectors, old_plan, _raw = _approved_v3_legacy_plan(tmp_path, monkeypatch)
    scope={"tenant_id":detail.tenant_id,"requester":detail.requester}
    fresh=catalog.create_project_supply_candidate(detail.project_id,detail.conversation_id,
        selectors=selectors,relations=(relation,),**scope)
    catalog.review_project_supply_candidate(detail.project_id,detail.conversation_id,
        fresh.candidate.candidate_id,fresh.candidate.fingerprint,"APPROVED",**scope)
    with sqlite3.connect(catalog._repository._database) as db:
        before=db.execute("SELECT body FROM conversations WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0]
        db.execute("UPDATE schematic_plan_legacy_snapshots SET plan_id=? WHERE conversation_id=?",
            ("wrong-plan",detail.conversation_id))
    with pytest.raises(ProjectCatalogUnavailable,match="snapshot head"):
        catalog.create_schematic_plan_v2(detail.project_id,detail.conversation_id,
            request_id="conflicting-old-snapshot",expected_head=(None,old_plan.plan_id,old_plan.fingerprint),**scope)
    with sqlite3.connect(catalog._repository._database) as db:
        assert db.execute("SELECT body FROM conversations WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0]==before
        assert db.execute("SELECT COUNT(*) FROM schematic_plan_revisions WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM schematic_plan_revision_heads WHERE conversation_id=?",
            (detail.conversation_id,)).fetchone()[0]==0
