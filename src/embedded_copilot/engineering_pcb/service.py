from __future__ import annotations

from embedded_copilot.engineering_context import (
    EngineeringContextRequest,
    EngineeringContextSnapshot,
)
from embedded_copilot.engineering_review import (
    ReviewDecision,
    ReviewRecord,
    ReviewTargetType,
)

from .exceptions import (
    PCBArtifactNotFound,
    PCBGovernanceError,
    PCBModelConflict,
    PCBReviewRejected,
    PCBUnavailable,
)
from .models import (
    PCBAnalysis,
    PCBAnalysisRequest,
    PCBArtifactType,
    PCBBoardArtifact,
    PCBBOMArtifact,
    PCBComponent,
    PCBContext,
    PCBContextRequest,
    PCBEvidenceClaim,
    PCBEvidenceReference,
    PCBEvidenceSource,
    PCBGovernanceBinding,
    PCBGovernanceBundle,
    PCBInterface,
    PCBNetlistArtifact,
    PCBPopulation,
    PCBRepairHandoff,
    PCBRepairHandoffReceipt,
    PCBSchematicArtifact,
    deterministic_id,
)
from .ports import (
    PCBAnalysisPort,
    PCBArtifactSourcePort,
    PCBDatasheetSourcePort,
    PCBEngineeringContextSourcePort,
    PCBGraphPort,
    PCBOptimizationPort,
    PCBParserPort,
    PCBRepairPort,
    PCBValidationPort,
)


class EngineeringPCBService:
    def __init__(
        self,
        *,
        artifact_source: PCBArtifactSourcePort,
        parser: PCBParserPort,
        graph: PCBGraphPort,
        analyzer: PCBAnalysisPort,
        context_source: PCBEngineeringContextSourcePort,
        datasheet_source: PCBDatasheetSourcePort,
        validation: PCBValidationPort,
        optimization: PCBOptimizationPort,
        repair: PCBRepairPort,
    ) -> None:
        self._artifact_source = artifact_source
        self._parser = parser
        self._graph = graph
        self._analyzer = analyzer
        self._context_source = context_source
        self._datasheet_source = datasheet_source
        self._validation = validation
        self._optimization = optimization
        self._repair = repair

    def build_context(self, request: PCBContextRequest) -> PCBContext:
        checked = PCBContextRequest.model_validate(request.model_dump(mode="python"))
        snapshot = self._context_source.get_context(
            EngineeringContextRequest(
                project_id=checked.project_id,
                task=checked.engineering_task,
                constraints=(),
            )
        )
        if snapshot is None:
            raise PCBUnavailable("engineering context is unavailable")
        snapshot = EngineeringContextSnapshot.model_validate(
            snapshot.model_dump(mode="python")
        )
        if snapshot.project_id != checked.project_id:
            raise PCBModelConflict("engineering context project mismatch")

        parsed = {}
        for reference in checked.artifacts:
            current = self._artifact_source.get_artifact(
                checked.project_id, reference.artifact_id
            )
            if current is None:
                raise PCBArtifactNotFound("PCB artifact is unavailable")
            current = type(reference).model_validate(current.model_dump(mode="python"))
            if current != reference:
                raise PCBModelConflict("PCB artifact identity changed")
            result = self._parser.parse(current)
            if result.artifact != current:
                raise PCBModelConflict("parser returned an incorrectly bound artifact")
            parsed[current.artifact_type] = result

        board = parsed.get(PCBArtifactType.BOARD)
        schematic = parsed.get(PCBArtifactType.SCHEMATIC)
        bom = parsed.get(PCBArtifactType.BOM)
        netlist = parsed.get(PCBArtifactType.NETLIST)
        if (
            not isinstance(board, PCBBoardArtifact)
            or not isinstance(schematic, PCBSchematicArtifact)
            or not isinstance(bom, PCBBOMArtifact)
            or not isinstance(netlist, PCBNetlistArtifact)
        ):
            raise PCBModelConflict(
                "parser result type does not match the artifact type"
            )

        components = self._merge_and_validate(board, schematic, bom, netlist)
        datasheets = []
        by_reference = {component.reference: component for component in components}
        for reference in checked.datasheet_scope:
            component = by_reference.get(reference)
            if component is None:
                raise PCBModelConflict(
                    "datasheet scope references an unknown component"
                )
            evidence = self._datasheet_source.get_evidence(
                checked.project_id, component
            )
            if not evidence:
                raise PCBUnavailable("required cited datasheet evidence is unavailable")
            for item in evidence:
                validated = type(item).model_validate(item.model_dump(mode="python"))
                if (
                    validated.project_id != checked.project_id
                    or validated.component_reference != reference
                ):
                    raise PCBModelConflict("datasheet evidence binding mismatch")
                datasheets.append(validated)

        interfaces = self._interfaces(netlist)
        provenance_by_fp: dict[str, PCBEvidenceReference] = {}
        for source in (board, schematic, bom, netlist):
            evidence = PCBEvidenceReference.create(
                evidence_id=deterministic_id("evidence", source.artifact.fingerprint),
                project_id=checked.project_id,
                source_type=PCBEvidenceSource.ARTIFACT,
                source_id=source.artifact.artifact_id,
                source_fingerprint=source.artifact.content_fingerprint,
                claim=PCBEvidenceClaim.OBSERVED,
                locator=source.artifact.source_reference,
            )
            provenance_by_fp[evidence.fingerprint] = evidence
        context_evidence = PCBEvidenceReference.create(
            evidence_id=deterministic_id("evidence", snapshot.fingerprint),
            project_id=checked.project_id,
            source_type=PCBEvidenceSource.CONTEXT,
            source_id=snapshot.context_id,
            source_fingerprint=snapshot.fingerprint,
            claim=PCBEvidenceClaim.CITED,
            locator=f"knowledge://context/{snapshot.context_id}",
        )
        provenance_by_fp[context_evidence.fingerprint] = context_evidence
        for datasheet in datasheets:
            for evidence in datasheet.evidence_refs:
                provenance_by_fp[evidence.fingerprint] = evidence
        return PCBContext.create(
            request_id=checked.request_id,
            project_id=checked.project_id,
            artifacts=checked.artifacts,
            board=board,
            schematic=schematic,
            bom=bom,
            netlist=netlist,
            components=components,
            nets=netlist.nets,
            connections=netlist.connections,
            bom_items=bom.items,
            interfaces=interfaces,
            datasheets=tuple(
                sorted(datasheets, key=lambda item: item.component_reference)
            ),
            engineering_task=checked.engineering_task,
            engineering_context_fingerprint=snapshot.fingerprint,
            provenance=tuple(
                sorted(provenance_by_fp.values(), key=lambda item: item.evidence_id)
            ),
        )

    def analyze(self, request: PCBAnalysisRequest, context: PCBContext) -> PCBAnalysis:
        checked_request = PCBAnalysisRequest.model_validate(
            request.model_dump(mode="python")
        )
        checked_context = PCBContext.model_validate(context.model_dump(mode="python"))
        if (
            checked_request.project_id != checked_context.project_id
            or checked_request.context_fingerprint != checked_context.fingerprint
        ):
            raise PCBModelConflict("analysis request context binding mismatch")
        current_snapshot = self._context_source.get_context(
            EngineeringContextRequest(
                project_id=checked_context.project_id,
                task=checked_context.engineering_task,
                constraints=(),
            )
        )
        if current_snapshot is None:
            raise PCBUnavailable("engineering context is unavailable")
        current_snapshot = EngineeringContextSnapshot.model_validate(
            current_snapshot.model_dump(mode="python")
        )
        if (
            current_snapshot.fingerprint
            != checked_context.engineering_context_fingerprint
        ):
            raise PCBModelConflict("engineering context changed during PCB analysis")
        for artifact in checked_context.artifacts:
            current = self._artifact_source.get_artifact(
                checked_context.project_id, artifact.artifact_id
            )
            if current is None or current != artifact:
                raise PCBModelConflict("PCB artifact changed during analysis")
        graph = self._graph.build(checked_context)
        if (
            graph.project_id != checked_context.project_id
            or graph.context_fingerprint != checked_context.fingerprint
        ):
            raise PCBModelConflict("PCB graph binding mismatch")
        analysis = self._analyzer.analyze(checked_request, checked_context, graph)
        analysis = PCBAnalysis.model_validate(analysis.model_dump(mode="python"))
        if (
            analysis.project_id != checked_context.project_id
            or analysis.context_fingerprint != checked_context.fingerprint
            or analysis.graph_fingerprint != graph.fingerprint
        ):
            raise PCBModelConflict("PCB analysis binding mismatch")
        return analysis

    def prepare_governance(self, analysis: PCBAnalysis) -> PCBGovernanceBundle:
        checked = PCBAnalysis.model_validate(analysis.model_dump(mode="python"))
        findings = self._validation.submit(checked)
        if type(findings) is not tuple or any(
            item.project_id != checked.project_id for item in findings
        ):
            raise PCBGovernanceError("validation projection binding mismatch")
        proposals = self._optimization.submit(checked, findings)
        if type(proposals) is not tuple or any(
            item.project_id != checked.project_id for item in proposals
        ):
            raise PCBGovernanceError("optimization projection binding mismatch")
        source_fps = {item.fingerprint for item in findings}
        for proposal in proposals:
            if not proposal.source_finding_refs or any(
                item.fingerprint not in source_fps
                for item in proposal.source_finding_refs
            ):
                raise PCBGovernanceError("optimization source finding binding mismatch")
        if len(proposals) != len(checked.proposals):
            raise PCBGovernanceError("optimization projection count mismatch")
        projected_by_id = {item.finding_id: item for item in findings}
        local_by_fp = {item.fingerprint: item for item in checked.findings}
        bindings = []
        unmatched = list(proposals)
        for pcb_proposal in checked.proposals:
            local_findings = tuple(
                local_by_fp[item] for item in pcb_proposal.finding_fingerprints
            )
            projected = tuple(
                projected_by_id[item.finding_id] for item in local_findings
            )
            expected_refs = {(item.finding_id, item.fingerprint) for item in projected}
            candidates = [
                item
                for item in unmatched
                if item.rationale == pcb_proposal.rationale
                and item.expected_effects == pcb_proposal.recommended_changes
                and {
                    (ref.finding_id, ref.fingerprint)
                    for ref in item.source_finding_refs
                }
                == expected_refs
            ]
            if len(candidates) != 1:
                raise PCBGovernanceError(
                    "optimization projection cannot be bound to exactly one PCB proposal"
                )
            optimization = candidates[0]
            unmatched = [
                item
                for item in unmatched
                if item.proposal_id != optimization.proposal_id
            ]
            bindings.append(
                PCBGovernanceBinding.create(
                    pcb_proposal_fingerprint=pcb_proposal.fingerprint,
                    validation_finding_fingerprints=tuple(
                        sorted(item.fingerprint for item in projected)
                    ),
                    optimization_proposal_id=optimization.proposal_id,
                    optimization_proposal_fingerprint=optimization.fingerprint,
                )
            )
        return PCBGovernanceBundle.create(
            bundle_id=deterministic_id("pcb-governance", checked.fingerprint),
            project_id=checked.project_id,
            analysis=checked,
            validation_findings=findings,
            optimization_proposals=proposals,
            bindings=tuple(bindings),
        )

    def record_repair_handoff(
        self, bundle: PCBGovernanceBundle, review_record: ReviewRecord
    ) -> PCBRepairHandoffReceipt:
        checked = PCBGovernanceBundle.model_validate(bundle.model_dump(mode="python"))
        review = ReviewRecord.model_validate(review_record.model_dump(mode="python"))
        target = review.review_request.target_reference
        if (
            review.review_request.project_id != checked.project_id
            or review.decision.project_id != checked.project_id
        ):
            raise PCBReviewRejected("review project binding mismatch")
        if (
            review.decision.decision is not ReviewDecision.APPROVE
            or target.target_type is not ReviewTargetType.OPTIMIZATION
        ):
            raise PCBReviewRejected("an approved optimization review is required")
        optimization = next(
            (
                item
                for item in checked.optimization_proposals
                if item.proposal_id == target.target_id
            ),
            None,
        )
        if (
            optimization is None
            or optimization.fingerprint != target.target_fingerprint
        ):
            raise PCBReviewRejected(
                "review target is not part of the PCB governance bundle"
            )
        binding = next(
            (
                item
                for item in checked.bindings
                if item.optimization_proposal_id == optimization.proposal_id
                and item.optimization_proposal_fingerprint == optimization.fingerprint
            ),
            None,
        )
        pcb_proposal = (
            None
            if binding is None
            else next(
                (
                    item
                    for item in checked.analysis.proposals
                    if item.fingerprint == binding.pcb_proposal_fingerprint
                ),
                None,
            )
        )
        if pcb_proposal is None:
            raise PCBReviewRejected(
                "optimization proposal is not bound to a PCB proposal"
            )
        handoff = PCBRepairHandoff.create(
            handoff_id=deterministic_id(
                "pcb-handoff", review.fingerprint, pcb_proposal.fingerprint
            ),
            project_id=checked.project_id,
            pcb_proposal_fingerprint=pcb_proposal.fingerprint,
            optimization_proposal_id=optimization.proposal_id,
            optimization_proposal_fingerprint=optimization.fingerprint,
            review_id=review.review_request.review_id,
            review_record_fingerprint=review.fingerprint,
            artifact_fingerprints=pcb_proposal.target_artifact_fingerprints,
            recorded_at=review.decision.decided_at,
        )
        receipt = self._repair.record(handoff)
        receipt = PCBRepairHandoffReceipt.model_validate(
            receipt.model_dump(mode="python")
        )
        if (
            receipt.project_id != handoff.project_id
            or receipt.handoff_fingerprint != handoff.fingerprint
            or receipt.recorded_at != handoff.recorded_at
        ):
            raise PCBGovernanceError("repair handoff receipt binding mismatch")
        return receipt

    @staticmethod
    def _merge_and_validate(
        board: PCBBoardArtifact,
        schematic: PCBSchematicArtifact,
        bom: PCBBOMArtifact,
        netlist: PCBNetlistArtifact,
    ) -> tuple[PCBComponent, ...]:
        board_map = {item.reference: item for item in board.components}
        schematic_map = {item.reference: item for item in schematic.components}
        netlist_map = {item.reference: item for item in netlist.components}
        bom_map = {
            reference: item for item in bom.items for reference in item.references
        }
        if len(bom_map) != sum(len(item.references) for item in bom.items):
            raise PCBModelConflict("BOM contains duplicate component references")
        populated = {
            reference
            for reference, item in bom_map.items()
            if item.population is PCBPopulation.POPULATED
        }
        if (
            set(board_map) != populated
            or set(schematic_map) != populated
            or set(netlist_map) != populated
        ):
            raise PCBModelConflict(
                "populated components are inconsistent across PCB artifacts"
            )
        for reference in populated:
            values = {
                (board_map[reference].value, board_map[reference].footprint),
                (schematic_map[reference].value, schematic_map[reference].footprint),
                (netlist_map[reference].value, netlist_map[reference].footprint),
                (bom_map[reference].value, bom_map[reference].footprint),
            }
            if len(values) != 1:
                raise PCBModelConflict(
                    "component value or footprint differs across PCB artifacts"
                )
        board_connections = {
            (item.component_reference, item.pin_number, item.net_id)
            for item in board.connections
        }
        netlist_connections = {
            (item.component_reference, item.pin_number, item.net_id)
            for item in netlist.connections
        }
        if board_connections != netlist_connections:
            raise PCBModelConflict("board and netlist connectivity differ")
        result = []
        for reference in sorted(populated):
            source = netlist_map[reference]
            item = bom_map[reference]
            result.append(
                PCBComponent.create(
                    reference=reference,
                    value=item.value,
                    footprint=item.footprint,
                    manufacturer=item.manufacturer,
                    manufacturer_part_number=item.manufacturer_part_number,
                    population=item.population,
                    pins=source.pins,
                    evidence_refs=tuple(
                        sorted(
                            (
                                *board_map[reference].evidence_refs,
                                *schematic_map[reference].evidence_refs,
                                *source.evidence_refs,
                                *item.evidence_refs,
                            ),
                            key=lambda evidence: evidence.fingerprint,
                        )
                    ),
                )
            )
        return tuple(result)

    @staticmethod
    def _interfaces(netlist: PCBNetlistArtifact) -> tuple[PCBInterface, ...]:
        groups = {}
        for net in netlist.nets:
            upper = net.name.upper()
            protocol = next(
                (name for name in ("I2C", "SPI", "UART") if name in upper), None
            )
            if protocol is not None:
                groups.setdefault(protocol, []).append(net)
        return tuple(
            PCBInterface.create(
                interface_id=deterministic_id(
                    "interface", netlist.fingerprint, protocol
                ),
                protocol=protocol,
                signal_nets=tuple(sorted(net.net_id for net in nets)),
                claim=PCBEvidenceClaim.INFERRED,
                evidence_refs=tuple(ref for net in nets for ref in net.evidence_refs),
            )
            for protocol, nets in sorted(groups.items())
        )
