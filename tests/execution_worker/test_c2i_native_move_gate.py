"""C2-I default execution gate, using only an isolated worker database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from embedded_copilot.execution_worker.service import (
    ExecutionOperationService,
    ExecutionRejected,
    ExecutionUnavailable,
)
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from tests.execution_worker.test_c2i_native_move_contract import _operation


def test_c2i_production_service_rejects_native_move_without_gate(tmp_path: Path) -> None:
    service = ExecutionOperationService(store=SQLiteExecutionStore(tmp_path / "worker.sqlite3"))
    request = SimpleNamespace(operation=_operation())
    with pytest.raises(ExecutionUnavailable):
        service.create_operation(request)
    with pytest.raises(ExecutionUnavailable):
        service.issue_approval(
            operation=request, reviewer_id="reviewer-1", nonce="nonce-1",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    with pytest.raises(ExecutionUnavailable):
        service.enqueue(operation=request, approval_id="approval-1")


def test_c2i_injected_gate_must_return_literal_true(tmp_path: Path) -> None:
    calls: list[object] = []

    def denied(operation: object) -> bool:
        calls.append(operation)
        return False

    service = ExecutionOperationService(
        store=SQLiteExecutionStore(tmp_path / "worker.sqlite3"),
        native_move_authorizer=denied,
    )
    operation = _operation()
    with pytest.raises(ExecutionRejected):
        service._require_native_move_authorized(operation)
    assert calls == [operation]

    allowed = ExecutionOperationService(
        store=SQLiteExecutionStore(tmp_path / "other-worker.sqlite3"),
        native_move_authorizer=lambda _: True,
    )
    allowed._require_native_move_authorized(operation)
