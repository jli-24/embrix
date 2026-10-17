from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from embedded_copilot.execution_worker.adapters import SafeProcessRunner, probe_trusted_tool_version
from embedded_copilot.execution_worker.artifacts import ArtifactStore
from embedded_copilot.execution_worker.contracts import (
    ArtifactIdentity,
    ExecutionTarget,
    TargetBindingStatus,
    OperationStatus,
    OperationType,
)
from embedded_copilot.execution_worker.registry import ExecutionToolRegistry, TrustedToolDiscovery
from embedded_copilot.execution_worker.service import ExecutionOperationService
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from embedded_copilot.execution_worker.worker import ExecutionWorker
from embedded_copilot.simulation_runtime.adapters import SimulationBackedTestAdapter
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
from embedded_copilot.simulation_runtime.ports import SimulationClockPort
from embedded_copilot.simulation_runtime.renode import RenodeGPIOConfig, RenodeSimulationAdapter
from embedded_copilot.simulation_runtime.service import SimulationService
from embedded_copilot.simulation_runtime.stores import (
    EphemeralPreparedSimulationStore,
    EphemeralSimulationResultStore,
    JsonSimulationEvidenceStore,
    JsonSimulationResultStore,
)


NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
PROJECT_ROOT = Path(os.environ.get("EMBRIX_CANONICAL_PROJECT_ROOT", r"D:\EmbeddedProjects\Minimal MCU Control Core"))
RENODE_ROOT = Path(os.environ["EMBRIX_RENODE_ROOT"]) if os.environ.get("EMBRIX_RENODE_ROOT") else None
RENODE_EXE = RENODE_ROOT / "renode.exe" if RENODE_ROOT is not None else None
PLATFORM = RENODE_ROOT / "platforms" / "cpus" / "stm32f103.repl" if RENODE_ROOT is not None else None
ELF = PROJECT_ROOT / "build" / "Minimal_MCU_Control_Core.elf"


class FixedClock(SimulationClockPort):
    def now(self) -> datetime:
        return NOW


class Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    def resolve(self, **_kwargs) -> Path:
        return self.root


class Targets:
    def __init__(self, target: ExecutionTarget) -> None:
        self.target = target

    def get_bound(self, **_kwargs) -> ExecutionTarget:
        return self.target

    def revalidate(self, target: ExecutionTarget) -> ExecutionTarget:
        return target


def _target() -> ExecutionTarget:
    return ExecutionTarget(
        target_id="target-canonical-stm32f103c8t6",
        tenant_id="desktop-tenant",
        project_id="project-cd1617ef-0029-4628-882b-a2cc4bf518dc",
        target_kind="STM32F103C8T6",
        board_family="BLUE_PILL",
        mcu_family="STM32F103",
        stable_identity_hash="sha256:" + "1" * 64,
        candidate_id="candidate-canonical-stm32f103c8t6",
        binding_status=TargetBindingStatus.BOUND,
        bound_by="engineer-1",
        bound_at=NOW,
    )


def test_real_renode_canonical_elf_boot_and_gpio(tmp_path: Path) -> None:
    if os.environ.get("EMBRIX_REAL_RENODE_ACCEPTANCE") != "1":
        pytest.skip("real Renode acceptance requires explicit external-process authorization")
    if RENODE_ROOT is None or RENODE_EXE is None or PLATFORM is None:
        pytest.skip("real Renode acceptance requires explicit EMBRIX_RENODE_ROOT configuration")
    if not PROJECT_ROOT.is_dir() or not ELF.is_file() or not RENODE_EXE.is_file() or not PLATFORM.is_file():
        pytest.skip("canonical golden ELF or installed official Renode is unavailable")

    target = _target()
    workspace_fingerprint = "sha256:" + hashlib.sha256(b"canonical-renode-workspace").hexdigest()
    content = ELF.read_bytes()
    artifact = ArtifactIdentity(
        artifact_id="artifact-canonical-stm32f103-elf",
        tenant_id=target.tenant_id,
        project_id=target.project_id,
        target_id=target.target_id,
        workspace_fingerprint=workspace_fingerprint,
        build_operation_id="build-canonical-stm32f103",
        toolchain_id="ARM_NONE_EABI_GCC",
        toolchain_version="15.3.1",
        build_configuration="RELEASE",
        artifact_type="ELF",
        artifact_sha256="sha256:" + hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        created_at=NOW,
    )
    binding = SimulationExecutionBinding.create(
        requester_id="engineer-1",
        target_id=target.target_id,
        target_fingerprint=target.fingerprint,
        workspace_snapshot_id="snapshot-canonical-renode",
        workspace_fingerprint=workspace_fingerprint,
        proposal_id="proposal-canonical-renode",
        proposal_fingerprint="sha256:" + "2" * 64,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
        nonce="nonce-canonical-renode",
    )
    request = SimulationRequest.create(
        simulation_id="simulation-canonical-renode-golden",
        tenant_id=target.tenant_id,
        project_id=target.project_id,
        backend=SimulationBackend.RENODE,
        firmware_artifact=artifact,
        platform_descriptor="stm32f103",
        scenario="BOOT_AND_GPIO_BEHAVIOR",
        assertions=(
            SimulationAssertion.create(
                assertion_id="renode-exit-zero",
                kind=SimulationAssertionKind.EXIT_STATUS,
                target="renode",
                expected=0,
            ),
        ),
        timeout_seconds=30,
        execution_binding=binding,
    )
    result_store = EphemeralSimulationResultStore()
    persistent_results = JsonSimulationResultStore(tmp_path / "simulation-results")
    evidence_store = JsonSimulationEvidenceStore(tmp_path / "simulation-evidence")
    adapter = RenodeSimulationAdapter(
        executable=RENODE_EXE,
        platform_file=PLATFORM,
        gpio=RenodeGPIOConfig(
            target_kind="STM32F103C8T6",
            platform_descriptor="stm32f103",
            led_port="A",
            led_pin=8,
            button_port="B",
            button_pin=0,
        ),
        runner=SafeProcessRunner(),
        evidence_store=evidence_store,
        clock=FixedClock(),
    )
    tool_discovery = TrustedToolDiscovery(
        trusted_roots=(RENODE_ROOT,),
        trusted_versions={"RENODE_SIMULATION": "1.16.1"},
        explicit_paths={"RENODE_SIMULATION": RENODE_EXE},
        version_probe=probe_trusted_tool_version,
    )
    execution_store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    operation_service = ExecutionOperationService(store=execution_store, clock=FixedClock().now)
    operation_port = ExecutionOperationAdapter(operation_service)
    simulation = SimulationService(
        adapters=(adapter,),
        operations=operation_port,
        prepared_store=EphemeralPreparedSimulationStore(),
        result_store=result_store,
        clock=FixedClock(),
    )
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    artifact_store.put(artifact, content)
    backed = SimulationBackedTestAdapter(
        adapters=(adapter,),
        prepared_store=simulation._prepared,  # noqa: SLF001 - composition test binds the same handoff store
        result_store=result_store,
        clock=FixedClock(),
    )
    worker = ExecutionWorker(
        worker_id="renode-golden-worker",
        store=execution_store,
        registry=ExecutionToolRegistry(tool_discovery),
        workspace_resolver=Workspace(tmp_path),
        targets=Targets(target),
        artifact_store=artifact_store,
        simulation=backed,
        clock=FixedClock().now,
    )

    pending = simulation.evaluate(request)
    assert pending.status is SimulationStatus.PENDING
    operation = simulation.operation_for(request)
    grant = operation_service.issue_approval_for_id(
        tenant_id=target.tenant_id,
        project_id=target.project_id,
        operation_id=operation.operation_id,
        reviewer_id="engineer-1",
        nonce="approval-canonical-renode",
        expires_at=NOW + timedelta(minutes=5),
    )
    operation_service.enqueue_by_id(
        tenant_id=target.tenant_id,
        project_id=target.project_id,
        operation_id=operation.operation_id,
        approval_id=grant.grant_id,
    )
    terminal = worker.run_once()
    result = simulation.evaluate(request)

    assert terminal is not None and terminal.status is OperationStatus.SUCCEEDED, (result.status, result.reason_code, result.assertion_results)
    assert result.status is SimulationStatus.PASS
    assert result.failure_class is SimulationFailureClass.NONE
    assert result.operation_id == operation.operation_id
    receipt = operation_port.get_receipt(
        tenant_id=target.tenant_id,
        project_id=target.project_id,
        operation_id=operation.operation_id,
    )
    assert receipt is not None and receipt.operation_type is OperationType.SIMULATION
    assert any(reference.startswith("simulation-evidence-") for reference in result.artifact_references)
    evidence_id = next(reference for reference in result.artifact_references if reference.startswith("simulation-evidence-"))
    evidence = evidence_store.get(evidence_id)
    assert evidence is not None
    assert evidence.elf_loaded and evidence.reset_handler_reached and evidence.main_reached
    assert len(evidence.observations) == 3
    persistent_results.put(operation.operation_id, result)
    assert persistent_results.get(operation.operation_id) is not None
