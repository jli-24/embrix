from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime

from .exceptions import ProposalConflict, ProposalRejected, ProposalUnavailable
from .models import (
    ApprovedContextRecord,
    AuditEvent,
    AuditEventType,
    EngineeringFinding,
    EvidenceRecord,
    FindingDraft,
    FindingSeverity,
    FindingType,
    OperationProposal,
    ProposalChange,
    ProposalReviewBinding,
    ProposalStatus,
    ProposalType,
    StatusHistory,
    ValidationRequest,
    ValidationResult,
    ValidationStatus,
    ValidationType,
    canonical_fingerprint,
    identifier,
)
from .ports import (
    ApprovedContextPort,
    ClockPort,
    EvidenceSourcePort,
    ProposalStorePort,
    ValidationRulePort,
)


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _copy_model(value: object, model: type):
    if type(value) is not model:
        raise ProposalRejected("contract type is invalid")
    try:
        return model.model_validate(copy.deepcopy(value.model_dump(mode="python")))
    except Exception as error:
        raise ProposalRejected("contract validation failed") from error


def _evidence_fingerprint(values: tuple[EvidenceRecord, ...]) -> str:
    payload = json.dumps(
        tuple((item.evidence_id, item.evidence_fingerprint) for item in values),
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class ValidationProposalService:
    """Deterministic, read-only v6.4 validation and proposal lifecycle."""

    def __init__(
        self,
        *,
        evidence: EvidenceSourcePort,
        context: ApprovedContextPort,
        rules: ValidationRulePort,
        store: ProposalStorePort,
        clock: ClockPort | None = None,
    ) -> None:
        values = (evidence, context, rules, store)
        ports = (EvidenceSourcePort, ApprovedContextPort, ValidationRulePort, ProposalStorePort)
        if any(not isinstance(value, port) for value, port in zip(values, ports, strict=True)):
            raise TypeError("v6.4 dependency is invalid")
        self._evidence = evidence
        self._context = context
        self._rules = rules
        self._store = store
        self._clock = clock or SystemClock()
        if not isinstance(self._clock, ClockPort):
            raise TypeError("v6.4 clock is invalid")

    def validate(self, request: ValidationRequest) -> ValidationResult:
        checked = _copy_model(request, ValidationRequest)
        self._ensure_project(checked.tenant_id, checked.project_id)
        try:
            stored_request = self._store.save_request(copy.deepcopy(checked))
        except ProposalConflict:
            raise
        except Exception as error:
            raise ProposalUnavailable("validation request persistence unavailable") from error
        if stored_request != checked:
            raise ProposalUnavailable("validation request persistence mismatch")
        self._audit(AuditEventType.VALIDATION_CREATED, checked, checked.fingerprint)

        if not checked.evidence_ids:
            result = self._result(
                checked,
                ValidationStatus.INSUFFICIENT_EVIDENCE,
                (),
                ("no active Engineering Evidence was supplied",),
            )
            self._persist_result(result)
            return result

        try:
            evidence = self._load_evidence(checked)
            memory = self._load_context(checked.tenant_id, checked.project_id, checked.memory_ids, memory=True)
            knowledge = self._load_context(checked.tenant_id, checked.project_id, checked.knowledge_ids, memory=False)
        except ProposalRejected as error:
            result = self._result(checked, ValidationStatus.INSUFFICIENT_EVIDENCE, (), (str(error),))
            self._persist_result(result)
            return result
        except Exception as error:
            result = self._result(checked, ValidationStatus.UNAVAILABLE, (), ("validation source unavailable",))
            self._persist_result(result)
            raise ProposalUnavailable("validation source unavailable") from error

        try:
            drafts = self._rules.evaluate(copy.deepcopy(checked), copy.deepcopy(evidence), copy.deepcopy(memory), copy.deepcopy(knowledge))
            if type(drafts) is not tuple or any(type(item) is not FindingDraft for item in drafts):
                raise TypeError("validation rule returned invalid findings")
        except Exception as error:
            result = self._result(checked, ValidationStatus.UNAVAILABLE, (), ("validation rule unavailable",))
            self._persist_result(result)
            raise ProposalUnavailable("validation rule unavailable") from error

        findings = tuple(
            EngineeringFinding.create(
                finding_id="finding-" + hashlib.sha256(f"{checked.fingerprint}:{index}:{draft.finding_type.value}:{draft.subject}".encode("utf-8")).hexdigest()[:48],
                tenant_id=checked.tenant_id,
                project_id=checked.project_id,
                finding_type=draft.finding_type,
                severity=draft.severity,
                subject=draft.subject,
                summary=draft.summary,
                evidence_ids=checked.evidence_ids,
                memory_ids=checked.memory_ids,
                knowledge_ids=checked.knowledge_ids,
                validation_id=checked.validation_id,
                confidence=draft.confidence,
                limitations=draft.limitations,
                target_identity=checked.target_identity,
                target_fingerprint=checked.target_fingerprint,
                created_at=self._clock.now(),
            )
            for index, draft in enumerate(drafts)
        )
        for finding in findings:
            try:
                stored_finding = self._store.save_finding(copy.deepcopy(finding))
            except ProposalConflict:
                raise
            except Exception as error:
                raise ProposalUnavailable("finding persistence unavailable") from error
            if stored_finding != finding:
                raise ProposalUnavailable("finding persistence mismatch")
            self._audit(AuditEventType.FINDING_CREATED, finding, finding.finding_fingerprint)

        status = ValidationStatus.PASS
        if any(item.finding_type in {FindingType.DEBUG_ROOT_CAUSE_CANDIDATE, FindingType.OPTIMIZATION_OPPORTUNITY} for item in findings):
            status = ValidationStatus.REVIEW_REQUIRED
        elif any(item.severity in {FindingSeverity.ERROR, FindingSeverity.CRITICAL} for item in findings):
            status = ValidationStatus.FAIL
        result = self._result(checked, status, findings, ())
        self._persist_result(result)
        return result

    def create_proposal(
        self,
        *,
        tenant_id: str,
        project_id: str,
        finding_id: str,
        proposed_changes: tuple[ProposalChange, ...] | None = None,
        rationale: str | None = None,
    ) -> OperationProposal:
        self._ensure_project(tenant_id, project_id)
        checked_finding = self._store.get_finding(tenant_id, project_id, identifier(finding_id, field="finding_id"))
        if checked_finding is None:
            raise ProposalRejected("finding not found")
        finding = _copy_model(checked_finding, EngineeringFinding)
        result = self._store.get_result(tenant_id, project_id, finding.validation_id)
        if not isinstance(result, ValidationResult):
            raise ProposalRejected("validation result not found")
        checked_result = _copy_model(result, ValidationResult)
        if checked_result.status not in {ValidationStatus.FAIL, ValidationStatus.REVIEW_REQUIRED}:
            raise ProposalRejected("finding is not proposal eligible")
        current_evidence = self._assert_current_evidence(finding.evidence_ids, tenant_id, project_id)
        proposal_type = {
            FindingType.DEBUG_ROOT_CAUSE_CANDIDATE: ProposalType.REPAIR,
            FindingType.CONFIGURATION_CONFLICT: ProposalType.CONFIGURATION_CHANGE,
            FindingType.HARDWARE_FIRMWARE_CONFLICT: ProposalType.CONFIGURATION_CHANGE,
            FindingType.REQUIREMENT_VIOLATION: ProposalType.REPAIR,
            FindingType.ARCHITECTURE_RISK: ProposalType.ARCHITECTURE_CHANGE,
            FindingType.PCB_CONNECTIVITY_CONFLICT: ProposalType.PCB_CHANGE,
            FindingType.OPTIMIZATION_OPPORTUNITY: ProposalType.OPTIMIZATION,
            FindingType.DEFECT_CANDIDATE: ProposalType.REPAIR,
            FindingType.INSUFFICIENT_EVIDENCE: ProposalType.HARDWARE_REVIEW,
        }[finding.finding_type]
        changes = proposed_changes or (self._default_change(finding),)
        checked_changes = tuple(ProposalChange.model_validate(copy.deepcopy(item.model_dump(mode="python"))) for item in changes)
        proposal_id = "proposal-" + hashlib.sha256(finding.finding_fingerprint.encode("utf-8")).hexdigest()[:48]
        proposal = OperationProposal.create(
            proposal_id=proposal_id,
            tenant_id=tenant_id,
            project_id=project_id,
            proposal_type=proposal_type,
            subject=finding.subject,
            summary=finding.summary,
            rationale=rationale or "Generated from a validated Engineering Finding.",
            finding_ids=(finding.finding_id,),
            evidence_ids=finding.evidence_ids,
            memory_ids=finding.memory_ids,
            knowledge_ids=finding.knowledge_ids,
            validation_ids=(finding.validation_id,),
            target_identity=finding.target_identity,
            target_fingerprint=finding.target_fingerprint,
            project_fingerprint=checked_result.project_fingerprint,
            evidence_fingerprint=_evidence_fingerprint(current_evidence),
            finding_fingerprint=finding.finding_fingerprint,
            validation_fingerprint=checked_result.validation_fingerprint,
            risk="REVIEW_REQUIRED",
            confidence=finding.confidence,
            limitations=finding.limitations,
            proposed_changes=checked_changes,
            validation_plan=("Static source validation", "Configuration consistency", "Runtime validation requires future capability"),
            rollback_plan=("Restore previous target fingerprint/version",),
            review_required=True,
            status=ProposalStatus.DRAFT,
            created_at=self._clock.now(),
        )
        stored = self._store.save_proposal(copy.deepcopy(proposal))
        if stored != proposal:
            raise ProposalUnavailable("proposal persistence mismatch")
        self._history(proposal, from_status=None, to_status=proposal.status.value)
        self._audit(AuditEventType.PROPOSAL_CREATED, proposal, proposal.proposal_fingerprint)
        return stored

    def submit_review(self, *, tenant_id: str, project_id: str, proposal_id: str, requester_id: str) -> ProposalReviewBinding:
        self._ensure_project(tenant_id, project_id)
        proposal = self._store.get_proposal(tenant_id, project_id, identifier(proposal_id, field="proposal_id"))
        if proposal is None:
            raise ProposalRejected("proposal not found")
        checked = _copy_model(proposal, OperationProposal)
        if checked.status is ProposalStatus.WAIT_REVIEW:
            existing = next(
                (item for item in self._store.list_reviews(tenant_id, project_id) if item.proposal_id == checked.proposal_id),
                None,
            )
            if existing is not None:
                return _copy_model(existing, ProposalReviewBinding)
        if checked.status is not ProposalStatus.DRAFT:
            raise ProposalRejected("proposal is not reviewable")
        review_id = "review-" + hashlib.sha256(checked.proposal_fingerprint.encode("utf-8")).hexdigest()[:48]
        binding = ProposalReviewBinding.create(
            review_id=review_id,
            tenant_id=tenant_id,
            project_id=project_id,
            proposal_id=checked.proposal_id,
            proposal_fingerprint=checked.proposal_fingerprint,
            project_fingerprint=checked.project_fingerprint,
            target_identity=checked.target_identity,
            target_fingerprint=checked.target_fingerprint,
            evidence_fingerprint=checked.evidence_fingerprint,
            finding_fingerprint=checked.finding_fingerprint,
            validation_fingerprint=checked.validation_fingerprint,
            requester_id=identifier(requester_id, field="requester_id"),
            created_at=self._clock.now(),
        )
        saved = self._store.save_review(binding)
        updated = checked.with_status(ProposalStatus.WAIT_REVIEW)
        self._store.save_proposal(updated)
        self._history(updated, from_status=ProposalStatus.DRAFT.value, to_status=ProposalStatus.WAIT_REVIEW.value)
        self._audit(AuditEventType.PROPOSAL_SUBMITTED, updated, updated.proposal_fingerprint)
        return saved

    def decide_review(self, *, tenant_id: str, project_id: str, review_id: str, reviewer_id: str, decision: str) -> ProposalReviewBinding:
        self._ensure_project(tenant_id, project_id)
        checked_id = identifier(review_id, field="review_id")
        review = self._store.get_review(tenant_id, project_id, checked_id)
        if review is None:
            raise ProposalRejected("review not found")
        binding = _copy_model(review, ProposalReviewBinding)
        checked_decision = identifier(decision, field="decision")
        if checked_decision not in {"APPROVE", "REJECT"}:
            raise ProposalRejected("review decision is invalid")
        if binding.status != "PENDING":
            if binding.reviewer_id == reviewer_id and binding.decision == checked_decision:
                return binding
            raise ProposalConflict("review replay or reuse rejected")
        proposal = self._store.get_proposal(tenant_id, project_id, binding.proposal_id)
        if proposal is None:
            raise ProposalRejected("proposal not found")
        checked_proposal = _copy_model(proposal, OperationProposal)
        if checked_proposal.proposal_fingerprint != binding.proposal_fingerprint or checked_proposal.status is not ProposalStatus.WAIT_REVIEW:
            return self._mark_stale(binding, checked_proposal)
        try:
            current_evidence = self._assert_current_evidence(checked_proposal.evidence_ids, tenant_id, project_id)
            if _evidence_fingerprint(current_evidence) != checked_proposal.evidence_fingerprint:
                return self._mark_stale(binding, checked_proposal)
        except ProposalRejected:
            return self._mark_stale(binding, checked_proposal)
        status = ProposalStatus.APPROVED if checked_decision == "APPROVE" else ProposalStatus.REJECTED
        updated_proposal = checked_proposal.with_status(status)
        self._store.save_proposal(updated_proposal)
        binding_values = binding.model_dump(mode="python")
        binding_values.pop("fingerprint", None)
        binding_values.update(
            reviewer_id=identifier(reviewer_id, field="reviewer_id"),
            decision=checked_decision,
            status=status.value,
            decided_at=self._clock.now(),
        )
        decided = ProposalReviewBinding.create(**binding_values)
        saved = self._store.save_review(decided)
        self._history(updated_proposal, from_status=ProposalStatus.WAIT_REVIEW.value, to_status=status.value, actor_id=reviewer_id)
        self._audit(AuditEventType.PROPOSAL_APPROVED if status is ProposalStatus.APPROVED else AuditEventType.PROPOSAL_REJECTED, updated_proposal, updated_proposal.proposal_fingerprint)
        return saved

    def get_proposal(self, tenant_id: str, project_id: str, proposal_id: str) -> OperationProposal | None:
        self._ensure_project(tenant_id, project_id)
        value = self._store.get_proposal(tenant_id, project_id, proposal_id)
        return None if value is None else _copy_model(value, OperationProposal)

    def list_proposals(self, tenant_id: str, project_id: str, *, limit: int | None = None) -> tuple[OperationProposal, ...]:
        self._ensure_project(tenant_id, project_id)
        return tuple(_copy_model(item, OperationProposal) for item in self._store.list_proposals(tenant_id, project_id, limit=limit))

    def get_validation(self, tenant_id: str, project_id: str, validation_id: str) -> ValidationResult | None:
        self._ensure_project(tenant_id, project_id)
        value = self._store.get_result(tenant_id, project_id, validation_id)
        return None if value is None else _copy_model(value, ValidationResult)

    def list_validations(self, tenant_id: str, project_id: str, *, limit: int | None = None) -> tuple[ValidationResult, ...]:
        self._ensure_project(tenant_id, project_id)
        results = []
        for request in self._store.list_requests(tenant_id, project_id, limit=limit):
            result = self._store.get_result(tenant_id, project_id, request.validation_id)
            if result is not None:
                results.append(_copy_model(result, ValidationResult))
        return tuple(results)

    def get_review(self, tenant_id: str, project_id: str, review_id: str) -> ProposalReviewBinding | None:
        self._ensure_project(tenant_id, project_id)
        value = self._store.get_review(tenant_id, project_id, review_id)
        return None if value is None else _copy_model(value, ProposalReviewBinding)

    def list_reviews(self, tenant_id: str, project_id: str, *, limit: int | None = None) -> tuple[ProposalReviewBinding, ...]:
        self._ensure_project(tenant_id, project_id)
        return tuple(_copy_model(item, ProposalReviewBinding) for item in self._store.list_reviews(tenant_id, project_id, limit=limit))

    def list_findings(self, tenant_id: str, project_id: str, *, limit: int | None = None) -> tuple[EngineeringFinding, ...]:
        self._ensure_project(tenant_id, project_id)
        return tuple(_copy_model(item, EngineeringFinding) for item in self._store.list_findings(tenant_id, project_id, limit=limit))

    def _ensure_project(self, tenant_id: str, project_id: str) -> None:
        if self._store.is_project_tombstoned(tenant_id, project_id):
            raise ProposalRejected("project is tombstoned")

    def tombstone_project(self, tenant_id: str, project_id: str) -> None:
        self._store.tombstone_project(tenant_id, project_id, self._clock.now().isoformat())

    def _load_evidence(self, request: ValidationRequest) -> tuple[EvidenceRecord, ...]:
        values = []
        for evidence_id in request.evidence_ids:
            raw = self._evidence.get_evidence(request.tenant_id, request.project_id, evidence_id)
            if raw is None:
                raise ProposalRejected("evidence is missing")
            value = _copy_model(raw, EvidenceRecord)
            if value.tenant_id != request.tenant_id or value.project_id != request.project_id or value.status != "ACTIVE":
                raise ProposalRejected("evidence is not active")
            values.append(value)
        return tuple(values)

    def _load_context(self, tenant_id: str, project_id: str, ids: tuple[str, ...], *, memory: bool) -> tuple[ApprovedContextRecord, ...]:
        values = []
        for context_id in ids:
            raw = self._context.get_memory(tenant_id, project_id, context_id) if memory else self._context.get_knowledge(tenant_id, project_id, context_id)
            if raw is None:
                raise ProposalRejected("approved context is missing")
            value = _copy_model(raw, ApprovedContextRecord)
            if value.tenant_id != tenant_id or value.project_id != project_id or value.status not in {"APPROVED", "ACTIVE"}:
                raise ProposalRejected("context is not approved")
            values.append(value)
        return tuple(values)

    def _assert_current_evidence(self, evidence_ids: tuple[str, ...], tenant_id: str, project_id: str) -> tuple[EvidenceRecord, ...]:
        return self._load_evidence(ValidationRequest.create(
            validation_id="revalidate",
            tenant_id=tenant_id,
            project_id=project_id,
            requester_id="revalidator",
            validation_type=ValidationType.CROSS_DOMAIN_CONSISTENCY,
            target_identity={"identity": "proposal", "kind": "proposal"},
            target_fingerprint="sha256:" + "0" * 64,
            project_fingerprint="sha256:" + "0" * 64,
            evidence_ids=evidence_ids,
            requested_at=self._clock.now(),
        ))

    def _result(self, request: ValidationRequest, status: ValidationStatus, findings: tuple[EngineeringFinding, ...], limitations: tuple[str, ...]) -> ValidationResult:
        return ValidationResult.create(
            validation_id=request.validation_id,
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            validation_type=request.validation_type,
            status=status,
            evidence_ids=request.evidence_ids,
            memory_ids=request.memory_ids,
            knowledge_ids=request.knowledge_ids,
            findings=findings,
            target_identity=request.target_identity,
            target_fingerprint=request.target_fingerprint,
            project_fingerprint=request.project_fingerprint,
            limitations=limitations,
            validated_at=self._clock.now(),
        )

    def _persist_result(self, result: ValidationResult) -> None:
        stored = self._store.save_result(copy.deepcopy(result))
        if stored != result:
            raise ProposalUnavailable("validation result persistence mismatch")
        self._audit(AuditEventType.VALIDATION_COMPLETED, result, result.validation_fingerprint)

    def _default_change(self, finding: EngineeringFinding) -> ProposalChange:
        return ProposalChange(
            change_kind="DESCRIPTIVE",
            logical_change=f"Review and address finding: {finding.summary}",
            expected_effect="Resolve or further validate the current Engineering Finding.",
        )

    def _mark_stale(self, binding: ProposalReviewBinding, proposal: OperationProposal) -> ProposalReviewBinding:
        stale_proposal = proposal.with_status(ProposalStatus.STALE)
        if proposal.status is not ProposalStatus.STALE:
            self._store.save_proposal(stale_proposal)
            self._history(stale_proposal, from_status=proposal.status.value, to_status=ProposalStatus.STALE.value)
        binding_values = binding.model_dump(mode="python")
        binding_values.pop("fingerprint", None)
        binding_values.pop("status", None)
        stale = ProposalReviewBinding.create(
            **binding_values,
            status="STALE",
        )
        saved = self._store.save_review(stale)
        self._audit(AuditEventType.PROPOSAL_STALE, stale_proposal, proposal.proposal_fingerprint)
        return saved

    def _history(self, value: OperationProposal, *, from_status: str | None, to_status: str, actor_id: str = "system-v64") -> None:
        history = StatusHistory(
            history_id="history-" + hashlib.sha256(f"{value.proposal_id}:{from_status}:{to_status}:{value.proposal_fingerprint}".encode("utf-8")).hexdigest()[:48],
            tenant_id=value.tenant_id,
            project_id=value.project_id,
            object_type="OPERATION_PROPOSAL",
            object_id=value.proposal_id,
            from_status=from_status,
            to_status=to_status,
            actor_id=identifier(actor_id, field="actor_id"),
            object_fingerprint=value.proposal_fingerprint,
            occurred_at=self._clock.now(),
        )
        self._store.append_history(history)

    def _audit(self, event_type: AuditEventType, value: object, object_fingerprint: str) -> None:
        event = AuditEvent(
            event_id="audit-" + hashlib.sha256(f"{event_type.value}:{getattr(value, 'project_id')}:{getattr(value, 'proposal_id', getattr(value, 'validation_id', getattr(value, 'finding_id', 'object')))}:{object_fingerprint}".encode("utf-8")).hexdigest()[:48],
            event_type=event_type,
            tenant_id=getattr(value, "tenant_id"),
            project_id=getattr(value, "project_id"),
            object_type=type(value).__name__,
            object_id=getattr(value, "proposal_id", getattr(value, "validation_id", getattr(value, "finding_id", "object"))),
            object_fingerprint=object_fingerprint,
            occurred_at=self._clock.now(),
        )
        self._store.append_audit(event)


class DeterministicValidationRules:
    """Small parser-backed rule adapter used by production composition and Golden tests."""

    def evaluate(self, request, evidence, memory, knowledge):
        facts = {key: value for item in evidence for key, value in item.facts}
        drafts: list[FindingDraft] = []
        firmware_address = facts.get("firmware.sensor_address")
        hardware_address = facts.get("hardware.sensor_address")
        if firmware_address is not None and hardware_address is not None and firmware_address != hardware_address:
            drafts.append(FindingDraft(
                finding_type=FindingType.HARDWARE_FIRMWARE_CONFLICT,
                severity=FindingSeverity.ERROR,
                subject="sensor address",
                summary=f"Firmware address {firmware_address} conflicts with approved hardware address {hardware_address}.",
                confidence=1.0,
            ))
        required = facts.get("requirement.sample_hz_min")
        actual = facts.get("firmware.sample_hz")
        if required is not None and actual is not None:
            try:
                if float(actual) < float(required):
                    drafts.append(FindingDraft(
                        finding_type=FindingType.REQUIREMENT_VIOLATION,
                        severity=FindingSeverity.ERROR,
                        subject="sample rate",
                        summary=f"Firmware sample rate {actual}Hz is below the requirement {required}Hz.",
                        confidence=1.0,
                    ))
            except ValueError:
                pass
        if request.validation_type is ValidationType.DEBUG_EVIDENCE and facts.get("debug.failure") and facts.get("debug.call_path"):
            drafts.append(FindingDraft(
                finding_type=FindingType.DEBUG_ROOT_CAUSE_CANDIDATE,
                severity=FindingSeverity.WARNING,
                subject="debug root cause candidate",
                summary=f"{facts['debug.call_path']} is correlated with {facts['debug.failure']}.",
                confidence=0.55,
                limitations=("Correlation is not a unique root cause.", "Runtime reproduction and register evidence are required."),
            ))
        if request.validation_type is ValidationType.CROSS_DOMAIN_CONSISTENCY and facts.get("optimization.mode") == "polling" and knowledge:
            drafts.append(FindingDraft(
                finding_type=FindingType.OPTIMIZATION_OPPORTUNITY,
                severity=FindingSeverity.INFO,
                subject="transfer scheduling",
                summary="Polling transfer may be reviewed against approved DMA suitability knowledge.",
                confidence=0.7,
                limitations=("Performance benefit is unverified without a benchmark.",),
            ))
        return tuple(drafts)


__all__ = ("DeterministicValidationRules", "SystemClock", "ValidationProposalService")
