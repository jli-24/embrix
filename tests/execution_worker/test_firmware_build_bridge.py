from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from embedded_copilot.application.models import ApplicationQuestionRequest
from embedded_copilot.execution_worker.firmware_build_bridge import (
    FirmwareBuildExecutionBridge,
)
from embedded_copilot.firmware_engineering import (
    FirmwareBuildConfiguration,
    FirmwareBuildRequest,
    FirmwareSourceArtifact,
)


class _Grant:
    grant_id = "approval-current-canonical-build"


class _Execution:
    def create_operation(self, request):
        self.created = request
        return request

    def issue_approval(self, **_kwargs):
        return _Grant()

    def enqueue(self, **_kwargs):
        return None


def _request(version: str = "UNAVAILABLE") -> FirmwareBuildRequest:
    configuration = FirmwareBuildConfiguration(
        target_kind="STM32F103C8T6",
        build_system="CMAKE",
        toolchain_id="ARM_NONE_EABI_GCC",
        toolchain_version=version,
        profile="Release",
        build_directory="build",
        expected_artifact_types=("ELF", "BIN", "MAP"),
        output_stem="minimal_mcu_control_core",
        compiler_flags=("-mcpu=cortex-m3", "-mthumb", "-mfloat-abi=soft"),
    )
    return FirmwareBuildRequest.create(
        project_id="project-current-canonical",
        conversation_id="conversation-current-canonical",
        firmware_reference="firmware-spec-current-canonical",
        build_profile=configuration.profile,
        approval_reference="user-approved-current-canonical-build",
        firmware_spec_id="firmware-spec-current-canonical",
        firmware_spec_fingerprint="sha256:" + "1" * 64,
        implementation_id="implementation-current-canonical",
        implementation_fingerprint="sha256:" + "2" * 64,
        target_kind=configuration.target_kind,
        build_system=configuration.build_system,
        toolchain_id=configuration.toolchain_id,
        toolchain_version=configuration.toolchain_version,
        configuration=configuration,
        source_artifacts=(
            FirmwareSourceArtifact(
                relative_path="generated/main.c",
                content_fingerprint="sha256:" + "3" * 64,
                size_bytes=1,
            ),
        ),
    )


def _application_request(project_id: str = "project-current-canonical") -> ApplicationQuestionRequest:
    return ApplicationQuestionRequest(
        request_id="application-request-current-canonical",
        trace_id="application-trace-current-canonical",
        project_id=project_id,
        tenant_id="tenant-current-canonical",
        requester="reviewer-current-canonical",
        question="Build the approved firmware.",
        requested_at=datetime.now(UTC),
    )


def _enqueue(
    request: FirmwareBuildRequest,
    *,
    resolved_version: str,
    application_request: ApplicationQuestionRequest | None = None,
):
    now = datetime.now(UTC)
    return FirmwareBuildExecutionBridge(_Execution()).approve_and_enqueue(
        request,
        tenant_id="tenant-current-canonical",
        requester_id="reviewer-current-canonical",
        target_id="target-current-canonical",
        target_fingerprint="sha256:" + "4" * 64,
        workspace_snapshot_id="snapshot-current-canonical",
        workspace_fingerprint="sha256:" + "5" * 64,
        proposal_id="proposal-current-canonical",
        proposal_fingerprint="sha256:" + "6" * 64,
        reviewer_id="reviewer-current-canonical",
        approval_nonce="approval-current-canonical",
        expires_at=now + timedelta(minutes=10),
        resolved_toolchain_version=resolved_version,
        application_request=application_request,
    )


def test_bridge_binds_discovered_version_without_changing_request_configuration() -> None:
    request = _request()

    operation = _enqueue(request, resolved_version="15.3.1").operation

    assert operation.toolchain.tool_version == "15.3.1"
    assert operation.firmware_build_request is not None
    assert operation.firmware_build_request.configuration == request.configuration
    assert operation.firmware_build_request.toolchain_version == "UNAVAILABLE"


def test_bridge_rejects_discovered_version_conflicting_with_configured_version() -> None:
    with pytest.raises(ValueError, match="conflicts with build configuration"):
        _enqueue(_request("14.2.0"), resolved_version="15.3.1")


def test_bridge_binds_application_request_correlation_to_real_operation() -> None:
    operation = _enqueue(
        _request(),
        resolved_version="15.3.1",
        application_request=_application_request(),
    ).operation

    assert operation.correlation is not None
    assert operation.correlation.request_id == "application-request-current-canonical"
    assert operation.correlation.trace_id == "application-trace-current-canonical"
    assert operation.correlation.project_id == operation.project_id
    assert operation.correlation.operation_id == operation.operation_id
    assert operation.correlation.workflow_run_id is None
    assert operation.correlation.approval_id is None


def test_bridge_rejects_application_request_from_another_project() -> None:
    with pytest.raises(ValueError, match="application request project"):
        _enqueue(
            _request(),
            resolved_version="15.3.1",
            application_request=_application_request("project-other"),
        )
