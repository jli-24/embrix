from __future__ import annotations

import hashlib
from pathlib import Path

from embedded_copilot.execution_worker import ExecutionToolRegistry, TrustedToolDiscovery
from embedded_copilot.execution_worker.contracts import OperationType
from embedded_copilot.firmware_engineering import (
    BuildReadinessStatus,
    FirmwareBuildConfiguration,
    FirmwareBuildRequest,
    FirmwareSourceArtifact,
    evaluate_build_readiness,
)


ROOT = Path(__file__).parents[2]


def test_current_mainline_firmware_build_golden_reports_real_toolchain_truth() -> None:
    source = ROOT / "demo" / "esp32_camera" / "camera_firmware.c"
    content = source.read_bytes()
    configuration = FirmwareBuildConfiguration(
        target_kind="ESP32-S3",
        build_system="ESP_IDF",
        toolchain_id="ESP_IDF_BUILD",
        toolchain_version="v5.3",
        profile="DEBUG",
        expected_artifact_types=("BIN",),
    )
    request = FirmwareBuildRequest.create(
        project_id="esp32-camera-golden",
        firmware_reference="camera-firmware-golden",
        build_profile="DEBUG",
        approval_reference=None,
        firmware_spec_id="firmware-spec-camera-golden",
        firmware_spec_fingerprint="sha256:" + "1" * 64,
        implementation_id="implementation-camera-golden",
        implementation_fingerprint="sha256:" + "2" * 64,
        target_kind="ESP32-S3",
        build_system="ESP_IDF",
        toolchain_id="ESP_IDF_BUILD",
        toolchain_version="v5.3",
        configuration=configuration,
        source_artifacts=(
            FirmwareSourceArtifact(
                relative_path="demo/esp32_camera/camera_firmware.c",
                content_fingerprint="sha256:" + hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
            ),
        ),
    )
    registry = ExecutionToolRegistry(
        TrustedToolDiscovery(
            allow_environment_paths=True,
            trusted_versions={"ESP_IDF_BUILD": "v5.3"},
        )
    )
    tool = registry.resolve(
        "ESP_IDF_BUILD",
        target_kind="ESP32-S3",
        operation=OperationType.BUILD,
    )
    readiness = evaluate_build_readiness(request, workspace_root=ROOT, tool=tool)
    assert readiness.request_fingerprint == request.fingerprint
    if tool.status != "AVAILABLE":
        assert readiness.status is BuildReadinessStatus.BLOCKED_TOOLCHAIN_UNAVAILABLE
        return
    # A future enabled Golden must execute the real ESP-IDF project, not this
    # metadata fixture; the current repository has no approved ESP-IDF project
    # and therefore deliberately cannot claim BUILD_PASS.
    assert readiness.status is not BuildReadinessStatus.READY
