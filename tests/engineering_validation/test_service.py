from __future__ import annotations

import copy

import pytest

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.engineering_validation import (
    ValidationArtifactReference,
    ValidationFinding,
    ValidationFindingStatus,
    ValidationRejected,
    ValidationRequest,
    ValidationService,
    ValidationSeverity,
    ValidationUnavailable,
)
from embedded_copilot.engineering_validation.adapters import (
    FakeValidationStoreAdapter,
)


def context_snapshot(*, project_id: str = "project-1") -> EngineeringContextSnapshot:
    return EngineeringContextSnapshot.create(
        project_id=project_id,
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


def request(**overrides: object) -> ValidationRequest:
    values: dict[str, object] = {
        "request_id": "validation-request-1",
        "project_id": "project-1",
        "artifact_reference": ValidationArtifactReference.create(
            artifact_id="demo-board",
            project_id="project-1",
            artifact_type="pcb_board",
            source_reference="artifact://demo-board",
        ),
        "context_snapshot": context_snapshot(),
        "knowledge_refs": ("knowledge://power-rule",),
        "validation_scope": ("hardware_power",),
    }
    values.update(overrides)
    return ValidationRequest.create(**values)


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


class RecordingRule:
    def __init__(self, findings: tuple[ValidationFinding, ...]) -> None:
        self.findings = findings
        self.call_count = 0
        self.seen: ValidationRequest | None = None

    def validate(self, value: ValidationRequest) -> tuple[ValidationFinding, ...]:
        self.call_count += 1
        self.seen = value
        return copy.deepcopy(self.findings)


def test_service_revalidates_input_calls_rule_once_and_builds_report() -> None:
    input_request = request()
    before = input_request.model_dump(mode="python")
    rule = RecordingRule((finding(),))
    store = FakeValidationStoreAdapter()
    service = ValidationService(rule=rule, store=store)

    report = service.validate(input_request)

    assert input_request.model_dump(mode="python") == before
    assert rule.call_count == 1
    assert rule.seen == input_request
    assert rule.seen is not input_request
    assert report.report_id.startswith("report-")
    assert len(report.report_id) == len("report-") + 48
    assert report.project_id == input_request.project_id
    assert report.findings == (finding(),)
    assert report.findings[0] is not rule.findings[0]
    assert report.summary == "findings=1; INFO=0; WARNING=1; ERROR=0; CRITICAL=0"
    assert store.get_finding("project-1", "finding-power-1") == finding()
    assert store.get_report("project-1", report.report_id) == report


def test_service_creates_deterministic_empty_report() -> None:
    input_request = request()
    rule = RecordingRule(())
    store = FakeValidationStoreAdapter()
    service = ValidationService(rule=rule, store=store)

    first = service.validate(input_request)
    second = service.validate(input_request)

    assert first == second
    assert first.findings == ()
    assert first.summary == "findings=0; INFO=0; WARNING=0; ERROR=0; CRITICAL=0"
    assert rule.call_count == 2


@pytest.mark.parametrize(
    "unsafe_finding",
    (
        finding(status=ValidationFindingStatus.CONFIRMED),
        finding(project_id="project-2"),
        finding(category="firmware_timing"),
        finding(evidence_refs=("knowledge://unbound-rule",)),
    ),
)
def test_service_rejects_findings_that_cross_human_or_request_boundaries(
    unsafe_finding: ValidationFinding,
) -> None:
    service = ValidationService(
        rule=RecordingRule((unsafe_finding,)),
        store=FakeValidationStoreAdapter(),
    )

    with pytest.raises(ValidationRejected):
        service.validate(request())


def test_service_rejects_duplicate_finding_identity() -> None:
    value = finding()
    service = ValidationService(
        rule=RecordingRule((value, value)),
        store=FakeValidationStoreAdapter(),
    )

    with pytest.raises(ValidationRejected):
        service.validate(request())


@pytest.mark.parametrize("result", ([finding()], (object(),)))
def test_service_fails_closed_on_malformed_rule_output(result: object) -> None:
    class MalformedRule:
        def validate(self, value):
            return result

    service = ValidationService(
        rule=MalformedRule(),
        store=FakeValidationStoreAdapter(),
    )
    with pytest.raises(ValidationUnavailable):
        service.validate(request())


def test_service_fails_closed_on_rule_exception_without_saving() -> None:
    class FailingRule:
        def validate(self, value):
            raise RuntimeError("rule failed")

    store = FakeValidationStoreAdapter()
    service = ValidationService(rule=FailingRule(), store=store)
    with pytest.raises(ValidationUnavailable):
        service.validate(request())
    assert store.finding_count == 0
    assert store.report_count == 0


def test_service_fails_closed_when_store_returns_different_finding() -> None:
    class MismatchedStore(FakeValidationStoreAdapter):
        def save_finding(self, value):
            return finding(message="different observation")

    service = ValidationService(
        rule=RecordingRule((finding(),)),
        store=MismatchedStore(),
    )
    with pytest.raises(ValidationUnavailable):
        service.validate(request())


def test_report_store_failure_returns_no_report_but_keeps_idempotent_finding() -> None:
    class FailingReportStore(FakeValidationStoreAdapter):
        def save_report(self, value):
            raise RuntimeError("report store unavailable")

    store = FailingReportStore()
    service = ValidationService(rule=RecordingRule((finding(),)), store=store)

    with pytest.raises(ValidationUnavailable):
        service.validate(request())
    assert store.get_finding("project-1", "finding-power-1") == finding()
    assert store.report_count == 0


def test_service_rejects_invalid_dependencies_and_request_subclasses() -> None:
    with pytest.raises(TypeError):
        ValidationService(rule=object(), store=FakeValidationStoreAdapter())
    with pytest.raises(TypeError):
        ValidationService(rule=RecordingRule(()), store=object())

    class DerivedRequest(ValidationRequest):
        pass

    service = ValidationService(
        rule=RecordingRule(()), store=FakeValidationStoreAdapter()
    )
    derived = DerivedRequest.model_validate(request().model_dump(mode="python"))
    with pytest.raises(ValidationRejected):
        service.validate(derived)
