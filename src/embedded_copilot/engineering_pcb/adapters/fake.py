from __future__ import annotations

import copy
from collections.abc import Mapping

from embedded_copilot.engineering_context import (
    EngineeringContextRequest,
    EngineeringContextSnapshot,
)

from ..exceptions import PCBConflict
from ..models import (
    ParsedPCBArtifact,
    PCBAnalysis,
    PCBArtifactReference,
    PCBRepairHandoff,
    PCBRepairHandoffReceipt,
    PCBRepairHandoffStatus,
    deterministic_id,
)
from .projection import (
    DeterministicPCBOptimizationProjection,
    DeterministicPCBValidationProjection,
)


class FakePCBArtifactSourceAdapter:
    def __init__(self, artifacts: tuple[PCBArtifactReference, ...] = ()) -> None:
        self._items = {
            (item.project_id, item.artifact_id): copy.deepcopy(
                item.model_dump(mode="python")
            )
            for item in artifacts
        }

    def get_artifact(
        self, project_id: str, artifact_id: str
    ) -> PCBArtifactReference | None:
        material = self._items.get((project_id, artifact_id))
        return (
            None
            if material is None
            else PCBArtifactReference.model_validate(copy.deepcopy(material))
        )


class FakePCBParserAdapter:
    def __init__(self, parsed: Mapping[str, ParsedPCBArtifact]) -> None:
        self._items = {key: copy.deepcopy(value) for key, value in parsed.items()}

    def parse(self, artifact: PCBArtifactReference) -> ParsedPCBArtifact:
        value = self._items[artifact.artifact_id]
        return type(value).model_validate(
            copy.deepcopy(value.model_dump(mode="python"))
        )


class FakePCBContextSourceAdapter:
    def __init__(self, snapshots: Mapping[str, EngineeringContextSnapshot]) -> None:
        self._items = {
            key: copy.deepcopy(value.model_dump(mode="python"))
            for key, value in snapshots.items()
        }

    def get_context(
        self, request: EngineeringContextRequest
    ) -> EngineeringContextSnapshot | None:
        material = self._items.get(request.project_id)
        return (
            None
            if material is None
            else EngineeringContextSnapshot.model_validate(copy.deepcopy(material))
        )


class FakePCBValidationAdapter(DeterministicPCBValidationProjection):
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def submit(self, analysis: PCBAnalysis):
        if self.fail:
            raise RuntimeError("injected validation failure")
        return super().submit(analysis)


class FakePCBOptimizationAdapter(DeterministicPCBOptimizationProjection):
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def submit(self, analysis, findings):
        if self.fail:
            raise RuntimeError("injected optimization failure")
        return super().submit(analysis, findings)


class FakePCBRepairAdapter:
    def __init__(self, *, fail: bool = False) -> None:
        self._items: dict[str, dict[str, object]] = {}
        self.fail = fail

    def record(self, handoff: PCBRepairHandoff) -> PCBRepairHandoffReceipt:
        if self.fail:
            raise RuntimeError("injected repair handoff failure")
        checked = PCBRepairHandoff.model_validate(handoff.model_dump(mode="python"))
        existing = self._items.get(checked.handoff_id)
        material = checked.model_dump(mode="python")
        if existing is not None and existing != material:
            raise PCBConflict("repair handoff identity conflict")
        self._items[checked.handoff_id] = copy.deepcopy(material)
        return PCBRepairHandoffReceipt.create(
            receipt_id=deterministic_id("pcb-handoff-receipt", checked.fingerprint),
            project_id=checked.project_id,
            handoff_fingerprint=checked.fingerprint,
            status=PCBRepairHandoffStatus.RECORDED,
            recorded_at=checked.recorded_at,
        )
