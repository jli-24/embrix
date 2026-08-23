from dataclasses import asdict
from pathlib import Path

import pytest

from embedded_copilot.execution_worker.artifacts import ArtifactStore
from embedded_copilot.execution_worker.contracts import ArtifactIdentity, OperationType
from embedded_copilot.execution_worker.registry import (
    ExecutionCapabilityDescriptor,
    ExecutionToolRegistry,
    ToolResolution,
    TrustedToolDiscovery,
)


def test_artifact_store_is_immutable_and_hash_verified(tmp_path) -> None:
    content = b"firmware"
    identity = ArtifactIdentity(
        artifact_id="artifact-1", tenant_id="tenant-1", project_id="project-1", target_id="target-1",
        workspace_fingerprint="sha256:" + "1" * 64, build_operation_id="build-1",
        toolchain_id="CMAKE_NINJA_BUILD", toolchain_version="3", build_configuration="DEBUG",
        artifact_type="BIN", artifact_sha256="sha256:" + __import__("hashlib").sha256(content).hexdigest(),
        size_bytes=len(content), created_at="2026-01-01T00:00:00+00:00",
    )
    store = ArtifactStore(tmp_path / "artifacts")
    store.put(identity, content)
    assert store.read(identity) == content
    with pytest.raises(ValueError):
        store.put(identity, b"drift")


def test_tool_registry_rejects_untrusted_path_resolution() -> None:
    registry = ExecutionToolRegistry(TrustedToolDiscovery())
    result = registry.resolve("CMAKE_NINJA_BUILD", target_kind="STM32F446RE", operation=OperationType.BUILD)
    assert result.status == "UNAVAILABLE"
    assert result.reason_code == "TRUSTED_TOOL_ROOT_UNCONFIGURED"


def test_tool_registry_rejects_interpreter_family() -> None:
    from embedded_copilot.execution_worker.registry import ToolDefinition, ToolFamily

    definition = ToolDefinition("BAD", "1", ToolFamily.CMAKE_NINJA, OperationType.BUILD, ("STM32F446RE",), ("powershell.exe",), 60)
    result = TrustedToolDiscovery(trusted_roots=(Path("C:/trusted"),)).resolve(definition)
    assert result.status == "UNAVAILABLE"
    assert result.reason_code == "FORBIDDEN_TOOL_FAMILY"


def test_tool_registry_discovers_test_as_unavailable_safe_descriptor() -> None:
    registry = ExecutionToolRegistry(TrustedToolDiscovery())

    descriptors = registry.discover(OperationType.TEST)

    assert len(descriptors) == 1
    descriptor = descriptors[0]
    assert isinstance(descriptor, ExecutionCapabilityDescriptor)
    assert descriptor.operation_type is OperationType.TEST
    assert descriptor.tool_id == "FIRMWARE_TEST"
    assert descriptor.adapter_status == "UNAVAILABLE"
    assert descriptor.reason_code == "TEST_ADAPTER_NOT_CONFIGURED"
    assert descriptor.adapter_status not in {"AVAILABLE", "VERIFIED", "PRODUCTION_READY"}

    payload = asdict(descriptor)
    assert set(payload) == {
        "operation_type",
        "tool_id",
        "tool_version",
        "target_kind",
        "artifact_types",
        "adapter_status",
        "reason_code",
    }
    serialized = str(payload).casefold()
    for forbidden in ("executable", "command", "cwd", "path", "environment", "shell"):
        assert forbidden not in serialized


def test_test_tool_resolution_cannot_be_promoted_by_discovery() -> None:
    class IncorrectAvailableDiscovery:
        def resolve(self, definition) -> ToolResolution:
            return ToolResolution(
                definition=definition,
                executable=Path("C:/private/firmware-test"),
                version_verified=True,
                status="AVAILABLE",
                reason_code="TOOL_RESOLVED",
            )

    registry = ExecutionToolRegistry(IncorrectAvailableDiscovery())

    result = registry.resolve(
        "FIRMWARE_TEST",
        target_kind="STM32F446RE",
        operation=OperationType.TEST,
    )

    assert result.executable is None
    assert result.version_verified is False
    assert result.status == "UNAVAILABLE"
    assert result.reason_code == "TEST_ADAPTER_NOT_CONFIGURED"


def test_existing_registry_resolution_behavior_does_not_regress() -> None:
    registry = ExecutionToolRegistry(TrustedToolDiscovery())

    build = registry.resolve(
        "CMAKE_NINJA_BUILD",
        target_kind="STM32F446RE",
        operation=OperationType.BUILD,
    )
    flash = registry.resolve(
        "OPENOCD_FLASH",
        target_kind="STM32F446RE",
        operation=OperationType.FLASH,
    )
    uart = registry.resolve(
        "SERIAL_UART",
        target_kind="STM32F446RE",
        operation=OperationType.UART_OBSERVATION,
    )

    assert (build.status, build.reason_code) == (
        "UNAVAILABLE",
        "TRUSTED_TOOL_ROOT_UNCONFIGURED",
    )
    assert (flash.status, flash.reason_code) == (
        "UNAVAILABLE",
        "TRUSTED_TOOL_ROOT_UNCONFIGURED",
    )
    assert (uart.status, uart.reason_code) == ("AVAILABLE", "NON_PROCESS_ADAPTER")
    with pytest.raises(ValueError, match="not allow-listed"):
        registry.resolve(
            "HIL",
            target_kind="STM32F446RE",
            operation=OperationType.HIL,
        )
