from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot.common import ZERO

from .test_firmware_persistence import _persist_design_stack
from .test_plan_persistence import _runtime


def test_capability_state_survives_restart_and_failed_turn(tmp_path: Path) -> None:
    _service, project, session, stored = _persist_design_stack(tmp_path)
    evaluation = stored.capability_evaluation
    intents = stored.execution_intents

    assert evaluation is not None
    assert evaluation.project_id == project.project_id
    assert evaluation.implementation_fingerprint == stored.implementation_spec.fingerprint
    assert intents

    reopened = _runtime(tmp_path)
    restored = reopened.conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert restored.capability_evaluation == evaluation
    assert restored.execution_intents == intents

    reopened.begin_turn(
        session.session_id,
        request_id="capability-fail-turn",
        question="继续检查能力边界",
        created_at=stored.updated_at,
    )
    reopened.fail_turn(
        session.session_id,
        "capability-fail-turn",
        "REVIEW_REQUIRED",
        stored.updated_at,
    )
    failed = reopened.conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert failed.capability_evaluation == evaluation
    assert failed.execution_intents == intents


def test_legacy_conversation_rebuilds_capability_state(tmp_path: Path) -> None:
    _, project, session, stored = _persist_design_stack(tmp_path)
    legacy = stored.model_dump(
        mode="json",
        exclude={
            "fingerprint",
            "capability_evaluation",
            "execution_intents",
            "execution_authorization",
        },
    )
    legacy["fingerprint"] = ZERO
    with sqlite3.connect(tmp_path / "desktop.sqlite3") as connection:
        connection.execute(
            "UPDATE conversations SET body = ? WHERE conversation_id = ?",
            (json.dumps(legacy, ensure_ascii=False), session.conversation.conversation_id),
        )

    restored = _runtime(tmp_path).conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert restored.capability_evaluation is not None
    assert restored.capability_evaluation.implementation_fingerprint == restored.implementation_spec.fingerprint
    assert len(restored.execution_intents) == len(restored.implementation_spec.tasks)


def test_capability_tampering_and_cross_project_binding_fail_closed(tmp_path: Path) -> None:
    _, _, _, stored = _persist_design_stack(tmp_path)

    tampered = stored.model_dump(mode="python")
    tampered["execution_intents"][0]["required_capabilities"] = ("PROJECT_BUILD",)
    tampered["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="fingerprint|capability"):
        type(stored).model_validate(tampered, strict=False)

    foreign = stored.model_dump(mode="python")
    foreign["capability_evaluation"]["project_id"] = "project-foreign"
    foreign["capability_evaluation"]["fingerprint"] = ZERO
    foreign["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="capability.*project|project.*capability"):
        type(stored).model_validate(foreign, strict=False)
