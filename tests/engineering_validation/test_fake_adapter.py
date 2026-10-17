from __future__ import annotations

import pytest

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.engineering_validation import (
    ValidationArtifactReference,
    ValidationConflict,
    ValidationFinding,
    ValidationFindingStatus,
    ValidationReport,
    ValidationRequest,
    ValidationSeverity,
)
from embedded_copilot.engineering_validation.adapters import (
    FakeValidationRuleAdapter,
    FakeValidationStoreAdapter,
)


def request(
    *,
    source_reference: str = "artifact://demo-board",
    knowledge_refs: tuple[str, ...] = ("knowledge://power-rule",),
    validation_scope: tuple[str, ...] = ("hardware_power",),
) -> ValidationRequest:
    snapshot = EngineeringContextSnapshot.create(
        project_id="project-1",
        task_description="validate demo board",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=1.0,
        provenance=(),
        verification_status="APPROVED",
    )
    return ValidationRequest.create(
        request_id="validation-request-1",
        project_id="project-1",
        artifact_reference=ValidationArtifactReference.create(
            artifact_id="demo-board",
            project_id="project-1",
            artifact_type="pcb_board",
            source_reference=source_reference,
        ),
        context_snapshot=snapshot,
        knowledge_refs=knowledge_refs,
        validation_scope=validation_scope,
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


def test_fake_rule_is_deterministic_copy_safe_and_matches_fixed_contract() -> None:
    adapter = FakeValidationRuleAdapter()
    first = adapter.validate(request())
    second = adapter.validate(request())

    assert first == second
    assert first is not second
    assert first[0] is not second[0]
    assert first[0].finding_id.startswith("finding-")
    assert len(first[0].finding_id) == len("finding-") + 48
    assert first[0].rule_id == "POWER-001"
    assert first[0].category == "hardware_power"
    assert first[0].severity is ValidationSeverity.WARNING
    assert first[0].status is ValidationFindingStatus.OPEN
    assert first[0].message == "possible voltage mismatch"
    assert first[0].evidence_refs == ("knowledge://power-rule",)


@pytest.mark.parametrize(
    "input_request",
    (
        request(source_reference="artifact://other-board"),
        request(knowledge_refs=("knowledge://other-rule",)),
        request(validation_scope=("firmware_timing",)),
    ),
)
def test_fake_rule_returns_empty_tuple_for_other_valid_requests(
    input_request: ValidationRequest,
) -> None:
    assert FakeValidationRuleAdapter().validate(input_request) == ()


def test_fake_store_is_deep_copy_idempotent_and_conflict_aware() -> None:
    store = FakeValidationStoreAdapter()
    value = finding()

    first = store.save_finding(value)
    second = store.save_finding(value)
    fetched = store.get_finding(value.project_id, value.finding_id)

    assert first == second == fetched == value
    assert first is not value
    assert fetched is not first
    assert store.finding_count == 1

    with pytest.raises(ValidationConflict):
        store.save_finding(finding(message="different voltage observation"))


def test_fake_store_saves_and_returns_independent_report_copy() -> None:
    store = FakeValidationStoreAdapter()
    value = finding()
    store.save_finding(value)
    report = ValidationReport.create(
        report_id="report-validation-1",
        project_id="project-1",
        findings=(value,),
        summary="findings=1; INFO=0; WARNING=1; ERROR=0; CRITICAL=0",
    )

    saved = store.save_report(report)
    repeated = store.save_report(report)
    fetched = store.get_report(report.project_id, report.report_id)

    assert saved == repeated == fetched == report
    assert saved is not report
    assert fetched is not saved
    assert store.report_count == 1

    conflicting = ValidationReport.create(
        report_id=report.report_id,
        project_id=report.project_id,
        findings=(),
        summary="findings=0; INFO=0; WARNING=0; ERROR=0; CRITICAL=0",
    )
    with pytest.raises(ValidationConflict):
        store.save_report(conflicting)
