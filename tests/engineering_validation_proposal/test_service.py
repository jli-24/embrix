from __future__ import annotations

from datetime import UTC, datetime

import pytest

from embedded_copilot.engineering_validation_proposal import (
    ApprovedContextRecord,
    DeterministicValidationRules,
    EvidenceRecord,
    FindingType,
    OperationProposal,
    ProposalChange,
    ProposalConflict,
    ProposalRejected,
    ProposalStatus,
    TargetIdentity,
    ValidationProposalService,
    ValidationRequest,
    ValidationResult,
    ValidationStatus,
    ValidationType,
)
from embedded_copilot.engineering_validation_proposal.adapters.fake import FakeProposalStore
from embedded_copilot.engineering_validation_proposal.models import canonical_fingerprint


FP_A = "sha256:" + "a" * 64
FP_B = "sha256:" + "b" * 64
NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


class _Clock:
    def now(self):
        return NOW


class _Evidence:
    def __init__(self, values):
        self.values = values

    def get_evidence(self, tenant_id, project_id, evidence_id):
        return self.values.get((tenant_id, project_id, evidence_id))


class _Context:
    def __init__(self, *, knowledge=()):
        self.knowledge = {item.context_id: item for item in knowledge}

    def get_memory(self, tenant_id, project_id, memory_id):
        return None

    def get_knowledge(self, tenant_id, project_id, knowledge_id):
        value = self.knowledge.get(knowledge_id)
        if value is None or value.tenant_id != tenant_id or value.project_id != project_id:
            return None
        return value


def _evidence(*, facts=(), fp=FP_A):
    return EvidenceRecord(
        evidence_id="evidence-1",
        tenant_id="tenant-1",
        project_id="project-1",
        source_reference="evidence://sensor-1",
        evidence_fingerprint=fp,
        facts=facts,
    )


def _request(*, validation_type=ValidationType.CROSS_DOMAIN_CONSISTENCY, evidence=("evidence-1",), knowledge=()):
    return ValidationRequest.create(
        validation_id="validation-1",
        tenant_id="tenant-1",
        project_id="project-1",
        requester_id="engineer-1",
        validation_type=validation_type,
        target_identity=TargetIdentity(identity="sensor-1", kind="firmware"),
        target_fingerprint=FP_A,
        project_fingerprint=FP_B,
        evidence_ids=evidence,
        knowledge_ids=knowledge,
        requested_at=NOW,
    )


def _service(*, facts=(), knowledge=(), store=None, evidence_fp=FP_A):
    evidence = _evidence(facts=facts, fp=evidence_fp)
    return ValidationProposalService(
        evidence=_Evidence({("tenant-1", "project-1", "evidence-1"): evidence}),
        context=_Context(knowledge=knowledge),
        rules=DeterministicValidationRules(),
        store=store or FakeProposalStore(),
        clock=_Clock(),
    )


def test_configuration_conflict_closes_validation_finding_proposal_review() -> None:
    service = _service(facts=(("firmware.sensor_address", "0x69"), ("hardware.sensor_address", "0x68")))
    result = service.validate(_request())
    assert result.status is ValidationStatus.FAIL
    assert result.findings[0].finding_type is FindingType.HARDWARE_FIRMWARE_CONFLICT

    proposal = service.create_proposal(tenant_id="tenant-1", project_id="project-1", finding_id=result.findings[0].finding_id)
    assert proposal.status is ProposalStatus.DRAFT
    review = service.submit_review(tenant_id="tenant-1", project_id="project-1", proposal_id=proposal.proposal_id, requester_id="engineer-1")
    assert service.get_proposal("tenant-1", "project-1", proposal.proposal_id).status is ProposalStatus.WAIT_REVIEW
    decided = service.decide_review(tenant_id="tenant-1", project_id="project-1", review_id=review.review_id, reviewer_id="reviewer-1", decision="APPROVE")
    assert decided.status == "APPROVED"
    assert service.get_proposal("tenant-1", "project-1", proposal.proposal_id).status is ProposalStatus.APPROVED


def test_validation_result_fingerprint_normalizes_multiple_findings_before_hashing() -> None:
    service = _service(
        facts=(
            ("firmware.sensor_address", "0x69"),
            ("hardware.sensor_address", "0x68"),
            ("requirement.sample_hz_min", "100"),
            ("firmware.sample_hz", "50"),
        )
    )

    result = service.validate(_request())

    assert len(result.findings) == 2
    assert tuple(item.finding_id for item in result.findings) == tuple(sorted(item.finding_id for item in result.findings))

    rebuilt = ValidationResult.create(
        validation_id=result.validation_id,
        tenant_id=result.tenant_id,
        project_id=result.project_id,
        validation_type=result.validation_type,
        status=result.status,
        evidence_ids=tuple(result.evidence_ids),
        memory_ids=tuple(result.memory_ids),
        knowledge_ids=tuple(result.knowledge_ids),
        findings=tuple(reversed(result.findings)),
        target_identity=result.target_identity,
        target_fingerprint=result.target_fingerprint,
        project_fingerprint=result.project_fingerprint,
        limitations=tuple(result.limitations),
        validated_at=result.validated_at,
    )
    assert rebuilt.validation_fingerprint == result.validation_fingerprint

    def restore_json_arrays(value):
        if isinstance(value, list):
            return tuple(restore_json_arrays(item) for item in value)
        if isinstance(value, dict):
            return {key: restore_json_arrays(item) for key, item in value.items()}
        return value

    round_tripped = ValidationResult.model_validate(restore_json_arrays(result.model_dump(mode="json")))
    assert round_tripped == result
    assert round_tripped.validation_type is ValidationType.CROSS_DOMAIN_CONSISTENCY
    assert round_tripped.status is ValidationStatus.FAIL
    assert round_tripped.validated_at.tzinfo is not None
    assert tuple(item.finding_id for item in round_tripped.findings) == tuple(sorted(item.finding_id for item in round_tripped.findings))


def test_debug_is_candidate_and_preserves_limitations() -> None:
    service = _service(facts=(("debug.failure", "HAL_TIMEOUT"), ("debug.call_path", "sensor_read -> HAL_I2C_Master_Transmit")))
    result = service.validate(_request(validation_type=ValidationType.DEBUG_EVIDENCE))
    finding = result.findings[0]
    assert result.status is ValidationStatus.REVIEW_REQUIRED
    assert finding.finding_type is FindingType.DEBUG_ROOT_CAUSE_CANDIDATE
    assert "unique root cause" in finding.limitations[0]


def test_no_evidence_creates_no_finding_or_proposal() -> None:
    service = _service()
    result = service.validate(_request(evidence=()))
    assert result.status is ValidationStatus.INSUFFICIENT_EVIDENCE
    assert result.findings == ()
    with pytest.raises(ProposalRejected):
        service.create_proposal(tenant_id="tenant-1", project_id="project-1", finding_id="finding-none")


def test_unapproved_context_is_insufficient_evidence() -> None:
    context = ApprovedContextRecord(
        context_id="knowledge-1", tenant_id="tenant-1", project_id="project-1",
        context_kind="KNOWLEDGE", status="REVOKED", context_fingerprint=FP_A,
    )
    service = _service(knowledge=(context,))
    result = service.validate(_request(knowledge=("knowledge-1",)))
    assert result.status is ValidationStatus.INSUFFICIENT_EVIDENCE
    assert result.findings == ()


def test_proposal_change_rejects_execution_fields() -> None:
    with pytest.raises(Exception):
        ProposalChange.model_validate({
            "change_kind": "REPAIR",
            "logical_change": "change",
            "expected_effect": "effect",
            "command": "rm -rf",
        })
    with pytest.raises(Exception):
        ProposalChange(
            change_kind="REPAIR", file_reference="C:\\work\\sensor.c",
            logical_change="change", expected_effect="effect",
        )


def test_review_replay_is_idempotent_and_conflicting_replay_is_rejected() -> None:
    service = _service(facts=(("firmware.sensor_address", "0x69"), ("hardware.sensor_address", "0x68")))
    result = service.validate(_request())
    proposal = service.create_proposal(tenant_id="tenant-1", project_id="project-1", finding_id=result.findings[0].finding_id)
    review = service.submit_review(tenant_id="tenant-1", project_id="project-1", proposal_id=proposal.proposal_id, requester_id="engineer-1")
    first = service.decide_review(tenant_id="tenant-1", project_id="project-1", review_id=review.review_id, reviewer_id="reviewer-1", decision="APPROVE")
    second = service.decide_review(tenant_id="tenant-1", project_id="project-1", review_id=review.review_id, reviewer_id="reviewer-1", decision="APPROVE")
    assert first == second
    with pytest.raises(ProposalConflict):
        service.decide_review(tenant_id="tenant-1", project_id="project-1", review_id=review.review_id, reviewer_id="other-reviewer", decision="REJECT")


def test_contract_fingerprint_is_deterministic_and_tamper_rejected() -> None:
    request = _request()
    assert request.fingerprint == canonical_fingerprint(request)
    changed = request.model_copy(update={"requester_id": "other"})
    assert changed.fingerprint != request.fingerprint
