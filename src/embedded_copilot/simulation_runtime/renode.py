from __future__ import annotations

import copy
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from embedded_copilot.execution_worker.adapters import (
    ProcessRunnerPort,
    run_trusted_tool,
)

from .contracts import (
    PreparedSimulation,
    SimulationAssertionResult,
    SimulationBackend,
    SimulationBackendCapability,
    SimulationCapabilityStatus,
    SimulationEvidence,
    SimulationExecutionMode,
    SimulationFailureClass,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
    validate_request,
)
from .exceptions import SimulationUnavailable
from .ports import (
    SimulationAdapterExecutionContext,
    SimulationAdapterPort,
    SimulationClockPort,
    SimulationEvidenceStorePort,
)


@dataclass(frozen=True, slots=True)
class RenodeGPIOConfig:
    """Typed GPIO mapping supplied by the approved assembly/golden boundary."""

    target_kind: str
    platform_descriptor: str
    led_port: str
    led_pin: int
    button_port: str
    button_pin: int
    model_fidelity: str = "PARTIAL_RCC"

    def __post_init__(self) -> None:
        if self.target_kind != "STM32F103C8T6":
            raise ValueError("Renode GPIO target is not approved")
        if self.platform_descriptor != "stm32f103":
            raise ValueError("Renode platform descriptor is not approved")
        if self.led_port not in {"A", "B", "C"} or self.button_port not in {"A", "B", "C"}:
            raise ValueError("Renode GPIO port is invalid")
        if type(self.led_pin) is not int or not 0 <= self.led_pin <= 15:
            raise ValueError("Renode LED pin is invalid")
        if type(self.button_pin) is not int or not 0 <= self.button_pin <= 15:
            raise ValueError("Renode button pin is invalid")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,31}", self.model_fidelity):
            raise ValueError("Renode model fidelity is invalid")


class RenodeSimulationAdapter(SimulationAdapterPort):
    """Runs the current firmware ELF through the official Renode executable."""

    __slots__ = ("_clock", "_config", "_disabled_reason", "_evidence", "_executable", "_platform", "_runner")

    def __init__(
        self,
        *,
        executable: Path | None = None,
        platform_file: Path | None = None,
        gpio: RenodeGPIOConfig | None = None,
        runner: ProcessRunnerPort | None = None,
        evidence_store: SimulationEvidenceStorePort | None = None,
        clock: SimulationClockPort | None = None,
    ) -> None:
        self._clock = clock
        self._disabled_reason = None
        if executable is None or platform_file is None or gpio is None or runner is None:
            self._executable = None
            self._platform = None
            self._config = None
            self._runner = None
            self._evidence = evidence_store
            self._disabled_reason = "RENODE_NOT_INSTALLED"
            return
        # Keep the operator-supplied absolute identity intact. On Windows the
        # desktop sandbox can virtualize AppData during Path.resolve(), which
        # would make Renode load a different, non-trusted model path.
        executable = Path(executable)
        platform_file = Path(platform_file)
        if executable.name.casefold() != "renode.exe" or not executable.is_file():
            raise ValueError("Renode executable is not a trusted file")
        if platform_file.name.casefold() != "stm32f103.repl" or not platform_file.is_file():
            raise ValueError("Renode platform model is not a trusted file")
        if not callable(getattr(runner, "run", None)):
            raise TypeError("Renode process runner is invalid")
        if evidence_store is not None and not isinstance(evidence_store, SimulationEvidenceStorePort):
            raise TypeError("Renode evidence store is invalid")
        self._executable = executable
        self._platform = platform_file
        self._config = gpio
        self._runner = runner
        self._evidence = evidence_store

    def probe(self) -> SimulationBackendCapability:
        if self._disabled_reason is not None:
            return SimulationBackendCapability.create(
                backend=SimulationBackend.RENODE,
                status=SimulationCapabilityStatus.UNAVAILABLE,
                version=None,
                execution_mode=SimulationExecutionMode.LOCAL_PROCESS,
                supported_targets=(),
                reason_code=self._disabled_reason,
            )
        try:
            output = run_trusted_tool(
                self._runner,
                (str(self._executable), "--version"),
                cwd=self._executable.parent,
                timeout_seconds=15,
            )
            if output[0] != 0:
                raise ValueError("Renode version probe failed")
            version = self._parse_version(output[1] + b"\n" + output[2])
            return SimulationBackendCapability.create(
                backend=SimulationBackend.RENODE,
                status=SimulationCapabilityStatus.VERIFIED,
                version=version,
                execution_mode=SimulationExecutionMode.LOCAL_PROCESS,
                supported_targets=(self._config.target_kind,),
                reason_code="RENODE_STM32F103_MODEL_AVAILABLE",
            )
        except (OSError, PermissionError, TimeoutError, ValueError):
            return SimulationBackendCapability.create(
                backend=SimulationBackend.RENODE,
                status=SimulationCapabilityStatus.UNAVAILABLE,
                version=None,
                execution_mode=SimulationExecutionMode.LOCAL_PROCESS,
                supported_targets=(),
                reason_code="RENODE_NOT_AVAILABLE",
            )

    def prepare(self, request: SimulationRequest) -> PreparedSimulation:
        if self._disabled_reason is not None:
            raise SimulationUnavailable(self._disabled_reason)
        checked = validate_request(request)
        if checked.backend is not SimulationBackend.RENODE:
            raise SimulationUnavailable("RENODE_BACKEND_REQUIRED")
        if checked.platform_descriptor != self._config.platform_descriptor:
            raise SimulationUnavailable("RENODE_PLATFORM_UNSUPPORTED")
        digest = hashlib.sha256(checked.fingerprint.encode("utf-8")).hexdigest()[:48]
        return PreparedSimulation.create(
            prepared_id=f"prepared-{digest}",
            request=checked,
            session_model="ADAPTER_INTERNAL_EPHEMERAL",
        )

    def execute(
        self,
        prepared: PreparedSimulation,
        execution_context: SimulationAdapterExecutionContext,
    ) -> SimulationResult:
        checked = PreparedSimulation.model_validate(copy.deepcopy(prepared.model_dump(mode="python")))
        request = checked.request
        started = self._now()
        artifact_store = execution_context.artifact_store
        if artifact_store is None or not callable(getattr(artifact_store, "read", None)):
            return self._result(request, execution_context.operation_id, started, SimulationStatus.INFRASTRUCTURE_ERROR, SimulationFailureClass.INFRASTRUCTURE, "SIMULATION_ARTIFACT_STORE_UNAVAILABLE", (), "artifact store was not supplied")
        try:
            content = artifact_store.read(request.firmware_artifact)
        except (FileNotFoundError, OSError):
            return self._result(request, execution_context.operation_id, started, SimulationStatus.FAIL, SimulationFailureClass.ENGINEERING, "STALE_ELF_ARTIFACT", (), "the bound ELF artifact is unavailable")
        except ValueError:
            return self._result(request, execution_context.operation_id, started, SimulationStatus.FAIL, SimulationFailureClass.ENGINEERING, "STALE_ELF_ARTIFACT", (), "the bound ELF artifact failed identity verification")
        elf_reason = self._validate_elf(content)
        if elf_reason is not None:
            return self._result(request, execution_context.operation_id, started, SimulationStatus.FAIL, SimulationFailureClass.ENGINEERING, elf_reason, (), "the bound artifact is not a 32-bit ARM ELF")

        try:
            # Renode's script tokenizer on Windows does not accept the Unicode
            # workspace path used by this desktop checkout. The temporary
            # process directory is therefore kept in the user TEMP boundary;
            # firmware bytes still come exclusively from the verified artifact
            # store and no workspace file is modified.
            with tempfile.TemporaryDirectory(prefix="embrix-renode-") as directory:
                run_root = Path(directory)
                firmware = run_root / "firmware.elf"
                script = run_root / "scenario.resc"
                firmware.write_bytes(content)
                script.write_text(self._script(firmware), encoding="utf-8")
                exit_code, stdout, stderr = self._runner.run(
                    (
                        str(self._executable),
                        "--disable-xwt",
                        "--console",
                        "--execute",
                        f"include @{str(script).replace(chr(92), '/')}",
                    ),
                    cwd=run_root,
                    timeout_seconds=request.timeout_seconds,
                    environment=self._process_environment(),
                    cancellation=execution_context.cancellation,
                )
        except TimeoutError:
            return self._result(request, execution_context.operation_id, started, SimulationStatus.TIMEOUT, SimulationFailureClass.INFRASTRUCTURE, "PROCESS_CONTROL_TIMEOUT", (), "Renode exceeded the bounded execution timeout")
        except (OSError, PermissionError):
            return self._result(request, execution_context.operation_id, started, SimulationStatus.INFRASTRUCTURE_ERROR, SimulationFailureClass.INFRASTRUCTURE, "RENODE_PROCESS_START_FAILED", (), "Renode could not be started")

        output = (stdout + b"\n" + stderr)[:64 * 1024].decode("utf-8", errors="replace")
        reset_reached = "EMBRIX_RESET_HANDLER" in output
        main_reached = "EMBRIX_MAIN_REACHED" in output
        elf_loaded = bool(re.search(r"Load(?:ed|ing) (?:block|section)|ELF", output, re.IGNORECASE))
        initial = self._gpio_observation(output, "EMBRIX_STAGE_INITIAL")
        pressed = self._gpio_observation(output, "EMBRIX_STAGE_PRESSED")
        released = self._gpio_observation(output, "EMBRIX_STAGE_RELEASED")
        observations = tuple(item for item in (initial, pressed, released) if item is not None)
        configuration = self._gpio_configuration_observation(output)
        stimuli = ("initial:button_released", "stimulus:button_pressed", "stimulus:button_released")
        boot_ok = elf_loaded and reset_reached and main_reached
        gpio_ok = len(observations) == 3 and self._gpio_expected(observations)
        assertions = tuple(
            SimulationAssertionResult.create(
                assertion_id=assertion.assertion_id,
                kind=assertion.kind,
                passed=(exit_code == assertion.expected if assertion.kind.value == "EXIT_STATUS" else boot_ok and gpio_ok),
                observed_summary=(f"renode_exit_code={exit_code}" if assertion.kind.value == "EXIT_STATUS" else f"boot={boot_ok},gpio={gpio_ok}"),
                evidence_reference=f"simulation-evidence:{request.simulation_id}:{index}",
            )
            for index, assertion in enumerate(request.assertions)
        )
        if exit_code != 0:
            status, failure, reason = SimulationStatus.INFRASTRUCTURE_ERROR, SimulationFailureClass.INFRASTRUCTURE, "BACKEND_PROCESS_EXITED"
        elif not boot_ok:
            status, failure, reason = SimulationStatus.FAIL, SimulationFailureClass.ENGINEERING, "FIRMWARE_BOOT_NOT_REACHED"
        elif not gpio_ok:
            status, failure, reason = SimulationStatus.FAIL, SimulationFailureClass.ENGINEERING, "GPIO_STIMULUS_ASSERTION_FAILED"
        elif not all(item.passed for item in assertions):
            status, failure, reason = SimulationStatus.FAIL, SimulationFailureClass.ENGINEERING, "SIMULATION_ASSERTION_FAILED"
        else:
            status, failure, reason = SimulationStatus.PASS, SimulationFailureClass.NONE, "SIMULATION_ASSERTIONS_PASSED"
        evidence_id = f"simulation-evidence-{hashlib.sha256((request.fingerprint + execution_context.operation_id).encode()).hexdigest()[:48]}"
        evidence = SimulationEvidence.create(
            evidence_id=evidence_id,
            operation_id=execution_context.operation_id,
            simulation_id=request.simulation_id,
            backend=SimulationBackend.RENODE,
            backend_version=self.probe().version or "unknown",
            model_fidelity=self._config.model_fidelity,
            artifact_id=request.firmware_artifact.artifact_id,
            artifact_sha256=request.firmware_artifact.artifact_sha256,
            request_fingerprint=request.fingerprint,
            elf_loaded=elf_loaded,
            cpu_started=reset_reached or main_reached,
            reset_handler_reached=reset_reached,
            main_reached=main_reached,
            observations=observations,
            stimuli=stimuli,
            configuration_observations=configuration,
            evidence_summary="real Renode execution observed boot hooks and GPIO register transitions, RCC APB2ENR uses the model SVD fallback",
            receipt_reference=f"operation:{execution_context.operation_id}",
            created_at=self._now(),
        )
        if self._evidence is not None:
            try:
                self._evidence.put(evidence)
            except (OSError, ValueError):
                return self._result(request, execution_context.operation_id, started, SimulationStatus.INFRASTRUCTURE_ERROR, SimulationFailureClass.INFRASTRUCTURE, "SIMULATION_EVIDENCE_PERSISTENCE_FAILED", (), "sanitized simulation evidence could not be persisted")
        return self._result(
            request,
            execution_context.operation_id,
            started,
            status,
            failure,
            reason,
            assertions,
            "real Renode process execution completed with bounded GPIO observations",
            evidence_id=evidence_id,
            observations=observations,
        )

    def cleanup(self, prepared: PreparedSimulation, execution_context: SimulationAdapterExecutionContext) -> None:
        return None

    def _script(self, firmware: Path) -> str:
        platform = str(self._platform).replace("\\", "/")
        elf = str(firmware).replace("\\", "/")
        return "\n".join(
            (
                "mach create",
                f"machine LoadPlatformDescription @{platform}",
                f"sysbus LoadELF @{elf}",
                f'machine LoadPlatformDescriptionFromString "led: Miscellaneous.LED @ gpioPort{self._config.led_port} {self._config.led_pin}"',
                f'machine LoadPlatformDescriptionFromString "button: Miscellaneous.Button @ gpioPort{self._config.button_port} {self._config.button_pin} {{ IRQ -> gpioPort{self._config.button_port}@{self._config.button_pin} }}"',
                'sysbus.cpu AddHook `sysbus GetSymbolAddress "Reset_Handler"` "cpu.ErrorLog(\'EMBRIX_RESET_HANDLER\')"',
                'sysbus.cpu AddHook `sysbus GetSymbolAddress "main"` "cpu.ErrorLog(\'EMBRIX_MAIN_REACHED\')"',
                "sysbus.gpioPortB.button Press",
                'emulation RunFor "0.001"',
                'cpu ErrorLog "EMBRIX_GPIO_CONFIGURATION"',
                f"sysbus.gpioPort{self._config.led_port} ReadDoubleWord 0x04",
                f"sysbus.gpioPort{self._config.button_port} ReadDoubleWord 0x00",
                'cpu ErrorLog "EMBRIX_STAGE_INITIAL"',
                f"sysbus.gpioPort{self._config.led_port} ReadDoubleWord 0x0C",
                f"sysbus.gpioPort{self._config.button_port} ReadDoubleWord 0x08",
                "sysbus.gpioPortB.button Release",
                'emulation RunFor "0.001"',
                'cpu ErrorLog "EMBRIX_STAGE_PRESSED"',
                f"sysbus.gpioPort{self._config.led_port} ReadDoubleWord 0x0C",
                f"sysbus.gpioPort{self._config.button_port} ReadDoubleWord 0x08",
                "sysbus.gpioPortB.button Press",
                'emulation RunFor "0.001"',
                'cpu ErrorLog "EMBRIX_STAGE_RELEASED"',
                f"sysbus.gpioPort{self._config.led_port} ReadDoubleWord 0x0C",
                f"sysbus.gpioPort{self._config.button_port} ReadDoubleWord 0x08",
                "quit",
                "",
            )
        )

    def _gpio_observation(self, output: str, marker: str) -> str | None:
        tail = output.split(marker, 1)[1] if marker in output else ""
        values = re.findall(r"(?m)^\s*(0x[0-9a-fA-F]{1,8})\s*$", tail)
        if len(values) < 2:
            return None
        led = int(values[0], 16)
        button = int(values[1], 16)
        return f"{marker.casefold()}:led_raw=0x{led:08x},button_raw=0x{button:08x}"

    def _gpio_configuration_observation(self, output: str) -> tuple[str, ...]:
        if "EMBRIX_GPIO_CONFIGURATION" not in output:
            return ()
        tail = output.split("EMBRIX_GPIO_CONFIGURATION", 1)[1]
        values = re.findall(r"(?m)^\s*(0x[0-9a-fA-F]{1,8})\s*$", tail)
        if len(values) < 2:
            return ()
        led_register = int(values[0], 16)
        button_register = int(values[1], 16)
        return (
            (
                "gpio_configuration:"
                f"{self._config.led_port.casefold()}_crh=0x{led_register:08x},"
                f"{self._config.button_port.casefold()}_crl=0x{button_register:08x}"
            ),
        )

    def _gpio_expected(self, observations: tuple[str, ...]) -> bool:
        values = []
        for item in observations:
            match = re.search(r"led_raw=(0x[0-9a-f]+),button_raw=(0x[0-9a-f]+)", item)
            if match is None:
                return False
            values.append((int(match.group(1), 16), int(match.group(2), 16)))
        mask = 1 << self._config.led_pin
        button_mask = 1 << self._config.button_pin
        return (
            values[0][0] & mask == 0 and values[0][1] & button_mask != 0
            and values[1][0] & mask != 0 and values[1][1] & button_mask == 0
            and values[2][0] & mask == 0 and values[2][1] & button_mask != 0
        )

    @staticmethod
    def _parse_version(output: bytes) -> str:
        match = re.search(rb"(?<![A-Za-z0-9])v?(\d+\.\d+\.\d+)(?![A-Za-z0-9])", output[:4096])
        if match is None:
            raise ValueError("Renode version was not reported")
        return match.group(1).decode("ascii")

    @staticmethod
    def _validate_elf(content: bytes) -> str | None:
        if len(content) < 20 or content[:4] != b"\x7fELF":
            return "INVALID_ELF_ARTIFACT"
        if content[4] != 1 or int.from_bytes(content[18:20], "little") != 40:
            return "ELF_TARGET_ARCHITECTURE_MISMATCH"
        return None

    def _result(self, request: SimulationRequest, operation_id: str, started: datetime, status: SimulationStatus, failure: SimulationFailureClass, reason: str, assertions: tuple[SimulationAssertionResult, ...], summary: str, *, evidence_id: str | None = None, observations: tuple[str, ...] = ()) -> SimulationResult:
        refs = [f"artifact:{request.firmware_artifact.artifact_id}"]
        telemetry = []
        if evidence_id is not None:
            refs.append(evidence_id)
            telemetry.append(f"telemetry:{request.simulation_id}:gpio")
        return SimulationResult.create(
            simulation_id=request.simulation_id,
            operation_id=operation_id,
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            backend=SimulationBackend.RENODE,
            status=status,
            failure_class=failure,
            reason_code=reason,
            started_at=started,
            finished_at=self._now(),
            assertion_results=assertions,
            artifact_references=tuple(refs),
            telemetry_references=tuple(telemetry),
            diagnostic_summary=summary,
            request_fingerprint=request.fingerprint,
        )

    def _now(self) -> datetime:
        return self._clock.now() if self._clock is not None else datetime.now(UTC)

    def _process_environment(self) -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key in {"SystemRoot", "TEMP", "TMP", "LANG", "LC_ALL"}
        }
        environment["PATH"] = os.environ.get("PATH", "")
        return environment


__all__ = ("RenodeGPIOConfig", "RenodeSimulationAdapter")
