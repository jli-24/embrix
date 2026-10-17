from __future__ import annotations

from pathlib import Path

import pytest

from .test_service import _Availability, _request
from embedded_copilot.pcb_production import PCBProductionService, inspect_serializer_boundary, serialize_kicad_pcb
from embedded_copilot.pcb_production.contracts import PCBArtifactDiffStatus


def test_serializer_is_deterministic_and_contains_real_board_sections() -> None:
    proposal = PCBProductionService(footprints=_Availability()).create_proposal(_request())
    first = serialize_kicad_pcb(proposal)
    second = serialize_kicad_pcb(proposal)
    assert first == second
    text = first.decode("utf-8")
    assert text.startswith("(kicad_pcb ")
    assert '(44 "Edge.Cuts" user "Edge.Cuts")' in text
    assert '(segment (start 109.35 109.45)' in text
    assert '(net 1 "NET_A")' in text
    assert "C:\\" not in text
    assert "Path.write_text" not in text


def test_serializer_embeds_the_exact_approved_library_footprint(tmp_path: Path) -> None:
    library = tmp_path / "Resistor_SMD.pretty"
    library.mkdir()
    (library / "R_0603_1608Metric.kicad_mod").write_text(
        '(footprint "R_0603_1608Metric"\n'
        '  (version 20260206)\n'
        '  (generator "test")\n'
        '  (layer "F.Cu")\n'
        '  (property "Reference" "REF**")\n'
        '  (fp_rect (start -0.8 -0.4) (end 0.8 0.4) (stroke (width 0.1) (type solid)) (fill none) (layer "F.SilkS"))\n'
        '  (pad "1" smd roundrect (at -0.825 0) (size 0.8 0.95) (layers "F.Cu" "F.Mask" "F.Paste"))\n'
        '  (pad "2" smd roundrect (at 0.825 0) (size 0.8 0.95) (layers "F.Cu" "F.Mask" "F.Paste"))\n'
        ')\n',
        encoding="utf-8",
    )

    proposal = PCBProductionService(footprints=_Availability()).create_proposal(_request())
    serialized = serialize_kicad_pcb(proposal, footprint_root=tmp_path).decode("utf-8")

    assert '(footprint "R_0603_1608Metric"' in serialized
    assert '(property "Reference" "R1")' in serialized
    assert '(fp_rect (start -0.8 -0.4)' in serialized
    assert '(pad "1" smd roundrect (at -0.825 0)' in serialized
    assert '(net 1 "NET_A")' in serialized
    assert '(fp_rect (start -1 -1)' not in serialized


def test_phase5_serializer_rejects_unapproved_geometry_injection(tmp_path: Path) -> None:
    proposal = PCBProductionService(footprints=_Availability()).create_proposal(
        _request(
            pcb_state_fingerprint="sha256:" + "3" * 64,
            pcb_source_binding_fingerprint="sha256:" + "4" * 64,
            pcb_constraint_set_fingerprint="sha256:" + "5" * 64,
            pcb_verification_plan_fingerprint="sha256:" + "6" * 64,
            pcb_element_binding_fingerprint="sha256:" + "7" * 64,
        )
    )

    with pytest.raises(ValueError, match="serializer boundary|UNAPPROVED_FOOTPRINT_DEFAULT"):
        serialize_kicad_pcb(proposal)
    with pytest.raises(ValueError, match="serializer boundary|EXTERNAL_PLANE_CONFIGURATION"):
        serialize_kicad_pcb(proposal, footprint_root=tmp_path, plane_nets=frozenset({"NET_A"}), plane_layers={"NET_A": "B.Cu"})

    diff = inspect_serializer_boundary(proposal)
    assert diff.status is PCBArtifactDiffStatus.BLOCKED
    assert "UNAPPROVED_FOOTPRINT_DEFAULT" in diff.unexpected_objects
