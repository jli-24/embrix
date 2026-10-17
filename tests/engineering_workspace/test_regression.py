from __future__ import annotations

from datetime import UTC, datetime, timedelta

from embedded_copilot.engineering_context import (
    ContextSourceType,
    EngineeringContextEvidence,
    EngineeringContextSnapshot,
)
from embedded_copilot.engineering_workspace import (
    EngineeringWorkspaceRequest,
    EngineeringWorkspaceService,
    WorkspaceDomain,
    WorkspaceObjectKind,
)
from embedded_copilot.engineering_workspace.adapters import (
    FakeWorkspaceReferenceSourceAdapter,
    FixedWorkspaceClock,
)

from .conftest import PROJECT, reference, resolved


def test_workspace_build_does_not_change_existing_context_snapshot_identity() -> None:
    timestamp = datetime(2026, 8, 11, tzinfo=UTC)
    evidence = EngineeringContextEvidence.create(
        evidence_id="workspace-regression-evidence",
        project_id=PROJECT,
        source_type=ContextSourceType.ENGINEERING_MEMORY,
        subject="workspace identity",
        summary="Existing context remains immutable.",
        fact_key="workspace.regression",
        fact_value="stable",
        source_reference="memory:workspace-regression",
        source_fingerprint="sha256:" + "8" * 64,
        confidence=0.9,
        verification_status="APPROVED",
    )
    snapshot = EngineeringContextSnapshot.create(
        project_id=PROJECT,
        task_description="workspace regression",
        memory_evidence=(evidence,),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.9,
        provenance=(evidence.source(),),
        verification_status="APPROVED",
    )
    before = snapshot.fingerprint
    value = reference(
        domain=WorkspaceDomain.ENGINEERING_CONTEXT,
        kind=WorkspaceObjectKind.ANALYSIS,
        object_id="context-snapshot-1",
        source_fingerprint=snapshot.fingerprint,
        occurred_at=timestamp,
    )
    service = EngineeringWorkspaceService(
        source=FakeWorkspaceReferenceSourceAdapter((resolved(value),)),
        clock=FixedWorkspaceClock(timestamp + timedelta(seconds=1)),
    )
    service.build_snapshot(
        EngineeringWorkspaceRequest(
            workspace_id="workspace-1", project_id=PROJECT, references=(value,)
        )
    )
    assert snapshot.fingerprint == before
