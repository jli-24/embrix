"""S02 parse restart recovery across real Python process exits."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from embedded_copilot.execution_worker.contracts import (
    ApprovalStatus,
    CancellationRequest,
    OperationRequest,
    OperationStatus,
)
from embedded_copilot.execution_worker.service import ExecutionOperationService
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from tests.execution_worker.test_s02_evidence_contracts import NOW
from tests.execution_worker.test_s02_evidence_contracts import operation as _operation


def test_committed_write_is_reconciled_in_new_process_without_tool_replay(tmp_path, monkeypatch):
    from embedded_copilot.execution_worker import ExecutionWorker, SchematicWriteAdapter
    from tests.execution_worker.test_s02_offline_wiring import prepare
    catalog, project, session, workspace, execution, _write_id, now = prepare(tmp_path, monkeypatch)
    worker = ExecutionWorker(worker_id="s02-first", store=execution.store, registry=None, workspace_resolver=None, targets=None,
        schematic_write=SchematicWriteAdapter(workspace), design_context_revalidator=catalog.revalidate_circuit_bound_operation, clock=lambda: now)
    assert worker.run_once().status is OperationStatus.SUCCEEDED
    # Model interruption after Worker transaction, before Catalog projection.
    assert catalog.conversation(project.project_id, session.conversation.conversation_id, tenant_id="tenant-1", requester="engineer-1").schematic_production.generation_receipt is None
    script = textwrap.dedent("""
        import json,sys,os
        from pathlib import Path
        from embedded_copilot.project_catalog import ProjectCatalogRuntime
        from embedded_copilot.execution_worker import ExecutionOperationService,SQLiteExecutionStore,ExecutionWorker
        from embedded_copilot.execution_worker.artifacts import ArtifactStore
        from embedded_copilot.project_catalog.source_publication import PublishedComponentSourcePort
        p=Path(sys.argv[1])
        c=ProjectCatalogRuntime(project_root=p/'projects',state_database=p/'desktop.sqlite3',initialize_async=False)
        c.set_hardware_component_source(PublishedComponentSourcePort(
            c._repository, tenant_id='tenant-1', publisher_id='g3-fixture-publisher'))
        e=ExecutionOperationService(store=SQLiteExecutionStore(p/'execution.sqlite3'),artifact_store=ArtifactStore(p/'artifacts'))
        c.set_schematic_evidence_context(execution=e)
        class ForbiddenAdapter:
            def execute(self,op):
                raise AssertionError('no replay permitted')
        w=ExecutionWorker(worker_id='s02-second',store=e.store,registry=None,workspace_resolver=None,targets=None,
            schematic_write=ForbiddenAdapter(),schematic_parse=ForbiddenAdapter(),design_context_revalidator=c.revalidate_circuit_bound_operation)
        assert w.recover_after_restart()==()
        assert c.reconcile_schematic_terminal_receipts()==()
        assert w.run_once() is None
        operations=e.store.list_operations('tenant-1',sys.argv[2],limit=100)
        parse=next(op for op in operations if op.operation_type.value=='KICAD_SCHEMATIC_PARSE')
        detail=c.conversation(sys.argv[2],sys.argv[3],tenant_id='tenant-1',requester='engineer-1')
        print(json.dumps({'pid':os.getpid(),'parse_id':parse.operation_id,'parse_fp':parse.fingerprint,
            'status':e.get_status(tenant_id='tenant-1',project_id=sys.argv[2],operation_id=parse.operation_id).status.value,
            'generation_fp':detail.schematic_production.generation_receipt.fingerprint}))
    """)
    results = []
    for _ in range(2):
        result = subprocess.run([sys.executable, "-c", script, str(tmp_path), project.project_id, session.conversation.conversation_id],
            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30, check=False)
        assert result.returncode == 0, result.stderr
        results.append(json.loads(result.stdout.splitlines()[-1]))
    assert results[0]["pid"] != results[1]["pid"]
    assert results[0]["status"] == results[1]["status"] == "WAIT_APPROVAL"
    for key in ("parse_id", "parse_fp", "generation_fp"):
        assert results[0][key] == results[1][key]


def test_terminal_transaction_interruption_does_not_publish_receipt(tmp_path):
    import hashlib

    from embedded_copilot.execution_worker.artifacts import ArtifactStore
    from embedded_copilot.execution_worker.schematic_xml import SchematicXmlAdapter
    from embedded_copilot.execution_worker.worker import ExecutionWorker
    from tests.execution_worker.test_s02_offline_wiring import XML
    operation = _operation(artifact_fingerprint="sha256:" + hashlib.sha256(b"fixture").hexdigest())
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    service = ExecutionOperationService(store=store, artifact_store=artifacts, clock=lambda: NOW, schematic_evidence_authorizer=lambda op: True)
    request = OperationRequest(operation=operation)
    service.create_operation(request)
    grant = service.issue_approval(operation=request, reviewer_id="owner-1", nonce="transaction-test", expires_at=operation.expires_at)
    service.enqueue(operation=request, approval_id=grant.grant_id)
    class Exporter:
        def export_xml(self, operation, content):
            return 0, XML
    adapter = SchematicXmlAdapter(read_input=lambda op: b"fixture", exporter=Exporter(), tool_executable_sha256="sha256:" + "4" * 64, tool_version="OFFLINE_FAKE", clock=lambda: NOW)
    worker = ExecutionWorker(worker_id="s02-worker", store=store, registry=None, workspace_resolver=None, targets=None,
        artifact_store=artifacts, schematic_parse=adapter, design_context_revalidator=lambda op: True, clock=lambda: NOW)
    with sqlite3.connect(tmp_path / "execution.sqlite3") as connection:
        connection.execute("CREATE TRIGGER interrupt_receipt BEFORE INSERT ON v66_objects WHEN NEW.kind='receipt' BEGIN SELECT RAISE(ABORT, 'synthetic interruption'); END")
    import pytest
    with pytest.raises(sqlite3.DatabaseError):
        worker.run_once()
    assert store.get_receipt(operation.tenant_id, operation.project_id, operation.operation_id) is None
    assert store.get_status(operation.tenant_id, operation.project_id, operation.operation_id).status is OperationStatus.RUNNING
    assert worker.recover_after_restart()[0].status is OperationStatus.RECOVERY_REQUIRED
    assert worker.run_once() is None


@pytest.mark.parametrize("invalidation", ["expiry", "cancellation", "source"])
def test_late_parse_result_cannot_publish_pass(tmp_path, invalidation):
    import hashlib

    from embedded_copilot.execution_worker.artifacts import ArtifactStore
    from embedded_copilot.execution_worker.schematic_xml import SchematicXmlAdapter
    from embedded_copilot.execution_worker.worker import ExecutionWorker
    from tests.execution_worker.test_s02_offline_wiring import XML
    operation = _operation(artifact_fingerprint="sha256:" + hashlib.sha256(b"fixture").hexdigest())
    current_time = [NOW]
    source_current = [True]
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    artifacts = ArtifactStore(tmp_path / "artifacts")
    service = ExecutionOperationService(store=store, artifact_store=artifacts, clock=lambda: current_time[0], schematic_evidence_authorizer=lambda op: True)
    request = OperationRequest(operation=operation)
    service.create_operation(request)
    grant = service.issue_approval(operation=request, reviewer_id="owner-1", nonce="late-test", expires_at=operation.expires_at)
    service.enqueue(operation=request, approval_id=grant.grant_id)
    class Exporter:
        def export_xml(self, operation, content):
            if invalidation == "expiry":
                current_time[0] = operation.expires_at
            elif invalidation == "cancellation":
                service.request_cancellation(CancellationRequest(
                    request_id="cancel-s02", tenant_id=operation.tenant_id,
                    project_id=operation.project_id, operation_id=operation.operation_id,
                    requester_id=operation.requester_id, reason="operator requested",
                    requested_at=NOW,
                ))
            else:
                source_current[0] = False
            return 0, XML
    adapter = SchematicXmlAdapter(read_input=lambda op: b"fixture", exporter=Exporter(), tool_executable_sha256="sha256:" + "4" * 64, tool_version="OFFLINE_FAKE", clock=lambda: current_time[0])
    worker = ExecutionWorker(worker_id="s02-worker", store=store, registry=None, workspace_resolver=None, targets=None,
        artifact_store=artifacts, schematic_parse=adapter, design_context_revalidator=lambda op: source_current[0], clock=lambda: current_time[0])
    expected = {"expiry": OperationStatus.TIMED_OUT, "cancellation": OperationStatus.CANCELLED, "source": OperationStatus.FAILED}
    assert worker.run_once().status is expected[invalidation]
    assert store.get_receipt(operation.tenant_id, operation.project_id, operation.operation_id) is None


def test_s02_running_parse_requires_recovery_in_a_new_process(tmp_path: Path) -> None:
    database = tmp_path / "worker.sqlite3"
    store = SQLiteExecutionStore(database)
    service = ExecutionOperationService(
        store=store,
        clock=lambda: NOW.replace(minute=NOW.minute + 1),
        schematic_evidence_authorizer=lambda _: True,
    )
    operation = _operation()
    request = OperationRequest(operation=operation)
    service.create_operation(request)
    approval = service.issue_approval(
        operation=request,
        reviewer_id="reviewer-1",
        nonce="process-recovery-approval",
        expires_at=NOW.replace(minute=NOW.minute + 5),
    )
    service.enqueue(operation=request, approval_id=approval.grant_id)
    crash_marker = tmp_path / "first-process-adapter-call.json"
    recovery_marker = tmp_path / "second-process-adapter-call.json"
    repository_root = Path(__file__).resolve().parents[2]

    crash_script = textwrap.dedent(
        """
        import json
        import os
        import sys
        from pathlib import Path
        from embedded_copilot.execution_worker.store import SQLiteExecutionStore
        from embedded_copilot.execution_worker.worker import ExecutionWorker
        from tests.execution_worker.test_s02_evidence_contracts import NOW

        class CrashAdapter:
            def execute(self, operation):
                Path(sys.argv[2]).write_text(
                    json.dumps({"pid": os.getpid(), "operation_id": operation.operation_id}),
                    encoding="utf-8",
                )
                os._exit(73)

        worker = ExecutionWorker(
            worker_id="worker-s02-recovery-test",
            store=SQLiteExecutionStore(sys.argv[1]),
            registry=None,
            workspace_resolver=None,
            targets=None,
            schematic_parse=CrashAdapter(),
            design_context_revalidator=lambda _: True,
            clock=lambda: NOW.replace(minute=NOW.minute + 2),
        )
        worker.run_once()
        raise SystemExit(74)
        """
    )
    crashed = subprocess.run(
        [sys.executable, "-c", crash_script, str(database), str(crash_marker)],
        cwd=repository_root,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert crashed.returncode == 73, crashed.stderr
    first_call = json.loads(crash_marker.read_text(encoding="utf-8"))
    assert first_call["operation_id"] == operation.operation_id

    running = store.get_status("tenant-1", "project-1", operation.operation_id)
    persisted_approval = store.get_approval(
        "tenant-1", "project-1", approval.grant_id
    )
    assert running is not None and running.status is OperationStatus.RUNNING
    assert store.get_receipt("tenant-1", "project-1", operation.operation_id) is None
    assert persisted_approval is not None
    assert persisted_approval.status is ApprovalStatus.CONSUMED

    recover_script = textwrap.dedent(
        """
        import json
        import os
        import sys
        from pathlib import Path
        from embedded_copilot.execution_worker.store import SQLiteExecutionStore
        from embedded_copilot.execution_worker.worker import ExecutionWorker
        from tests.execution_worker.test_s02_evidence_contracts import NOW

        class CountingAdapter:
            def execute(self, operation):
                Path(sys.argv[2]).write_text(operation.operation_id, encoding="utf-8")
                raise AssertionError("recovered operation must not execute again")

        store = SQLiteExecutionStore(sys.argv[1])
        worker = ExecutionWorker(
            worker_id="worker-s02-recovery-test",
            store=store,
            registry=None,
            workspace_resolver=None,
            targets=None,
            schematic_parse=CountingAdapter(),
            design_context_revalidator=lambda _: True,
            clock=lambda: NOW.replace(minute=NOW.minute + 3),
        )
        recovered = worker.recover_after_restart()
        status = store.get_status("tenant-1", "project-1", recovered[0].operation_id)
        approval = store.get_approval(
            "tenant-1", "project-1", status.approval_id
        )
        replay_result = worker.run_once()
        print(json.dumps({
            "pid": os.getpid(),
            "recovered_count": len(recovered),
            "recovered_status": recovered[0].status.value,
            "stored_status": status.status.value,
            "receipt_present": store.get_receipt(
                "tenant-1", "project-1", recovered[0].operation_id
            ) is not None,
            "approval_status": approval.status.value,
            "replay_returned_none": replay_result is None,
        }))
        """
    )
    recovered = subprocess.run(
        [sys.executable, "-c", recover_script, str(database), str(recovery_marker)],
        cwd=repository_root,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert recovered.returncode == 0, recovered.stderr
    output_lines = recovered.stdout.splitlines()
    assert output_lines and output_lines[-1].startswith("{")
    recovery = json.loads(output_lines[-1])
    assert recovery["pid"] != first_call["pid"]
    assert recovery["recovered_count"] == 1
    assert recovery["recovered_status"] == OperationStatus.RECOVERY_REQUIRED.value
    assert recovery["stored_status"] == OperationStatus.RECOVERY_REQUIRED.value
    assert recovery["receipt_present"] is False
    assert recovery["approval_status"] == ApprovalStatus.CONSUMED.value
    assert recovery["replay_returned_none"] is True
    assert not recovery_marker.exists()
