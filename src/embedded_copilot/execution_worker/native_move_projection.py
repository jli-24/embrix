"""Finite KiCad 10 source expectation; native getters live in a standard-library-only core."""

from __future__ import annotations

from embedded_copilot.pcb.parser.kicad import (
    _atom,
    _children,
    _head,
    _parse_s_expression,
)
from embedded_copilot.pcb.parser.models import PCBParserLimits

from .native_move_native_core import (
    SCHEMA,
    _canonical,
    _degrees_to_micro,
    _mm_to_nm,
    _uuid,
    load_native_projection,
    native_projection,
)

READER_ID = "KICAD10_NATIVE_FINITE_V2"

def _only(node: tuple, name: str) -> tuple:
    found = _children(node, name)
    if len(found) != 1:
        raise ValueError(f"native {name} coverage unavailable")
    return found[0]


def _required(node: tuple, index: int) -> str:
    value = _atom(node, index)
    if not value:
        raise ValueError("native source atom unavailable")
    return value


def _source_footprint(node: tuple) -> dict:
    uid = _uuid(_required(_only(node, "uuid"), 1))
    at = _only(node, "at")
    if len(at) not in (3, 4):
        raise ValueError("native footprint placement coverage unavailable")
    ref = [item for item in _children(node, "property") if _atom(item, 1) == "Reference"]
    if len(ref) != 1:
        raise ValueError("native footprint reference coverage unavailable")
    layer = _required(_only(node, "layer"), 1)
    # KiCad 10.0.5 normalizes footprint angles to (-180, 180], but pad
    # file angles to [0, 360). GetFPRelativeOrientation subtracts the former
    # from the latter without normalizing the result (which can exceed 360).
    footprint_rotation = _degrees_to_micro(_atom(at, 3) or "0") % 360_000_000
    if footprint_rotation > 180_000_000:
        footprint_rotation -= 360_000_000
    pads = []
    for pad in _children(node, "pad"):
        # SetOrientation rotates already-added pads. Do not guess at unusual
        # source ordering outside this finite placement-before-pad mapping.
        if node.index(pad) < node.index(at):
            raise ValueError("native footprint placement order coverage unavailable")
        pad_at = _only(pad, "at")
        size = _only(pad, "size")
        if len(pad_at) not in (3, 4) or len(size) != 3 or _required(pad, 2) != "smd" or _required(pad, 3) != "rect":
            raise ValueError("native pad type or geometry coverage unavailable")
        nets = _children(pad, "net")
        if len(nets) > 1 or (nets and len(nets[0]) != 3):
            raise ValueError("native pad net coverage unavailable")
        net_code = 0 if not nets else int(_required(nets[0], 1))
        net_name = "" if not nets else _required(nets[0], 2)
        pads.append({
            "uuid": _uuid(_required(_only(pad, "uuid"), 1)),
            "number": _required(pad, 1), "type": "smd", "shape": "rect",
            "local_x_nm": _mm_to_nm(_required(pad_at, 1)),
            "local_y_nm": _mm_to_nm(_required(pad_at, 2)),
            "local_rotation_microdegrees": (
                _degrees_to_micro(_atom(pad_at, 3) or "0") % 360_000_000
            ) - footprint_rotation,
            "size_x_nm": _mm_to_nm(_required(size, 1)),
            "size_y_nm": _mm_to_nm(_required(size, 2)),
            "net_code": net_code, "net_name": net_name,
        })
    if len({item["uuid"] for item in pads}) != len(pads):
        raise ValueError("duplicate native pad UUID")
    return {
        "uuid": uid, "reference": _required(ref[0], 2), "layer": layer,
        "x_nm": _mm_to_nm(_required(at, 1)), "y_nm": _mm_to_nm(_required(at, 2)),
        "rotation_microdegrees": footprint_rotation,
        "pads": sorted(pads, key=lambda item: item["uuid"]),
    }


def source_projection(payload: bytes, target_uuid: str) -> dict:
    """Derive a finite expected model from frozen source bytes, never from native read-back."""
    if not isinstance(payload, bytes) or b"\x00" in payload:
        raise ValueError("native PCB source invalid")
    root = _parse_s_expression(payload.decode("utf-8", errors="strict"), PCBParserLimits())
    if _head(root) != "kicad_pcb":
        raise ValueError("native PCB root invalid")
    footprints = [_source_footprint(item) for item in _children(root, "footprint")]
    return _canonical(footprints, _uuid(target_uuid))

__all__ = ("READER_ID", "SCHEMA", "load_native_projection", "native_projection", "source_projection")
