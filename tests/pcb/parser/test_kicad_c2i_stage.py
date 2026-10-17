"""C2-I bounded, in-memory KiCad staging; synthetic cases are not native oracles."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from embedded_copilot.pcb.parser.kicad import (
    KiCadByteSpanRejected,
    observe_kicad_pcb_bytes,
    stage_kicad_footprint_xy_bytes,
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
    return f"(kicad_pcb (version {version})\n{footprint}\n)\n".encode()


def _footprint(*, at: str = "(at 10 20 90)", uuid: str = SYNTHETIC_UUID) -> str:
    return (
        f'(footprint "L:Part" (uuid "{uuid}") {at} '
        '(property "Reference" "U1") (pad "1" smd rect (at 0 0) (net 1 "GND")))'
    )


def _stage(
    source: bytes,
    *,
    uuid: str = SYNTHETIC_UUID,
    old: tuple[object, object] = (10, 20),
    new: tuple[object, object] = (11, 21),
    expected_hash: str | None = None,
):
    return stage_kicad_footprint_xy_bytes(
        source,
        uuid,
        expected_old=old,
        new=new,
        expected_source_sha256=expected_hash or sha256(source).hexdigest(),
    )


def _assert_two_token_locality(source: bytes, result) -> None:
    first, second = result.edits
    assert first.start < first.end < second.start < second.end
    assert source[first.start : first.end] == first.before
    assert source[second.start : second.end] == second.before
    assert result.staged_bytes == (
        source[: first.start]
        + first.after
        + source[first.end : second.start]
        + second.after
        + source[second.end :]
    )


def test_third_party_fixed_locator_stages_only_top_level_xy() -> None:
    source = ORIGINAL.read_bytes()
    assert sha256(source).hexdigest() == (
        "b9d90e6184a1e4873d50be65da1faefaf7d003638ed16cef8e0212f247e64da7"
    )
    assert (
        b'(uuid "' + TARGET_UUID.encode() + b'")\n\t\t(at 110.49 78.867 180)' in source
    )
    result = _stage(
        source, uuid=TARGET_UUID, old=(110.49, 78.867), new=(111.49, 79.867)
    )
    _assert_two_token_locality(source, result)
    assert tuple(edit.before for edit in result.edits) == (b"110.49", b"78.867")
    assert tuple(edit.after for edit in result.edits) == (b"111.49", b"79.867")
    observed = observe_kicad_pcb_bytes("pic_programmer.kicad_pcb", result.staged_bytes)
    matching = [item for item in observed.footprints if item.native_id == TARGET_UUID]
    assert len(matching) == 1
    assert (matching[0].position_x_mm, matching[0].position_y_mm) == (111.49, 79.867)
    assert matching[0].rotation_degrees == 180
    assert sha256(ORIGINAL.read_bytes()).hexdigest() == sha256(source).hexdigest()


@pytest.mark.parametrize(
    ("source", "code"),
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
        (
            _board(_footprint() + f'(unknown (uuid "{SYNTHETIC_UUID}" extra))'),
            "UNSUPPORTED_SYNTAX",
        ),
        (_board("(group " + _footprint() + ")"), "TARGET_HIERARCHY_INVALID"),
        (
            _board(
                f'(footprint "L:Part" (at 10 20) (pad "1" (uuid "{SYNTHETIC_UUID}")))'
            ),
            "TARGET_HIERARCHY_INVALID",
        ),
        (_board(_footprint(at="(at 10 20) (at 11 21)")), "AT_CARDINALITY"),
        (_board(_footprint(at='(pad "1" (at 10 20))')), "AT_CARDINALITY"),
        (_board(_footprint(at="(at 10mm 20)")), "INVALID_COORDINATE"),
        (_board(_footprint(at="(at nan 20)")), "INVALID_COORDINATE"),
        (_board(_footprint(), version="20260206"), "UNSUPPORTED_FORMAT_VERSION"),
        (_board(_footprint() + " #comment"), "UNSUPPORTED_SYNTAX"),
        (_board(_footprint())[:-2], "UNSUPPORTED_SYNTAX"),
        (_board(_footprint() + r' (property "bad" "\q")'), "UNSUPPORTED_SYNTAX"),
    ],
)
def test_synthetic_rejection_cases(source: bytes, code: str) -> None:
    with pytest.raises(KiCadByteSpanRejected) as error:
        _stage(source)
    assert error.value.code == code


def test_source_change_rejected_before_stage() -> None:
    source = _board(_footprint())
    with pytest.raises(KiCadByteSpanRejected) as error:
        _stage(source, expected_hash="0" * 64)
    assert error.value.code == "SOURCE_CHANGED"


def test_invalid_hash_or_uuid_is_rejected() -> None:
    source = _board(_footprint())
    with pytest.raises(KiCadByteSpanRejected) as bad_hash:
        _stage(source, expected_hash="not-a-hash")
    assert bad_hash.value.code == "INVALID_STAGE_INPUT"
    with pytest.raises(KiCadByteSpanRejected) as bad_uuid:
        _stage(source, uuid=SYNTHETIC_UUID.upper())
    assert bad_uuid.value.code == "INVALID_TARGET_UUID"


def test_old_coordinate_mismatch_rejected() -> None:
    with pytest.raises(KiCadByteSpanRejected) as error:
        _stage(_board(_footprint()), old=(10, 99))
    assert error.value.code == "OLD_COORDINATE_MISMATCH"


@pytest.mark.parametrize(
    "new", [(float("nan"), 21), (11, float("inf")), (True, 21), (10001, 21)]
)
def test_nonfinite_or_out_of_range_target_rejected(new: tuple[object, object]) -> None:
    with pytest.raises(KiCadByteSpanRejected) as error:
        _stage(_board(_footprint()), new=new)
    assert error.value.code == "INVALID_COORDINATE"


def test_quoted_pseudo_fields_unicode_unknown_nodes_and_nested_at_are_untouched() -> (
    None
):
    footprint = (
        f'(footprint "L:Ω" (uuid "{SYNTHETIC_UUID}") (at -1.25 2 90) '
        '(property "Line" "first\nsecond") '
        r'(property "Note" "Ω \" (at 999 888) \\ escaped") '
        f'(property "PseudoUUID" "(uuid \\"{SYNTHETIC_UUID}\\")") '
        '(fp_text value "x" (at 7 8)) '
        '(unknown_future (at 444 555) (nested "(" ")")) '
        '(pad "1" smd rect (at 0 0) (net 1 "GND")))'
    )
    source = _board(footprint)
    result = _stage(source, old=(-1.25, 2), new=(123.4567, -234.5))
    _assert_two_token_locality(source, result)
    assert tuple(edit.before for edit in result.edits) == (b"-1.25", b"2")
    assert tuple(edit.after for edit in result.edits) == (b"123.4567", b"-234.5")
    assert b"(at 7 8)" in result.staged_bytes
    assert b"(at 444 555)" in result.staged_bytes
    assert b'(at 0 0) (net 1 "GND")' in result.staged_bytes


def test_excessive_numeric_precision_rejected_without_large_output() -> None:
    from decimal import Decimal

    with pytest.raises(KiCadByteSpanRejected) as error:
        _stage(_board(_footprint()), new=(Decimal("1e-1000000"), 21))
    assert error.value.code == "INVALID_COORDINATE"
