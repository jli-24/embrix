from __future__ import annotations

import copy

from embedded_copilot.engineering_validation import ValidationFinding

from ..contracts import (
    validate_finding,
    validate_project_id,
    validate_proposal,
    validate_proposal_id,
    validate_request,
)
from ..exceptions import OptimizationConflict
from ..models import (
    OptimizationProposal,
    OptimizationProposalStatus,
    OptimizationRequest,
    OptimizationTarget,
    ValidationFindingReference,
)
from ..ports import OptimizationRulePort, OptimizationStorePort


class FakeOptimizationRuleAdapter(OptimizationRulePort):
    def generate(
        self,
        request: OptimizationRequest,
        findings: tuple[ValidationFinding, ...],
    ) -> tuple[OptimizationProposal, ...]:
        checked_request = validate_request(request)
        if type(findings) is not tuple:
            raise TypeError("findings are invalid")
        checked_findings = tuple(validate_finding(item) for item in findings)
        if OptimizationTarget.HARDWARE not in checked_request.optimization_scope:
            return ()

        requested = {
            (item.finding_id, item.fingerprint) for item in checked_request.finding_refs
        }
        proposals: list[OptimizationProposal] = []
        for finding in checked_findings:
            identity = (finding.finding_id, finding.fingerprint)
            if (
                identity not in requested
                or finding.project_id != checked_request.project_id
                or finding.rule_id != "POWER-001"
                or finding.message != "possible voltage mismatch"
            ):
                continue
            proposals.append(
                OptimizationProposal.create(
                    project_id=checked_request.project_id,
                    source_finding_refs=(
                        ValidationFindingReference.create(
                            finding_id=finding.finding_id,
                            fingerprint=finding.fingerprint,
                        ),
                    ),
                    target=OptimizationTarget.HARDWARE,
                    title="Review power decoupling design",
                    rationale=(
                        "Power validation indicates possible voltage mismatch."
                    ),
                    expected_effects=("Improve power stability.",),
                    evidence_refs=finding.evidence_refs,
                    risk_notes=("Requires engineer review.",),
                    status=OptimizationProposalStatus.CREATED,
                )
            )
        return tuple(
            validate_proposal(item)
            for item in sorted(proposals, key=lambda value: value.proposal_id)
        )


class FakeOptimizationStoreAdapter(OptimizationStorePort):
    def __init__(self) -> None:
        self._values: dict[tuple[str, str], OptimizationProposal] = {}

    def save_proposal(
        self, proposal: OptimizationProposal
    ) -> OptimizationProposal:
        try:
            checked = validate_proposal(proposal)
        except (TypeError, ValueError) as error:
            if type(proposal) is OptimizationProposal:
                try:
                    key = (
                        validate_project_id(proposal.project_id),
                        validate_proposal_id(proposal.proposal_id),
                    )
                except (TypeError, ValueError):
                    raise error
                if key in self._values:
                    raise OptimizationConflict("proposal identity conflict") from error
            raise
        key = (checked.project_id, checked.proposal_id)
        existing = self._values.get(key)
        if existing is not None and existing != checked:
            raise OptimizationConflict("proposal identity conflict")
        if existing is None:
            self._values[key] = checked
        return validate_proposal(self._values[key])

    def get_proposal(
        self, project_id: str, proposal_id: str
    ) -> OptimizationProposal | None:
        key = (
            validate_project_id(project_id),
            validate_proposal_id(proposal_id),
        )
        value = self._values.get(key)
        return None if value is None else validate_proposal(copy.deepcopy(value))

    @property
    def proposal_count(self) -> int:
        return len(self._values)


__all__ = ("FakeOptimizationRuleAdapter", "FakeOptimizationStoreAdapter")
