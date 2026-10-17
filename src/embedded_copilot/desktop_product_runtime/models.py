from __future__ import annotations

import copy
from datetime import datetime
from enum import StrEnum

from pydantic import field_validator, model_validator

from embedded_copilot.engineering_desktop import (
    DesktopAnswerDisplay,
    DesktopProjectionStatus,
    DesktopReferenceView,
    DesktopSession,
    DesktopWorkspaceView,
)
from embedded_copilot.model_gateway.contracts import safe_text

from .contracts import (
    FingerprintedDesktopContract,
    fingerprint,
    identifier,
    tuple_only,
    utc_datetime,
)


def _exact(value: object, model: type, *, field: str):
    if type(value) is model:
        value = value.model_dump(mode="python")
    elif type(value) is not dict:
        raise TypeError(f"{field} is invalid")
    return model.model_validate(copy.deepcopy(value))


class ProductSessionStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


class DesktopTheme(StrEnum):
    SYSTEM = "SYSTEM"
    LIGHT = "LIGHT"
    DARK = "DARK"


class NotificationLevel(StrEnum):
    ALL = "ALL"
    IMPORTANT = "IMPORTANT"
    NONE = "NONE"


class TimeDisplayFormat(StrEnum):
    LOCAL = "LOCAL"
    UTC = "UTC"


class ProductEventType(StrEnum):
    SESSION_OPENED = "SESSION_OPENED"
    SESSION_CLOSED = "SESSION_CLOSED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    WORKSPACE_REFRESHED = "WORKSPACE_REFRESHED"
    CHAT_COMPLETED = "CHAT_COMPLETED"
    CHAT_REJECTED = "CHAT_REJECTED"
    CHAT_FAILED = "CHAT_FAILED"
    PREFERENCES_UPDATED = "PREFERENCES_UPDATED"
    RESYNC_REQUIRED = "RESYNC_REQUIRED"


class ProductSessionOpenRequest(FingerprintedDesktopContract):
    product_session_id: str
    project_id: str
    expires_at: datetime

    @field_validator("product_session_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("expires_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="expires_at")


class ProductSession(FingerprintedDesktopContract):
    product_session_id: str
    requester: str
    desktop_session: DesktopSession
    workspace_reference: DesktopReferenceView
    created_at: datetime
    status: ProductSessionStatus

    @field_validator("product_session_id", "requester", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("desktop_session", mode="before")
    @classmethod
    def _desktop_session(cls, value: object) -> DesktopSession:
        return _exact(value, DesktopSession, field="desktop session")

    @field_validator("workspace_reference", mode="before")
    @classmethod
    def _workspace_reference(cls, value: object) -> DesktopReferenceView:
        return _exact(value, DesktopReferenceView, field="workspace reference")

    @field_validator("created_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="created_at")

    @model_validator(mode="after")
    def _binding(self):
        if (
            self.requester != self.desktop_session.requester
            or self.workspace_reference.reference_id == ""
        ):
            raise ValueError("product session binding mismatch")
        return self


class DesktopUserPreferences(FingerprintedDesktopContract):
    requester: str
    theme: DesktopTheme
    sidebar_collapsed: bool
    time_format: TimeDisplayFormat
    notification_level: NotificationLevel
    updated_at: datetime

    @field_validator("requester", mode="before")
    @classmethod
    def _requester(cls, value: object) -> str:
        return identifier(value, field="requester")

    @field_validator("sidebar_collapsed", mode="before")
    @classmethod
    def _collapsed(cls, value: object) -> bool:
        if type(value) is not bool:
            raise ValueError("sidebar_collapsed is invalid")
        return value

    @field_validator("updated_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="updated_at")


class ProjectExplorerItem(FingerprintedDesktopContract):
    reference: DesktopReferenceView
    children: tuple[DesktopReferenceView, ...]

    @field_validator("reference", mode="before")
    @classmethod
    def _reference(cls, value: object) -> DesktopReferenceView:
        return _exact(value, DesktopReferenceView, field="explorer reference")

    @field_validator("children", mode="before")
    @classmethod
    def _children(cls, value: object) -> tuple[DesktopReferenceView, ...]:
        return tuple(
            _exact(item, DesktopReferenceView, field="explorer child")
            for item in tuple_only(value, field="explorer children")
        )


class ProjectExplorerView(FingerprintedDesktopContract):
    project_reference: DesktopReferenceView
    workspace_reference: DesktopReferenceView
    items: tuple[ProjectExplorerItem, ...]
    generated_at: datetime

    @field_validator("project_reference", "workspace_reference", mode="before")
    @classmethod
    def _reference(cls, value: object) -> DesktopReferenceView:
        return _exact(value, DesktopReferenceView, field="explorer reference")

    @field_validator("items", mode="before")
    @classmethod
    def _items(cls, value: object) -> tuple[ProjectExplorerItem, ...]:
        return tuple(
            _exact(item, ProjectExplorerItem, field="explorer item")
            for item in tuple_only(value, field="explorer items")
        )

    @field_validator("generated_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="generated_at")


class TimelineInteractionView(FingerprintedDesktopContract):
    workspace_fingerprint: str
    entries: tuple[DesktopReferenceView, ...]
    generated_at: datetime

    @field_validator("workspace_fingerprint", mode="before")
    @classmethod
    def _fingerprint(cls, value: object) -> str:
        return fingerprint(value, field="workspace_fingerprint")

    @field_validator("entries", mode="before")
    @classmethod
    def _entries(cls, value: object) -> tuple[DesktopReferenceView, ...]:
        return tuple(
            _exact(item, DesktopReferenceView, field="timeline entry")
            for item in tuple_only(value, field="timeline entries")
        )

    @field_validator("generated_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="generated_at")


class DesktopChatRequest(FingerprintedDesktopContract):
    product_session_id: str
    request_id: str
    trace_id: str
    question: str
    constraints: tuple[str, ...]
    requested_at: datetime

    @field_validator("product_session_id", mode="before")
    @classmethod
    def _session_id(cls, value: object) -> str:
        return identifier(value, field="product_session_id")

    @field_validator("request_id", "trace_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("question", mode="before")
    @classmethod
    def _question(cls, value: object) -> str:
        return safe_text(value, field="question", maximum=4096, allow_newlines=True)

    @field_validator("constraints", mode="before")
    @classmethod
    def _constraints(cls, value: object) -> tuple[str, ...]:
        return tuple(
            safe_text(item, field="constraint", maximum=256)
            for item in tuple_only(value, field="constraints")
        )

    @field_validator("requested_at", mode="before")
    @classmethod
    def _requested_at(cls, value: object) -> datetime:
        return utc_datetime(value, field="requested_at")


class DesktopChatResponse(FingerprintedDesktopContract):
    product_session_id: str
    display: DesktopAnswerDisplay
    timestamp: datetime

    @field_validator("product_session_id", mode="before")
    @classmethod
    def _session_id(cls, value: object) -> str:
        return identifier(value, field="product_session_id")

    @field_validator("display", mode="before")
    @classmethod
    def _display(cls, value: object) -> DesktopAnswerDisplay:
        return _exact(value, DesktopAnswerDisplay, field="desktop answer display")

    @field_validator("timestamp", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="timestamp")


class DesktopProductEvent(FingerprintedDesktopContract):
    event_id: str
    sequence: int
    product_session_id: str
    event_type: ProductEventType
    reference_fingerprint: str | None
    status: DesktopProjectionStatus | None
    timestamp: datetime

    @field_validator("event_id", "product_session_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("sequence", mode="before")
    @classmethod
    def _sequence(cls, value: object) -> int:
        if type(value) is not int or value < 1:
            raise ValueError("event sequence is invalid")
        return value

    @field_validator("reference_fingerprint", mode="before")
    @classmethod
    def _reference(cls, value: object) -> str | None:
        return (
            None if value is None else fingerprint(value, field="reference_fingerprint")
        )

    @field_validator("timestamp", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="timestamp")


class DesktopNotificationView(FingerprintedDesktopContract):
    notification_id: str
    event_type: ProductEventType
    reference_fingerprint: str | None
    status: DesktopProjectionStatus | None
    occurred_at: datetime

    @field_validator("notification_id", mode="before")
    @classmethod
    def _id(cls, value: object) -> str:
        return identifier(value, field="notification_id")

    @field_validator("reference_fingerprint", mode="before")
    @classmethod
    def _reference(cls, value: object) -> str | None:
        return (
            None if value is None else fingerprint(value, field="reference_fingerprint")
        )

    @field_validator("occurred_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="occurred_at")


def explorer_from_workspace(view: DesktopWorkspaceView) -> ProjectExplorerView:
    items = tuple(
        ProjectExplorerItem(reference=item, children=())
        for item in sorted(view.artifacts, key=lambda item: item.fingerprint)
    )
    return ProjectExplorerView(
        project_reference=view.project_reference,
        workspace_reference=view.workspace_snapshot_reference,
        items=items,
        generated_at=view.generated_at,
    )


__all__ = tuple(
    name
    for name in globals()
    if name.startswith(("Desktop", "Product", "Project", "Timeline"))
)
