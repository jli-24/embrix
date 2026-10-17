"""Offline parser scope only: private candidate bytes plus isolated XML fixture."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from embedded_copilot.engineering_copilot.schematic_library_observation import (
    parse_shtc3_ep_observation,
)

SYMBOL_ID = "SHTC3_EP:SHTC3_EP"
FOOTPRINT_ID = "SHTC3_EP:Sensirion_DFN-4-1EP_2x2mm_P1mm_EP0.7x1.6mm"
SYMBOL_HASH = "DECBB807BF2AE25A5757D3F64BF596DFC17CA1AE048F71D9DEE0940EE6DD9B63"
FOOTPRINT_HASH = "E53F4556DB4573C0D93AA546F606220C063CC075B17AAA8D29AB44E20AE228EF"
XML = (
    '<export><components><comp ref="U5">'
    '<libsource lib="SHTC3_EP" part="SHTC3_EP"/>'
    "<footprint>" + FOOTPRINT_ID + "</footprint>"
    '<property name="Manufacturer" value="Sensirion"/>'
    '<property name="MPN" value="SHTC3"/>'
    '<property name="Package" value="DFN-4 2x2 mm"/>'
    "</comp></components><libparts>"
    '<libpart lib="SHTC3_EP" part="SHTC3_EP"><pins>'
    '<pin num="1" name="VDD"/><pin num="2" name="SCL"/>'
    '<pin num="3" name="SDA"/><pin num="4" name="VSS"/>'
    '<pin num="5" name="VSS"/>'
    "</pins></libpart></libparts><nets>"
    '<net name="3V3"><node ref="U5" pin="1"/></net>'
    '<net name="I2C_SCL"><node ref="U5" pin="2"/></net>'
    '<net name="I2C_SDA"><node ref="U5" pin="3"/></net>'
    '<net name="GND"><node ref="U5" pin="4"/>'
    '<node ref="U5" pin="5"/></net>'
    "</nets></export>"
).encode()


def _sha(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


@pytest.fixture
def original_bytes():
    root_name = os.environ.get("EMBRIX_SHTC3_PRIVATE_LIBRARY_ROOT")
    if not root_name:
        pytest.skip("NOT_RUN: explicit private SHTC3 candidate root required")
    root = Path(root_name)
    symbol = (root / "SHTC3_EP.kicad_sym").read_bytes()
    footprint = (
        root
        / "footprints"
        / "SHTC3_EP.pretty"
        / "Sensirion_DFN-4-1EP_2x2mm_P1mm_EP0.7x1.6mm.kicad_mod"
    ).read_bytes()
    assert hashlib.sha256(symbol).hexdigest().upper() == SYMBOL_HASH
    assert hashlib.sha256(footprint).hexdigest().upper() == FOOTPRINT_HASH
    return symbol, footprint


def _request(original_bytes, **changes):
    symbol, footprint = original_bytes
    return {
        "symbol_bytes": symbol,
        "footprint_bytes": footprint,
        "xml_bytes": XML,
        "component_ref": "U5",
        "symbol_library_id": SYMBOL_ID,
        "footprint_library_id": FOOTPRINT_ID,
        "symbol_sha256": _sha(symbol),
        "footprint_sha256": _sha(footprint),
        **changes,
    }


def test_actual_private_bytes_and_isolated_xml_observe_five_nets(original_bytes):
    result = parse_shtc3_ep_observation(**_request(original_bytes))
    assert result.symbol_sha256 == _sha(original_bytes[0])
    assert result.footprint_sha256 == _sha(original_bytes[1])
    assert result.xml_sha256 == _sha(XML)
    assert result.observed_symbol_pins == (
        ("1", "VDD"),
        ("2", "SCL"),
        ("3", "SDA"),
        ("4", "VSS"),
        ("5", "VSS"),
    )
    assert result.observed_physical_pad_ids == ("1", "2", "3", "4", "5")
    assert result.non_electrical_paste_pad_count == 5
    assert tuple(
        (net.logical_symbol_pin, net.physical_pad_id, net.xml_net_name)
        for net in result.observed_xml_nets
    ) == (
        ("1", "1", "3V3"),
        ("2", "2", "I2C_SCL"),
        ("3", "3", "I2C_SDA"),
        ("4", "4", "GND"),
        ("5", "5", "GND"),
    )


def test_hash_and_identity_staleness_rejected(original_bytes):
    request = _request(original_bytes)
    bad = (
        {"symbol_sha256": _sha(b"stale")},
        {"footprint_sha256": _sha(b"stale")},
        {"component_ref": "U6"},
        {"symbol_library_id": "Sensor_Humidity:SHTC3_EP"},
        {"footprint_library_id": "Sensor_Humidity:" + FOOTPRINT_ID.split(":", 1)[1]},
    )
    for change in bad:
        with pytest.raises(ValueError):
            parse_shtc3_ep_observation(**{**request, **change})


def test_unsupported_original_library_structures_rejected(original_bytes):
    symbol, footprint = original_bytes
    altered_symbols = (
        symbol.replace(
            FOOTPRINT_ID.encode(),
            ("Sensor_Humidity:" + FOOTPRINT_ID.split(":", 1)[1]).encode(),
        ),
        symbol.replace(b"(version 20251024)", b"(version 2000)"),
        symbol.replace(b'(number "5"', b'(number "6"'),
        symbol.replace(
            b'(symbol "SHTC3_EP"',
            b'(symbol "SHTC3_EP" (extends "SHTC3")',
        ),
    )
    altered_footprints = (
        footprint.replace(b'(pad "5" smd', b'(pad "4" smd'),
        footprint.replace(b'(layers "F.Paste")', b'(layers "F.Cu")', 1),
    )
    for altered in altered_symbols:
        with pytest.raises(ValueError):
            parse_shtc3_ep_observation(
                **_request(
                    original_bytes, symbol_bytes=altered, symbol_sha256=_sha(altered)
                )
            )
    for altered in altered_footprints:
        with pytest.raises(ValueError):
            parse_shtc3_ep_observation(
                **_request(
                    original_bytes,
                    footprint_bytes=altered,
                    footprint_sha256=_sha(altered),
                )
            )


def test_missing_unsafe_or_mismatched_xml_rejected(original_bytes):
    bad_xml = (
        b"",
        b'<!DOCTYPE export [<!ENTITY x "boom">]>' + XML,
        XML.replace(b'<node ref="U5" pin="5"/>', b""),
        XML.replace(b'lib="SHTC3_EP" part="SHTC3_EP"', b'lib="Other" part="SHTC3_EP"'),
        XML.replace(FOOTPRINT_ID.encode(), b"SHTC3_EP:Other"),
        XML.replace(b'<pin num="5" name="VSS"/>', b'<pin num="5" name="NC"/>'),
    )
    for content in bad_xml:
        with pytest.raises(ValueError):
            parse_shtc3_ep_observation(**_request(original_bytes, xml_bytes=content))


@pytest.mark.parametrize(
    "old,new",
    (
        (
            b'(symbol "SHTC3_EP"',
            b'(symbol "SHTC3_EP" (unknown_electrical_structure yes)',
        ),
        (b"(pin power_in line", b'(pin power_in line (alternate "NC" no_connect line)'),
    ),
    ids=("unknown-symbol-child", "alternate-pin-nc"),
)
def test_reviewed_symbol_ambiguity_rejected_with_matching_hash(
    original_bytes, old, new
):
    symbol = original_bytes[0]
    assert old in symbol
    changed = symbol.replace(old, new, 1)
    with pytest.raises(ValueError):
        parse_shtc3_ep_observation(
            **_request(
                original_bytes, symbol_bytes=changed, symbol_sha256=_sha(changed)
            )
        )


@pytest.mark.parametrize(
    "new",
    (
        b'(pad "5" smd custom (options (clearance outline) (anchor rect)) '
        + b"(primitives (gr_poly (pts (xy 0 0) (xy 1 0) (xy 0 1)) "
        + b"(width 0) (fill yes)))",
        b'(pad "5" smd unknown_shape',
    ),
    ids=("custom-pad-primitives", "unknown-pad-shape"),
)
def test_reviewed_pad_profile_ambiguity_rejected_with_matching_hash(
    original_bytes, new
):
    footprint = original_bytes[1]
    old = b'(pad "5" smd roundrect'
    assert old in footprint
    changed = footprint.replace(old, new, 1)
    with pytest.raises(ValueError):
        parse_shtc3_ep_observation(
            **_request(
                original_bytes,
                footprint_bytes=changed,
                footprint_sha256=_sha(changed),
            )
        )


@pytest.mark.parametrize(
    "old,new",
    (
        (b"</footprint>", b"</footprint><footprint>SHTC3_EP:Other</footprint>"),
        (
            b'<libsource lib="SHTC3_EP" part="SHTC3_EP"/>',
            b'<libsource lib="SHTC3_EP" part="SHTC3_EP"/>'
            + b'<libsource lib="Other" part="Other"/>',
        ),
    ),
    ids=("duplicate-conflicting-footprint", "duplicate-conflicting-libsource"),
)
def test_reviewed_xml_identity_ambiguity_rejected(original_bytes, old, new):
    assert old in XML
    changed = XML.replace(old, new, 1)
    with pytest.raises(ValueError):
        parse_shtc3_ep_observation(**_request(original_bytes, xml_bytes=changed))


def test_symbol_version_leaf_cannot_hide_second_atom(original_bytes):
    symbol = original_bytes[0]
    old = b"(version 20251024)"
    assert old in symbol
    changed = symbol.replace(old, b"(version 20251024 2000)", 1)
    with pytest.raises(ValueError):
        parse_shtc3_ep_observation(
            **_request(
                original_bytes, symbol_bytes=changed, symbol_sha256=_sha(changed)
            )
        )


def test_footprint_version_leaf_cannot_hide_variant(original_bytes):
    footprint = original_bytes[1]
    old = b"(version 20260206)"
    assert old in footprint
    changed = footprint.replace(
        old, b"(version 20260206 (unknown_format_variant custom))", 1
    )
    with pytest.raises(ValueError):
        parse_shtc3_ep_observation(
            **_request(
                original_bytes,
                footprint_bytes=changed,
                footprint_sha256=_sha(changed),
            )
        )


def test_xml_pin_cannot_claim_alternate_physical_pad(original_bytes):
    old = b'<pin num="5" name="VSS"/>'
    assert old in XML
    changed = XML.replace(old, b'<pin num="5" name="VSS" physical_pad_id="4"/>', 1)
    with pytest.raises(ValueError):
        parse_shtc3_ep_observation(**_request(original_bytes, xml_bytes=changed))


def test_xml_net_node_cannot_claim_alternate_physical_pad(original_bytes):
    old = b'<node ref="U5" pin="5"/>'
    assert old in XML
    changed = XML.replace(old, b'<node ref="U5" pin="5" physical_pad_id="4"/>', 1)
    with pytest.raises(ValueError):
        parse_shtc3_ep_observation(**_request(original_bytes, xml_bytes=changed))
