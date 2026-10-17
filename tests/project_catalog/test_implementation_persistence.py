from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot.common import ZERO

from .test_firmware_persistence import _persist_design_stack
from .test_plan_persistence import _runtime


def test_implementation_spec_survives_restart_and_failed_turn(tmp_path: Path) -> None:
    _service, project, session, stored = _persist_design_stack(tmp_path)
    spec = stored.implementation_spec
    assert spec is not None
    assert spec.project_id == project.project_id
    assert spec.conversation_id == session.conversation.conversation_id
    assert spec.hardware_design_fingerprint == stored.hardware_design_spec.fingerprint
    assert spec.firmware_spec_fingerprint == stored.firmware_spec.fingerprint
    assert spec.simulation_spec_fingerprint == stored.simulation_spec.fingerprint
    assert spec.verification_fingerprint == stored.verification_spec.fingerprint
    assert spec.workflow_fingerprint == stored.engineering_workflow.fingerprint

    reopened = _runtime(tmp_path)
    restored = reopened.conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert restored.implementation_spec == spec

    reopened.begin_turn(
        session.session_id,
        request_id="implementation-fail-turn",
        question="继续检查实现规划",
        created_at=stored.updated_at,
    )
    reopened.fail_turn(session.session_id, "implementation-fail-turn", "REVIEW_REQUIRED", stored.updated_at)
    failed = reopened.conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert failed.implementation_spec == spec


def test_implementation_spec_tampering_and_old_conversation_fail_closed(tmp_path: Path) -> None:
    _, project, _, stored = _persist_design_stack(tmp_path)

    tampered = stored.model_dump(mode="python")
    tampered["implementation_spec"]["tasks"][0]["description"] = "tampered"
    tampered["implementation_spec"]["fingerprint"] = ZERO
    tampered["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="fingerprint"):
        type(stored).model_validate(tampered, strict=False)

    legacy = stored.model_dump(
        mode="json",
        exclude={"fingerprint", "implementation_spec", "capability_evaluation", "execution_intents", "execution_authorization"},
    )
    legacy["fingerprint"] = "sha256:" + hashlib.sha256(
        json.dumps(legacy, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    restored = type(stored).model_validate(legacy, strict=False)
    assert restored.implementation_spec is None
    assert restored.project_id == project.project_id
