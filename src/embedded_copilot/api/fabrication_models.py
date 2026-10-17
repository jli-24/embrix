from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from embedded_copilot.manufacturing import (
    FabricationMode,
    FabricationSourceRevision,
    ManufacturingCheck,
    ManufacturingParameterDecision,
    ManufacturingSpecification,
    UpstreamGate,
)

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:#/+\-]{0,159}$")
_FP = re.compile(r"^sha256:[a-f0-9]{64}$")


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value.strip()) is None:
        raise ValueError(f"{field} is invalid")
    return value.strip()


def _fingerprint(value: object, field: str) -> str:
    if not isinstance(value, str) or _FP.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")
    return value


def _relative_path(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} is invalid")
    normalized = value.replace("\\", "/")
    if (
        normalized != value
        or normalized.startswith(("/", "//"))
        or ":" in normalized
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise ValueError(f"{field} is invalid")
    return normalized


class FabricationReadinessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=False)

    project_id: str
    source: FabricationSourceRevision
    pcb_relative_path: str
    bom_relative_path: str
    schematic_relative_path: str
    elf_relative_path: str
    requirement_context: dict[str, object] | None = None
    requirement_specification: dict[str, object] | None = None
    engineering_decision: dict[str, object] | None = None
    design_truth: dict[str, object] | None = None
    pcb_artifact: dict[str, object] | None = None
    bom_artifact: dict[str, object] | None = None
    existing_evidence: tuple[dict[str, object], ...] = ()
    explicit_mode: FabricationMode | None = None
    allow_pcb_only_without_assembly: bool = False
    mode_approval_ref: str | None = None
    mode_approved_by: str | None = None
    mode_evidence_refs: tuple[str, ...] = ()
    specification: ManufacturingSpecification | None = None
    parameter_decisions: tuple[ManufacturingParameterDecision, ...] = ()
    upstream_checks: tuple[ManufacturingCheck, ...] = ()
    upstream_gates: tuple[UpstreamGate, ...] = ()

    _project_id = field_validator("project_id", mode="before")(
        lambda value: _identifier(value, "project_id")
    )
    _paths = field_validator(
        "pcb_relative_path", "bom_relative_path", "schematic_relative_path", "elf_relative_path", mode="before"
    )(
        lambda value, info: _relative_path(value, info.field_name)
    )
    _mode_approval = field_validator("mode_approval_ref", "mode_approved_by", mode="before")(
        lambda value, info: None if value is None else _identifier(value, info.field_name)
    )
    _mode_evidence = field_validator("mode_evidence_refs", mode="before")(
        lambda value: tuple(_identifier(item, "mode_evidence_ref") for item in value)
        if isinstance(value, (tuple, list)) else (_ for _ in ()).throw(ValueError("mode_evidence_refs is invalid"))
    )

    @field_validator("source", mode="before")
    @classmethod
    def _source_contract(cls, value: object) -> FabricationSourceRevision:
        return FabricationSourceRevision.model_validate(
            value.model_dump(mode="python") if isinstance(value, FabricationSourceRevision) else value,
            strict=False,
        )

    @field_validator("specification", mode="before")
    @classmethod
    def _specification_contract(cls, value: object) -> ManufacturingSpecification | None:
        if value is None:
            return None
        return ManufacturingSpecification.model_validate(
            value.model_dump(mode="python") if isinstance(value, ManufacturingSpecification) else value,
            strict=False,
        )

    @field_validator("parameter_decisions", mode="before")
    @classmethod
    def _parameter_decision_contracts(cls, value: object) -> tuple[ManufacturingParameterDecision, ...]:
        if not isinstance(value, (tuple, list)):
            raise TypeError("parameter_decisions is invalid")
        return tuple(
            ManufacturingParameterDecision.model_validate(
                item.model_dump(mode="python") if isinstance(item, ManufacturingParameterDecision) else item,
                strict=False,
            )
            for item in value
        )

    @field_validator("upstream_checks", mode="before")
    @classmethod
    def _check_contracts(cls, value: object) -> tuple[ManufacturingCheck, ...]:
        if not isinstance(value, (tuple, list)):
            raise TypeError("upstream_checks is invalid")
        return tuple(
            ManufacturingCheck.model_validate(
                item.model_dump(mode="python") if isinstance(item, ManufacturingCheck) else item,
                strict=False,
            )
            for item in value
        )

    @field_validator("upstream_gates", mode="before")
    @classmethod
    def _gate_contracts(cls, value: object) -> tuple[UpstreamGate, ...]:
        if not isinstance(value, (tuple, list)):
            raise TypeError("upstream_gates is invalid")
        return tuple(
            UpstreamGate.model_validate(
                item.model_dump(mode="python") if isinstance(item, UpstreamGate) else item,
                strict=False,
            )
            for item in value
        )

    @model_validator(mode="after")
    def _source_binding(self) -> FabricationReadinessRequest:
        if self.source.project_id != self.project_id:
            raise ValueError("fabrication source project mismatch")
        return self


class FabricationPackageRequest(FabricationReadinessRequest):
    specification: ManufacturingSpecification
    target_id: str
    target_fingerprint: str
    workspace_snapshot_id: str
    workspace_fingerprint: str
    proposal_id: str
    proposal_fingerprint: str
    conversation_id: str
    output_root_relative: str = "manufacturing_package"

    _binding_ids = field_validator(
        "target_id", "workspace_snapshot_id", "proposal_id", "conversation_id", mode="before"
    )(
        lambda value, info: _identifier(value, info.field_name)
    )
    _binding_fps = field_validator("target_fingerprint", "workspace_fingerprint", "proposal_fingerprint", mode="before")(
        lambda value, info: _fingerprint(value, info.field_name)
    )
    _output_root = field_validator("output_root_relative", mode="before")(
        lambda value: _relative_path(value, "output_root_relative")
    )
    @model_validator(mode="after")
    def _specification_is_bound(self) -> FabricationPackageRequest:
        if (
            self.specification.project_id != self.project_id
            or self.specification.project_revision != self.source.project_revision
        ):
            raise ValueError("fabrication specification binding mismatch")
        return self


__all__ = ("FabricationPackageRequest", "FabricationReadinessRequest")
