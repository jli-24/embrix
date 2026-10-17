from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from embedded_copilot.application import ApplicationQuestionRequest
from embedded_copilot.engineering_copilot.common import ZERO
from embedded_copilot.engineering_copilot import AuthorizationDecisionStatus

from .test_firmware_persistence import _answer, _persist_design_stack
from .test_plan_persistence import NOW, _runtime


def test_authorization_survives_restart_and_failed_turn(tmp_path: Path) -> None:
    _, project, session, stored = _persist_design_stack(tmp_path)
    authorization = stored.execution_authorization

    assert authorization is not None
    assert authorization.project_id == project.project_id
    assert authorization.conversation_id == session.conversation.conversation_id
    assert authorization.capability_evaluation_id == stored.capability_evaluation.evaluation_id

    reopened = _runtime(tmp_path)
    restored = reopened.conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert restored.execution_authorization == authorization

    reopened.begin_turn(
        session.session_id,
        request_id="authorization-fail-turn",
        question="继续检查授权边界",
        created_at=stored.updated_at,
    )
    reopened.fail_turn(
        session.session_id,
        "authorization-fail-turn",
        "REVIEW_REQUIRED",
        stored.updated_at,
    )
    failed = reopened.conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert failed.execution_authorization == authorization


def test_legacy_conversation_rebuilds_authorization(tmp_path: Path) -> None:
    _, project, session, stored = _persist_design_stack(tmp_path)
    legacy = stored.model_dump(
        mode="json",
        exclude={"fingerprint", "execution_authorization"},
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
    assert restored.execution_authorization is not None
    assert (
        restored.execution_authorization.capability_evaluation_id
        == restored.capability_evaluation.evaluation_id
    )


def test_authorization_tampering_fails_closed(tmp_path: Path) -> None:
    _, _, _, stored = _persist_design_stack(tmp_path)
    tampered = stored.model_dump(mode="python")
    tampered["execution_authorization"]["requests"][0]["risk_level"] = "CRITICAL"
    tampered["fingerprint"] = ZERO

    with pytest.raises(ValidationError, match="fingerprint|authorization|risk"):
        type(stored).model_validate(tampered, strict=False)


def test_terminal_authorization_and_source_proposal_survive_turn_and_restart(
    tmp_path: Path,
) -> None:
    service, project, session, stored = _persist_design_stack(tmp_path)
    request = next(
        item
        for item in stored.execution_authorization.requests
        if item.capability_id == "FIRMWARE_CODE_GENERATION"
    )
    decided = service.decide_execution_authorization(
        project.project_id,
        session.conversation.conversation_id,
        request.request_id,
        AuthorizationDecisionStatus.APPROVED,
        tenant_id="tenant-1",
        requester="engineer-1",
        decided_at=NOW,
    )
    assert next(
        item for item in decided.execution_authorization.requests
        if item.request_id == request.request_id
    ).status.value == "AUTHORIZED"

    application_request = ApplicationQuestionRequest(
        request_id="firmware-source-command",
        trace_id="firmware-source-trace",
        project_id=project.project_id,
        tenant_id="tenant-1",
        requester="engineer-1",
        question="开始生成这个固件模块",
        requested_at=NOW,
    )
    service.begin_turn(
        session.session_id,
        request_id=application_request.request_id,
        trace_id=application_request.trace_id,
        question=application_request.question,
        created_at=NOW,
    )
    service.complete_turn(
        session.session_id,
        application_request.request_id,
        _answer(),
        NOW,
        route="GREENFIELD_DESIGN",
    )
    proposed = service.conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert proposed.firmware_source_generation is not None
    assert proposed.firmware_source_generation.status.value == "AWAITING_APPROVAL"
    assert len(proposed.firmware_source_generation.proposal.files) == 2
    assert proposed.firmware_source_generation.request.source_request_id == "firmware-source-command"
    assert proposed.firmware_source_generation.request.trace_id == "firmware-source-trace"
    assert proposed.firmware_source_generation.request.project_id == project.project_id

    restored = _runtime(tmp_path).conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert restored.execution_authorization == proposed.execution_authorization
    assert restored.firmware_source_generation == proposed.firmware_source_generation
