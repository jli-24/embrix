from __future__ import annotations

from datetime import UTC, datetime

import pytest

from embedded_copilot.engineering_context import (
    ContextVerificationStatus,
    EngineeringContextSnapshot,
)
from embedded_copilot.engineering_pcb import (
    PCBArtifactReference,
    PCBArtifactType,
    PCBBoardArtifact,
    PCBComponent,
    PCBConnection,
    PCBContextRequest,
    PCBEvidenceClaim,
    PCBEvidenceReference,
    PCBEvidenceSource,
    PCBNet,
    PCBNetType,
    PCBPin,
    PCBPopulation,
    PCBSourceFormat,
    content_fingerprint,
    deterministic_id,
)
from embedded_copilot.engineering_pcb.parser import (
    parse_csv_bom,
    parse_kicad_schematic,
    parse_kicad_xml_netlist,
)

NOW = datetime(2026, 8, 10, 1, 2, 3, tzinfo=UTC)
PROJECT = "pcb-project"

BOARD_CONTENT = b"(kicad_pcb (version 20240108))"
SCHEMATIC_CONTENT = b"""(kicad_sch
  (version 20231120)
  (symbol
    (uuid "11111111-1111-1111-1111-111111111111")
    (property "Reference" "U1")
    (property "Value" "MCU")
    (property "Footprint" "QFN")))"""
BOM_CONTENT = b"References,Quantity,Value,Footprint,Manufacturer,ManufacturerPartNumber,Population\r\nU1,1,MCU,QFN,Acme,MCU-1,POPULATED\r\n"
NETLIST_CONTENT = b"""<?xml version="1.0" encoding="UTF-8"?>
<export><components><comp ref="U1"><value>MCU</value><footprint>QFN</footprint></comp></components>
<nets><net code="1" name="VCC"><node ref="U1" pin="1"/></net>
<net code="2" name="GND"><node ref="U1" pin="2"/></net></nets></export>"""


def make_artifact(
    artifact_type: PCBArtifactType, content: bytes, *, project_id: str = PROJECT
) -> PCBArtifactReference:
    formats = {
        PCBArtifactType.BOARD: (PCBSourceFormat.KICAD_PCB, "main.kicad_pcb", "board"),
        PCBArtifactType.SCHEMATIC: (
            PCBSourceFormat.KICAD_SCH,
            "main.kicad_sch",
            "schematic",
        ),
        PCBArtifactType.BOM: (PCBSourceFormat.CSV_BOM, "bom.csv", "bom"),
        PCBArtifactType.NETLIST: (
            PCBSourceFormat.KICAD_XML_NETLIST,
            "netlist.xml",
            "netlist",
        ),
    }
    source_format, filename, artifact_id = formats[artifact_type]
    return PCBArtifactReference.create(
        project_id=project_id,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        source_format=source_format,
        source_reference=f"artifact://{project_id}/{artifact_id}",
        filename=filename,
        byte_size=len(content),
        content_fingerprint=content_fingerprint(content),
        captured_at=NOW,
    )


def make_evidence(artifact: PCBArtifactReference) -> PCBEvidenceReference:
    return PCBEvidenceReference.create(
        evidence_id=deterministic_id("evidence", artifact.fingerprint),
        project_id=artifact.project_id,
        source_type=PCBEvidenceSource.ARTIFACT,
        source_id=artifact.artifact_id,
        source_fingerprint=artifact.content_fingerprint,
        claim=PCBEvidenceClaim.OBSERVED,
        locator=artifact.source_reference,
    )


@pytest.fixture
def artifacts() -> tuple[PCBArtifactReference, ...]:
    return (
        make_artifact(PCBArtifactType.BOARD, BOARD_CONTENT),
        make_artifact(PCBArtifactType.SCHEMATIC, SCHEMATIC_CONTENT),
        make_artifact(PCBArtifactType.BOM, BOM_CONTENT),
        make_artifact(PCBArtifactType.NETLIST, NETLIST_CONTENT),
    )


@pytest.fixture
def parsed_artifacts(artifacts):
    by_type = {item.artifact_type: item for item in artifacts}
    board_ref = by_type[PCBArtifactType.BOARD]
    evidence = make_evidence(board_ref)
    board = PCBBoardArtifact.create(
        artifact=board_ref,
        components=(
            PCBComponent.create(
                reference="U1",
                value="MCU",
                footprint="QFN",
                population=PCBPopulation.POPULATED,
                pins=(
                    PCBPin.create(
                        component_reference="U1",
                        pin_number="1",
                        electrical_type="smd",
                        evidence_refs=(evidence,),
                    ),
                    PCBPin.create(
                        component_reference="U1",
                        pin_number="2",
                        electrical_type="smd",
                        evidence_refs=(evidence,),
                    ),
                ),
                evidence_refs=(evidence,),
            ),
        ),
        nets=(
            PCBNet.create(
                net_id="GND",
                name="GND",
                net_type=PCBNetType.GROUND,
                evidence_refs=(evidence,),
            ),
            PCBNet.create(
                net_id="VCC",
                name="VCC",
                net_type=PCBNetType.POWER,
                evidence_refs=(evidence,),
            ),
        ),
        connections=(
            PCBConnection.create(
                component_reference="U1",
                pin_number="1",
                net_id="VCC",
                connection_kind="BOARD_PAD",
                artifact_fingerprints=(board_ref.fingerprint,),
                evidence_refs=(evidence,),
            ),
            PCBConnection.create(
                component_reference="U1",
                pin_number="2",
                net_id="GND",
                connection_kind="BOARD_PAD",
                artifact_fingerprints=(board_ref.fingerprint,),
                evidence_refs=(evidence,),
            ),
        ),
    )
    schematic = parse_kicad_schematic(
        by_type[PCBArtifactType.SCHEMATIC], SCHEMATIC_CONTENT
    )
    bom = parse_csv_bom(by_type[PCBArtifactType.BOM], BOM_CONTENT)
    netlist = parse_kicad_xml_netlist(by_type[PCBArtifactType.NETLIST], NETLIST_CONTENT)
    return {
        "board": board,
        "schematic": schematic,
        "bom": bom,
        "netlist": netlist,
    }


@pytest.fixture
def snapshot() -> EngineeringContextSnapshot:
    return EngineeringContextSnapshot.create(
        project_id=PROJECT,
        task_description="Review deterministic PCB connectivity",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.0,
        provenance=(),
        verification_status=ContextVerificationStatus.UNVERIFIED,
    )


@pytest.fixture
def context_request(artifacts) -> PCBContextRequest:
    return PCBContextRequest.create(
        request_id="pcb-context-request",
        project_id=PROJECT,
        artifacts=artifacts,
        datasheet_scope=(),
        engineering_task="Review deterministic PCB connectivity",
        requested_at=NOW,
    )
