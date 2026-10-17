from __future__ import annotations

from datetime import UTC, datetime, timedelta

from embedded_copilot.execution_worker.contracts import (
    ArtifactIdentity,
    ExecutionTarget,
    OperationStatus,
    TargetBindingStatus,
)
from embedded_copilot.execution_worker.registry import (
    ExecutionToolRegistry,
    TrustedToolDiscovery,
)
from embedded_copilot.execution_worker.service import ExecutionOperationService
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from embedded_copilot.execution_worker.worker import ExecutionWorker
from embedded_copilot.simulation_runtime.adapters import (
    FakeSimulationAdapter,
    SimulationBackedTestAdapter,
)
from embedded_copilot.simulation_runtime.contracts import (
    SimulationAssertion,
    SimulationAssertionKind,
    SimulationBackend,
    SimulationExecutionBinding,
    SimulationFailureClass,
    SimulationRequest,
    SimulationStatus,
)
from embedded_copilot.simulation_runtime.operation import ExecutionOperationAdapter
from embedded_copilot.simulation_runtime.service import SimulationService
from embedded_copilot.simulation_runtime.stores import (
    EphemeralPreparedSimulationStore,
    EphemeralSimulationResultStore,
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class Workspace:
    def __init__(self, root) -> None:
        self.root = root

    def resolve(self, **_kwargs):
        return self.root


class Targets:
    def __init__(self, target: ExecutionTarget) -> None:
        self.target = target

    def get_bound(self, **_kwargs):
        return self.target

    def revalidate(self, target):
        return target


def target() -> ExecutionTarget:
    return ExecutionTarget(
        target_id="target-1",
        tenant_id="tenant-1",
        project_id="project-1",
        target_kind="STM32F446RE",
        board_family="NUCLEO-F446RE",
        mcu_family="STM32F446RE",
        stable_identity_hash="probe-1",
        candidate_id="candidate-1",
        binding_status=TargetBindingStatus.BOUND,
        bound_by="engineer-1",
        bound_at=NOW,
    )


def request(bound_target: ExecutionTarget) -> SimulationRequest:
    workspace_fingerprint = "sha256:" + "3" * 64
    artifact = ArtifactIdentity(
        artifact_id="artifact-1",
        tenant_id="tenant-1",
        project_id="project-1",
        target_id=bound_target.target_id,
        workspace_fingerprint=workspace_fingerprint,
        build_operation_id="build-1",
        toolchain_id="CMAKE_NINJA_BUILD",
        toolchain_version="3",
        build_configuration="DEBUG",
        artifact_type="ELF",
        artifact_sha256="sha256:" + "4" * 64,
        size_bytes=128,
        created_at=NOW,
    )
    return SimulationRequest.create(
        simulation_id="simulation-1",
        tenant_id="tenant-1",
        project_id="project-1",
        backend=SimulationBackend.FAKE,
        firmware_artifact=artifact,
        platform_descriptor="stm32f446re-platform",
        scenario="uart-boot-smoke",
        assertions=(
            SimulationAssertion.create(
                assertion_id="uart-ready",
                kind=SimulationAssertionKind.UART_CONTAINS,
                target="uart0",
                expected="READY",
            ),
        ),
        timeout_seconds=30,
        execution_binding=SimulationExecutionBinding.create(
            requester_id="engineer-1",
            target_id=bound_target.target_id,
            target_fingerprint=bound_target.fingerprint,
            workspace_snapshot_id="snapshot-1",
            workspace_fingerprint=workspace_fingerprint,
            proposal_id="proposal-1",
            proposal_fingerprint="sha256:" + "5" * 64,
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=10),
            nonce="simulation-nonce-1",
        ),
    )


def runtime(tmp_path, *, fake: FakeSimulationAdapter | None = None):
    bound_target = target()
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    operation_service = ExecutionOperationService(store=store, clock=FixedClock().now)
    operation_port = ExecutionOperationAdapter(operation_service)
    prepared = EphemeralPreparedSimulationStore()
    results = EphemeralSimulationResultStore()
    adapter = fake or FakeSimulationAdapter(clock=FixedClock())
    simulation = SimulationService(
        adapters=(adapter,),
        operations=operation_port,
        prepared_store=prepared,
        result_store=results,
        clock=FixedClock(),
    )
    worker = ExecutionWorker(
        worker_id="simulation-worker-1",
        store=store,
        registry=ExecutionToolRegistry(TrustedToolDiscovery()),
        workspace_resolver=Workspace(tmp_path),
        targets=Targets(bound_target),
        test=SimulationBackedTestAdapter(
            adapters=(adapter,),
            prepared_store=prepared,
            result_store=results,
            clock=FixedClock(),
        ),
        clock=FixedClock().now,
    )
    return bound_target, store, operation_service, simulation, worker


def approve_and_run(operation_service, simulation, worker, checked_request):
    operation = simulation.operation_for(checked_request)
    grant = operation_service.issue_approval_for_id(
        tenant_id=operation.tenant_id,
        project_id=operation.project_id,
        operation_id=operation.operation_id,
        reviewer_id="engineer-1",
        nonce="approval-nonce-1",
        expires_at=NOW + timedelta(minutes=5),
    )
    operation_service.enqueue_by_id(
        tenant_id=operation.tenant_id,
        project_id=operation.project_id,
        operation_id=operation.operation_id,
        approval_id=grant.grant_id,
    )
    return worker.run_once()


def test_service_maps_one_simulation_to_one_existing_test_operation(tmp_path) -> None:
    bound_target, store, operation_service, simulation, worker = runtime(tmp_path)
    checked_request = request(bound_target)

    pending = simulation.evaluate(checked_request)
    operation = simulation.operation_for(checked_request)

    assert pending.status is SimulationStatus.PENDING
    assert pending.reason_code == "SIMULATION_WAIT_APPROVAL"
    assert operation.operation_type.value == "SIMULATION"
    assert operation.operation_family == "SIMULATION"
    assert operation.backend == "FAKE"
    assert (
        store.get_status("tenant-1", "project-1", operation.operation_id).status
        is OperationStatus.WAIT_APPROVAL
    )

    terminal = approve_and_run(operation_service, simulation, worker, checked_request)
    result = simulation.evaluate(checked_request)

    assert terminal is not None and terminal.status is OperationStatus.SUCCEEDED
    assert result.status is SimulationStatus.PASS
    assert result.failure_class is SimulationFailureClass.NONE
    assert result.operation_id == operation.operation_id
    assert all(item.passed for item in result.assertion_results)
    assert len(store.list_operations(tenant_id="tenant-1", project_id="project-1")) == 1


def test_engineering_timeout_is_worker_failure_but_keeps_engineering_cause(
    tmp_path,
) -> None:
    fake = FakeSimulationAdapter(
        clock=FixedClock(),
        outcomes=(
            (
                SimulationStatus.TIMEOUT,
                SimulationFailureClass.ENGINEERING,
                "EXPECTED_UART_NOT_OBSERVED",
            ),
        ),
    )
    bound_target, _store, operation_service, simulation, worker = runtime(
        tmp_path, fake=fake
    )
    checked_request = request(bound_target)
    simulation.evaluate(checked_request)
    terminal = approve_and_run(operation_service, simulation, worker, checked_request)

    result = simulation.evaluate(checked_request)

    assert terminal is not None and terminal.status is OperationStatus.FAILED
    assert result.status is SimulationStatus.TIMEOUT
    assert result.failure_class is SimulationFailureClass.ENGINEERING


def test_infrastructure_timeout_preserves_timeout_cause(tmp_path) -> None:
    fake = FakeSimulationAdapter(
        clock=FixedClock(),
        outcomes=(
            (
                SimulationStatus.TIMEOUT,
                SimulationFailureClass.INFRASTRUCTURE,
                "BACKEND_START_TIMEOUT",
            ),
        ),
    )
    bound_target, _store, operation_service, simulation, worker = runtime(
        tmp_path, fake=fake
    )
    checked_request = request(bound_target)
    simulation.evaluate(checked_request)
    terminal = approve_and_run(operation_service, simulation, worker, checked_request)

    result = simulation.evaluate(checked_request)

    assert terminal is not None and terminal.status is OperationStatus.TIMED_OUT
    assert result.status is SimulationStatus.TIMEOUT
    assert result.failure_class is SimulationFailureClass.INFRASTRUCTURE


def test_cancelled_result_is_not_promoted_to_engineering_failure(tmp_path) -> None:
    fake = FakeSimulationAdapter(
        clock=FixedClock(),
        outcomes=(
            (
                SimulationStatus.CANCELLED,
                SimulationFailureClass.CANCELLED,
                "SIMULATION_CANCELLED",
            ),
        ),
    )
    bound_target, _store, operation_service, simulation, worker = runtime(
        tmp_path, fake=fake
    )
    checked_request = request(bound_target)
    simulation.evaluate(checked_request)
    approve_and_run(operation_service, simulation, worker, checked_request)

    result = simulation.evaluate(checked_request)

    assert result.status is SimulationStatus.CANCELLED
    assert result.failure_class is SimulationFailureClass.CANCELLED
