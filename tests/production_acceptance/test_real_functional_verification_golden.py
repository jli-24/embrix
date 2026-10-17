from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from embedded_copilot.engineering_requirements.models import RequirementContext
from embedded_copilot.execution_worker.adapters import (
    SafeProcessRunner,
    probe_trusted_tool_version,
)
from embedded_copilot.execution_worker.artifacts import ArtifactStore
from embedded_copilot.execution_worker.contracts import (
    ArtifactIdentity,
    ExecutionTarget,
    OperationStatus,
    OperationType,
    TargetBindingStatus,
)
from embedded_copilot.execution_worker.registry import (
    ExecutionToolRegistry,
    TrustedToolDiscovery,
)
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
from embedded_copilot.simulation_runtime.renode import (
    RenodeGPIOConfig,
    RenodeSimulationAdapter,
)
from embedded_copilot.simulation_runtime.service import SimulationService
from embedded_copilot.simulation_runtime.stores import (
    EphemeralPreparedSimulationStore,
    EphemeralSimulationResultStore,
    JsonSimulationEvidenceStore,
    JsonSimulationResultStore,
)
from embedded_copilot.verification_loop import (
    ExpectedBehavior,
    FunctionalVerificationRequest,
    FunctionalVerificationService,
    JsonFunctionalVerificationStore,
    L4GateStatus,
)
from embedded_copilot.virtual_assembly import (
    CanonicalVirtualAssemblyAdapter,
    VirtualAssemblyService,
)

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
PROJECT_ID = "project-cd1617ef-0029-4628-882b-a2cc4bf518dc"
PROJECT_ROOT = Path(os.environ.get("EMBRIX_CANONICAL_PROJECT_ROOT", r"D:\EmbeddedProjects\Minimal MCU Control Core"))
TOOLCHAIN_BIN = Path(os.environ.get("EMBRIX_CANONICAL_TOOLCHAIN_BIN", r"D:\EmbeddedToolchains\ArmGNU\15.3.rel1\bin"))
RENODE_ROOT = Path(os.environ["EMBRIX_RENODE_ROOT"]) if os.environ.get("EMBRIX_RENODE_ROOT") else None
RENODE_EXE = RENODE_ROOT / "renode.exe" if RENODE_ROOT is not None else None
PLATFORM = RENODE_ROOT / "platforms" / "cpus" / "stm32f103.repl" if RENODE_ROOT is not None else None
ELF = PROJECT_ROOT / "build" / "Minimal_MCU_Control_Core.elf"
STATE_DB = Path(__file__).resolve().parents[2] / ".data" / "canonical-golden-project.sqlite3"


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
        project_id=PROJECT_ID,
        target_kind="STM32F103C8T6",
        board_family="BLUE_PILL",
        mcu_family="STM32F103",
        stable_identity_hash="sha256:" + "1" * 64,
        candidate_id="candidate-canonical-stm32f103c8t6",
        binding_status=TargetBindingStatus.BOUND,
        bound_by="engineer-1",
        bound_at=NOW,
    )


def _requirements() -> RequirementContext:
    with sqlite3.connect(STATE_DB) as connection:
        row = connection.execute(
            "select body from conversations where conversation_id = ?",
            ("conversation-ba96322b-c712-4485-9bfb-5901fa0d5878",),
        ).fetchone()
    if row is None:
        raise RuntimeError("canonical planning conversation is unavailable")
    body = json.loads(row[0])
    return RequirementContext.model_validate(body["requirement_context"], strict=False)


def _run_real_renode(source, assembly, tmp_path: Path):
    target = _target()
    content = ELF.read_bytes()
    artifact_sha256 = "sha256:" + hashlib.sha256(content).hexdigest()
    workspace_fingerprint = "sha256:" + hashlib.sha256(b"canonical-functional-workspace").hexdigest()
    artifact = ArtifactIdentity(
        artifact_id=assembly.elf_artifact_id or "artifact-elf",
        tenant_id=target.tenant_id,
        project_id=target.project_id,
        target_id=target.target_id,
        workspace_fingerprint=workspace_fingerprint,
        build_operation_id="build-canonical-stm32f103",
        toolchain_id="ARM_NONE_EABI_GCC",
        toolchain_version="15.3.1",
        build_configuration="CANONICAL",
        artifact_type="ELF",
        artifact_sha256=artifact_sha256,
        size_bytes=len(content),
        created_at=NOW,
    )
    binding = SimulationExecutionBinding.create(
        requester_id="engineer-1",
        target_id=target.target_id,
        target_fingerprint=target.fingerprint,
        workspace_snapshot_id="snapshot-canonical-functional",
        workspace_fingerprint=workspace_fingerprint,
        proposal_id="proposal-canonical-functional",
        proposal_fingerprint="sha256:" + "2" * 64,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
        nonce="nonce-canonical-functional",
    )
    scenario = source.consistency_input.simulation_specification.scenarios[0]
    request = SimulationRequest.create(
        simulation_id="simulation-canonical-functional-golden",
        tenant_id=target.tenant_id,
        project_id=target.project_id,
        backend=SimulationBackend.RENODE,
        firmware_artifact=artifact,
        platform_descriptor="stm32f103",
        scenario=scenario.scenario_id,
        assertions=(
            SimulationAssertion.create(
                assertion_id="renode-functional-exit-zero",
                kind=SimulationAssertionKind.EXIT_STATUS,
                target="renode",
                expected=0,
            ),
        ),
        timeout_seconds=30,
        execution_binding=binding,
    )
    result_store = EphemeralSimulationResultStore()
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
    discovery = TrustedToolDiscovery(
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
        prepared_store=simulation._prepared,
        result_store=result_store,
        clock=FixedClock(),
    )
    worker = ExecutionWorker(
        worker_id="renode-functional-golden-worker",
        store=execution_store,
        registry=ExecutionToolRegistry(discovery),
        workspace_resolver=Workspace(tmp_path),
        targets=Targets(target),
        artifact_store=artifact_store,
        simulation=backed,
        clock=FixedClock().now,
    )
    assert simulation.evaluate(request).status is SimulationStatus.PENDING
    operation = simulation.operation_for(request)
    grant = operation_service.issue_approval_for_id(
        tenant_id=target.tenant_id,
        project_id=target.project_id,
        operation_id=operation.operation_id,
        reviewer_id="engineer-1",
        nonce="approval-canonical-functional",
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
    assert terminal is not None and terminal.status is OperationStatus.SUCCEEDED
    assert result.status is SimulationStatus.PASS
    assert result.failure_class is SimulationFailureClass.NONE
    assert result.operation_id == operation.operation_id
    receipt = operation_port.get_receipt(
        tenant_id=target.tenant_id,
        project_id=target.project_id,
        operation_id=operation.operation_id,
    )
    assert receipt is not None and receipt.operation_type is OperationType.SIMULATION
    evidence_id = next(reference for reference in result.artifact_references if reference.startswith("simulation-evidence-"))
    evidence = evidence_store.get(evidence_id)
    assert evidence is not None
    assert evidence.elf_loaded and evidence.cpu_started and evidence.reset_handler_reached and evidence.main_reached
    assert len(evidence.observations) == 3
    assert evidence.configuration_observations == ("gpio_configuration:a_crh=0x00000002,b_crl=0x00000004",)
    persistent_results = JsonSimulationResultStore(tmp_path / "simulation-results")
    persistent_results.put(operation.operation_id, result)
    assert persistent_results.get(operation.operation_id) is not None
    return request, result, evidence


def test_real_functional_verification_closes_l4_with_real_lineage(tmp_path: Path) -> None:
    if os.environ.get("EMBRIX_REAL_RENODE_ACCEPTANCE") != "1":
        pytest.skip("real functional verification acceptance requires explicit external-process authorization")
    if RENODE_ROOT is None or RENODE_EXE is None or PLATFORM is None:
        pytest.skip("real Renode acceptance requires explicit EMBRIX_RENODE_ROOT configuration")
    if not PROJECT_ROOT.is_dir() or not TOOLCHAIN_BIN.is_dir() or not ELF.is_file():
        pytest.skip("canonical project, toolchain, or ELF is unavailable")
    if not RENODE_EXE.is_file() or not PLATFORM.is_file() or not STATE_DB.is_file():
        pytest.skip("official Renode or persisted canonical planning state is unavailable")

    source = CanonicalVirtualAssemblyAdapter(
        PROJECT_ROOT,
        project_id=PROJECT_ID,
        toolchain_bin=TOOLCHAIN_BIN,
    ).collect()
    assembly = VirtualAssemblyService().assemble(source)
    consistency = source.consistency_input
    simulation_specification = consistency.simulation_specification
    verification_specification = consistency.verification_specification
    assert simulation_specification is not None and verification_specification is not None
    scenario = simulation_specification.scenarios[0]
    case = verification_specification.cases[0]
    request, result, evidence = _run_real_renode(source, assembly, tmp_path)
    input_states = tuple(item.removeprefix("button_").upper() for item in scenario.inputs)
    output_states = tuple(item.removeprefix("led_").upper() for item in scenario.expected_outputs)
    expected_behavior = (
        ExpectedBehavior.create(
            behavior_id="input_stimulus",
            category="simulation_scenario",
            expected=input_states,
            source_refs=(case.case_id, scenario.scenario_id),
        ),
        ExpectedBehavior.create(
            behavior_id="output_response",
            category="verification_case",
            expected=output_states,
            source_refs=(case.case_id, scenario.scenario_id),
        ),
        ExpectedBehavior.create(
            behavior_id="functional_scenario",
            category="verification_case",
            expected=tuple(zip(input_states, output_states, strict=True)),
            source_refs=(case.case_id, scenario.scenario_id),
        ),
    )
    functional_request = FunctionalVerificationRequest.create(
        project_id=PROJECT_ID,
        requirement_specification=_requirements(),
        design_truth=consistency.truth,
        firmware_specification=consistency.firmware_specification,
        verification_specification=verification_specification,
        simulation_specification=simulation_specification,
        virtual_assembly=assembly,
        simulation_scenario=scenario,
        simulation_request=request,
        simulation_result=result,
        simulation_evidence=evidence,
        expected_behavior=expected_behavior,
    )
    result_store = JsonFunctionalVerificationStore(tmp_path / "functional-verification")
    verified = FunctionalVerificationService(store=result_store, clock=FixedClock().now).verify_and_persist(functional_request)
    assert verified.result.status.value == "PASS"
    assert verified.result.l4_gate is L4GateStatus.VIRTUAL_FUNCTIONAL_VERIFIED
    assert verified.result.lineage_fresh and verified.result.evidence_complete
    assert verified.result.model_scope.sufficient
    assert all(check.status.value in {"PASS", "NOT_APPLICABLE"} for check in verified.result.checks)
    assert verified.evidence.observed_from_backend and not verified.evidence.synthetic
    assert verified.evidence.backend is SimulationBackend.RENODE
    assert result_store.get(PROJECT_ID, verified.result.verification_result_id) == verified

