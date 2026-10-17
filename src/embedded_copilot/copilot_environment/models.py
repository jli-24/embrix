from __future__ import annotations

import copy
from datetime import datetime
from enum import StrEnum

from pydantic import field_validator, model_validator

from .contracts import (
    FingerprintedEnvironmentContract,
    identifier,
    safe_text,
    tuple_only,
    utc_datetime,
)


def _exact(value: object, model: type, *, field: str):
    if type(value) is model:
        value = value.model_dump(mode="python")
    elif type(value) is not dict:
        raise TypeError(f"{field} is invalid")
    return model.model_validate(copy.deepcopy(value))


def _references(value: object, *, field: str) -> tuple[str, ...]:
    return tuple(
        safe_text(item, field=field, maximum=256)
        for item in tuple_only(value, field=field)
    )


class TenantEnvironmentStatus(StrEnum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class ProjectEnvironmentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    DISABLED = "DISABLED"


class RuntimeComponent(StrEnum):
    APPLICATION = "APPLICATION"
    DESKTOP = "DESKTOP"
    MODEL = "MODEL"
    WORKSPACE = "WORKSPACE"
    CAPABILITY = "CAPABILITY"
    HARDWARE = "HARDWARE"


class RuntimeHealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class MemoryViewStatus(StrEnum):
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class EnvironmentEventType(StrEnum):
    PROJECT_CHANGED = "PROJECT_CHANGED"
    RUNTIME_STATUS_CHANGED = "RUNTIME_STATUS_CHANGED"
    AUDIT_AVAILABLE = "AUDIT_AVAILABLE"
    MEMORY_REFERENCE_UPDATED = "MEMORY_REFERENCE_UPDATED"
    TEAM_ACTIVITY_UPDATED = "TEAM_ACTIVITY_UPDATED"


class TenantEnvironment(FingerprintedEnvironmentContract):
    tenant_id: str
    environment_id: str
    created_at: datetime
    status: TenantEnvironmentStatus

    @field_validator("tenant_id", "environment_id", mode="before")
    @classmethod
    def _identifiers(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("created_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="created_at")


class EnvironmentProjectReference(FingerprintedEnvironmentContract):
    tenant_id: str
    project_id: str
    project_name_reference: str
    workspace_reference: str
    status: ProjectEnvironmentStatus

    @field_validator("tenant_id", "project_id", mode="before")
    @classmethod
    def _identifiers(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("project_name_reference", "workspace_reference", mode="before")
    @classmethod
    def _references(cls, value: object, info) -> str:
        return safe_text(value, field=info.field_name, maximum=256)


class ProjectView(FingerprintedEnvironmentContract):
    environment: TenantEnvironment
    project: EnvironmentProjectReference

    @field_validator("environment", mode="before")
    @classmethod
    def _environment(cls, value: object) -> TenantEnvironment:
        return _exact(value, TenantEnvironment, field="tenant environment")

    @field_validator("project", mode="before")
    @classmethod
    def _project(cls, value: object) -> EnvironmentProjectReference:
        return _exact(value, EnvironmentProjectReference, field="project reference")

    @model_validator(mode="after")
    def _tenant_binding(self):
        if self.environment.tenant_id != self.project.tenant_id:
            raise ValueError("project tenant binding mismatch")
        return self


class EngineeringTeamContext(FingerprintedEnvironmentContract):
    tenant_id: str
    team_id: str
    members_reference: tuple[str, ...]
    project_scope: tuple[str, ...]

    @field_validator("tenant_id", "team_id", mode="before")
    @classmethod
    def _identifiers(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("members_reference", "project_scope", mode="before")
    @classmethod
    def _reference_values(cls, value: object, info) -> tuple[str, ...]:
        values = _references(value, field=info.field_name)
        if len(values) != len(set(values)):
            raise ValueError(f"{info.field_name} must be unique")
        return values


class CollaborationView(FingerprintedEnvironmentContract):
    environment: TenantEnvironment
    team: EngineeringTeamContext
    active_reviewer_references: tuple[str, ...]
    pending_review_references: tuple[str, ...]
    recent_activity_references: tuple[str, ...]

    @field_validator("environment", mode="before")
    @classmethod
    def _environment(cls, value: object) -> TenantEnvironment:
        return _exact(value, TenantEnvironment, field="tenant environment")

    @field_validator("team", mode="before")
    @classmethod
    def _team(cls, value: object) -> EngineeringTeamContext:
        return _exact(value, EngineeringTeamContext, field="team context")

    @field_validator(
        "active_reviewer_references",
        "pending_review_references",
        "recent_activity_references",
        mode="before",
    )
    @classmethod
    def _reference_values(cls, value: object, info) -> tuple[str, ...]:
        values = _references(value, field=info.field_name)
        if len(values) != len(set(values)):
            raise ValueError(f"{info.field_name} must be unique")
        return values

    @model_validator(mode="after")
    def _tenant_binding(self):
        if self.environment.tenant_id != self.team.tenant_id:
            raise ValueError("team tenant binding mismatch")
        return self


class CopilotRuntimeHealth(FingerprintedEnvironmentContract):
    tenant_id: str
    component: RuntimeComponent
    status: RuntimeHealthStatus
    observed_at: datetime

    @field_validator("tenant_id", mode="before")
    @classmethod
    def _tenant(cls, value: object) -> str:
        return identifier(value, field="tenant_id")

    @field_validator("observed_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="observed_at")


class EngineeringAuditView(FingerprintedEnvironmentContract):
    tenant_id: str
    project_id: str
    event_type: str
    object_reference: str
    status: str
    timestamp: datetime

    @field_validator("tenant_id", "project_id", mode="before")
    @classmethod
    def _identifiers(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("event_type", "object_reference", "status", mode="before")
    @classmethod
    def _reference_values(cls, value: object, info) -> str:
        return safe_text(value, field=info.field_name, maximum=256)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="timestamp")


class EngineeringMemoryView(FingerprintedEnvironmentContract):
    tenant_id: str
    project_id: str
    memory_reference: str
    status: MemoryViewStatus

    @field_validator("tenant_id", "project_id", mode="before")
    @classmethod
    def _identifiers(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("memory_reference", mode="before")
    @classmethod
    def _reference(cls, value: object) -> str:
        return safe_text(value, field="memory_reference", maximum=256)


class EnvironmentDeploymentProfile(FingerprintedEnvironmentContract):
    tenant_id: str
    deployment_id: str
    runtime_type: str
    version_reference: str
    status: TenantEnvironmentStatus

    @field_validator("tenant_id", "deployment_id", mode="before")
    @classmethod
    def _identifiers(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("runtime_type", "version_reference", mode="before")
    @classmethod
    def _references(cls, value: object, info) -> str:
        return safe_text(value, field=info.field_name, maximum=256)

    @model_validator(mode="after")
    def _runtime_type(self):
        if self.runtime_type not in {"LOCAL", "ENTERPRISE", "EDGE"}:
            raise ValueError("runtime_type is invalid")
        return self


class EnvironmentEvent(FingerprintedEnvironmentContract):
    tenant_id: str
    event_type: EnvironmentEventType
    reference: str
    status: str
    timestamp: datetime

    @field_validator("tenant_id", mode="before")
    @classmethod
    def _tenant(cls, value: object) -> str:
        return identifier(value, field="tenant_id")

    @field_validator("reference", "status", mode="before")
    @classmethod
    def _reference_values(cls, value: object, info) -> str:
        return safe_text(value, field=info.field_name, maximum=256)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="timestamp")


__all__ = tuple(name for name in globals() if not name.startswith("_"))
