from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from embedded_copilot.engineering_pcb import (
    PCBConstraint,
    PCBConstraintSet,
    ReturnPathVerifier,
)
from embedded_copilot.project_catalog import (
    ConversationDetail,
    ProjectCatalogRuntime,
    ProjectCreationMode,
    ProjectInitializationStatus,
    ProjectType,
    ProjectView,
)
from tests.engineering_pcb import test_g7b2_return_path as fixture

from .owner_seed import seed_project_owner

NOW = datetime(2026, 9, 18, tzinfo=UTC)
ZERO = "sha256:" + "0" * 64


def _runtime(tmp_path: Path) -> ProjectCatalogRuntime:
    root = tmp_path / "projects"
    root.mkdir()
    runtime = ProjectCatalogRuntime(
        project_root=root,
        state_database=tmp_path / "catalog.sqlite3",
        initialize_async=False,
    )
    runtime._repository.save_project(
        ProjectView(
            project_id="g7b2-project",
            tenant_id="g7b2-tenant",
            name="G7-B2",
            location_id="g7b2-location",
            directory_name="g7b2",
            creation_mode=ProjectCreationMode.CREATE_NEW,
            relative_path="g7b2",
            project_type=ProjectType.GENERIC_FIRMWARE,
            initialization_status=ProjectInitializationStatus.READY,
            created_at=NOW,
            updated_at=NOW,
            last_activity_at=NOW,
        )
    )
    seed_project_owner(tmp_path / "catalog.sqlite3", "g7b2-project", "g7b2-engineer")
    return runtime


def _detail(tmp_path: Path) -> tuple[ProjectCatalogRuntime, ConversationDetail, object]:
    state, constraints, _, evidence = fixture._base()
    gnd = next(item.net_id for item in state.nets if item.name == "GND")
    state = fixture._with_planes(
        state,
        (
            fixture._plane(
                state,
                evidence,
                net_id=gnd,
                polygons=(fixture._polygon("canonical", ((0.0, 0.0), (10.0, 0.0), (10.0, 2.0), (0.0, 2.0))),),
            ),
        ),
    )
    constraint = PCBConstraint.create(
        **{
            **constraints.constraints[0].model_dump(mode="python", exclude={"fingerprint"}),
            "revision": state.revision,
        }
    )
    constraints = PCBConstraintSet.create(
        project_id=state.project_id,
        revision=state.revision,
        state_fingerprint=state.fingerprint,
        constraints=(constraint,),
        evidence_refs=(evidence,),
    )
    plan = __import__("embedded_copilot.engineering_pcb", fromlist=["PCBConstraintCompiler"]).PCBConstraintCompiler().build_plan(state, constraints)
    detailed = ReturnPathVerifier().verify_detailed(state, constraints, plan)
    detail = ConversationDetail(
        conversation_id="g7b2-conversation",
        project_id="g7b2-project",
        tenant_id="g7b2-tenant",
        requester="g7b2-engineer",
        title="G7-B2 return path",
        turns=(),
        created_at=NOW,
        updated_at=NOW,
        pcb_engineering_state=state,
        pcb_constraint_set=constraints,
        pcb_verification_plan=plan,
        pcb_return_path_verification=detailed.result,
        pcb_return_path_measurements=detailed.measurements,
        pcb_return_path_relationships=detailed.relationships,
        pcb_return_path_discontinuities=detailed.discontinuities,
        pcb_return_path_transitions=detailed.transitions,
    )
    return _runtime(tmp_path), detail, detailed


def test_return_path_result_relationships_measurements_and_restart_survive(tmp_path: Path) -> None:
    runtime, detail, detailed = _detail(tmp_path)
    stored = runtime.persist_conversation_detail(detail)
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
    assert restored.pcb_return_path_verification is not None
    assert restored.pcb_return_path_verification.fingerprint == detailed.result.fingerprint
    assert restored.pcb_return_path_measurements
    assert restored.pcb_return_path_relationships
    assert restored.pcb_return_path_discontinuities == ()
    assert restored.pcb_return_path_transitions == ()


def test_return_path_result_is_marked_stale_when_g7a_inputs_disappear(tmp_path: Path) -> None:
    runtime, detail, _ = _detail(tmp_path)
    stored = runtime.persist_conversation_detail(detail)
    incoming = stored.model_copy(
        update={
            "pcb_engineering_state": None,
            "pcb_constraint_set": None,
            "pcb_verification_plan": None,
            "pcb_return_path_verification": None,
            "fingerprint": ZERO,
        }
    )
    stale = runtime.persist_conversation_detail(incoming)
    assert stale.pcb_return_path_verification is not None
    assert stale.pcb_return_path_verification.stale is True
    assert stale.pcb_return_path_verification.status.value == "UNKNOWN"
    assert "STALE_INPUTS" in stale.pcb_return_path_verification.unknowns
