from __future__ import annotations

import copy

from embedded_copilot.engineering_copilot import ArchitectureDesign

from ..contracts import EngineeringArchitectureSnapshot, EngineeringConfidence


def architecture_snapshot_from_canonical(
    design: ArchitectureDesign,
) -> EngineeringArchitectureSnapshot:
    """Project canonical architecture to the legacy completion read model.

    This adapter is deliberately one-way: the completion model cannot be used to
    construct or mutate ``ArchitectureDesign``.
    """

    checked = ArchitectureDesign.model_validate(
        copy.deepcopy(design.model_dump(mode="python")), strict=False
    )
    component_names = tuple(item.name for item in checked.system_components)
    component_by_id = {item.component_id: item.name for item in checked.system_components}
    interfaces = tuple(
        f"{component_by_id[item.source_component_id]} -> "
        f"{component_by_id[item.target_component_id]} ({item.protocol})"
        for item in checked.interfaces
    )
    return EngineeringArchitectureSnapshot.create(
        project_id=checked.project_id,
        components=component_names,
        interfaces=interfaces,
        constraints=checked.constraints,
        decision_references=tuple(item.decision_id for item in checked.decisions),
        confidence=EngineeringConfidence.PROJECTED,
    )


__all__ = ("architecture_snapshot_from_canonical",)
