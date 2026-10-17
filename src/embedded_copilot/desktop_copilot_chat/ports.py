from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from embedded_copilot.application import (
    ApplicationQuestionRequest,
    ApplicationRoutingContext,
    ApplicationRouteDecision,
    ApplicationRunResult,
)
from embedded_copilot.multimodal.context import AttachmentBinding

from .models import (
    ChatAuditEvent,
    ChatContextReference,
    ChatEvent,
    ChatTraceEvent,
    ChatProjectReference,
    ChatRuntimePanelView,
    ChatSession,
)


@runtime_checkable
class DesktopApplicationPort(Protocol):
    def answer(self, request: ApplicationQuestionRequest, *, routing_context: ApplicationRoutingContext | None = None, route_decision: ApplicationRouteDecision | None = None) -> ApplicationRunResult: ...
    def classify(self, request: ApplicationQuestionRequest, *, routing_context: ApplicationRoutingContext | None = None) -> ApplicationRouteDecision: ...


@runtime_checkable
class ChatProjectProjectionPort(Protocol):
    def project(self, tenant_id: str, project_id: str) -> ChatProjectReference: ...
    def project_for_requester(
        self, tenant_id: str, project_id: str, requester: str
    ) -> ChatProjectReference: ...
    def references(
        self, tenant_id: str, project_id: str
    ) -> tuple[ChatContextReference, ...]: ...


@runtime_checkable
class ChatEnvironmentProjectionPort(Protocol):
    def panel(self, tenant_id: str, project_id: str) -> ChatRuntimePanelView: ...


@runtime_checkable
class ChatSessionStorePort(Protocol):
    def save(self, session: ChatSession) -> ChatSession: ...
    def get(self, session_id: str) -> ChatSession | None: ...


@runtime_checkable
class ChatAuditSinkPort(Protocol):
    def append(self, event: ChatAuditEvent) -> ChatAuditEvent: ...
    def get(self, event_id: str) -> ChatAuditEvent | None: ...


@runtime_checkable
class ChatEventStorePort(Protocol):
    def append(self, event: ChatEvent) -> ChatEvent: ...
    def read_after(
        self, session_id: str, last_event_id: str | None
    ) -> tuple[ChatEvent, ...] | None: ...


@runtime_checkable
class ChatTraceStorePort(Protocol):
    def append(self, event: ChatTraceEvent) -> ChatTraceEvent: ...

    def read(self, trace_id: str) -> tuple[ChatTraceEvent, ...]: ...


@runtime_checkable
class ChatClockPort(Protocol):
    def now(self) -> datetime: ...


@runtime_checkable
class ChatAttachmentBindingPort(Protocol):
    def bind(self, binding: AttachmentBinding) -> None: ...
    def list(self, session_id: str) -> tuple[AttachmentBinding, ...]: ...


@runtime_checkable
class ChatTransmissionApprovalPort(Protocol):
    def consume(
        self,
        *,
        tenant_id: str,
        requester: str,
        session_id: str,
        project_id: str,
        request_id: str,
        reference_fingerprints: tuple[str, ...],
    ) -> object: ...


@runtime_checkable
class ChatConversationPort(Protocol):
    def routing_context(self, session_id: str) -> object | None: ...
    def begin_turn(self, session_id: str, *, request_id: str, trace_id: str | None = None, question: str, created_at: datetime, context_references: tuple[dict[str, object], ...] = ()) -> None: ...
    def complete_turn(self, session_id: str, request_id: str, answer: object, completed_at: datetime, *, route: str | None = None, trace_id: str | None = None, terminal_event: str | None = None, analysis_state: str | None = None, analysis_reason_code: str | None = None, requirement_context: object | None = None, engineering_plan: object | None = None, engineering_plan_review: object | None = None, architecture_design: object | None = None, architecture_review: object | None = None, design_truth: object | None = None) -> None: ...
    def fail_turn(
        self,
        session_id: str,
        request_id: str,
        error_code: str | None,
        completed_at: datetime,
        *,
        context_references: tuple[dict[str, object], ...] = (),
        analysis_state: str = "FAILED",
        analysis_reason_code: str | None = None,
        route: str | None = None,
        trace_id: str | None = None,
        terminal_event: str | None = None,
        answer_text: str | None = None,
        requirement_context: object | None = None,
        engineering_plan: object | None = None,
        engineering_plan_review: object | None = None,
        architecture_design: object | None = None,
        architecture_review: object | None = None,
        design_truth: object | None = None,
    ) -> None: ...


__all__ = tuple(name for name in globals() if name.endswith("Port"))
