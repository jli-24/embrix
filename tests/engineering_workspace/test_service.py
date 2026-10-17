from __future__ import annotations

from datetime import timedelta

import pytest

from embedded_copilot.engineering_workspace import (
    EngineeringWorkspaceRequest,
    EngineeringWorkspaceService,
    WorkspaceConflict,
    WorkspaceDomain,
    WorkspaceObjectKind,
    WorkspaceRejected,
    WorkspaceUnavailable,
)
from embedded_copilot.engineering_workspace.adapters import (
    FakeWorkspaceReferenceSourceAdapter,
    FixedWorkspaceClock,
)

from .conftest import NOW, PROJECT, reference, resolved


def _service(*items):
    return EngineeringWorkspaceService(
        source=FakeWorkspaceReferenceSourceAdapter(tuple(items)),
        clock=FixedWorkspaceClock(NOW + timedelta(minutes=1)),
    )


def test_workspace_snapshot_keeps_full_reference_identity_and_sorted_timeline() -> None:
    later = reference(
        domain=WorkspaceDomain.OPTIMIZATION,
        kind=WorkspaceObjectKind.PROPOSAL,
        object_id="optimization-proposal-1",
        source_fingerprint="sha256:" + "3" * 64,
        occurred_at=NOW,
    )
    earlier = reference(
        domain=WorkspaceDomain.PCB,
        kind=WorkspaceObjectKind.ANALYSIS,
        object_id="pcb-analysis-1",
        source_fingerprint="sha256:" + "4" * 64,
        occurred_at=NOW - timedelta(seconds=1),
    )
    service = _service(resolved(later), resolved(earlier))
    snapshot = service.build_snapshot(
        EngineeringWorkspaceRequest(
            workspace_id="workspace-1",
            project_id=PROJECT,
            references=(later, earlier),
        )
    )
    assert tuple(item.reference for item in snapshot.timeline) == (earlier, later)
    assert snapshot.source_references == (earlier, later)
    assert snapshot.timeline[0].lineage == resolved(earlier).lineage
    assert snapshot.generated_at > snapshot.timeline[-1].reference.occurred_at


def test_same_identity_with_different_source_fingerprint_fails_closed() -> None:
    requested = reference(source_fingerprint="sha256:" + "5" * 64)
    resolved_value = reference(source_fingerprint="sha256:" + "6" * 64)
    service = _service(resolved(resolved_value))
    with pytest.raises(WorkspaceConflict):
        service.build_snapshot(
            EngineeringWorkspaceRequest(
                workspace_id="workspace-1",
                project_id=PROJECT,
                references=(requested,),
            )
        )


def test_snapshot_identity_changes_when_a_source_fingerprint_changes() -> None:
    first = reference(source_fingerprint="sha256:" + "8" * 64)
    second = reference(source_fingerprint="sha256:" + "9" * 64)
    first_snapshot = _service(resolved(first)).build_snapshot(
        EngineeringWorkspaceRequest(
            workspace_id="workspace-1", project_id=PROJECT, references=(first,)
        )
    )
    second_snapshot = _service(resolved(second)).build_snapshot(
        EngineeringWorkspaceRequest(
            workspace_id="workspace-1", project_id=PROJECT, references=(second,)
        )
    )
    assert first_snapshot.fingerprint != second_snapshot.fingerprint
    assert first_snapshot.timeline[0].reference.source_fingerprint != (
        second_snapshot.timeline[0].reference.source_fingerprint
    )


def test_missing_or_wrong_identity_reference_is_rejected() -> None:
    requested = reference()
    wrong = reference(
        domain=WorkspaceDomain.EDA,
        object_id="eda-analysis-1",
        source_fingerprint="sha256:" + "7" * 64,
    )
    service = _service(resolved(wrong))
    with pytest.raises(WorkspaceRejected):
        service.build_snapshot(
            EngineeringWorkspaceRequest(
                workspace_id="workspace-1",
                project_id=PROJECT,
                references=(requested,),
            )
        )


def test_source_failure_is_sanitized_and_fails_closed() -> None:
    value = reference()
    source = FakeWorkspaceReferenceSourceAdapter((resolved(value),))
    source.fail = True
    service = EngineeringWorkspaceService(
        source=source, clock=FixedWorkspaceClock(NOW + timedelta(minutes=1))
    )
    with pytest.raises(WorkspaceUnavailable):
        service.build_snapshot(
            EngineeringWorkspaceRequest(
                workspace_id="workspace-1", project_id=PROJECT, references=(value,)
            )
        )
