from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot.common import ZERO

from .test_firmware_persistence import _persist_design_stack
from .test_plan_persistence import _runtime


def test_simulation_spec_survives_restart_and_failed_turn(tmp_path: Path) -> None:
    _service, project, session, stored = _persist_design_stack(tmp_path)
    spec = stored.simulation_spec
    assert spec is not None
    assert spec.project_id == project.project_id
    assert spec.conversation_id == session.conversation.conversation_id
    assert spec.hardware_design_fingerprint == stored.hardware_design_spec.fingerprint
    assert spec.firmware_spec_fingerprint == stored.firmware_spec.fingerprint
    assert spec.workflow_fingerprint == stored.engineering_workflow.fingerprint

    reopened = _runtime(tmp_path)
    restored = reopened.conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert restored.simulation_spec == spec

    reopened.begin_turn(
        session.session_id,
        request_id="simulation-fail-turn",
        question="继续检查仿真设计",
        created_at=stored.updated_at,
    )
    reopened.fail_turn(
        session.session_id,
        "simulation-fail-turn",
        "REVIEW_REQUIRED",
        stored.updated_at,
    )
    failed = reopened.conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert failed.simulation_spec == spec


def test_simulation_spec_tampering_and_identity_mismatch_fail_closed(tmp_path: Path) -> None:
    _, project, _, stored = _persist_design_stack(tmp_path)

    tampered = stored.model_dump(mode="python")
    tampered["simulation_spec"]["title"] = "tampered"
    tampered["simulation_spec"]["fingerprint"] = "sha256:" + "b" * 64
    tampered["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="fingerprint"):
        type(stored).model_validate(tampered, strict=False)

    foreign = stored.model_dump(mode="python")
    foreign["simulation_spec"]["project_id"] = "project-foreign"
    foreign["simulation_spec"]["fingerprint"] = ZERO
    foreign["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="simulation design|provenance"):
        type(stored).model_validate(foreign, strict=False)
    assert project.project_id != "project-foreign"


def test_old_conversation_without_simulation_spec_restores_as_none(tmp_path: Path) -> None:
    _, _, _, stored = _persist_design_stack(tmp_path)
    legacy = stored.model_dump(
        mode="json",
        exclude={"fingerprint", "simulation_spec", "verification_spec", "implementation_spec", "capability_evaluation", "execution_intents", "execution_authorization"},
    )
    legacy["fingerprint"] = "sha256:" + hashlib.sha256(
        json.dumps(
            legacy,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()

    restored = type(stored).model_validate(legacy, strict=False)
    assert restored.simulation_spec is None
    assert restored.firmware_spec == stored.firmware_spec
