"""Synthetic finite model preconditions; no physical PCB or engineering acceptance.

Only explicit positive construction sites use this helper. Negative raw model
constructors remain unchanged so missing evidence continues to be rejected.
"""
from __future__ import annotations

from embedded_copilot.engineering_pcb import (
    PCBElementBinding,
    PCBIntentBindingStatus,
    PCBReadbackObjectKind,
    PCBReadbackObjectReference,
)
from tests.engineering_pcb.test_phase6_authoritative_facts import (
    FP,
    READBACK_FP,
    readback_object,
)


def bound_element(**values: object) -> PCBElementBinding:
    """Reuse existing synthetic typed readback references for supplied IDs only."""
    assert values.get("status") is PCBIntentBindingStatus.BOUND
    supplied_readback = {
        "pcb_artifact_id", "pcb_artifact_fingerprint",
        "readback_fingerprint", "readback_objects",
    }
    assert not supplied_readback.intersection(values)
    references: list[PCBReadbackObjectReference] = []
    for field, kind, plural in (
        ("pcb_component_id", PCBReadbackObjectKind.COMPONENT, False),
        ("pcb_footprint_id", PCBReadbackObjectKind.FOOTPRINT, False),
        ("pcb_pad_ids", PCBReadbackObjectKind.PAD, True),
        ("pcb_track_ids", PCBReadbackObjectKind.TRACK, True),
        ("pcb_via_ids", PCBReadbackObjectKind.VIA, True),
        ("pcb_zone_ids", PCBReadbackObjectKind.ZONE, True),
    ):
        value = values.get(field)
        if value is None:
            continue
        if plural:
            assert type(value) is tuple
            identifiers = value
        else:
            identifiers = (value,)
        for object_id in identifiers:
            assert isinstance(object_id, str)
            references.append(readback_object(object_id, kind))
    return PCBElementBinding.create(
        **values,
        pcb_artifact_id="pcb-artifact-1",
        pcb_artifact_fingerprint=FP,
        readback_fingerprint=READBACK_FP,
        readback_objects=tuple(references),
    )
