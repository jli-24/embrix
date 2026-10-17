from __future__ import annotations

from embedded_copilot.engineering_optimization_loop import (
    OptimizationProposal,
    OptimizationProposalStatus,
    OptimizationTarget,
    ValidationFindingReference,
)
from embedded_copilot.engineering_validation import (
    ValidationFinding,
    ValidationFindingStatus,
    ValidationSeverity,
)

from ..models import PCBAnalysis, PCBFindingSeverity


class DeterministicPCBValidationProjection:
    def submit(self, analysis: PCBAnalysis) -> tuple[ValidationFinding, ...]:
        checked = PCBAnalysis.model_validate(analysis.model_dump(mode="python"))
        severity = {
            PCBFindingSeverity.INFO: ValidationSeverity.INFO,
            PCBFindingSeverity.WARNING: ValidationSeverity.WARNING,
            PCBFindingSeverity.ERROR: ValidationSeverity.ERROR,
            PCBFindingSeverity.CRITICAL: ValidationSeverity.CRITICAL,
        }
        return tuple(
            ValidationFinding.create(
                finding_id=finding.finding_id,
                project_id=checked.project_id,
                rule_id=finding.rule_id,
                category=finding.category.value,
                severity=severity[finding.severity],
                status=ValidationFindingStatus.OPEN,
                message=finding.message,
                evidence_refs=tuple(
                    sorted(
                        {
                            f"knowledge://pcb/{item.evidence_id}"
                            for item in finding.evidence_refs
                        }
                    )
                ),
            )
            for finding in checked.findings
        )


class DeterministicPCBOptimizationProjection:
    def submit(
        self, analysis: PCBAnalysis, findings: tuple[ValidationFinding, ...]
    ) -> tuple[OptimizationProposal, ...]:
        checked = PCBAnalysis.model_validate(analysis.model_dump(mode="python"))
        validated_findings = tuple(
            ValidationFinding.model_validate(item.model_dump(mode="python"))
            for item in findings
        )
        finding_refs = tuple(
            ValidationFindingReference.create(
                finding_id=item.finding_id, fingerprint=item.fingerprint
            )
            for item in validated_findings
        )
        if not checked.proposals:
            return ()
        if not finding_refs:
            raise ValueError("PCB proposals require projected validation findings")
        return tuple(
            OptimizationProposal.create(
                project_id=checked.project_id,
                source_finding_refs=finding_refs,
                target=OptimizationTarget.POWER
                if "POWER" in proposal.rationale.upper()
                else OptimizationTarget.HARDWARE,
                title="PCB engineering improvement proposal",
                rationale=proposal.rationale,
                expected_effects=proposal.recommended_changes,
                evidence_refs=tuple(
                    sorted(
                        {
                            f"knowledge://pcb/{item.evidence_id}"
                            for item in proposal.evidence_refs
                        }
                    )
                ),
                risk_notes=(f"PCB proposal risk is {proposal.risk.value}",),
                status=OptimizationProposalStatus.CREATED,
            )
            for proposal in checked.proposals
        )
