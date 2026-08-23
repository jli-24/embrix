from __future__ import annotations

import hashlib
import os
import subprocess
import time
from pathlib import Path
from typing import Protocol

from .contracts import (
    ArtifactIdentity,
    BuildOperation,
    FlashOperation,
    HilOperation,
    HilStepKind,
    OperationRequest,
    OperationType,
    TestOperation,
    UartObservationOperation,
)
from .ports import AdapterResult, ExecutionContext


class ProcessRunnerPort(Protocol):
    def run(self, argv: tuple[str, ...], *, cwd: Path, timeout_seconds: int, environment: dict[str, str], cancellation: object) -> tuple[int, bytes, bytes]: ...


class SafeProcessRunner:
    """The only subprocess boundary; argv is created by fixed adapters."""

    def run(self, argv: tuple[str, ...], *, cwd: Path, timeout_seconds: int, environment: dict[str, str], cancellation: object) -> tuple[int, bytes, bytes]:
        if not argv or any(not isinstance(item, str) or not item for item in argv):
            raise ValueError("invalid fixed tool invocation")
        if Path(argv[0]).name.casefold() in {"cmd.exe", "powershell.exe", "pwsh.exe", "bash", "sh", "python.exe", "python"}:
            raise PermissionError("interpreter launchers are forbidden")
        process = subprocess.Popen(
            list(argv),
            cwd=str(cwd),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        deadline = time.monotonic() + timeout_seconds
        while process.poll() is None:
            if bool(getattr(cancellation, "is_cancelled", lambda: False)()):
                process.terminate()
                process.wait(timeout=2)
                raise InterruptedError("operation cancelled")
            if time.monotonic() >= deadline:
                process.kill()
                process.wait(timeout=2)
                raise TimeoutError("operation timed out")
            time.sleep(0.05)
        stdout, stderr = process.communicate()
        return process.returncode or 0, stdout[:64 * 1024], stderr[:64 * 1024]


def _safe_summary(stdout: bytes, stderr: bytes) -> str:
    combined = (stdout + b"\n" + stderr).decode("utf-8", errors="replace")
    lines = []
    for line in combined.splitlines()[:40]:
        line = line.strip()
        if not line:
            continue
        lowered = line.casefold()
        if any(token in lowered for token in ("api_key", "token", "password", "secret", "credential")):
            line = "[redacted diagnostic]"
        if len(line) > 240:
            line = line[:240]
        lines.append(line)
    return " | ".join(lines)[:1024] or "no diagnostics"


def _reported_flash_verification(stdout: bytes, stderr: bytes) -> str:
    text = (stdout + b"\n" + stderr).decode("utf-8", errors="replace").casefold()
    if any(marker in text for marker in ("hash of data verified", "verified successfully", "verification successful", "verified ok")):
        return "VERIFIED"
    return "NOT_REPORTED"


class UnavailableTestAdapter:
    """Fail-closed default until a typed firmware-test adapter is injected."""

    def execute(self, operation: TestOperation, context: ExecutionContext) -> AdapterResult:
        return AdapterResult("UNAVAILABLE", "firmware test adapter unavailable")


class BuildToolAdapter:
    def __init__(self, *, runner: ProcessRunnerPort | None = None) -> None:
        self._runner = runner or SafeProcessRunner()

    def execute(self, operation: BuildOperation, context: ExecutionContext) -> AdapterResult:
        if context.tool is None or context.tool.executable is None:
            return AdapterResult("UNAVAILABLE", "build tool unavailable")
        build_root = (context.state_root / "build" / operation.operation_id).resolve()
        build_root.mkdir(parents=True, exist_ok=True)
        if not build_root.is_relative_to(context.state_root.resolve()):
            return AdapterResult("FAILED", "build workspace rejected")
        environment = _minimal_environment()
        executable = str(context.tool.executable)
        if operation.toolchain.toolchain_id == "ESP_IDF_BUILD":
            argv = (executable, "build")
        elif operation.toolchain.toolchain_id == "CMAKE_NINJA_BUILD":
            argv = (executable, "--build", str(build_root), "--config", operation.configuration)
        else:
            return AdapterResult("UNAVAILABLE", "toolchain template unavailable")
        try:
            code, stdout, stderr = self._runner.run(argv, cwd=context.workspace_root, timeout_seconds=context.tool.definition.timeout_seconds, environment=environment, cancellation=context.cancellation)
        except InterruptedError:
            return AdapterResult("CANCELLED", "build cancelled")
        except TimeoutError:
            return AdapterResult("TIMED_OUT", "build timed out")
        except (OSError, PermissionError):
            return AdapterResult("UNAVAILABLE", "build tool unavailable")
        if code != 0:
            return AdapterResult("FAILED", _safe_summary(stdout, stderr))
        artifact_path = _find_artifact(build_root, operation.expected_artifact_type)
        if artifact_path is None:
            return AdapterResult("FAILED", "build artifact missing")
        content = artifact_path.read_bytes()
        artifact = ArtifactIdentity(
            artifact_id=f"artifact-{operation.operation_id}",
            tenant_id=operation.tenant_id,
            project_id=operation.project_id,
            target_id=operation.target_id,
            workspace_fingerprint=operation.workspace_fingerprint,
            build_operation_id=operation.operation_id,
            toolchain_id=operation.toolchain.toolchain_id,
            toolchain_version=operation.toolchain.tool_version,
            build_configuration=operation.configuration,
            artifact_type=operation.expected_artifact_type,
            artifact_sha256="sha256:" + hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            created_at=operation.created_at,
        )
        return AdapterResult("SUCCEEDED", _safe_summary(stdout, stderr), artifact=artifact, artifact_content=content)


class FlashToolAdapter:
    def __init__(self, *, runner: ProcessRunnerPort | None = None, connection: object | None = None) -> None:
        self._runner = runner or SafeProcessRunner()
        self._connection = connection

    def execute(self, operation: FlashOperation, context: ExecutionContext) -> AdapterResult:
        if context.tool is None or context.tool.executable is None:
            return AdapterResult("UNAVAILABLE", "flash tool unavailable")
        artifact_store = context.artifact_store
        if artifact_store is None or not callable(getattr(artifact_store, "read", None)):
            return AdapterResult("UNAVAILABLE", "artifact store unavailable")
        try:
            content = artifact_store.read(operation.artifact)
        except (FileNotFoundError, ValueError):
            return AdapterResult("FAILED", "complete artifact identity verification failed")
        connection = self._connection or context.target_connection
        if connection is None or not callable(getattr(connection, "build_flash_argv", None)):
            return AdapterResult("UNAVAILABLE", "trusted target flash connection unavailable")
        argv = tuple(connection.build_flash_argv(
            tool_family=context.tool.definition.family.value,
            executable=str(context.tool.executable),
            target=context.target,
            artifact_path=_materialize_flash_input(context, operation, content),
        ))
        if not argv or argv[0] != str(context.tool.executable):
            return AdapterResult("FAILED", "trusted flash invocation rejected")
        flash_cancellation = _FlashCancellation(context.cancellation, connection)
        try:
            code, stdout, stderr = self._runner.run(argv, cwd=context.state_root, timeout_seconds=context.tool.definition.timeout_seconds, environment=_minimal_environment(), cancellation=flash_cancellation)
        except InterruptedError:
            return AdapterResult("CANCELLED", "flash cancellation pending or accepted")
        except TimeoutError:
            return AdapterResult("TIMED_OUT", "flash timed out")
        except (OSError, PermissionError):
            return AdapterResult("UNAVAILABLE", "flash tool unavailable")
        if code != 0:
            return AdapterResult("FAILED", _safe_summary(stdout, stderr), flash_verification_status="FAILED")
        verification = _reported_flash_verification(stdout, stderr)
        detail = _safe_summary(stdout, stderr)
        if flash_cancellation.cancel_pending:
            detail = (detail + " | cancellation pending at flash safety boundary")[:1024]
        return AdapterResult("SUCCEEDED", detail, flash_verification_status=verification)


class UartObservationAdapter:
    def __init__(self, transport: object | None = None) -> None:
        self._transport = transport

    def execute(self, operation: UartObservationOperation, context: ExecutionContext) -> AdapterResult:
        if self._transport is None or not callable(getattr(self._transport, "observe", None)):
            return AdapterResult("UNAVAILABLE", "UART transport unavailable")
        try:
            observed = self._transport.observe(operation.port_binding_id, operation.baud_rate, operation.duration_seconds, operation.max_bytes)
        except TimeoutError:
            return AdapterResult("TIMED_OUT", "UART observation timed out")
        except InterruptedError:
            return AdapterResult("CANCELLED", "UART observation cancelled")
        except OSError:
            return AdapterResult("UNAVAILABLE", "UART transport unavailable")
        if not isinstance(observed, bytes):
            return AdapterResult("FAILED", "UART adapter returned invalid evidence")
        text = observed[:operation.max_bytes].decode("utf-8", errors="replace")
        hits = tuple(pattern for pattern in operation.expected_patterns if pattern in text)
        evidence = tuple(f"uart-{operation.operation_id}-{index}" for index, _ in enumerate(hits))
        summary = "patterns observed: " + ", ".join(hits) if hits else ("no expected pattern observed" if operation.expected_patterns else "UART bounded observation captured")
        return AdapterResult("SUCCEEDED", summary[:1024], evidence_references=evidence)


class HilScenarioAdapter:
    def __init__(self, uart: UartObservationAdapter, power: object | None = None, reset: object | None = None) -> None:
        self._uart = uart
        self._power = power
        self._reset = reset

    def execute(self, operation: HilOperation, context: ExecutionContext) -> AdapterResult:
        evidence: list[str] = []
        for index, step in enumerate(operation.scenario.steps):
            if bool(getattr(context.cancellation, "is_cancelled", lambda: False)()):
                return AdapterResult("CANCELLED", "HIL cancelled at step boundary", evidence_references=tuple(evidence))
            if step.kind is HilStepKind.POWER_CYCLE:
                if self._power is None or not callable(getattr(self._power, "power_cycle", None)):
                    return AdapterResult("UNAVAILABLE", "power cycle adapter unavailable")
                self._power.power_cycle()
            elif step.kind is HilStepKind.RESET_TARGET:
                if self._reset is None or not callable(getattr(self._reset, "reset", None)):
                    return AdapterResult("UNAVAILABLE", "target reset adapter unavailable")
                self._reset.reset(context.target.target_id)
            elif step.kind is HilStepKind.WAIT_DURATION:
                time.sleep(step.duration_seconds or 0)
            elif step.kind in {HilStepKind.WAIT_FOR_UART_PATTERN, HilStepKind.ASSERT_UART_PATTERN, HilStepKind.ASSERT_NO_PANIC}:
                pattern = step.pattern or ("panic" if step.kind is HilStepKind.ASSERT_NO_PANIC else "")
                observation = self._uart.execute(
                    UartObservationOperation(
                        operation_id=f"{operation.operation_id}-step-{index}", tenant_id=operation.tenant_id, project_id=operation.project_id, requester_id=operation.requester_id,
                        target_id=operation.target_id, target_fingerprint=operation.target_fingerprint, workspace_snapshot_id=operation.workspace_snapshot_id,
                        workspace_fingerprint=operation.workspace_fingerprint, proposal_id=operation.proposal_id, proposal_fingerprint=operation.proposal_fingerprint,
                        created_at=operation.created_at, expires_at=operation.expires_at, nonce=operation.nonce,
                        port_binding_id="scenario-uart", baud_rate=115200, duration_seconds=min(30, operation.scenario.timeout_seconds), max_bytes=64 * 1024,
                        expected_patterns=(pattern,),
                    ), context
                )
                if observation.status != "SUCCEEDED":
                    return observation
                evidence.extend(observation.evidence_references)
                if step.kind in {HilStepKind.ASSERT_UART_PATTERN, HilStepKind.ASSERT_NO_PANIC} and not observation.evidence_references:
                    return AdapterResult("FAILED", "HIL assertion failed", evidence_references=tuple(evidence))
        return AdapterResult("SUCCEEDED", "HIL scenario completed", evidence_references=tuple(evidence))


def _find_artifact(root: Path, artifact_type: str) -> Path | None:
    suffix = {"ELF": ".elf", "BIN": ".bin", "HEX": ".hex", "MAP": ".map"}[artifact_type]
    matches = sorted(path for path in root.rglob(f"*{suffix}") if path.is_file())
    return matches[0] if len(matches) == 1 else None


def _minimal_environment() -> dict[str, str]:
    allowed = {"SystemRoot", "TEMP", "TMP", "PATH", "LANG", "LC_ALL"}
    return {key: value for key, value in os.environ.items() if key in allowed}


def _materialize_flash_input(context: ExecutionContext, operation: FlashOperation, content: bytes) -> Path:
    root = (context.state_root / "flash-input" / operation.operation_id).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_relative_to(context.state_root.resolve()):
        raise ValueError("flash input workspace escaped")
    path = root / operation.artifact.artifact_type.lower()
    if path.exists() and path.read_bytes() != content:
        raise ValueError("flash input drift")
    if not path.exists():
        path.write_bytes(content)
    return path


class _FlashCancellation:
    """Do not terminate a flash process after its adapter enters its safe section."""

    def __init__(self, cancellation: object, connection: object) -> None:
        self._cancellation = cancellation
        self._connection = connection
        self.cancel_pending = False

    def is_cancelled(self) -> bool:
        requested = bool(getattr(self._cancellation, "is_cancelled", lambda: False)())
        in_safe_section = bool(getattr(self._connection, "in_non_cancellable_section", lambda: False)())
        if requested and in_safe_section:
            self.cancel_pending = True
            return False
        return requested


__all__ = (
    "BuildToolAdapter",
    "FlashToolAdapter",
    "HilScenarioAdapter",
    "ProcessRunnerPort",
    "SafeProcessRunner",
    "UartObservationAdapter",
    "UnavailableTestAdapter",
)
