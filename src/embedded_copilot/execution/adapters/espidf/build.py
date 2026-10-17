from __future__ import annotations

import asyncio
from pathlib import Path

from ...exceptions import ExecutionRejected, ExecutionUnavailable
from ...integration_contracts import (
    BuildArtifactReference,
    BuildRequest,
    BuildResult,
    ESPIDFToolConfiguration,
    ExecutionStatus,
)
from ...ports import CancellationPort, WorkspaceResolverPort
from ..runner import ProcessOutcome, ProcessRunnerPort, SubprocessRunner


class ESPIDFBuildAdapter:
    def __init__(
        self,
        *,
        configuration: ESPIDFToolConfiguration,
        workspace_resolver: WorkspaceResolverPort,
        runner: ProcessRunnerPort | None = None,
        cancellation: CancellationPort | None = None,
    ) -> None:
        self._configuration = ESPIDFToolConfiguration.model_validate(
            configuration.model_dump(mode="python")
        )
        self._resolver = workspace_resolver
        self._cancellation = cancellation
        self._runner = runner or SubprocessRunner(cancellation)

    def build(self, request: BuildRequest) -> BuildResult:
        checked = BuildRequest.model_validate(request.model_dump(mode="python"))
        if (
            checked.request.project_id != self._configuration.project_id
            or checked.request.target_id != self._configuration.target_id
        ):
            raise ExecutionRejected("build target rejected")
        if checked.request.cancellation_token and self._is_cancelled(
            checked.request.cancellation_token
        ):
            raise asyncio.CancelledError
        workspace = self._resolver.resolve(checked.workspace_snapshot)
        workspace.assert_current()
        try:
            outcome = self._runner.run(
                (self._configuration.idf_executable, "build"),
                cwd=workspace.root,
                timeout_seconds=checked.request.timeout_seconds,
                cancellation_token=checked.request.cancellation_token,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return BuildResult.create(
                request_id=checked.request.request_id,
                status=ExecutionStatus.TIMEOUT,
                exit_code=None,
                artifact=None,
                logs_summary="build timed out",
            )
        except (OSError, ValueError):
            raise ExecutionUnavailable("build unavailable") from None
        workspace.assert_current()
        if not isinstance(outcome, ProcessOutcome):
            raise ExecutionRejected("build response rejected")
        if outcome.returncode != 0:
            return BuildResult.create(
                request_id=checked.request.request_id,
                status=ExecutionStatus.FAILED,
                exit_code=outcome.returncode,
                artifact=None,
                logs_summary=_safe_summary(outcome.stderr or outcome.stdout),
            )
        try:
            workspace.assert_current()
            artifact_path = _artifact_path(
                workspace.root, self._configuration.artifact_relative_path
            )
            content = artifact_path.read_bytes()
            if len(content) == 0 or len(content) > 64 * 1024 * 1024:
                raise ValueError
            workspace.assert_current()
        except (OSError, ValueError):
            raise ExecutionRejected("build artifact rejected") from None
        artifact = BuildArtifactReference.from_bytes(
            project_id=checked.request.project_id,
            workspace_id=checked.workspace_snapshot.workspace_id,
            target_id=checked.request.target_id,
            content=content,
        )
        return BuildResult.create(
            request_id=checked.request.request_id,
            status=ExecutionStatus.SUCCEEDED,
            exit_code=0,
            artifact=artifact,
            logs_summary=_safe_summary(outcome.stdout),
        )

    def _is_cancelled(self, token: str) -> bool:
        return bool(self._cancellation and self._cancellation.is_cancelled(token))


def _safe_summary(value: bytes) -> str:
    return "build output redacted" if value else "build completed"


def _artifact_path(root: Path, relative_path: str) -> Path:
    candidate = root.joinpath(*relative_path.split("/")).resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ValueError("artifact path is invalid") from None
    return candidate


__all__ = ("ESPIDFBuildAdapter",)
