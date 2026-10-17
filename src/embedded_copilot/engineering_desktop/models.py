from __future__ import annotations

import copy
from datetime import datetime
from enum import StrEnum

from pydantic import field_validator, model_validator

from embedded_copilot.application import ApplicationQuestionRequest
from embedded_copilot.model_gateway.contracts import safe_text as model_safe_text

from .contracts import (
    FingerprintedDesktopContract,
    deterministic_id,
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


class DesktopSessionStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


class DesktopInteractionType(StrEnum):
    QUESTION = "QUESTION"
    VIEW_WORKSPACE = "VIEW_WORKSPACE"
    VIEW_EVIDENCE = "VIEW_EVIDENCE"
    VIEW_TIMELINE = "VIEW_TIMELINE"
    VIEW_PROPOSAL = "VIEW_PROPOSAL"
    VIEW_REVIEW = "VIEW_REVIEW"


class DesktopReferenceKind(StrEnum):
    PROJECT = "PROJECT"
    WORKSPACE_SNAPSHOT = "WORKSPACE_SNAPSHOT"
    TIMELINE_ENTRY = "TIMELINE_ENTRY"
    EVIDENCE = "EVIDENCE"
    ARTIFACT = "ARTIFACT"
    PROPOSAL = "PROPOSAL"
    REVIEW = "REVIEW"
    ANSWER = "ANSWER"
    MODEL = "MODEL"


class DesktopProjectionStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REQUEST_CHANGE = "REQUEST_CHANGE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


class DesktopProvider(StrEnum):
    OPENAI = "OPENAI"
    GLM = "GLM"
    QWEN = "QWEN"
    DEEPSEEK = "DEEPSEEK"
    OLLAMA = "OLLAMA"
    VLLM = "VLLM"


class DesktopRuntimeStatus(StrEnum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class DesktopRuntimeComponent(StrEnum):
    APPLICATION_RUNTIME = "APPLICATION_RUNTIME"
    MODEL_GATEWAY = "MODEL_GATEWAY"
    WORKSPACE = "WORKSPACE"
    CAPABILITY_ROUTER = "CAPABILITY_ROUTER"
    HARDWARE_RUNTIME = "HARDWARE_RUNTIME"


class DesktopAuditEventType(StrEnum):
    DESKTOP_SESSION_OPENED = "DESKTOP_SESSION_OPENED"
    DESKTOP_SESSION_CLOSED = "DESKTOP_SESSION_CLOSED"
    DESKTOP_SESSION_EXPIRED = "DESKTOP_SESSION_EXPIRED"
    PROJECT_VIEWED = "PROJECT_VIEWED"
    QUESTION_SUBMITTED = "QUESTION_SUBMITTED"
    ANSWER_DISPLAYED = "ANSWER_DISPLAYED"
    EVIDENCE_VIEWED = "EVIDENCE_VIEWED"
    PROPOSAL_VIEWED = "PROPOSAL_VIEWED"
    REVIEW_VIEWED = "REVIEW_VIEWED"


class DesktopSession(FingerprintedDesktopContract):
    session_id: str
    project_id: str
    requester: str
    opened_at: datetime
    last_activity: datetime
    expires_at: datetime
    status: DesktopSessionStatus

    @field_validator("session_id", "project_id", "requester", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("opened_at", "last_activity", "expires_at", mode="before")
    @classmethod
    def _times(cls, value: object, info) -> datetime:
        return utc_datetime(value, field=info.field_name)

    @model_validator(mode="after")
    def _time_order(self):
        if self.opened_at > self.last_activity or self.last_activity > self.expires_at:
            raise ValueError("desktop session time binding mismatch")
        return self


class DesktopReferenceView(FingerprintedDesktopContract):
    reference_kind: DesktopReferenceKind
    source_domain: str
    source_kind: str
    reference_id: str
    source_fingerprint: str
    status: DesktopProjectionStatus | None = None
    relation_type: str | None = None
    occurred_at: datetime | None = None

    @field_validator("source_domain", "source_kind", "reference_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return fingerprint(value, field="source_fingerprint")

    @field_validator("relation_type", mode="before")
    @classmethod
    def _relation(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="relation_type")

    @field_validator("occurred_at", mode="before")
    @classmethod
    def _occurred(cls, value: object) -> datetime | None:
        return None if value is None else utc_datetime(value, field="occurred_at")


class DesktopWorkspaceView(FingerprintedDesktopContract):
    project_reference: DesktopReferenceView
    workspace_snapshot_reference: DesktopReferenceView
    timeline: tuple[DesktopReferenceView, ...]
    artifacts: tuple[DesktopReferenceView, ...]
    evidence: tuple[DesktopReferenceView, ...]
    active_proposals: tuple[DesktopReferenceView, ...]
    pending_reviews: tuple[DesktopReferenceView, ...]
    generated_at: datetime

    @field_validator("project_reference", "workspace_snapshot_reference", mode="before")
    @classmethod
    def _view(cls, value: object) -> DesktopReferenceView:
        return _exact(value, DesktopReferenceView, field="desktop reference")

    @field_validator(
        "timeline",
        "artifacts",
        "evidence",
        "active_proposals",
        "pending_reviews",
        mode="before",
    )
    @classmethod
    def _views(cls, value: object) -> tuple[DesktopReferenceView, ...]:
        return tuple(
            _exact(item, DesktopReferenceView, field="desktop reference")
            for item in tuple_only(value, field="desktop references")
        )

    @field_validator("generated_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="generated_at")


class DesktopOpenProjectRequest(FingerprintedDesktopContract):
    session_id: str
    project_id: str
    requester: str
    expires_at: datetime

    @field_validator("session_id", "project_id", "requester", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("expires_at", mode="before")
    @classmethod
    def _expires(cls, value: object) -> datetime:
        return utc_datetime(value, field="expires_at")


class DesktopProjectView(FingerprintedDesktopContract):
    session: DesktopSession
    workspace: DesktopWorkspaceView

    @field_validator("session", mode="before")
    @classmethod
    def _session(cls, value: object) -> DesktopSession:
        return _exact(value, DesktopSession, field="desktop session")

    @field_validator("workspace", mode="before")
    @classmethod
    def _workspace(cls, value: object) -> DesktopWorkspaceView:
        return _exact(value, DesktopWorkspaceView, field="desktop workspace")


class DesktopInteractionRequest(FingerprintedDesktopContract):
    session_id: str
    project_id: str
    request_type: DesktopInteractionType
    reference: DesktopReferenceView | None = None
    timestamp: datetime

    @field_validator("session_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("reference", mode="before")
    @classmethod
    def _reference(cls, value: object) -> DesktopReferenceView | None:
        return (
            None
            if value is None
            else _exact(value, DesktopReferenceView, field="desktop reference")
        )

    @field_validator("timestamp", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="timestamp")

    @model_validator(mode="after")
    def _request_binding(self):
        if self.request_type is DesktopInteractionType.QUESTION:
            if self.reference is not None:
                raise ValueError(
                    "desktop question interaction cannot contain reference"
                )
        elif self.reference is None:
            raise ValueError("desktop view interaction requires reference")
        return self


class DesktopQuestionRequest(FingerprintedDesktopContract):
    session_id: str
    project_id: str
    application_request: ApplicationQuestionRequest
    timestamp: datetime

    @field_validator("session_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("application_request", mode="before")
    @classmethod
    def _application_request(cls, value: object) -> ApplicationQuestionRequest:
        return _exact(value, ApplicationQuestionRequest, field="application question")

    @field_validator("timestamp", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="timestamp")

    @model_validator(mode="after")
    def _binding(self):
        if self.application_request.project_id != self.project_id:
            raise ValueError("desktop question project mismatch")
        return self


class DesktopSessionCloseRequest(FingerprintedDesktopContract):
    session_id: str
    project_id: str
    timestamp: datetime

    @field_validator("session_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="timestamp")


class DesktopAnswerView(FingerprintedDesktopContract):
    answer_reference: DesktopReferenceView
    evidence_reference: DesktopReferenceView
    model_reference: DesktopReferenceView
    timestamp: datetime

    @field_validator(
        "answer_reference", "evidence_reference", "model_reference", mode="before"
    )
    @classmethod
    def _references(cls, value: object) -> DesktopReferenceView:
        return _exact(value, DesktopReferenceView, field="desktop answer reference")

    @field_validator("timestamp", mode="before")
    @classmethod
    def _timestamp(cls, value: object) -> datetime:
        return utc_datetime(value, field="timestamp")


class DesktopAnswerDisplay(FingerprintedDesktopContract):
    """Ephemeral product projection; never persisted in Desktop state or audit."""

    answer_view: DesktopAnswerView
    content: str
    timestamp: datetime

    @field_validator("answer_view", mode="before")
    @classmethod
    def _answer_view(cls, value: object) -> DesktopAnswerView:
        return _exact(value, DesktopAnswerView, field="desktop answer view")

    @field_validator("content", mode="before")
    @classmethod
    def _content(cls, value: object) -> str:
        return model_safe_text(
            value, field="content", maximum=16384, allow_newlines=True
        )

    @field_validator("timestamp", mode="before")
    @classmethod
    def _display_timestamp(cls, value: object) -> datetime:
        return utc_datetime(value, field="timestamp")


class DesktopModelProviderView(FingerprintedDesktopContract):
    provider: DesktopProvider
    model: str
    capabilities: tuple[str, ...]
    status: DesktopRuntimeStatus

    @field_validator("model", mode="before")
    @classmethod
    def _model(cls, value: object) -> str:
        return identifier(value, field="model")

    @field_validator("capabilities", mode="before")
    @classmethod
    def _capabilities(cls, value: object) -> tuple[str, ...]:
        items = tuple(
            identifier(item, field="capability")
            for item in tuple_only(value, field="capabilities")
        )
        if not items or len(items) != len(set(items)):
            raise ValueError("desktop provider capabilities are invalid")
        return tuple(sorted(items))


class DesktopModelSettingsView(FingerprintedDesktopContract):
    providers: tuple[DesktopModelProviderView, ...]
    generated_at: datetime

    @field_validator("providers", mode="before")
    @classmethod
    def _providers(cls, value: object) -> tuple[DesktopModelProviderView, ...]:
        items = tuple(
            _exact(item, DesktopModelProviderView, field="desktop model provider")
            for item in tuple_only(value, field="providers")
        )
        if len({item.provider for item in items}) != len(items):
            raise ValueError("desktop providers are not unique")
        return tuple(sorted(items, key=lambda item: item.provider.value))

    @field_validator("generated_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="generated_at")


class DesktopRuntimeComponentView(FingerprintedDesktopContract):
    component: DesktopRuntimeComponent
    status: DesktopRuntimeStatus


class RuntimeStatusView(FingerprintedDesktopContract):
    components: tuple[DesktopRuntimeComponentView, ...]
    generated_at: datetime

    @field_validator("components", mode="before")
    @classmethod
    def _components(cls, value: object) -> tuple[DesktopRuntimeComponentView, ...]:
        items = tuple(
            _exact(item, DesktopRuntimeComponentView, field="desktop runtime component")
            for item in tuple_only(value, field="components")
        )
        if {item.component for item in items} != set(DesktopRuntimeComponent):
            raise ValueError("desktop runtime status coverage is invalid")
        return tuple(sorted(items, key=lambda item: item.component.value))

    @field_validator("generated_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="generated_at")


class DesktopAuditEvent(FingerprintedDesktopContract):
    event_id: str
    event_type: DesktopAuditEventType
    session_id: str
    session_fingerprint: str
    project_id: str
    reference_fingerprint: str | None
    timestamp: datetime

    @field_validator("event_id", "session_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("session_fingerprint", mode="before")
    @classmethod
    def _session_fp(cls, value: object) -> str:
        return fingerprint(value, field="session_fingerprint")

    @field_validator("reference_fingerprint", mode="before")
    @classmethod
    def _reference_fp(cls, value: object) -> str | None:
        return (
            None if value is None else fingerprint(value, field="reference_fingerprint")
        )

    @field_validator("timestamp", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="timestamp")

    @model_validator(mode="after")
    def _event_id_binding(self):
        expected = deterministic_id(
            "desktop-event",
            self.session_fingerprint,
            self.event_type.value,
            self.reference_fingerprint or "none",
        )
        if self.event_id != expected:
            raise ValueError("desktop audit event identity mismatch")
        return self

    @classmethod
    def event_id_for(
        cls,
        session_fingerprint: str,
        event_type: DesktopAuditEventType,
        reference_fingerprint: str | None = None,
    ) -> str:
        return deterministic_id(
            "desktop-event",
            fingerprint(session_fingerprint, field="session_fingerprint"),
            event_type.value,
            "none"
            if reference_fingerprint is None
            else fingerprint(reference_fingerprint, field="reference_fingerprint"),
        )


__all__ = tuple(
    name
    for name in globals()
    if name.startswith("Desktop") or name == "RuntimeStatusView"
)
