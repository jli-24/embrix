from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from embedded_copilot.execution_worker.contracts import HardwareBringupOperation
from embedded_copilot.execution_worker.ports import AdapterResult, ExecutionContext

from .bringup_contracts import (
    BootEvidence,
    BringupResult,
    FlashEvidence,
    HardwareConnection,
    HardwareConnectionStatus,
    HardwareLineage,
    HardwareSession,
    HardwareSessionStatus,
    HardwareToolResult,
    UartEvidence,
)
from .bringup_ports import (
    HardwareBringupBackendPort,
    HardwareCommandRunnerPort,
    HardwareEvidenceStorePort,
    HardwareUartTransportPort,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _tool_text(stdout: bytes, stderr: bytes) -> str:
    return (stdout[:4096] + b"\n" + stderr[:4096]).decode("utf-8", errors="ignore").casefold()


def _device_id(text: str) -> str | None:
    match = re.search(r"(?:device\s*id|chip\s*id)\s*[:= ]\s*(0x[0-9a-f]+|[0-9a-f-]{4,})", text, re.IGNORECASE)
    return None if match is None else "device-" + match.group(1).replace("0x", "")


def _hex_after(text: str, *labels: str) -> int | None:
    for label in labels:
        match = re.search(re.escape(label) + r"\s*[:= ]\s*(0x[0-9a-f]+)", text, re.IGNORECASE)
        if match:
            return int(match.group(1), 16)
    return None


class UnavailableHardwareBringupBackend:
    def execute(self, operation: HardwareBringupOperation, context: ExecutionContext) -> HardwareToolResult:
        return HardwareToolResult(
            result=BringupResult.BLOCKED,
            reason_code="BLOCKED_NO_HARDWARE",
            detail="no real hardware backend is configured",
            observed_from_hardware=False,
            observed_at=_now(),
        )


class FixedToolHardwareBackend:
    """Builds fixed argv and delegates process execution to the existing runner port."""

    def __init__(self, runner: HardwareCommandRunnerPort, *, clock: Callable[[], datetime] | None = None) -> None:
        self._runner = runner
        self._clock = clock or _now

    def execute(self, operation: HardwareBringupOperation, context: ExecutionContext) -> HardwareToolResult:
        executable = None if context.tool is None else context.tool.executable
        if executable is None:
            return self._unavailable("HARDWARE_TOOL_UNAVAILABLE")
        if operation.action == "UART":
            return self._unavailable("UART_BACKEND_REQUIRED")
        try:
            artifact_path = self._materialize_artifact(operation, context) if operation.action == "FLASH" else None
            argv = self.build_argv(operation, context, Path(executable), artifact_path)
            code, stdout, stderr = self._runner.run(
                argv,
                cwd=context.state_root,
                timeout_seconds=context.tool.definition.timeout_seconds,
                cancellation=context.cancellation,
            )
        except InterruptedError:
            return self._unavailable("HARDWARE_OPERATION_CANCELLED")
        except TimeoutError:
            return self._unavailable("HARDWARE_OPERATION_TIMEOUT")
        except (OSError, PermissionError, ValueError):
            return self._unavailable("FIRMWARE_HASH_MISMATCH" if operation.action == "FLASH" else "HARDWARE_TOOL_UNAVAILABLE")
        text = _tool_text(stdout, stderr)
        detected = any(marker in text for marker in ("stm32f103", "stm32f1", "target detected", "device id", "chip id"))
        device = _device_id(text)
        if operation.action == "CONNECT":
            result = BringupResult.PASS if code == 0 and detected and device else BringupResult.UNKNOWN if code == 0 else BringupResult.FAIL
            return HardwareToolResult(
                result=result,
                reason_code="TARGET_DETECTED" if result is BringupResult.PASS else "TARGET_NOT_CONFIRMED" if result is BringupResult.UNKNOWN else "PROBE_CONNECTION_FAILED",
                detail="hardware probe observation completed",
                observed_from_hardware=True,
                device_id=device,
                target_detected=detected,
                mcu_state="CONNECTED" if result is BringupResult.PASS else None,
                observed_at=self._clock(),
            )
        if operation.action == "FLASH":
            verified = any(marker in text for marker in ("verified", "verification successful", "memory verify"))
            flash_ok = code == 0 and any(marker in text for marker in ("programmed", "download verified", "verified", "success"))
            result = BringupResult.PASS if detected and flash_ok and verified and operation.artifact is not None else BringupResult.FAIL if code != 0 else BringupResult.UNKNOWN
            return HardwareToolResult(
                result=result,
                reason_code="FLASH_COMPLETE" if result is BringupResult.PASS else "FLASH_FAILED" if result is BringupResult.FAIL else "FLASH_NOT_VERIFIED",
                detail="hardware flash observation completed",
                observed_from_hardware=True,
                device_id=device,
                target_detected=detected,
                flash_success=flash_ok,
                memory_verify=verified,
                artifact_hash_match=verified and operation.artifact is not None,
                observed_at=self._clock(),
            )
        vector = _hex_after(text, "vector table", "vector_table")
        reset = _hex_after(text, "reset handler", "reset_handler")
        stack = _hex_after(text, "stack pointer", "stack_pointer")
        boot_observed = vector is not None and reset is not None and stack is not None
        result = BringupResult.PASS if code == 0 and detected and boot_observed else BringupResult.UNKNOWN if code == 0 else BringupResult.FAIL
        return HardwareToolResult(
            result=result,
            reason_code="BOOT_VERIFIED" if result is BringupResult.PASS else "BOOT_NOT_VERIFIED" if result is BringupResult.UNKNOWN else "BOOT_FAILED",
            detail="hardware boot observation completed",
            observed_from_hardware=True,
            device_id=device,
            target_detected=detected,
            vector_table=vector,
            reset_handler=reset,
            stack_pointer=stack,
            mcu_state="RUNNING" if result is BringupResult.PASS else None,
            observed_at=self._clock(),
        )

    def build_argv(self, operation: HardwareBringupOperation, context: ExecutionContext, executable: Path, artifact_path: Path | None = None) -> tuple[str, ...]:
        raise NotImplementedError

    @staticmethod
    def _materialize_artifact(operation: HardwareBringupOperation, context: ExecutionContext) -> Path:
        if operation.artifact is None or context.artifact_store is None or not callable(getattr(context.artifact_store, "read", None)):
            raise ValueError("firmware artifact unavailable")
        content = context.artifact_store.read(operation.artifact)
        if not isinstance(content, bytes) or len(content) != operation.artifact.size_bytes or "sha256:" + hashlib.sha256(content).hexdigest() != operation.artifact.artifact_sha256:
            raise ValueError("firmware artifact hash mismatch")
        directory = (context.state_root / "hardware-artifacts" / operation.operation_id).resolve()
        if not directory.is_relative_to(context.state_root.resolve()):
            raise ValueError("hardware artifact path escaped state root")
        directory.mkdir(parents=True, exist_ok=True)
        suffix = ".elf" if operation.artifact.artifact_type == "ELF" else ".bin"
        path = directory / (operation.artifact.artifact_id + suffix)
        if path.exists() and path.read_bytes() != content:
            raise ValueError("hardware artifact materialization drift")
        if not path.exists():
            path.write_bytes(content)
        return path

    def _unavailable(self, reason: str) -> HardwareToolResult:
        return HardwareToolResult(
            result=BringupResult.UNAVAILABLE,
            reason_code=reason,
            detail="hardware tool execution unavailable",
            observed_from_hardware=False,
            observed_at=self._clock(),
        )


class OpenOCDHardwareBackend(FixedToolHardwareBackend):
    def build_argv(self, operation: HardwareBringupOperation, context: ExecutionContext, executable: Path, artifact_path: Path | None = None) -> tuple[str, ...]:
        common = (str(executable), "-f", "interface/stlink.cfg", "-f", "target/stm32f1x.cfg", "-c", f"hla_serial {operation.probe_serial}")
        if operation.action == "CONNECT":
            return common + ("-c", "init", "-c", "reset halt", "-c", "shutdown")
        if operation.action == "FLASH":
            if operation.artifact is None:
                raise ValueError("flash artifact is required")
            if artifact_path is None:
                raise ValueError("flash artifact path is required")
            return common + ("-c", f"program {artifact_path} verify reset exit")
        return common + ("-c", "init", "-c", "reset run", "-c", "shutdown")


class PyOCDHardwareBackend(FixedToolHardwareBackend):
    def build_argv(self, operation: HardwareBringupOperation, context: ExecutionContext, executable: Path, artifact_path: Path | None = None) -> tuple[str, ...]:
        if operation.action == "FLASH":
            if operation.artifact is None:
                raise ValueError("flash artifact is required")
            if artifact_path is None:
                raise ValueError("flash artifact path is required")
            return (str(executable), "flash", "-t", "stm32f103c8", "-u", operation.probe_serial, str(artifact_path))
        return (str(executable), "commander", "-t", "stm32f103c8", "-u", operation.probe_serial, "show")


class STLinkHardwareBackend(FixedToolHardwareBackend):
    def build_argv(self, operation: HardwareBringupOperation, context: ExecutionContext, executable: Path, artifact_path: Path | None = None) -> tuple[str, ...]:
        common = (str(executable), "-c", f"port=SWD sn={operation.probe_serial}")
        if operation.action == "FLASH":
            if operation.artifact is None:
                raise ValueError("flash artifact is required")
            if artifact_path is None:
                raise ValueError("flash artifact path is required")
            return common + ("-d", str(artifact_path), "-v", "-rst")
        return common + ("-r32", "0xE0042000", "1")


class UARTHardwareBackend:
    def __init__(self, transport: HardwareUartTransportPort, *, clock: Callable[[], datetime] | None = None) -> None:
        self._transport = transport
        self._clock = clock or _now

    def execute(self, operation: HardwareBringupOperation, context: ExecutionContext) -> HardwareToolResult:
        if operation.uart_port_binding is None or operation.baud_rate is None:
            return HardwareToolResult(result=BringupResult.BLOCKED, reason_code="UART_CONFIGURATION_REQUIRED", detail="UART configuration is incomplete", observed_from_hardware=False, observed_at=self._clock())
        try:
            raw = self._transport.read(operation.uart_port_binding, operation.baud_rate, duration_seconds=30, max_bytes=64 * 1024, cancellation=context.cancellation)
        except InterruptedError:
            return HardwareToolResult(result=BringupResult.UNAVAILABLE, reason_code="UART_CANCELLED", detail="UART observation cancelled", observed_from_hardware=False, observed_at=self._clock())
        except TimeoutError:
            return HardwareToolResult(result=BringupResult.UNKNOWN, reason_code="UART_TIMEOUT", detail="UART observation timed out", observed_from_hardware=True, observed_at=self._clock())
        except OSError:
            return HardwareToolResult(result=BringupResult.UNAVAILABLE, reason_code="UART_UNAVAILABLE", detail="UART transport unavailable", observed_from_hardware=False, observed_at=self._clock())
        if not isinstance(raw, bytes):
            return HardwareToolResult(result=BringupResult.FAIL, reason_code="UART_INVALID_PAYLOAD", detail="UART transport returned invalid payload", observed_from_hardware=True, observed_at=self._clock())
        lines = tuple(line[:256] for line in raw[:64 * 1024].decode("utf-8", errors="replace").splitlines() if line.strip())[:128]
        matched = tuple(pattern for pattern in operation.expected_patterns if any(pattern in line for line in lines))
        result = BringupResult.PASS if operation.expected_patterns and len(matched) == len(operation.expected_patterns) else BringupResult.UNKNOWN if not lines else BringupResult.FAIL
        return HardwareToolResult(result=result, reason_code="UART_PASS" if result is BringupResult.PASS else "UART_FAIL" if result is BringupResult.FAIL else "UART_UNKNOWN", detail="UART observation completed", observed_from_hardware=True, observed_lines=lines, observed_at=self._clock())


class HardwareBringupAdapter:
    """ExecutionWorker adapter that persists only lineage-bound hardware evidence."""

    def __init__(self, *, evidence_store: HardwareEvidenceStorePort, backends: dict[str, HardwareBringupBackendPort] | None = None, clock: Callable[[], datetime] | None = None) -> None:
        self._store = evidence_store
        self._backends = dict(backends or {})
        self._clock = clock or _now

    def execute(self, operation: HardwareBringupOperation, context: ExecutionContext) -> AdapterResult:
        backend = self._backends.get(operation.tool_id)
        if backend is None:
            raw = UnavailableHardwareBringupBackend().execute(operation, context)
        else:
            raw = backend.execute(operation, context)
        lineage = HardwareLineage(
            project_id=operation.project_id,
            pcb_sha256=operation.pcb_sha256,
            schematic_sha256=operation.schematic_sha256,
            firmware_sha256=operation.firmware_sha256,
            elf_sha256=operation.elf_sha256,
            build_id=operation.build_id,
            design_truth_id=operation.design_truth_id,
            hardware_session_id=operation.hardware_session_id,
        )
        references: list[str] = []
        if operation.action == "CONNECT":
            connection = HardwareConnection(
                connection_id=operation.connection_id,
                project_id=operation.project_id,
                probe_type=operation.probe_type,
                probe_serial=operation.probe_serial,
                target_mcu=operation.target_mcu,
                connection_time=raw.observed_at,
                firmware_hash=operation.firmware_sha256,
                pcb_hash=operation.pcb_sha256,
                schematic_hash=operation.schematic_sha256,
                elf_hash=operation.elf_sha256,
                build_id=operation.build_id,
                design_truth_id=operation.design_truth_id,
                hardware_session_id=operation.hardware_session_id,
                status=HardwareConnectionStatus.CONNECTED if raw.result is BringupResult.PASS else HardwareConnectionStatus.BLOCKED_NO_HARDWARE if raw.result in {BringupResult.BLOCKED, BringupResult.UNAVAILABLE} else HardwareConnectionStatus.FAILED,
                device_id=raw.device_id,
                reason_code=raw.reason_code,
            )
            self._store.save(connection)
            references.append(connection.connection_id)
            session = HardwareSession(
                session_id=operation.hardware_session_id,
                project_id=operation.project_id,
                connection_id=operation.connection_id,
                probe_serial=operation.probe_serial,
                target_mcu=operation.target_mcu,
                started_at=raw.observed_at,
                status=HardwareSessionStatus.OPEN if raw.result is BringupResult.PASS else HardwareSessionStatus.FAILED,
            )
            self._store.save(session)
            references.append(session.session_id)
        elif operation.action == "FLASH" and raw.device_id is not None:
            evidence = FlashEvidence(
                evidence_id=f"flash-evidence-{operation.operation_id}",
                operation_id=operation.operation_id,
                hardware_session_id=operation.hardware_session_id,
                firmware_sha256=operation.firmware_sha256,
                device_id=raw.device_id,
                probe_serial=operation.probe_serial,
                timestamp=raw.observed_at,
                target_detected=raw.target_detected,
                flash_success=raw.flash_success,
                memory_verify=raw.memory_verify,
                artifact_hash_match=raw.artifact_hash_match,
                result=raw.result,
                lineage=lineage,
            )
            self._store.save(evidence)
            references.append(evidence.evidence_id)
        elif operation.action == "BOOT":
            evidence = BootEvidence(
                evidence_id=f"boot-evidence-{operation.operation_id}",
                operation_id=operation.operation_id,
                hardware_session_id=operation.hardware_session_id,
                timestamp=raw.observed_at,
                vector_table=raw.vector_table,
                reset_handler=raw.reset_handler,
                stack_pointer=raw.stack_pointer,
                mcu_state=raw.mcu_state,
                result=raw.result,
                lineage=lineage,
            )
            self._store.save(evidence)
            references.append(evidence.evidence_id)
        elif operation.action == "UART":
            evidence = UartEvidence(
                evidence_id=f"uart-evidence-{operation.operation_id}",
                operation_id=operation.operation_id,
                hardware_session_id=operation.hardware_session_id,
                firmware_sha256=operation.firmware_sha256,
                timestamp=raw.observed_at,
                baud_rate=operation.baud_rate or 115200,
                expected=operation.expected_patterns,
                observed=raw.observed_lines,
                matched=tuple(pattern for pattern in operation.expected_patterns if any(pattern in line for line in raw.observed_lines)),
                result=raw.result,
                lineage=lineage,
            )
            self._store.save(evidence)
            references.append(evidence.evidence_id)
        status = "SUCCEEDED" if raw.result is BringupResult.PASS else "UNAVAILABLE" if raw.result in {BringupResult.BLOCKED, BringupResult.UNAVAILABLE} else "FAILED"
        return AdapterResult(status, raw.reason_code, evidence_references=tuple(references))


__all__ = (
    "FixedToolHardwareBackend",
    "HardwareBringupAdapter",
    "OpenOCDHardwareBackend",
    "PyOCDHardwareBackend",
    "STLinkHardwareBackend",
    "UARTHardwareBackend",
    "UnavailableHardwareBringupBackend",
)
