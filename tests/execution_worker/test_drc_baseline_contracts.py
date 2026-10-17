"""Synthetic typed contract probes; no actual tool or C1 acceptance."""
from datetime import UTC,datetime,timedelta
import hashlib,json
import pytest
from pydantic import ValidationError
from embedded_copilot.execution_worker.contracts import (PcbDrcBaselineCollectorPolicy,PcbDrcBaselineOperation,OperationType,OperationRequest,ApprovalPurpose,approval_purpose_for_operation,drc_baseline_proposal_fingerprint)
FP="sha256:"+"a"*64

def _policy():
    return PcbDrcBaselineCollectorPolicy(collector_id="collector",collector_version="v1",tool_id="fake-fixed-cli",tool_version="10.0.5",executable_sha256=FP,tool_bundle_manifest_fingerprint=FP,environment_content_fingerprint=FP,rules_manifest_fingerprint=FP,dependency_manifest_fingerprint=FP,timeout_seconds=30)

def _operation():
    values=dict(operation_id="baseline-1",tenant_id="tenant",project_id="project",requester_id="user",target_id="project-root",target_fingerprint=FP,workspace_snapshot_id="snapshot",workspace_fingerprint=FP,proposal_id="proposal",operation_type=OperationType.KICAD_PCB_DRC_BASELINE,created_at=datetime(2026,10,11,tzinfo=UTC),expires_at=datetime(2026,10,11,tzinfo=UTC)+timedelta(minutes=5),nonce="nonce",import_revision_id="revision",import_revision_fingerprint=FP,source_set_fingerprint=FP,manifest_fingerprint=FP,parser_ruleset_id="rules",parser_ruleset_fingerprint=FP,supported_subset_id="subset",supported_subset_fingerprint=FP,native_entry_relative_path="board.kicad_pcb",pcb_relative_path="board.kicad_pcb",pcb_sha256=FP,collector_policy=_policy(),prl_relative_path="board.kicad_prl",prl_before_sha256=None)
    values["proposal_fingerprint"]=drc_baseline_proposal_fingerprint(values)
    return PcbDrcBaselineOperation(**values)

def test_independent_baseline_type_roundtrips_without_fake_write_or_output_identity():
    op=_operation()
    assert OperationRequest.model_validate_json(OperationRequest(operation=op).model_dump_json()).operation==op
    assert approval_purpose_for_operation(op.operation_type) is ApprovalPurpose.PROPOSAL
    assert not {"source_write_operation_id","source_write_receipt_fingerprint","baseline_report","artifact"}&set(op.model_dump())
    material={key:op.model_dump(mode="json")[key] for key in ("operation_type","tenant_id","project_id","requester_id","target_id","target_fingerprint","workspace_snapshot_id","workspace_fingerprint","import_revision_id","import_revision_fingerprint","source_set_fingerprint","manifest_fingerprint","parser_ruleset_id","parser_ruleset_fingerprint","supported_subset_id","supported_subset_fingerprint","native_entry_relative_path","pcb_relative_path","pcb_sha256","prl_relative_path","prl_before_sha256")}
    material.update(schema_version=1,collector_policy_fingerprint=op.collector_policy.fingerprint)
    assert op.proposal_fingerprint=="sha256:"+hashlib.sha256(json.dumps(material,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()

@pytest.mark.parametrize("change",({"baseline_report":{}},{"argv":[]},{"environment":{}},{"ignored_checks":("ignored",)},{"included_severities":("warning",)},{"normalizer_version":"other"},{"raw_version":"other"},{"timeout_seconds":0},{"prl_policy":"ALLOW_REVIEWED_KEYS"}))
def test_policy_rejects_outputs_client_dispatch_and_loosened_scope(change):
    with pytest.raises((ValidationError,ValueError,TypeError)):PcbDrcBaselineCollectorPolicy.model_validate(_policy().model_dump(mode="python")|change)

@pytest.mark.parametrize("field",("import_revision_fingerprint","source_set_fingerprint","manifest_fingerprint","parser_ruleset_fingerprint","supported_subset_fingerprint","pcb_sha256","prl_before_sha256","pcb_relative_path"))
def test_proposal_binds_complete_c1_workspace_and_prl_inputs(field):
    data=_operation().model_dump(mode="python",exclude={"fingerprint"})
    data[field]="other.kicad_pcb" if field=="pcb_relative_path" else "sha256:"+"b"*64
    with pytest.raises((ValidationError,ValueError)):PcbDrcBaselineOperation.model_validate(data)

@pytest.mark.parametrize("path",("../board.kicad_pcb","/board.kicad_pcb","C:/board.kicad_pcb","dir\\board.kicad_pcb","board.txt"))
def test_operation_rejects_unsafe_or_nonpcb_selector(path):
    data=_operation().model_dump(mode="python",exclude={"fingerprint"});data["pcb_relative_path"]=path
    with pytest.raises((ValidationError,ValueError,TypeError)):PcbDrcBaselineOperation.model_validate(data)

def test_default_service_cannot_create_or_sign_a_baseline_without_canonical_owner(tmp_path):
    from embedded_copilot.execution_worker.service import ExecutionOperationService,ExecutionUnavailable
    from embedded_copilot.execution_worker.store import SQLiteExecutionStore
    op=_operation();store=SQLiteExecutionStore(tmp_path/"state.sqlite3");service=ExecutionOperationService(store=store,clock=lambda:op.created_at)
    with pytest.raises(ExecutionUnavailable,match="DRC_BASELINE_UNAVAILABLE"):service.create_operation(OperationRequest(operation=op))
    assert store.get_operation(op.tenant_id,op.project_id,op.operation_id) is None


def test_store_restart_and_grant_enqueue_revalidate_before_any_approval_consumption(tmp_path):
    from embedded_copilot.execution_worker.service import ExecutionOperationService,ExecutionRejected
    from embedded_copilot.execution_worker.store import SQLiteExecutionStore
    from embedded_copilot.execution_worker.contracts import ApprovalStatus,OperationStatus
    op=_operation();database=tmp_path/"state.sqlite3";live=[True];store=SQLiteExecutionStore(database)
    service=ExecutionOperationService(store=store,clock=lambda:op.created_at,drc_baseline_revalidator=lambda _:live[0])
    service.create_operation(OperationRequest(operation=op));assert service.get_status(tenant_id=op.tenant_id,project_id=op.project_id,operation_id=op.operation_id).status is OperationStatus.WAIT_APPROVAL
    assert SQLiteExecutionStore(database).get_operation(op.tenant_id,op.project_id,op.operation_id)==op
    live[0]=False
    with pytest.raises(ExecutionRejected):service.issue_approval_for_id(tenant_id=op.tenant_id,project_id=op.project_id,operation_id=op.operation_id,reviewer_id="reviewer",nonce="approval-nonce",expires_at=op.expires_at)
    with store._connect() as connection:assert connection.execute("SELECT COUNT(*) FROM v66_objects WHERE kind='approval'").fetchone()[0]==0
    live[0]=True;grant=service.issue_approval_for_id(tenant_id=op.tenant_id,project_id=op.project_id,operation_id=op.operation_id,reviewer_id="reviewer",nonce="approval-nonce",expires_at=op.expires_at)
    live[0]=False
    with pytest.raises(ExecutionRejected):service.enqueue_by_id(tenant_id=op.tenant_id,project_id=op.project_id,operation_id=op.operation_id,approval_id=grant.grant_id)
    assert store.get_approval(op.tenant_id,op.project_id,grant.grant_id).status is ApprovalStatus.ISSUED
    assert store.get_status(op.tenant_id,op.project_id,op.operation_id).status is OperationStatus.WAIT_APPROVAL
