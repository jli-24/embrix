from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from embedded_copilot.engineering_pcb import (
    PCBApplicability,
    PCBArtifactReference,
    PCBArtifactType,
    PCBConstraintCompiler,
    PCBEngineeringProjector,
    PCBFoundationEvidence,
    PCBFoundationEvidenceSource,
    PCBSourceFormat,
)
from embedded_copilot.pcb.models import (
    PCBLayer,
    PCBNet,
    PCBNetNode,
    PCBPosition,
    PCBTrack,
    UnifiedPCBModel,
)
from embedded_copilot.project_catalog import (
    ConversationDetail,
    ProjectCatalogRuntime,
    ProjectCreationMode,
    ProjectInitializationStatus,
    ProjectType,
    ProjectView,
)

from .owner_seed import seed_project_owner

NOW = datetime(2026, 9, 18, tzinfo=UTC)


def _foundation() -> tuple[object, object, object]:
    artifact = PCBArtifactReference.create(
        project_id="project-g7a-persist",
        artifact_id="artifact-g7a-persist",
        artifact_type=PCBArtifactType.BOARD,
        source_format=PCBSourceFormat.KICAD_PCB,
        source_reference="artifact://artifact-g7a-persist",
        filename="main.kicad_pcb",
        byte_size=128,
        content_fingerprint="sha256:" + "1" * 64,
        captured_at=NOW,
    )
    evidence = PCBFoundationEvidence.create(
        evidence_id="evidence-g7a-persist",
        project_id=artifact.project_id,
        source_type=PCBFoundationEvidenceSource.ARTIFACT,
        source_id=artifact.artifact_id,
        source_fingerprint=artifact.content_fingerprint,
        claim="Observed from the current G5 PCB artifact.",
        locator="artifact://artifact-g7a-persist",
    )
    parsed = UnifiedPCBModel(
        board_name="main",
        source_format="kicad_pcb",
        layers=(PCBLayer(name="F.Cu", index=0, type="signal"),),
        nets=(PCBNet(name="CTRL", net_type="signal", nodes=(PCBNetNode(reference="U1", pin="1"),)),),
        tracks=(PCBTrack(start=PCBPosition(x_mm=1.0, y_mm=1.0), end=PCBPosition(x_mm=2.0, y_mm=1.0), width_mm=0.2, layer="F.Cu", net_name="CTRL"),),
    )
    state = PCBEngineeringProjector().project(
        project_id=artifact.project_id,
        revision=artifact.content_fingerprint,
        source_artifact=artifact,
        parsed=parsed,
        evidence_refs=(evidence,),
    )
    constraints = PCBConstraintCompiler().compile(state)
    plan = PCBConstraintCompiler().build_plan(state, constraints)
    assert any(item.applicability is PCBApplicability.REQUIRED for item in constraints.constraints)
    return state, constraints, plan


def _detail(*, state=None, constraints=None, plan=None) -> ConversationDetail:
    return ConversationDetail(
        conversation_id="conversation-g7a-persist",
        project_id="project-g7a-persist",
        tenant_id="tenant-g7a-persist",
        requester="engineer-g7a-persist",
        title="G7-A persistence",
        turns=(),
        created_at=NOW,
        updated_at=NOW,
        pcb_engineering_state=state,
        pcb_constraint_set=constraints,
        pcb_verification_plan=plan,
    )


def _runtime(tmp_path: Path) -> ProjectCatalogRuntime:
    root = tmp_path / "projects"
    root.mkdir(exist_ok=True)
    runtime = ProjectCatalogRuntime(
        project_root=root,
        state_database=tmp_path / "catalog.sqlite3",
        initialize_async=False,
    )
    runtime._repository.save_project(
        ProjectView(
            project_id="project-g7a-persist",
            tenant_id="tenant-g7a-persist",
            name="G7-A",
            location_id="location-g7a",
            directory_name="g7a",
            creation_mode=ProjectCreationMode.CREATE_NEW,
            relative_path="g7a",
            project_type=ProjectType.GENERIC_FIRMWARE,
            initialization_status=ProjectInitializationStatus.READY,
            created_at=NOW,
            updated_at=NOW,
            last_activity_at=NOW,
        )
    )
    seed_project_owner(tmp_path / "catalog.sqlite3", "project-g7a-persist", "engineer-g7a-persist")
    return runtime


def test_g7a_state_constraint_plan_survive_restart(tmp_path: Path) -> None:
    state, constraints, plan = _foundation()
    runtime = _runtime(tmp_path)
    stored = runtime.persist_conversation_detail(_detail(state=state, constraints=constraints, plan=plan))

    restarted = ProjectCatalogRuntime(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "catalog.sqlite3",
        initialize_async=False,
    )
    restored = restarted.conversation(
        stored.project_id,
        stored.conversation_id,
        tenant_id=stored.tenant_id,
        requester=stored.requester,
    )

    assert restored.pcb_engineering_state is not None
    assert restored.pcb_constraint_set is not None
    assert restored.pcb_verification_plan is not None
    assert restored.pcb_engineering_state.fingerprint == state.fingerprint
    assert restored.pcb_constraint_set.fingerprint == constraints.fingerprint
    assert restored.pcb_verification_plan.fingerprint == plan.fingerprint


def test_g7a_foundation_is_selectively_marked_stale_when_context_is_missing(tmp_path: Path) -> None:
    state, constraints, plan = _foundation()
    runtime = _runtime(tmp_path)
    stored = runtime.persist_conversation_detail(_detail(state=state, constraints=constraints, plan=plan))
    incoming = stored.model_copy(
        update={
            "pcb_engineering_state": None,
            "pcb_constraint_set": None,
            "pcb_verification_plan": None,
            "fingerprint": "sha256:" + "0" * 64,
        }
    )

    stale = runtime.persist_conversation_detail(incoming)

    assert stale.pcb_engineering_state is not None
    assert stale.pcb_constraint_set is not None
    assert stale.pcb_verification_plan is not None
    assert stale.pcb_engineering_state.status.value == "STALE"
    assert stale.pcb_constraint_set.status.value == "STALE"
    assert stale.pcb_verification_plan.status.value == "STALE"
    assert stale.pcb_engineering_state.stale_reason
    assert "reproject_pcb_engineering_state" in stale.pcb_engineering_state.required_reverification


def test_g7a_cross_project_binding_is_rejected(tmp_path: Path) -> None:
    state, constraints, plan = _foundation()
    with pytest.raises(ValueError, match="PCB engineering state project mismatch"):
        ConversationDetail.model_validate(
            _detail(state=state, constraints=constraints, plan=plan)
            .model_copy(update={"project_id": "different-project", "fingerprint": "sha256:" + "0" * 64})
            .model_dump(mode="python")
        )
