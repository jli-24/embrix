from __future__ import annotations

import copy

from .contracts import (
    validate_approved_knowledge,
    validate_finding,
    validate_proposal,
    validate_report,
    validate_request,
)
from .exceptions import (
    OptimizationConflict,
    OptimizationRejected,
    OptimizationUnavailable,
)
from .models import (
    OptimizationProposal,
    OptimizationProposalStatus,
    OptimizationRequest,
)
from .ports import (
    ApprovedKnowledgeSourcePort,
    OptimizationRulePort,
    OptimizationStorePort,
    ValidationFindingSourcePort,
)


class OptimizationService:
    def __init__(
        self,
        *,
        validation_source: ValidationFindingSourcePort,
        knowledge_source: ApprovedKnowledgeSourcePort,
        rule: OptimizationRulePort,
        store: OptimizationStorePort,
    ) -> None:
        if not isinstance(validation_source, ValidationFindingSourcePort):
            raise TypeError("validation source is invalid")
        if not isinstance(knowledge_source, ApprovedKnowledgeSourcePort):
            raise TypeError("knowledge source is invalid")
        if not isinstance(rule, OptimizationRulePort):
            raise TypeError("optimization rule is invalid")
        if not isinstance(store, OptimizationStorePort):
            raise TypeError("optimization store is invalid")
        self._validation_source = validation_source
        self._knowledge_source = knowledge_source
        self._rule = rule
        self._store = store

    def generate(
        self, request: OptimizationRequest
    ) -> tuple[OptimizationProposal, ...]:
        try:
            checked_request = validate_request(request)
        except (TypeError, ValueError) as error:
            raise OptimizationRejected("optimization request rejected") from error

        report = self._load_report(checked_request)
        findings = self._load_findings(checked_request, report)
        approved_references = self._load_knowledge(checked_request)
        proposals = self._generate_proposals(checked_request, findings)
        checked_proposals = self._bind_proposals(
            checked_request,
            findings,
            approved_references,
            proposals,
        )
        return self._save_proposals(checked_proposals)

    def _load_report(self, request: OptimizationRequest):
        try:
            raw_report = self._validation_source.get_report(
                copy.deepcopy(request.project_id),
                copy.deepcopy(request.validation_report_id),
            )
        except Exception as error:
            raise OptimizationUnavailable("validation source unavailable") from error
        if raw_report is None:
            raise OptimizationRejected("validation report not found")
        try:
            report = validate_report(raw_report)
        except (TypeError, ValueError) as error:
            raise OptimizationUnavailable("validation source returned invalid report") from error
        if (
            report.project_id != request.project_id
            or report.report_id != request.validation_report_id
        ):
            raise OptimizationRejected("validation report binding mismatch")
        return report

    def _load_findings(self, request: OptimizationRequest, report):
        requested_ids = tuple(item.finding_id for item in request.finding_refs)
        report_by_id = {item.finding_id: item for item in report.findings}
        for reference in request.finding_refs:
            report_finding = report_by_id.get(reference.finding_id)
            if (
                report_finding is None
                or report_finding.project_id != request.project_id
                or report_finding.fingerprint != reference.fingerprint
            ):
                raise OptimizationRejected("report finding binding mismatch")

        try:
            raw_findings = self._validation_source.get_findings(
                copy.deepcopy(request.project_id), copy.deepcopy(requested_ids)
            )
        except Exception as error:
            raise OptimizationUnavailable("validation source unavailable") from error
        if type(raw_findings) is not tuple:
            raise OptimizationUnavailable("validation source returned invalid findings")
        try:
            findings = tuple(validate_finding(item) for item in raw_findings)
        except (TypeError, ValueError) as error:
            raise OptimizationUnavailable("validation source returned invalid findings") from error

        returned_ids = tuple(item.finding_id for item in findings)
        if len(returned_ids) != len(set(returned_ids)) or set(returned_ids) != set(
            requested_ids
        ):
            raise OptimizationRejected("validation finding set mismatch")
        by_id = {item.finding_id: item for item in findings}
        ordered = tuple(by_id[item] for item in requested_ids)
        for reference, value in zip(request.finding_refs, ordered, strict=True):
            if (
                value.project_id != request.project_id
                or value.fingerprint != reference.fingerprint
                or value != report_by_id[reference.finding_id]
            ):
                raise OptimizationRejected("validation finding binding mismatch")
        return ordered

    def _load_knowledge(self, request: OptimizationRequest) -> frozenset[str]:
        validated: set[str] = set()
        for reference in request.knowledge_refs:
            try:
                raw_knowledge = self._knowledge_source.get_approved_knowledge(
                    copy.deepcopy(request.project_id),
                    copy.deepcopy(reference.knowledge_id),
                )
            except Exception as error:
                raise OptimizationUnavailable("knowledge source unavailable") from error
            if raw_knowledge is None:
                raise OptimizationRejected("approved knowledge not found")
            try:
                knowledge = validate_approved_knowledge(raw_knowledge)
            except (TypeError, ValueError) as error:
                raise OptimizationUnavailable(
                    "knowledge source returned invalid knowledge"
                ) from error
            if (
                knowledge.project_id != request.project_id
                or knowledge.knowledge_id != reference.knowledge_id
                or knowledge.fingerprint != reference.fingerprint
            ):
                raise OptimizationRejected("approved knowledge binding mismatch")
            validated.add(reference.reference)
        return frozenset(validated)

    def _generate_proposals(self, request, findings):
        try:
            raw_proposals = self._rule.generate(
                validate_request(request),
                tuple(validate_finding(item) for item in findings),
            )
        except Exception as error:
            raise OptimizationUnavailable("optimization rule unavailable") from error
        if type(raw_proposals) is not tuple:
            raise OptimizationUnavailable("optimization rule returned invalid output")
        try:
            return tuple(validate_proposal(item) for item in raw_proposals)
        except (TypeError, ValueError) as error:
            raise OptimizationUnavailable("optimization rule returned invalid output") from error

    def _bind_proposals(
        self,
        request,
        findings,
        approved_references,
        proposals,
    ) -> tuple[OptimizationProposal, ...]:
        finding_by_identity = {
            (item.finding_id, item.fingerprint): item for item in findings
        }
        ids: set[str] = set()
        fingerprints: set[str] = set()
        for proposal in proposals:
            if (
                proposal.project_id != request.project_id
                or proposal.status is not OptimizationProposalStatus.CREATED
                or proposal.target not in request.optimization_scope
            ):
                raise OptimizationRejected("optimization proposal boundary mismatch")
            source_findings = []
            for reference in proposal.source_finding_refs:
                value = finding_by_identity.get(
                    (reference.finding_id, reference.fingerprint)
                )
                if value is None:
                    raise OptimizationRejected("proposal finding binding mismatch")
                source_findings.append(value)
            allowed_evidence = set(approved_references)
            for value in source_findings:
                allowed_evidence.update(value.evidence_refs)
            if not set(proposal.evidence_refs).issubset(allowed_evidence):
                raise OptimizationRejected("proposal evidence binding mismatch")
            if proposal.proposal_id in ids or proposal.fingerprint in fingerprints:
                raise OptimizationRejected("duplicate optimization proposal identity")
            ids.add(proposal.proposal_id)
            fingerprints.add(proposal.fingerprint)
        return tuple(sorted(proposals, key=lambda item: item.proposal_id))

    def _save_proposals(
        self, proposals: tuple[OptimizationProposal, ...]
    ) -> tuple[OptimizationProposal, ...]:
        saved: list[OptimizationProposal] = []
        for proposal in proposals:
            try:
                returned = self._store.save_proposal(validate_proposal(proposal))
            except OptimizationConflict:
                raise
            except Exception as error:
                raise OptimizationUnavailable("optimization store unavailable") from error
            try:
                checked = validate_proposal(returned)
            except (TypeError, ValueError) as error:
                raise OptimizationUnavailable(
                    "optimization store returned invalid proposal"
                ) from error
            if checked != proposal:
                raise OptimizationUnavailable("optimization store changed proposal")
            saved.append(checked)
        return tuple(saved)


__all__ = ("OptimizationService",)

