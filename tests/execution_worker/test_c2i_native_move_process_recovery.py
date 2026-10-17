"""C2-I restart recovery across real Python process exits."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

from embedded_copilot.execution_worker.contracts import (
    ApprovalStatus,
    OperationRequest,
    OperationStatus,
)
from embedded_copilot.execution_worker.service import ExecutionOperationService
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from tests.execution_worker.test_c2i_native_move_contract import NOW, _operation


def test_c2i_running_operation_requires_recovery_in_a_new_process(tmp_path: Path) -> None:
    database = tmp_path / "worker.sqlite3"
    store = SQLiteExecutionStore(database)
    service = ExecutionOperationService(
        store=store,
        clock=lambda: NOW.replace(minute=NOW.minute + 1),
        native_move_authorizer=lambda _: True,
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
        from tests.execution_worker.test_c2i_native_move_contract import NOW

        class CrashAdapter:
            def execute(self, operation):
                Path(sys.argv[2]).write_text(
                    json.dumps({"pid": os.getpid(), "operation_id": operation.operation_id}),
                    encoding="utf-8",
                )
                os._exit(73)

        worker = ExecutionWorker(
            worker_id="worker-c2i-recovery-test",
            store=SQLiteExecutionStore(sys.argv[1]),
            registry=None,
            workspace_resolver=None,
            targets=None,
            native_move_adapter=CrashAdapter(),
            native_move_revalidator=lambda _: True,
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
        from tests.execution_worker.test_c2i_native_move_contract import NOW

        class CountingAdapter:
            def execute(self, operation):
                Path(sys.argv[2]).write_text(operation.operation_id, encoding="utf-8")
                raise AssertionError("recovered operation must not execute again")

        store = SQLiteExecutionStore(sys.argv[1])
        worker = ExecutionWorker(
            worker_id="worker-c2i-recovery-test",
            store=store,
            registry=None,
            workspace_resolver=None,
            targets=None,
            native_move_adapter=CountingAdapter(),
            native_move_revalidator=lambda _: True,
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
