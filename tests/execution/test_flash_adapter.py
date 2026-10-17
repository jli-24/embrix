from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from embedded_copilot.execution import (
    ApprovalReference,
    BuildArtifactReference,
    ESPIDFToolConfiguration,
    ExecutionCapability,
    ExecutionRequest,
    FlashRequest,
)
from embedded_copilot.execution.adapters.espidf.flash import EsptoolFlashAdapter
from embedded_copilot.execution.adapters.runner import ProcessOutcome
from embedded_copilot.execution.ports import ResolvedWorkspace
from embedded_copilot.workspace_runtime import (
    WorkspaceInspectionRequest,
    create_workspace_runtime,
)


def _setup(root: Path):
    (root / "src").mkdir()
    (root / "src" / "main.c").write_text("int main(void) {}\n", encoding="utf-8")
    snapshot = (
        create_workspace_runtime(root)
        .workspace_port()
        .inspect_workspace(
            WorkspaceInspectionRequest(
                workspace_id="workspace-1", relative_paths=("src/main.c",)
            )
        )
    )
    (root / "build").mkdir()
    content = b"firmware-image"
    (root / "build" / "app.bin").write_bytes(content)
    artifact = BuildArtifactReference.from_bytes(
        project_id="project-1",
        workspace_id="workspace-1",
        target_id="target-1",
        content=content,
    )
    return snapshot, artifact


def _config() -> ESPIDFToolConfiguration:
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


def _request(artifact: BuildArtifactReference, *, target_id="target-1", approval=True):
    approval_ref = (
        ApprovalReference.create(
            approval_id="approval-1",
            project_id="project-1",
            target_id=target_id,
            capability=ExecutionCapability.FLASH,
            proposal_fingerprint="sha256:" + "a" * 64,
            expires_at="2030-01-01T00:00:00Z",
        )
        if approval
        else None
    )
    return ExecutionRequest.create(
        request_id="flash-request-1",
        project_id="project-1",
        target_id=target_id,
        proposal_fingerprint="sha256:" + "a" * 64,
        capability=ExecutionCapability.FLASH,
        approval=approval_ref,
        timeout_seconds=5.0,
        cancellation_token="cancel-1",
    )


class _Resolver:
    def __init__(self, root: Path):
        self.root = root

    def resolve(self, snapshot):
        return ResolvedWorkspace(self.root, lambda: None)


class _Runner:
    def __init__(self, result):
        self.result, self.calls = result, []

    def run(self, argv, **kwargs):
        self.calls.append((tuple(argv), kwargs))
        return self.result


class _Cancellation:
    def is_cancelled(self, cancellation_token: str) -> bool:
        return True


def test_flash_uses_bound_artifact_and_fixed_target_command(tmp_path: Path) -> None:
    snapshot, artifact = _setup(tmp_path)
    runner = _Runner(ProcessOutcome(0, b"ok", b""))
    result = EsptoolFlashAdapter(
        configuration=_config(), workspace_resolver=_Resolver(tmp_path), runner=runner
    ).flash(
        FlashRequest.create(
            request=_request(artifact), workspace_snapshot=snapshot, artifact=artifact
        )
    )
    assert result.status.value == "SUCCEEDED"
    assert runner.calls[0][0] == (
        "esptool.py",
        "--chip",
        "esp32",
        "--port",
        "COM7",
        "--baud",
        "115200",
        "write_flash",
        "0x10000",
        "build/app.bin",
    )


def test_flash_rejects_missing_approval_target_or_firmware_mismatch(
    tmp_path: Path,
) -> None:
    snapshot, artifact = _setup(tmp_path)
    with pytest.raises(ValidationError, match="approval required"):
        FlashRequest.create(
            request=_request(artifact, approval=False),
            workspace_snapshot=snapshot,
            artifact=artifact,
        )
    with pytest.raises(ValidationError, match="target mismatch"):
        FlashRequest.create(
            request=_request(artifact, target_id="target-2"),
            workspace_snapshot=snapshot,
            artifact=artifact,
        )
    tampered = artifact.model_copy(
        update={"firmware_fingerprint": "sha256:" + "f" * 64}
    )
    with pytest.raises(ValidationError):
        FlashRequest.create(
            request=_request(artifact), workspace_snapshot=snapshot, artifact=tampered
        )


def test_flash_timeout_and_cancellation_are_not_success(tmp_path: Path) -> None:
    snapshot, artifact = _setup(tmp_path)

    class Timed:
        def run(self, *args, **kwargs):
            raise TimeoutError

    result = EsptoolFlashAdapter(
        configuration=_config(), workspace_resolver=_Resolver(tmp_path), runner=Timed()
    ).flash(
        FlashRequest.create(
            request=_request(artifact), workspace_snapshot=snapshot, artifact=artifact
        )
    )
    assert result.status.value == "TIMEOUT"

    class Cancelled:
        def run(self, *args, **kwargs):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        EsptoolFlashAdapter(
            configuration=_config(),
            workspace_resolver=_Resolver(tmp_path),
            runner=Cancelled(),
        ).flash(
            FlashRequest.create(
                request=_request(artifact),
                workspace_snapshot=snapshot,
                artifact=artifact,
            )
        )

    with pytest.raises(asyncio.CancelledError):
        EsptoolFlashAdapter(
            configuration=_config(),
            workspace_resolver=_Resolver(tmp_path),
            runner=_Runner(ProcessOutcome(0, b"", b"")),
            cancellation=_Cancellation(),
        ).flash(
            FlashRequest.create(
                request=_request(artifact),
                workspace_snapshot=snapshot,
                artifact=artifact,
            )
        )


def test_flash_configuration_rejects_outside_artifact_path() -> None:
    with pytest.raises(ValidationError, match="artifact_relative_path is invalid"):
        ESPIDFToolConfiguration.create(
            configuration_id="idf-config-1",
            project_id="project-1",
            workspace_id="workspace-1",
            target_id="target-1",
            idf_executable="idf.py",
            esptool_executable="esptool.py",
            artifact_relative_path="../outside.bin",
            serial_port="COM7",
        )
