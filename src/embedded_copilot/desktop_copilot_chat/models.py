from __future__ import annotations

import copy
import hashlib
from datetime import datetime
from enum import StrEnum

from pydantic import field_validator, model_validator

from embedded_copilot.application import ApplicationQuestionRequest

from .contracts import (
    FingerprintedChatContract,
    fingerprint,
    identifier,
    ResponseType,
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


class ChatSessionStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"


class ChatAnswerStatus(StrEnum):
    COMPLETED = "COMPLETED"


class ChatReferenceKind(StrEnum):
    WORKSPACE = "WORKSPACE"
    TIMELINE = "TIMELINE"
    EVIDENCE = "EVIDENCE"
    PROPOSAL = "PROPOSAL"
    MEMORY = "MEMORY"


class ChatEventType(StrEnum):
    CHAT_STARTED = "CHAT_STARTED"
    ANSWER_CHUNK = "ANSWER_CHUNK"
    ANSWER_COMPLETED = "ANSWER_COMPLETED"
    ANSWER_FAILED = "ANSWER_FAILED"


class AnalysisState(StrEnum):
    REQUESTED = "REQUESTED"
    CONTEXT_READY = "CONTEXT_READY"
    WAITING_FOR_CLARIFICATION = "WAITING_FOR_CLARIFICATION"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DISCOVERY_STARTED = "DISCOVERY_STARTED"
    REQUIREMENTS_PARTIAL = "REQUIREMENTS_PARTIAL"
    REQUIREMENTS_REFINING = "REQUIREMENTS_REFINING"
    REQUIREMENTS_READY = "REQUIREMENTS_READY"
    DESIGN_READY = "DESIGN_READY"


class AnalysisTraceStage(StrEnum):
    UI_SUBMIT = "UI_SUBMIT"
    CHAT_REQUEST_RECEIVED = "CHAT_REQUEST_RECEIVED"
    PROJECT_RESOLVE = "PROJECT_RESOLVE"
    CONVERSATION_RESOLVE = "CONVERSATION_RESOLVE"
    TRANSMISSION_APPROVAL = "TRANSMISSION_APPROVAL"
    APPLICATION_RUNTIME_ENTER = "APPLICATION_RUNTIME_ENTER"
    ROUTE_SELECT = "ROUTE_SELECT"
    CONTEXT_BUILD = "CONTEXT_BUILD"
    SOURCE_SNAPSHOT = "SOURCE_SNAPSHOT"
    FIRMWARE_PARSE = "FIRMWARE_PARSE"
    EVIDENCE_BUILD = "EVIDENCE_BUILD"
    EVIDENCE_GATE = "EVIDENCE_GATE"
    PROVIDER_RESOLVE = "PROVIDER_RESOLVE"
    MODEL_GATEWAY = "MODEL_GATEWAY"
    ANSWER_PERSIST = "ANSWER_PERSIST"
    SSE_TERMINAL = "SSE_TERMINAL"
    UI_PROJECTION = "UI_PROJECTION"


class ChatAuditEventType(StrEnum):
    CHAT_SESSION_OPENED = "CHAT_SESSION_OPENED"
    CHAT_REQUESTED = "CHAT_REQUESTED"
    CHAT_COMPLETED = "CHAT_COMPLETED"
    CHAT_FAILED = "CHAT_FAILED"
    CHAT_CLOSED = "CHAT_CLOSED"


class ChatObservationStatus(StrEnum):
    OBSERVED = "OBSERVED"
    FAILED = "FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class ChatMemoryStatus(StrEnum):
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    APPROVED = "APPROVED"
    OBSERVED = "OBSERVED"


class ChatProjectReference(FingerprintedChatContract):
    tenant_id: str
    project_id: str
    project_name_reference: str
    workspace_reference: str

    @field_validator("tenant_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("project_name_reference", "workspace_reference", mode="before")
    @classmethod
    def _references(cls, value: object, info) -> str:
        return safe_text(value, field=info.field_name, maximum=256)


class ChatContextReference(FingerprintedChatContract):
    tenant_id: str
    project_id: str
    reference_id: str
    reference_kind: ChatReferenceKind
    source_fingerprint: str
    display_label: str

    @field_validator("tenant_id", "project_id", "reference_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def _source(cls, value: object) -> str:
        return fingerprint(value, field="source_fingerprint")

    @field_validator("display_label", mode="before")
    @classmethod
    def _label(cls, value: object) -> str:
        return safe_text(value, field="display_label", maximum=128)


class ChatSessionOpenRequest(FingerprintedChatContract):
    session_id: str
    tenant_id: str
    project_id: str
    requester: str
    expires_at: datetime

    @field_validator(
        "session_id", "tenant_id", "project_id", "requester", mode="before"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("expires_at", mode="before")
    @classmethod
    def _expires(cls, value: object) -> datetime:
        return utc_datetime(value, field="expires_at")


class ChatSession(FingerprintedChatContract):
    session_id: str
    tenant_id: str
    project_id: str
    requester: str
    created_at: datetime
    expires_at: datetime
    status: ChatSessionStatus

    @field_validator(
        "session_id", "tenant_id", "project_id", "requester", mode="before"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("created_at", "expires_at", mode="before")
    @classmethod
    def _times(cls, value: object, info) -> datetime:
        return utc_datetime(value, field=info.field_name)

    @model_validator(mode="after")
    def _lifetime(self):
        if self.expires_at <= self.created_at:
            raise ValueError("chat session lifetime is invalid")
        return self


class DesktopChatRequest(FingerprintedChatContract):
    session_id: str
    project_reference: ChatProjectReference
    application_question: ApplicationQuestionRequest
    external_search: bool = False
    context_references: tuple[ChatContextReference, ...]
    requested_at: datetime

    @field_validator("session_id", mode="before")
    @classmethod
    def _session_id(cls, value: object) -> str:
        return identifier(value, field="session_id")

    @field_validator("project_reference", mode="before")
    @classmethod
    def _project(cls, value: object) -> ChatProjectReference:
        return _exact(value, ChatProjectReference, field="project reference")

    @field_validator("application_question", mode="before")
    @classmethod
    def _question(cls, value: object) -> ApplicationQuestionRequest:
        if type(value) is ApplicationQuestionRequest:
            value = value.model_dump(mode="python")
        if type(value) is not dict:
            raise TypeError("application question is invalid")
        return ApplicationQuestionRequest.model_validate(copy.deepcopy(value))

    @field_validator("external_search", mode="before")
    @classmethod
    def _external_search(cls, value: object) -> bool:
        if type(value) is not bool:
            raise TypeError("external_search is invalid")
        return value

    @field_validator("context_references", mode="before")
    @classmethod
    def _context(cls, value: object) -> tuple[ChatContextReference, ...]:
        items = tuple(
            _exact(item, ChatContextReference, field="chat context reference")
            for item in tuple_only(value, field="context_references")
        )
        if len({item.fingerprint for item in items}) != len(items):
            raise ValueError("chat context references must be unique")
        return items

    @field_validator("requested_at", mode="before")
    @classmethod
    def _requested(cls, value: object) -> datetime:
        return utc_datetime(value, field="requested_at")

    @model_validator(mode="after")
    def _binding(self):
        if self.application_question.project_id != self.project_reference.project_id:
            raise ValueError("chat application question project mismatch")
        if self.application_question.external_search != self.external_search:
            raise ValueError("chat external search binding mismatch")
        if any(
            item.tenant_id != self.project_reference.tenant_id
            or item.project_id != self.project_reference.project_id
            for item in self.context_references
        ):
            raise ValueError("chat context reference binding mismatch")
        return self


class ChatEvidenceView(FingerprintedChatContract):
    source_type: str
    label: str
    reference_id: str
    status: str
    summary: str | None = None
    source_uri: str | None = None
    source_fingerprint: str
    observed_at: datetime

    @field_validator("source_type", "label", "status", mode="before")
    @classmethod
    def _text(cls, value: object, info) -> str:
        return safe_text(value, field=info.field_name, maximum=128)

    @field_validator("reference_id", mode="before")
    @classmethod
    def _reference(cls, value: object) -> str:
        return identifier(value, field="reference_id")

    @field_validator("summary", mode="before")
    @classmethod
    def _summary(cls, value: object) -> str | None:
        return None if value is None else safe_text(value, field="summary", maximum=512)

    @field_validator("source_uri", mode="before")
    @classmethod
    def _source_uri(cls, value: object) -> str | None:
        if value is None:
            return None
        checked = safe_text(value, field="source_uri", maximum=2048)
        if not checked.startswith("https://") or "@" in checked.split("/", 3)[2]:
            raise ValueError("source_uri is invalid")
        return checked

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def _source(cls, value: object) -> str:
        return fingerprint(value, field="source_fingerprint")

    @field_validator("observed_at", mode="before")
    @classmethod
    def _observed(cls, value: object) -> datetime:
        return utc_datetime(value, field="observed_at")


class ChatRecommendationView(FingerprintedChatContract):
    review: str
    reason: str | None = None
    status: str = "PROPOSAL"
    reference_id: str
    source_fingerprint: str

    @field_validator("review", "status", mode="before")
    @classmethod
    def _text(cls, value: object, info) -> str:
        checked = safe_text(value, field=info.field_name, maximum=256)
        if info.field_name == "status" and checked != "PROPOSAL":
            raise ValueError("recommendation status is invalid")
        return checked

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, value: object) -> str | None:
        return None if value is None else safe_text(value, field="reason", maximum=512)

    @field_validator("reference_id", mode="before")
    @classmethod
    def _reference(cls, value: object) -> str:
        return identifier(value, field="reference_id")

    @field_validator("source_fingerprint", mode="before")
    @classmethod
    def _source(cls, value: object) -> str:
        return fingerprint(value, field="source_fingerprint")


class ChatAnswerView(FingerprintedChatContract):
    answer_id: str
    status: ChatAnswerStatus
    answer_text: str
    response_type: ResponseType = ResponseType.CHAT
    artifact_refs: tuple[str, ...] = ()
    evidence_references: tuple[ChatContextReference, ...]
    evidence_details: tuple[ChatEvidenceView, ...] = ()
    recommendations: tuple[ChatRecommendationView, ...] = ()
    created_at: datetime

    @field_validator("response_type", mode="before")
    @classmethod
    def _response_type(cls, value: object) -> ResponseType:
        return value if isinstance(value, ResponseType) else ResponseType(value)

    @field_validator("answer_id", mode="before")
    @classmethod
    def _answer_id(cls, value: object) -> str:
        return identifier(value, field="answer_id")

    @field_validator("answer_text", mode="before")
    @classmethod
    def _answer_text(cls, value: object) -> str:
        return safe_text(value, field="answer_text", maximum=16384)

    @field_validator("artifact_refs", mode="before")
    @classmethod
    def _artifact_refs(cls, value: object) -> tuple[str, ...]:
        return tuple(identifier(item, field="artifact_ref") for item in tuple_only(value, field="artifact_refs"))

    @field_validator("evidence_references", mode="before")
    @classmethod
    def _evidence(cls, value: object) -> tuple[ChatContextReference, ...]:
        return tuple(
            _exact(item, ChatContextReference, field="answer evidence reference")
            for item in tuple_only(value, field="evidence_references")
        )

    @field_validator("evidence_details", mode="before")
    @classmethod
    def _details(cls, value: object) -> tuple[ChatEvidenceView, ...]:
        return tuple(
            _exact(item, ChatEvidenceView, field="answer evidence detail")
            for item in tuple_only(value, field="evidence_details")
        )

    @field_validator("recommendations", mode="before")
    @classmethod
    def _recommendations(cls, value: object) -> tuple[ChatRecommendationView, ...]:
        return tuple(
            _exact(item, ChatRecommendationView, field="answer recommendation")
            for item in tuple_only(value, field="recommendations")
        )

    @field_validator("created_at", mode="before")
    @classmethod
    def _created(cls, value: object) -> datetime:
        return utc_datetime(value, field="created_at")


class ChatAuditEvent(FingerprintedChatContract):
    event_id: str
    event_type: ChatAuditEventType
    session_fingerprint: str
    project_id: str
    request_id: str | None
    answer_fingerprint: str | None
    timestamp: datetime

    @field_validator("event_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("request_id", mode="before")
    @classmethod
    def _request(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="request_id")

    @field_validator("session_fingerprint", mode="before")
    @classmethod
    def _session(cls, value: object) -> str:
        return fingerprint(value, field="session_fingerprint")

    @field_validator("answer_fingerprint", mode="before")
    @classmethod
    def _answer(cls, value: object) -> str | None:
        return None if value is None else fingerprint(value, field="answer_fingerprint")

    @field_validator("timestamp", mode="before")
    @classmethod
    def _timestamp(cls, value: object) -> datetime:
        return utc_datetime(value, field="timestamp")

    @model_validator(mode="after")
    def _audit_binding(self):
        request_event = self.event_type in {
            ChatAuditEventType.CHAT_REQUESTED,
            ChatAuditEventType.CHAT_COMPLETED,
            ChatAuditEventType.CHAT_FAILED,
        }
        if request_event != (self.request_id is not None):
            raise ValueError("chat audit request binding mismatch")
        needs_answer = self.event_type is ChatAuditEventType.CHAT_COMPLETED
        if needs_answer != (self.answer_fingerprint is not None):
            raise ValueError("chat audit answer binding mismatch")
        return self

    @classmethod
    def event_id_for(
        cls,
        session_fingerprint: str,
        event_type: ChatAuditEventType,
        request_id: str | None = None,
    ) -> str:
        value = fingerprint(session_fingerprint, field="session_fingerprint")
        request = (
            "" if request_id is None else identifier(request_id, field="request_id")
        )
        digest = hashlib.sha256(
            f"{value}|{event_type.value}|{request}".encode()
        ).hexdigest()
        return "chat-audit-" + digest[:48]


class ChatEvent(FingerprintedChatContract):
    event_id: str
    sequence: int
    session_id: str
    event_type: ChatEventType
    answer_chunk: str | None
    answer_fingerprint: str | None
    timestamp: datetime
    request_id: str | None = None
    trace_id: str | None = None
    analysis_state: AnalysisState | None = None
    reason_code: str | None = None
    terminal_event: str | None = None

    @field_validator("event_id", "session_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("sequence", mode="before")
    @classmethod
    def _sequence(cls, value: object) -> int:
        if type(value) is not int or value < 1:
            raise ValueError("chat event sequence is invalid")
        return value

    @field_validator("answer_chunk", mode="before")
    @classmethod
    def _chunk(cls, value: object) -> str | None:
        return (
            None
            if value is None
            else safe_text(value, field="answer_chunk", maximum=512)
        )

    @field_validator("answer_fingerprint", mode="before")
    @classmethod
    def _answer(cls, value: object) -> str | None:
        return None if value is None else fingerprint(value, field="answer_fingerprint")

    @field_validator("timestamp", mode="before")
    @classmethod
    def _timestamp(cls, value: object) -> datetime:
        return utc_datetime(value, field="timestamp")

    @field_validator("request_id", "trace_id", "reason_code", mode="before")
    @classmethod
    def _optional_ids(cls, value: object, info) -> str | None:
        return None if value is None else identifier(value, field=info.field_name)

    @field_validator("analysis_state", mode="before")
    @classmethod
    def _analysis_state(cls, value: object) -> AnalysisState | None:
        return None if value is None else AnalysisState(value)

    @field_validator("terminal_event", mode="before")
    @classmethod
    def _terminal_event(cls, value: object) -> str | None:
        return None if value is None else identifier(value, field="terminal_event")

    @model_validator(mode="after")
    def _event_binding(self):
        if self.event_type is ChatEventType.ANSWER_CHUNK:
            if self.answer_chunk is None or self.answer_fingerprint is not None:
                raise ValueError("chat chunk event is invalid")
        elif self.event_type is ChatEventType.ANSWER_COMPLETED:
            if self.answer_chunk is not None or self.answer_fingerprint is None:
                raise ValueError("chat completion event is invalid")
        elif self.answer_chunk is not None or self.answer_fingerprint is not None:
            raise ValueError("chat lifecycle event is invalid")
        if self.terminal_event is not None and self.terminal_event != self.event_type.value:
            raise ValueError("chat terminal event binding mismatch")
        if self.event_type in {ChatEventType.ANSWER_COMPLETED, ChatEventType.ANSWER_FAILED}:
            if self.terminal_event is not None and self.reason_code is None:
                raise ValueError("chat terminal reason is missing")
        return self


class ChatTraceEvent(FingerprintedChatContract):
    event_id: str
    trace_id: str
    request_id: str
    project_id: str
    conversation_id: str
    stage: AnalysisTraceStage
    route: str | None = None
    agent_identity: str | None = None
    service_identity: str | None = None
    source_status: str | None = None
    evidence_count: int | None = None
    gate_result: str | None = None
    provider_configured: bool | None = None
    gateway_invocation_count: int = 0
    reason_code: str | None = None
    terminal_event: str | None = None
    original_exception_type: str | None = None
    original_exception_hash: str | None = None
    exception_layer: str | None = None
    exception_stage: str | None = None
    traceback_fingerprint: str | None = None
    timestamp: datetime

    @field_validator("event_id", "trace_id", "request_id", "project_id", "conversation_id", mode="before")
    @classmethod
    def _trace_ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator(
        "route",
        "agent_identity",
        "service_identity",
        "source_status",
        "gate_result",
        "reason_code",
        "terminal_event",
        "original_exception_type",
        "original_exception_hash",
        "exception_layer",
        "exception_stage",
        "traceback_fingerprint",
        mode="before",
    )
    @classmethod
    def _trace_text(cls, value: object, info) -> str | None:
        if value is None:
            return None
        if info.field_name in {
            "original_exception_type",
            "original_exception_hash",
            "exception_layer",
            "exception_stage",
            "traceback_fingerprint",
        } and value == "-":
            return value
        return identifier(value, field=info.field_name)

    @field_validator("evidence_count", "gateway_invocation_count", mode="before")
    @classmethod
    def _counts(cls, value: object, info) -> int | None:
        if value is None and info.field_name == "evidence_count":
            return None
        if type(value) is not int or value < 0:
            raise ValueError(f"{info.field_name} is invalid")
        return value

    @field_validator("provider_configured", mode="before")
    @classmethod
    def _provider(cls, value: object) -> bool | None:
        if value is None:
            return None
        if type(value) is not bool:
            raise TypeError("provider_configured is invalid")
        return value

    @field_validator("timestamp", mode="before")
    @classmethod
    def _trace_timestamp(cls, value: object) -> datetime:
        return utc_datetime(value, field="timestamp")


class ChatRuntimeHealth(FingerprintedChatContract):
    component: str
    status: str
    observed_at: datetime

    @field_validator("component", "status", mode="before")
    @classmethod
    def _text(cls, value: object, info) -> str:
        return safe_text(value, field=info.field_name, maximum=128)

    @field_validator("observed_at", mode="before")
    @classmethod
    def _time(cls, value: object) -> datetime:
        return utc_datetime(value, field="observed_at")


class ChatRuntimePanelView(FingerprintedChatContract):
    hil_status: ChatObservationStatus
    eda_status: ChatObservationStatus
    memory_statuses: tuple[ChatMemoryStatus, ...]
    runtime_health: tuple[ChatRuntimeHealth, ...]
    generated_at: datetime

    @field_validator("memory_statuses", mode="before")
    @classmethod
    def _memory(cls, value: object) -> tuple[ChatMemoryStatus, ...]:
        values = tuple(tuple_only(value, field="memory_statuses"))
        if len(values) != len(set(values)):
            raise ValueError("memory statuses must be unique")
        return tuple(sorted(values, key=lambda item: item.value))

    @field_validator("runtime_health", mode="before")
    @classmethod
    def _health(cls, value: object) -> tuple[ChatRuntimeHealth, ...]:
        return tuple(
            _exact(item, ChatRuntimeHealth, field="chat runtime health")
            for item in tuple_only(value, field="runtime_health")
        )

    @field_validator("generated_at", mode="before")
    @classmethod
    def _generated(cls, value: object) -> datetime:
        return utc_datetime(value, field="generated_at")


__all__ = tuple(name for name in globals() if name.startswith(("Chat", "Desktop", "Analysis")))
