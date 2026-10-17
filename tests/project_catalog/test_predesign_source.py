from __future__ import annotations

from types import SimpleNamespace

import pytest

from embedded_copilot.project_catalog.models import predesign_source_snapshot


def _source() -> SimpleNamespace:
    def item(**values):
        return SimpleNamespace(**values)

    fp = "sha256:" + "a" * 64
    return item(
        project_id="project-1", conversation_id="conversation-1",
        turns=(item(turn_id="turn-1", status=item(value="COMPLETED"), route="GREENFIELD_DESIGN",
                    request_id="request-1", question="design", answer_text="design answer",
                    response_type=item(value="CHAT"), artifact_refs=(), evidence=(),
                    recommendations=(), context_references=()),),
        requirement_context=item(project_id="project-1", conversation_id="conversation-1",
                                 effective_status=item(value="CONFIRMED"), open_questions=(), conflicts=(), fingerprint=fp),
        engineering_plan=item(project_id="project-1", plan_id="plan-1", status=item(value="APPROVED"),
                              content_fingerprint=fp, fingerprint=fp),
        engineering_plan_review=item(project_id="project-1", plan_id="plan-1", status=item(value="APPROVED"),
                                     proposal_fingerprint=fp, fingerprint=fp),
        architecture_design=item(project_id="project-1", architecture_id="architecture-1", plan_id="plan-1",
                                 status=item(value="APPROVED"), content_fingerprint=fp, fingerprint=fp),
        architecture_review=item(project_id="project-1", architecture_id="architecture-1", plan_id="plan-1",
                                 status=item(value="APPROVED"), proposal_fingerprint=fp, fingerprint=fp,
                                 findings=()),
        hardware_selection=item(status=item(value="APPROVED"),
                                approval=item(project_id="project-1", approval_id="approval-1", fingerprint=fp,
                                              architecture_fingerprint=fp, bom_fingerprint=fp),
                                bom=item(project_id="project-1", fingerprint=fp, architecture_fingerprint=fp,
                                         requirements_fingerprint=fp),
                                hardware_truth=item(project_id="project-1", fingerprint=fp, approval_fingerprint=fp,
                                                    architecture_fingerprint=fp, requirements_fingerprint=fp)),
        hardware_design_spec=None,
    )


def test_predesign_snapshot_uses_approved_sources_without_erc_and_ignores_other_turns() -> None:
    detail = _source()
    first = predesign_source_snapshot(detail, "turn-1")
    detail.turns += (SimpleNamespace(turn_id="other", status=SimpleNamespace(value="COMPLETED"),
                                     route="OTHER", request_id="other", question="unrelated"),)
    assert predesign_source_snapshot(detail, "turn-1") == first
    assert first.hardware_selection_approval_id == "approval-1"


def test_predesign_snapshot_rejects_unapproved_hardware_and_source_drift() -> None:
    detail = _source()
    detail.hardware_selection.status = SimpleNamespace(value="REVIEW")
    with pytest.raises(ValueError, match="hardware selection"):
        predesign_source_snapshot(detail, "turn-1")
    detail = _source()
    detail.architecture_design.fingerprint = "sha256:" + "b" * 64
    with pytest.raises(ValueError, match="source binding"):
        predesign_source_snapshot(detail, "turn-1")


@pytest.mark.parametrize("source_field,nested_field", [
    ("requirement_context", "project_id"),
    ("engineering_plan", "project_id"),
    ("engineering_plan_review", "project_id"),
    ("architecture_design", "project_id"),
    ("architecture_review", "project_id"),
    ("approval", "project_id"),
    ("bom", "project_id"),
    ("hardware_truth", "project_id"),
])
def test_predesign_snapshot_rejects_foreign_project_source(source_field, nested_field) -> None:
    detail = _source()
    source = (
        getattr(detail.hardware_selection, source_field)
        if source_field in {"approval", "bom", "hardware_truth"}
        else getattr(detail, source_field)
    )
    setattr(source, nested_field, "project-other")
    with pytest.raises(ValueError):
        predesign_source_snapshot(detail, "turn-1")


def test_predesign_snapshot_rejects_foreign_conversation_requirement() -> None:
    detail = _source()
    detail.requirement_context.conversation_id = "conversation-other"
    with pytest.raises(ValueError, match="requirements"):
        predesign_source_snapshot(detail, "turn-1")
