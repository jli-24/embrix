from __future__ import annotations

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_context import (
    ContextSourceType,
    EngineeringContextEvidence,
    EngineeringContextSnapshot,
)
from embedded_copilot.engineering_copilot import (
    EngineeringPlannerService,
    EngineeringRequirement,
    proposal_handoff,
)
from embedded_copilot.model_gateway import ModelResponse


def _snapshot(project_id: str = "project-1") -> EngineeringContextSnapshot:
    evidence = EngineeringContextEvidence.create(
        evidence_id="evidence-1",
        project_id=project_id,
        source_type=ContextSourceType.ENGINEERING_MEMORY,
        subject="MCU",
        summary="verified constraint",
        source_reference="memory:1",
        source_fingerprint="sha256:" + "a" * 64,
        confidence=0.8,
        verification_status="APPROVED",
    )
    return EngineeringContextSnapshot.create(
        project_id=project_id,
        task_description="plan board",
        memory_evidence=(evidence,),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.8,
        provenance=(evidence.source(),),
        verification_status="APPROVED",
    )


def _requirement() -> EngineeringRequirement:
    return EngineeringRequirement.create(
        requirement_id="requirement-1",
        project_id="project-1",
        description="Design a controller",
        constraints=("offline",),
        evidence_refs=("evidence-1",),
    )


class _Gateway:
    def generate(self, request):
        return ModelResponse(
            content="non-authoritative note",
            model_name="offline",
            provider="local",
            provenance=request.engineering_context.provenance,
        )


def test_requirement_is_strict_tuple_only_and_frozen() -> None:
    requirement = _requirement()
    with pytest.raises((TypeError, ValidationError)):
        EngineeringRequirement(
            requirement_id="r", project_id="p", description="x", constraints=[]
        )
    with pytest.raises(ValidationError):
        requirement.description = "changed"


def test_deterministic_plan_and_optional_note_isolation() -> None:
    plain = EngineeringPlannerService().plan(_requirement(), _snapshot())
    noted = EngineeringPlannerService(_Gateway()).plan(_requirement(), _snapshot())
    assert plain.tasks == noted.tasks
    assert plain.fingerprint == noted.fingerprint
    assert noted.notes[0].note == "non-authoritative note"
    assert plain == EngineeringPlannerService().plan(_requirement(), _snapshot())


def test_context_and_evidence_binding_fail_closed_and_handoff_has_no_content() -> None:
    with pytest.raises(ValueError, match="context binding rejected"):
        EngineeringPlannerService().plan(_requirement(), _snapshot("project-2"))
    handoff = proposal_handoff(
        source_type="PLAN",
        source_id="plan-1",
        project_id="project-1",
        proposal_fingerprint="sha256:" + "b" * 64,
        evidence_refs=("evidence-1",),
        governance_reference="review-1",
    )
    assert "content" not in type(handoff).model_fields
    assert "prompt" not in type(handoff).model_fields
