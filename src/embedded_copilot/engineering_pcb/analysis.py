from __future__ import annotations

from .models import (
    EngineeringPCBGraph,
    PCBAnalysis,
    PCBAnalysisRequest,
    PCBAnalysisStatus,
    PCBContext,
    PCBEvidenceClaim,
    PCBFinding,
    PCBFindingCategory,
    PCBFindingSeverity,
    PCBNetType,
    PCBObservation,
    PCBProposal,
    PCBProposalRisk,
    PCBProposalType,
    deterministic_id,
)


class DeterministicPCBAnalyzer:
    _RULES = frozenset(
        {"POWER_NETS", "GROUND_NETS", "FLOATING_PINS", "DATASHEET_COVERAGE"}
    )

    def analyze(
        self,
        request: PCBAnalysisRequest,
        context: PCBContext,
        graph: EngineeringPCBGraph,
    ) -> PCBAnalysis:
        checked_request = PCBAnalysisRequest.model_validate(
            request.model_dump(mode="python")
        )
        checked_context = PCBContext.model_validate(context.model_dump(mode="python"))
        checked_graph = EngineeringPCBGraph.model_validate(
            graph.model_dump(mode="python")
        )
        selected = set(checked_request.rule_scope)
        if not selected or not selected.issubset(self._RULES):
            raise ValueError("analysis rule_scope is empty or unsupported")

        observations: list[PCBObservation] = []
        findings: list[PCBFinding] = []

        def observe(rule_id: str, message: str, affected: tuple[str, ...], evidence):
            observations.append(
                PCBObservation.create(
                    observation_id=deterministic_id(
                        "observation", checked_request.analysis_id, rule_id, *affected
                    ),
                    rule_id=rule_id,
                    claim=PCBEvidenceClaim.OBSERVED,
                    message=message,
                    affected_ids=affected,
                    evidence_refs=tuple(evidence),
                )
            )

        def find(
            rule_id: str,
            category: PCBFindingCategory,
            severity: PCBFindingSeverity,
            message: str,
            affected: tuple[str, ...],
            evidence,
        ):
            findings.append(
                PCBFinding.create(
                    finding_id=deterministic_id(
                        "pcb-finding", checked_request.analysis_id, rule_id, *affected
                    ),
                    project_id=checked_request.project_id,
                    rule_id=rule_id,
                    category=category,
                    severity=severity,
                    message=message,
                    affected_ids=affected,
                    evidence_refs=tuple(evidence),
                )
            )

        power = tuple(
            net for net in checked_context.nets if net.net_type is PCBNetType.POWER
        )
        ground = tuple(
            net for net in checked_context.nets if net.net_type is PCBNetType.GROUND
        )
        if "POWER_NETS" in selected:
            if power:
                observe(
                    "POWER_NETS",
                    "Explicit power nets are present",
                    tuple(net.net_id for net in power),
                    tuple(ref for net in power for ref in net.evidence_refs),
                )
            else:
                find(
                    "POWER_NETS",
                    PCBFindingCategory.POWER,
                    PCBFindingSeverity.ERROR,
                    "No explicit power net is present",
                    (checked_context.board.artifact.artifact_id,),
                    checked_context.provenance,
                )
        if "GROUND_NETS" in selected:
            if ground:
                observe(
                    "GROUND_NETS",
                    "Explicit ground nets are present",
                    tuple(net.net_id for net in ground),
                    tuple(ref for net in ground for ref in net.evidence_refs),
                )
            else:
                find(
                    "GROUND_NETS",
                    PCBFindingCategory.POWER,
                    PCBFindingSeverity.ERROR,
                    "No explicit ground net is present",
                    (checked_context.board.artifact.artifact_id,),
                    checked_context.provenance,
                )
        if "FLOATING_PINS" in selected:
            connected = {
                (item.component_reference, item.pin_number)
                for item in checked_context.connections
            }
            for component in checked_context.components:
                for pin in component.pins:
                    if (
                        not pin.no_connect
                        and (component.reference, pin.pin_number) not in connected
                    ):
                        find(
                            "FLOATING_PINS",
                            PCBFindingCategory.CONNECTIVITY,
                            PCBFindingSeverity.WARNING,
                            "A declared pin has no netlist connection",
                            (component.reference, pin.pin_number),
                            pin.evidence_refs,
                        )
        if "DATASHEET_COVERAGE" in selected:
            for datasheet in checked_context.datasheets:
                observe(
                    "DATASHEET_COVERAGE",
                    "Cited datasheet constraints are available",
                    (datasheet.component_reference,),
                    datasheet.evidence_refs,
                )

        proposals = tuple(
            PCBProposal.create(
                proposal_id=deterministic_id("pcb-proposal", finding.fingerprint),
                project_id=checked_request.project_id,
                finding_fingerprints=(finding.fingerprint,),
                target_artifact_fingerprints=tuple(
                    item.fingerprint for item in checked_context.artifacts
                ),
                proposal_type=PCBProposalType.COMPONENT_REVIEW
                if finding.category is PCBFindingCategory.CONNECTIVITY
                else PCBProposalType.LAYOUT_REVIEW,
                recommended_changes=(
                    "Review the cited PCB evidence and prepare a human-approved engineering change proposal",
                ),
                rationale=finding.message,
                risk=PCBProposalRisk.HIGH
                if finding.severity
                in {PCBFindingSeverity.ERROR, PCBFindingSeverity.CRITICAL}
                else PCBProposalRisk.MEDIUM,
                evidence_refs=finding.evidence_refs,
            )
            for finding in findings
        )
        return PCBAnalysis.create(
            analysis_id=checked_request.analysis_id,
            project_id=checked_request.project_id,
            context_fingerprint=checked_context.fingerprint,
            graph_fingerprint=checked_graph.fingerprint,
            engineering_context_fingerprint=checked_context.engineering_context_fingerprint,
            observations=tuple(
                sorted(observations, key=lambda item: item.observation_id)
            ),
            findings=tuple(sorted(findings, key=lambda item: item.finding_id)),
            proposals=tuple(sorted(proposals, key=lambda item: item.proposal_id)),
            evidence_refs=checked_context.provenance,
            status=PCBAnalysisStatus.COMPLETE,
        )
