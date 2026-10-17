from __future__ import annotations

import copy
from typing import Protocol, runtime_checkable

from embedded_copilot.pcb.parser.kicad import (
    KiCadByteSpanStage,
    stage_kicad_footprint_xy_bytes,
)

from ..exceptions import EDAUnavailable, UnsupportedEDAChange
from ..models import (
    EDAApplyReceipt,
    EDAApplyRequest,
    EDAArtifactReference,
    EDAValidationKind,
    EDAValidationReport,
)


@runtime_checkable
class KiCad10PcbnewPort(Protocol):
    @property
    def exact_version(self) -> str: ...

    def move_footprints(
        self, request: EDAApplyRequest, staging_artifact: EDAArtifactReference
    ) -> EDAApplyReceipt: ...


@runtime_checkable
class KiCadCLIValidationPort(Protocol):
    @property
    def exact_version(self) -> str: ...

    def run_validation(
        self,
        artifact: EDAArtifactReference,
        kind: EDAValidationKind,
        argv: tuple[str, ...],
    ) -> EDAValidationReport: ...


def _require_kicad_10(version: str) -> None:
    parts = version.split(".")
    if len(parts) < 2 or parts[0] != "10" or not parts[1].isdigit():
        raise EDAUnavailable("unsupported KiCad version")


def stage_kicad_footprint_position_bytes(
    payload: bytes,
    native_uuid: str,
    *,
    expected_old: tuple[object, object],
    new: tuple[object, object],
    expected_source_sha256: str,
) -> KiCadByteSpanStage:
    """Offline C2-I staging only; no native backend, path, or file write."""
    return stage_kicad_footprint_xy_bytes(
        payload,
        native_uuid,
        expected_old=expected_old,
        new=new,
        expected_source_sha256=expected_source_sha256,
    )


class KiCad10StagingApplyAdapter:
    __slots__ = ("_backend",)

    def __init__(self, backend: KiCad10PcbnewPort) -> None:
        if not isinstance(backend, KiCad10PcbnewPort):
            raise TypeError("pcbnew backend is invalid")
        _require_kicad_10(backend.exact_version)
        self._backend = backend

    def apply(
        self, request: EDAApplyRequest, staging_artifact: EDAArtifactReference
    ) -> EDAApplyReceipt:
        if staging_artifact.artifact_type.value != "BOARD" or any(
            change.operation != "MOVE_FOOTPRINT"
            for change in request.change_set.changes
        ):
            raise UnsupportedEDAChange("only staging board MOVE_FOOTPRINT is supported")
        return self._backend.move_footprints(
            copy.deepcopy(request), copy.deepcopy(staging_artifact)
        )


class KiCad10ValidationAdapter:
    __slots__ = ("_backend",)

    def __init__(self, backend: KiCadCLIValidationPort) -> None:
        if not isinstance(backend, KiCadCLIValidationPort):
            raise TypeError("KiCad CLI backend is invalid")
        _require_kicad_10(backend.exact_version)
        self._backend = backend

    def validate(
        self, artifact: EDAArtifactReference, kind: EDAValidationKind
    ) -> EDAValidationReport:
        if kind is EDAValidationKind.ERC:
            argv = (
                "kicad-cli",
                "sch",
                "erc",
                "--format",
                "json",
                "--exit-code-violations",
            )
        else:
            argv = (
                "kicad-cli",
                "pcb",
                "drc",
                "--format",
                "json",
                "--exit-code-violations",
            )
        return self._backend.run_validation(copy.deepcopy(artifact), kind, argv)


__all__ = (
    "KiCad10PcbnewPort",
    "KiCad10StagingApplyAdapter",
    "KiCad10ValidationAdapter",
    "KiCadCLIValidationPort",
)
