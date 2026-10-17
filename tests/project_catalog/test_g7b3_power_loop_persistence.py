from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from embedded_copilot.engineering_pcb import PowerLayoutCandidate
from embedded_copilot.project_catalog import (
    ConversationDetail,
    ProjectCatalogRejected,
    ProjectCatalogRuntime,
    ProjectCreationMode,
    ProjectInitializationStatus,
    ProjectType,
    ProjectView,
)
from tests.engineering_pcb import test_g7b3_power_loop as fixture

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
            name="G7-B3",
            location_id="g7b2-location",
            directory_name="g7b3",
            creation_mode=ProjectCreationMode.CREATE_NEW,
            relative_path="g7b3",
            project_type=ProjectType.GENERIC_FIRMWARE,
            initialization_status=ProjectInitializationStatus.READY,
            created_at=NOW,
            updated_at=NOW,
            last_activity_at=NOW,
        )
    )
    seed_project_owner(tmp_path / "catalog.sqlite3", "g7b2-project", "g7b3-engineer")
    return runtime


def _detail() -> ConversationDetail:
    state, constraints, plan, _ = fixture._fixture()
    return ConversationDetail(
        conversation_id="g7b3-conversation",
        project_id="g7b2-project",
        tenant_id="g7b2-tenant",
        requester="g7b3-engineer",
        title="G7-B3 power loop",
        turns=(),
        created_at=NOW,
        updated_at=NOW,
        pcb_engineering_state=state,
        pcb_constraint_set=constraints,
        pcb_verification_plan=plan,
    )


def test_power_loop_runtime_persists_relations_measurements_and_restart(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    stored = runtime.persist_conversation_detail(_detail())
    verified = runtime.verify_pcb_power_loop(
        stored.project_id,
        stored.conversation_id,
        tenant_id=stored.tenant_id,
        requester=stored.requester,
    )
    assert verified.pcb_power_loop_verification is not None
    assert verified.pcb_power_loop_verification.status.value == "PASS"
    assert verified.pcb_power_stage_relationships
    assert verified.pcb_power_loop_relationships
    assert verified.pcb_power_loop_measurements
    assert verified.pcb_power_switch_nodes
    assert verified.pcb_power_feedback_relationships

    restarted = ProjectCatalogRuntime(
        project_root=tmp_path / "projects",
        state_database=tmp_path / "catalog.sqlite3",
        initialize_async=False,
    )
    restored = restarted.conversation(
        verified.project_id,
        verified.conversation_id,
        tenant_id=verified.tenant_id,
        requester=verified.requester,
    )
    assert restored.pcb_power_loop_verification is not None
    assert restored.pcb_power_loop_verification.fingerprint == verified.pcb_power_loop_verification.fingerprint
    assert restored.pcb_power_loop_measurements
    assert restored.pcb_power_stage_relationships


def test_power_loop_prevent_is_read_only_and_rejects_stale_candidate(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    stored = runtime.persist_conversation_detail(_detail())
    before = runtime.conversation(
        stored.project_id,
        stored.conversation_id,
        tenant_id=stored.tenant_id,
        requester=stored.requester,
    )
    evaluation = runtime.evaluate_pcb_power_loop_prevent(
        stored.project_id,
        stored.conversation_id,
        tenant_id=stored.tenant_id,
        requester=stored.requester,
        candidate=PowerLayoutCandidate.create(
            candidate_id="runtime-valid",
            base_state_fingerprint=before.pcb_engineering_state.fingerprint,
            route_segments=before.pcb_engineering_state.routing,
        ),
    )
    assert evaluation.status.value == "ALLOW"
    after = runtime.conversation(
        stored.project_id,
        stored.conversation_id,
        tenant_id=stored.tenant_id,
        requester=stored.requester,
    )
    assert after.fingerprint == before.fingerprint
    assert after.pcb_power_loop_verification is None

    stale_candidate = PowerLayoutCandidate.create(
        candidate_id="runtime-stale",
        base_state_fingerprint=ZERO,
        route_segments=before.pcb_engineering_state.routing,
    )
    try:
        runtime.evaluate_pcb_power_loop_prevent(
            stored.project_id,
            stored.conversation_id,
            tenant_id=stored.tenant_id,
            requester=stored.requester,
            candidate=stale_candidate,
        )
    except ProjectCatalogRejected as error:
        assert "stale" in str(error).lower()
    else:
        raise AssertionError("stale power-loop candidate was accepted")


def test_power_loop_result_is_marked_stale_when_g7a_inputs_disappear(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    stored = runtime.persist_conversation_detail(_detail())
    verified = runtime.verify_pcb_power_loop(
        stored.project_id,
        stored.conversation_id,
        tenant_id=stored.tenant_id,
        requester=stored.requester,
    )
    incoming = verified.model_copy(
        update={
            "pcb_engineering_state": None,
            "pcb_constraint_set": None,
            "pcb_verification_plan": None,
            "pcb_power_loop_verification": None,
            "fingerprint": ZERO,
        }
    )
    stale = runtime.persist_conversation_detail(incoming)
    assert stale.pcb_power_loop_verification is not None
    assert stale.pcb_power_loop_verification.stale is True
    assert stale.pcb_power_loop_verification.status.value == "UNKNOWN"
    assert "STALE_INPUTS" in stale.pcb_power_loop_verification.unknowns
