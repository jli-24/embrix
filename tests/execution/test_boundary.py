from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from embedded_copilot.execution import (
    ApprovalReference,
    BuildRequest,
    BuildResult,
    ExecutionCapability,
    ExecutionRejected,
    ExecutionRequest,
    ExecutionService,
    ExecutionStatus,
    ExecutionUnavailable,
    FakeBuildAdapter,
    FakeDebugAdapter,
)
from embedded_copilot.workspace_runtime import (
    WorkspaceInspectionRequest,
    create_workspace_runtime,
)


def _approval(capability=ExecutionCapability.BUILD):
    return ApprovalReference.create(
        approval_id="approval-1",
        project_id="project-1",
        target_id="target-1",
        capability=capability,
        proposal_fingerprint="sha256:" + "a" * 64,
        expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    )


def _request(capability=ExecutionCapability.BUILD, approval=True):
    return ExecutionRequest.create(
        request_id="request-1",
        project_id="project-1",
        target_id="target-1",
        proposal_fingerprint="sha256:" + "a" * 64,
        capability=capability,
        approval=_approval(capability) if approval else None,
        timeout_seconds=10.0,
        cancellation_token="cancel-1",
    )


def test_build_and_flash_require_bound_approval() -> None:
    with pytest.raises(ValidationError, match="approval required"):
        _request(ExecutionCapability.BUILD, approval=False)
    wrong = _approval(ExecutionCapability.FLASH)
    with pytest.raises(ValidationError, match="approval binding mismatch"):
        ExecutionRequest.create(
            request_id="request-1",
            project_id="project-1",
            target_id="target-1",
            proposal_fingerprint="sha256:" + "a" * 64,
            capability=ExecutionCapability.BUILD,
            approval=wrong,
        )


def test_capabilities_are_separate_and_fakes_are_offline() -> None:
    service = ExecutionService(
        build_port=FakeBuildAdapter(), debug_port=FakeDebugAdapter()
    )
    assert service.build(_request()).status == "UNAVAILABLE"
    debug = _request(ExecutionCapability.DEBUG_LOGS, approval=False)
    assert service.collect_logs(debug).capability == ExecutionCapability.DEBUG_LOGS
    with pytest.raises(ValidationError, match="approval required"):
        _request(ExecutionCapability.FLASH, approval=False)


def test_malformed_and_timeout_adapter_results_are_sanitized() -> None:
    class Malformed:
        def build(self, request):
            return object()

    class TimedOut:
        def build(self, request):
            raise TimeoutError("private diagnostic")

    with pytest.raises(ExecutionRejected, match="execution response rejected"):
        ExecutionService(build_port=Malformed()).build(_request())
    with pytest.raises(ExecutionUnavailable, match="execution unavailable"):
        ExecutionService(build_port=TimedOut()).build(_request())


def test_service_revalidates_specialized_build_result(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.c").write_text("int main(void) {}\n", encoding="utf-8")
    snapshot = (
        create_workspace_runtime(tmp_path)
        .workspace_port()
        .inspect_workspace(
            WorkspaceInspectionRequest(
                workspace_id="workspace-1", relative_paths=("src/main.c",)
            )
        )
    )
    request = BuildRequest.create(request=_request(), workspace_snapshot=snapshot)

    class Specialized:
        def build(self, value):
            return BuildResult.create(
                request_id=value.request.request_id,
                status=ExecutionStatus.UNAVAILABLE,
                exit_code=None,
                artifact=None,
                logs_summary="build unavailable",
            )

    result = ExecutionService(build_port=Specialized()).build(request)
    assert isinstance(result, BuildResult)
    assert result.status is ExecutionStatus.UNAVAILABLE

    offline = ExecutionService(build_port=FakeBuildAdapter()).build(request)
    assert isinstance(offline, BuildResult)
    assert offline.status is ExecutionStatus.UNAVAILABLE
