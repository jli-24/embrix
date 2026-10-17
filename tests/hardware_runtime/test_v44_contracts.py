from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.hardware_runtime import (
    HardwareCapability,
    HardwareObservationRequest,
    HardwareObservationType,
    HardwareTargetReference,
)

NOW = datetime(2026, 8, 10, tzinfo=UTC)


def target() -> HardwareTargetReference:
    return HardwareTargetReference(
        project_id="project-1",
        device_id="device-1",
        board_type="ESP32-S3-DevKitC-1",
        chip_family="ESP32-S3",
        target_profile="esp32-s3-devkitc-1:idf-5.3",
        firmware_target="esp32s3",
        available_capabilities=(HardwareCapability.UART_OBSERVE,),
    )


def test_target_capabilities_are_tuple_only_and_tamper_evident() -> None:
    value = target()
    with pytest.raises(ValidationError):
        HardwareTargetReference(
            **{
                **value.model_dump(mode="python", exclude={"fingerprint"}),
                "available_capabilities": [HardwareCapability.UART_OBSERVE],
            }
        )
    with pytest.raises(ValidationError):
        HardwareTargetReference.model_validate(
            {**value.model_dump(mode="python"), "fingerprint": "sha256:" + "9" * 64}
        )


def test_observation_request_is_read_only_and_explicit() -> None:
    request = HardwareObservationRequest(
        request_id="observe-1",
        project_id="project-1",
        target=target(),
        observation_type=HardwareObservationType.UART_BOOT_LOG,
        timeout_seconds=5.0,
        requester="engineer-1",
        evidence_scope=("boot",),
        requested_at=NOW,
    )
    assert request.target.device_id == "device-1"
    assert request.observation_type is HardwareObservationType.UART_BOOT_LOG
