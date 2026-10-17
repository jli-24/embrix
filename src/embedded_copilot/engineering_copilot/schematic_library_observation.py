"""Finite read-only SHTC3_EP library and XML observation from trusted caller bytes."""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from embedded_copilot.engineering_copilot.schematic_evidence import parse_xml_netlist
from embedded_copilot.pcb.parser.kicad import (
    _atom,
    _children,
    _parse_s_expression,
)
from embedded_copilot.pcb.parser.models import PCBParserLimits

_SYMBOL = "SHTC3_EP"
_FOOTPRINT = "Sensirion_DFN-4-1EP_2x2mm_P1mm_EP0.7x1.6mm"
_PINS = {
    "1": "VDD",
    "2": "SCL",
    "3": "SDA",
    "4": "VSS",
    "5": "VSS",  # Logical library pin for EP; not a manufacturer-numbered pin 5.
}


@dataclass(frozen=True)
class ObservedXmlNet:
    logical_symbol_pin: str
    physical_pad_id: str
    xml_net_name: str


@dataclass(frozen=True)
class Shtc3EpLibraryObservation:
    symbol_sha256: str
    footprint_sha256: str
    xml_sha256: str
    symbol_library_id: str
    footprint_library_id: str
    component_ref: str
    observed_symbol_pins: tuple[tuple[str, str], ...]
    observed_physical_pad_ids: tuple[str, ...]
    observed_xml_nets: tuple[ObservedXmlNet, ...]
    non_electrical_paste_pad_count: int


def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _expression(content: bytes, maximum: int, head: str):
    if type(content) is not bytes or not content or len(content) > maximum:
        raise ValueError("LIBRARY_OR_XML_UNAVAILABLE")
    try:
        text = content.decode("utf-8", errors="strict")
        if "\x00" in text:
            raise ValueError("NUL")
        root = _parse_s_expression(text, PCBParserLimits())
    except (UnicodeError, ValueError, TypeError):
        raise ValueError("UNSUPPORTED_LIBRARY_FORMAT") from None
    if _atom(root, 0) != head:
        raise ValueError("UNSUPPORTED_LIBRARY_FORMAT")
    return root


def _one(expression, child_name: str):
    found = _children(expression, child_name)
    if len(found) != 1:
        raise ValueError("UNSUPPORTED_LIBRARY_FORMAT")
    return found[0]


def _shape(expression, expected_children: dict[str, int], atom_count: int) -> None:
    atoms = [item for item in expression[1:] if isinstance(item, str)]
    children = [item for item in expression[1:] if isinstance(item, tuple)]
    if (
        len(atoms) != atom_count
        or len(atoms) + len(children) != len(expression) - 1
        or Counter(_atom(item, 0) for item in children) != expected_children
    ):
        raise ValueError("UNSUPPORTED_LIBRARY_FORMAT")


def _leaf(expression, value: str) -> None:
    _shape(expression, {}, 1)
    if _atom(expression, 1) != value:
        raise ValueError("UNSUPPORTED_LIBRARY_FORMAT")


def _numbers(expression, counts: tuple[int, ...]) -> None:
    values = expression[1:]
    if len(values) not in counts or not all(isinstance(value, str) for value in values):
        raise ValueError("UNSUPPORTED_LIBRARY_FORMAT")
    try:
        if not all(Decimal(value).is_finite() for value in values):
            raise ValueError("UNSUPPORTED_LIBRARY_FORMAT")
    except InvalidOperation:
        raise ValueError("UNSUPPORTED_LIBRARY_FORMAT") from None


def _selected_xml_identity(
    content: bytes,
    component_ref: str,
    symbol_library_id: str,
    footprint_library_id: str,
) -> None:
    if (
        type(content) is not bytes
        or not content
        or len(content) > 4 * 1024 * 1024
        or b"<!DOCTYPE" in content.upper()
        or b"<!ENTITY" in content.upper()
    ):
        raise ValueError("XML_LIBRARY_IDENTITY_MISMATCH")
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise ValueError("XML_LIBRARY_IDENTITY_MISMATCH") from None
    if root.tag != "export":
        raise ValueError("XML_LIBRARY_IDENTITY_MISMATCH")
    selected = [
        item
        for item in root.findall("./components/comp")
        if item.get("ref") == component_ref
    ]
    if len(selected) != 1:
        raise ValueError("XML_LIBRARY_IDENTITY_MISMATCH")
    comp = selected[0]
    if set(comp.attrib) != {"ref"}:
        raise ValueError("XML_LIBRARY_IDENTITY_MISMATCH")
    if any(
        item.tag
        not in {
            "value",
            "footprint",
            "libsource",
            "property",
            "tstamps",
            "sheetpath",
            "datasheet",
        }
        for item in comp
    ):
        raise ValueError("XML_LIBRARY_IDENTITY_MISMATCH")
    footprints = comp.findall("footprint")
    sources = comp.findall("libsource")
    if (
        len(footprints) != 1
        or footprints[0].text != footprint_library_id
        or footprints[0].attrib
        or list(footprints[0])
        or len(sources) != 1
        or set(sources[0].attrib) != {"lib", "part"}
        or list(sources[0])
        or (sources[0].get("lib"), sources[0].get("part"))
        != tuple(symbol_library_id.split(":", 1))
    ):
        raise ValueError("XML_LIBRARY_IDENTITY_MISMATCH")
    parts = [
        item
        for item in root.findall("./libparts/libpart")
        if (item.get("lib"), item.get("part")) == tuple(symbol_library_id.split(":", 1))
    ]
    if (
        len(parts) != 1
        or set(parts[0].attrib) != {"lib", "part"}
        or len(parts[0].findall("pins")) != 1
    ):
        raise ValueError("XML_PIN_COVERAGE_UNKNOWN")
    pins = parts[0].find("pins")
    pin_types = {
        "1": "power_in",
        "2": "input",
        "3": "bidirectional",
        "4": "power_in",
        "5": "power_in",
    }
    if (
        pins is None
        or pins.attrib
        or any(
            item.tag != "pin"
            or list(item)
            or not {"num", "name"} <= set(item.attrib) <= {"num", "name", "type"}
            or (
                "type" in item.attrib
                and item.get("type") != pin_types.get(item.get("num"))
            )
            for item in pins
        )
    ):
        raise ValueError("XML_PIN_COVERAGE_UNKNOWN")
    pin_names = {"1": "VDD", "2": "SCL", "3": "SDA", "4": "VSS", "5": "VSS"}
    for net in root.findall("./nets/net"):
        selected_nodes = [
            item for item in net.findall("node") if item.get("ref") == component_ref
        ]
        if selected_nodes and not {"name"} <= set(net.attrib) <= {"name", "code"}:
            raise ValueError("XML_NET_COVERAGE_UNKNOWN")
        for node in selected_nodes:
            attrs = set(node.attrib)
            pin = node.get("pin")
            if (
                list(node)
                or not {"ref", "pin"}
                <= attrs
                <= {"ref", "pin", "pinfunction", "pintype"}
                or pin not in pin_names
                or (
                    "pinfunction" in attrs and node.get("pinfunction") != pin_names[pin]
                )
                or ("pintype" in attrs and node.get("pintype") != pin_types[pin])
            ):
                raise ValueError("XML_NET_COVERAGE_UNKNOWN")


def _symbol_pins(content: bytes, footprint_library_id: str):
    root = _expression(content, 64 * 1024, "kicad_symbol_lib")
    _shape(root, {"version": 1, "generator": 1, "generator_version": 1, "symbol": 1}, 0)
    _leaf(_one(root, "version"), "20251024")
    _leaf(_one(root, "generator"), "kicad_symbol_editor")
    _leaf(_one(root, "generator_version"), "10.0")
    symbol = _one(root, "symbol")
    _shape(
        symbol,
        {
            "property": 7,
            "exclude_from_sim": 1,
            "in_bom": 1,
            "on_board": 1,
            "in_pos_files": 1,
            "duplicate_pin_numbers_are_jumpers": 1,
            "symbol": 1,
            "embedded_fonts": 1,
        },
        1,
    )
    if _atom(symbol, 1) != _SYMBOL:
        raise ValueError("UNSUPPORTED_LIBRARY_FORMAT")
    _leaf(_one(symbol, "duplicate_pin_numbers_are_jumpers"), "no")
    properties = [
        prop for prop in _children(symbol, "property") if _atom(prop, 1) == "Footprint"
    ]
    if len(properties) != 1:
        raise ValueError("LIBRARY_IDENTITY_MISMATCH")
    _shape(
        properties[0],
        {"at": 1, "show_name": 1, "do_not_autoplace": 1, "hide": 1, "effects": 1},
        2,
    )
    if _atom(properties[0], 2) != footprint_library_id:
        raise ValueError("LIBRARY_IDENTITY_MISMATCH")
    unit = _one(symbol, "symbol")
    _shape(unit, {"rectangle": 1, "pin": 5}, 1)
    if _atom(unit, 1) != _SYMBOL + "_1_1":
        raise ValueError("UNSUPPORTED_LIBRARY_FORMAT")
    pins = {}
    types = {
        "1": "power_in",
        "2": "input",
        "3": "bidirectional",
        "4": "power_in",
        "5": "power_in",
    }
    for pin in _children(unit, "pin"):
        _shape(pin, {"at": 1, "length": 1, "name": 1, "number": 1}, 2)
        number_node = _one(pin, "number")
        name_node = _one(pin, "name")
        _shape(number_node, {"effects": 1}, 1)
        _shape(name_node, {"effects": 1}, 1)
        _numbers(_one(pin, "at"), (3,))
        _numbers(_one(pin, "length"), (1,))
        number = _atom(number_node, 1)
        name = _atom(name_node, 1)
        if (
            not number
            or not name
            or number in pins
            or (_atom(pin, 1), _atom(pin, 2)) != (types.get(number), "line")
        ):
            raise ValueError("UNSUPPORTED_LIBRARY_FORMAT")
        pins[number] = name
    if pins != _PINS:
        raise ValueError("UNSUPPORTED_LIBRARY_PROFILE")
    return tuple(sorted(pins.items()))


def _footprint_pads(content: bytes):
    root = _expression(content, 128 * 1024, "footprint")
    _shape(
        root,
        {
            "fp_line": 11,
            "pad": 10,
            "property": 4,
            "version": 1,
            "generator": 1,
            "generator_version": 1,
            "layer": 1,
            "descr": 1,
            "tags": 1,
            "attr": 1,
            "duplicate_pad_numbers_are_jumpers": 1,
            "fp_poly": 1,
            "fp_text": 1,
            "embedded_fonts": 1,
            "model": 1,
        },
        1,
    )
    _leaf(_one(root, "version"), "20260206")
    _leaf(_one(root, "generator"), "pcbnew")
    _leaf(_one(root, "generator_version"), "10.0")
    _leaf(_one(root, "layer"), "F.Cu")
    _leaf(_one(root, "attr"), "smd")
    _leaf(_one(root, "duplicate_pad_numbers_are_jumpers"), "no")
    if _atom(root, 1) != _FOOTPRINT:
        raise ValueError("UNSUPPORTED_LIBRARY_FORMAT")
    numbered = set()
    paste_count = 0
    for pad in _children(root, "pad"):
        _shape(
            pad, {"at": 1, "size": 1, "layers": 1, "roundrect_rratio": 1, "uuid": 1}, 3
        )
        _numbers(_one(pad, "at"), (2, 3))
        _numbers(_one(pad, "size"), (2,))
        _numbers(_one(pad, "roundrect_rratio"), (1,))
        number = _atom(pad, 1)
        pad_type = _atom(pad, 2)
        if _atom(pad, 3) != "roundrect":
            raise ValueError("UNSUPPORTED_LIBRARY_PROFILE")
        layers = _one(pad, "layers")[1:]
        if not all(isinstance(layer, str) for layer in layers):
            raise ValueError("UNSUPPORTED_LIBRARY_FORMAT")
        if number == "":
            if pad_type != "smd" or layers != ("F.Paste",):
                raise ValueError("UNSUPPORTED_LIBRARY_PROFILE")
            paste_count += 1
        elif number in _PINS and number not in numbered:
            if pad_type != "smd" or layers != ("F.Cu", "F.Mask"):
                raise ValueError("UNSUPPORTED_LIBRARY_PROFILE")
            numbered.add(number)
        else:
            raise ValueError("UNSUPPORTED_LIBRARY_PROFILE")
    if numbered != set(_PINS) or paste_count != 5:
        raise ValueError("UNSUPPORTED_LIBRARY_PROFILE")
    return tuple(sorted(numbered)), paste_count


def parse_shtc3_ep_observation(
    *,
    symbol_bytes: bytes,
    footprint_bytes: bytes,
    xml_bytes: bytes,
    component_ref: str,
    symbol_library_id: str,
    footprint_library_id: str,
    symbol_sha256: str,
    footprint_sha256: str,
) -> Shtc3EpLibraryObservation:
    """Observe one flattened private library and independently exported XML.

    The caller supplies already trusted bytes and current hashes. XML net names
    remain XML names; this parser does not map them to Catalog net IDs.
    """
    if (
        type(component_ref) is not str
        or not component_ref
        or type(symbol_library_id) is not str
        or symbol_library_id != "SHTC3_EP:" + _SYMBOL
        or type(footprint_library_id) is not str
        or footprint_library_id != "SHTC3_EP:" + _FOOTPRINT
    ):
        raise ValueError("LIBRARY_IDENTITY_MISMATCH")
    if (
        _digest(symbol_bytes) != symbol_sha256
        or _digest(footprint_bytes) != footprint_sha256
    ):
        raise ValueError("LIBRARY_CONTENT_MISMATCH")
    pins = _symbol_pins(symbol_bytes, footprint_library_id)
    pads, paste_count = _footprint_pads(footprint_bytes)
    _selected_xml_identity(
        xml_bytes, component_ref, symbol_library_id, footprint_library_id
    )
    observed = parse_xml_netlist(xml_bytes)
    selected = [item for item in observed.components if item.reference == component_ref]
    if (
        len(selected) != 1
        or selected[0].symbol != symbol_library_id
        or selected[0].footprint != footprint_library_id
    ):
        raise ValueError("XML_LIBRARY_IDENTITY_MISMATCH")
    definitions = tuple(
        sorted(
            (pin, name)
            for ref, pin, name in observed.pin_definitions
            if ref == component_ref
        )
    )
    if definitions != pins:
        raise ValueError("XML_PIN_COVERAGE_UNKNOWN")
    nets = {pin: name for ref, pin, name in observed.pin_nets if ref == component_ref}
    if (
        set(nets) != set(_PINS)
        or len(nets) != 5
        or any(not name for name in nets.values())
    ):
        raise ValueError("XML_NET_COVERAGE_UNKNOWN")
    return Shtc3EpLibraryObservation(
        symbol_sha256=_digest(symbol_bytes),
        footprint_sha256=_digest(footprint_bytes),
        xml_sha256=_digest(xml_bytes),
        symbol_library_id=symbol_library_id,
        footprint_library_id=footprint_library_id,
        component_ref=component_ref,
        observed_symbol_pins=pins,
        observed_physical_pad_ids=pads,
        observed_xml_nets=tuple(
            ObservedXmlNet(pin, pin, nets[pin]) for pin in sorted(_PINS)
        ),
        non_electrical_paste_pad_count=paste_count,
    )
