from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot import PlanStatus, approve_engineering_plan
from embedded_copilot.engineering_requirements import generate_engineering_plan, merge_requirement_context
from embedded_copilot.project_catalog import (
    ProjectCatalogRuntime,
    ProjectCreationMode,
    ProjectType,
    ProjectLocationReference,
)

from .test_runtime import NOW, Clock, request as project_request


def _runtime(tmp_path: Path) -> ProjectCatalogRuntime:
    root = tmp_path / "projects"
    root.mkdir(exist_ok=True)
    return ProjectCatalogRuntime(
        project_root=root,
        state_database=tmp_path / "desktop.sqlite3",
        clock=Clock(),
        initialize_async=False,
    )


def _confirmed(project_id: str, conversation_id: str):
    partial = merge_requirement_context(
        None,
        project_id=project_id,
        conversation_id=conversation_id,
        turn_id="turn-1",
        utterance="我要做一个 STM32 小车，巡线，电池供电，直流电机",
    )
    return merge_requirement_context(
        partial,
        project_id=project_id,
        conversation_id=conversation_id,
        turn_id="turn-2",
        utterance="开始设计",
        confirmation_requested=True,
    )


def test_conversation_restart_recovers_plan_and_review(tmp_path: Path) -> None:
    service = _runtime(tmp_path)
    location = service.locations(tenant_id="tenant-1", requester="engineer-1")[0]
    preview = service.preview(
        project_request(project_type=ProjectType.STM32, creation_mode=ProjectCreationMode.CREATE_NEW, relative_path=None, location=ProjectLocationReference(location_id=location.location_id, fingerprint=location.fingerprint)),
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    project = service.create(preview.preview_id, preview.fingerprint, confirmed_at=NOW, tenant_id="tenant-1", requester="engineer-1")
    session = service.create_conversation(project.project_id, tenant_id="tenant-1", requester="engineer-1")
    conversation_id = session.conversation.conversation_id
    requirement = _confirmed(project.project_id, conversation_id)
    proposed = generate_engineering_plan(requirement)
    plan, review = approve_engineering_plan(proposed, reviewer="engineer-1", decided_at=NOW)

    service.begin_turn(session.session_id, request_id="plan-turn", trace_id="trace-plan", question="开始", created_at=NOW)
    service.complete_turn(
        session.session_id,
        "plan-turn",
        type("Answer", (), {"model_dump": lambda self, mode="json": {"answer_text": "计划已批准", "evidence_details": (), "recommendations": ()}})(),
        NOW,
        route="GREENFIELD_DESIGN",
        requirement_context=requirement,
        engineering_plan=plan,
        engineering_plan_review=review,
    )

    reopened = _runtime(tmp_path)
    detail = reopened.conversation(project.project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1")
    assert detail.engineering_plan is not None
    assert detail.engineering_plan.status is PlanStatus.APPROVED
    assert detail.engineering_plan_review is not None
    routing = reopened.routing_context(reopened.resume_conversation(project.project_id, conversation_id, tenant_id="tenant-1", requester="engineer-1").session_id)
    assert routing is not None
    assert routing.engineering_plan is not None
    assert routing.engineering_plan.plan_id == plan.plan_id


def test_conversation_rejects_tampered_persisted_plan(tmp_path: Path) -> None:
    service = _runtime(tmp_path)
    location = service.locations(tenant_id="tenant-1", requester="engineer-1")[0]
    preview = service.preview(
        project_request(project_type=ProjectType.STM32, creation_mode=ProjectCreationMode.CREATE_NEW, relative_path=None, location=ProjectLocationReference(location_id=location.location_id, fingerprint=location.fingerprint)),
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    project = service.create(preview.preview_id, preview.fingerprint, confirmed_at=NOW, tenant_id="tenant-1", requester="engineer-1")
    session = service.create_conversation(project.project_id, tenant_id="tenant-1", requester="engineer-1")
    detail = session.conversation
    requirement = _confirmed(project.project_id, detail.conversation_id)
    plan = generate_engineering_plan(requirement)
    raw = detail.model_dump(mode="python")
    raw["requirement_context"] = requirement.model_dump(mode="python")
    raw["engineering_plan"] = plan.model_dump(mode="python")
    raw["engineering_plan"]["title"] = "tampered"
    raw["fingerprint"] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError, match="fingerprint"):
        type(detail).model_validate(raw, strict=False)
