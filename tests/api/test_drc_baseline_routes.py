"""Exact baseline HTTP preparation and bounded existing Receipt projection; no external tools."""
import pytest
from fastapi import FastAPI,Request
from fastapi.testclient import TestClient
from embedded_copilot.api.project_catalog_routes import router as catalog_router
from embedded_copilot.api.copilot_execution_routes import router as execution_router
from embedded_copilot.api.copilot_execution_models import receipt_response
from embedded_copilot.execution_worker.worker import ExecutionWorker
from tests.project_catalog.test_drc_baseline_runtime import setup
from tests.execution_worker.test_drc_baseline_worker import setup as worker_setup
OWNER={"X-Copilot-Requester":"owner-1","X-Environment-Tenant":"tenant-1"}


def app_for(items):
    app=FastAPI();app.state.project_catalog_runtime=items[0];app.state.execution_operation_port=items[5]
    app.state.drc_baseline_available=True;app.state.drc_baseline_revalidator=items[0].revalidate_drc_baseline_operation
    @app.middleware("http")
    async def trace(request:Request,call_next):request.state.trace_id="baseline-test";return await call_next(request)
    app.include_router(catalog_router);app.include_router(execution_router)
    return app


def body(items):return {"import_revision_id":items[3]["import_revision_id"],"import_revision_fingerprint":items[3]["import_revision_fingerprint"],"pcb_relative_path":"native/layout.kicad_pcb"}


def test_default_baseline_unavailable_and_exact_three_fields(tmp_path):
    items=setup(tmp_path);app=app_for(items);items[0].set_drc_baseline_collector(None)
    with TestClient(app) as client:
        url=f"/api/v1/copilot/projects/{items[1]}/eda-native/drc-baseline-operations"
        assert client.post(url,headers=OWNER,json=body(items)|{"argv":[]}).status_code==422
        response=client.post(url,headers=OWNER,json=body(items));assert response.status_code==409
        assert "DRC_BASELINE_UNAVAILABLE" in response.text
    with items[6]._connect() as connection:assert connection.execute("SELECT count(*) FROM v66_objects WHERE kind IN ('operation','approval')").fetchone()[0]==0


def test_route_prepare_wait_then_approval_revalidation_before_grant(tmp_path):
    items=setup(tmp_path);app=app_for(items)
    with TestClient(app) as client:
        response=client.post(f"/api/v1/copilot/projects/{items[1]}/eda-native/drc-baseline-operations",headers=OWNER,json=body(items))
        assert response.status_code==201,response.text
        value=response.json();assert value["status"]=="WAIT_APPROVAL"
        operation=items[5].get_operation(tenant_id="tenant-1",project_id=items[1],operation_id=value["operation_id"])
        app.state.drc_baseline_revalidator=lambda _:False
        response=client.post(f"/api/v1/copilot/operations/{operation.operation_id}/approval",params={"project_id":items[1]},headers=OWNER,json={"reviewer_id":"owner-1","nonce":"baseline-approval","expires_at":operation.expires_at.isoformat()})
        assert response.status_code==409,response.text
    with items[6]._connect() as connection:assert connection.execute("SELECT count(*) FROM v66_objects WHERE kind='approval'").fetchone()[0]==0


def test_existing_receipt_fields_expose_actual_observation_counts_and_safe_ids(tmp_path):
    worker,store,_,op,_,_,_=worker_setup(tmp_path);assert worker.run_once().status.value=="SUCCEEDED"
    receipt=store.get_receipt(op.tenant_id,op.project_id,op.operation_id);view=receipt_response(receipt)
    assert view.evidence_references==receipt.evidence_references
    assert view.safe_diagnostics_summary.endswith("error_count=0 warning_count=1")
    assert "BASELINE_COMPARISON_ONLY" in view.safe_diagnostics_summary
    assert "counts" not in view.model_dump() and "pcb_drc_result" not in view.model_dump()
    forged=receipt.model_copy(update={"safe_diagnostics_summary":"raw private path"})
    with pytest.raises(ValueError):receipt_response(forged)



def test_formal_create_app_defaults_unavailable_without_trusted_collector(tmp_path):
    from embedded_copilot.api.main import create_app
    from embedded_copilot.core.config import Settings
    items=setup(tmp_path);items[0].set_drc_baseline_collector(None)
    app=create_app(settings=Settings(environment="test",project_root=tmp_path/"projects",desktop_state_database=tmp_path/"desktop.sqlite3",_env_file=None),project_catalog_runtime=items[0],execution_operation_port=items[5])
    assert "/api/v1/copilot/projects/{project_id}/eda-native/drc-baseline-operations" in app.openapi()["paths"]
    with TestClient(app) as client:
        response=client.post(f"/api/v1/copilot/projects/{items[1]}/eda-native/drc-baseline-operations",headers=OWNER,json=body(items))
        assert response.status_code==409,response.text
        assert app.state.drc_baseline_available is False
    with items[6]._connect() as connection:assert connection.execute("SELECT count(*) FROM v66_objects WHERE kind IN ('operation','approval')").fetchone()[0]==0



def test_formal_same_composition_trusted_collector_prepares_wait_only(tmp_path):
    from types import SimpleNamespace
    from embedded_copilot.api.main import create_app
    from embedded_copilot.core.config import Settings
    items=setup(tmp_path)
    app=create_app(settings=Settings(environment="test",project_root=tmp_path/"projects",desktop_state_database=tmp_path/"desktop-trusted.sqlite3",_env_file=None),project_catalog_runtime=items[0],execution_operation_port=items[5],workspace_apply_port=SimpleNamespace(_service=items[4].workspace),drc_baseline_collector=items[4])
    with TestClient(app) as client:
        assert app.state.drc_baseline_available is True
        response=client.post(f"/api/v1/copilot/projects/{items[1]}/eda-native/drc-baseline-operations",headers=OWNER,json=body(items))
        assert response.status_code==201,response.text
        assert response.json()["status"]=="WAIT_APPROVAL"
    assert items[8]==[]
    with items[6]._connect() as connection:
        assert connection.execute("SELECT count(*) FROM v66_objects WHERE kind='approval'").fetchone()[0]==0
        assert connection.execute("SELECT count(*) FROM v66_queue").fetchone()[0]==0
