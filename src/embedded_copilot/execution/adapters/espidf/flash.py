from __future__ import annotations

import asyncio
from pathlib import Path

from ...exceptions import ExecutionRejected, ExecutionUnavailable
from ...integration_contracts import (
    BuildArtifactReference,
    ESPIDFToolConfiguration,
    ExecutionStatus,
    FlashRequest,
    FlashResult,
)
from ...ports import CancellationPort, WorkspaceResolverPort
from ..runner import ProcessOutcome, ProcessRunnerPort, SubprocessRunner


class EsptoolFlashAdapter:
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

    def flash(self, request: FlashRequest) -> FlashResult:
        checked = FlashRequest.model_validate(request.model_dump(mode="python"))
        if (
            checked.request.project_id != self._configuration.project_id
            or checked.request.target_id != self._configuration.target_id
        ):
            raise ExecutionRejected("flash target rejected")
        if checked.request.cancellation_token and self._is_cancelled(
            checked.request.cancellation_token
        ):
            raise asyncio.CancelledError
        workspace = self._resolver.resolve(checked.workspace_snapshot)
        workspace.assert_current()
        try:
            artifact_path = _artifact_path(
                workspace.root, self._configuration.artifact_relative_path
            )
            content = artifact_path.read_bytes()
        except (OSError, ValueError):
            raise ExecutionRejected("flash artifact rejected") from None
        if (
            BuildArtifactReference.from_bytes(
                project_id=checked.artifact.project_id,
                workspace_id=checked.artifact.workspace_id,
                target_id=checked.artifact.target_id,
                content=content,
            ).firmware_fingerprint
            != checked.artifact.firmware_fingerprint
        ):
            raise ExecutionRejected("flash artifact rejected")
        try:
            outcome = self._runner.run(
                (
                    self._configuration.esptool_executable,
                    "--chip",
                    self._configuration.chip,
                    "--port",
                    self._configuration.serial_port,
                    "--baud",
                    str(self._configuration.baud_rate),
                    "write_flash",
                    self._configuration.flash_offset,
                    self._configuration.artifact_relative_path,
                ),
                cwd=workspace.root,
                timeout_seconds=checked.request.timeout_seconds,
                cancellation_token=checked.request.cancellation_token,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return FlashResult.create(
                request_id=checked.request.request_id,
                status=ExecutionStatus.TIMEOUT,
                exit_code=None,
                firmware_fingerprint=checked.artifact.firmware_fingerprint,
                logs_summary="flash timed out",
            )
        except (OSError, ValueError):
            raise ExecutionUnavailable("flash unavailable") from None
        workspace.assert_current()
        if not isinstance(outcome, ProcessOutcome):
            raise ExecutionRejected("flash response rejected")
        return FlashResult.create(
            request_id=checked.request.request_id,
            status=ExecutionStatus.SUCCEEDED
            if outcome.returncode == 0
            else ExecutionStatus.FAILED,
            exit_code=outcome.returncode,
            firmware_fingerprint=checked.artifact.firmware_fingerprint,
            logs_summary=_safe_summary(
                outcome.stderr if outcome.returncode else outcome.stdout
            ),
        )

    def _is_cancelled(self, token: str) -> bool:
        return bool(self._cancellation and self._cancellation.is_cancelled(token))


def _safe_summary(value: bytes) -> str:
    return "flash output redacted" if value else "flash completed"


def _artifact_path(root: Path, relative_path: str) -> Path:
    candidate = root.joinpath(*relative_path.split("/")).resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ValueError("artifact path is invalid") from None
    return candidate


__all__ = ("EsptoolFlashAdapter",)
