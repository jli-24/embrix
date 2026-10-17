from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot import (
    approve_architecture_design,
    approve_engineering_plan,
    generate_design_truth,
)
from embedded_copilot.engineering_copilot.common import ZERO
from embedded_copilot.engineering_requirements import (
    generate_architecture_design,
    generate_engineering_plan,
)
from embedded_copilot.project_catalog import (
    ProjectCreationMode,
    ProjectLocationReference,
    ProjectType,
)

from .test_plan_persistence import NOW, _confirmed, _runtime
from .test_runtime import request as project_request


def _answer() -> object:
    return type(
        "Answer",
        (),
        {
            "model_dump": lambda self, mode="json": {
                "answer_text": "固件规划已生成",
                "evidence_details": (),
                "recommendations": (),
            }
        },
    )()


def _approved_conversation(tmp_path: Path):
    service = _runtime(tmp_path)
    location = service.locations(tenant_id="tenant-1", requester="engineer-1")[0]
    preview = service.preview(
        project_request(
            project_type=ProjectType.STM32,
            creation_mode=ProjectCreationMode.CREATE_NEW,
            relative_path=None,
            location=ProjectLocationReference(
                location_id=location.location_id,
                fingerprint=location.fingerprint,
            ),
        ),
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    project = service.create(
        preview.preview_id,
        preview.fingerprint,
        confirmed_at=NOW,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    session = service.create_conversation(
        project.project_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    requirement = _confirmed(project.project_id, session.conversation.conversation_id)
    proposed_plan = generate_engineering_plan(requirement)
    plan, plan_review = approve_engineering_plan(
        proposed_plan,
        reviewer="engineer-1",
        decided_at=NOW,
    )
    proposed_architecture = generate_architecture_design(requirement, plan)
    architecture, architecture_review = approve_architecture_design(
        proposed_architecture,
        reviewer="engineer-1",
        decided_at=NOW,
    )
    return (
        service,
        project,
        session,
        requirement,
        plan,
        plan_review,
        architecture,
        architecture_review,
    )


def _persist_design_stack(tmp_path: Path):
    (
        service,
        project,
        session,
        requirement,
        plan,
        plan_review,
        architecture,
        architecture_review,
    ) = _approved_conversation(tmp_path)
    service.begin_turn(
        session.session_id,
        request_id="firmware-persist-turn",
        trace_id="trace-firmware-persist",
        question="生成固件规划",
        created_at=NOW,
    )
    service.complete_turn(
        session.session_id,
        "firmware-persist-turn",
        _answer(),
        NOW,
        route="GREENFIELD_DESIGN",
        requirement_context=requirement,
        engineering_plan=plan,
        engineering_plan_review=plan_review,
        architecture_design=architecture,
        architecture_review=architecture_review,
        design_truth=generate_design_truth(architecture),
    )
    stored = service.conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert stored.firmware_spec is not None
    return service, project, session, stored


def test_firmware_spec_survives_restart_and_failed_turn(tmp_path: Path) -> None:
    _service, project, session, stored = _persist_design_stack(tmp_path)
    spec = stored.firmware_spec
    assert spec is not None
    assert spec.project_id == project.project_id
    assert spec.conversation_id == session.conversation.conversation_id
    assert spec.hardware_design_fingerprint == stored.hardware_design_spec.fingerprint
    assert spec.workflow_fingerprint == stored.engineering_workflow.fingerprint

    reopened = _runtime(tmp_path)
    restored = reopened.conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert restored.firmware_spec == spec

    reopened.begin_turn(
        session.session_id,
        request_id="firmware-fail-turn",
        question="继续检查固件规划",
        created_at=NOW,
    )
    reopened.fail_turn(
        session.session_id,
        "firmware-fail-turn",
        "REVIEW_REQUIRED",
        NOW,
    )
    failed = reopened.conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert failed.firmware_spec == spec


def test_firmware_spec_tampering_and_identity_mismatch_fail_closed(tmp_path: Path) -> None:
    _, project, _, stored = _persist_design_stack(tmp_path)

    tampered = stored.model_dump(mode="python")
    tampered["firmware_spec"]["title"] = "tampered"
    tampered["firmware_spec"]["fingerprint"] = "sha256:" + "b" * 64
    tampered["fingerprint"] = "sha256:" + "b" * 64
    with pytest.raises(ValidationError, match="fingerprint"):
        type(stored).model_validate(tampered, strict=False)

    foreign = stored.model_dump(mode="python")
    foreign["firmware_spec"]["project_id"] = "project-foreign"
    foreign["firmware_spec"]["fingerprint"] = ZERO
    foreign["fingerprint"] = ZERO
    with pytest.raises(ValidationError, match="firmware design|provenance"):
        type(stored).model_validate(foreign, strict=False)
    assert project.project_id != "project-foreign"


def test_old_conversation_without_firmware_spec_restores_as_none(tmp_path: Path) -> None:
    _, _, _, stored = _persist_design_stack(tmp_path)
    legacy = stored.model_dump(
        mode="json",
        exclude={"fingerprint", "firmware_spec", "simulation_spec", "verification_spec", "implementation_spec", "capability_evaluation", "execution_intents", "execution_authorization"},
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
    assert restored.firmware_spec is None
