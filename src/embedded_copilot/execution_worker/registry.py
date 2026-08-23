from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from .contracts import OperationType, ToolchainIdentity, identifier


class ToolFamily(StrEnum):
    ESP_IDF = "ESP_IDF"
    ESPTOOL = "ESPTOOL"
    CMAKE_NINJA = "CMAKE_NINJA"
    ARM_NONE_EABI_GCC = "ARM_NONE_EABI_GCC"
    OPENOCD = "OPENOCD"
    STLINK = "STLINK"
    SERIAL_UART = "SERIAL_UART"
    FIRMWARE_TEST = "FIRMWARE_TEST"


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    tool_id: str
    tool_version: str
    family: ToolFamily
    supported_operation: OperationType
    supported_targets: tuple[str, ...]
    allowed_executable_names: tuple[str, ...]
    timeout_seconds: int
    artifact_types: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolResolution:
    definition: ToolDefinition
    executable: Path | None
    version_verified: bool
    status: str
    reason_code: str


@dataclass(frozen=True, slots=True)
class ExecutionCapabilityDescriptor:
    """A path-free projection of one allow-listed execution capability."""

    operation_type: OperationType
    tool_id: str
    tool_version: str
    target_kind: tuple[str, ...]
    artifact_types: tuple[str, ...]
    adapter_status: str
    reason_code: str


class ToolDiscoveryPort(Protocol):
    def resolve(self, definition: ToolDefinition) -> ToolResolution: ...


_FORBIDDEN_LAUNCHERS = {
    "cmd",
    "cmd.exe",
    "powershell",
    "powershell.exe",
    "pwsh",
    "pwsh.exe",
    "bash",
    "bash.exe",
    "sh",
    "sh.exe",
    "python",
    "python.exe",
    "python3",
    "python3.exe",
}


class TrustedToolDiscovery:
    """Resolves only operator-configured tool families, never user paths."""

    def __init__(self, *, trusted_roots: tuple[Path, ...] = (), trusted_versions: dict[str, str] | None = None) -> None:
        self._trusted_roots = tuple(path.resolve(strict=False) for path in trusted_roots)
        self._trusted_versions = dict(trusted_versions or {})

    def resolve(self, definition: ToolDefinition) -> ToolResolution:
        if any(name.casefold() in _FORBIDDEN_LAUNCHERS for name in definition.allowed_executable_names):
            return ToolResolution(definition, None, False, "UNAVAILABLE", "FORBIDDEN_TOOL_FAMILY")
        if definition.allowed_executable_names and not self._trusted_roots:
            return ToolResolution(definition, None, False, "UNAVAILABLE", "TRUSTED_TOOL_ROOT_UNCONFIGURED")
        for executable_name in definition.allowed_executable_names:
            candidate = self._which(executable_name)
            if candidate is None:
                continue
            if self._trusted_roots and not any(candidate.is_relative_to(root) for root in self._trusted_roots):
                continue
            if candidate.name.casefold() not in {name.casefold() for name in definition.allowed_executable_names}:
                continue
            configured_version = self._trusted_versions.get(definition.tool_id)
            if configured_version != definition.tool_version:
                return ToolResolution(definition, None, False, "UNAVAILABLE", "TOOL_VERSION_UNVERIFIED")
            return ToolResolution(definition, candidate, True, "AVAILABLE", "TOOL_RESOLVED")
        if not definition.allowed_executable_names:
            return ToolResolution(definition, None, True, "AVAILABLE", "NON_PROCESS_ADAPTER")
        return ToolResolution(definition, None, False, "UNAVAILABLE", "TOOL_NOT_FOUND")

    @staticmethod
    def _which(name: str) -> Path | None:
        path_value = os.environ.get("PATH", "")
        for directory in path_value.split(os.pathsep):
            if not directory:
                continue
            candidate = Path(directory) / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate.resolve(strict=True)
        return None


class ExecutionToolRegistry:
    """Fixed production allow-list. Arbitrary toolchains are not registrable."""

    _definitions = (
        ToolDefinition("ESP_IDF_BUILD", "v5.3", ToolFamily.ESP_IDF, OperationType.BUILD, ("ESP32-S3",), ("idf.py", "idf.exe"), 600, ("BIN",)),
        ToolDefinition("ESPTOOL_FLASH", "v4", ToolFamily.ESPTOOL, OperationType.FLASH, ("ESP32-S3",), ("esptool", "esptool.py", "esptool.exe"), 300),
        ToolDefinition("SERIAL_UART", "1", ToolFamily.SERIAL_UART, OperationType.UART_OBSERVATION, ("ESP32-S3", "STM32F446RE"), (), 300),
        ToolDefinition("CMAKE_NINJA_BUILD", "3", ToolFamily.CMAKE_NINJA, OperationType.BUILD, ("STM32F446RE",), ("cmake", "cmake.exe"), 600, ("ELF", "BIN", "HEX", "MAP")),
        ToolDefinition("ARM_NONE_EABI_GCC", "13", ToolFamily.ARM_NONE_EABI_GCC, OperationType.BUILD, ("STM32F446RE",), ("arm-none-eabi-gcc", "arm-none-eabi-gcc.exe"), 600, ("ELF", "BIN", "HEX", "MAP")),
        ToolDefinition("OPENOCD_FLASH", "0.12", ToolFamily.OPENOCD, OperationType.FLASH, ("STM32F446RE",), ("openocd", "openocd.exe"), 300),
        ToolDefinition("STLINK_FLASH", "1", ToolFamily.STLINK, OperationType.FLASH, ("STM32F446RE",), ("STM32_Programmer_CLI", "STM32_Programmer_CLI.exe"), 300),
        ToolDefinition("FIRMWARE_TEST", "1", ToolFamily.FIRMWARE_TEST, OperationType.TEST, ("ESP32-S3", "STM32F446RE"), (), 300, ("ELF", "BIN", "HEX", "MAP")),
    )

    def __init__(self, discovery: ToolDiscoveryPort) -> None:
        self._discovery = discovery
        self._by_id = {item.tool_id: item for item in self._definitions}

    def definition(self, tool_id: str) -> ToolDefinition:
        key = identifier(tool_id, field="tool_id")
        try:
            return self._by_id[key]
        except KeyError:
            raise ValueError("tool family is not allow-listed") from None

    def resolve(self, tool_id: str, *, target_kind: str, operation: OperationType) -> ToolResolution:
        definition = self.definition(tool_id)
        if definition.supported_operation is not operation or target_kind not in definition.supported_targets:
            return ToolResolution(definition, None, False, "UNAVAILABLE", "TOOL_OPERATION_TARGET_UNSUPPORTED")
        if definition.tool_id == "FIRMWARE_TEST":
            return ToolResolution(
                definition,
                None,
                False,
                "UNAVAILABLE",
                "TEST_ADAPTER_NOT_CONFIGURED",
            )
        return self._discovery.resolve(definition)

    def discover(self, operation: OperationType) -> tuple[ExecutionCapabilityDescriptor, ...]:
        """Return safe registry projections without resolving process tools."""

        if operation is not OperationType.TEST:
            return ()
        return tuple(
            ExecutionCapabilityDescriptor(
                operation_type=item.supported_operation,
                tool_id=item.tool_id,
                tool_version=item.tool_version,
                target_kind=item.supported_targets,
                artifact_types=item.artifact_types,
                adapter_status="UNAVAILABLE",
                reason_code="TEST_ADAPTER_NOT_CONFIGURED",
            )
            for item in self._definitions
            if item.supported_operation is OperationType.TEST
        )

    def identities(self) -> tuple[ToolchainIdentity, ...]:
        return tuple(
            ToolchainIdentity(
                toolchain_id=item.tool_id,
                tool_version=item.tool_version,
                tool_family=item.family.value,
                supported_operation=item.supported_operation,
                supported_target=item.supported_targets[0],
            )
            for item in self._definitions
            if item.supported_operation is OperationType.BUILD
        )


__all__ = (
    "ExecutionCapabilityDescriptor",
    "ExecutionToolRegistry",
    "ToolDefinition",
    "ToolDiscoveryPort",
    "ToolFamily",
    "ToolResolution",
    "TrustedToolDiscovery",
)
