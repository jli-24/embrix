from __future__ import annotations

import hashlib
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from embedded_copilot.firmware_engineering import (
    BuildReadinessResult,
    BuildReadinessStatus,
    FirmwareBuildArtifact,
    FirmwareBuildConfiguration,
    FirmwareBuildRequest,
    FirmwareBuildResult,
    FirmwareBuildResultStatus,
    FirmwareBuildStatus,
    evaluate_build_readiness,
)

from .contracts import (
    ArtifactIdentity,
    BuildEvidence,
    BuildOperation,
    FlashOperation,
    HilOperation,
    HilStepKind,
    TestOperation,
    UartObservationOperation,
)
from .ports import AdapterResult, ExecutionContext


class ProcessRunnerPort(Protocol):
    def run(self, argv: tuple[str, ...], *, cwd: Path, timeout_seconds: int, environment: dict[str, str], cancellation: object) -> tuple[int, bytes, bytes]: ...


def run_trusted_tool(
    runner: ProcessRunnerPort,
    argv: tuple[str, ...],
    *,
    cwd: Path,
    timeout_seconds: int,
) -> tuple[int, bytes, bytes]:
    """Invoke one fixed tool through the existing process boundary."""

    return runner.run(
        argv,
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        environment=_minimal_environment(),
        cancellation=object(),
    )


def run_kicad_cli(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    timeout_seconds: float,
    environment: Mapping[str, str] | None = None,
) -> object:
    """Execute a fixed KiCad CLI argv at the sole subprocess boundary.

    The schematic ERC adapter injects this narrow callable instead of importing
    ``subprocess`` itself.  This keeps process creation centralized alongside
    the existing worker adapters and preserves the no-shell contract.
    """

    if not argv or any(not isinstance(item, str) or not item for item in argv):
        raise ValueError("invalid fixed KiCad invocation")
    process_environment = _minimal_environment()
    if environment is not None:
        for key, value in environment.items():
            if (
                not isinstance(key, str)
                or (re.fullmatch(r"KICAD[A-Z0-9_]*", key) is None and key not in {"APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH"})
                or not isinstance(value, str)
                or not value
                or "\x00" in value
            ):
                raise ValueError("invalid KiCad process environment")
            process_environment[key] = value
    try:
        return subprocess.run(
            list(argv),
            cwd=str(cwd),
            env=process_environment,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as error:
        raise TimeoutError("KiCad CLI timed out") from error


def probe_kicad_cli_version(executable: str, *, cwd: Path, timeout_seconds: float = 15.0) -> str:
    """Return the version reported by the trusted KiCad executable.

    Version discovery stays at the same subprocess boundary as ERC.  Only a
    bounded, fixed ``--version`` invocation is accepted; diagnostics are not
    returned to callers and an absent/ambiguous version fails closed.
    """

    completed = run_kicad_cli((executable, "--version"), cwd=cwd, timeout_seconds=timeout_seconds)
    output = (
        bytes(getattr(completed, "stdout", b"") or b"")
        + b"\n"
        + bytes(getattr(completed, "stderr", b"") or b"")
    )[:4096].decode("utf-8", errors="ignore")
    for line in output.splitlines():
        candidate = line.strip()
        match = re.search(r"(?<![A-Za-z0-9])(?:KiCad\s+)?(\d+(?:\.\d+){1,3})(?![A-Za-z0-9])", candidate, re.IGNORECASE)
        if match:
            return match.group(1)
    raise ValueError("KiCad version was not reported")


def probe_trusted_tool_version(executable: Path, *, timeout_seconds: int = 15) -> str:
    completed = SafeProcessRunner().run(
        (str(executable), "--version"),
        cwd=executable.parent,
        timeout_seconds=timeout_seconds,
        environment=_minimal_environment(),
        cancellation=object(),
    )
    if completed[0] != 0:
        raise ValueError("toolchain version probe failed")
    output = (completed[1] + b"\n" + completed[2]).decode("utf-8", errors="ignore")[:4096]
    match = re.search(r"(?<![A-Za-z0-9])v?(\d+\.\d+\.\d+)(?![A-Za-z0-9])", output)
    if match is None:
        match = re.search(r"(?<![A-Za-z0-9])v?(\d+\.\d+)(?![A-Za-z0-9])", output)
    if match is None:
        raise ValueError("toolchain version was not reported")
    return match.group(1)


@dataclass(frozen=True, slots=True)
class NativeProcessResult:
    exit_code: int
    stdout: bytes
    stderr: bytes
    stdout_overflow: bool
    stderr_overflow: bool


class SafeProcessRunner:
    """One subprocess control loop; only the dedicated method admits fixed native Python."""

    def run(self, argv: tuple[str, ...], *, cwd: Path, timeout_seconds: int, environment: dict[str, str], cancellation: object) -> tuple[int, bytes, bytes]:
        if not argv or any(not isinstance(item, str) or not item for item in argv):
            raise ValueError("invalid fixed tool invocation")
        launcher = Path(argv[0]).name.casefold()
        if launcher in {"cmd.exe", "powershell.exe", "pwsh.exe", "bash", "sh"} or re.fullmatch(r"(?:py(?:\.exe)?|python(?:w|\d+(?:\.\d+)?)?(?:\.exe)?)", launcher):
            raise PermissionError("interpreter launchers are forbidden")
        result = self._execute(
            argv, cwd=cwd, timeout_seconds=timeout_seconds, environment=environment,
            guard=lambda: not bool(getattr(cancellation, "is_cancelled", lambda: False)()),
            preflight=False, strict_capture=False,
        )
        return result.exit_code, result.stdout, result.stderr

    def run_drc_baseline(self, argv: tuple[str, ...], *, cwd: Path, timeout_seconds: int,
                         environment: dict[str, str], cancellation: object) -> tuple[int, bytes, bytes]:
        """Fixed baseline CLI vector with a canonical phase check immediately before spawn."""
        if (type(argv) is not tuple or len(argv) != 8
            or any(type(item) is not str or not item or "\x00" in item for item in argv)
            or argv[1:6] != ("pcb", "drc", "--format", "json", "--output")):
            raise PermissionError("fixed baseline invocation required")
        executable, output, board = Path(argv[0]), Path(argv[6]), Path(argv[7])
        if (not isinstance(cwd, Path) or not cwd.is_absolute()
            or not all(path.is_absolute() for path in (executable, output, board))
            or executable.name.casefold() not in {"kicad-cli", "kicad-cli.exe"}
            or board.suffix.casefold() != ".kicad_pcb" or output.suffix.casefold() != ".json"
            or not board.resolve(strict=True).is_relative_to(cwd.resolve(strict=True))
            or output.resolve(strict=False).is_relative_to(cwd.resolve(strict=True))
            or not callable(getattr(cancellation, "is_cancelled", None))
            or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 600):
            raise PermissionError("fixed baseline paths or phase unavailable")
        result = self._execute(
            argv, cwd=cwd, timeout_seconds=timeout_seconds, environment=environment,
            guard=lambda: not bool(cancellation.is_cancelled()), preflight=True, strict_capture=True,
        )
        return result.exit_code, result.stdout, result.stderr

    def run_native_readback(self, invocation: object, phase: object) -> NativeProcessResult:
        """Launch only the frozen native read-back bundle after canonical phase checks."""
        from .native_move import NativeReadbackPhase
        from .native_move_pcbnew_reader import NativePcbnewReadbackInvocation

        if type(invocation) is not NativePcbnewReadbackInvocation or type(phase) is not NativeReadbackPhase:
            raise TypeError("trusted native invocation and phase are required")
        if not invocation.authorize(phase):
            raise PermissionError("native read-back authorization unavailable")
        argv = invocation.fixed_argv()
        if argv != (
            str(invocation.bundle.interpreter), "-I", "-S", str(invocation.bundle.wrapper),
            str(invocation.board_path), invocation.operation.native_uuid,
        ):
            raise PermissionError("native read-back argv drift")
        timeout_seconds = phase.timeout_seconds()
        if timeout_seconds < 1:
            raise TimeoutError("native read-back deadline expired")
        return self._execute(
            argv, cwd=invocation.bundle.root, timeout_seconds=timeout_seconds,
            environment=invocation.bundle.environment_dict(),
            guard=lambda: invocation.authorize(phase), preflight=True, strict_capture=True,
        )

    @staticmethod
    def _execute(
        argv: tuple[str, ...], *, cwd: Path, timeout_seconds: int,
        environment: dict[str, str], guard: Callable[[], bool],
        preflight: bool, strict_capture: bool,
    ) -> NativeProcessResult:
        if preflight and not guard():
            raise InterruptedError("operation cancelled before native dispatch")
        process = subprocess.Popen(
            list(argv), cwd=str(cwd), env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
        )
        stdout_buffer = bytearray()
        stderr_buffer = bytearray()
        overflow = [False, False]
        drain_failed = [False, False]
        capture_limit = 64 * 1024

        def drain(stream: object, buffer: bytearray, index: int) -> None:
            read = getattr(stream, "read", None)
            if not callable(read):
                drain_failed[index] = True
                return
            try:
                while True:
                    chunk = read(8192)
                    if not chunk:
                        return
                    remaining = capture_limit - len(buffer)
                    if len(chunk) > remaining:
                        overflow[index] = True
                    if remaining > 0:
                        buffer.extend(bytes(chunk)[:remaining])
            except (OSError, ValueError):
                drain_failed[index] = True
                return

        def stop_process(*, force: bool = False) -> None:
            if process.poll() is not None:
                return
            if force:
                process.kill()
            else:
                process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

        started_threads = []
        try:
            stdout_thread = threading.Thread(target=drain, args=(process.stdout, stdout_buffer, 0), daemon=True)
            stderr_thread = threading.Thread(target=drain, args=(process.stderr, stderr_buffer, 1), daemon=True)
            stdout_thread.start()
            started_threads.append(stdout_thread)
            stderr_thread.start()
            started_threads.append(stderr_thread)
            deadline = time.monotonic() + timeout_seconds
            while True:
                if not guard():
                    stop_process()
                    raise InterruptedError("operation cancelled")
                if time.monotonic() >= deadline:
                    stop_process(force=True)
                    raise TimeoutError("operation timed out")
                if process.poll() is not None:
                    break
                time.sleep(0.05)
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)
            if strict_capture and (stdout_thread.is_alive() or stderr_thread.is_alive()):
                raise TimeoutError("native output did not finish draining")
            if strict_capture and any(drain_failed):
                raise ValueError("native output drain failed")
            if strict_capture and time.monotonic() >= deadline:
                raise TimeoutError("native result exceeded deadline")
            if not guard():
                raise InterruptedError("operation cancelled after native result")
            result = NativeProcessResult(
                process.returncode or 0, bytes(stdout_buffer), bytes(stderr_buffer),
                overflow[0], overflow[1],
            )
            if strict_capture and (result.stdout_overflow or result.stderr_overflow):
                raise ValueError("native output exceeded capture limit")
            return result
        finally:
            cleanup_error = None
            try:
                if process.poll() is None:
                    try:
                        process.kill()
                    except Exception as error:  # noqa: BLE001 - continue cleanup before reporting first failure
                        cleanup_error = error
                    try:
                        process.wait(timeout=2)
                    except Exception as error:  # noqa: BLE001 - a failed reap is reported
                        if cleanup_error is None:
                            cleanup_error = error
            except Exception as error:  # noqa: BLE001 - still close and join created resources
                if cleanup_error is None:
                    cleanup_error = error
            for stream in (process.stdout, process.stderr):
                close = getattr(stream, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception as error:  # noqa: BLE001 - keep cleaning remaining resources
                        if cleanup_error is None:
                            cleanup_error = error
            for thread in started_threads:
                try:
                    thread.join(timeout=1)
                except Exception as error:  # noqa: BLE001 - keep joining remaining threads
                    if cleanup_error is None:
                        cleanup_error = error
            if cleanup_error is not None:
                raise cleanup_error


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


class FirmwareBuildToolAdapter(BuildToolAdapter):
    """Trusted real-build adapter used by the existing ExecutionWorker BUILD path."""

    def execute(self, operation: BuildOperation, context: ExecutionContext) -> AdapterResult:
        request = operation.firmware_build_request
        if request is None:
            return super().execute(operation, context)
        if context.tool is None or context.tool.executable is None:
            readiness = evaluate_build_readiness(request, workspace_root=context.workspace_root, tool=context.tool)
            result = _typed_build_result(request, readiness=readiness, status=FirmwareBuildResultStatus.BUILD_UNAVAILABLE, summary="trusted firmware toolchain unavailable")
            return AdapterResult("UNAVAILABLE", "trusted firmware toolchain unavailable", build_result=result)
        readiness = evaluate_build_readiness(request, workspace_root=context.workspace_root, tool=context.tool)
        if readiness.status is not BuildReadinessStatus.READY:
            result = _typed_build_result(request, readiness=readiness, status=FirmwareBuildResultStatus.BUILD_BLOCKED, summary="firmware build blocked by readiness preflight")
            return AdapterResult("FAILED", "firmware build blocked by readiness preflight", build_result=result)
        configuration = request.configuration
        if configuration is None:
            return AdapterResult("FAILED", "firmware build configuration missing")
        build_directory = hashlib.sha256(operation.operation_id.encode("utf-8")).hexdigest()
        build_root = (context.state_root / "build" / build_directory).resolve()
        if not build_root.is_relative_to(context.state_root.resolve()):
            return AdapterResult("FAILED", "build workspace rejected")
        build_root.mkdir(parents=True, exist_ok=True)
        output_root = (context.state_root / "output").resolve()
        if not output_root.is_relative_to(context.state_root.resolve()) or output_root.is_relative_to(context.workspace_root.resolve()):
            return AdapterResult("FAILED", "build output root rejected")
        output_root.mkdir(parents=True, exist_ok=True)
        if not output_root.is_dir():
            return AdapterResult("FAILED", "build output root rejected")
        executable = str(context.tool.executable)
        if configuration.build_system == "CMAKE" and configuration.target_kind == "STM32F103C8T6":
            return self._execute_cmake_stm32(
                operation,
                request,
                configuration,
                context,
                build_root=build_root,
                output_root=output_root,
            )
        try:
            argv = _firmware_build_argv(operation, request, configuration, executable, context.workspace_root, output_root)
        except (OSError, ValueError):
            return AdapterResult("FAILED", "trusted firmware build invocation rejected")
        try:
            code, stdout, stderr = self._runner.run(
                argv,
                cwd=context.workspace_root,
                timeout_seconds=context.tool.definition.timeout_seconds,
                environment=_minimal_environment(),
                cancellation=context.cancellation,
            )
        except InterruptedError:
            return AdapterResult("CANCELLED", "firmware build cancelled")
        except TimeoutError:
            result = _typed_build_result(request, readiness=readiness, status=FirmwareBuildResultStatus.BUILD_TIMEOUT, summary="firmware build timed out")
            return AdapterResult("TIMED_OUT", "firmware build timed out", build_result=result)
        except (OSError, PermissionError):
            result = _typed_build_result(request, readiness=readiness, status=FirmwareBuildResultStatus.BUILD_UNAVAILABLE, summary="firmware toolchain unavailable")
            return AdapterResult("UNAVAILABLE", "firmware toolchain unavailable", build_result=result)
        diagnostics = _safe_summary(stdout, stderr)
        log_content = _raw_stage_evidence(
            "firmware_build",
            code,
            stdout,
            stderr,
            workspace_root=context.workspace_root,
        )
        log_path = (build_root / "build.log").resolve()
        if not log_path.is_relative_to(build_root):
            return AdapterResult("FAILED", "build log path rejected")
        log_path.write_bytes(log_content)
        if code != 0:
            result = _typed_build_result(request, readiness=readiness, status=FirmwareBuildResultStatus.BUILD_FAIL, summary=diagnostics, diagnostics=(diagnostics,))
            return AdapterResult("FAILED", diagnostics, build_result=result)
        artifacts: list[tuple[ArtifactIdentity, bytes]] = []
        output_models: list[FirmwareBuildArtifact] = []
        primary: tuple[ArtifactIdentity, bytes] | None = None
        expected = tuple(dict.fromkeys(configuration.expected_artifact_types or (operation.expected_artifact_type,)))
        for artifact_type in expected:
            artifact_path = _find_artifact(output_root, artifact_type)
            if artifact_path is None or not artifact_path.resolve().is_relative_to(output_root):
                result = _typed_build_result(request, readiness=readiness, status=FirmwareBuildResultStatus.BUILD_FAIL, summary=f"build output {artifact_type} missing", diagnostics=(f"OUTPUT_MISSING_{artifact_type}",))
                return AdapterResult("FAILED", f"build output {artifact_type} missing", build_result=result)
            content = artifact_path.read_bytes()
            identity = _artifact_identity(operation, artifact_type, content, artifact_path.name)
            artifacts.append((identity, content))
            output_models.append(FirmwareBuildArtifact(reference=identity.artifact_id, artifact_type=artifact_type, content_fingerprint=identity.artifact_sha256, size_bytes=len(content), relative_path=f"build/{artifact_path.name}"))
            if primary is None and artifact_type == operation.expected_artifact_type:
                primary = (identity, content)
        log_identity = _artifact_identity(operation, "LOG", log_content, "build.log")
        artifacts.append((log_identity, log_content))
        evidence = BuildEvidence(
            operation_id=operation.operation_id,
            request_fingerprint=request.fingerprint,
            result_status="BUILD_PASS",
            toolchain_id=operation.toolchain.toolchain_id,
            toolchain_version=operation.toolchain.tool_version,
            source_fingerprints=tuple(item.content_fingerprint for item in request.source_artifacts),
            output_artifact_ids=tuple(item[0].artifact_id for item in artifacts),
            log_artifact_id=log_identity.artifact_id,
            raw_evidence_artifact_id=log_identity.artifact_id,
            diagnostics=(diagnostics,),
            created_at=operation.created_at,
        )
        result = _typed_build_result(request, readiness=readiness, status=FirmwareBuildResultStatus.BUILD_PASS, summary=diagnostics, outputs=tuple(output_models), log_artifact_reference=log_identity.artifact_id, diagnostics=(diagnostics,), receipt_reference=f"receipt-{operation.operation_id}", evidence_references=(f"build-evidence-{operation.operation_id}",))
        if primary is None:
            failed = _typed_build_result(
                request,
                readiness=readiness,
                status=FirmwareBuildResultStatus.BUILD_FAIL,
                summary="primary build artifact missing",
                diagnostics=("PRIMARY_OUTPUT_MISSING",),
            )
            return AdapterResult("FAILED", "primary build artifact missing", build_result=failed)
        return AdapterResult("SUCCEEDED", diagnostics, artifact=primary[0], artifact_content=primary[1], additional_artifacts=tuple(item for item in artifacts if item[0].artifact_id != primary[0].artifact_id), build_result=result, build_evidence=evidence, evidence_references=(f"build-evidence-{operation.operation_id}",))

    def _execute_cmake_stm32(
        self,
        operation: BuildOperation,
        request: FirmwareBuildRequest,
        configuration: FirmwareBuildConfiguration,
        context: ExecutionContext,
        *,
        build_root: Path,
        output_root: Path,
    ) -> AdapterResult:
        try:
            cmake = _trusted_auxiliary_executable("cmake.exe", "EMBRIX_CMAKE_EXECUTABLE")
            ninja = _trusted_auxiliary_executable("ninja.exe", "EMBRIX_NINJA_EXECUTABLE")
            toolchain_root = context.tool.executable.resolve(strict=True).parent.parent
            toolchain_bin = toolchain_root / "bin"
            if not toolchain_bin.is_dir() or not (toolchain_bin / context.tool.executable.name).is_file():
                raise ValueError("trusted Arm GNU toolchain root rejected")
            configure_argv, build_argv = _cmake_stm32_argv(
                operation,
                configuration,
                context.workspace_root,
                output_root,
                cmake=cmake,
                ninja=ninja,
                toolchain_root=toolchain_root,
            )
            environment = _minimal_environment()
            environment["PATH"] = os.pathsep.join(
                item for item in (str(toolchain_bin), str(cmake.parent), str(ninja.parent), environment.get("PATH", "")) if item
            )
        except (OSError, PermissionError, ValueError):
            return AdapterResult("UNAVAILABLE", "trusted CMake/Ninja build tools unavailable")

        try:
            configure_code, configure_stdout, configure_stderr = self._runner.run(
                configure_argv,
                cwd=context.workspace_root,
                timeout_seconds=context.tool.definition.timeout_seconds,
                environment=environment,
                cancellation=context.cancellation,
            )
            if configure_code != 0:
                diagnostics = _stage_diagnostics("cmake_configure", configure_code, configure_stdout, configure_stderr)
                raw_evidence = _raw_stage_evidence(
                    "cmake_configure",
                    configure_code,
                    configure_stdout,
                    configure_stderr,
                    workspace_root=context.workspace_root,
                )
                return _cmake_build_failure(request, operation, configuration, context, diagnostics, raw_evidence, build_root, configure_exit_code=configure_code, build_exit_code=None)
            build_code, build_stdout, build_stderr = self._runner.run(
                build_argv,
                cwd=context.workspace_root,
                timeout_seconds=context.tool.definition.timeout_seconds,
                environment=environment,
                cancellation=context.cancellation,
            )
        except InterruptedError:
            return AdapterResult("CANCELLED", "firmware build cancelled")
        except TimeoutError:
            result = _typed_build_result(request, readiness=evaluate_build_readiness(request, workspace_root=context.workspace_root, tool=context.tool), status=FirmwareBuildResultStatus.BUILD_TIMEOUT, summary="firmware build timed out")
            return AdapterResult("TIMED_OUT", "firmware build timed out", build_result=result)
        except (OSError, PermissionError):
            result = _typed_build_result(request, readiness=evaluate_build_readiness(request, workspace_root=context.workspace_root, tool=context.tool), status=FirmwareBuildResultStatus.BUILD_UNAVAILABLE, summary="firmware build tools unavailable")
            return AdapterResult("UNAVAILABLE", "firmware build tools unavailable", build_result=result)

        diagnostics = _stage_diagnostics(
            "cmake_build",
            build_code,
            configure_stdout + build_stdout,
            configure_stderr + build_stderr,
        )
        log_content = _raw_stage_evidence(
            "cmake_build",
            build_code,
            configure_stdout + build_stdout,
            configure_stderr + build_stderr,
            workspace_root=context.workspace_root,
        )
        log_path = (build_root / "build.log").resolve()
        if not log_path.is_relative_to(build_root):
            return AdapterResult("FAILED", "build log path rejected")
        log_path.write_bytes(log_content)
        readiness = evaluate_build_readiness(request, workspace_root=context.workspace_root, tool=context.tool)
        if build_code != 0:
            return _cmake_build_failure(request, operation, configuration, context, diagnostics, log_content, build_root, configure_exit_code=configure_code, build_exit_code=build_code)

        expected = tuple(dict.fromkeys(configuration.expected_artifact_types or (operation.expected_artifact_type,)))
        artifacts: list[tuple[ArtifactIdentity, bytes]] = []
        output_models: list[FirmwareBuildArtifact] = []
        primary: tuple[ArtifactIdentity, bytes] | None = None
        for artifact_type in expected:
            artifact_path = _find_artifact(
                output_root,
                artifact_type,
                preferred_stem=configuration.output_stem,
            )
            if artifact_path is None or not artifact_path.resolve().is_relative_to(output_root):
                result = _typed_build_result(request, readiness=readiness, status=FirmwareBuildResultStatus.BUILD_FAIL, summary=f"build output {artifact_type} missing", diagnostics=(f"OUTPUT_MISSING_{artifact_type}",))
                return AdapterResult("FAILED", f"build output {artifact_type} missing", build_result=result)
            content = artifact_path.read_bytes()
            identity = _artifact_identity(operation, artifact_type, content, artifact_path.name)
            artifacts.append((identity, content))
            output_models.append(FirmwareBuildArtifact(reference=identity.artifact_id, artifact_type=artifact_type, content_fingerprint=identity.artifact_sha256, size_bytes=len(content), relative_path=f"build/{artifact_path.name}"))
            if primary is None and artifact_type == operation.expected_artifact_type:
                primary = (identity, content)

        elf = next((item for item in artifacts if item[0].artifact_type == "ELF"), None)
        binary = next((item for item in artifacts if item[0].artifact_type == "BIN"), None)
        elf_verified = bool(elf and _verify_arm_elf(elf[1]))
        bin_verified = bool(binary and len(binary[1]) > 0)
        if not elf_verified:
            return AdapterResult("FAILED", "ELF artifact verification failed", build_result=_typed_build_result(request, readiness=readiness, status=FirmwareBuildResultStatus.BUILD_FAIL, summary="ELF artifact verification failed", diagnostics=("ELF_MAGIC_INVALID",)))
        if "BIN" in expected and not bin_verified:
            return AdapterResult("FAILED", "BIN artifact verification failed", build_result=_typed_build_result(request, readiness=readiness, status=FirmwareBuildResultStatus.BUILD_FAIL, summary="BIN artifact verification failed", diagnostics=("BIN_EMPTY",)))

        log_identity = _artifact_identity(operation, "LOG", log_content, "build.log")
        artifacts.append((log_identity, log_content))
        evidence = BuildEvidence(
            operation_id=operation.operation_id,
            request_fingerprint=request.fingerprint,
            result_status="BUILD_PASS",
            toolchain_id=operation.toolchain.toolchain_id,
            toolchain_version=operation.toolchain.tool_version,
            source_fingerprints=tuple(item.content_fingerprint for item in request.source_artifacts),
            output_artifact_ids=tuple(item[0].artifact_id for item in artifacts),
            log_artifact_id=log_identity.artifact_id,
            raw_evidence_artifact_id=log_identity.artifact_id,
            diagnostics=(diagnostics,),
            tool_observed=True,
            build_system=configuration.build_system,
            cmake_configure_exit_code=configure_code,
            compiler_exit_code=0,
            assembler_exit_code=0,
            linker_exit_code=0,
            objcopy_exit_code=0 if binary is not None else None,
            elf_verified=elf_verified,
            bin_verified=bin_verified,
            created_at=operation.created_at,
        )
        result = _typed_build_result(request, readiness=readiness, status=FirmwareBuildResultStatus.BUILD_PASS, summary=diagnostics, outputs=tuple(output_models), log_artifact_reference=log_identity.artifact_id, diagnostics=(diagnostics,), receipt_reference=f"receipt-{operation.operation_id}", evidence_references=(f"build-evidence-{operation.operation_id}",))
        if primary is None:
            failed = _typed_build_result(request, readiness=readiness, status=FirmwareBuildResultStatus.BUILD_FAIL, summary="primary build artifact missing", diagnostics=("PRIMARY_OUTPUT_MISSING",))
            return AdapterResult("FAILED", "primary build artifact missing", build_result=failed)
        return AdapterResult("SUCCEEDED", diagnostics, artifact=primary[0], artifact_content=primary[1], additional_artifacts=tuple(item for item in artifacts if item[0].artifact_id != primary[0].artifact_id), build_result=result, build_evidence=evidence, evidence_references=(f"build-evidence-{operation.operation_id}",))


def _firmware_build_argv(
    operation: BuildOperation,
    request: FirmwareBuildRequest,
    configuration: FirmwareBuildConfiguration,
    executable: str,
    workspace_root: Path,
    output_root: Path,
) -> tuple[str, ...]:
    if configuration.build_system == "CMAKE_NINJA":
        return (executable, "--build", str(output_root), "--config", configuration.profile)
    if configuration.build_system == "ESP_IDF":
        return (executable, "build")
    if configuration.build_system == "ARM_NONE_EABI_GCC":
        source_paths = tuple(
            str((workspace_root / item.relative_path).resolve())
            for item in request.source_artifacts
        )
        if any(not Path(item).is_relative_to(workspace_root.resolve()) for item in source_paths):
            raise ValueError("source path escaped workspace")
        output = (output_root / f"{configuration.output_stem}.elf").resolve()
        if not output.is_relative_to(output_root):
            raise ValueError("output path escaped build root")
        argv: list[str] = [executable, *configuration.compiler_flags, *source_paths, "-o", str(output)]
        if configuration.linker_script is not None:
            linker = (workspace_root / configuration.linker_script).resolve()
            if not linker.is_relative_to(workspace_root.resolve()) or not linker.is_file():
                raise ValueError("linker script is unavailable")
            argv.extend(("-T", str(linker)))
        return tuple(argv)
    raise ValueError("unsupported build system")


def _trusted_auxiliary_executable(expected_name: str, environment_key: str) -> Path:
    configured = os.environ.get(environment_key, "").strip()
    if not configured:
        raise ValueError(f"{environment_key} is required")
    executable = Path(configured).resolve(strict=True)
    if executable.name.casefold() != expected_name.casefold() or not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError("trusted auxiliary executable rejected")
    return executable


def _cmake_stm32_argv(
    operation: BuildOperation,
    configuration: FirmwareBuildConfiguration,
    workspace_root: Path,
    output_root: Path,
    *,
    cmake: Path,
    ninja: Path,
    toolchain_root: Path,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    project_file = (workspace_root / configuration.cmake_project).resolve()
    toolchain_file = (workspace_root / configuration.cmake_toolchain_file).resolve()
    if not project_file.is_relative_to(workspace_root.resolve()) or not project_file.is_file():
        raise ValueError("CMake project file is unavailable")
    if not toolchain_file.is_relative_to(workspace_root.resolve()) or not toolchain_file.is_file():
        raise ValueError("CMake toolchain file is unavailable")
    configure = (
        str(cmake),
        "-S", str(workspace_root.resolve()),
        "-B", str(output_root),
        "-G", "Ninja",
        f"-DCMAKE_TOOLCHAIN_FILE={toolchain_file}",
        f"-DEMBRIX_TOOLCHAIN_ROOT={toolchain_root}",
        f"-DCMAKE_BUILD_TYPE={configuration.profile}",
        f"-DCMAKE_MAKE_PROGRAM={ninja}",
    )
    build = (str(cmake), "--build", str(output_root), "--config", configuration.profile, "--verbose")
    return configure, build


def _stage_diagnostics(stage: str, code: int, stdout: bytes, stderr: bytes) -> str:
    return (
        f"{stage}_exit_code={code}; "
        f"output_bytes={len(stdout[:64 * 1024])}; "
        f"error_output_bytes={len(stderr[:64 * 1024])}"
    )


def _raw_stage_evidence(
    stage: str,
    code: int,
    stdout: bytes,
    stderr: bytes,
    *,
    workspace_root: Path,
) -> bytes:
    """Keep compiler evidence while removing machine-specific local paths."""

    def sanitize(value: bytes) -> str:
        rendered = value.decode("utf-8", errors="replace")
        for path in (str(workspace_root.resolve()), workspace_root.resolve().as_posix()):
            rendered = rendered.replace(path, "<PROJECT>")
        rendered = re.sub(
            r"(?i)(?<![A-Za-z0-9])(?:[A-Z]:[\\/]|\\\\)[^\r\n\s\"']+",
            "<LOCAL_PATH>",
            rendered,
        )
        return rendered

    content = (
        f"stage={stage}\n"
        f"exit_code={code}\n"
        "--- stdout ---\n"
        f"{sanitize(stdout)}\n"
        "--- stderr ---\n"
        f"{sanitize(stderr)}\n"
    )
    return content.encode("utf-8", errors="replace")[:64 * 1024]


def _verify_arm_elf(content: bytes) -> bool:
    """Verify the minimum ELF32 little-endian ARM firmware identity."""

    if len(content) < 28 or content[:4] != b"\x7fELF":
        return False
    elf_class = content[4]
    data_encoding = content[5]
    machine = int.from_bytes(content[18:20], byteorder="little")
    entry = int.from_bytes(content[24:28], byteorder="little")
    return elf_class == 1 and data_encoding == 1 and machine == 40 and entry != 0


def _cmake_build_failure(
    request: FirmwareBuildRequest,
    operation: BuildOperation,
    configuration: FirmwareBuildConfiguration,
    context: ExecutionContext,
    diagnostics: str,
    raw_evidence: bytes,
    build_root: Path,
    *,
    configure_exit_code: int,
    build_exit_code: int | None,
) -> AdapterResult:
    log_content = raw_evidence[:64 * 1024]
    log_path = (build_root / "build.log").resolve()
    if not log_path.is_relative_to(build_root):
        return AdapterResult("FAILED", "build log path rejected")
    log_path.write_bytes(log_content)
    readiness = evaluate_build_readiness(request, workspace_root=context.workspace_root, tool=context.tool)
    log_identity = _artifact_identity(operation, "LOG", log_content, "build.log")
    evidence = BuildEvidence(
        operation_id=operation.operation_id,
        request_fingerprint=request.fingerprint,
        result_status="BUILD_FAIL",
        toolchain_id=operation.toolchain.toolchain_id,
        toolchain_version=operation.toolchain.tool_version,
        source_fingerprints=tuple(item.content_fingerprint for item in request.source_artifacts),
        output_artifact_ids=(log_identity.artifact_id,),
        log_artifact_id=log_identity.artifact_id,
        raw_evidence_artifact_id=log_identity.artifact_id,
        diagnostics=(diagnostics,),
        tool_observed=True,
        build_system=configuration.build_system,
        cmake_configure_exit_code=configure_exit_code,
        compiler_exit_code=build_exit_code,
        created_at=operation.created_at,
    )
    result = _typed_build_result(
        request,
        readiness=readiness,
        status=FirmwareBuildResultStatus.BUILD_FAIL,
        summary=diagnostics,
        log_artifact_reference=log_identity.artifact_id,
        diagnostics=(diagnostics,),
        evidence_references=(f"build-evidence-{operation.operation_id}",),
    )
    return AdapterResult(
        "FAILED",
        diagnostics,
        additional_artifacts=((log_identity, log_content),),
        build_result=result,
        build_evidence=evidence,
        evidence_references=(f"build-evidence-{operation.operation_id}",),
    )


def _artifact_identity(operation: BuildOperation, artifact_type: str, content: bytes, name: str) -> ArtifactIdentity:
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", name)[:80] or "output"
    return ArtifactIdentity(
        artifact_id=f"artifact-{operation.operation_id}-{artifact_type.casefold()}-{safe_name}",
        tenant_id=operation.tenant_id,
        project_id=operation.project_id,
        target_id=operation.target_id,
        workspace_fingerprint=operation.workspace_fingerprint,
        build_operation_id=operation.operation_id,
        toolchain_id=operation.toolchain.toolchain_id,
        toolchain_version=operation.toolchain.tool_version,
        build_configuration=operation.configuration,
        artifact_type=artifact_type,
        artifact_sha256=digest,
        size_bytes=len(content),
        created_at=operation.created_at,
    )


def _typed_build_result(
    request: FirmwareBuildRequest,
    *,
    readiness: BuildReadinessResult,
    status: FirmwareBuildResultStatus,
    summary: str,
    outputs: tuple[FirmwareBuildArtifact, ...] = (),
    log_artifact_reference: str | None = None,
    diagnostics: tuple[str, ...] = (),
    receipt_reference: str | None = None,
    evidence_references: tuple[str, ...] = (),
) -> FirmwareBuildResult:
    old_status = (
        FirmwareBuildStatus.SUCCESS
        if status is FirmwareBuildResultStatus.BUILD_PASS
        else FirmwareBuildStatus.UNAVAILABLE
        if status in {FirmwareBuildResultStatus.BUILD_BLOCKED, FirmwareBuildResultStatus.BUILD_UNAVAILABLE, FirmwareBuildResultStatus.BUILD_TIMEOUT}
        else FirmwareBuildStatus.FAILED
    )
    primary = outputs[0].reference if outputs else "unavailable-artifact"
    return FirmwareBuildResult.create(
        status=old_status,
        artifact_reference=primary,
        build_status=old_status,
        summary=summary,
        request_fingerprint=request.fingerprint,
        result_status=status,
        readiness=readiness,
        outputs=outputs,
        log_artifact_reference=log_artifact_reference,
        diagnostics=diagnostics,
        receipt_reference=receipt_reference,
        evidence_references=evidence_references,
    )


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


def _find_artifact(root: Path, artifact_type: str, *, preferred_stem: str | None = None) -> Path | None:
    suffix = {"ELF": ".elf", "BIN": ".bin", "HEX": ".hex", "MAP": ".map"}[artifact_type]
    matches = sorted(path for path in root.rglob(f"*{suffix}") if path.is_file())
    if preferred_stem:
        preferred = [path for path in matches if path.stem == preferred_stem]
        if len(preferred) == 1:
            return preferred[0]
    return matches[0] if len(matches) == 1 else None


def _minimal_environment() -> dict[str, str]:
    # KiCad resolves its project symbol table through the Windows user
    # configuration roots as well as the explicitly supplied library path.
    # Keep this allowlist narrow while retaining the deterministic desktop
    # identity required by the trusted KiCad CLI.
    allowed = {
        "SystemRoot",
        "TEMP",
        "TMP",
        "PATH",
        "LANG",
        "LC_ALL",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "HOMEDRIVE",
        "HOMEPATH",
        "ProgramData",
        "KICAD_CONFIG_HOME",
    }
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
    "FirmwareBuildToolAdapter",
    "FlashToolAdapter",
    "HilScenarioAdapter",
    "ProcessRunnerPort",
    "SafeProcessRunner",
    "UartObservationAdapter",
    "UnavailableTestAdapter",
    "probe_trusted_tool_version",
    "run_kicad_cli",
)
