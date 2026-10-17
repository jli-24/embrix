from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from embedded_copilot.manufacturing.board import (
    measure_copper_to_edge,
    measure_copper_to_edge_text,
    min_trace_spacing_from_routes,
)
from embedded_copilot.engineering_pcb import PCBManufacturingMeasurements


def _board(*, copper: str = '(segment (start 2 1) (end 8 1) (width 0.2) (layer "F.Cu"))') -> str:
    return f'''(kicad_pcb (version 20240108) (generator pcbnew)
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user "Edge.Cuts"))
  (gr_rect (start 0 0) (end 10 10) (stroke (width 0.05) (type default)) (fill none) (layer "Edge.Cuts"))
  {copper}
)'''


def test_copper_to_edge_uses_current_geometry_and_records_minimum_object(tmp_path) -> None:
    content = _board().encode("utf-8")
    path = tmp_path / "main.kicad_pcb"
    path.write_bytes(content)
    fingerprint = "sha256:" + hashlib.sha256(content).hexdigest()

    result = measure_copper_to_edge(path, artifact_fingerprint=fingerprint)

    assert result.artifact_fingerprint == fingerprint
    assert result.actual_minimum_mm == pytest.approx(0.9)
    assert result.minimum_object.startswith("segment:")
    assert result.edge_segment == (0.0, 0.0, 10.0, 0.0)
    assert result.affected_objects == (result.minimum_object,)


def test_copper_to_edge_rejects_stale_artifact_fingerprint(tmp_path) -> None:
    path = tmp_path / "main.kicad_pcb"
    path.write_text(_board(), encoding="utf-8")

    with pytest.raises(ValueError, match="fingerprint is stale"):
        measure_copper_to_edge(path, artifact_fingerprint="sha256:" + "0" * 64)


def test_copper_to_edge_does_not_guess_when_geometry_is_unavailable() -> None:
    board_without_copper = _board(copper="")

    with pytest.raises(ValueError, match="no copper primitive"):
        measure_copper_to_edge_text(board_without_copper, artifact_fingerprint="sha256:" + "a" * 64)


def test_copper_to_edge_rejects_missing_zone_geometry() -> None:
    zone = '''(zone (net 0) (net_name "") (layers "F.Cu")
    (polygon (pts (xy 1 1) (xy 9 1)))
  )'''

    with pytest.raises(ValueError, match="zone geometry is missing"):
        measure_copper_to_edge_text(_board(copper=zone), artifact_fingerprint="sha256:" + "a" * 64)


def test_manufacturing_measurements_project_real_geometry_evidence(tmp_path) -> None:
    content = _board().encode("utf-8")
    path = tmp_path / "main.kicad_pcb"
    path.write_bytes(content)
    fingerprint = "sha256:" + hashlib.sha256(content).hexdigest()
    state = SimpleNamespace(
        project_id="project-geometry",
        source_artifact_fingerprint=fingerprint,
        board=SimpleNamespace(copper_layers=("F.Cu", "B.Cu"), width_mm=10.0, height_mm=10.0),
        stackup=SimpleNamespace(material=None, thickness_mm=1.6),
        routing=(),
        vias=(),
    )

    measurements = PCBManufacturingMeasurements.from_artifact(state, path)

    assert measurements.min_copper_to_edge_mm == pytest.approx(0.9)
    assert measurements.copper_to_edge_evidence is not None
    assert measurements.copper_to_edge_evidence.artifact_fingerprint == fingerprint


def test_min_trace_spacing_uses_distinct_net_routes() -> None:
    routes = (
        SimpleNamespace(layer="F.Cu", net_id="N1", start=(0.0, 0.0), end=(10.0, 0.0), width_mm=0.2),
        SimpleNamespace(layer="F.Cu", net_id="N2", start=(0.0, 1.0), end=(10.0, 1.0), width_mm=0.2),
    )

    assert min_trace_spacing_from_routes(routes) == pytest.approx(0.8)
