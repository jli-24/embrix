"""Canonical C1/full Workspace/PRL baseline preparation over synthetic private fixtures."""
from pathlib import Path
import pytest
from embedded_copilot.engineering_workspace_apply import WorkspaceApplyService,FixedWorkspaceTargetResolver,SQLiteWorkspaceApplyStore,SystemWorkspaceClock
from embedded_copilot.execution_worker.drc_baseline import PcbDrcBaselineCollector
from embedded_copilot.execution_worker.artifacts import ArtifactStore
from embedded_copilot.execution_worker.service import ExecutionOperationService
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from embedded_copilot.execution_worker.contracts import OperationStatus
from embedded_copilot.project_catalog import ProjectCatalogUnavailable,ProjectCatalogConflict,ProjectCatalogForbidden,ProjectCatalogRejected
from tests.project_catalog.test_c2i_native_move_runtime import _catalog
from tests.execution_worker.test_drc_baseline_collector import fixture


def setup(tmp_path):
    catalog,project,board,revision=_catalog(tmp_path)
    helper=tmp_path/"helper";helper.mkdir();prototype,_,calls,raw=fixture(helper)
    raw["source"]=board.name
    workspace=WorkspaceApplyService(resolver=FixedWorkspaceTargetResolver(board.parent.parent,tenant_id="tenant-1",project_id=project),store=SQLiteWorkspaceApplyStore(tmp_path/"workspace.sqlite3"),state_root=tmp_path/"workspace-state",clock=SystemWorkspaceClock())
    catalog.set_workspace_snapshot_source(workspace)
    collector=PcbDrcBaselineCollector(workspace=workspace,policy=prototype.policy,tool_bundle=prototype._bundle,rules=prototype._rules,dependencies=prototype._dependencies,environment=prototype._environment,report_root=tmp_path/"baseline-reports",runner=prototype._runner,clock=catalog._now)
    catalog.set_drc_baseline_collector(collector)
    store=SQLiteExecutionStore(tmp_path/"execution.sqlite3");artifacts=ArtifactStore(tmp_path/"artifacts")
    service=ExecutionOperationService(store=store,artifact_store=artifacts,clock=catalog._now,drc_baseline_revalidator=catalog.revalidate_drc_baseline_operation)
    return catalog,project,board,revision,collector,service,store,artifacts,calls


def prepare(items,path="native/layout.kicad_pcb"):
    catalog,project,board,revision,collector,service,store,artifacts,calls=items
    status=catalog.create_drc_baseline_operation(project,tenant_id="tenant-1",requester="owner-1",import_revision_id=revision["import_revision_id"],import_revision_fingerprint=revision["import_revision_fingerprint"],pcb_relative_path=path,execution=service)
    return service.get_operation(tenant_id="tenant-1",project_id=project,operation_id=status.operation_id),status


def test_prepare_creates_only_wait_approval_and_explicit_reprepare_new_operation(tmp_path):
    items=setup(tmp_path);op,status=prepare(items);op2,status2=prepare(items)
    assert status.status is OperationStatus.WAIT_APPROVAL and status2.status is OperationStatus.WAIT_APPROVAL
    assert op.operation_id!=op2.operation_id and op.proposal_fingerprint==op2.proposal_fingerprint
    assert op.pcb_relative_path=="native/layout.kicad_pcb" and op.prl_relative_path=="native/layout.kicad_prl"
    assert items[0].revalidate_drc_baseline_operation(op) and items[8]==[]
    with items[6]._connect() as connection:
        assert connection.execute("SELECT count(*) FROM v66_objects WHERE kind='approval'").fetchone()[0]==0
        assert connection.execute("SELECT count(*) FROM v66_queue").fetchone()[0]==0


@pytest.mark.parametrize("changed",("../notes.txt","layout.kicad_prl","layout.kicad_pcb"))
def test_full_workspace_prl_and_c1_mutation_invalidate_prepared_operation(tmp_path,changed):
    items=setup(tmp_path);op,_=prepare(items)
    (items[2].parent/changed).write_bytes(b"changed")
    assert not items[0].revalidate_drc_baseline_operation(op)


@pytest.mark.parametrize("path",("../layout.kicad_pcb","native/missing.kicad_pcb","native/layout.txt","native\\layout.kicad_pcb"))
def test_selector_is_exact_manifest_member_no_latest_or_first_pcb(tmp_path,path):
    items=setup(tmp_path)
    with pytest.raises((ProjectCatalogRejected,ProjectCatalogConflict)):prepare(items,path)
    with items[6]._connect() as connection:assert connection.execute("SELECT count(*) FROM v66_objects WHERE kind='operation'").fetchone()[0]==0


def test_default_unavailable_creates_no_operation_grant_or_queue(tmp_path):
    items=setup(tmp_path);items[0].set_drc_baseline_collector(None)
    with pytest.raises(ProjectCatalogUnavailable,match="DRC_BASELINE_UNAVAILABLE"):prepare(items)
    with items[6]._connect() as connection:assert connection.execute("SELECT count(*) FROM v66_objects WHERE kind IN ('operation','approval')").fetchone()[0]==0


def test_owner_changed_during_actual_workspace_io_blocks_prepare(tmp_path,monkeypatch):
    items=setup(tmp_path);catalog=items[0];original=catalog.get_project_for_requester;count=[0]
    def owner(*args,**kwargs):
        count[0]+=1
        if count[0]>=4:raise ProjectCatalogForbidden("owner changed")
        return original(*args,**kwargs)
    monkeypatch.setattr(catalog,"get_project_for_requester",owner)
    with pytest.raises(ProjectCatalogForbidden):prepare(items)
    with items[6]._connect() as connection:assert connection.execute("SELECT count(*) FROM v66_objects WHERE kind='operation'").fetchone()[0]==0


@pytest.mark.parametrize("field",("parser_ruleset_id","parser_ruleset_fingerprint","supported_subset_id","supported_subset_fingerprint","source_set_fingerprint","manifest_fingerprint"))
def test_exact_parser_subset_and_source_claims_cannot_be_rebound(tmp_path,field):
    from embedded_copilot.execution_worker.contracts import PcbDrcBaselineOperation,drc_baseline_proposal_fingerprint
    items=setup(tmp_path);op,_=prepare(items);data=op.model_dump(mode="python",exclude={"fingerprint"})
    data[field]="changed" if field.endswith("_id") else "sha256:"+"b"*64
    data["proposal_fingerprint"]=drc_baseline_proposal_fingerprint(data)
    assert not items[0].revalidate_drc_baseline_operation(PcbDrcBaselineOperation(**data))


def test_c1_revision_a_to_b_to_a_does_not_restore_old_active_identity(tmp_path):
    items=setup(tmp_path);catalog,project,board,revision,*_=items;op,_=prepare(items)
    original=board.read_bytes();head=revision
    for blob in (original+b"\n",original):
        board.write_bytes(blob)
        preview=catalog.create_eda_import_preview(project,tenant_id="tenant-1",requester="owner-1",backend="KICAD",native_entry_relative_path="native/layout.kicad_pcb",selected_native_entries=(),expected_head_revision_id=head["import_revision_id"],expected_head_revision_fingerprint=head["import_revision_fingerprint"])
        head=catalog.commit_eda_import_preview(project,preview["preview_id"],preview["preview_fingerprint"],tenant_id="tenant-1",requester="owner-1")
    assert head["import_revision_id"]!=revision["import_revision_id"]
    assert not catalog.revalidate_drc_baseline_operation(op)



def test_canonical_catalog_worker_single_fake_complete_observation(tmp_path):
    from embedded_copilot.execution_worker.worker import ExecutionWorker
    items=setup(tmp_path);catalog,project,board,revision,collector,service,store,artifacts,calls=items
    op,_=prepare(items)
    grant=service.issue_approval_for_id(tenant_id=op.tenant_id,project_id=project,operation_id=op.operation_id,reviewer_id=op.requester_id,nonce="baseline-grant",expires_at=op.expires_at)
    service.enqueue_by_id(tenant_id=op.tenant_id,project_id=project,operation_id=op.operation_id,approval_id=grant.grant_id)
    worker=ExecutionWorker(worker_id="baseline",store=store,registry=None,workspace_resolver=None,targets=None,artifact_store=artifacts,drc_baseline_collector=collector,drc_baseline_revalidator=catalog.revalidate_drc_baseline_operation,recovery_workspace=collector.workspace,clock=catalog._now)
    status=worker.run_once()
    assert status.status is OperationStatus.SUCCEEDED and len(calls)==1
    receipt=store.get_receipt(op.tenant_id,project,op.operation_id)
    assert receipt.safe_diagnostics_summary.endswith("error_count=0 warning_count=1")
    assert catalog.revalidate_drc_baseline_operation(op) and worker.run_once() is None
