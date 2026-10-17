from __future__ import annotations

import copy

import pytest

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.engineering_governance import ApprovedKnowledge
from embedded_copilot.engineering_optimization_loop import (
    ApprovedKnowledgeReference,
    OptimizationProposal,
    OptimizationProposalStatus,
    OptimizationRejected,
    OptimizationRequest,
    OptimizationService,
    OptimizationTarget,
    OptimizationUnavailable,
    ValidationFindingReference,
)
from embedded_copilot.engineering_validation import (
    ValidationFinding,
    ValidationFindingStatus,
    ValidationReport,
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


def report(*items: ValidationFinding, project_id: str = "project-1") -> ValidationReport:
    findings = items or (finding(),)
    return ValidationReport.create(
        report_id="report-validation-1",
        project_id=project_id,
        findings=findings,
        summary="findings=1; INFO=0; WARNING=1; ERROR=0; CRITICAL=0",
    )


def knowledge(**overrides: object) -> ApprovedKnowledge:
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


def knowledge_ref(value: ApprovedKnowledge | None = None) -> ApprovedKnowledgeReference:
    checked = value or knowledge()
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


_UNSET = object()


class RecordingValidationSource:
    def __init__(
        self,
        *,
        report_value: object = _UNSET,
        findings: object | None = None,
    ) -> None:
        self.report_value = report() if report_value is _UNSET else report_value
        self.findings = findings if findings is not None else (finding(),)
        self.report_calls = 0
        self.finding_calls = 0

    def get_report(self, project_id: str, report_id: str):
        self.report_calls += 1
        return copy.deepcopy(self.report_value)

    def get_findings(self, project_id: str, finding_ids: tuple[str, ...]):
        self.finding_calls += 1
        return copy.deepcopy(self.findings)


class RecordingKnowledgeSource:
    def __init__(self, value: object = _UNSET) -> None:
        self.value = knowledge() if value is _UNSET else value
        self.call_count = 0

    def get_approved_knowledge(self, project_id: str, knowledge_id: str):
        self.call_count += 1
        return copy.deepcopy(self.value)


class RecordingRule:
    def __init__(self, proposals: object) -> None:
        self.proposals = proposals
        self.call_count = 0
        self.seen_request: OptimizationRequest | None = None
        self.seen_findings: tuple[ValidationFinding, ...] | None = None

    def generate(self, value: OptimizationRequest, findings):
        self.call_count += 1
        self.seen_request = value
        self.seen_findings = findings
        return copy.deepcopy(self.proposals)


class RecordingStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], OptimizationProposal] = {}
        self.save_calls = 0

    def save_proposal(self, value: OptimizationProposal) -> OptimizationProposal:
        self.save_calls += 1
        self.values[(value.project_id, value.proposal_id)] = copy.deepcopy(value)
        return copy.deepcopy(value)

    def get_proposal(
        self, project_id: str, proposal_id: str
    ) -> OptimizationProposal | None:
        return copy.deepcopy(self.values.get((project_id, proposal_id)))


def service(
    *,
    validation_source: object | None = None,
    knowledge_source: object | None = None,
    rule: object | None = None,
    store: object | None = None,
) -> OptimizationService:
    return OptimizationService(
        validation_source=validation_source or RecordingValidationSource(),
        knowledge_source=knowledge_source or RecordingKnowledgeSource(),
        rule=rule or RecordingRule((proposal(),)),
        store=store or RecordingStore(),
    )


def test_service_revalidates_sources_calls_rule_once_and_saves_sorted_copies() -> None:
    input_request = request()
    before = input_request.model_dump(mode="python")
    validation_source = RecordingValidationSource()
    knowledge_source = RecordingKnowledgeSource()
    later = proposal(title="Z rail review")
    earlier = proposal(title="A rail review")
    rule = RecordingRule((later, earlier))
    store = RecordingStore()

    result = service(
        validation_source=validation_source,
        knowledge_source=knowledge_source,
        rule=rule,
        store=store,
    ).generate(input_request)

    assert input_request.model_dump(mode="python") == before
    assert validation_source.report_calls == 1
    assert validation_source.finding_calls == 1
    assert knowledge_source.call_count == 1
    assert rule.call_count == 1
    assert rule.seen_request == input_request
    assert rule.seen_request is not input_request
    assert rule.seen_findings == (finding(),)
    assert rule.seen_findings[0] is not validation_source.findings[0]
    assert tuple(item.proposal_id for item in result) == tuple(
        sorted((earlier.proposal_id, later.proposal_id))
    )
    assert store.save_calls == 2
    assert all(item is not rule.proposals[index] for index, item in enumerate(result))


def test_service_returns_deterministic_empty_tuple_without_saving() -> None:
    rule = RecordingRule(())
    store = RecordingStore()
    result = service(rule=rule, store=store).generate(request())
    assert result == ()
    assert rule.call_count == 1
    assert store.save_calls == 0


@pytest.mark.parametrize(
    "validation_source",
    (
        RecordingValidationSource(report_value=None),
        RecordingValidationSource(findings=()),
        RecordingValidationSource(findings=(finding(), finding())),
        RecordingValidationSource(findings=(finding(message="changed finding"),)),
        RecordingValidationSource(
            findings=(finding(finding_id="finding-unrequested"),)
        ),
    ),
)
def test_service_rejects_missing_duplicate_or_drifted_finding_bindings(
    validation_source: RecordingValidationSource,
) -> None:
    with pytest.raises(OptimizationRejected):
        service(validation_source=validation_source).generate(request())


def test_service_allows_report_to_contain_unselected_findings() -> None:
    selected = finding()
    unselected = finding(finding_id="finding-other", message="other mismatch")
    validation_source = RecordingValidationSource(
        report_value=report(selected, unselected), findings=(selected,)
    )
    result = service(validation_source=validation_source).generate(request())
    assert len(result) == 1


@pytest.mark.parametrize(
    "knowledge_value",
    (
        knowledge(project_id="project-2"),
        knowledge(knowledge_id="knowledge-other"),
        knowledge(claim="A different approved claim."),
    ),
)
def test_service_rejects_approved_knowledge_identity_mismatch(
    knowledge_value: ApprovedKnowledge,
) -> None:
    with pytest.raises(OptimizationRejected):
        service(knowledge_source=RecordingKnowledgeSource(knowledge_value)).generate(
            request()
        )


def test_service_rejects_missing_approved_knowledge() -> None:
    with pytest.raises(OptimizationRejected):
        service(knowledge_source=RecordingKnowledgeSource(None)).generate(request())


def test_service_accepts_bound_approved_knowledge_reference_as_evidence() -> None:
    knowledge_evidence = proposal(
        evidence_refs=("knowledge://knowledge-power-1",)
    )
    result = service(rule=RecordingRule((knowledge_evidence,))).generate(request())
    assert result == (knowledge_evidence,)


@pytest.mark.parametrize(
    "unsafe_proposal",
    (
        proposal(project_id="project-2"),
        proposal(target=OptimizationTarget.FIRMWARE),
        proposal(status=OptimizationProposalStatus.APPROVED),
        proposal(
            source_finding_refs=(
                ValidationFindingReference.create(
                    finding_id="finding-other", fingerprint=finding().fingerprint
                ),
            )
        ),
        proposal(
            source_finding_refs=(
                ValidationFindingReference.create(
                    finding_id=finding().finding_id,
                    fingerprint="sha256:" + "1" * 64,
                ),
            )
        ),
        proposal(evidence_refs=("knowledge://unbound",)),
    ),
)
def test_service_rejects_proposals_crossing_scope_evidence_or_human_boundary(
    unsafe_proposal: OptimizationProposal,
) -> None:
    with pytest.raises(OptimizationRejected):
        service(rule=RecordingRule((unsafe_proposal,))).generate(request())


def test_service_rejects_duplicate_proposal_identity() -> None:
    value = proposal()
    with pytest.raises(OptimizationRejected):
        service(rule=RecordingRule((value, value))).generate(request())


@pytest.mark.parametrize("result", ([proposal()], (object(),)))
def test_service_fails_closed_on_malformed_rule_output(result: object) -> None:
    with pytest.raises(OptimizationUnavailable):
        service(rule=RecordingRule(result)).generate(request())


def test_service_maps_source_rule_and_store_exceptions_to_unavailable() -> None:
    class FailingValidationSource(RecordingValidationSource):
        def get_report(self, project_id: str, report_id: str):
            raise RuntimeError("source unavailable")

    class FailingRule(RecordingRule):
        def generate(self, value, findings):
            raise RuntimeError("rule unavailable")

    class FailingStore(RecordingStore):
        def save_proposal(self, value):
            raise RuntimeError("store unavailable")

    class FailingKnowledgeSource(RecordingKnowledgeSource):
        def get_approved_knowledge(self, project_id: str, knowledge_id: str):
            raise RuntimeError("knowledge unavailable")

    with pytest.raises(OptimizationUnavailable):
        service(validation_source=FailingValidationSource()).generate(request())
    with pytest.raises(OptimizationUnavailable):
        service(rule=FailingRule(())).generate(request())
    with pytest.raises(OptimizationUnavailable):
        service(knowledge_source=FailingKnowledgeSource()).generate(request())
    with pytest.raises(OptimizationUnavailable):
        service(store=FailingStore()).generate(request())


@pytest.mark.parametrize(
    ("validation_source", "knowledge_source"),
    (
        (RecordingValidationSource(report_value=object()), RecordingKnowledgeSource()),
        (RecordingValidationSource(findings=[finding()]), RecordingKnowledgeSource()),
        (RecordingValidationSource(), RecordingKnowledgeSource(object())),
    ),
)
def test_service_fails_closed_on_malformed_source_output(
    validation_source: RecordingValidationSource,
    knowledge_source: RecordingKnowledgeSource,
) -> None:
    with pytest.raises(OptimizationUnavailable):
        service(
            validation_source=validation_source,
            knowledge_source=knowledge_source,
        ).generate(request())


def test_service_fails_closed_when_store_changes_proposal() -> None:
    class ChangedStore(RecordingStore):
        def save_proposal(self, value):
            return proposal(title="store changed proposal")

    with pytest.raises(OptimizationUnavailable):
        service(store=ChangedStore()).generate(request())


def test_store_failure_keeps_prior_proposal_for_idempotent_retry() -> None:
    first = proposal(title="A rail review")
    second = proposal(title="Z rail review")

    class FailOnceStore(RecordingStore):
        def __init__(self) -> None:
            super().__init__()
            self.failed = False

        def save_proposal(self, value):
            if self.save_calls == 1 and not self.failed:
                self.failed = True
                self.save_calls += 1
                raise RuntimeError("temporary store failure")
            return super().save_proposal(value)

    store = FailOnceStore()
    loop = service(rule=RecordingRule((first, second)), store=store)
    with pytest.raises(OptimizationUnavailable):
        loop.generate(request())
    assert len(store.values) == 1

    result = loop.generate(request())
    assert result == tuple(sorted((first, second), key=lambda item: item.proposal_id))
    assert len(store.values) == 2


def test_service_rejects_invalid_dependencies_and_request_subclasses() -> None:
    with pytest.raises(TypeError):
        OptimizationService(
            validation_source=object(),
            knowledge_source=RecordingKnowledgeSource(),
            rule=RecordingRule(()),
            store=RecordingStore(),
        )

    class DerivedRequest(OptimizationRequest):
        pass

    derived = DerivedRequest.model_validate(request().model_dump(mode="python"))
    with pytest.raises(OptimizationRejected):
        service().generate(derived)
