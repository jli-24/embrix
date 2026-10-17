"""C2-P offline experiment only; synthetic inputs are not third-party oracles."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from embedded_copilot.pcb.parser.kicad import observe_kicad_pcb_bytes
from tests.pcb.parser.kicad_byte_span_experiment import (
    SpanExperimentRejected,
    patch_footprint_xy,
)

ROOT = Path(__file__).resolve().parents[3]
ORIGINAL = (
    ROOT
    / "acceptance/s06-third-party/kicad-pic-programmer-8.0.9/original"
    / "pic_programmer.kicad_pcb"
)
TARGET_UUID = "00000000-0000-0000-0000-000054032b86"
SYNTHETIC_UUID = "01234567-89ab-4cde-8f01-23456789abcd"


def _board(footprint: str, *, version: str = "20240108") -> bytes:
    return (
        f"(kicad_pcb (version {version}) (generator pcbnew)\n{footprint}\n)\n"
    ).encode()


def _footprint(*, at: str = "(at 10 20 90)", uuid: str = SYNTHETIC_UUID) -> str:
    return (
        f'(footprint "Library:Part" (layer "F.Cu") (uuid "{uuid}") '
        f'{at} (property "Reference" "U1") '
        '(pad "1" smd rect (at 0 0) (net 1 "GND")))'
    )


def _assert_only_selected_tokens_change(
    before: bytes, after: bytes, edits: tuple
) -> None:
    assert len(edits) == 2
    cursor = 0
    pieces = []
    for edit in edits:
        assert before[edit.start : edit.end] == edit.before
        assert edit.start >= cursor
        pieces.extend((before[cursor : edit.start], edit.after))
        cursor = edit.end
    pieces.append(before[cursor:])
    assert after == b"".join(pieces)


def test_third_party_manual_locator_and_byte_locality() -> None:
    before = ORIGINAL.read_bytes()
    provenance = json.loads((ORIGINAL.parent.parent / "provenance.json").read_bytes())
    claim = provenance["oracle"]["claims"][0]
    assert provenance["oracle"]["source_line_range"] == [232, 238]
    assert (claim["native_id"], claim["locator"], claim["at"]) == (
        TARGET_UUID,
        "/kicad_pcb/footprint[0]",
        [110.49, 78.867, 180.0],
    )
    assert sha256(before).hexdigest() == (
        "b9d90e6184a1e4873d50be65da1faefaf7d003638ed16cef8e0212f247e64da7"
    )
    assert TARGET_UUID.encode() in before.splitlines()[233]
    assert b"(at 110.49 78.867 180)" in before.splitlines()[234]
    assert (
        b'(uuid "' + TARGET_UUID.encode() + b'")\n\t\t(at 110.49 78.867 180)' in before
    )

    result = patch_footprint_xy(
        before,
        TARGET_UUID,
        expected_old=(110.49, 78.867),
        new=(111.49, 79.867),
    )

    _assert_only_selected_tokens_change(before, result.patched, result.edits)
    assert tuple(edit.before for edit in result.edits) == (b"110.49", b"78.867")
    assert tuple(edit.after for edit in result.edits) == (b"111.49", b"79.867")
    assert result.patched != before
    # The production observation parser is independent of the experiment's
    # byte-offset writer. It reads the patched in-memory bytes anew.
    observed = observe_kicad_pcb_bytes("pic_programmer.kicad_pcb", result.patched)
    assert observed.native_format_version == "20240108"
    target = next(item for item in observed.footprints if item.native_id == TARGET_UUID)
    assert target.locator == "/kicad_pcb/footprint[0]"
    assert (target.position_x_mm, target.position_y_mm, target.rotation_degrees) == (
        111.49,
        79.867,
        180.0,
    )


@pytest.mark.parametrize(
    ("source", "expected_code"),
    [
        (
            _board(_footprint(uuid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")),
            "TARGET_NOT_FOUND",
        ),
        (_board(_footprint() + _footprint()), "DUPLICATE_TARGET"),
        (
            _board(_footprint() + f'(group (uuid "{SYNTHETIC_UUID}"))'),
            "DUPLICATE_TARGET",
        ),
        (_board("(group " + _footprint() + ")"), "TARGET_HIERARCHY_INVALID"),
        (
            _board(
                '(footprint "L:P" (at 10 20) (pad "1" (uuid "' + SYNTHETIC_UUID + '")))'
            ),
            "TARGET_HIERARCHY_INVALID",
        ),
        (_board(_footprint(at="(at 10 20) (at 11 21)")), "AT_CARDINALITY"),
        (
            _board(_footprint(at='(property "x" "y") (pad "1" (at 10 20))')),
            "AT_CARDINALITY",
        ),
        (_board(_footprint(at="(at 10mm 20)")), "INVALID_COORDINATE"),
        (_board(_footprint(at="(at nan 20)")), "INVALID_COORDINATE"),
        (_board(_footprint(at="(at 10 inf)")), "INVALID_COORDINATE"),
        (_board(_footprint(), version="20260206"), "UNSUPPORTED_FORMAT_VERSION"),
        (_board(_footprint() + " #unsupported"), "UNSUPPORTED_SYNTAX"),
        (_board(_footprint())[:-2], "UNSUPPORTED_SYNTAX"),
        (_board(_footprint() + r' (property "bad" "\q")'), "UNSUPPORTED_SYNTAX"),
    ],
)
def test_synthetic_invalid_inputs_fail_closed(
    source: bytes, expected_code: str
) -> None:
    with pytest.raises(SpanExperimentRejected) as error:
        patch_footprint_xy(
            source,
            SYNTHETIC_UUID,
            expected_old=(10, 20),
            new=(11, 21),
        )
    assert error.value.code == expected_code


def test_synthetic_old_position_mismatch_rejected() -> None:
    with pytest.raises(SpanExperimentRejected) as error:
        patch_footprint_xy(
            _board(_footprint()),
            SYNTHETIC_UUID,
            expected_old=(10, 99),
            new=(11, 21),
        )
    assert error.value.code == "OLD_COORDINATE_MISMATCH"


@pytest.mark.parametrize("new", [(float("nan"), 21), (11, float("inf")), (True, 21)])
def test_synthetic_nonfinite_or_boolean_target_rejected(new: tuple) -> None:
    with pytest.raises(SpanExperimentRejected) as error:
        patch_footprint_xy(
            _board(_footprint()), SYNTHETIC_UUID, expected_old=(10, 20), new=new
        )
    assert error.value.code == "INVALID_COORDINATE"


def test_synthetic_unicode_escape_nested_at_and_different_numeric_lengths() -> None:
    footprint = (
        f'(footprint "L:Ω" (uuid "{SYNTHETIC_UUID}") (layer "F.Cu") '
        "(at -1.25 2 90) "
        '(property "Line" "first\nsecond") '
        r'(property "Note" "Ω \" (at 999 888) \\ escaped") '
        '(fp_text value "x" (at 7 8)) '
        '(unknown_future (at 444 555) (nested "(" ")")) '
        '(pad "1" smd rect (at 0 0) (net 1 "GND")))'
    )
    before = _board(footprint)
    result = patch_footprint_xy(
        before,
        SYNTHETIC_UUID,
        expected_old=(-1.25, 2),
        new=(123.4567, -234.5),
    )
    _assert_only_selected_tokens_change(before, result.patched, result.edits)
    assert tuple(edit.before for edit in result.edits) == (b"-1.25", b"2")
    assert tuple(edit.after for edit in result.edits) == (b"123.4567", b"-234.5")
    assert b"(at 7 8)" in result.patched
    assert b"(at 444 555)" in result.patched
    assert b'(at 0 0) (net 1 "GND")' in result.patched


def test_synthetic_uuid_text_is_not_native_identity() -> None:
    quoted = f'(property "Note" "(uuid \\"{SYNTHETIC_UUID}\\")")'
    source = _board(_footprint() + quoted)
    result = patch_footprint_xy(
        source, SYNTHETIC_UUID, expected_old=(10, 20), new=(11, 21)
    )
    _assert_only_selected_tokens_change(source, result.patched, result.edits)
