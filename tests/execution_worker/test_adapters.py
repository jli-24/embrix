from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from embedded_copilot.execution_worker.adapters import UnavailableTestAdapter
from embedded_copilot.execution_worker.contracts import (
    ExecutionTarget,
    TargetBindingStatus,
    TestOperation as ExecutionTestOperation,
)
from embedded_copilot.execution_worker.ports import (
    AdapterResult,
    ExecutionContext,
    TestAdapterPort as ExecutionTestAdapterPort,
)


NOW = datetime.now(UTC)
FP = "sha256:" + "1" * 64


def _operation() -> ExecutionTestOperation:
    return ExecutionTestOperation(
        operation_id="operation-test-1",
        tenant_id="tenant-1",
        project_id="project-1",
        requester_id="requester-1",
        target_id="target-1",
        target_fingerprint=FP,
        workspace_snapshot_id="snapshot-1",
        workspace_fingerprint=FP,
        proposal_id="proposal-1",
        proposal_fingerprint=FP,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
        nonce="nonce-test-1",
        test_suite_id="firmware-smoke",
        test_profile_id="default-validation",
    )


def _target() -> ExecutionTarget:
    return ExecutionTarget(
        target_id="target-1",
        tenant_id="tenant-1",
        project_id="project-1",
        target_kind="STM32F446RE",
        board_family="NUCLEO-F446RE",
        mcu_family="STM32F446RE",
        stable_identity_hash="probe-hash-1",
        candidate_id="candidate-1",
        binding_status=TargetBindingStatus.BOUND,
        bound_by="operator-1",
        bound_at=NOW,
    )


def test_unavailable_test_adapter_fails_closed_without_execution_details(tmp_path) -> None:
    operation = _operation()
    context = ExecutionContext(
        workspace_root=tmp_path / "workspace",
        state_root=tmp_path / "state",
        target=_target(),
        tool=None,
        cancellation=object(),
    )

    result = UnavailableTestAdapter().execute(operation, context)

    assert result == AdapterResult(
        status="UNAVAILABLE",
        summary="firmware test adapter unavailable",
    )
    serialized = str(asdict(result)).casefold()
    assert "command" not in serialized
    assert "environment" not in serialized
    assert str(tmp_path).casefold() not in serialized


def test_test_adapter_port_accepts_an_injected_typed_fake(tmp_path) -> None:
    class FakeTestAdapter:
        def execute(
            self,
            operation: ExecutionTestOperation,
            context: ExecutionContext,
        ) -> AdapterResult:
            assert operation.operation_type.value == "TEST"
            assert context.target.target_id == operation.target_id
            return AdapterResult("SUCCEEDED", "synthetic test result")

    adapter = FakeTestAdapter()
    context = ExecutionContext(
        workspace_root=tmp_path / "workspace",
        state_root=tmp_path / "state",
        target=_target(),
        tool=None,
        cancellation=object(),
    )

    assert isinstance(adapter, ExecutionTestAdapterPort)
    assert adapter.execute(_operation(), context).status == "SUCCEEDED"
