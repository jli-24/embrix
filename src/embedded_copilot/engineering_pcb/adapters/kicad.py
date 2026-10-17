from __future__ import annotations

import copy
from collections.abc import Mapping

from embedded_copilot.input.models import UserAttachment
from embedded_copilot.pcb.models import UnifiedPCBModel
from embedded_copilot.pcb.parser import PCBParser

from ..exceptions import PCBArtifactNotFound, PCBModelConflict, PCBUnsupportedFormat
from ..models import (
    PCBArtifactReference,
    PCBArtifactType,
    PCBBoardArtifact,
    PCBComponent,
    PCBConnection,
    PCBNet,
    PCBPin,
    PCBPopulation,
    PCBSchematicArtifact,
)
from ..parser import artifact_evidence, classify_net, parse_kicad_schematic


class KiCadBoardParserAdapter:
    """Read-only bridge to the existing bounded KiCad board parser."""

    def __init__(
        self, parser: PCBParser, attachments: Mapping[str, UserAttachment]
    ) -> None:
        self._parser = parser
        self._attachments = copy.deepcopy(dict(attachments))

    def parse(self, artifact: PCBArtifactReference) -> PCBBoardArtifact:
        if artifact.artifact_type is not PCBArtifactType.BOARD:
            raise PCBUnsupportedFormat("board parser received a non-board artifact")
        try:
            attachment = copy.deepcopy(self._attachments[artifact.artifact_id])
        except KeyError as exc:
            raise PCBArtifactNotFound("board attachment is unavailable") from exc
        model = self._parser.parse(attachment)
        if not isinstance(model, UnifiedPCBModel) or model.source_format != "kicad_pcb":
            raise PCBModelConflict("legacy PCB parser returned an invalid model")
        evidence = artifact_evidence(artifact)
        nets = tuple(
            PCBNet.create(
                net_id=net.name,
                name=net.name,
                net_type=classify_net(net.name),
                evidence_refs=(evidence,),
            )
            for net in sorted(model.nets, key=lambda item: item.name)
        )
        known_nets = {net.net_id for net in nets}
        components = []
        connections = []
        for component in sorted(model.components, key=lambda item: item.reference):
            pins = []
            for pin in sorted(component.pins, key=lambda item: item.number):
                pins.append(
                    PCBPin.create(
                        component_reference=component.reference,
                        pin_number=pin.number,
                        electrical_type=pin.pad_type,
                        no_connect=pin.net_name is None,
                        evidence_refs=(evidence,),
                    )
                )
                if pin.net_name is not None:
                    if pin.net_name not in known_nets:
                        raise PCBModelConflict("board pin references an unknown net")
                    connections.append(
                        PCBConnection.create(
                            component_reference=component.reference,
                            pin_number=pin.number,
                            net_id=pin.net_name,
                            connection_kind="BOARD_PAD",
                            artifact_fingerprints=(artifact.fingerprint,),
                            evidence_refs=(evidence,),
                        )
                    )
            components.append(
                PCBComponent.create(
                    reference=component.reference,
                    value=component.value,
                    footprint=component.footprint,
                    population=PCBPopulation.POPULATED,
                    pins=tuple(pins),
                    evidence_refs=(evidence,),
                )
            )
        return PCBBoardArtifact.create(
            artifact=artifact,
            components=tuple(components),
            nets=nets,
            connections=tuple(
                sorted(
                    connections,
                    key=lambda item: (
                        item.component_reference,
                        item.pin_number,
                        item.net_id,
                    ),
                )
            ),
        )


class KiCadSchematicParserAdapter:
    def __init__(self, content: Mapping[str, bytes]) -> None:
        self._content = copy.deepcopy(dict(content))

    def parse(self, artifact: PCBArtifactReference) -> PCBSchematicArtifact:
        try:
            content = self._content[artifact.artifact_id]
        except KeyError as exc:
            raise PCBArtifactNotFound("schematic content is unavailable") from exc
        return parse_kicad_schematic(artifact, copy.deepcopy(content))
