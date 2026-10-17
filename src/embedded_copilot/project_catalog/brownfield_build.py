"""Catalog-owned, source-grounded context for an attached firmware build."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

from pydantic import field_validator, model_validator

from embedded_copilot.desktop_copilot_chat.contracts import (
    FingerprintedChatContract,
    fingerprint,
    identifier,
    safe_text,
    utc_datetime,
)
from embedded_copilot.engineering_copilot import (
    EngineeringPlan,
    PlanReviewDecision,
    PlanReviewStatus,
    PlanStatus,
)
from embedded_copilot.firmware_engineering import FirmwareBuildConfiguration
from embedded_copilot.firmware_engineering.models import canonical_fingerprint


class BrownfieldFirmwareSpec(FingerprintedChatContract):
    firmware_spec_id: str
    project_id: str
    workspace_fingerprint: str
    source_revision: str
    target_platform: str
    mcu_family: str
    mcu_exact_part: str
    source_layout: tuple[str, ...]
    build_system: str
    toolchain_family: str

    @field_validator("firmware_spec_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("workspace_fingerprint", "source_revision", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("target_platform", "mcu_family", "mcu_exact_part", "build_system", "toolchain_family", mode="before")
    @classmethod
    def _text(cls, value: object, info) -> str:
        return safe_text(value, field=info.field_name, maximum=128)

    @field_validator("source_layout", mode="before")
    @classmethod
    def _sources(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, (tuple, list)) or not value or len(value) > 1024:
            raise ValueError("source layout is invalid")
        from embedded_copilot.engineering_workspace_apply.contracts import (
            relative_reference,
        )

        paths = tuple(relative_reference(item, field="source_layout") for item in value)
        if len(paths) != len(set(paths)):
            raise ValueError("source layout contains duplicates")
        return paths


class BrownfieldImplementationSpec(FingerprintedChatContract):
    implementation_id: str
    project_id: str
    firmware_spec_id: str
    firmware_spec_fingerprint: str
    build_configuration_id: str
    build_configuration_fingerprint: str

    @field_validator("implementation_id", "project_id", "firmware_spec_id", "build_configuration_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("firmware_spec_fingerprint", "build_configuration_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)


class BrownfieldBuildContext(FingerprintedChatContract):
    project_id: str
    tenant_id: str
    requester: str
    project_fingerprint: str
    workspace_target_id: str
    workspace_target_fingerprint: str
    workspace_fingerprint: str
    plan: EngineeringPlan
    review: PlanReviewDecision | None = None
    firmware_spec: BrownfieldFirmwareSpec
    implementation_spec: BrownfieldImplementationSpec
    configuration: FirmwareBuildConfiguration
    created_at: datetime
    updated_at: datetime

    @field_validator("project_id", "tenant_id", "requester", "workspace_target_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("project_fingerprint", "workspace_target_fingerprint", "workspace_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _times(cls, value: object, info) -> datetime:
        return utc_datetime(value, field=info.field_name)

    @model_validator(mode="after")
    def _bindings(self) -> BrownfieldBuildContext:
        spec = self.firmware_spec
        implementation = self.implementation_spec
        configuration_fingerprint = canonical_fingerprint(self.configuration)
        context_material = (
            self.project_fingerprint, self.workspace_fingerprint, self.workspace_target_fingerprint,
            spec.fingerprint, implementation.fingerprint,
        )
        context_fingerprint = "sha256:" + hashlib.sha256(
            json.dumps(context_material, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if (
            self.plan.project_id != self.project_id
            or self.plan.context_fingerprint != context_fingerprint
            or len(self.plan.phases) != 1
            or tuple(output.output_type for output in self.plan.phases[0].outputs) != ("BUILD_ARTIFACT",)
            or spec.project_id != self.project_id
            or implementation.project_id != self.project_id
            or spec.workspace_fingerprint != self.workspace_fingerprint
            or implementation.firmware_spec_id != spec.firmware_spec_id
            or implementation.firmware_spec_fingerprint != spec.fingerprint
            or implementation.build_configuration_fingerprint != configuration_fingerprint
            or implementation.build_configuration_id != "build-config-" + configuration_fingerprint[7:39]
            or self.configuration.target_kind != spec.mcu_exact_part
            or self.configuration.build_system != spec.build_system
            or self.configuration.toolchain_id != spec.toolchain_family
            or self.updated_at < self.created_at
        ):
            raise ValueError("brownfield build context binding mismatch")
        if self.review is None:
            if self.plan.status is not PlanStatus.PROPOSED:
                raise ValueError("brownfield build proposal status mismatch")
        elif (
            self.review.project_id != self.project_id
            or self.review.plan_id != self.plan.plan_id
            or self.review.proposal_fingerprint != self.plan.content_fingerprint
            or (self.review.status is PlanReviewStatus.APPROVED and self.plan.status is not PlanStatus.APPROVED)
            or (self.review.status is PlanReviewStatus.REJECTED and self.plan.status is not PlanStatus.REJECTED)
            or self.review.status is PlanReviewStatus.PENDING
        ):
            raise ValueError("brownfield build review binding mismatch")
        return self

    @property
    def build_configuration_id(self) -> str:
        return self.implementation_spec.build_configuration_id

    @property
    def source_layout(self) -> tuple[str, ...]:
        return self.firmware_spec.source_layout
