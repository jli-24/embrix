from __future__ import annotations

from datetime import UTC, datetime

from embedded_copilot.project_catalog.firmware_production import (
    FirmwareBuildArtifactProjection,
    FirmwareBuildReceiptProjection,
    FirmwareProductionState,
    FirmwareProductionStatus,
)


NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def _fp(letter: str) -> str:
    return "sha256:" + letter * 64


def _state() -> FirmwareProductionState:
    receipt = FirmwareBuildReceiptProjection(
        receipt_id="build-receipt-g6",
        project_id="project-g6",
        conversation_id="conversation-g6",
        operation_id="firmware-build-g6",
        operation_receipt_fingerprint=_fp("a"),
        request_fingerprint=_fp("b"),
        firmware_spec_id="firmware-spec-g6",
        firmware_plan_fingerprint=_fp("c"),
        hw_fw_binding_fingerprint=_fp("d"),
        design_truth_id="truth-g6",
        design_truth_fingerprint=_fp("e"),
        implementation_id="implementation-g6",
        implementation_fingerprint=_fp("f"),
        build_configuration_id="build-config-g6",
        build_configuration_fingerprint=_fp("1"),
        source_fingerprints=(_fp("2"),),
        status="BUILD_FAIL",
        toolchain_id="ARM_NONE_EABI_GCC",
        toolchain_version="15.3.1",
        build_system="CMAKE",
        artifact_ids=("artifact-g6-log",),
        log_artifact_id="artifact-g6-log",
        compiler_exit_code=1,
        tool_observed=True,
        created_at=NOW,
    )
    artifact = FirmwareBuildArtifactProjection(
        artifact_id="artifact-g6-log",
        artifact_type="LOG",
        relative_path=".embedded-copilot-state/build/firmware-build-g6/build.log",
        content_fingerprint=_fp("3"),
        size_bytes=128,
        build_receipt_id=receipt.receipt_id,
    )
    return FirmwareProductionState(
        production_id="firmware-production-g6",
        project_id=receipt.project_id,
        conversation_id=receipt.conversation_id,
        firmware_spec_id=receipt.firmware_spec_id,
        firmware_plan_fingerprint=receipt.firmware_plan_fingerprint,
        hw_fw_binding_fingerprint=receipt.hw_fw_binding_fingerprint,
        design_truth_id=receipt.design_truth_id,
        design_truth_fingerprint=receipt.design_truth_fingerprint,
        implementation_id=receipt.implementation_id,
        implementation_fingerprint=receipt.implementation_fingerprint,
        build_configuration_id=receipt.build_configuration_id,
        build_configuration_fingerprint=receipt.build_configuration_fingerprint,
        source_fingerprints=receipt.source_fingerprints,
        build_receipt=receipt,
        artifacts=(artifact,),
        status=FirmwareProductionStatus.BUILD_FAILED,
        updated_at=NOW,
    )


def test_firmware_production_state_is_typed_and_restart_stable() -> None:
    state = _state()
    restored = FirmwareProductionState.model_validate(state.model_dump(mode="python"), strict=False)

    assert restored == state
    assert restored.build_receipt.tool_observed is True
    assert restored.artifacts[0].relative_path.startswith(".embedded-copilot-state/")


def test_failed_build_is_stale_safe_without_becoming_a_gate_pass() -> None:
    stale = _state().mark_stale(reason="upstream DesignTruth changed", updated_at=NOW)

    assert stale.status is FirmwareProductionStatus.STALE
    assert stale.stale_inputs == ("upstream DesignTruth changed",)
    assert stale.build_receipt.status == "BUILD_FAIL"

