from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from embedded_copilot.execution_worker.contracts import (
    CancellationRequest,
    OperationRequest,
    OperationStatus,
    SchematicErcOperation,
    SchematicParseOperation,
)
from embedded_copilot.execution_worker.service import (
    ExecutionOperationService,
    ExecutionRejected,
)
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from embedded_copilot.execution_worker.worker import ExecutionWorker

NOW = datetime(2026, 10, 4, tzinfo=UTC)
FP = "sha256:" + "a" * 64


def _operation(kind: str) -> SchematicParseOperation | SchematicErcOperation:
    common = {
        "operation_id": f"guard-{kind}",
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "requester_id": "engineer-1",
        "target_id": "project-root",
        "target_fingerprint": FP,
        "workspace_snapshot_id": "snapshot-1",
        "workspace_fingerprint": FP,
        "proposal_id": "plan-1",
        "proposal_fingerprint": FP,
        "created_at": NOW,
        "expires_at": NOW + timedelta(minutes=1),
        "nonce": "nonce-1",
        "conversation_id": "conversation-1",
    }
    if kind == "parse":
        return SchematicParseOperation(
            **common,
            source_write_operation_id="write-1",
            source_write_receipt_fingerprint=FP,
            schematic_plan_id="plan-1",
            schematic_plan_fingerprint=FP,
            artifact_fingerprint=FP,
        )
    return SchematicErcOperation(
        **common,
        schematic_artifact_ref="hardware/main.kicad_sch",
        schematic_artifact_fingerprint=FP,
        source_operation_id="write-1",
        source_operation_receipt_fingerprint=FP,
        target_relative_path="hardware/main.kicad_sch",
        source_parse_operation_id="parse-1",
        source_parse_receipt_fingerprint=FP,
    )


def _service(tmp_path: Path, kind: str):
    clock = [NOW]
    store = SQLiteExecutionStore(tmp_path / f"{kind}.sqlite3")
    service = ExecutionOperationService(
        store=store,
        clock=lambda: clock[0],
        schematic_evidence_authorizer=lambda _op: True,
    )
    operation = _operation(kind)
    request = OperationRequest(operation=operation)
    service.create_operation(request)
    return store, service, operation, request, clock


def _queued(tmp_path: Path, kind: str):
    store, service, operation, request, clock = _service(tmp_path, kind)
    approval = service.issue_approval(
        operation=request,
        reviewer_id="reviewer-1",
        nonce="approval-1",
        expires_at=NOW + timedelta(minutes=2),
    )
    service.enqueue(operation=request, approval_id=approval.grant_id)
    return store, service, operation, clock


class _NoAdapterCall:
    def execute(self, _operation):
        raise AssertionError("expired or cancelled schematic evidence reached adapter")


def _worker(store: SQLiteExecutionStore, operation, clock):
    return ExecutionWorker(
        worker_id="worker-guard",
        store=store,
        registry=None,
        workspace_resolver=None,
        targets=None,
        schematic_parse=_NoAdapterCall(),
        schematic_erc=_NoAdapterCall(),
        design_context_revalidator=lambda _op: True,
        allowed_operation_types=(operation.operation_type,),
        clock=lambda: clock[0],
    )


def _assert_no_receipt(
    store: SQLiteExecutionStore, operation, expected: OperationStatus
):
    status = store.get_status(
        operation.tenant_id, operation.project_id, operation.operation_id
    )
    assert status is not None and status.status is expected
    assert status.receipt_fingerprint is None
    assert (
        store.get_receipt(
            operation.tenant_id, operation.project_id, operation.operation_id
        )
        is None
    )


@pytest.mark.parametrize("kind", ["parse", "erc"])
def test_fresh_approval_cannot_revive_expired_operation(
    tmp_path: Path, kind: str
) -> None:
    store, service, operation, request, clock = _service(tmp_path, kind)
    clock[0] = NOW + timedelta(minutes=1)
    with pytest.raises(ExecutionRejected, match="expired"):
        service.issue_approval(
            operation=request,
            reviewer_id="reviewer-1",
            nonce="fresh-approval",
            expires_at=NOW + timedelta(minutes=2),
        )
    _assert_no_receipt(store, operation, OperationStatus.WAIT_APPROVAL)


@pytest.mark.parametrize("kind", ["parse", "erc"])
def test_expired_operation_cannot_enqueue_with_valid_grant(
    tmp_path: Path, kind: str
) -> None:
    store, service, operation, request, clock = _service(tmp_path, kind)
    approval = service.issue_approval(
        operation=request,
        reviewer_id="reviewer-1",
        nonce="approval-1",
        expires_at=NOW + timedelta(minutes=2),
    )
    clock[0] = NOW + timedelta(minutes=1)
    with pytest.raises(ExecutionRejected, match="expired"):
        service.enqueue(operation=request, approval_id=approval.grant_id)
    _assert_no_receipt(store, operation, OperationStatus.WAIT_APPROVAL)


@pytest.mark.parametrize("kind", ["parse", "erc"])
def test_queued_expiry_times_out_before_adapter(tmp_path: Path, kind: str) -> None:
    store, _, operation, clock = _queued(tmp_path, kind)
    clock[0] = NOW + timedelta(minutes=1)
    result = _worker(store, operation, clock).run_once()
    assert result is not None and result.status is OperationStatus.TIMED_OUT
    _assert_no_receipt(store, operation, OperationStatus.TIMED_OUT)


@pytest.mark.parametrize("kind", ["parse", "erc"])
def test_queued_cancel_terminates_before_adapter(tmp_path: Path, kind: str) -> None:
    store, service, operation, clock = _queued(tmp_path, kind)
    service.request_cancellation(
        CancellationRequest(
            request_id="cancel-1",
            tenant_id=operation.tenant_id,
            project_id=operation.project_id,
            operation_id=operation.operation_id,
            requester_id=operation.requester_id,
            reason="user requested",
            requested_at=clock[0],
        )
    )
    result = _worker(store, operation, clock).run_once()
    assert result is not None and result.status is OperationStatus.CANCELLED
    _assert_no_receipt(store, operation, OperationStatus.CANCELLED)


@pytest.mark.parametrize("kind", ["parse", "erc"])
@pytest.mark.parametrize("change", ["expiry", "cancel"])
def test_start_to_dispatch_drift_never_calls_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    change: str,
) -> None:
    store, service, operation, clock = _queued(tmp_path, kind)
    original_start = store.start_operation_transition

    def start_then_change(*args, **kwargs):
        running = original_start(*args, **kwargs)
        assert running is not None
        if change == "expiry":
            clock[0] = NOW + timedelta(minutes=1)
        else:
            service.request_cancellation(
                CancellationRequest(
                    request_id="cancel-during-start",
                    tenant_id=operation.tenant_id,
                    project_id=operation.project_id,
                    operation_id=operation.operation_id,
                    requester_id=operation.requester_id,
                    reason="user requested",
                    requested_at=clock[0],
                )
            )
        return running

    monkeypatch.setattr(store, "start_operation_transition", start_then_change)
    expected = (
        OperationStatus.TIMED_OUT if change == "expiry" else OperationStatus.CANCELLED
    )
    result = _worker(store, operation, clock).run_once()
    assert result is not None and result.status is expected
    _assert_no_receipt(store, operation, expected)


@pytest.mark.parametrize("kind", ["parse", "erc"])
@pytest.mark.parametrize("change", ["expiry", "cancel"])
def test_schematic_revalidator_to_adapter_drift_is_blocked(
    tmp_path: Path,
    change: str,
    kind: str,
) -> None:
    store, service, operation, clock = _queued(tmp_path, kind)
    calls = 0

    def revalidate(_operation):
        nonlocal calls
        calls += 1
        if calls == 2:
            if change == "expiry":
                clock[0] = NOW + timedelta(minutes=1)
            else:
                service.request_cancellation(
                    CancellationRequest(
                        request_id="cancel-during-revalidate",
                        tenant_id=operation.tenant_id,
                        project_id=operation.project_id,
                        operation_id=operation.operation_id,
                        requester_id=operation.requester_id,
                        reason="user requested",
                        requested_at=clock[0],
                    )
                )
        return True

    worker = _worker(store, operation, clock)
    worker._design_context_revalidator = revalidate
    expected = (
        OperationStatus.TIMED_OUT if change == "expiry" else OperationStatus.CANCELLED
    )
    result = worker.run_once()
    assert result is not None and result.status is expected
    _assert_no_receipt(store, operation, expected)


class _FailedAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, _operation):
        from embedded_copilot.execution_worker.ports import AdapterResult

        self.calls += 1
        return AdapterResult("FAILED", "synthetic offline failure")


@pytest.mark.parametrize("kind", ["parse", "erc"])
@pytest.mark.parametrize("change", ["expiry", "cancel"])
def test_result_revalidator_drift_prevents_canonical_receipt(
    tmp_path: Path,
    kind: str,
    change: str,
) -> None:
    store, service, operation, clock = _queued(tmp_path, kind)
    adapter = _FailedAdapter()
    calls = 0

    def revalidate(_operation):
        nonlocal calls
        calls += 1
        if calls == 3:
            if change == "expiry":
                clock[0] = NOW + timedelta(minutes=1)
            else:
                service.request_cancellation(
                    CancellationRequest(
                        request_id="cancel-during-result-revalidate",
                        tenant_id=operation.tenant_id,
                        project_id=operation.project_id,
                        operation_id=operation.operation_id,
                        requester_id=operation.requester_id,
                        reason="user requested",
                        requested_at=clock[0],
                    )
                )
        return True

    worker = _worker(store, operation, clock)
    worker._design_context_revalidator = revalidate
    worker._schematic_parse = adapter
    worker._schematic_erc = adapter
    expected = (
        OperationStatus.TIMED_OUT if change == "expiry" else OperationStatus.CANCELLED
    )
    result = worker.run_once()
    assert adapter.calls == 1
    assert calls == 3
    assert result is not None and result.status is expected
    _assert_no_receipt(store, operation, expected)


@pytest.mark.parametrize("kind", ["parse", "erc"])
@pytest.mark.parametrize("change", ["expiry", "cancel"])
def test_terminal_success_is_overridden_by_latest_schematic_state(
    tmp_path: Path,
    kind: str,
    change: str,
) -> None:
    store, service, operation, clock = _queued(tmp_path, kind)
    if change == "expiry":
        clock[0] = NOW + timedelta(minutes=1)
    else:
        service.request_cancellation(
            CancellationRequest(
                request_id="cancel-before-terminal",
                tenant_id=operation.tenant_id,
                project_id=operation.project_id,
                operation_id=operation.operation_id,
                requester_id=operation.requester_id,
                reason="user requested",
                requested_at=clock[0],
            )
        )
    current = store.get_status(
        operation.tenant_id, operation.project_id, operation.operation_id
    )
    assert current is not None
    result = _worker(store, operation, clock)._terminal(
        operation,
        current,
        OperationStatus.SUCCEEDED,
        "synthetic result",
        "ADAPTER_SUCCEEDED",
    )
    expected = (
        OperationStatus.TIMED_OUT if change == "expiry" else OperationStatus.CANCELLED
    )
    assert result.status is expected
    _assert_no_receipt(store, operation, expected)


@pytest.mark.parametrize("kind", ["parse", "erc"])
@pytest.mark.parametrize("change", ["expiry", "cancel"])
def test_store_cas_rejects_schematic_success_after_cancel_or_expiry(
    tmp_path: Path,
    kind: str,
    change: str,
) -> None:
    store, service, operation, clock = _queued(tmp_path, kind)
    if change == "expiry":
        clock[0] = NOW + timedelta(minutes=1)
    else:
        service.request_cancellation(
            CancellationRequest(
                request_id="cancel-before-store-commit",
                tenant_id=operation.tenant_id,
                project_id=operation.project_id,
                operation_id=operation.operation_id,
                requester_id=operation.requester_id,
                reason="user requested",
                requested_at=clock[0],
            )
        )
    current = store.get_status(
        operation.tenant_id, operation.project_id, operation.operation_id
    )
    assert current is not None
    success = current.model_validate(
        {
            **current.model_dump(mode="python"),
            "status": OperationStatus.SUCCEEDED,
            "updated_at": clock[0],
            "fingerprint": "sha256:" + "0" * 64,
        }
    )
    with pytest.raises(ValueError, match="cancel|expir|state"):
        store.terminal_transition(
            operation, current, success, reason_code="ADAPTER_SUCCEEDED"
        )
    _assert_no_receipt(store, operation, current.status)


def _legacy_erc() -> SchematicErcOperation:
    paired = _operation("erc")
    return SchematicErcOperation.model_validate(
        {
            **paired.model_dump(mode="python"),
            "operation_id": "guard-legacy-erc",
            "source_parse_operation_id": None,
            "source_parse_receipt_fingerprint": None,
            "fingerprint": "sha256:" + "0" * 64,
        }
    )


def _persist_legacy_erc(tmp_path: Path):
    from embedded_copilot.execution_worker.contracts import (
        OperationStatusView,
        RecoveryState,
    )

    store = SQLiteExecutionStore(tmp_path / "legacy-erc.sqlite3")
    operation = _legacy_erc()
    status = OperationStatusView(
        operation_id=operation.operation_id,
        tenant_id=operation.tenant_id,
        project_id=operation.project_id,
        operation_type=operation.operation_type,
        status=OperationStatus.WAIT_APPROVAL,
        approval_id=None,
        worker_id=None,
        recovery_state=RecoveryState.NONE,
        cancellation_requested=False,
        updated_at=NOW,
        safe_detail="historical legacy ERC",
    )
    store.create_operation_transition(operation, status, occurred_at=NOW)
    return store, operation, status


def _historical_legacy_grant(
    store: SQLiteExecutionStore, operation: SchematicErcOperation
):
    from embedded_copilot.execution_worker.contracts import (
        ApprovalStatus,
        OperationApprovalGrant,
        approval_purpose_for_operation,
    )

    grant = OperationApprovalGrant(
        grant_id="approval-legacy-erc",
        tenant_id=operation.tenant_id,
        project_id=operation.project_id,
        requester_id=operation.requester_id,
        approval_purpose=approval_purpose_for_operation(operation.operation_type),
        operation_id=operation.operation_id,
        operation_type=operation.operation_type,
        operation_fingerprint=operation.fingerprint,
        proposal_id=operation.proposal_id,
        proposal_fingerprint=operation.proposal_fingerprint,
        target_id=operation.target_id,
        target_fingerprint=operation.target_fingerprint,
        workspace_snapshot_id=operation.workspace_snapshot_id,
        workspace_fingerprint=operation.workspace_fingerprint,
        reviewer_id="reviewer-1",
        status=ApprovalStatus.ISSUED,
        expires_at=NOW + timedelta(minutes=2),
        nonce="historical-approval",
        issued_at=NOW,
    )
    store.issue_approval_transition(operation, grant)
    return grant


def test_legacy_erc_cannot_receive_fresh_approval(tmp_path: Path) -> None:
    import hashlib

    store, operation, original_status = _persist_legacy_erc(tmp_path)
    service = ExecutionOperationService(
        store=store, clock=lambda: NOW, schematic_evidence_authorizer=lambda _op: True
    )
    expires_at = NOW + timedelta(minutes=2)
    grant_id = (
        "approval-"
        + hashlib.sha256(
            f"{operation.fingerprint}|reviewer-1|fresh-legacy|{expires_at.isoformat()}".encode()
        ).hexdigest()[:32]
    )
    with pytest.raises(ExecutionRejected, match="canonical parse evidence"):
        service.issue_approval_for_id(
            tenant_id=operation.tenant_id,
            project_id=operation.project_id,
            operation_id=operation.operation_id,
            reviewer_id="reviewer-1",
            nonce="fresh-legacy",
            expires_at=expires_at,
        )
    assert (
        store.get_status(
            operation.tenant_id, operation.project_id, operation.operation_id
        )
        == original_status
    )
    assert (
        store.get_approval(operation.tenant_id, operation.project_id, grant_id) is None
    )


@pytest.mark.parametrize("expired", [False, True])
def test_legacy_erc_cannot_enqueue_historical_grant(
    tmp_path: Path, expired: bool
) -> None:
    store, operation, original_status = _persist_legacy_erc(tmp_path)
    grant = _historical_legacy_grant(store, operation)
    now = NOW + timedelta(minutes=1) if expired else NOW
    service = ExecutionOperationService(
        store=store, clock=lambda: now, schematic_evidence_authorizer=lambda _op: True
    )
    with pytest.raises(ExecutionRejected, match="canonical parse evidence"):
        service.enqueue_by_id(
            tenant_id=operation.tenant_id,
            project_id=operation.project_id,
            operation_id=operation.operation_id,
            approval_id=grant.grant_id,
        )
    assert (
        store.get_status(
            operation.tenant_id, operation.project_id, operation.operation_id
        )
        == original_status
    )
    assert (
        store.get_approval(operation.tenant_id, operation.project_id, grant.grant_id)
        == grant
    )
    assert (
        store.claim_next(
            worker_id="worker-legacy-check",
            lease_seconds=60,
            now=now,
            operation_types=(operation.operation_type,),
        )
        is None
    )


def test_historical_queued_legacy_erc_never_calls_adapter(tmp_path: Path) -> None:
    store, operation, _ = _persist_legacy_erc(tmp_path)
    grant = _historical_legacy_grant(store, operation)
    store.enqueue_transition(operation, grant, occurred_at=NOW)
    clock = [NOW]
    status = _worker(store, operation, clock).run_once()
    assert status is not None and status.status is OperationStatus.FAILED
    assert status.safe_detail == "schematic ERC canonical parse evidence unavailable"
    _assert_no_receipt(store, operation, OperationStatus.FAILED)
