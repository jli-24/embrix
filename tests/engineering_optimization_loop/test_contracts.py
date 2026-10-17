from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.engineering_governance import ApprovedKnowledge
from embedded_copilot.engineering_optimization_loop import (
    ApprovedKnowledgeReference,
    OptimizationProposal,
    OptimizationProposalStatus,
    OptimizationRequest,
    OptimizationTarget,
    ValidationFindingReference,
)
from embedded_copilot.engineering_validation import (
    ValidationFinding,
    ValidationFindingStatus,
    ValidationSeverity,
)


def context_snapshot(*, project_id: str = "project-1") -> EngineeringContextSnapshot:
    return EngineeringContextSnapshot.create(
        project_id=project_id,
        task_description="optimize demo board",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=1.0,
        provenance=(),
        verification_status="APPROVED",
    )


def finding(**overrides: object) -> ValidationFinding:
    values: dict[str, object] = {
        "finding_id": "finding-power-1",
        "project_id": "project-1",
        "rule_id": "POWER-001",
        "category": "hardware_power",
        "severity": ValidationSeverity.WARNING,
        "status": ValidationFindingStatus.OPEN,
        "message": "possible voltage mismatch",
        "evidence_refs": ("knowledge://power-rule",),
    }
    values.update(overrides)
    return ValidationFinding.create(**values)


def approved_knowledge(**overrides: object) -> ApprovedKnowledge:
    values: dict[str, object] = {
        "knowledge_id": "knowledge-power-1",
        "candidate_id": "candidate-power-1",
        "project_id": "project-1",
        "claim": "Review decoupling when a voltage mismatch is observed.",
        "evidence_refs": ("context:evidence-power-1",),
        "approved_by": "engineer-1",
    }
    values.update(overrides)
    return ApprovedKnowledge.create(**values)


def finding_ref(value: ValidationFinding | None = None) -> ValidationFindingReference:
    checked = value or finding()
    return ValidationFindingReference.create(
        finding_id=checked.finding_id,
        fingerprint=checked.fingerprint,
    )


def knowledge_ref(
    value: ApprovedKnowledge | None = None,
) -> ApprovedKnowledgeReference:
    checked = value or approved_knowledge()
    return ApprovedKnowledgeReference.create(
        reference=f"knowledge://{checked.knowledge_id}",
        fingerprint=checked.fingerprint,
    )


def request(**overrides: object) -> OptimizationRequest:
    values: dict[str, object] = {
        "request_id": "optimization-request-1",
        "project_id": "project-1",
        "validation_report_id": "report-validation-1",
        "finding_refs": (finding_ref(),),
        "context_snapshot": context_snapshot(),
        "knowledge_refs": (knowledge_ref(),),
        "optimization_scope": (OptimizationTarget.HARDWARE,),
    }
    values.update(overrides)
    return OptimizationRequest.create(**values)


def proposal(**overrides: object) -> OptimizationProposal:
    values: dict[str, object] = {
        "project_id": "project-1",
        "source_finding_refs": (finding_ref(),),
        "target": OptimizationTarget.HARDWARE,
        "title": "Review power decoupling design",
        "rationale": "Power validation indicates possible voltage mismatch.",
        "expected_effects": ("Improve power stability.",),
        "evidence_refs": ("knowledge://power-rule",),
        "risk_notes": ("Requires engineer review.",),
        "status": OptimizationProposalStatus.CREATED,
    }
    values.update(overrides)
    return OptimizationProposal.create(**values)


def test_contracts_are_frozen_strict_and_forbid_extra_fields() -> None:
    value = proposal()
    with pytest.raises(ValidationError):
        value.title = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        OptimizationProposal.model_validate(
            {**value.model_dump(mode="python"), "unexpected": "value"}
        )
    with pytest.raises((TypeError, ValidationError)):
        OptimizationRequest.model_validate(
            {
                **request().model_dump(mode="python"),
                "optimization_scope": ("HARDWARE",),
            }
        )


def test_request_uses_sorted_typed_refs_and_computed_fingerprint() -> None:
    first_finding = finding(finding_id="finding-a")
    last_finding = finding(finding_id="finding-z", message="rail mismatch")
    first_knowledge = approved_knowledge(knowledge_id="knowledge-a")
    last_knowledge = approved_knowledge(
        knowledge_id="knowledge-z", claim="Review the power rail margin."
    )
    snapshot = context_snapshot()

    value = request(
        finding_refs=(finding_ref(last_finding), finding_ref(first_finding)),
        context_snapshot=snapshot,
        knowledge_refs=(knowledge_ref(last_knowledge), knowledge_ref(first_knowledge)),
        optimization_scope=(
            OptimizationTarget.POWER,
            OptimizationTarget.HARDWARE,
        ),
    )

    assert tuple(item.finding_id for item in value.finding_refs) == (
        "finding-a",
        "finding-z",
    )
    assert tuple(item.reference for item in value.knowledge_refs) == (
        "knowledge://knowledge-a",
        "knowledge://knowledge-z",
    )
    assert value.optimization_scope == (
        OptimizationTarget.HARDWARE,
        OptimizationTarget.POWER,
    )
    assert value.context_snapshot == snapshot
    assert value.context_snapshot is not snapshot
    assert value.fingerprint.startswith("sha256:")
    assert "fingerprint" not in value.model_dump(mode="python")
    assert "fingerprint" not in OptimizationRequest.model_json_schema()["properties"]


def test_request_rejects_mutable_duplicate_empty_and_cross_project_inputs() -> None:
    duplicate_finding = finding_ref()
    duplicate_knowledge = knowledge_ref()
    with pytest.raises((TypeError, ValidationError)):
        request(finding_refs=[duplicate_finding])
    with pytest.raises((TypeError, ValidationError)):
        request(knowledge_refs=[duplicate_knowledge])
    with pytest.raises((TypeError, ValidationError)):
        request(optimization_scope=[OptimizationTarget.HARDWARE])
    with pytest.raises((ValueError, ValidationError)):
        request(finding_refs=())
    with pytest.raises((ValueError, ValidationError)):
        request(finding_refs=(duplicate_finding, duplicate_finding))
    with pytest.raises((ValueError, ValidationError)):
        request(
            finding_refs=(
                duplicate_finding,
                ValidationFindingReference.create(
                    finding_id="finding-other",
                    fingerprint=duplicate_finding.fingerprint,
                ),
            )
        )
    with pytest.raises((ValueError, ValidationError)):
        request(knowledge_refs=(duplicate_knowledge, duplicate_knowledge))
    with pytest.raises((ValueError, ValidationError)):
        request(
            knowledge_refs=(
                duplicate_knowledge,
                ApprovedKnowledgeReference.create(
                    reference="knowledge://knowledge-other",
                    fingerprint=duplicate_knowledge.fingerprint,
                ),
            )
        )
    with pytest.raises((ValueError, ValidationError)):
        request(optimization_scope=())
    with pytest.raises((ValueError, ValidationError)):
        request(
            optimization_scope=(
                OptimizationTarget.HARDWARE,
                OptimizationTarget.HARDWARE,
            )
        )
    with pytest.raises((ValueError, ValidationError)):
        request(context_snapshot=context_snapshot(project_id="project-2"))


def test_reference_factories_do_not_ignore_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ValidationFindingReference.create(
            finding_id=finding().finding_id,
            fingerprint=finding().fingerprint,
            unexpected="value",
        )
    with pytest.raises(ValidationError):
        ApprovedKnowledgeReference.create(
            reference="knowledge://knowledge-power-1",
            fingerprint=approved_knowledge().fingerprint,
            unexpected="value",
        )


@pytest.mark.parametrize(
    "reference",
    (
        "Knowledge://knowledge-power-1",
        "http://example.com/knowledge",
        "https://example.com/knowledge",
        "file:///tmp/knowledge",
        "C:/private/knowledge",
        r"\\server\share\knowledge",
        "data:text/plain;base64,AAAA",
        "knowledge://token=secret",
        "knowledge://knowledge-power-1?credential=value",
        "knowledge://knowledge-power-1#fragment",
        "knowledge://knowledge%2fpower",
    ),
)
def test_approved_knowledge_reference_rejects_unsafe_values(reference: str) -> None:
    with pytest.raises((TypeError, ValueError, ValidationError)):
        ApprovedKnowledgeReference.create(
            reference=reference,
            fingerprint=approved_knowledge().fingerprint,
        )


def test_proposal_normalizes_text_and_owns_tuple_copies() -> None:
    source = finding_ref()
    value = proposal(
        source_finding_refs=(source,),
        title="Review cafe\u0301 rail design",
        expected_effects=("Improve cafe\u0301 rail stability.",),
    )

    assert value.title == "Review caf\u00e9 rail design"
    assert value.expected_effects == ("Improve caf\u00e9 rail stability.",)
    assert value.source_finding_refs[0] == source
    assert value.source_finding_refs[0] is not source
    assert copy.deepcopy(value) == value

    with pytest.raises((TypeError, ValidationError)):
        proposal(expected_effects=["Improve power stability."])
    with pytest.raises((TypeError, ValidationError)):
        proposal(risk_notes=["Requires engineer review."])
    with pytest.raises((TypeError, ValidationError)):
        proposal(source_finding_refs=[source])
    with pytest.raises((ValueError, ValidationError)):
        proposal(expected_effects=())
    with pytest.raises((ValueError, ValidationError)):
        proposal(evidence_refs=("knowledge://power-rule", "knowledge://power-rule"))
    with pytest.raises((ValueError, ValidationError)):
        proposal(rationale="raw model output: hidden reasoning")


def test_proposal_fingerprint_is_semantic_and_proposal_id_is_derived() -> None:
    created = proposal()
    approved = OptimizationProposal.model_validate(
        {
            **created.model_dump(mode="python"),
            "status": OptimizationProposalStatus.APPROVED,
        }
    )
    rejected = OptimizationProposal.model_validate(
        {
            **created.model_dump(mode="python"),
            "status": OptimizationProposalStatus.REJECTED,
        }
    )

    assert created.fingerprint == approved.fingerprint == rejected.fingerprint
    assert created.proposal_id == approved.proposal_id == rejected.proposal_id
    assert created.proposal_id == "proposal-" + created.fingerprint.removeprefix(
        "sha256:"
    )[:48]

    with pytest.raises(ValidationError):
        OptimizationProposal.model_validate(
            {
                **created.model_dump(mode="python"),
                "proposal_id": "proposal-" + "0" * 48,
            }
        )
    with pytest.raises(ValidationError):
        OptimizationProposal.model_validate(
            {
                **created.model_dump(mode="python"),
                "fingerprint": "sha256:" + "f" * 64,
            }
        )
