from __future__ import annotations

from datetime import UTC, datetime

from embedded_copilot.execution import (
    BuildArtifactReference,
    BuildResult,
    DebugObservation,
    DebugSeverity,
    ExecutionStatus,
)
from embedded_copilot.verification_loop import (
    VerificationRequest,
    VerificationService,
    VerificationStatus,
)


def _build(status: ExecutionStatus) -> BuildResult:
    artifact = BuildArtifactReference.from_bytes(
        project_id="project-1",
        workspace_id="workspace-1",
        target_id="target-1",
        content=b"firmware",
    )
    return BuildResult.create(
        request_id="build-request-1",
        status=status,
        exit_code=0 if status is ExecutionStatus.SUCCEEDED else 2,
        artifact=artifact if status is ExecutionStatus.SUCCEEDED else None,
        logs_summary="build completed"
        if status is ExecutionStatus.SUCCEEDED
        else "build output redacted",
    )


def _request(
    build: BuildResult, debug: DebugObservation | None = None
) -> VerificationRequest:
    return VerificationRequest.create(
        verification_id="verification-1",
        project_id="project-1",
        target_id="target-1",
        repair_proposal_fingerprint="sha256:" + "c" * 64,
        prior_failure_fingerprint="sha256:" + "d" * 64,
        build_request_id=build.request_id,
        debug_request_id=debug.request_id if debug else None,
        build_result=build,
        debug_observation=debug,
    )


def test_successful_build_is_execution_pass_not_hardware_claim() -> None:
    result = VerificationService().verify(_request(_build(ExecutionStatus.SUCCEEDED)))
    assert result.status is VerificationStatus.PASS
    assert "hardware" not in result.summary.casefold()


def test_failed_build_and_error_debug_are_failures() -> None:
    assert (
        VerificationService().verify(_request(_build(ExecutionStatus.FAILED))).status
        is VerificationStatus.FAIL
    )
    debug = DebugObservation.create(
        observation_id="debug-1",
        request_id="debug-request-1",
        project_id="project-1",
        target_id="target-1",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        source="UART",
        severity=DebugSeverity.ERROR,
        log_lines=("error signal",),
        confidence=0.7,
    )
    assert (
        VerificationService()
        .verify(_request(_build(ExecutionStatus.SUCCEEDED), debug))
        .status
        is VerificationStatus.FAIL
    )


def test_reused_failure_fingerprint_escalates() -> None:
    build = _build(ExecutionStatus.SUCCEEDED)
    request = _request(build).model_dump(mode="python")
    request["prior_failure_fingerprint"] = build.fingerprint
    request.pop("fingerprint")
    assert (
        VerificationService().verify(VerificationRequest.create(**request)).status
        is VerificationStatus.ESCALATE
    )
