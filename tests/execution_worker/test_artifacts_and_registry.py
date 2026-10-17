from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import pytest

from embedded_copilot.execution_worker.adapters import _verify_arm_elf
from embedded_copilot.execution_worker.artifacts import ArtifactStore
from embedded_copilot.execution_worker.contracts import (
    ArtifactIdentity, BuildEvidence, OperationReceipt, OperationStatus, OperationType,
)
from embedded_copilot.execution_worker.service import (
    ExecutionOperationService, ExecutionRejected, ExecutionUnavailable,
)
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
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


def test_service_rechecks_artifact_bytes_for_artifact_and_receipt_after_reopen(tmp_path) -> None:
    content = b"firmware"
    identity = ArtifactIdentity(
        artifact_id="artifact-1", tenant_id="tenant-1", project_id="project-1", target_id="target-1",
        workspace_fingerprint="sha256:" + "1" * 64, build_operation_id="build-1",
        toolchain_id="CMAKE_NINJA_BUILD", toolchain_version="3", build_configuration="DEBUG",
        artifact_type="BIN", artifact_sha256="sha256:" + __import__("hashlib").sha256(content).hexdigest(),
        size_bytes=len(content), created_at="2026-01-01T00:00:00+00:00",
    )
    log_content = b"build log"
    log_identity = ArtifactIdentity(
        **{
            **identity.model_dump(mode="python"),
            "artifact_id": "artifact-log-1",
            "artifact_type": "LOG",
            "artifact_sha256": "sha256:" + __import__("hashlib").sha256(log_content).hexdigest(),
            "size_bytes": len(log_content),
            "fingerprint": "sha256:" + "0" * 64,
        }
    )
    database = tmp_path / "execution.sqlite3"
    blob_root = tmp_path / "artifacts"
    sqlite = SQLiteExecutionStore(database)
    sqlite.save_artifact(identity)
    sqlite.save_artifact(log_identity)
    sqlite.save_receipt(OperationReceipt(
        operation_id="build-1", tenant_id="tenant-1", project_id="project-1",
        operation_type=OperationType.BUILD, status=OperationStatus.SUCCEEDED,
        tool_execution_status="SUCCEEDED", flash_verification_status="NOT_APPLICABLE",
        target_runtime_verification_status="NOT_VERIFIED", artifact=identity,
        build_evidence=BuildEvidence(
            operation_id="build-1", request_fingerprint="sha256:" + "2" * 64,
            result_status="BUILD_PASS", toolchain_id="CMAKE_NINJA_BUILD",
            toolchain_version="3", output_artifact_ids=(identity.artifact_id, log_identity.artifact_id),
            log_artifact_id=log_identity.artifact_id,
            raw_evidence_artifact_id=log_identity.artifact_id, created_at=datetime.now(UTC),
        ),
        created_at=datetime.now(UTC),
    ))
    blobs = ArtifactStore(blob_root)
    blobs.put(identity, content)
    blobs.put(log_identity, log_content)
    service = ExecutionOperationService(store=sqlite, artifact_store=blobs)
    scope = {"tenant_id": "tenant-1", "project_id": "project-1"}
    assert service.get_artifact(**scope, artifact_id="artifact-1") == identity
    assert service.get_receipt(**scope, operation_id="build-1").artifact == identity

    reopened = ExecutionOperationService(
        store=SQLiteExecutionStore(database), artifact_store=ArtifactStore(blob_root),
    )
    (blob_root / log_identity.artifact_id).write_bytes(b"tampered log")
    with pytest.raises(ExecutionRejected):
        reopened.get_receipt(**scope, operation_id="build-1")
    (blob_root / log_identity.artifact_id).unlink()
    with pytest.raises(ExecutionUnavailable):
        reopened.get_receipt(**scope, operation_id="build-1")
    reopened.artifact_store.put(log_identity, log_content)

    (blob_root / identity.artifact_id).write_bytes(b"tampered")
    with pytest.raises(ExecutionRejected):
        reopened.get_artifact(**scope, artifact_id="artifact-1")
    with pytest.raises(ExecutionRejected):
        reopened.get_receipt(**scope, operation_id="build-1")

    (blob_root / identity.artifact_id).unlink()
    with pytest.raises(ExecutionUnavailable):
        reopened.get_artifact(**scope, artifact_id="artifact-1")
    with pytest.raises(ExecutionUnavailable):
        reopened.get_receipt(**scope, operation_id="build-1")


def test_arm_elf_verification_rejects_non_arm_or_missing_entry() -> None:
    elf = bytearray(52)
    elf[:4] = b"\x7fELF"
    elf[4] = 1  # ELF32
    elf[5] = 1  # little-endian
    elf[18:20] = (40).to_bytes(2, "little")  # EM_ARM
    elf[24:28] = (0x08000201).to_bytes(4, "little")

    assert _verify_arm_elf(bytes(elf)) is True

    elf[18:20] = (62).to_bytes(2, "little")  # EM_X86_64
    assert _verify_arm_elf(bytes(elf)) is False

    elf[18:20] = (40).to_bytes(2, "little")
    elf[24:28] = b"\x00\x00\x00\x00"
    assert _verify_arm_elf(bytes(elf)) is False


def test_tool_registry_rejects_untrusted_path_resolution() -> None:
    registry = ExecutionToolRegistry(TrustedToolDiscovery())
    result = registry.resolve("CMAKE_NINJA_BUILD", target_kind="STM32F446RE", operation=OperationType.BUILD)
    assert result.status == "UNAVAILABLE"
    assert result.reason_code == "TRUSTED_TOOL_ROOT_UNCONFIGURED"


def test_arm_registry_rejects_host_compiler_even_when_under_trusted_root(tmp_path) -> None:
    host_compiler = tmp_path / "gcc.exe"
    host_compiler.write_bytes(b"host compiler placeholder")
    registry = ExecutionToolRegistry(
        TrustedToolDiscovery(
            trusted_roots=(tmp_path,),
            explicit_paths={"ARM_NONE_EABI_GCC": host_compiler},
            trusted_versions={"ARM_NONE_EABI_GCC": "15.3.1"},
        )
    )
    result = registry.resolve(
        "ARM_NONE_EABI_GCC",
        target_kind="STM32F103C8T6",
        operation=OperationType.BUILD,
    )
    assert result.status == "UNAVAILABLE"
    assert result.reason_code == "TOOL_NOT_FOUND"


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


def test_registry_rejects_forged_available_process_resolution() -> None:
    class ForgedDiscovery:
        def resolve(self, definition) -> ToolResolution:
            return ToolResolution(
                definition=definition,
                executable=Path("C:/private/cmake.exe"),
                version_verified=False,
                status="AVAILABLE",
                reason_code="TOOL_RESOLVED",
            )

    result = ExecutionToolRegistry(ForgedDiscovery()).resolve(
        "CMAKE_NINJA_BUILD",
        target_kind="STM32F446RE",
        operation=OperationType.BUILD,
    )

    assert result.executable is None
    assert result.version_verified is False
    assert result.status == "UNAVAILABLE"
    assert result.reason_code == "TOOL_RESOLUTION_INVALID"


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
