from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from embedded_copilot.execution import (
    ApprovalReference,
    BuildRequest,
    ESPIDFToolConfiguration,
    ExecutionCapability,
    ExecutionRejected,
    ExecutionRequest,
    ExecutionStatus,
    RegisteredWorkspaceResolver,
)
from embedded_copilot.execution.adapters.espidf.build import ESPIDFBuildAdapter
from embedded_copilot.execution.adapters.runner import ProcessOutcome
from embedded_copilot.execution.ports import ResolvedWorkspace
from embedded_copilot.workspace_runtime import (
    WorkspaceInspectionRequest,
    create_workspace_runtime,
)


def _snapshot(root: Path):
    (root / "src").mkdir()
    (root / "src" / "main.c").write_text("int main(void) {}\n", encoding="utf-8")
    return (
        create_workspace_runtime(root)
        .workspace_port()
        .inspect_workspace(
            WorkspaceInspectionRequest(
                workspace_id="workspace-1", relative_paths=("src/main.c",)
            )
        )
    )


def _request(capability: ExecutionCapability = ExecutionCapability.BUILD):
    approval = ApprovalReference.create(
        approval_id="approval-1",
        project_id="project-1",
        target_id="target-1",
        capability=capability,
        proposal_fingerprint="sha256:" + "a" * 64,
        expires_at="2030-01-01T00:00:00Z",
    )
    return ExecutionRequest.create(
        request_id="request-1",
        project_id="project-1",
        target_id="target-1",
        proposal_fingerprint="sha256:" + "a" * 64,
        capability=capability,
        approval=approval,
        timeout_seconds=5.0,
        cancellation_token="cancel-1",
    )


class _Resolver:
    def __init__(self, root: Path):
        self.root = root

    def resolve(self, snapshot):
        return ResolvedWorkspace(self.root, lambda: None)


class _Runner:
    def __init__(self, outcome: object):
        self.outcome = outcome
        self.calls: list[tuple[tuple[str, ...], Path]] = []

    def run(self, argv, *, cwd, timeout_seconds, cancellation_token):
        self.calls.append((tuple(argv), cwd))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class _Cancellation:
    def __init__(self, cancelled: bool) -> None:
        self._cancelled = cancelled

    def is_cancelled(self, cancellation_token: str) -> bool:
        return self._cancelled


def _configuration() -> ESPIDFToolConfiguration:
    return ESPIDFToolConfiguration.create(
        configuration_id="idf-config-1",
        project_id="project-1",
        workspace_id="workspace-1",
        target_id="target-1",
        idf_executable="idf.py",
        esptool_executable="esptool.py",
        artifact_relative_path="build/app.bin",
        serial_port="COM7",
    )


def test_build_uses_fixed_command_workspace_and_returns_artifact(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    (tmp_path / "build").mkdir()
    content = b"firmware-image"
    (tmp_path / "build" / "app.bin").write_bytes(content)
    runner = _Runner(ProcessOutcome(0, b"build complete", b""))
    result = ESPIDFBuildAdapter(
        configuration=_configuration(),
        workspace_resolver=_Resolver(tmp_path),
        runner=runner,
    ).build(BuildRequest.create(request=_request(), workspace_snapshot=snapshot))

    assert result.status is ExecutionStatus.SUCCEEDED
    assert result.artifact is not None
    assert result.artifact.size_bytes == len(content)
    assert runner.calls == [(("idf.py", "build"), tmp_path)]
    assert "idf.py" not in result.logs_summary


def test_build_failure_redacts_output_and_timeout_is_structured(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    failed = _Runner(ProcessOutcome(2, b"", b"C:\\private\\secret token=password"))
    result = ESPIDFBuildAdapter(
        configuration=_configuration(),
        workspace_resolver=_Resolver(tmp_path),
        runner=failed,
    ).build(BuildRequest.create(request=_request(), workspace_snapshot=snapshot))
    assert result.status is ExecutionStatus.FAILED
    assert "private" not in result.logs_summary.casefold()
    assert "password" not in result.logs_summary.casefold()

    timed = _Runner(TimeoutError("private timeout details"))
    timeout_result = ESPIDFBuildAdapter(
        configuration=_configuration(),
        workspace_resolver=_Resolver(tmp_path),
        runner=timed,
    ).build(BuildRequest.create(request=_request(), workspace_snapshot=snapshot))
    assert timeout_result.status is ExecutionStatus.TIMEOUT


def test_build_cancellation_and_malformed_runner_fail_closed(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    cancelled = _Runner(asyncio.CancelledError())
    adapter = ESPIDFBuildAdapter(
        configuration=_configuration(),
        workspace_resolver=_Resolver(tmp_path),
        runner=cancelled,
    )
    with pytest.raises(asyncio.CancelledError):
        adapter.build(
            BuildRequest.create(request=_request(), workspace_snapshot=snapshot)
        )

    malformed = _Runner(object())
    with pytest.raises(ExecutionRejected):
        ESPIDFBuildAdapter(
            configuration=_configuration(),
            workspace_resolver=_Resolver(tmp_path),
            runner=malformed,
        ).build(BuildRequest.create(request=_request(), workspace_snapshot=snapshot))

    with pytest.raises(asyncio.CancelledError):
        ESPIDFBuildAdapter(
            configuration=_configuration(),
            workspace_resolver=_Resolver(tmp_path),
            runner=_Runner(ProcessOutcome(0, b"", b"")),
            cancellation=_Cancellation(True),
        ).build(BuildRequest.create(request=_request(), workspace_snapshot=snapshot))


def test_registered_resolver_rejects_changed_snapshot_and_root_identity(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    snapshot = _snapshot(root)
    port = create_workspace_runtime(root).workspace_port()
    resolver = RegisteredWorkspaceResolver(
        root=root, workspace_id="workspace-1", port=port
    )

    (root / "src" / "main.c").write_text("int changed(void) {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="workspace snapshot rejected"):
        resolver.resolve(snapshot)

    parked = tmp_path / "parked"
    root.rename(parked)
    root.mkdir()
    with pytest.raises(ValueError, match="workspace identity rejected"):
        resolver.resolve(snapshot)
