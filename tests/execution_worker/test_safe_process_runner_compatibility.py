from __future__ import annotations

import hashlib
import io
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from embedded_copilot.execution_worker import adapters
from embedded_copilot.execution_worker.adapters import (
    BuildToolAdapter,
    FlashToolAdapter,
    HilScenarioAdapter,
    SafeProcessRunner,
    UartObservationAdapter,
)
from embedded_copilot.execution_worker.artifacts import ArtifactStore
from embedded_copilot.execution_worker.contracts import (
    ArtifactIdentity,
    BuildOperation,
    ExecutionTarget,
    FlashOperation,
    HilOperation,
    HilScenario,
    HilStep,
    HilStepKind,
    OperationType,
    TargetBindingStatus,
    ToolchainIdentity,
    UartObservationOperation,
)
from embedded_copilot.execution_worker.ports import ExecutionContext
from embedded_copilot.execution_worker.registry import (
    ToolDefinition,
    ToolFamily,
    ToolResolution,
)

NOW = datetime(2026, 10, 5, tzinfo=UTC)
FP = "sha256:" + "1" * 64


class FakePipe(io.BytesIO):
    def __init__(self, content: bytes) -> None:
        super().__init__(content)
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        chunk = super().read(size)
        self.bytes_read += len(chunk)
        return chunk


class FakePopen:
    def __init__(
        self, *, code: int = 0, stdout: bytes = b"tool completed",
        stderr: bytes = b"", running: bool = False, terminate_stalls: bool = False,
    ) -> None:
        self.stdout = FakePipe(stdout)
        self.stderr = FakePipe(stderr)
        self.returncode = None if running else code
        self.terminated = 0
        self.killed = 0
        self.waits: list[float | None] = []
        self.terminate_stalls = terminate_stalls

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated += 1
        if not self.terminate_stalls:
            self.returncode = -15

    def kill(self) -> None:
        self.killed += 1
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.waits.append(timeout)
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake-tool", timeout)
        return self.returncode


class Cancellation:
    def __init__(self, *, cancel_on_check: int = 1) -> None:
        self.checks = 0
        self.cancel_on_check = cancel_on_check

    def is_cancelled(self) -> bool:
        self.checks += 1
        return self.checks >= self.cancel_on_check


@pytest.fixture
def fake_process(monkeypatch):
    process = FakePopen()
    calls = []
    clock = {"now": 0.0, "step": 0.1}
    launch_error = {"value": None}

    def popen(argv, **kwargs):
        calls.append((tuple(argv), kwargs))
        if launch_error["value"] is not None:
            raise launch_error["value"]
        return process

    def monotonic():
        clock["now"] += clock["step"]
        return clock["now"]

    def unexpected_run(*args, **kwargs):
        pytest.fail("compatibility tests must not launch subprocess.run")

    monkeypatch.setattr(adapters.subprocess, "Popen", popen)
    monkeypatch.setattr(adapters.subprocess, "run", unexpected_run)
    monkeypatch.setattr(adapters, "time", SimpleNamespace(monotonic=monotonic, sleep=lambda _: None))
    return SimpleNamespace(process=process, calls=calls, clock=clock, launch_error=launch_error)


def _run(tmp_path: Path, *, cancellation: object = None, timeout: int = 10):
    return SafeProcessRunner().run(
        ("cmake.exe", "--version"), cwd=tmp_path, timeout_seconds=timeout,
        environment={"LANG": "C"}, cancellation=cancellation,
    )


@pytest.mark.parametrize("code", [0, 7, -9])
def test_generic_runner_returns_exit_code_and_drains_both_pipes(tmp_path, fake_process, code):
    process = fake_process.process
    process.returncode = code
    process.stdout = FakePipe(b"stdout result")
    process.stderr = FakePipe(b"stderr result")

    assert _run(tmp_path) == (code, b"stdout result", b"stderr result")
    assert fake_process.calls == [(
        ("cmake.exe", "--version"),
        {"cwd": str(tmp_path), "env": {"LANG": "C"}, "stdin": subprocess.DEVNULL,
         "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "shell": False},
    )]
    assert process.stdout.closed and process.stderr.closed
    assert process.killed == process.terminated == 0


def test_generic_runner_caps_each_pipe_but_drains_all_bytes(tmp_path, fake_process):
    process = fake_process.process
    stdout = b"A" * (64 * 1024 + 12345)
    stderr = b"B" * (64 * 1024 + 23456)
    process.stdout = FakePipe(stdout)
    process.stderr = FakePipe(stderr)

    assert _run(tmp_path) == (0, stdout[:64 * 1024], stderr[:64 * 1024])
    assert process.stdout.bytes_read == len(stdout)
    assert process.stderr.bytes_read == len(stderr)
    assert process.stdout.closed and process.stderr.closed


def test_generic_runner_timeout_kills_and_reaps_process(tmp_path, fake_process):
    process = fake_process.process
    process.returncode = None
    fake_process.clock["step"] = 100

    with pytest.raises(TimeoutError, match="operation timed out"):
        _run(tmp_path, timeout=1)

    assert process.killed == 1 and process.terminated == 0
    assert process.waits == [2]
    assert process.stdout.closed and process.stderr.closed


@pytest.mark.parametrize("terminate_stalls", [False, True])
def test_generic_runner_cancellation_terminates_and_reaps_process(tmp_path, fake_process, terminate_stalls):
    process = fake_process.process
    process.returncode = None
    process.terminate_stalls = terminate_stalls
    cancellation = Cancellation(cancel_on_check=2)

    with pytest.raises(InterruptedError, match="operation cancelled"):
        _run(tmp_path, cancellation=cancellation)

    assert cancellation.checks == 2
    assert process.terminated == 1
    assert process.killed == int(terminate_stalls)
    assert process.waits == ([2, 2] if terminate_stalls else [2])
    assert process.stdout.closed and process.stderr.closed


def test_generic_pre_cancelled_baseline_stops_after_spawn(tmp_path, fake_process):
    """The existing generic API checks cancellation after starting its process."""
    fake_process.process.returncode = None

    with pytest.raises(InterruptedError):
        _run(tmp_path, cancellation=Cancellation())

    assert len(fake_process.calls) == 1
    assert fake_process.process.terminated == 1


@pytest.mark.parametrize("argv", [(), ("",), ("cmake.exe", ""), (None,), ("cmake.exe", 7)])
def test_generic_runner_rejects_invalid_argv_before_spawn(tmp_path, fake_process, argv):
    with pytest.raises(ValueError, match="invalid fixed tool invocation"):
        SafeProcessRunner().run(argv, cwd=tmp_path, timeout_seconds=1, environment={}, cancellation=object())
    assert fake_process.calls == []


@pytest.mark.parametrize("argv", [
    ("python",), ("python.exe",), ("python", "-c", "print(1)"),
    ("python3",), ("python3.exe",), ("python3.11.exe",), ("pythonw.exe",),
    ("py",), ("py.exe",), ("python2.7.exe",),
    ("python.exe", "-m", "http.server"), ("PYTHON.EXE", "-c", "print(1)"),
    ("python", "-m", "embedded_copilot.execution_worker.native_move_wrapper"),
    ("cmd.exe",), ("powershell.exe",), ("pwsh.exe",), ("bash",), ("sh",),
])
def test_generic_runner_keeps_python_and_interactive_shell_ban(tmp_path, fake_process, argv):
    with pytest.raises(PermissionError, match="interpreter launchers are forbidden"):
        SafeProcessRunner().run(argv, cwd=tmp_path, timeout_seconds=1, environment={}, cancellation=object())
    assert fake_process.calls == []


def _base(operation_id: str) -> dict:
    return {
        "operation_id": operation_id, "tenant_id": "tenant-1", "project_id": "project-1", "requester_id": "requester-1",
        "target_id": "target-1", "target_fingerprint": FP, "workspace_snapshot_id": "snapshot-1", "workspace_fingerprint": FP,
        "proposal_id": "proposal-1", "proposal_fingerprint": FP, "created_at": NOW, "expires_at": NOW + timedelta(minutes=10),
        "nonce": "nonce-1",
    }


def _context(tmp_path, *, family=ToolFamily.CMAKE_NINJA, cancellation=None, artifact_store=None, connection=None):
    target = ExecutionTarget(
        target_id="target-1", tenant_id="tenant-1", project_id="project-1", target_kind="STM32F446RE",
        board_family="NUCLEO-F446RE", mcu_family="STM32F446RE", stable_identity_hash="probe-hash-1",
        candidate_id="candidate-1", binding_status=TargetBindingStatus.BOUND, bound_by="operator-1", bound_at=NOW,
    )
    flash = family is ToolFamily.OPENOCD
    tool = ToolResolution(
        ToolDefinition(
            tool_id="OPENOCD_FLASH" if flash else "CMAKE_NINJA_BUILD", tool_version="3.0", family=family,
            supported_operation=OperationType.FLASH if flash else OperationType.BUILD,
            supported_targets=(target.target_kind,), allowed_executable_names=("openocd.exe" if flash else "cmake.exe",),
            timeout_seconds=10, artifact_types=("ELF",),
        ),
        tmp_path / ("openocd.exe" if flash else "cmake.exe"), True, "AVAILABLE", "AVAILABLE",
    )
    return ExecutionContext(
        workspace_root=tmp_path / "workspace", state_root=tmp_path / "state", target=target, tool=tool,
        cancellation=cancellation, artifact_store=artifact_store, target_connection=connection,
    )


def _build_operation(toolchain_id="CMAKE_NINJA_BUILD"):
    return BuildOperation(
        **_base("build-1"), configuration="DEBUG", expected_artifact_type="ELF",
        toolchain=ToolchainIdentity(
            toolchain_id=toolchain_id, tool_version="3.0",
            tool_family="ESP_IDF" if toolchain_id == "ESP_IDF_BUILD" else "CMAKE_NINJA",
            supported_operation=OperationType.BUILD, supported_target="STM32F446RE",
        ),
    )


def _failure(fake_process, failure):
    if failure == "nonzero":
        fake_process.process.returncode = 2
        fake_process.process.stderr = FakePipe(b"tool failed")
    elif failure in {"timeout", "cancel"}:
        fake_process.process.returncode = None
        if failure == "timeout":
            fake_process.clock["step"] = 100
    elif failure == "spawn_error":
        fake_process.launch_error["value"] = OSError("fake unavailable executable")


@pytest.mark.parametrize("toolchain_id", ["CMAKE_NINJA_BUILD", "ESP_IDF_BUILD"])
def test_build_adapter_fixed_template_runs_through_safe_runner(tmp_path, fake_process, toolchain_id):
    context = _context(tmp_path)
    operation = _build_operation(toolchain_id)
    build_root = context.state_root / "build" / operation.operation_id
    build_root.mkdir(parents=True)
    (build_root / "firmware.elf").write_bytes(b"fake firmware ELF")

    result = BuildToolAdapter(runner=SafeProcessRunner()).execute(operation, context)

    expected = ((str(context.tool.executable), "build") if toolchain_id == "ESP_IDF_BUILD" else
                (str(context.tool.executable), "--build", str(build_root.resolve()), "--config", "DEBUG"))
    assert fake_process.calls[0][0] == expected
    assert fake_process.calls[0][1]["cwd"] == str(context.workspace_root)
    assert fake_process.calls[0][1]["shell"] is False
    assert result.status == "SUCCEEDED"
    assert result.artifact_content == b"fake firmware ELF"
    assert result.artifact.artifact_sha256 == "sha256:" + hashlib.sha256(result.artifact_content).hexdigest()
    assert result.artifact.build_operation_id == operation.operation_id


@pytest.mark.parametrize("failure,status", [
    ("nonzero", "FAILED"), ("timeout", "TIMED_OUT"), ("cancel", "CANCELLED"),
    ("spawn_error", "UNAVAILABLE"), ("missing_artifact", "FAILED"),
])
def test_build_adapter_maps_real_runner_error_paths(tmp_path, fake_process, failure, status):
    _failure(fake_process, failure)
    context = _context(tmp_path, cancellation=Cancellation() if failure == "cancel" else None)

    result = BuildToolAdapter(runner=SafeProcessRunner()).execute(_build_operation(), context)

    assert result.status == status
    assert result.artifact is None and result.artifact_content is None
    assert len(fake_process.calls) == 1
    if failure == "missing_artifact":
        assert result.summary == "build artifact missing"


class FixedFlashConnection:
    def __init__(self):
        self.calls = []

    def build_flash_argv(self, *, tool_family, executable, target, artifact_path):
        self.calls.append((tool_family, target.target_id, artifact_path))
        return (executable, "-f", "board/st_nucleo_f4.cfg", "-c", f"program {artifact_path} verify reset exit")


def _flash(tmp_path, *, cancellation=None):
    content = b"fake flash artifact"
    artifact = ArtifactIdentity(
        artifact_id="artifact-1", tenant_id="tenant-1", project_id="project-1", target_id="target-1",
        workspace_fingerprint=FP, build_operation_id="build-1", toolchain_id="CMAKE_NINJA_BUILD",
        toolchain_version="3.0", build_configuration="DEBUG", artifact_type="ELF",
        artifact_sha256="sha256:" + hashlib.sha256(content).hexdigest(), size_bytes=len(content), created_at=NOW,
    )
    store = ArtifactStore(tmp_path / "artifacts")
    store.put(artifact, content)
    connection = FixedFlashConnection()
    context = _context(tmp_path, family=ToolFamily.OPENOCD, cancellation=cancellation,
                       artifact_store=store, connection=connection)
    operation = FlashOperation(**_base("flash-1"), artifact=artifact, tool_id="OPENOCD_FLASH", tool_version="3.0")
    return operation, context, connection


def test_flash_adapter_materializes_verified_artifact_and_runs_safe_runner(tmp_path, fake_process):
    operation, context, connection = _flash(tmp_path)
    fake_process.process.stdout = FakePipe(b"verified successfully")

    result = FlashToolAdapter(runner=SafeProcessRunner()).execute(operation, context)

    flash_input = (context.state_root / "flash-input" / operation.operation_id / "elf").resolve()
    assert connection.calls == [("OPENOCD", context.target.target_id, flash_input)]
    assert flash_input.read_bytes() == context.artifact_store.read(operation.artifact)
    assert fake_process.calls[0][0] == (
        str(context.tool.executable), "-f", "board/st_nucleo_f4.cfg", "-c", f"program {flash_input} verify reset exit",
    )
    assert fake_process.calls[0][1]["cwd"] == str(context.state_root)
    assert fake_process.calls[0][1]["shell"] is False
    assert result.status == "SUCCEEDED" and result.flash_verification_status == "VERIFIED"


@pytest.mark.parametrize("failure,status", [
    ("nonzero", "FAILED"), ("timeout", "TIMED_OUT"), ("cancel", "CANCELLED"), ("spawn_error", "UNAVAILABLE"),
])
def test_flash_adapter_maps_real_runner_error_paths(tmp_path, fake_process, failure, status):
    operation, context, _ = _flash(tmp_path, cancellation=Cancellation() if failure == "cancel" else None)
    _failure(fake_process, failure)

    result = FlashToolAdapter(runner=SafeProcessRunner()).execute(operation, context)

    assert result.status == status
    assert len(fake_process.calls) == 1
    if failure == "nonzero":
        assert result.flash_verification_status == "FAILED"


class FakeSerialTransport:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def observe(self, *args):
        self.calls.append(args)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


@pytest.mark.parametrize("observation,status", [
    (b"READY extra evidence", "SUCCEEDED"), (TimeoutError(), "TIMED_OUT"),
    (InterruptedError(), "CANCELLED"), (OSError(), "UNAVAILABLE"), ("invalid bytes", "FAILED"),
])
def test_uart_uses_transport_and_never_spawns(tmp_path, fake_process, observation, status):
    operation = UartObservationOperation(
        **_base("uart-1"), port_binding_id="uart-binding-1", baud_rate=115200,
        duration_seconds=2, max_bytes=5, expected_patterns=("READY", "extra"),
    )
    transport = FakeSerialTransport(observation)

    result = UartObservationAdapter(transport).execute(operation, _context(tmp_path))

    assert result.status == status
    assert transport.calls == [("uart-binding-1", 115200, 2, 5)]
    assert fake_process.calls == []
    if status == "SUCCEEDED":
        assert result.evidence_references == ("uart-uart-1-0",)
        assert result.summary == "patterns observed: READY"


@pytest.mark.parametrize("observation,status", [
    (b"READY", "SUCCEEDED"), (b"booting", "FAILED"), (TimeoutError(), "TIMED_OUT"),
    (InterruptedError(), "CANCELLED"), (OSError(), "UNAVAILABLE"),
])
def test_hil_preserves_uart_chain_without_process_spawn(tmp_path, fake_process, observation, status):
    operation = HilOperation(
        **_base("hil-1"), flash_operation_id="flash-1",
        scenario=HilScenario(
            scenario_id="scenario-1", project_id="project-1", target_id="target-1", artifact_id="artifact-1",
            steps=(HilStep(kind=HilStepKind.ASSERT_UART_PATTERN, pattern="READY"),), timeout_seconds=5,
        ),
    )
    transport = FakeSerialTransport(observation)

    result = HilScenarioAdapter(UartObservationAdapter(transport)).execute(operation, _context(tmp_path))

    assert result.status == status
    assert transport.calls == [("scenario-uart", 115200, 5, 64 * 1024)]
    assert fake_process.calls == []
    if status == "SUCCEEDED":
        assert result.evidence_references == ("uart-hil-1-step-0-0",)


def install_thread_setup_failure(monkeypatch, fault):
    """No real threads: inject failure at a selected constructor/start boundary."""
    created = []

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            self.index = len(created)
            self.started = False
            self.target = target
            self.args = args
            created.append(self)
            if fault == f"construct_{self.index + 1}":
                raise RuntimeError("fake thread setup failure")

        def start(self):
            if fault == f"start_{self.index + 1}":
                raise RuntimeError("fake thread setup failure")
            self.started = True
            self.target(*self.args)

        def join(self, *, timeout):
            if not self.started:
                raise RuntimeError("unstarted fake thread cannot be joined")

        def is_alive(self):
            return False

    monkeypatch.setattr(adapters.threading, "Thread", FakeThread)
    return created


@pytest.mark.parametrize("fault", ["construct_1", "construct_2", "start_1", "start_2"])
def test_generic_runner_reaps_process_when_thread_setup_fails(tmp_path, fake_process, monkeypatch, fault):
    fake_process.process.returncode = None
    install_thread_setup_failure(monkeypatch, fault)
    with pytest.raises(RuntimeError, match="fake thread setup failure"):
        _run(tmp_path)
    assert len(fake_process.calls) == 1
    assert fake_process.process.killed == 1
    assert fake_process.process.waits == [2]
    assert fake_process.process.stdout.closed and fake_process.process.stderr.closed

@pytest.mark.parametrize("fault", ["kill", "wait", "stdout_close", "stderr_close"])
def test_runner_cleanup_attempts_all_resources_after_setup_and_cleanup_errors(tmp_path, fake_process, monkeypatch, fault):
    process = fake_process.process
    process.returncode = None
    events = []

    class TrackedPipe(FakePipe):
        def __init__(self, name):
            super().__init__(b"")
            self.name = name

        def close(self):
            events.append(self.name + "_close")
            super().close()
            if fault == self.name + "_close":
                raise RuntimeError(fault)

    process.stdout = TrackedPipe("stdout")
    process.stderr = TrackedPipe("stderr")
    original_kill = process.kill
    original_wait = process.wait

    def kill():
        events.append("kill")
        if fault == "kill":
            raise RuntimeError(fault)
        original_kill()

    def wait(timeout=None):
        events.append("wait")
        if fault == "wait":
            raise RuntimeError(fault)
        return original_wait(timeout)

    process.kill = kill
    process.wait = wait
    created = install_thread_setup_failure(monkeypatch, "start_2")
    with pytest.raises(RuntimeError, match=fault):
        _run(tmp_path)
    assert "stdout_close" in events and "stderr_close" in events
    assert "wait" in events
    assert process.stdout.closed and process.stderr.closed
    assert len(created) == 2 and created[0].started


def test_runner_cleanup_attempts_second_join_when_first_join_fails(tmp_path, fake_process, monkeypatch):
    events = []

    class JoinFaultThread:
        def __init__(self, *, target, args, daemon):
            self.index = len(events)
            self.target = target
            self.args = args
            events.append("constructed")

        def start(self):
            self.target(*self.args)

        def join(self, *, timeout):
            events.append(f"join_{self.index}")
            if self.index == 0:
                raise RuntimeError("join failure")

        def is_alive(self):
            return False

    monkeypatch.setattr(adapters.threading, "Thread", JoinFaultThread)
    with pytest.raises(RuntimeError, match="join failure"):
        _run(tmp_path)
    assert events[-2:] == ["join_0", "join_1"]
    assert fake_process.process.stdout.closed and fake_process.process.stderr.closed
