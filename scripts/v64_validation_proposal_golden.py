from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from embedded_copilot.engineering_validation_proposal import (
    ApprovedContextRecord,
    DeterministicValidationRules,
    EvidenceRecord,
    FindingType,
    ProposalRejected,
    ProposalStatus,
    ProposalType,
    ValidationProposalService,
    ValidationRequest,
    ValidationStatus,
    ValidationType,
)
from embedded_copilot.engineering_validation_proposal.adapters.fake import FakeProposalStore
from embedded_copilot.engineering_validation_proposal.adapters.sqlite import SQLiteV64Store

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "acceptance" / "v6.4-validation-proposal-golden.json"
FP_A = "sha256:" + "a" * 64
FP_B = "sha256:" + "b" * 64
NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


class EvidenceSource:
    def __init__(self, record: EvidenceRecord | None) -> None:
        self.records = {} if record is None else {(record.tenant_id, record.project_id, record.evidence_id): record}

    def get_evidence(self, tenant_id: str, project_id: str, evidence_id: str) -> EvidenceRecord | None:
        return self.records.get((tenant_id, project_id, evidence_id))


class ContextSource:
    def __init__(self, *, knowledge: tuple[ApprovedContextRecord, ...] = ()) -> None:
        self.knowledge = {item.context_id: item for item in knowledge}

    def get_memory(self, tenant_id: str, project_id: str, memory_id: str) -> ApprovedContextRecord | None:
        return None

    def get_knowledge(self, tenant_id: str, project_id: str, knowledge_id: str) -> ApprovedContextRecord | None:
        value = self.knowledge.get(knowledge_id)
        return value if value and value.tenant_id == tenant_id and value.project_id == project_id else None


class Clock:
    def now(self) -> datetime:
        return NOW


@dataclass
class CaseResult:
    name: str
    passed: bool
    detail: str


def evidence(*facts: tuple[str, str], fingerprint_value: str = FP_A) -> EvidenceRecord:
    return EvidenceRecord(evidence_id="evidence-1", tenant_id="tenant-1", project_id="project-1", source_reference="evidence://golden", evidence_fingerprint=fingerprint_value, facts=facts)


def context(*, status: str = "APPROVED") -> ApprovedContextRecord:
    return ApprovedContextRecord(context_id="knowledge-1", tenant_id="tenant-1", project_id="project-1", context_kind="KNOWLEDGE", status=status, context_fingerprint=FP_A)


def service(record: EvidenceRecord | None, *, knowledge: tuple[ApprovedContextRecord, ...] = (), store=None):
    source = EvidenceSource(record)
    return ValidationProposalService(evidence=source, context=ContextSource(knowledge=knowledge), rules=DeterministicValidationRules(), store=store or FakeProposalStore(), clock=Clock()), source


def request(name: str, validation_type: ValidationType, *, evidence_ids: tuple[str, ...] = ("evidence-1",), knowledge_ids: tuple[str, ...] = ()) -> ValidationRequest:
    return ValidationRequest.create(validation_id=f"validation-{name}", tenant_id="tenant-1", project_id="project-1", requester_id="engineer-1", validation_type=validation_type, target_identity={"identity": "sensor-1", "kind": "firmware"}, target_fingerprint=FP_A, project_fingerprint=FP_B, evidence_ids=evidence_ids, knowledge_ids=knowledge_ids, requested_at=NOW)


def conflict_flow(name: str, validation_type: ValidationType, facts: tuple[tuple[str, str], ...], *, knowledge=()):
    srv, _ = service(evidence(*facts), knowledge=knowledge)
    result = srv.validate(request(name, validation_type, knowledge_ids=("knowledge-1",) if knowledge else ()))
    finding = result.findings[0]
    proposal = srv.create_proposal(tenant_id="tenant-1", project_id="project-1", finding_id=finding.finding_id)
    return srv, result, finding, proposal


def run_case(name: str, state_dir: Path) -> CaseResult:
    try:
        if name == "firmware_conflict":
            _, result, finding, proposal = conflict_flow(name, ValidationType.CROSS_DOMAIN_CONSISTENCY, (("firmware.sensor_address", "0x69"), ("hardware.sensor_address", "0x68")))
            return CaseResult(name, result.status is ValidationStatus.FAIL and finding.finding_type is FindingType.HARDWARE_FIRMWARE_CONFLICT and proposal.status is ProposalStatus.DRAFT, "FAIL -> finding -> DRAFT")
        if name == "requirement_violation":
            _, result, finding, _ = conflict_flow(name, ValidationType.REQUIREMENT_COMPLIANCE, (("requirement.sample_hz_min", "100"), ("firmware.sample_hz", "50")))
            return CaseResult(name, result.status is ValidationStatus.FAIL and finding.finding_type is FindingType.REQUIREMENT_VIOLATION, "requirement finding")
        if name == "debug_root_cause_candidate":
            _, result, finding, _ = conflict_flow(name, ValidationType.DEBUG_EVIDENCE, (("debug.failure", "HAL_TIMEOUT"), ("debug.call_path", "sensor_read -> HAL_I2C_Master_Transmit")))
            return CaseResult(name, result.status is ValidationStatus.REVIEW_REQUIRED and finding.finding_type is FindingType.DEBUG_ROOT_CAUSE_CANDIDATE, "candidate requires review")
        if name == "optimization_opportunity":
            _, result, finding, proposal = conflict_flow(name, ValidationType.CROSS_DOMAIN_CONSISTENCY, (("optimization.mode", "polling"),), knowledge=(context(),))
            return CaseResult(name, result.status is ValidationStatus.REVIEW_REQUIRED and finding.finding_type is FindingType.OPTIMIZATION_OPPORTUNITY and proposal.proposal_type is ProposalType.OPTIMIZATION, "optimization proposal")
        if name in {"insufficient_evidence", "empty_evidence_gate"}:
            srv, _ = service(evidence())
            result = srv.validate(request(name, ValidationType.CROSS_DOMAIN_CONSISTENCY, evidence_ids=()))
            return CaseResult(name, result.status is ValidationStatus.INSUFFICIENT_EVIDENCE and not result.findings, "no finding or proposal")
        if name == "stale_evidence":
            srv, source = service(evidence(("firmware.sensor_address", "0x69"), ("hardware.sensor_address", "0x68")))
            checked = srv.validate(request(name, ValidationType.CROSS_DOMAIN_CONSISTENCY))
            proposal = srv.create_proposal(tenant_id="tenant-1", project_id="project-1", finding_id=checked.findings[0].finding_id)
            review = srv.submit_review(tenant_id="tenant-1", project_id="project-1", proposal_id=proposal.proposal_id, requester_id="engineer-1")
            source.records[("tenant-1", "project-1", "evidence-1")] = source.records[("tenant-1", "project-1", "evidence-1")].model_copy(update={"evidence_fingerprint": FP_B})
            stale = srv.decide_review(tenant_id="tenant-1", project_id="project-1", review_id=review.review_id, reviewer_id="reviewer-1", decision="APPROVE")
            return CaseResult(name, stale.status == "STALE" and srv.get_proposal("tenant-1", "project-1", proposal.proposal_id).status is ProposalStatus.STALE, "evidence drift is stale")
        if name in {"approval", "rejection", "replay_idempotency"}:
            srv, _, _, proposal = conflict_flow(name, ValidationType.CROSS_DOMAIN_CONSISTENCY, (("firmware.sensor_address", "0x69"), ("hardware.sensor_address", "0x68")))
            review = srv.submit_review(tenant_id="tenant-1", project_id="project-1", proposal_id=proposal.proposal_id, requester_id="engineer-1")
            decision = "REJECT" if name == "rejection" else "APPROVE"
            first = srv.decide_review(tenant_id="tenant-1", project_id="project-1", review_id=review.review_id, reviewer_id="reviewer-1", decision=decision)
            second = srv.decide_review(tenant_id="tenant-1", project_id="project-1", review_id=review.review_id, reviewer_id="reviewer-1", decision=decision) if name == "replay_idempotency" else first
            expected = "REJECTED" if name == "rejection" else "APPROVED"
            return CaseResult(name, first.status == expected and second == first, "review lifecycle")
        if name == "cross_tenant_isolation":
            srv, _, _, proposal = conflict_flow(name, ValidationType.CROSS_DOMAIN_CONSISTENCY, (("firmware.sensor_address", "0x69"), ("hardware.sensor_address", "0x68")))
            return CaseResult(name, srv.get_proposal("tenant-2", "project-1", proposal.proposal_id) is None, "tenant binding")
        if name == "sqlite_restart_recovery":
            database = state_dir / "restart.sqlite3"
            store = SQLiteV64Store(database)
            srv, _ = service(evidence(("firmware.sensor_address", "0x69"), ("hardware.sensor_address", "0x68")), store=store)
            result = srv.validate(request(name, ValidationType.CROSS_DOMAIN_CONSISTENCY))
            proposal = srv.create_proposal(tenant_id="tenant-1", project_id="project-1", finding_id=result.findings[0].finding_id)
            review = srv.submit_review(tenant_id="tenant-1", project_id="project-1", proposal_id=proposal.proposal_id, requester_id="engineer-1")
            restarted = ValidationProposalService(evidence=EvidenceSource(evidence(("firmware.sensor_address", "0x69"), ("hardware.sensor_address", "0x68"))), context=ContextSource(), rules=DeterministicValidationRules(), store=SQLiteV64Store(database), clock=Clock())
            approved = restarted.decide_review(tenant_id="tenant-1", project_id="project-1", review_id=review.review_id, reviewer_id="reviewer-1", decision="APPROVE")
            resumed_again = ValidationProposalService(evidence=EvidenceSource(evidence(("firmware.sensor_address", "0x69"), ("hardware.sensor_address", "0x68"))), context=ContextSource(), rules=DeterministicValidationRules(), store=SQLiteV64Store(database), clock=Clock())
            replay = resumed_again.decide_review(tenant_id="tenant-1", project_id="project-1", review_id=review.review_id, reviewer_id="reviewer-1", decision="APPROVE")
            return CaseResult(name, restarted.get_proposal("tenant-1", "project-1", proposal.proposal_id).status is ProposalStatus.APPROVED and approved == replay, "Runtime A -> B -> C restart/replay")
        if name == "tombstone_fail_closed":
            srv, _, _, proposal = conflict_flow(name, ValidationType.CROSS_DOMAIN_CONSISTENCY, (("firmware.sensor_address", "0x69"), ("hardware.sensor_address", "0x68")))
            srv.tombstone_project("tenant-1", "project-1")
            try:
                srv.get_proposal("tenant-1", "project-1", proposal.proposal_id)
            except ProposalRejected:
                return CaseResult(name, True, "tombstone blocks access")
            return CaseResult(name, False, "tombstone did not block access")
        if name == "approved_no_execution":
            srv, _, _, proposal = conflict_flow(name, ValidationType.CROSS_DOMAIN_CONSISTENCY, (("firmware.sensor_address", "0x69"), ("hardware.sensor_address", "0x68")))
            review = srv.submit_review(tenant_id="tenant-1", project_id="project-1", proposal_id=proposal.proposal_id, requester_id="engineer-1")
            srv.decide_review(tenant_id="tenant-1", project_id="project-1", review_id=review.review_id, reviewer_id="reviewer-1", decision="APPROVE")
            return CaseResult(name, not any(hasattr(srv, item) for item in ("apply", "execute", "build", "flash")), "approved proposal has no execution path")
        if name == "pass_boundary":
            srv, _ = service(evidence(("firmware.sensor_address", "0x68"), ("hardware.sensor_address", "0x68")))
            result = srv.validate(request(name, ValidationType.CROSS_DOMAIN_CONSISTENCY))
            encoded = json.dumps(result.model_dump(mode="json"), ensure_ascii=False).casefold()
            return CaseResult(name, result.status is ValidationStatus.PASS and "hardware verified" not in encoded and "device passed" not in encoded, "PASS is rule-scoped")
        if name == "revoked_context":
            srv, _ = service(evidence(("optimization.mode", "polling"),), knowledge=(context(status="REVOKED"),))
            result = srv.validate(request(name, ValidationType.CROSS_DOMAIN_CONSISTENCY, knowledge_ids=("knowledge-1",)))
            return CaseResult(name, result.status is ValidationStatus.INSUFFICIENT_EVIDENCE and not result.findings, "revoked context rejected")
    except Exception as error:
        return CaseResult(name, False, f"{type(error).__name__}: {error}")
    return CaseResult(name, False, "case not implemented")


def run_golden() -> dict[str, object]:
    fixture = Path(os.getenv("V64_GOLDEN_FIXTURE", str(DEFAULT_FIXTURE)))
    state_dir = Path(os.getenv("V64_GOLDEN_STATE", tempfile.mkdtemp(prefix="v64-golden-")))
    state_dir.mkdir(parents=True, exist_ok=True)
    cases = tuple(json.loads(fixture.read_text(encoding="utf-8"))["cases"])
    results = [run_case(name, state_dir) for name in cases]
    return {"suite": "v6.4", "status": "GOLDEN VERIFIED" if all(item.passed for item in results) else "PARTIAL VERIFIED", "cases": [item.__dict__ for item in results]}


if __name__ == "__main__":
    report = run_golden()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report["status"] == "GOLDEN VERIFIED" else 1)
