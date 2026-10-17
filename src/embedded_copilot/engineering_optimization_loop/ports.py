from __future__ import annotations

from typing import Protocol, runtime_checkable

from embedded_copilot.engineering_governance import ApprovedKnowledge
from embedded_copilot.engineering_validation import ValidationFinding, ValidationReport

from .models import OptimizationProposal, OptimizationRequest


@runtime_checkable
class ValidationFindingSourcePort(Protocol):
    def get_report(
        self, project_id: str, report_id: str
    ) -> ValidationReport | None: ...

    def get_findings(
        self, project_id: str, finding_ids: tuple[str, ...]
    ) -> tuple[ValidationFinding, ...]: ...


@runtime_checkable
class ApprovedKnowledgeSourcePort(Protocol):
    def get_approved_knowledge(
        self, project_id: str, knowledge_id: str
    ) -> ApprovedKnowledge | None: ...


@runtime_checkable
class OptimizationRulePort(Protocol):
    def generate(
        self,
        request: OptimizationRequest,
        findings: tuple[ValidationFinding, ...],
    ) -> tuple[OptimizationProposal, ...]: ...


@runtime_checkable
class OptimizationStorePort(Protocol):
    def save_proposal(
        self, proposal: OptimizationProposal
    ) -> OptimizationProposal: ...

    def get_proposal(
        self, project_id: str, proposal_id: str
    ) -> OptimizationProposal | None: ...


__all__ = (
    "ApprovedKnowledgeSourcePort",
    "OptimizationRulePort",
    "OptimizationStorePort",
    "ValidationFindingSourcePort",
)

