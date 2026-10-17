from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_completion import architecture_snapshot_from_canonical
from embedded_copilot.engineering_copilot import (
    EngineeringEvidenceBundle,
    EngineeringEvidenceStatus,
    LearningMode,
    TeachingContext,
    approve_architecture_design,
    approve_engineering_plan,
    generate_design_truth,
    transition_engineering_evidence,
)
from embedded_copilot.engineering_requirements import (
    generate_architecture_design,
    generate_engineering_plan,
)
from embedded_copilot.project_catalog import (
    ConversationDetail,
    ProjectCatalogNotFound,
    ProjectCreationMode,
    ProjectLocationReference,
    ProjectType,
)

from .test_plan_persistence import NOW, _confirmed, _runtime
from .test_runtime import request as project_request


def test_conversation_restart_recovers_canonical_architecture(tmp_path: Path) -> None:
    service = _runtime(tmp_path)
    location = service.locations(tenant_id="tenant-1", requester="engineer-1")[0]
    preview = service.preview(
        project_request(
            project_type=ProjectType.STM32,
            creation_mode=ProjectCreationMode.CREATE_NEW,
            relative_path=None,
            location=ProjectLocationReference(
                location_id=location.location_id, fingerprint=location.fingerprint
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
        project.project_id, tenant_id="tenant-1", requester="engineer-1"
    )
    requirement = _confirmed(project.project_id, session.conversation.conversation_id)
    proposed = generate_engineering_plan(requirement)
    plan, plan_review = approve_engineering_plan(
        proposed, reviewer="engineer-1", decided_at=NOW
    )
    architecture = generate_architecture_design(requirement, plan)
    architecture, architecture_review = approve_architecture_design(
        architecture, reviewer="engineer-1", decided_at=NOW
    )
    design_truth = generate_design_truth(architecture)

    service.begin_turn(
        session.session_id,
        request_id="architecture-persist-turn",
        trace_id="trace-architecture-persist",
        question="批准架构",
        created_at=NOW,
    )
    service.complete_turn(
        session.session_id,
        "architecture-persist-turn",
        type(
            "Answer",
            (),
            {
                "model_dump": lambda self, mode="json": {
                    "answer_text": "架构已批准",
                    "evidence_details": (),
                    "recommendations": (),
                }
            },
        )(),
        NOW,
        route="GREENFIELD_DESIGN",
        requirement_context=requirement,
        engineering_plan=plan,
        engineering_plan_review=plan_review,
        architecture_design=architecture,
        architecture_review=architecture_review,
        design_truth=design_truth,
    )

    reopened = _runtime(tmp_path)
    detail = reopened.conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert detail.architecture_design is not None
    assert detail.architecture_design.status.value == "APPROVED"
    assert detail.architecture_review is not None
    assert detail.architecture_review.proposal_fingerprint == detail.architecture_design.content_fingerprint
    assert detail.design_truth is not None
    assert detail.design_truth.architecture_id == detail.architecture_design.architecture_id
    assert detail.design_truth.fingerprint == design_truth.fingerprint
    assert detail.engineering_knowledge_model is not None
    assert detail.engineering_knowledge_model.schema_version == "1.0"
    assert detail.engineering_knowledge_model.project_id == project.project_id
    assert detail.engineering_knowledge_model.conversation_id == session.conversation.conversation_id
    assert detail.engineering_knowledge_model.source_truth_fingerprint == detail.design_truth.fingerprint
    assert detail.engineering_workflow is not None
    assert detail.engineering_workflow.project_id == project.project_id
    assert detail.engineering_workflow.conversation_id == session.conversation.conversation_id
    assert detail.engineering_workflow.plan_id == detail.engineering_plan.plan_id
    assert detail.engineering_workflow.architecture_id == detail.architecture_design.architecture_id
    assert detail.engineering_workflow.status.value == "PLANNED"
    assert detail.hardware_design_spec is not None
    assert detail.hardware_design_spec.project_id == project.project_id
    assert detail.hardware_design_spec.architecture_fingerprint == detail.architecture_design.fingerprint
    assert detail.hardware_design_spec.workflow_fingerprint == detail.engineering_workflow.fingerprint
    tampered_hardware = detail.model_dump(mode="python")
    tampered_hardware["hardware_design_spec"]["project_id"] = "project-foreign"
    tampered_hardware["hardware_design_spec"]["fingerprint"] = "sha256:" + "0" * 64
    tampered_hardware["fingerprint"] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError, match="hardware design"):
        ConversationDetail.model_validate(tampered_hardware)
    reopened.begin_turn(
        session.session_id,
        request_id="hardware-fail-turn",
        question="继续检查硬件规划",
        created_at=NOW,
    )
    reopened.fail_turn(session.session_id, "hardware-fail-turn", "REVIEW_REQUIRED", NOW)
    failed_detail = reopened.conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert failed_detail.hardware_design_spec is not None
    assert detail.engineering_knowledge_model.learning_topics == ()
    assert detail.engineering_knowledge_model.modification_points == ()
    tampered_knowledge = detail.model_dump(mode="python")
    tampered_knowledge["engineering_knowledge_model"]["summary"] = "tampered"
    tampered_knowledge["fingerprint"] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError, match="fingerprint"):
        type(detail).model_validate(tampered_knowledge)
    routing = reopened.routing_context(
        reopened.resume_conversation(
            project.project_id,
            session.conversation.conversation_id,
            tenant_id="tenant-1",
            requester="engineer-1",
        ).session_id
    )
    assert routing is not None
    assert routing.design_truth is not None
    assert routing.design_truth.project_id == project.project_id

    isolated_session = reopened.create_conversation(
        project.project_id, tenant_id="tenant-1", requester="engineer-1"
    )
    isolated_routing = reopened.routing_context(isolated_session.session_id)
    assert isolated_routing is not None
    assert isolated_routing.design_truth is None
    with pytest.raises(ProjectCatalogNotFound):
        reopened.conversation(
            "different-project",
            session.conversation.conversation_id,
            tenant_id="tenant-1",
            requester="engineer-1",
        )

    legacy = detail.model_dump(mode="json", exclude={"fingerprint", "design_truth", "engineering_knowledge_model"})
    legacy_fingerprint = "sha256:" + hashlib.sha256(
        json.dumps(legacy, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    legacy.pop("design_truth", None)
    legacy["fingerprint"] = legacy_fingerprint
    restored_legacy = type(detail).model_validate(legacy, strict=False)
    assert restored_legacy.design_truth is None


def test_canonical_architecture_projects_one_way_to_legacy_snapshot() -> None:
    from tests.engineering_copilot.test_p0_architecture_design_contract import (
        _approved_plan,
    )

    context, plan, _ = _approved_plan()
    design = generate_architecture_design(context, plan)
    snapshot = architecture_snapshot_from_canonical(design)
    assert snapshot.project_id == design.project_id
    assert "STM32" in snapshot.components
    assert snapshot.confidence.value == "PROJECTED"


def test_reverse_design_truth_persists_knowledge_model_in_conversation_json(tmp_path: Path) -> None:
    from tests.engineering_copilot.test_artifact_intelligence_evidence import _bundle

    service = _runtime(tmp_path)
    location = service.locations(tenant_id="tenant-1", requester="engineer-1")[0]
    preview = service.preview(
        project_request(
            project_type=ProjectType.STM32,
            creation_mode=ProjectCreationMode.CREATE_NEW,
            relative_path=None,
            location=ProjectLocationReference(
                location_id=location.location_id, fingerprint=location.fingerprint
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
        project.project_id, tenant_id="tenant-1", requester="engineer-1"
    )
    base_bundle = _bundle()
    evidence = tuple(
        type(item).create(
            **{
                **item.model_dump(mode="python"),
                "project_id": project.project_id,
                "conversation_id": session.conversation.conversation_id,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        for item in base_bundle.evidence
    )
    bundle = EngineeringEvidenceBundle.create(
        bundle_id=base_bundle.bundle_id,
        tenant_id="tenant-1",
        project_id=project.project_id,
        conversation_id=session.conversation.conversation_id,
        artifact_fingerprints=base_bundle.artifact_fingerprints,
        evidence=evidence,
    )
    approved = transition_engineering_evidence(
        transition_engineering_evidence(bundle, EngineeringEvidenceStatus.REVIEWING),
        EngineeringEvidenceStatus.APPROVED,
    )
    seeded = session.conversation.model_copy(
        update={"engineering_evidence_bundle": approved, "fingerprint": "sha256:" + "0" * 64}
    )
    service._repository.save_conversation(ConversationDetail.model_validate(seeded.model_dump(mode="python")))

    truth = service.build_reverse_design_truth(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    stored = service.conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )

    assert truth.origin.value == "REVERSE"
    assert stored.engineering_knowledge_model is not None
    assert stored.engineering_knowledge_model.source_truth_fingerprint == truth.fingerprint
    assert stored.engineering_knowledge_model.project_id == project.project_id
    assert stored.engineering_workflow is None
    assert stored.hardware_design_spec is None
    assert stored.firmware_spec is None
    assert stored.simulation_spec is None
    assert stored.verification_spec is None
    assert stored.implementation_spec is None
    assert stored.capability_evaluation is None
    assert stored.execution_intents == ()
    assert stored.execution_authorization is None

    teaching = service.generate_teaching_context(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
        mode=LearningMode.ENGINEERING,
    )
    assert teaching.source_knowledge_schema_version == stored.engineering_knowledge_model.schema_version
    assert teaching.source_knowledge_fingerprint == stored.engineering_knowledge_model.fingerprint
    assert teaching.learning_path.steps

    persisted = service.conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert persisted.teaching_context == teaching

    legacy = persisted.model_dump(mode="json", exclude={"fingerprint", "teaching_context"})
    legacy["fingerprint"] = "sha256:" + hashlib.sha256(
        json.dumps(legacy, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    restored_legacy = ConversationDetail.model_validate(legacy, strict=False)
    assert restored_legacy.teaching_context is None
    assert restored_legacy.engineering_knowledge_model == persisted.engineering_knowledge_model

    mismatched = TeachingContext.create(
        **{
            **teaching.model_dump(mode="python"),
            "source_knowledge_fingerprint": "sha256:" + "b" * 64,
            "fingerprint": "sha256:" + "0" * 64,
        }
    )
    invalid_detail = persisted.model_copy(
        update={"teaching_context": mismatched, "fingerprint": "sha256:" + "0" * 64}
    )
    with pytest.raises(ValidationError, match="teaching context"):
        ConversationDetail.model_validate(invalid_detail.model_dump(mode="python"))

    reopened = _runtime(tmp_path).conversation(
        project.project_id,
        session.conversation.conversation_id,
        tenant_id="tenant-1",
        requester="engineer-1",
    )
    assert reopened.engineering_knowledge_model is not None
    assert reopened.engineering_knowledge_model == stored.engineering_knowledge_model
    assert reopened.teaching_context == teaching
