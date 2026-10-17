from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from embedded_copilot.execution_worker.contracts import ArtifactIdentity
from embedded_copilot.simulation_runtime.contracts import (
    SimulationAssertion,
    SimulationAssertionKind,
    SimulationBackend,
    SimulationExecutionBinding,
    SimulationFailureClass,
    SimulationRequest,
    SimulationStatus,
)
from embedded_copilot.simulation_runtime.exceptions import SimulationUnavailable
from embedded_copilot.simulation_runtime.ports import SimulationAdapterExecutionContext
from embedded_copilot.simulation_runtime.renode import RenodeGPIOConfig, RenodeSimulationAdapter


NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


class BytesStore:
    def __init__(self, content: bytes | Exception) -> None:
        self.content = content

    def read(self, _identity: ArtifactIdentity) -> bytes:
        if isinstance(self.content, Exception):
            raise self.content
        return self.content


class Runner:
    def __init__(self, mode: str = "crash") -> None:
        self.mode = mode

    def run(self, argv, *, cwd: Path, timeout_seconds: int, environment: dict[str, str], cancellation: object):
        if "--version" in argv:
            return 0, b"Renode v1.16.1", b""
        if self.mode == "timeout":
            raise TimeoutError("bounded timeout")
        if self.mode == "crash":
            return 17, b"", b"backend failed"
        return 0, b"", b""


def _elf(*, machine: int = 40) -> bytes:
    content = bytearray(32)
    content[:4] = b"\x7fELF"
    content[4] = 1
    content[18:20] = machine.to_bytes(2, "little")
    return bytes(content)


def _adapter(tmp_path: Path, *, runner: Runner, evidence_store=None) -> RenodeSimulationAdapter:
    executable = tmp_path / "renode.exe"
    platform = tmp_path / "stm32f103.repl"
    executable.write_bytes(b"trusted executable fixture")
    platform.write_text("trusted platform fixture", encoding="utf-8")
    return RenodeSimulationAdapter(
        executable=executable,
        platform_file=platform,
        gpio=RenodeGPIOConfig(
            target_kind="STM32F103C8T6",
            platform_descriptor="stm32f103",
            led_port="A",
            led_pin=8,
            button_port="B",
            button_pin=0,
        ),
        runner=runner,
        evidence_store=evidence_store,
    )


def _request(content: bytes, *, platform: str = "stm32f103") -> SimulationRequest:
    workspace = "sha256:" + "1" * 64
    artifact = ArtifactIdentity(
        artifact_id="artifact-renode-failure",
        tenant_id="tenant-renode",
        project_id="project-renode",
        target_id="target-renode",
        workspace_fingerprint=workspace,
        build_operation_id="build-renode",
        toolchain_id="ARM_NONE_EABI_GCC",
        toolchain_version="15.3.1",
        build_configuration="DEBUG",
        artifact_type="ELF",
        artifact_sha256="sha256:" + hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        created_at=NOW,
    )
    return SimulationRequest.create(
        simulation_id="simulation-renode-failure",
        tenant_id="tenant-renode",
        project_id="project-renode",
        backend=SimulationBackend.RENODE,
        firmware_artifact=artifact,
        platform_descriptor=platform,
        scenario="BOOT_AND_GPIO_BEHAVIOR",
        assertions=(
            SimulationAssertion.create(
                assertion_id="exit-zero",
                kind=SimulationAssertionKind.EXIT_STATUS,
                target="renode",
                expected=0,
            ),
        ),
        timeout_seconds=5,
        execution_binding=SimulationExecutionBinding.create(
            requester_id="engineer-renode",
            target_id="target-renode",
            target_fingerprint="sha256:" + "2" * 64,
            workspace_snapshot_id="snapshot-renode",
            workspace_fingerprint=workspace,
            proposal_id="proposal-renode",
            proposal_fingerprint="sha256:" + "3" * 64,
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            nonce="nonce-renode",
        ),
    )


def _execute(adapter: RenodeSimulationAdapter, request: SimulationRequest, content: bytes):
    prepared = adapter.prepare(request)
    return adapter.execute(
        prepared,
        SimulationAdapterExecutionContext(
            operation_id="operation-renode-failure",
            cancellation=object(),
            artifact_store=BytesStore(content),
        ),
    )


@pytest.mark.parametrize(
    ("content", "reason"),
    ((b"not an elf", "INVALID_ELF_ARTIFACT"), (_elf(machine=3), "ELF_TARGET_ARCHITECTURE_MISMATCH")),
)
def test_renode_rejects_invalid_or_wrong_target_elf(tmp_path: Path, content: bytes, reason: str) -> None:
    result = _execute(_adapter(tmp_path, runner=Runner()), _request(content), content)
    assert result.status is SimulationStatus.FAIL
    assert result.failure_class is SimulationFailureClass.ENGINEERING
    assert result.reason_code == reason


def test_renode_rejects_unsupported_model_before_execution(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, runner=Runner())
    with pytest.raises(SimulationUnavailable, match="RENODE_PLATFORM_UNSUPPORTED"):
        adapter.prepare(_request(_elf(), platform="stm32f407"))


def test_renode_maps_timeout_to_infrastructure_timeout(tmp_path: Path) -> None:
    content = _elf()
    result = _execute(_adapter(tmp_path, runner=Runner("timeout")), _request(content), content)
    assert result.status is SimulationStatus.TIMEOUT
    assert result.failure_class is SimulationFailureClass.INFRASTRUCTURE
    assert result.reason_code == "PROCESS_CONTROL_TIMEOUT"


def test_renode_maps_backend_crash_to_infrastructure_error(tmp_path: Path) -> None:
    content = _elf()
    result = _execute(_adapter(tmp_path, runner=Runner("crash")), _request(content), content)
    assert result.status is SimulationStatus.INFRASTRUCTURE_ERROR
    assert result.failure_class is SimulationFailureClass.INFRASTRUCTURE
    assert result.reason_code == "BACKEND_PROCESS_EXITED"


def test_renode_rejects_stale_artifact_identity(tmp_path: Path) -> None:
    content = _elf()
    adapter = _adapter(tmp_path, runner=Runner())
    prepared = adapter.prepare(_request(content))
    result = adapter.execute(
        prepared,
        SimulationAdapterExecutionContext(
            operation_id="operation-renode-failure",
            cancellation=object(),
            artifact_store=BytesStore(ValueError("identity drift")),
        ),
    )
    assert result.status is SimulationStatus.FAIL
    assert result.reason_code == "STALE_ELF_ARTIFACT"
