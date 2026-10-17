"""The 13 approved finite parser cases, importing production E: code directly."""

from __future__ import annotations

from hashlib import sha256

import pytest

from embedded_copilot.pcb.parser.kicad import observe_kicad_pcb_bytes


def _footprint(fields: str, uuid: str = "01234567-89ab-4cde-8f01-23456789abcd") -> str:
    return f'(footprint "Library:Part" (uuid {uuid}) {fields})'


def _board(fields: str) -> bytes:
    return f'(kicad_pcb (version 20240108) {_footprint(fields)})'.encode()


@pytest.mark.parametrize("fields", [
    '(at 1 2) (at 30 40) (property "Reference" "R1")',
    '(at 1 2) (at 1 2) (property "Reference" "R1")',
    '(at 1 2) (property "Reference" "R1") (property "Reference" "R2")',
    '(at 1 2) (property "Reference" "R1") (property "reference" "R1")',
    '(at 1 2) (fp_text reference "R1") (fp_text reference "R2")',
    '(at 1 2) (property "Reference" "R1") (fp_text reference "R1")',
    '(property "Reference" "R1") (at 30 40) (at 1 2)',
    '(at 1 2) (fp_text reference "R2") (property "Reference" "R1")',
])
def test_ambiguous_singletons_are_not_published(fields: str) -> None:
    payload = _board(fields)
    result = observe_kicad_pcb_bytes("board.kicad_pcb", payload)
    assert result.footprints == ()
    assert result.source_sha256 == sha256(payload).hexdigest()
    assert result.native_format_version == "20240108"
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert (issue.kind, issue.code, issue.locator, issue.blocks_mapping) == (
        "CONFLICT", "AMBIGUOUS_FOOTPRINT_FIELDS", "/kicad_pcb/footprint[0]", True,
    )


@pytest.mark.parametrize("fields,reference", [
    ('(at 1 2) (property "Reference" "R1")', "R1"),
    ('(at 1 2) (fp_text reference "R1")', "R1"),
    ('(at 1 2)', None),
    ('(at 1 2) (property "Value" "a") (property "Value" "b")', None),
])
def test_existing_unambiguous_subset_is_preserved(fields: str, reference: str | None) -> None:
    result = observe_kicad_pcb_bytes("board.kicad_pcb", _board(fields))
    assert len(result.footprints) == 1
    assert result.footprints[0].reference == reference
    assert result.issues == ()


def test_independent_valid_object_remains_visible() -> None:
    bad = _footprint('(at 1 2) (at 3 4) (property "Reference" "R1")')
    good_id = "01234567-89ab-4cde-8f01-23456789abce"
    good = _footprint('(at 5 6) (property "Reference" "R2")', good_id)
    payload = f'(kicad_pcb (version 20240108) {bad} {good})'.encode()
    result = observe_kicad_pcb_bytes("board.kicad_pcb", payload)
    assert [item.native_id for item in result.footprints] == [good_id]
    assert len(result.issues) == 1 and result.issues[0].blocks_mapping is True
