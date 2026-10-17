from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest

from embedded_copilot.execution_worker.adapters import FirmwareBuildToolAdapter
from embedded_copilot.execution_worker.contracts import (
    BuildOperation,
    ExecutionTarget,
    OperationType,
    TargetBindingStatus,
    ToolchainIdentity,
)
from embedded_copilot.execution_worker.ports import ExecutionContext
from embedded_copilot.execution_worker.registry import ToolDefinition, ToolFamily, ToolResolution
from embedded_copilot.firmware_engineering import (
    BuildReadinessStatus,
    FirmwareBuildConfiguration,
    FirmwareBuildRequest,
    FirmwareBuildResultStatus,
    FirmwareSourceArtifact,
    evaluate_build_readiness,
)


FP = "sha256:" + "1" * 64


def _request(relative_path: str, digest: str, size: int) -> FirmwareBuildRequest:
    configuration = FirmwareBuildConfiguration(
        target_kind="STM32F446RE",
        build_system="ARM_NONE_EABI_GCC",
        toolchain_id="ARM_NONE_EABI_GCC",
        toolchain_version="13",
        profile="DEBUG",
        expected_artifact_types=("ELF",),
    )
    return FirmwareBuildRequest.create(
        project_id="project-1",
        firmware_reference="firmware-1",
        build_profile="DEBUG",
        approval_reference=None,
        firmware_spec_id="firmware-spec-1",
        firmware_spec_fingerprint=FP,
        implementation_id="implementation-1",
        implementation_fingerprint=FP,
        target_kind="STM32F446RE",
        build_system="ARM_NONE_EABI_GCC",
        toolchain_id="ARM_NONE_EABI_GCC",
        toolchain_version="13",
        configuration=configuration,
        source_artifacts=(FirmwareSourceArtifact(relative_path=relative_path, content_fingerprint=digest, size_bytes=size),),
    )


def test_readiness_blocks_missing_and_hash_drift(tmp_path) -> None:
    source = tmp_path / "main.c"
    source.write_text("int main(void) { return 0; }", encoding="utf-8")
    actual = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
    missing = _request("missing.c", actual, source.stat().st_size)
    result = evaluate_build_readiness(missing, workspace_root=tmp_path)
    assert result.status is BuildReadinessStatus.BLOCKED_SOURCE_ARTIFACT_MISSING
    drift = _request("main.c", "sha256:" + "2" * 64, source.stat().st_size)
    result = evaluate_build_readiness(drift, workspace_root=tmp_path)
    assert result.status is BuildReadinessStatus.BLOCKED_SOURCE_HASH_MISMATCH


def test_stm32_cmake_readiness_reports_missing_target_runtime(tmp_path) -> None:
    source = tmp_path / "main.c"
    source.write_text("int main(void) { return 0; }", encoding="utf-8")
    digest = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
    configuration = FirmwareBuildConfiguration(
        target_kind="STM32F103C8T6",
        build_system="CMAKE",
        toolchain_id="ARM_NONE_EABI_GCC",
        toolchain_version="15.3.1",
        profile="Release",
        build_directory="build",
        expected_artifact_types=("ELF", "BIN", "MAP"),
    )
    request = FirmwareBuildRequest.create(
        project_id="project-stm32-runtime",
        firmware_reference="firmware-stm32-runtime",
        build_profile="Release",
        approval_reference=None,
        firmware_spec_id="firmware-spec-stm32-runtime",
        firmware_spec_fingerprint=FP,
        implementation_id="implementation-stm32-runtime",
        implementation_fingerprint=FP,
        target_kind="STM32F103C8T6",
        build_system="CMAKE",
        toolchain_id="ARM_NONE_EABI_GCC",
        toolchain_version="15.3.1",
        configuration=configuration,
        source_artifacts=(FirmwareSourceArtifact(relative_path="main.c", content_fingerprint=digest, size_bytes=source.stat().st_size),),
    )
    result = evaluate_build_readiness(request, workspace_root=tmp_path)
    assert result.status is BuildReadinessStatus.BLOCKED_TARGET_RUNTIME_MISSING
    assert "CMakeLists.txt" in result.missing_runtime


def test_request_rejects_absolute_source_and_configuration_mismatch() -> None:
    with pytest.raises(ValueError):
        FirmwareSourceArtifact(relative_path="C:/private/main.c", content_fingerprint=FP, size_bytes=1)
    with pytest.raises(ValueError):
        FirmwareBuildConfiguration(
            target_kind="STM32F446RE",
            build_system="ARM_NONE_EABI_GCC",
            toolchain_id="ARM_NONE_EABI_GCC",
            toolchain_version="13",
            profile="DEBUG",
            compiler_flags=("--sysroot",),
        )


def test_source_artifact_requires_available_status() -> None:
    with pytest.raises(ValueError):
        FirmwareSourceArtifact(
            relative_path="main.c",
            content_fingerprint=FP,
            size_bytes=1,
            availability="MISSING",
        )


class _Runner:
    def __init__(self, output) -> None:
        self.output = output
        self.argv = None

    def run(self, argv, *, cwd, timeout_seconds, environment, cancellation):
        self.argv = argv
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_bytes(b"real-elf")
        return 0, b"compiler completed", b""


def test_typed_adapter_uses_existing_process_boundary_and_registers_outputs(tmp_path) -> None:
    source = tmp_path / "main.c"
    source.write_text("int main(void) { return 0; }", encoding="utf-8")
    digest = "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
    request = _request("main.c", digest, source.stat().st_size)
    now = datetime.now(UTC)
    operation = BuildOperation(
        operation_id="firmware-build-1",
        tenant_id="tenant-1",
        project_id="project-1",
        requester_id="requester-1",
        target_id="target-1",
        target_fingerprint=FP,
        workspace_snapshot_id="snapshot-1",
        workspace_fingerprint=FP,
        proposal_id="proposal-1",
        proposal_fingerprint=FP,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        nonce="nonce-1",
        toolchain=ToolchainIdentity(
            toolchain_id="ARM_NONE_EABI_GCC",
            tool_version="13",
            tool_family="ARM_NONE_EABI_GCC",
            supported_operation=OperationType.BUILD,
            supported_target="STM32F446RE",
        ),
        configuration="DEBUG",
        expected_artifact_type="ELF",
        firmware_build_request_id=request.firmware_reference,
        firmware_build_request_fingerprint=request.fingerprint,
        firmware_spec_id=request.firmware_spec_id,
        firmware_spec_fingerprint=request.firmware_spec_fingerprint,
        implementation_id=request.implementation_id,
        implementation_fingerprint=request.implementation_fingerprint,
        source_artifacts=request.source_artifacts,
        build_system="ARM_NONE_EABI_GCC",
        firmware_build_request=request,
    )
    definition = ToolDefinition(
        "ARM_NONE_EABI_GCC", "13", ToolFamily.ARM_NONE_EABI_GCC, OperationType.BUILD,
        ("STM32F446RE",), ("arm-none-eabi-gcc.exe",), 30, ("ELF",),
    )
    state_root = tmp_path.with_name(tmp_path.name + "-state")
    runner = _Runner(state_root / "output" / "firmware.elf")
    result = FirmwareBuildToolAdapter(runner=runner).execute(
        operation,
        ExecutionContext(
            workspace_root=tmp_path,
            state_root=state_root,
            target=ExecutionTarget(
                target_id="target-1", tenant_id="tenant-1", project_id="project-1",
                target_kind="STM32F446RE", board_family="NUCLEO-F446RE", mcu_family="STM32F446RE",
                stable_identity_hash="stable-1", candidate_id="candidate-1",
                binding_status=TargetBindingStatus.BOUND, bound_by="reviewer-1", bound_at=now,
            ),
            tool=ToolResolution(definition, tmp_path / "arm-none-eabi-gcc.exe", True, "AVAILABLE", "TOOL_RESOLVED"),
            cancellation=object(),
        ),
    )
    assert result.status == "SUCCEEDED"
    assert result.build_result is not None
    assert result.build_result.result_status is FirmwareBuildResultStatus.BUILD_PASS
    assert result.build_evidence is not None
    assert result.artifact is not None
    assert result.additional_artifacts
    assert runner.argv[0].endswith("arm-none-eabi-gcc.exe")
    assert "shell" not in " ".join(runner.argv).casefold()
