from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from .contracts import (
    BuildReadinessResult,
    BuildReadinessStatus,
    FirmwareBuildConfiguration,
    FirmwareBuildRequest,
    FirmwareBuildResult,
    FirmwareBuildResultStatus,
    FirmwareBuildStatus,
    FirmwareSourceArtifact,
)
from .request_intent import is_firmware_build_request

if TYPE_CHECKING:
    from embedded_copilot.execution_worker.registry import ToolResolution


_SUPPORTED_BUILD_SYSTEMS = frozenset({"CMAKE", "CMAKE_NINJA", "ESP_IDF", "ARM_NONE_EABI_GCC"})


def _target_runtime_missing(root, configuration: FirmwareBuildConfiguration) -> tuple[str, ...]:
    if configuration.target_kind != "STM32F103C8T6" or configuration.build_system != "CMAKE":
        return ()
    references = (
        configuration.cmake_project,
        configuration.cmake_toolchain_file,
        configuration.linker_script or "linker/STM32F103C8Tx_FLASH.ld",
        configuration.startup_source,
        configuration.system_source,
        configuration.cmsis_include,
        configuration.device_include,
    )
    missing: list[str] = []
    for relative in references:
        candidate = (root / relative).resolve(strict=False)
        if not candidate.is_relative_to(root) or not candidate.exists():
            missing.append(relative)
    return tuple(dict.fromkeys(missing))




def create_firmware_build_request(
    firmware_spec: object,
    implementation_spec: object,
    *,
    configuration: FirmwareBuildConfiguration,
    source_artifacts: tuple[FirmwareSourceArtifact, ...],
    approval_reference: str | None = None,
    expected_source_revision: str | None = None,
    actual_source_revision: str | None = None,
    hardware_firmware_conflicts: tuple[str, ...] = (),
) -> FirmwareBuildRequest:
    """Project the existing design contracts into one immutable build request."""

    from embedded_copilot.engineering_copilot import (
        FirmwareSpecification,
        ImplementationSpecification,
    )

    if type(firmware_spec) is not FirmwareSpecification or type(implementation_spec) is not ImplementationSpecification:
        raise TypeError("FirmwareSpecification and ImplementationSpecification are required")
    if (
        firmware_spec.project_id != implementation_spec.project_id
        or implementation_spec.firmware_spec_id != firmware_spec.firmware_spec_id
        or implementation_spec.firmware_spec_fingerprint != firmware_spec.fingerprint
        or implementation_spec.conversation_id != firmware_spec.conversation_id
    ):
        raise ValueError("firmware and implementation specification binding mismatch")
    return FirmwareBuildRequest.create(
        project_id=firmware_spec.project_id,
        conversation_id=firmware_spec.conversation_id,
        firmware_reference=firmware_spec.firmware_spec_id,
        build_profile=configuration.profile,
        approval_reference=approval_reference,
        firmware_spec_id=firmware_spec.firmware_spec_id,
        firmware_spec_fingerprint=firmware_spec.fingerprint,
        implementation_id=implementation_spec.implementation_id,
        implementation_fingerprint=implementation_spec.fingerprint,
        target_kind=configuration.target_kind,
        build_system=configuration.build_system,
        toolchain_id=configuration.toolchain_id,
        toolchain_version=configuration.toolchain_version,
        configuration=configuration,
        source_artifacts=source_artifacts,
        expected_source_revision=expected_source_revision,
        actual_source_revision=actual_source_revision,
        hardware_firmware_conflicts=hardware_firmware_conflicts,
    )


def _source_digest(path: object) -> tuple[str, int]:
    content = path.read_bytes()
    return "sha256:" + hashlib.sha256(content).hexdigest(), len(content)


def evaluate_build_readiness(
    request: FirmwareBuildRequest,
    *,
    workspace_root: object | None = None,
    tool: ToolResolution | None = None,
) -> BuildReadinessResult:
    """Perform deterministic preflight without executing a compiler."""

    checked = FirmwareBuildRequest.model_validate(request.model_dump(mode="python"))
    reasons: list[str] = []
    missing: list[str] = []
    mismatched: list[str] = []
    missing_runtime: tuple[str, ...] = ()

    if checked.configuration is None:
        reasons.append(BuildReadinessStatus.BLOCKED_BUILD_CONFIGURATION_MISSING.value)
    if checked.target_kind is None:
        reasons.append(BuildReadinessStatus.BLOCKED_TARGET_UNKNOWN.value)
    if checked.build_system is None:
        reasons.append(BuildReadinessStatus.BLOCKED_BUILD_SYSTEM_UNKNOWN.value)
    elif checked.build_system not in _SUPPORTED_BUILD_SYSTEMS:
        reasons.append(BuildReadinessStatus.BLOCKED_UNSUPPORTED_BUILD_SYSTEM.value)
    if checked.toolchain_id is None or checked.toolchain_version is None:
        reasons.append(BuildReadinessStatus.BLOCKED_TOOLCHAIN_UNKNOWN.value)
    if not checked.source_artifacts:
        reasons.append(BuildReadinessStatus.BLOCKED_SOURCE_ARTIFACT_MISSING.value)
    if checked.expected_source_revision is not None and checked.actual_source_revision != checked.expected_source_revision:
        reasons.append(BuildReadinessStatus.BLOCKED_STALE_FIRMWARE_SOURCE.value)
    if checked.hardware_firmware_conflicts:
        reasons.append(BuildReadinessStatus.BLOCKED_HW_FW_CONFLICT.value)

    if checked.configuration is not None:
        if checked.target_kind is not None and checked.configuration.target_kind != checked.target_kind:
            reasons.append(BuildReadinessStatus.BLOCKED_AMBIGUOUS_CONFIGURATION.value)
        if checked.build_system is not None and checked.configuration.build_system != checked.build_system:
            reasons.append(BuildReadinessStatus.BLOCKED_AMBIGUOUS_CONFIGURATION.value)
        if checked.toolchain_id is not None and checked.configuration.toolchain_id != checked.toolchain_id:
            reasons.append(BuildReadinessStatus.BLOCKED_AMBIGUOUS_CONFIGURATION.value)

    if workspace_root is not None:
        root = workspace_root.resolve(strict=False)  # type: ignore[union-attr]
        if checked.configuration is not None:
            missing_runtime = _target_runtime_missing(root, checked.configuration)
        for source in checked.source_artifacts:
            candidate = (root / source.relative_path).resolve(strict=False)
            if not candidate.is_relative_to(root) or not candidate.is_file():
                missing.append(source.relative_path)
                continue
            try:
                actual_hash, actual_size = _source_digest(candidate)
            except OSError:
                missing.append(source.relative_path)
                continue
            if actual_hash != source.content_fingerprint or actual_size != source.size_bytes:
                mismatched.append(source.relative_path)
        if missing:
            reasons.append(BuildReadinessStatus.BLOCKED_SOURCE_ARTIFACT_MISSING.value)
        if mismatched:
            reasons.append(BuildReadinessStatus.BLOCKED_SOURCE_HASH_MISMATCH.value)

    if tool is not None:
        if tool.status != "AVAILABLE" or tool.executable is None:
            reasons.append(BuildReadinessStatus.BLOCKED_TOOLCHAIN_UNAVAILABLE.value)
        elif not tool.version_verified:
            reasons.append(BuildReadinessStatus.BLOCKED_TOOLCHAIN_UNKNOWN.value)
    if missing_runtime:
        reasons.append(BuildReadinessStatus.BLOCKED_TARGET_RUNTIME_MISSING.value)

    deduplicated = tuple(dict.fromkeys(reasons))
    return BuildReadinessResult.create(
        request_fingerprint=checked.fingerprint,
        status=(BuildReadinessStatus.READY if not deduplicated else BuildReadinessStatus(deduplicated[0])),
        reasons=deduplicated,
        missing_sources=tuple(dict.fromkeys(missing)),
        mismatched_sources=tuple(dict.fromkeys(mismatched)),
        missing_runtime=missing_runtime,
        toolchain_reason=None if tool is None else tool.reason_code,
    )


def blocked_build_result(
    request: FirmwareBuildRequest,
    readiness: BuildReadinessResult,
) -> FirmwareBuildResult:
    return FirmwareBuildResult.create(
        status=FirmwareBuildStatus.FAILED,
        artifact_reference="unavailable-artifact",
        build_status=FirmwareBuildStatus.UNAVAILABLE,
        summary="firmware build blocked by readiness preflight",
        request_fingerprint=request.fingerprint,
        result_status=FirmwareBuildResultStatus.BUILD_BLOCKED,
        readiness=readiness,
        diagnostics=readiness.reasons,
    )


__all__ = ["blocked_build_result", "create_firmware_build_request", "evaluate_build_readiness", "is_firmware_build_request"]
