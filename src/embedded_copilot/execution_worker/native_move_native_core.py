"""Standard-library-only finite native pcbnew getter projection, imported by fixed wrapper."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from uuid import UUID

SCHEMA = "kicad10-native-finite-v2"

def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _uuid(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("native UUID unavailable")
    normalized = str(UUID(value))
    if value != normalized:
        raise ValueError("native UUID is not canonical")
    return normalized


def _mm_to_nm(value: object) -> int:
    try:
        scaled = Decimal(str(value)) * 1_000_000
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("native coordinate unavailable") from error
    if not scaled.is_finite() or scaled != scaled.to_integral_value():
        raise ValueError("native coordinate exceeds nanometre precision")
    return int(scaled)


def _degrees_to_micro(value: object) -> int:
    try:
        scaled = Decimal(str(value)) * 1_000_000
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("native orientation unavailable") from error
    if not scaled.is_finite() or scaled != scaled.to_integral_value():
        raise ValueError("native orientation exceeds microdegree precision")
    return int(scaled)


def _canonical(footprints: list[dict], target_uuid: str) -> dict:
    if not footprints or len({item["uuid"] for item in footprints}) != len(footprints):
        raise ValueError("native footprint coverage unavailable")
    targets = [item for item in footprints if item["uuid"] == target_uuid]
    if len(targets) != 1:
        raise ValueError("native target coverage unavailable")
    all_pads = [(item["uuid"], pad) for item in footprints for pad in item["pads"]]
    if len({pad["uuid"] for _, pad in all_pads}) != len(all_pads):
        raise ValueError("duplicate native pad UUID")
    ordered = sorted(footprints, key=lambda item: item["uuid"])
    target = targets[0]
    return {
        "projection_schema": SCHEMA,
        "coverage": {"footprints": len(ordered), "pads": len(all_pads), "target_uuid": target_uuid,
                     "fields": "uuid,reference,layer,placement,pad-uuid-number-type-shape-local-position-rotation-size-net"},
        "native_uuid": target_uuid,
        "x_mm": target["x_nm"] / 1_000_000,
        "y_mm": target["y_nm"] / 1_000_000,
        "rotation_degrees": target["rotation_microdegrees"] / 1_000_000,
        "pad_net_fingerprint": _digest([(item["uuid"], item["pads"]) for item in ordered]),
        "non_target_summary_fingerprint": _digest([
            {key: value for key, value in item.items() if key != "pads"}
            for item in ordered if item["uuid"] != target_uuid
        ]),
    }


def native_projection(board: object, target_uuid: str, pcbnew: object) -> dict:
    """Read independent pcbnew BOARD/FOOTPRINT/PAD getters; no source-text parser."""
    footprints = []
    for footprint in board.GetFootprints():
        uid = _uuid(str(footprint.m_Uuid.AsString()))
        position = footprint.GetPosition()
        pads = []
        for pad in footprint.Pads():
            if pad.GetAttribute() != pcbnew.PAD_ATTRIB_SMD or pad.GetShape() != pcbnew.PAD_SHAPE_RECT:
                raise ValueError("native pad type or geometry coverage unavailable")
            relative = pad.GetFPRelativePosition()
            size = pad.GetSize()
            rotation = pad.GetFPRelativeOrientation().AsDegrees()
            pads.append({
                "uuid": _uuid(str(pad.m_Uuid.AsString())),
                "number": str(pad.GetNumber()), "type": "smd", "shape": "rect",
                "local_x_nm": int(relative.x), "local_y_nm": int(relative.y),
                "local_rotation_microdegrees": _degrees_to_micro(rotation),
                "size_x_nm": int(size.x), "size_y_nm": int(size.y),
                "net_code": int(pad.GetNetCode()), "net_name": str(pad.GetNetname()),
            })
        if len({item["uuid"] for item in pads}) != len(pads):
            raise ValueError("duplicate native pad UUID")
        footprints.append({
            "uuid": uid, "reference": str(footprint.GetReference()),
            "layer": str(footprint.GetLayerName()),
            "x_nm": int(position.x), "y_nm": int(position.y),
            "rotation_microdegrees": _degrees_to_micro(footprint.GetOrientationDegrees()),
            "pads": sorted(pads, key=lambda item: item["uuid"]),
        })
    return _canonical(footprints, _uuid(target_uuid))


def load_native_projection(pcbnew: object, pcb_path: str, target_uuid: str) -> dict:
    """Read through an already supplied pcbnew module; never import, save or mutate the board."""
    board = pcbnew.LoadBoard(pcb_path)
    if board is None:
        raise ValueError("native PCB load unavailable")
    return native_projection(board, target_uuid, pcbnew)
