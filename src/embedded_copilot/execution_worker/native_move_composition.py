"""Explicit, offline composition for the reviewed native footprint move ports."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from embedded_copilot.engineering_workspace_apply import WorkspaceApplyService
from embedded_copilot.project_catalog import ProjectCatalogRuntime

from .adapters import SafeProcessRunner
from .artifacts import ArtifactStore
from .contracts import (
    KiCadNativeFootprintMoveOperation,
    NativeMoveDrcPolicy,
    NativeMoveReaderPolicy,
    NativeMoveSideEffectPolicy,
    OperationApprovalGrant,
)
from .drc_baseline import BaselineClosureFile
from .native_move import NativeMoveProductionSetup
from .native_move_drc_collector import NativeMoveDrcCollector
from .native_move_pcbnew_reader import NativePcbnewReader, NativeReadbackBundleV1
from .store import SQLiteExecutionStore


def build_native_move_production_setup(
    *,
    reader_policy: NativeMoveReaderPolicy,
    drc_policy: NativeMoveDrcPolicy,
    side_effect_policy: NativeMoveSideEffectPolicy,
    readback_bundle: NativeReadbackBundleV1,
    tool_bundle: tuple[BaselineClosureFile, ...],
    rules: tuple[BaselineClosureFile, ...],
    dependencies: tuple[BaselineClosureFile, ...],
    environment: tuple[tuple[str, str], ...],
    report_root: Path,
    store: SQLiteExecutionStore,
    catalog: ProjectCatalogRuntime,
    workspace: WorkspaceApplyService,
    artifact_store: ArtifactStore,
    runner: SafeProcessRunner,
    clock: Callable[[], datetime],
    validate_policies: Callable[
        [NativeMoveReaderPolicy, NativeMoveDrcPolicy, NativeMoveSideEffectPolicy], bool
    ],
    authorize_phase: Callable[
        [str, KiCadNativeFootprintMoveOperation, OperationApprovalGrant, datetime], bool
    ],
) -> NativeMoveProductionSetup:
    """Bind reviewed inputs to one canonical setup without launching native tools."""
    if (
        type(reader_policy) is not NativeMoveReaderPolicy
        or type(drc_policy) is not NativeMoveDrcPolicy
        or type(side_effect_policy) is not NativeMoveSideEffectPolicy
        or type(readback_bundle) is not NativeReadbackBundleV1
        or type(store) is not SQLiteExecutionStore
        or type(catalog) is not ProjectCatalogRuntime
        or type(workspace) is not WorkspaceApplyService
        or type(artifact_store) is not ArtifactStore
        or type(runner) is not SafeProcessRunner
        or not callable(clock)
        or not callable(validate_policies)
        or not callable(authorize_phase)
    ):
        raise ValueError("native move production inputs are unavailable")

    reader = NativePcbnewReader(
        bundle=readback_bundle,
        runner=runner,
        store=store,
        catalog=catalog,
        workspace=workspace,
        artifact_store=artifact_store,
        authorize_phase=authorize_phase,
        clock=clock,
    )
    drc = NativeMoveDrcCollector(
        workspace=workspace,
        artifact_store=artifact_store,
        store=store,
        catalog=catalog,
        authorize_phase=authorize_phase,
        tool_bundle=tool_bundle,
        rules=rules,
        dependencies=dependencies,
        environment=environment,
        report_root=report_root,
        runner=runner,
        clock=clock,
    )
    setup = NativeMoveProductionSetup(
        workspace=workspace,
        reader_policy=reader_policy,
        drc_policy=drc_policy,
        side_effect_policy=side_effect_policy,
        artifact_store=artifact_store,
        validate_policies=validate_policies,
        authorize_phase=authorize_phase,
        reader=reader,
        drc=drc,
    )
    if (
        not reader.ready_for(reader_policy)
        or not drc.ready_for(drc_policy)
        or not setup.available()
    ):
        raise ValueError("native move production composition is unavailable")
    return setup
