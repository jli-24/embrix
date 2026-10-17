from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot import EngineeringPlan, approve_engineering_plan
from embedded_copilot.engineering_copilot.contracts import PlanModelOperationBinding
from embedded_copilot.model_gateway.governance import EngineeringStage


def _v1_plan() -> EngineeringPlan:
    fixture = Path(__file__).parent / "fixtures" / "s05_v1_plan_review.json"
    return EngineeringPlan.model_validate(json.loads(fixture.read_text(encoding="utf-8"))["plan"], strict=False)


def _v2_plan() -> EngineeringPlan:
    old = _v1_plan()
    phase = next(item for item in old.phases if item.phase_id == "phase-hardware-design")
    binding = PlanModelOperationBinding.create(
        phase_id=phase.phase_id,
        plan_task_id=phase.tasks[0].task_id,
        operation_id="HARDWARE_SELECTION_PROPOSAL",
        stage_id=EngineeringStage.HARDWARE_SELECTION,
    )
    data = old.model_dump(mode="python")
    data.update(model_operation_schema_version=2, model_operation_bindings=(binding,), fingerprint="sha256:" + "0" * 64)
    return EngineeringPlan.model_validate(data, strict=False)


def test_v1_plan_and_review_fixed_fingerprints_remain_unchanged() -> None:
    plan = _v1_plan()
    assert plan.model_operation_schema_version == 1
    assert plan.model_operation_bindings == ()
    assert plan.fingerprint == "sha256:ccd77e80df745a09e6bfa9dfcf043dc25c372395688a3f8fa938df52119f8db7"
    assert plan.content_fingerprint == "sha256:789d8f0ebe61bbf3bda6082478ed08e32b4dec2e964db5311c058438a59b7716"
    approved, review = approve_engineering_plan(plan, reviewer="user-1", decided_at=datetime(2026, 9, 2, tzinfo=UTC))
    assert review.fingerprint == "sha256:f03218b86a0d1de29dffdc06c943d47f68559fb8dc68a83464f88081801cf20a"
    assert review.proposal_fingerprint == plan.content_fingerprint
    assert EngineeringPlan.model_validate(approved.model_dump(mode="python")).fingerprint == approved.fingerprint


def test_v2_binding_changes_plan_content_and_requires_new_review() -> None:
    old = _v1_plan()
    plan = _v2_plan()
    assert plan.model_operation_schema_version == 2
    assert plan.content_fingerprint != old.content_fingerprint
    assert plan.fingerprint != old.fingerprint
    _, review = approve_engineering_plan(plan, reviewer="user-1", decided_at=datetime(2026, 9, 2, tzinfo=UTC))
    assert review.proposal_fingerprint == plan.content_fingerprint
    assert review.proposal_fingerprint != old.content_fingerprint


@pytest.mark.parametrize("field,value", [
    ("stage_id", EngineeringStage.SCHEMATIC_DESIGN),
    ("plan_task_id", "absent-task"),
    ("phase_id", "absent-phase"),
])
def test_v2_rejects_tampered_or_unbound_operation(field: str, value: object) -> None:
    data = _v2_plan().model_dump(mode="python")
    data["model_operation_bindings"][0][field] = value
    with pytest.raises(ValidationError):
        EngineeringPlan.model_validate(data, strict=False)


def test_v1_cannot_gain_binding_and_v2_requires_explicit_binding_list() -> None:
    old = _v1_plan().model_dump(mode="python")
    old["model_operation_bindings"] = _v2_plan().model_operation_bindings
    with pytest.raises(ValidationError):
        EngineeringPlan.model_validate(old, strict=False)
    new = _v2_plan().model_dump(mode="python")
    new.pop("model_operation_bindings")
    with pytest.raises(ValidationError):
        EngineeringPlan.model_validate(new, strict=False)
