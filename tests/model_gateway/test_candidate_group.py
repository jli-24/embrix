from datetime import UTC, datetime

import pytest

from embedded_copilot.model_gateway import (
    BoundModelIdentity,
    CandidateGroup,
    CandidateSpec,
    EngineeringStage,
    ModelGatewayManagementService,
    ModelGatewayState,
    ModelSelectionError,
    SelectionMode,
    SQLiteModelGatewayStore,
    StageModelPolicy,
)

NOW = datetime(2026, 9, 27, tzinfo=UTC)
FIRST = BoundModelIdentity(
    provider_id="provider-a",
    endpoint_id="endpoint-a",
    credential_ref="credential:a",
    model_profile_id="model-a",
    model_id="remote-a",
)
SECOND = FIRST.model_copy(update={"model_profile_id": "model-b", "model_id": "remote-b"})


def _group(**changes: object) -> CandidateGroup:
    material = {
        "group_id": "group-a",
        "stage": EngineeringStage.REQUIREMENT_ANALYSIS,
        "candidates": (
            CandidateSpec(identity=FIRST, priority=2),
            CandidateSpec(identity=SECOND, priority=1),
        ),
        "revision": 1,
    }
    material.update(changes)
    return CandidateGroup(**material)


def _policy(**changes: object) -> StageModelPolicy:
    material = {
        "id": "policy-a",
        "project_id": "project-a",
        "stage_id": EngineeringStage.REQUIREMENT_ANALYSIS,
        "selection_mode": SelectionMode.CANDIDATE_GROUP,
        "candidate_group_id": "group-a",
        "candidate_group_revision": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }
    material.update(changes)
    return StageModelPolicy(**material)


def test_candidate_group_valid_version_is_immutable_and_survives_state_roundtrip(tmp_path) -> None:
    group = _group()
    management = ModelGatewayManagementService()
    assert management.add_candidate_group(group) == group
    with pytest.raises(ValueError, match="already exists"):
        management.add_candidate_group(group.model_copy(update={"candidates": tuple(reversed(group.candidates))}))
    state = ModelGatewayState(candidate_groups=(group,), stage_model_policies=(_policy(),))
    store = SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3")
    store.save(state)
    loaded = store.load()
    assert loaded is not None
    assert loaded.candidate_groups == (group,)
    assert loaded.stage_model_policies == (_policy(),)
    restarted = ModelGatewayManagementService()
    restarted.restore(loaded)
    assert restarted.candidate_group(group.group_id, group.revision) == group


@pytest.mark.parametrize(
    ("candidates", "error"),
    [
        ((), "shape is invalid"),
        ((CandidateSpec(identity=FIRST, priority=1), CandidateSpec(identity=SECOND, priority=1)), "priorities must be unique"),
        ((CandidateSpec(identity=FIRST, priority=1), CandidateSpec(identity=FIRST, priority=2)), "identities must be unique"),
    ],
)
def test_candidate_group_rejects_invalid_candidate_lists(candidates, error) -> None:
    with pytest.raises(ValueError, match=error):
        _group(candidates=candidates)


def test_stage_policy_rejects_inline_candidates_and_wrong_group_binding() -> None:
    with pytest.raises(ValueError, match="group reference only"):
        _policy(candidate_models=(FIRST,))
    with pytest.raises(ValueError, match="group reference only"):
        _policy(fixed_model=FIRST)
    management = ModelGatewayManagementService()
    management.add_candidate_group(_group())
    with pytest.raises(ModelSelectionError, match="MODEL_SELECTION_FAILED"):
        management.set_stage_model_policy(_policy(candidate_group_revision=2))
    with pytest.raises(ModelSelectionError, match="MODEL_SELECTION_FAILED"):
        management.set_stage_model_policy(_policy(stage_id=EngineeringStage.DEBUGGING))
    with pytest.raises(ValueError, match="binding is invalid"):
        ModelGatewayState(candidate_groups=(_group(),), stage_model_policies=(_policy(candidate_group_revision=2),))
