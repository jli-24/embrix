"""Formal HTTP pair/current map/record profile; actual libs with explicit offline canonical write/XML seam."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from embedded_copilot.api.desktop_identity import LocalDevelopmentIdentityAdapter
from embedded_copilot.api.project_catalog_routes import router
from tests.project_catalog.test_p3_library_observation_collector import _collector,_run_collector_worker


def test_formal_library_readback_selector_and_profile2_record_currentness(tmp_path,monkeypatch):
    catalog,old_op,xml,reviewed,root=_collector(tmp_path,monkeypatch)
    catalog._schematic_evidence_execution._clock=catalog._now
    app=FastAPI();app.include_router(router);app.state.project_catalog_runtime=catalog
    app.state.desktop_identity_provider=LocalDevelopmentIdentityAdapter(tenant_id=old_op.tenant_id,requester=old_op.requester_id)
    @app.middleware("http")
    async def trace(request,call_next):
        request.state.trace_id="p3-profile-api-test"
        return await call_next(request)
    base=f"/api/v1/copilot/projects/{old_op.project_id}/conversations/{old_op.conversation_id}"
    pair=dict(library_map_evidence_id=reviewed.evidence.evidence_id,library_map_evidence_fingerprint=reviewed.evidence.fingerprint)
    with TestClient(app) as client:
        path=base+"/schematic-evidence/operations"
        assert client.post(path,json={"kind":"PARSE",**pair}).status_code==422
        assert client.post(path,json={"kind":"READBACK","library_map_evidence_id":pair["library_map_evidence_id"]}).status_code==422
        response=client.post(path,json={"kind":"READBACK",**pair})
        assert response.status_code==200,response.text
        payload=response.json()
        # Response is canonical operation DTO, with no execution side effect.
        from embedded_copilot.execution_worker.contracts import SchematicReadBackOperation
        op=SchematicReadBackOperation.model_validate_json(__import__("json").dumps(payload))
        assert op.library_map_evidence_id==reviewed.evidence.evidence_id
        receipt=_run_collector_worker(catalog,op,xml)
        monkeypatch.setattr(catalog,"schematic_xml_receipt",lambda *a,**kw:receipt)
        monkeypatch.setattr("embedded_copilot.engineering_copilot.schematic_evidence.compare_plan_xml",lambda *a:False)
        detail=catalog.conversation(op.project_id,op.conversation_id,tenant_id=op.tenant_id,requester=op.requester_id)
        plan=detail.schematic_planning.approved_plan
        values=dict(intent_id=plan.circuit_intent_id,intent_revision=plan.circuit_intent_revision,
            intent_fingerprint=plan.circuit_intent_fingerprint,plan_id=plan.plan_id,plan_fingerprint=plan.fingerprint,
            artifact_fingerprint=op.artifact_fingerprint,generation_operation_id=op.source_write_operation_id,
            readback_operation_id=op.operation_id,library_map_evidence_ids=[reviewed.evidence.evidence_id],
            library_map_evidence_fingerprints=[reviewed.evidence.fingerprint])
        record_path=base+"/schematic-verifications"
        assert client.post(record_path,json={**values,"library_map_evidence_fingerprints":None}).status_code==422
        response=client.post(record_path,json=values)
        assert response.status_code==200,response.text
        record=response.json()["record"]
        assert record["profile_version"]==2 and len(record["claims"])==8 and record["aggregate_status"]=="BLOCKED"
        app.state.desktop_identity_provider=LocalDevelopmentIdentityAdapter(tenant_id=op.tenant_id,requester="other")
        assert client.post(record_path,json=values).status_code==403
        app.state.desktop_identity_provider=LocalDevelopmentIdentityAdapter(tenant_id=op.tenant_id,requester=op.requester_id)
        (root/"symbol.kicad_sym").write_bytes(b"changed original")
        assert catalog.schematic_verification(op.project_id,record["verification_id"],tenant_id=op.tenant_id,requester=op.requester_id)[1]=="STALE"
