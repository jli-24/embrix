from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from embedded_copilot.application import ApplicationQuestionRequest
from embedded_copilot.application.desktop_composition import (
    build_project_first_chat_runtime,
)
from embedded_copilot.desktop_copilot_chat import DesktopCopilotChatRuntime
from embedded_copilot.execution_worker import (
    ExecutionOperationService,
    ExecutionTarget,
    SQLiteExecutionStore,
    TargetBindingStatus,
)
from embedded_copilot.firmware_engineering import (
    FirmwareBuildConfiguration,
    FirmwareBuildRequest,
    FirmwareSourceArtifact,
)
from embedded_copilot.multimodal.context import ProcessLocalAttachmentBindingRepository
from embedded_copilot.project_catalog import (
    ProjectCatalogRuntime,
    ProjectCreationMode,
    ProjectCreationRequest,
    ProjectLocationReference,
    ProjectType,
)


NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
TENANT = "tenant-phase2"
REQUESTER = "engineer-phase2"


def _catalog(tmp_path: Path) -> ProjectCatalogRuntime:
    (tmp_path / "projects").mkdir()
    return ProjectCatalogRuntime(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "state" / "desktop.sqlite3",
        metadata_directory=tmp_path / "metadata",
        initialize_async=False,
    )


def _project(catalog: ProjectCatalogRuntime):
    location = catalog.locations(tenant_id=TENANT, requester=REQUESTER)[0]
    preview = catalog.preview(
        ProjectCreationRequest(
            name="Phase 2 Wiring Project",
            project_type=ProjectType.STM32,
            creation_mode=ProjectCreationMode.CREATE_NEW,
            parent_location=ProjectLocationReference(
                location_id=location.location_id,
                fingerprint=location.fingerprint,
            ),
        ),
        tenant_id=TENANT,
        requester=REQUESTER,
    )
    return catalog.create(
        preview.preview_id,
        preview.fingerprint,
        confirmed_at=preview.created_at,
        tenant_id=TENANT,
        requester=REQUESTER,
    )


def _build_request(project_id: str) -> FirmwareBuildRequest:
    configuration = FirmwareBuildConfiguration(
        target_kind="STM32F103C8T6",
        build_system="CMAKE",
        toolchain_id="ARM_NONE_EABI_GCC",
        toolchain_version="UNAVAILABLE",
        profile="Release",
        build_directory="build",
        expected_artifact_types=("ELF",),
        output_stem="phase2-firmware",
    )
    return FirmwareBuildRequest.create(
        project_id=project_id,
        conversation_id="conversation-phase2",
        firmware_reference="firmware-spec-phase2",
        build_profile=configuration.profile,
        approval_reference=None,
        firmware_spec_id="firmware-spec-phase2",
        firmware_spec_fingerprint="sha256:" + "1" * 64,
        implementation_id="implementation-phase2",
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


def _application_request(project_id: str) -> ApplicationQuestionRequest:
    return ApplicationQuestionRequest(
        request_id="application-request-phase2",
        trace_id="application-trace-phase2",
        project_id=project_id,
        tenant_id=TENANT,
        requester=REQUESTER,
        question="编译固件",
        requested_at=NOW,
    )


def test_legacy_application_bridge_rejects_build_without_persisted_proposal(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    project = _project(catalog)
    store = SQLiteExecutionStore(tmp_path / "state" / "execution.sqlite3")
    execution = ExecutionOperationService(store=store)
    runtime = build_project_first_chat_runtime(
        database=tmp_path / "state" / "desktop.sqlite3",
        project_catalog=catalog,
        approved_memory_store=None,
        environment=None,
        attachments=ProcessLocalAttachmentBindingRepository(),
        execution_port=execution,
    )
    assert isinstance(runtime, DesktopCopilotChatRuntime)

    target = ExecutionTarget(
        target_id="target-phase2",
        tenant_id=TENANT,
        project_id=project.project_id,
        target_kind="STM32F103C8T6",
        board_family="BLUEPILL",
        mcu_family="STM32F103",
        stable_identity_hash="stable-target-phase2",
        candidate_id="candidate-phase2",
        binding_status=TargetBindingStatus.BOUND,
        bound_by=REQUESTER,
        bound_at=NOW,
    )
    # The legacy direct bridge has no persisted Proposal for this request.
    # It must fail before an Operation or synthetic Build receipt is created.
    with pytest.raises(Exception, match="proposal not found"):
        runtime.application_runtime.create_firmware_build_operation(
            _application_request(project.project_id),
            _build_request(project.project_id),
            target_id=target.target_id,
            target_fingerprint=target.fingerprint,
            workspace_snapshot_id="snapshot-phase2",
            workspace_fingerprint="sha256:" + "4" * 64,
            proposal_id="proposal-phase2",
            proposal_fingerprint="sha256:" + "5" * 64,
            approval_nonce="nonce-phase2",
            expires_at=NOW + timedelta(minutes=10),
        )
    assert store.list_operations(TENANT, project.project_id) == ()


def test_application_wiring_does_not_derive_request_id_from_trace_id(
    tmp_path: Path,
) -> None:
    catalog = _catalog(tmp_path)
    project = _project(catalog)
    execution = ExecutionOperationService(
        store=SQLiteExecutionStore(tmp_path / "state" / "execution.sqlite3")
    )
    runtime = build_project_first_chat_runtime(
        database=tmp_path / "state" / "desktop.sqlite3",
        project_catalog=catalog,
        approved_memory_store=None,
        environment=None,
        attachments=ProcessLocalAttachmentBindingRepository(),
        execution_port=execution,
    )
    assert runtime is not None
    malformed = ApplicationQuestionRequest.model_construct(
        trace_id="trace-without-request",
        project_id=project.project_id,
        tenant_id=TENANT,
        requester=REQUESTER,
        question="编译固件",
        requested_at=NOW,
    )

    with pytest.raises((TypeError, ValueError)):
        runtime.application_runtime.create_firmware_build_operation(
            malformed,
            _build_request(project.project_id),
            target_id="target-phase2",
            target_fingerprint="sha256:" + "4" * 64,
            workspace_snapshot_id="snapshot-phase2",
            workspace_fingerprint="sha256:" + "5" * 64,
            proposal_id="proposal-phase2",
            proposal_fingerprint="sha256:" + "6" * 64,
            approval_nonce="nonce-phase2",
            expires_at=NOW + timedelta(minutes=10),
        )

