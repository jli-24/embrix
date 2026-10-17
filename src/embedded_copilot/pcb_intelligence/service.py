from __future__ import annotations

from embedded_copilot.engineering_copilot.common import validate_copy
from embedded_copilot.input.models import UserAttachment
from embedded_copilot.pcb.models import UnifiedPCBModel

from .contracts import PCBConnectivityGraph, PCBInputBundle, PCBIssue, SchematicProposal


class PCBReviewService:
    def __init__(self, parser=None):
        self._parser = parser

    def analyze(
        self, bundle: PCBInputBundle
    ) -> tuple[PCBConnectivityGraph, tuple[PCBIssue, ...], SchematicProposal]:
        data = validate_copy(bundle, PCBInputBundle)
        model = data.pcb_model
        components = tuple(sorted(c.reference for c in model.components))
        nets = tuple(sorted(n.name for n in model.nets))
        connections = tuple(
            sorted(
                f"{node.reference}:{node.pin}->{net.name}"
                for net in model.nets
                for node in net.nodes
            )
        )
        names = " ".join(net.name.casefold() for net in model.nets)
        interfaces = tuple(
            x for x in ("SPI", "I2C", "UART", "POWER") if x.casefold() in names
        )
        graph = PCBConnectivityGraph.create(
            project_id=data.project_id,
            components=components,
            nets=nets,
            connections=connections,
            interfaces=interfaces,
        )
        issues: list[PCBIssue] = []
        if not model.nets:
            issues.append(
                PCBIssue.create(
                    issue_id="pcb-no-nets",
                    project_id=data.project_id,
                    severity="HIGH",
                    category="CONNECTIVITY",
                    description="未发现网络连接",
                    suggestion="由工程师补充并验证 netlist",
                    evidence_refs=(),
                )
            )
        refs = tuple(sorted(set(nets)))
        proposal = SchematicProposal.create(
            proposal_id=f"schematic-{data.project_id}-{graph.fingerprint[-12:]}",
            project_id=data.project_id,
            connections=tuple(f"review:{i}" for i in interfaces),
            evidence_refs=refs,
            confidence=0.5 if issues else 0.8,
        )
        return graph, tuple(issues), proposal

    def review(self, bundle: PCBInputBundle):
        return self.analyze(bundle)

    def review_attachment(
        self,
        *,
        project_id: str,
        attachment: UserAttachment,
        schematic: tuple[str, ...] = (),
        bom: tuple[str, ...] = (),
        netlist: tuple[str, ...] = (),
    ):
        if self._parser is None:
            raise ValueError("PCB parser unavailable")
        if not isinstance(attachment, UserAttachment):
            raise TypeError("PCB attachment is invalid")
        parsed = self._parser.parse(attachment)
        if type(parsed) is not UnifiedPCBModel:
            raise TypeError("PCB parser result is invalid")
        bundle = PCBInputBundle.create(
            project_id=project_id,
            pcb_model=parsed,
            schematic=schematic,
            bom=bom,
            netlist=netlist,
        )
        return self.analyze(bundle)


__all__ = ("PCBReviewService",)
