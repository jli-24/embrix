from __future__ import annotations

import pytest

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.engineering_optimization_loop import (
    OptimizationConflict,
    OptimizationProposal,
    OptimizationProposalStatus,
    OptimizationRequest,
    OptimizationTarget,
    ValidationFindingReference,
)
from embedded_copilot.engineering_optimization_loop.adapters import (
    FakeOptimizationRuleAdapter,
    FakeOptimizationStoreAdapter,
)
from embedded_copilot.engineering_validation import (
    ValidationFinding,
    ValidationFindingStatus,
    ValidationSeverity,
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


def request(
    *, scope: tuple[OptimizationTarget, ...] = (OptimizationTarget.HARDWARE,)
) -> OptimizationRequest:
    value = finding()
    return OptimizationRequest.create(
        request_id="optimization-request-1",
        project_id="project-1",
        validation_report_id="report-validation-1",
        finding_refs=(
            ValidationFindingReference.create(
                finding_id=value.finding_id, fingerprint=value.fingerprint
            ),
        ),
        context_snapshot=EngineeringContextSnapshot.create(
            project_id="project-1",
            task_description="optimize demo board",
            memory_evidence=(),
            graph_evidence=(),
            knowledge_evidence=(),
            multimodal_evidence=(),
            conflicts=(),
            confidence=1.0,
            provenance=(),
            verification_status="APPROVED",
        ),
        knowledge_refs=(),
        optimization_scope=scope,
    )


def proposal(**overrides: object) -> OptimizationProposal:
    value = finding()
    values: dict[str, object] = {
        "project_id": "project-1",
        "source_finding_refs": (
            ValidationFindingReference.create(
                finding_id=value.finding_id, fingerprint=value.fingerprint
            ),
        ),
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


def test_fake_rule_is_deterministic_copy_safe_and_matches_fixed_contract() -> None:
    adapter = FakeOptimizationRuleAdapter()
    first = adapter.generate(request(), (finding(),))
    second = adapter.generate(request(), (finding(),))

    assert first == second
    assert first is not second
    assert first[0] is not second[0]
    assert first[0].proposal_id.startswith("proposal-")
    assert len(first[0].proposal_id) == len("proposal-") + 48
    assert first[0].target is OptimizationTarget.HARDWARE
    assert first[0].title == "Review power decoupling design"
    assert (
        first[0].rationale
        == "Power validation indicates possible voltage mismatch."
    )
    assert first[0].expected_effects == ("Improve power stability.",)
    assert first[0].risk_notes == ("Requires engineer review.",)
    assert first[0].status is OptimizationProposalStatus.CREATED
    assert first[0].source_finding_refs[0].fingerprint == finding().fingerprint


@pytest.mark.parametrize(
    ("input_request", "findings"),
    (
        (request(scope=(OptimizationTarget.FIRMWARE,)), (finding(),)),
        (request(), (finding(rule_id="POWER-002"),)),
        (request(), (finding(message="different observation"),)),
        (request(), ()),
    ),
)
def test_fake_rule_returns_empty_for_nonmatching_valid_inputs(
    input_request: OptimizationRequest,
    findings: tuple[ValidationFinding, ...],
) -> None:
    assert FakeOptimizationRuleAdapter().generate(input_request, findings) == ()


def test_fake_store_is_deep_copy_idempotent_and_read_isolated() -> None:
    store = FakeOptimizationStoreAdapter()
    value = proposal()

    first = store.save_proposal(value)
    repeated = store.save_proposal(value)
    fetched = store.get_proposal(value.project_id, value.proposal_id)

    assert first == repeated == fetched == value
    assert first is not value
    assert fetched is not first
    assert store.proposal_count == 1


def test_fake_store_separates_semantic_identity_and_rejects_status_mutation() -> None:
    store = FakeOptimizationStoreAdapter()
    value = proposal()
    store.save_proposal(value)

    changed_content = proposal(title="Different review")
    store.save_proposal(changed_content)
    assert changed_content.proposal_id != value.proposal_id
    assert store.proposal_count == 2

    changed_status = OptimizationProposal.model_validate(
        {
            **value.model_dump(mode="python"),
            "status": OptimizationProposalStatus.APPROVED,
        }
    )
    with pytest.raises(OptimizationConflict):
        store.save_proposal(changed_status)

    tampered_fingerprint = value.model_copy(
        update={"fingerprint": "sha256:" + "f" * 64}
    )
    with pytest.raises(OptimizationConflict):
        store.save_proposal(tampered_fingerprint)
