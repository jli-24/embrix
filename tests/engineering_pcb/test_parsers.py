from __future__ import annotations

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_pcb import (
    PCBArtifactType,
    PCBParseError,
    PCBSourceDrift,
    PCBUnsupportedFormat,
)
from embedded_copilot.engineering_pcb.parser import (
    parse_csv_bom,
    parse_kicad_schematic,
    parse_kicad_xml_netlist,
)

from .conftest import BOM_CONTENT, NETLIST_CONTENT, SCHEMATIC_CONTENT, make_artifact

POWER_AND_GLOBAL_LABEL_SCHEMATIC_CONTENT = b'''(kicad_sch
  (version 20231120)
  (lib_symbols
    (symbol "MCU:TEST"
      (symbol "TEST_1_1"
        (pin bidirectional line (at 0 0 0) (length 2.54)
          (name "PA8" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27))))
        )
      )
    )
    (symbol "power:GND"
      (symbol "GND_1_1"
        (pin power_in line (at 0 0 0) (length 0)
          (name "GND" (effects (font (size 1.27 1.27))))
          (number "1" (effects (font (size 1.27 1.27))))
        )
      )
    )
  )
  (symbol (lib_id "MCU:TEST") (at 10 10 0)
    (uuid "11111111-1111-1111-1111-111111111111")
    (property "Reference" "U1")
    (property "Value" "TEST")
    (property "Footprint" "")
    (pin "1" (uuid "22222222-2222-2222-2222-222222222222"))
  )
  (symbol (lib_id "power:GND") (at 15 10 0)
    (uuid "33333333-3333-3333-3333-333333333333")
    (property "Reference" "#PWR01")
    (property "Value" "GND")
    (property "Footprint" "")
    (pin "1" (uuid "44444444-4444-4444-4444-444444444444"))
  )
  (wire (pts (xy 10 10) (xy 20 10)))
  (label "LOCAL" (at 10 10 0))
  (global_label "GLOBAL" (shape input) (at 20 10 0))
)'''


def test_csv_bom_parses_fixed_schema():
    artifact = make_artifact(PCBArtifactType.BOM, BOM_CONTENT)
    result = parse_csv_bom(artifact, BOM_CONTENT)
    assert result.items[0].references == ("U1",)
    assert result.items[0].manufacturer_part_number == "MCU-1"


def test_csv_bom_rejects_unknown_header():
    content = BOM_CONTENT.replace(b"Population", b"State")
    with pytest.raises(PCBParseError):
        parse_csv_bom(make_artifact(PCBArtifactType.BOM, content), content)


def test_csv_bom_rejects_quantity_mismatch():
    content = BOM_CONTENT.replace(b"U1,1", b"U1,2")
    with pytest.raises(ValidationError):
        parse_csv_bom(make_artifact(PCBArtifactType.BOM, content), content)


def test_csv_bom_rejects_non_utf8():
    content = b"\xff\xfeinvalid"
    with pytest.raises(PCBParseError):
        parse_csv_bom(make_artifact(PCBArtifactType.BOM, content), content)


def test_xml_netlist_parses_connections():
    artifact = make_artifact(PCBArtifactType.NETLIST, NETLIST_CONTENT)
    result = parse_kicad_xml_netlist(artifact, NETLIST_CONTENT)
    assert {
        (item.component_reference, item.pin_number, item.net_id)
        for item in result.connections
    } == {("U1", "1", "VCC"), ("U1", "2", "GND")}


@pytest.mark.parametrize(
    "declaration", (b'<!DOCTYPE export SYSTEM "x">', b'<!ENTITY x "y">')
)
def test_xml_netlist_rejects_dtd_and_entities(declaration):
    content = declaration + NETLIST_CONTENT
    with pytest.raises(PCBParseError):
        parse_kicad_xml_netlist(
            make_artifact(PCBArtifactType.NETLIST, content), content
        )


def test_xml_netlist_rejects_unknown_component_reference():
    content = NETLIST_CONTENT.replace(b'ref="U1" pin="2"', b'ref="U2" pin="2"')
    with pytest.raises(PCBParseError):
        parse_kicad_xml_netlist(
            make_artifact(PCBArtifactType.NETLIST, content), content
        )


def test_xml_netlist_rejects_duplicate_node():
    content = NETLIST_CONTENT.replace(
        b"</net></nets>", b'<node ref="U1" pin="1"/></net></nets>'
    )
    with pytest.raises(PCBParseError):
        parse_kicad_xml_netlist(
            make_artifact(PCBArtifactType.NETLIST, content), content
        )


def test_schematic_parses_placed_symbol():
    artifact = make_artifact(PCBArtifactType.SCHEMATIC, SCHEMATIC_CONTENT)
    result = parse_kicad_schematic(artifact, SCHEMATIC_CONTENT)
    assert result.components[0].reference == "U1"


def test_schematic_extracts_global_label_and_power_symbol_net():
    artifact = make_artifact(
        PCBArtifactType.SCHEMATIC, POWER_AND_GLOBAL_LABEL_SCHEMATIC_CONTENT
    )
    result = parse_kicad_schematic(artifact, POWER_AND_GLOBAL_LABEL_SCHEMATIC_CONTENT)

    assert tuple(item.reference for item in result.components) == ("U1",)
    assert [
        (item.component_reference, item.pin_number, item.net_id)
        for item in result.connections
    ] == [("U1", "1", "GLOBAL|GND|LOCAL")]


@pytest.mark.parametrize(
    "construct", (b"(bus (pts))", b"(sheet (uuid x))", b"(hierarchical_label X)")
)
def test_schematic_rejects_unsupported_connectivity(construct):
    content = SCHEMATIC_CONTENT[:-1] + construct + b")"
    with pytest.raises(PCBUnsupportedFormat):
        parse_kicad_schematic(
            make_artifact(PCBArtifactType.SCHEMATIC, content), content
        )


def test_schematic_rejects_missing_uuid():
    content = SCHEMATIC_CONTENT.replace(
        b'(uuid "11111111-1111-1111-1111-111111111111")', b""
    )
    with pytest.raises(PCBParseError):
        parse_kicad_schematic(
            make_artifact(PCBArtifactType.SCHEMATIC, content), content
        )


def test_schematic_rejects_unknown_version():
    content = SCHEMATIC_CONTENT.replace(b"20231120", b"20990101")
    with pytest.raises(PCBUnsupportedFormat):
        parse_kicad_schematic(
            make_artifact(PCBArtifactType.SCHEMATIC, content), content
        )


def test_parser_rejects_source_drift():
    artifact = make_artifact(PCBArtifactType.BOM, BOM_CONTENT)
    with pytest.raises(PCBSourceDrift):
        parse_csv_bom(artifact, BOM_CONTENT + b" ")


def test_parser_rejects_wrong_artifact_type():
    artifact = make_artifact(PCBArtifactType.BOARD, b"x")
    with pytest.raises(PCBUnsupportedFormat):
        parse_csv_bom(artifact, b"x")
