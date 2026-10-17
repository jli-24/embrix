from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from embedded_copilot.hardware_hil_runtime import (
    EngineeringProposalKind,
    EngineeringProposalReference,
    HardwareFixtureReference,
    HardwareObservationType,
    HardwareTestProposalRequest,
    HILExecutionStatus,
    HILRiskLevel,
    HILTestType,
)
from embedded_copilot.hardware_runtime import (
    FirmwareArtifactReference,
    HardwareCapability,
    HardwareTargetReference,
)

NOW = datetime(2026, 8, 10, tzinfo=UTC)


def target() -> HardwareTargetReference:
    return HardwareTargetReference(
        project_id="project-1",
        device_id="device-1",
        board_type="devkit",
        chip_family="esp32",
        target_profile="profile-1",
        firmware_target="app",
        available_capabilities=(HardwareCapability.UART_OBSERVE,),
    )


def firmware() -> FirmwareArtifactReference:
    return FirmwareArtifactReference(
        artifact_id="firmware-1",
        project_id="project-1",
        workspace_snapshot_fingerprint="sha256:" + "1" * 64,
        target=target(),
        firmware_fingerprint="sha256:" + "2" * 64,
        build_provenance_fingerprint="sha256:" + "3" * 64,
        created_at=NOW,
    )


def proposal_request() -> HardwareTestProposalRequest:
    board = target()
    return HardwareTestProposalRequest(
        request_id="request-1",
        project_id="project-1",
        source_proposal=EngineeringProposalReference(
            project_id="project-1",
            kind=EngineeringProposalKind.PCB,
            proposal_id="proposal-1",
            proposal_fingerprint="sha256:" + "4" * 64,
        ),
        target=board,
        firmware=firmware(),
        fixture=HardwareFixtureReference(
            project_id="project-1",
            fixture_id="fixture-1",
            target_fingerprint=board.fingerprint,
            supported_observation_types=(HardwareObservationType.UART_BOOT,),
            calibration_evidence_fingerprint="sha256:" + "5" * 64,
            expires_at=NOW + timedelta(days=1),
        ),
        test_type=HILTestType.BOOT,
        test_steps=("observe boot",),
        expected_observations=(HardwareObservationType.UART_BOOT,),
        risk_level=HILRiskLevel.LOW,
        evidence_refs=("evidence-1",),
        created_by="engineer-1",
        created_at=NOW,
    )


def test_contract_is_frozen_and_fingerprint_is_deterministic():
    first = proposal_request()
    second = proposal_request()
    assert first.fingerprint == second.fingerprint
    with pytest.raises(ValidationError):
        first.project_id = "project-2"


def test_contract_rejects_cross_project_and_unsafe_step():
    material = proposal_request().model_dump(mode="python")
    material["fixture"]["project_id"] = "project-2"
    with pytest.raises(ValidationError):
        HardwareTestProposalRequest(**material)
    material = proposal_request().model_dump(mode="python")
    material["test_steps"] = ("subprocess run",)
    with pytest.raises(ValidationError):
        HardwareTestProposalRequest(**material)


def test_execution_status_has_no_passed_or_verified_states():
    assert {item.value for item in HILExecutionStatus} == {
        "STARTED",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
    }
