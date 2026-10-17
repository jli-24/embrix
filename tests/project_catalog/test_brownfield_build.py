from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from embedded_copilot.engineering_copilot import PlanReviewStatus, PlanStatus
from embedded_copilot.engineering_workspace_apply import (
    ProjectCatalogWorkspaceTargetResolver,
    SQLiteWorkspaceApplyStore,
    SystemWorkspaceClock,
    WorkspaceApplyService,
)
from embedded_copilot.firmware_engineering import FirmwareBuildConfiguration
from embedded_copilot.project_catalog import (
    ProjectCatalogConflict,
    ProjectCatalogForbidden,
    ProjectCatalogRejected,
    ProjectCatalogRuntime,
    ProjectCreationMode,
    ProjectCreationRequest,
    ProjectDirectoryReference,
    ProjectLocationReference,
    ProjectType,
)

NOW = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)


class Clock:
    def now(self) -> datetime:
        return NOW


def attached(tmp_path: Path):
    root = tmp_path / "projects"
    source = root / "firmware"
    source.mkdir(parents=True)
    for path in (
        "CMakeLists.txt", "cmake/arm-none-eabi.cmake", "src/main.c",
        "startup.s", "system.c",
    ):
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("source\n", encoding="utf-8")
    fixture_time_ns = int(NOW.timestamp()) * 1_000_000_000
    os.utime(source, ns=(fixture_time_ns, fixture_time_ns))
    catalog = ProjectCatalogRuntime(
        project_root=root, state_database=tmp_path / "catalog.sqlite3",
        clock=Clock(), initialize_async=False,
    )
    workspace = WorkspaceApplyService(
        resolver=ProjectCatalogWorkspaceTargetResolver(catalog),
        store=SQLiteWorkspaceApplyStore(tmp_path / "workspace.sqlite3"),
        state_root=tmp_path / "workspace-state",
        clock=SystemWorkspaceClock(),
    )
    catalog.set_workspace_snapshot_source(workspace)
    location = catalog.locations(tenant_id="tenant-1", requester="engineer-1")[0]
    directory = catalog.directories(location.location_id, tenant_id="tenant-1", requester="engineer-1").items[0]
    preview = catalog.preview(
        ProjectCreationRequest(
            name="Existing Firmware", project_type=ProjectType.STM32,
            creation_mode=ProjectCreationMode.ATTACH_EXISTING,
            parent_location=ProjectLocationReference(location_id=location.location_id, fingerprint=location.fingerprint),
            existing_directory=ProjectDirectoryReference(directory_id=directory.directory_id, fingerprint=directory.fingerprint),
        ), tenant_id="tenant-1", requester="engineer-1",
    )
    project = catalog.create(
        preview.preview_id, preview.fingerprint, confirmed_at=NOW,
        tenant_id="tenant-1", requester="engineer-1",
    )
    configuration = FirmwareBuildConfiguration(
        target_kind="STM32F103C8T6", build_system="CMAKE",
        toolchain_id="arm-none-eabi-gcc", toolchain_version="1.0", profile="DEBUG",
        build_directory="build", cmake_project="CMakeLists.txt",
        cmake_toolchain_file="cmake/arm-none-eabi.cmake",
        startup_source="startup.s", system_source="system.c",
    )
    return catalog, workspace, project, source, configuration


def propose(catalog, project, configuration, workspace_fingerprint):
    return catalog.propose_brownfield_build(
        project.project_id, tenant_id="tenant-1", requester="engineer-1",
        workspace_fingerprint=workspace_fingerprint,
        target_platform="STM32", mcu_family="STM32F1",
        mcu_exact_part="STM32F103C8T6", source_layout=("src/main.c",),
        configuration=configuration,
    )


def test_brownfield_proposal_review_and_restart_read_use_catalog_and_workspace_owners(tmp_path: Path) -> None:
    catalog, workspace, project, source, configuration = attached(tmp_path)
    snapshot = workspace.snapshot(tenant_id="tenant-1", project_id=project.project_id)
    context = propose(catalog, project, configuration, snapshot.project_fingerprint)
    assert context.plan.status is PlanStatus.PROPOSED
    assert context.review is None
    assert context.firmware_spec.source_revision.startswith("sha256:")
    assert context.implementation_spec.build_configuration_fingerprint
    assert (source / "src/main.c").read_text(encoding="utf-8") == "source\n"

    approved = catalog.decide_brownfield_build(
        project.project_id, tenant_id="tenant-1", requester="engineer-1",
        proposal_fingerprint=context.plan.content_fingerprint, decision=PlanReviewStatus.APPROVED,
    )
    assert approved.plan.status is PlanStatus.APPROVED
    assert approved.review.status is PlanReviewStatus.APPROVED
    restarted = ProjectCatalogRuntime(
        project_root=tmp_path / "projects", state_database=tmp_path / "catalog.sqlite3",
        clock=Clock(), initialize_async=False,
    )
    restarted.set_workspace_snapshot_source(workspace)
    assert restarted.brownfield_build_context(
        project.project_id, tenant_id="tenant-1", requester="engineer-1"
    ) == approved


def test_brownfield_review_rejects_workspace_change_and_wrong_proposal(tmp_path: Path) -> None:
    catalog, workspace, project, source, configuration = attached(tmp_path)
    snapshot = workspace.snapshot(tenant_id="tenant-1", project_id=project.project_id)
    context = propose(catalog, project, configuration, snapshot.project_fingerprint)
    with pytest.raises(ProjectCatalogConflict):
        catalog.decide_brownfield_build(
            project.project_id, tenant_id="tenant-1", requester="engineer-1",
            proposal_fingerprint="sha256:" + "a" * 64, decision=PlanReviewStatus.APPROVED,
        )
    (source / "src/main.c").write_text("changed\n", encoding="utf-8")
    with pytest.raises(ProjectCatalogConflict):
        catalog.decide_brownfield_build(
            project.project_id, tenant_id="tenant-1", requester="engineer-1",
            proposal_fingerprint=context.plan.content_fingerprint, decision=PlanReviewStatus.APPROVED,
        )
    with pytest.raises(ProjectCatalogConflict):
        catalog.brownfield_build_context(
            project.project_id, tenant_id="tenant-1", requester="engineer-1"
        )


def test_brownfield_proposal_rejects_unscanned_source_and_unconfirmed_target(tmp_path: Path) -> None:
    catalog, workspace, project, _, configuration = attached(tmp_path)
    snapshot = workspace.snapshot(tenant_id="tenant-1", project_id=project.project_id)
    with pytest.raises(ProjectCatalogConflict):
        propose(catalog, project, configuration, "sha256:" + "a" * 64)
    with pytest.raises(ProjectCatalogRejected):
        catalog.propose_brownfield_build(
            project.project_id, tenant_id="tenant-1", requester="engineer-1",
            workspace_fingerprint=snapshot.project_fingerprint,
            target_platform="STM32", mcu_family="STM32F4", mcu_exact_part="STM32F407VG",
            source_layout=("src/main.c",), configuration=configuration,
        )


def test_brownfield_new_configuration_replaces_old_review_and_owner_is_required(tmp_path: Path) -> None:
    catalog, workspace, project, _, configuration = attached(tmp_path)
    snapshot = workspace.snapshot(tenant_id="tenant-1", project_id=project.project_id)
    first = propose(catalog, project, configuration, snapshot.project_fingerprint)
    catalog.decide_brownfield_build(
        project.project_id, tenant_id="tenant-1", requester="engineer-1",
        proposal_fingerprint=first.plan.content_fingerprint, decision=PlanReviewStatus.APPROVED,
    )
    changed = configuration.model_copy(update={"profile": "RELEASE"})
    second = propose(catalog, project, changed, snapshot.project_fingerprint)
    assert second.review is None
    assert second.configuration.profile == "RELEASE"
    assert second.implementation_spec.build_configuration_fingerprint != first.implementation_spec.build_configuration_fingerprint
    with pytest.raises(ProjectCatalogConflict):
        catalog.decide_brownfield_build(
            project.project_id, tenant_id="tenant-1", requester="engineer-1",
            proposal_fingerprint=first.plan.content_fingerprint, decision=PlanReviewStatus.APPROVED,
        )
    with pytest.raises(ProjectCatalogForbidden):
        catalog.brownfield_build_context(
            project.project_id, tenant_id="tenant-1", requester="other-engineer",
        )
