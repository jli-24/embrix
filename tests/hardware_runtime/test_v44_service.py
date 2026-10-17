from datetime import UTC, datetime

import pytest

from embedded_copilot.hardware_runtime import (
    FakeHardwareAuditSink,
    FakeHardwareBuildPort,
    FakeHardwareFlashPort,
    FakeHardwareMeasurementPort,
    FakeHardwareOperationPlanStore,
    FakeHardwareResourcePolicy,
    FakeHardwareReviewPort,
    FakeHardwareTargetSource,
    FixedHardwareClock,
    HardwareApprovalRequired,
    HardwareCapability,
    HardwareObservationRequest,
    HardwareObservationStatus,
    HardwareObservationType,
    HardwareResourceGrant,
    HardwareReviewDecisionType,
    HardwareRisk,
    HardwareRuntimeService,
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
        available_capabilities=(
            HardwareCapability.BUILD,
            HardwareCapability.FLASH,
            HardwareCapability.UART_OBSERVE,
        ),
    )


def service():
    clock = FixedHardwareClock(NOW)
    item = target()
    reviews = FakeHardwareReviewPort(clock)
    build = FakeHardwareBuildPort(clock)
    flash = FakeHardwareFlashPort(clock)
    measurement = FakeHardwareMeasurementPort(clock)
    runtime = HardwareRuntimeService(
        targets=FakeHardwareTargetSource((item,)),
        resource_policy=FakeHardwareResourcePolicy(
            (
                HardwareResourceGrant(
                    project_id="project-1",
                    requester="engineer-1",
                    target_fingerprint=item.fingerprint,
                    capabilities=(HardwareCapability.UART_OBSERVE,),
                ),
            )
        ),
        build_port=build,
        flash_port=flash,
        measurement_port=measurement,
        reviews=reviews,
        plans=FakeHardwareOperationPlanStore(),
        audit=FakeHardwareAuditSink(),
        clock=clock,
    )
    return runtime, reviews, build, flash, measurement


def test_router_safe_observe_path_is_read_only() -> None:
    runtime, _, build, flash, measurement = service()
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
    result = runtime.observe(request)
    assert result.status is HardwareObservationStatus.OBSERVED
    assert measurement.calls == 1
    assert build.calls == 0
    assert flash.calls == 0


def test_build_requires_its_own_approval() -> None:
    runtime, reviews, build, _, _ = service()
    plan = runtime.prepare_build(
        project_id="project-1",
        target=target(),
        actor="engineer-1",
        reason="Create an approved firmware artifact",
        risk=HardwareRisk.MEDIUM,
    )
    review = runtime.submit_review(plan)
    with pytest.raises(HardwareApprovalRequired):
        runtime.authorize_build(plan.plan_id, review.request.review_id)
    reviews.decide(
        review.request.review_id, HardwareReviewDecisionType.APPROVE, "reviewer-1"
    )
    request = runtime.authorize_build(plan.plan_id, review.request.review_id)
    receipt = runtime.build(request)
    assert receipt.firmware_artifact is not None
    assert build.calls == 1
    assert runtime.build(request) == receipt
    assert build.calls == 1


def test_build_approval_cannot_be_reused_for_flash() -> None:
    runtime, reviews, _, _, _ = service()
    build_plan = runtime.prepare_build(
        project_id="project-1",
        target=target(),
        actor="engineer-1",
        reason="Build before flashing",
        risk=HardwareRisk.MEDIUM,
    )
    review = runtime.submit_review(build_plan)
    reviews.decide(
        review.request.review_id, HardwareReviewDecisionType.APPROVE, "reviewer-1"
    )
    build_receipt = runtime.build(
        runtime.authorize_build(build_plan.plan_id, review.request.review_id)
    )
    artifact = build_receipt.firmware_artifact
    assert artifact is not None
    flash_plan = runtime.prepare_flash(
        artifact=artifact,
        actor="engineer-1",
        reason="Flash separately approved artifact",
        risk=HardwareRisk.HIGH,
    )
    with pytest.raises(HardwareApprovalRequired):
        runtime.authorize_flash(
            flash_plan.plan_id, review.request.review_id, build_receipt
        )


def test_unconfigured_gpio_fixture_returns_unavailable_without_fake_measurement() -> (
    None
):
    clock = FixedHardwareClock(NOW)
    gpio_target = HardwareTargetReference(
        **{
            **target().model_dump(
                mode="python", exclude={"available_capabilities", "fingerprint"}
            ),
            "available_capabilities": (HardwareCapability.GPIO_OBSERVE,),
        }
    )
    measurement = FakeHardwareMeasurementPort(clock)
    runtime = HardwareRuntimeService(
        targets=FakeHardwareTargetSource((gpio_target,)),
        resource_policy=FakeHardwareResourcePolicy(
            (
                HardwareResourceGrant(
                    project_id="project-1",
                    requester="engineer-1",
                    target_fingerprint=gpio_target.fingerprint,
                    capabilities=(HardwareCapability.GPIO_OBSERVE,),
                ),
            )
        ),
        build_port=FakeHardwareBuildPort(clock),
        flash_port=FakeHardwareFlashPort(clock),
        measurement_port=measurement,
        reviews=FakeHardwareReviewPort(clock),
        plans=FakeHardwareOperationPlanStore(),
        audit=FakeHardwareAuditSink(),
        clock=clock,
    )
    result = runtime.observe(
        HardwareObservationRequest(
            request_id="observe-gpio",
            project_id="project-1",
            target=gpio_target,
            observation_type=HardwareObservationType.GPIO_STATE,
            timeout_seconds=5.0,
            requester="engineer-1",
            evidence_scope=("gpio",),
            requested_at=NOW,
        )
    )
    assert result.status is HardwareObservationStatus.UNAVAILABLE
    assert result.content is None
    assert result.evidence_refs == ()
