"""Read-only references projected from the authoritative execution operation.

BUILD_PASS means only that the typed firmware build evidence matches this operation;
it makes no claim about firmware or hardware verification.
"""

from __future__ import annotations

from typing import Literal

from pydantic import field_validator, model_validator

from embedded_copilot.engineering_copilot.common import Fingerprinted, fp, ident

from .contracts import PlanReference, StageReference


class BuildOperationReference(Fingerprinted):
    operation_id: str
    operation_fingerprint: str
    build_request_fingerprint: str

    _id = field_validator("operation_id", mode="before")(
        lambda value: ident(value, "operation_id")
    )
    _fps = field_validator("operation_fingerprint", "build_request_fingerprint", mode="before")(
        lambda value, info: fp(value, info.field_name)
    )


class BuildReceiptReference(Fingerprinted):
    operation_id: str
    receipt_fingerprint: str
    evidence_fingerprint: str | None = None
    artifact_ids: tuple[str, ...] = ()

    _id = field_validator("operation_id", mode="before")(
        lambda value: ident(value, "operation_id")
    )
    _fps = field_validator("receipt_fingerprint", "evidence_fingerprint", mode="before")(
        lambda value, info: None if value is None else fp(value, info.field_name)
    )
    _artifacts = field_validator("artifact_ids", mode="before")(
        lambda value: tuple(ident(item, "artifact_id") for item in value)
        if type(value) is tuple else (_ for _ in ()).throw(ValueError("artifact ids invalid"))
    )


class FirmwareBuildCoordinationState(Fingerprinted):
    """Current execution projection; never an approval or execution authority.

    A Plan BUILD_ARTIFACT output declares stage intent only. The existing
    Operation and independent human approval bind the exact build inputs.
    """

    task_id: str
    tenant_id: str
    project_id: str
    plan_reference: PlanReference
    stage_reference: StageReference
    operation_reference: BuildOperationReference
    coordination_status: Literal["BUILD_PASS", "BUILD_FAILED", "UNVERIFIED", "BLOCKED"]
    receipt_reference: BuildReceiptReference | None = None

    _ids = field_validator("task_id", "tenant_id", "project_id", mode="before")(
        lambda value, info: ident(value, info.field_name)
    )

    @model_validator(mode="after")
    def _bound(self) -> FirmwareBuildCoordinationState:
        if self.stage_reference.plan_reference != self.plan_reference:
            raise ValueError("build stage plan binding mismatch")
        if self.receipt_reference is not None and (
            self.receipt_reference.operation_id != self.operation_reference.operation_id
        ):
            raise ValueError("build receipt operation binding mismatch")
        if self.coordination_status in {"BUILD_PASS", "BUILD_FAILED"} and (
            self.receipt_reference is None
            or self.receipt_reference.evidence_fingerprint is None
        ):
            raise ValueError("terminal build projection requires evidence")
        return self
