from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from embedded_copilot.engineering_pcb import DifferentialPairRouteCandidate
from embedded_copilot.project_catalog import (
    ConversationDetail,
    ProjectCatalogRuntime,
    ProjectCreationMode,
    ProjectInitializationStatus,
    ProjectType,
    ProjectView,
)
from embedded_copilot.project_catalog.exceptions import PCBProductionRejected
from tests.engineering_pcb.test_g7b4_differential_pair import (
    _constraint_set,
    _rules,
    _state,
)

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
            name="G7-B4",
            location_id="g7b4-location",
            directory_name="g7b4",
            creation_mode=ProjectCreationMode.CREATE_NEW,
            relative_path="g7b4",
            project_type=ProjectType.GENERIC_FIRMWARE,
            initialization_status=ProjectInitializationStatus.READY,
            created_at=NOW,
            updated_at=NOW,
            last_activity_at=NOW,
        )
    )
    seed_project_owner(tmp_path / "catalog.sqlite3", "g7b2-project", "g7b4-engineer")
    return runtime


def _detail() -> ConversationDetail:
    state, evidence = _state()
    constraints, plan = _constraint_set(state, evidence, rules=_rules(evidence))
    return ConversationDetail(
        conversation_id="g7b4-conversation",
        project_id="g7b2-project",
        tenant_id="g7b2-tenant",
        requester="g7b4-engineer",
        title="G7-B4 differential pair",
        turns=(),
        created_at=NOW,
        updated_at=NOW,
        pcb_engineering_state=state,
        pcb_constraint_set=constraints,
        pcb_verification_plan=plan,
    )


def _candidate(state, candidate_id="runtime-valid") -> DifferentialPairRouteCandidate:
    return DifferentialPairRouteCandidate.create(
        candidate_id=candidate_id,
        base_state_fingerprint=state.fingerprint,
        pair_id="pair-usb",
        constraint_id="constraint-differential-usb",
        positive_route_segments=(next(item for item in state.routing if item.segment_id == "dp-1"),),
        negative_route_segments=(next(item for item in state.routing if item.segment_id == "dm-1"),),
        positive_route_path=next(item for item in state.route_paths if item.route_id == "dp-path"),
        negative_route_path=next(item for item in state.route_paths if item.route_id == "dm-path"),
    )


def test_differential_pair_runtime_persists_relations_measurements_and_restart(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    stored = runtime.persist_conversation_detail(_detail())
    verified = runtime.verify_pcb_differential_pair(
        stored.project_id,
        stored.conversation_id,
        tenant_id=stored.tenant_id,
        requester=stored.requester,
    )
    assert verified.pcb_differential_pair_verification is not None
    assert verified.pcb_differential_pair_verification.status.value == "PASS"
    assert verified.pcb_differential_pair_relationships
    assert verified.pcb_differential_pair_measurements

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
    assert restored.pcb_differential_pair_verification is not None
    assert restored.pcb_differential_pair_verification.fingerprint == verified.pcb_differential_pair_verification.fingerprint
    assert restored.pcb_differential_pair_relationships
    assert restored.pcb_differential_pair_measurements


def test_differential_pair_prevent_is_read_only_and_rejects_stale_candidate(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    stored = runtime.persist_conversation_detail(_detail())
    before = runtime.conversation(
        stored.project_id,
        stored.conversation_id,
        tenant_id=stored.tenant_id,
        requester=stored.requester,
    )
    evaluation = runtime.evaluate_pcb_differential_pair_prevent(
        stored.project_id,
        stored.conversation_id,
        tenant_id=stored.tenant_id,
        requester=stored.requester,
        candidate=_candidate(before.pcb_engineering_state),
    )
    assert evaluation.status.value == "ALLOW"
    after = runtime.conversation(
        stored.project_id,
        stored.conversation_id,
        tenant_id=stored.tenant_id,
        requester=stored.requester,
    )
    assert after.fingerprint == before.fingerprint
    assert after.pcb_differential_pair_verification is None

    stale = _candidate(before.pcb_engineering_state, "runtime-stale").model_copy(update={"base_state_fingerprint": ZERO, "fingerprint": ZERO})
    try:
        runtime.evaluate_pcb_differential_pair_prevent(
            stored.project_id,
            stored.conversation_id,
            tenant_id=stored.tenant_id,
            requester=stored.requester,
            candidate=stale,
        )
    except PCBProductionRejected as error:
        assert "stale" in str(error).lower()
    else:
        raise AssertionError("stale differential-pair candidate was accepted")


def test_differential_pair_result_is_marked_stale_when_g7a_inputs_disappear(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    stored = runtime.persist_conversation_detail(_detail())
    verified = runtime.verify_pcb_differential_pair(
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
            "pcb_differential_pair_verification": None,
            "fingerprint": ZERO,
        }
    )
    stale = runtime.persist_conversation_detail(incoming)
    assert stale.pcb_differential_pair_verification is not None
    assert stale.pcb_differential_pair_verification.stale is True
    assert stale.pcb_differential_pair_verification.status.value == "UNKNOWN"
    assert "STALE_INPUTS" in stale.pcb_differential_pair_verification.unknowns
