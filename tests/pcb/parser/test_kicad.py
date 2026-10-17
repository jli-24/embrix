from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from embedded_copilot.input import InputLoader
from embedded_copilot.pcb.exceptions import PCBParseError
from embedded_copilot.pcb.models import PCBNetType
from embedded_copilot.pcb.parser import KiCadPCBParser, RootedPCBSourceResolver
from embedded_copilot.pcb.parser import kicad as kicad_parser_module
from tests.pcb.parser.fixtures import (
    COMPLETE_KICAD_BOARD,
    EMPTY_KICAD_BOARD,
    LEGACY_MODULE_BOARD,
    STACKED_PAD_BOARD,
)


def _parse(tmp_path: Path, content: str, *, filename: str = "demo.kicad_pcb"):
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")
    attachment = InputLoader(tmp_path).load(
        filename,
        attachment_id="fixture-board",
        content_type="application/x-kicad-pcb",
    )
    parser = KiCadPCBParser(
        RootedPCBSourceResolver(tmp_path, {attachment.id: filename})
    )
    return parser.parse(attachment)


def _native_board(
    *, version: str = "20240108", uuid: str | None = "01234567-89ab-4cde-8f01-23456789abcd"
) -> bytes:
    native_id = f"(uuid {uuid})" if uuid is not None else ""
    return (
        f'(kicad_pcb (version {version}) (generator pcbnew) '
        '(layers (0 "F.Cu" signal)) '
        f'(footprint "Library:Part" (layer "F.Cu") {native_id} '
        '(at 10 20 90) (property "Reference" "U1") '
        '(pad "1" smd rect (at 0 0) (net 1 "GND"))))'
    ).encode()


def test_native_observation_keeps_uuid_locator_and_omits_pad_net_claims() -> None:
    result = kicad_parser_module.observe_kicad_pcb_bytes(
        "hardware/demo.kicad_pcb", _native_board()
    )

    assert result.native_format_version == "20240108"
    assert len(result.footprints) == 1
    footprint = result.footprints[0]
    assert footprint.native_id == "01234567-89ab-4cde-8f01-23456789abcd"
    assert footprint.locator == "/kicad_pcb/footprint[0]"
    assert footprint.library_id == "Library:Part"
    assert footprint.reference == "U1"
    assert (footprint.position_x_mm, footprint.position_y_mm, footprint.rotation_degrees) == (
        10.0,
        20.0,
        90.0,
    )
    assert not hasattr(footprint, "pins")
    assert not hasattr(footprint, "nets")


def test_native_observation_unknown_version_is_inventory_only() -> None:
    result = kicad_parser_module.observe_kicad_pcb_bytes(
        "demo.kicad_pcb", _native_board(version="20990101")
    )

    assert result.native_format_version == "UNKNOWN"
    assert result.footprints == ()
    assert (result.issues[0].kind, result.issues[0].code) == (
        "UNKNOWN",
        "UNSUPPORTED_FORMAT_VERSION",
    )


def test_native_observation_missing_uuid_is_loss_without_identity_guess() -> None:
    result = kicad_parser_module.observe_kicad_pcb_bytes(
        "demo.kicad_pcb", _native_board(uuid=None)
    )

    assert result.footprints == ()
    assert (result.issues[0].kind, result.issues[0].code) == (
        "LOSS",
        "FOOTPRINT_UUID_MISSING",
    )


def test_native_observation_duplicate_uuid_is_conflict_not_two_objects() -> None:
    source = _native_board().decode("utf-8")
    first = source.index('(footprint "Library:Part"')
    footprint = source[first:-1]
    source = source[:-1] + footprint + ")"

    result = kicad_parser_module.observe_kicad_pcb_bytes(
        "demo.kicad_pcb", source.encode("utf-8")
    )

    assert result.footprints == ()
    assert "DUPLICATE_FOOTPRINT_UUID" in {issue.code for issue in result.issues}


def test_native_observation_uuid_identity_is_case_insensitive() -> None:
    source = _native_board().decode()
    first = source.index('(footprint "Library:Part"')
    footprint = source[first:-1]
    source = source[:-1] + footprint.replace(
        "01234567-89ab-4cde-8f01-23456789abcd",
        "01234567-89AB-4CDE-8F01-23456789ABCD",
    ) + ")"

    result = kicad_parser_module.observe_kicad_pcb_bytes(
        "demo.kicad_pcb", source.encode()
    )

    assert result.footprints == ()
    assert "DUPLICATE_FOOTPRINT_UUID" in {issue.code for issue in result.issues}


def test_kicad_parser_extracts_complete_supported_structure(tmp_path: Path) -> None:
    model = _parse(tmp_path, COMPLETE_KICAD_BOARD)

    assert model.board_name == "demo"
    assert model.metadata == {"format_version": 20240108, "generator": "pcbnew"}
    assert [(layer.index, layer.name, layer.type) for layer in model.layers] == [
        (0, "F.Cu", "signal"),
        (31, "B.Cu", "signal"),
        (36, "B.SilkS", "user"),
    ]
    component = model.components[0]
    assert component.reference == "U1"
    assert component.value == "ESP32-S3"
    assert component.footprint == "QFN-56"
    assert component.library == "Package_DFN_QFN"
    assert component.position.model_dump() == {"x_mm": 10.0, "y_mm": 20.0}
    assert component.rotation == 90.0
    assert [(pin.number, pin.pad_type, pin.net_name) for pin in component.pins] == [
        ("1", "smd", "GND"),
        ("2", "smd", "+3V3"),
        ("3", "smd", "CLK_OUT"),
    ]
    assert [(net.name, net.net_type) for net in model.nets] == [
        ("GND", PCBNetType.GROUND),
        ("+3V3", PCBNetType.POWER),
        ("CLK_OUT", PCBNetType.CLOCK),
    ]
    assert model.nets[0].nodes[0].model_dump() == {"reference": "U1", "pin": "1"}
    assert model.tracks[0].net_name == "GND"
    assert model.tracks[0].width_mm == 0.25
    assert model.vias[0].net_name == "GND"
    assert model.vias[0].layers == ("F.Cu", "B.Cu")
    assert model.zones[0].model_dump(mode="json") == {
        "name": "Ground fill",
        "net_name": "GND",
        "layers": ["F.Cu"],
        "polygon_points": [],
    }


def test_kicad_parser_does_not_modify_source(tmp_path: Path) -> None:
    path = tmp_path / "demo.kicad_pcb"
    path.write_bytes(COMPLETE_KICAD_BOARD.encode("utf-8"))
    attachment = InputLoader(tmp_path).load(
        path.name,
        attachment_id="read-only-board",
        content_type="application/x-kicad-pcb",
    )
    parser = KiCadPCBParser(
        RootedPCBSourceResolver(tmp_path, {attachment.id: path.name})
    )
    original = path.read_bytes()
    model = parser.parse(attachment)

    assert model.components
    assert path.read_bytes() == original


def test_kicad_parser_supports_legacy_module_labels(tmp_path: Path) -> None:
    model = _parse(tmp_path, LEGACY_MODULE_BOARD, filename="legacy.kicad_pcb")

    assert model.components[0].reference == "U2"
    assert model.components[0].value == "STM32F103"
    assert model.components[0].library == "Package_QFP"
    assert model.components[0].footprint == "LQFP-48"


def test_kicad_parser_collapses_identical_stacked_pads(tmp_path: Path) -> None:
    model = _parse(tmp_path, STACKED_PAD_BOARD)

    assert [(pin.number, pin.net_name) for pin in model.components[0].pins] == [
        ("1", "BUTTON"),
    ]


def test_kicad_parser_preserves_zone_polygon_geometry(tmp_path: Path) -> None:
    content = COMPLETE_KICAD_BOARD.replace(
        '(zone (net 1) (net_name "GND") (layer "F.Cu") (name "Ground fill"))',
        '(zone (net 1) (net_name "GND") (layer "F.Cu") (name "Ground fill") '
        '(polygon (pts (xy 0 0) (xy 10 0) (xy 10 10) (xy 0 10))))',
    )
    model = _parse(tmp_path, content)

    assert model.zones[0].polygon_points == (
        (0.0, 0.0),
        (10.0, 0.0),
        (10.0, 10.0),
        (0.0, 10.0),
    )


def test_kicad_parser_normalizes_uppercase_extension_in_board_name(
    tmp_path: Path,
) -> None:
    model = _parse(tmp_path, COMPLETE_KICAD_BOARD, filename="DEMO.KICAD_PCB")

    assert model.board_name == "DEMO"


@pytest.mark.parametrize(
    "content",
    [
        "(kicad_pcb (version 20240108)",
        "(not_a_board (version 20240108))",
        EMPTY_KICAD_BOARD,
        "(kicad_pcb (version 20240108) (footprint \"Incomplete\"))",
        """(kicad_pcb
          (version 20240108)
          (layers (0 "F.Cu" signal))
          (net 1 "GND")
          (footprint "Library:Part"
            (layer "F.Cu") (at 1 2)
            (property "Reference" "U1")
            (property "Value" "Part")
            (pad "1" smd rect (net 99 "MISSING_NET"))))""",
        """(kicad_pcb
          (version 20240108)
          (layers (0 "F.Cu" signal))
          (footprint "Library:Part"
            (layer "Missing.Cu") (at 1 2)
            (property "Reference" "U1")
            (property "Value" "Part")))""",
    ],
)
def test_kicad_parser_safely_rejects_malformed_or_incomplete_boards(
    tmp_path: Path,
    content: str,
) -> None:
    with pytest.raises(PCBParseError) as captured:
        _parse(tmp_path, content)

    message = str(captured.value)
    assert str(tmp_path) not in message


def test_kicad_parser_redacts_source_content_from_errors(tmp_path: Path) -> None:
    content = '(kicad_pcb (generator "PRIVATE_CONTENT_SENTINEL C:/Users/private")'

    with pytest.raises(PCBParseError) as captured:
        _parse(tmp_path, content)

    assert "PRIVATE_CONTENT_SENTINEL" not in str(captured.value)
    assert "C:/Users" not in str(captured.value)


def test_kicad_parser_rejects_unsupported_attachment_type(tmp_path: Path) -> None:
    path = tmp_path / "board.kicad_sch"
    path.write_text("(kicad_sch (version 20240108))", encoding="utf-8")
    attachment = InputLoader(tmp_path).load(
        path.name,
        attachment_id="schematic",
        content_type="application/x-kicad-schematic",
    )
    parser = KiCadPCBParser(
        RootedPCBSourceResolver(tmp_path, {attachment.id: path.name})
    )

    with pytest.raises(PCBParseError, match="PCB attachment is unsupported"):
        parser.parse(attachment)


def test_kicad_parser_rejects_size_change_after_metadata_load(tmp_path: Path) -> None:
    path = tmp_path / "changed.kicad_pcb"
    path.write_text(COMPLETE_KICAD_BOARD, encoding="utf-8")
    attachment = InputLoader(tmp_path).load(
        path.name,
        attachment_id="changed",
        content_type="application/x-kicad-pcb",
    )
    path.write_text(COMPLETE_KICAD_BOARD + " ", encoding="utf-8")
    parser = KiCadPCBParser(
        RootedPCBSourceResolver(tmp_path, {attachment.id: path.name})
    )

    with pytest.raises(PCBParseError, match="PCB source validation failed"):
        parser.parse(attachment)


def test_kicad_parser_rejects_source_change_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "changed-during-read.kicad_pcb"
    path.write_text(COMPLETE_KICAD_BOARD, encoding="utf-8")
    attachment = InputLoader(tmp_path).load(
        path.name,
        attachment_id="changed-during-read",
        content_type="application/x-kicad-pcb",
    )
    parser = KiCadPCBParser(
        RootedPCBSourceResolver(tmp_path, {attachment.id: path.name})
    )
    actual_fstat = kicad_parser_module.os.fstat
    calls = 0

    def changed_fstat(descriptor: int) -> object:
        nonlocal calls
        original = actual_fstat(descriptor)
        calls += 1
        if calls == 1:
            return original
        return SimpleNamespace(
            st_mode=original.st_mode,
            st_size=original.st_size,
            st_dev=original.st_dev,
            st_ino=original.st_ino,
            st_mtime_ns=original.st_mtime_ns + 1,
            st_ctime_ns=original.st_ctime_ns,
        )

    monkeypatch.setattr(kicad_parser_module.os, "fstat", changed_fstat)

    with pytest.raises(PCBParseError, match="PCB source validation failed"):
        parser.parse(attachment)


def test_kicad_parser_rejects_symlink_replacement(tmp_path: Path) -> None:
    target = tmp_path / "target.kicad_pcb"
    target.write_text(COMPLETE_KICAD_BOARD, encoding="utf-8")
    alias = tmp_path / "alias.kicad_pcb"
    alias.write_text(COMPLETE_KICAD_BOARD, encoding="utf-8")
    attachment = InputLoader(tmp_path).load(
        alias.name,
        attachment_id="symlink-replacement",
        content_type="application/x-kicad-pcb",
    )
    alias.unlink()
    try:
        alias.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    parser = KiCadPCBParser(
        RootedPCBSourceResolver(tmp_path, {attachment.id: alias.name})
    )

    with pytest.raises(PCBParseError, match="PCB source validation failed"):
        parser.parse(attachment)
