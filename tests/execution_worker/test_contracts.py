from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from embedded_copilot.execution_worker.contracts import (
    ApprovalPurpose,
    ApprovalStatus,
    ArtifactIdentity,
    BuildOperation,
    ExecutionTarget,
    HilScenario,
    HilStep,
    HilStepKind,
    OperationApprovalGrant,
    OperationType,
    OperationRequest,
    TargetBindingStatus,
    TestOperation as ExecutionTestOperation,
    ToolchainIdentity,
)


NOW = datetime.now(UTC)
FP = "sha256:" + "1" * 64


def _build(**extra):
    return BuildOperation(
        operation_id="operation-build-1",
        tenant_id="tenant-1",
        project_id="project-1",
        requester_id="requester-1",
        target_id="target-1",
        target_fingerprint=FP,
        workspace_snapshot_id="snapshot-1",
        workspace_fingerprint=FP,
        proposal_id="proposal-1",
        proposal_fingerprint=FP,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
        nonce="nonce-1",
        toolchain=ToolchainIdentity(
            toolchain_id="CMAKE_NINJA_BUILD",
            tool_version="3",
            tool_family="CMAKE_NINJA",
            supported_operation=OperationType.BUILD,
            supported_target="STM32F446RE",
        ),
        configuration="DEBUG",
        expected_artifact_type="ELF",
        **extra,
    )


def _artifact(**extra) -> ArtifactIdentity:
    values = {
        "artifact_id": "artifact-test-1",
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "target_id": "target-1",
        "workspace_fingerprint": FP,
        "build_operation_id": "operation-build-1",
        "toolchain_id": "CMAKE_NINJA_BUILD",
        "toolchain_version": "3",
        "build_configuration": "DEBUG",
        "artifact_type": "ELF",
        "artifact_sha256": "sha256:" + "2" * 64,
        "size_bytes": 128,
        "created_at": NOW,
    }
    values.update(extra)
    return ArtifactIdentity(**values)


def _test_operation(**extra) -> ExecutionTestOperation:
    values = {
        "operation_id": "operation-test-1",
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "requester_id": "requester-1",
        "target_id": "target-1",
        "target_fingerprint": FP,
        "workspace_snapshot_id": "snapshot-1",
        "workspace_fingerprint": FP,
        "proposal_id": "proposal-1",
        "proposal_fingerprint": FP,
        "created_at": NOW,
        "expires_at": NOW + timedelta(minutes=10),
        "nonce": "nonce-test-1",
        "test_suite_id": "firmware-smoke",
        "test_profile_id": "default-validation",
    }
    values.update(extra)
    return ExecutionTestOperation(**values)


def test_operation_is_strict_frozen_and_never_accepts_command_fields() -> None:
    with pytest.raises(ValidationError):
        _build(command="cmake --build .")
    operation = _build()
    with pytest.raises(ValidationError):
        operation.configuration = "RELEASE"
    assert operation.fingerprint.startswith("sha256:")
    assert operation.fingerprint == operation.model_copy().fingerprint


def test_operation_request_revalidates_typed_union() -> None:
    operation = _build()
    request = OperationRequest(operation=operation)
    checked = OperationRequest.model_validate(request.model_dump(mode="python"))
    assert type(checked.operation) is BuildOperation
    assert checked.operation.fingerprint == operation.fingerprint


def test_test_operation_is_a_strict_typed_validation_action() -> None:
    operation = _test_operation()

    assert operation.operation_type is OperationType.TEST
    assert operation.tool_id == "FIRMWARE_TEST"
    assert operation.artifact is None
    assert operation.fingerprint.startswith("sha256:")
    with pytest.raises(ValidationError):
        _test_operation(tool_id="PYTEST")
    with pytest.raises(ValidationError):
        _test_operation(command="pytest -q")
    with pytest.raises(ValidationError):
        operation.test_suite_id = "changed"


def test_operation_request_revalidates_test_operation_union() -> None:
    operation = _test_operation(artifact=_artifact())

    checked = OperationRequest.model_validate(
        OperationRequest(operation=operation).model_dump(mode="python")
    )

    assert type(checked.operation) is ExecutionTestOperation
    assert checked.operation.artifact == operation.artifact
    assert checked.operation.fingerprint == operation.fingerprint


def test_test_operation_rejects_cross_bound_artifact() -> None:
    with pytest.raises(ValidationError, match="artifact target binding mismatch"):
        _test_operation(artifact=_artifact(target_id="target-other"))


def test_test_approval_purpose_is_operation_bound() -> None:
    operation = _test_operation()
    values = {
        "grant_id": "approval-test-1",
        "tenant_id": operation.tenant_id,
        "project_id": operation.project_id,
        "requester_id": operation.requester_id,
        "approval_purpose": ApprovalPurpose.TEST,
        "operation_id": operation.operation_id,
        "operation_type": operation.operation_type,
        "operation_fingerprint": operation.fingerprint,
        "proposal_id": operation.proposal_id,
        "proposal_fingerprint": operation.proposal_fingerprint,
        "target_id": operation.target_id,
        "target_fingerprint": operation.target_fingerprint,
        "workspace_snapshot_id": operation.workspace_snapshot_id,
        "workspace_fingerprint": operation.workspace_fingerprint,
        "reviewer_id": "reviewer-1",
        "status": ApprovalStatus.ISSUED,
        "expires_at": NOW + timedelta(minutes=5),
        "nonce": "approval-nonce-1",
        "issued_at": NOW,
    }

    grant = OperationApprovalGrant(**values)

    assert grant.approval_purpose is ApprovalPurpose.TEST
    with pytest.raises(ValidationError, match="approval purpose"):
        OperationApprovalGrant(
            **{**values, "approval_purpose": ApprovalPurpose.BUILD}
        )


def test_target_requires_explicit_project_binding() -> None:
    with pytest.raises(ValidationError):
        ExecutionTarget(
            target_id="target-1", tenant_id="tenant-1", project_id="project-1",
            target_kind="STM32F446RE", board_family="NUCLEO-F446RE", mcu_family="STM32F446RE",
            stable_identity_hash="probe-hash-1", candidate_id="candidate-1",
            binding_status=TargetBindingStatus.CANDIDATE, bound_by="operator-1", bound_at=NOW,
        )


def test_hil_steps_are_declarative_and_reject_script_shape() -> None:
    scenario = HilScenario(
        scenario_id="scenario-1", project_id="project-1", target_id="target-1", artifact_id="artifact-1",
        steps=(
            HilStep(kind=HilStepKind.WAIT_FOR_UART_PATTERN, pattern="BOOT_OK"),
            HilStep(kind=HilStepKind.ASSERT_NO_PANIC),
        ), timeout_seconds=60,
    )
    assert len(scenario.steps) == 2
    with pytest.raises(ValidationError):
        HilStep.model_validate({"kind": "WAIT_DURATION", "script": "python test.py", "duration_seconds": 1})
