from __future__ import annotations

from datetime import UTC, datetime

from embedded_copilot.engineering_workspace import (
    WorkspaceDomain,
    WorkspaceLifecycleStatus,
    WorkspaceLineageLink,
    WorkspaceObjectKind,
    WorkspaceObjectReference,
    WorkspaceRelationType,
    WorkspaceResolvedReference,
)

NOW = datetime(2026, 8, 11, 10, 0, tzinfo=UTC)
PROJECT = "project-1"


def reference(
    *,
    domain: WorkspaceDomain = WorkspaceDomain.PCB,
    kind: WorkspaceObjectKind = WorkspaceObjectKind.ANALYSIS,
    object_id: str = "pcb-analysis-1",
    source_fingerprint: str = "sha256:" + "1" * 64,
    occurred_at: datetime = NOW,
    status: WorkspaceLifecycleStatus = WorkspaceLifecycleStatus.COMPLETED,
) -> WorkspaceObjectReference:
    return WorkspaceObjectReference(
        project_id=PROJECT,
        source_domain=domain,
        source_kind=kind,
        source_object_id=object_id,
        source_fingerprint=source_fingerprint,
        occurred_at=occurred_at,
        status=status,
    )


def resolved(
    value: WorkspaceObjectReference | None = None,
) -> WorkspaceResolvedReference:
    checked = value or reference()
    return WorkspaceResolvedReference(
        reference=checked,
        lineage=(
            WorkspaceLineageLink(
                source_domain=WorkspaceDomain.ENGINEERING_CONTEXT,
                source_kind=WorkspaceObjectKind.ANALYSIS,
                source_object_id="context-analysis-1",
                source_fingerprint="sha256:" + "2" * 64,
                relation_type=WorkspaceRelationType.DERIVED_FROM,
            ),
        ),
    )
