import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from embedded_copilot.execution_worker.contracts import (
    ArtifactIdentity,
    ExecutionTarget,
    HardwareBringupOperation,
    OperationStatus,
    OperationType,
    TargetBindingStatus,
)
from embedded_copilot.execution_worker.ports import ExecutionContext
from embedded_copilot.execution_worker.registry import (
    ExecutionToolRegistry,
    ToolDefinition,
    ToolFamily,
    ToolResolution,
    TrustedToolDiscovery,
)
from embedded_copilot.execution_worker.service import ExecutionOperationService
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from embedded_copilot.execution_worker.worker import ExecutionWorker
from embedded_copilot.hardware_runtime import (
    BringupResult,
    EvidenceStore,
    HardwareBringupAdapter,
    HardwareBringupGateResult,
    HardwareBringupRuntimeService,
    HardwareConnection,
    HardwareConnectionStatus,
    HardwareLineage,
    HardwareSessionStatus,
    HardwareToolResult,
    OpenOCDHardwareBackend,
    PyOCDHardwareBackend,
    compare_hardware_observations,
    evaluate_hardware_bringup_gate,
)
 
FP = "sha256:" + "a" * 64
BIN_SHA = "sha256:" + hashlib.sha256(b"x").hexdigest()
NOW = datetime(2026, 9, 9, tzinfo=UTC)


def artifact() -> ArtifactIdentity:
    return ArtifactIdentity(
        artifact_id="firmware-elf-1",
        tenant_id="tenant-1",
        project_id="project-1",
        target_id="target-1",
        workspace_fingerprint=FP,
        build_operation_id="build-1",
        toolchain_id="ARM_NONE_EABI_GCC",
        toolchain_version="15.3.1",
        build_configuration="Release",
        artifact_type="BIN",
        artifact_sha256=BIN_SHA,
        size_bytes=1,
        created_at=NOW,
    )


def operation(action: str = "FLASH", *, artifact_value: ArtifactIdentity | None = None) -> HardwareBringupOperation:
    return HardwareBringupOperation(
        operation_id=f"bringup-{action.lower()}",
        tenant_id="tenant-1",
        project_id="project-1",
        requester_id="engineer-1",
        target_id="target-1",
        target_fingerprint=FP,
        workspace_snapshot_id="snapshot-1",
        workspace_fingerprint=FP,
        proposal_id="proposal-1",
        proposal_fingerprint=FP,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        nonce="nonce-1",
        action=action,
        tool_id="OPENOCD_FLASH",
        probe_type="OPENOCD",
        probe_serial="probe-1",
        connection_id="connection-1",
        hardware_session_id="session-1",
        firmware_sha256=artifact_value.artifact_sha256 if artifact_value is not None else FP,
        pcb_sha256=FP,
        schematic_sha256=FP,
        elf_sha256=FP,
        build_id="build-id-1",
        design_truth_id="design-truth-1",
        artifact=artifact_value,
        baud_rate=115200 if action == "UART" else None,
        uart_port_binding="COM7" if action == "UART" else None,
        expected_patterns=("System Init OK",) if action == "UART" else (),
    )


def context(root: Path | None = None) -> ExecutionContext:
    target = ExecutionTarget(
        target_id="target-1",
        tenant_id="tenant-1",
        project_id="project-1",
        target_kind="STM32F103C8T6",
        board_family="blue-pill",
        mcu_family="STM32F1",
        stable_identity_hash="probe-1",
        candidate_id="candidate-1",
        binding_status=TargetBindingStatus.BOUND,
        bound_by="engineer-1",
        bound_at=NOW,
    )
    definition = ToolDefinition("OPENOCD_FLASH", "0.12", ToolFamily.OPENOCD, OperationType.FLASH, ("STM32F103C8T6",), ("openocd.exe",), 30)
    class Artifacts:
        def read(self, value):
            return b"x"

    workspace_root = root or Path.cwd()
    return ExecutionContext(
        workspace_root=workspace_root,
        state_root=workspace_root / ".state",
        target=target,
        tool=ToolResolution(definition, Path("C:/tools/openocd.exe"), True, "AVAILABLE", "TOOL_RESOLVED"),
        cancellation=object(),
        artifact_store=Artifacts(),
    )


def test_hardware_operation_is_stm32f103_only_and_flash_is_artifact_bound() -> None:
    with pytest.raises(ValueError):
        operation("FLASH", artifact_value=None)
    with pytest.raises(ValueError):
        HardwareBringupOperation.model_validate({**operation("FLASH", artifact_value=artifact()).model_dump(mode="python"), "target_mcu": "STM32F446RE"})
    with pytest.raises(ValueError):
        HardwareBringupOperation.model_validate({**operation("CONNECT").model_dump(mode="python"), "probe_type": "PYOCD"})


def test_gate_never_passes_with_missing_or_unknown_hardware_checks() -> None:
    result = evaluate_hardware_bringup_gate({"hardware_connected": BringupResult.PASS})
    assert isinstance(result, HardwareBringupGateResult)
    assert result.status == "BLOCKED"
    assert all(item.status is not BringupResult.PASS for item in result.checks if item.name != "hardware_connected")


def test_gate_pass_requires_all_eight_checks() -> None:
    statuses = {name: BringupResult.PASS for name in (
        "hardware_connected", "target_detected", "firmware_flash", "boot_verification",
        "uart_capture", "artifact_binding", "evidence_persistence", "restart_recovery",
    )}
    assert evaluate_hardware_bringup_gate(statuses).status == "PASS"


def test_evidence_store_survives_restart_and_preserves_identity(tmp_path: Path) -> None:
    connection = HardwareConnection(
        connection_id="connection-1",
        project_id="project-1",
        probe_type="OPENOCD",
        probe_serial="probe-1",
        target_mcu="STM32F103C8T6",
        connection_time=NOW,
        firmware_hash=FP,
        pcb_hash=FP,
        schematic_hash=FP,
        elf_hash=FP,
        build_id="build-id-1",
        design_truth_id="design-truth-1",
        hardware_session_id="session-1",
        status=HardwareConnectionStatus.CONNECTED,
        device_id="device-1234",
    )
    first = EvidenceStore(tmp_path)
    first.save(connection)
    second = EvidenceStore(tmp_path)
    restored = second.get("connection-1")
    assert restored == connection


def test_unconfigured_hardware_is_blocked_without_pass(tmp_path: Path) -> None:
    adapter = HardwareBringupAdapter(evidence_store=EvidenceStore(tmp_path))
    result = adapter.execute(operation("CONNECT"), context())
    assert result.status == "UNAVAILABLE"
    assert result.summary == "BLOCKED_NO_HARDWARE"
    assert result.evidence_references == ("connection-1", "session-1")
    assert EvidenceStore(tmp_path).get("session-1").status is HardwareSessionStatus.FAILED


def test_fixed_openocd_and_pyocd_backends_use_allowlisted_argv_without_shell(tmp_path: Path) -> None:
    class Runner:
        def __init__(self) -> None:
            self.argv = None

        def run(self, argv, *, cwd, timeout_seconds, cancellation):
            self.argv = argv
            return 1, b"", b""

    runner = Runner()
    openocd = OpenOCDHardwareBackend(runner, clock=lambda: NOW)
    openocd.execute(operation("FLASH", artifact_value=artifact()), context(tmp_path))
    assert runner.argv[0].replace("\\", "/") == "C:/tools/openocd.exe"
    assert runner.argv[1:6] == ("-f", "interface/stlink.cfg", "-f", "target/stm32f1x.cfg", "-c")
    assert any("program" in value for value in runner.argv)

    py_runner = Runner()
    pyocd = PyOCDHardwareBackend(py_runner, clock=lambda: NOW)
    pyocd.execute(operation("FLASH", artifact_value=artifact()).model_copy(update={"tool_id": "PYOCD_FLASH"}), context(tmp_path))
    assert py_runner.argv[0].replace("\\", "/") == "C:/tools/openocd.exe"
    assert py_runner.argv[1:6] == ("flash", "-t", "stm32f103c8", "-u", "probe-1")


def test_hardware_tool_result_rejects_synthetic_pass() -> None:
    with pytest.raises(ValueError):
        HardwareToolResult(
            result=BringupResult.PASS,
            reason_code="TARGET_DETECTED",
            detail="pass",
            observed_from_hardware=False,
            observed_at=NOW,
        )


def test_verification_comparison_exposes_expected_simulation_hardware_and_difference() -> None:
    lineage = HardwareLineage(
        project_id="project-1", pcb_sha256=FP, schematic_sha256=FP, firmware_sha256=FP,
        elf_sha256=FP, build_id="build-id-1", design_truth_id="design-truth-1", hardware_session_id="session-1",
    )
    result = compare_hardware_observations(
        comparison_id="comparison-1",
        lineage=lineage,
        simulation_evidence_id="simulation-1",
        hardware_evidence_id="uart-evidence-1",
        expected=("System Init OK",),
        simulation_observed=("System Init OK",),
        hardware_observed=("SPI Init Failed",),
        created_at=NOW,
    )
    assert result.result is BringupResult.FAIL
    assert result.expected == ("System Init OK",)
    assert result.simulation_observed == ("System Init OK",)
    assert result.hardware_observed == ("SPI Init Failed",)
    assert len(result.difference) == 1


def test_submit_uses_existing_approval_worker_and_fails_closed_without_hardware(tmp_path: Path) -> None:
    database = tmp_path / "execution.sqlite3"
    execution_store = SQLiteExecutionStore(database)
    evidence_store = EvidenceStore(tmp_path / "evidence")
    target = ExecutionTarget(
        target_id="target-1", tenant_id="tenant-1", project_id="project-1", target_kind="STM32F103C8T6",
        board_family="blue-pill", mcu_family="STM32F1", stable_identity_hash="probe-1", candidate_id="candidate-1",
        binding_status=TargetBindingStatus.BOUND, bound_by="engineer-1", bound_at=NOW,
    )
    bound = operation("CONNECT").model_validate({**operation("CONNECT").model_dump(mode="python"), "target_fingerprint": target.fingerprint, "fingerprint": "sha256:" + "0" * 64})

    class Targets:
        def get_bound(self, **kwargs):
            return target

        def revalidate(self, value):
            return value

    class Workspace:
        def resolve(self, **kwargs):
            return tmp_path

    worker = ExecutionWorker(
        worker_id="hardware-worker-1",
        store=execution_store,
        registry=ExecutionToolRegistry(TrustedToolDiscovery()),
        workspace_resolver=Workspace(),
        targets=Targets(),
        hardware_bringup=HardwareBringupAdapter(evidence_store=evidence_store),
    )
    runtime = HardwareBringupRuntimeService(execution=ExecutionOperationService(store=execution_store), worker=worker)
    status = runtime.submit(bound, reviewer_id="reviewer-1", approval_nonce="approval-1", approval_expires_at=datetime.now(UTC) + timedelta(minutes=5))

    assert status is not None
    assert status.status is OperationStatus.FAILED
    receipt = execution_store.get_receipt("tenant-1", "project-1", bound.operation_id)
    assert receipt is not None
    assert receipt.safe_diagnostics_summary == "BLOCKED_NO_HARDWARE"
    assert receipt.evidence_references == ("connection-1", "session-1")
    assert SQLiteExecutionStore(database).get_operation("tenant-1", "project-1", bound.operation_id) == bound
