from __future__ import annotations

import copy
from collections.abc import Mapping

from ..models import PCBComponent, PCBDatasheetEvidence


class FakePCBDatasheetSourceAdapter:
    def __init__(
        self,
        evidence: Mapping[tuple[str, str], tuple[PCBDatasheetEvidence, ...]]
        | None = None,
    ) -> None:
        self._evidence = copy.deepcopy(dict(evidence or {}))

    def get_evidence(
        self, project_id: str, component: PCBComponent
    ) -> tuple[PCBDatasheetEvidence, ...]:
        values = self._evidence.get((project_id, component.reference), ())
        return tuple(
            PCBDatasheetEvidence.model_validate(item.model_dump(mode="python"))
            for item in values
        )
