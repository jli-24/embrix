from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.engineering_qa import EngineeringQuestion
from embedded_copilot.model_gateway import (
    ModelInvocationGenerationOptions,
    ModelInvocationPort,
    ModelInvocationRequest,
    ModelPolicy,
    ModelRequest,
)

from .models import (
    ApplicationAnswerEvidence,
    ApplicationAuditEvent,
    ApplicationQuestionRequest,
    ApplicationRoutingContext,
    ApplicationRouteDecision,
)


@runtime_checkable
class ApplicationAgentRuntimePort(Protocol):
    def route(
        self,
        request: ApplicationQuestionRequest,
        routing_context: ApplicationRoutingContext | None = None,
    ) -> ApplicationRouteDecision: ...


@runtime_checkable
class ApplicationFirmwareAgentPort(Protocol):
    """Internal specialist boundary; it never returns a user-facing answer."""

    def analyze(self, request: ApplicationQuestionRequest, context: object) -> object: ...

    async def aanalyze(
        self, request: ApplicationQuestionRequest, context: object
    ) -> object: ...


@runtime_checkable
class ApplicationFirmwarePromptPort(Protocol):
    def request_for(
        self,
        question: EngineeringQuestion,
        snapshot: EngineeringContextSnapshot,
        analysis: dict[str, object],
        policy: ModelPolicy | None,
        *, generation_options: ModelInvocationGenerationOptions | None = None,
    ) -> ModelRequest | ModelInvocationRequest: ...


@runtime_checkable
class ApplicationDebugAgentPort(Protocol):
    def analyze(self, request: ApplicationQuestionRequest, context: object) -> object: ...

    async def aanalyze(self, request: ApplicationQuestionRequest, context: object) -> object: ...


@runtime_checkable
class ApplicationDebugPromptPort(Protocol):
    def request_for(
        self,
        question: EngineeringQuestion,
        snapshot: EngineeringContextSnapshot,
        analysis: dict[str, object],
        policy: ModelPolicy | None,
        *, generation_options: ModelInvocationGenerationOptions | None = None,
    ) -> ModelRequest | ModelInvocationRequest: ...


@runtime_checkable
class ApplicationAnswerPort(Protocol):
    def answer(self, request: ApplicationQuestionRequest) -> ApplicationAnswerEvidence: ...

    async def aanswer(
        self, request: ApplicationQuestionRequest
    ) -> ApplicationAnswerEvidence: ...


@runtime_checkable
class ApplicationAuditSinkPort(Protocol):
    def append(self, event: ApplicationAuditEvent) -> ApplicationAuditEvent: ...

    def get(self, event_id: str) -> ApplicationAuditEvent | None: ...


@runtime_checkable
class ApplicationClockPort(Protocol):
    def now(self) -> datetime: ...


__all__ = (
    "ApplicationAgentRuntimePort",
    "ApplicationFirmwareAgentPort",
    "ApplicationFirmwarePromptPort",
    "ApplicationDebugAgentPort",
    "ApplicationDebugPromptPort",
    "ApplicationAnswerPort",
    "ApplicationAuditSinkPort",
    "ApplicationClockPort",
    "ModelInvocationPort",
)
