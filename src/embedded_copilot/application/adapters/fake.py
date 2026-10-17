from __future__ import annotations

import asyncio
import copy
from datetime import datetime

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.model_gateway import ModelOpenQuestion, ModelRequest, ModelRequirementProposal, ModelResponse

from ..exceptions import ApplicationAuditConflict, ApplicationAuditUnavailable
from ..models import (
    ApplicationAuditEvent,
    ApplicationQuestionRequest,
    ApplicationRouteDecision,
)


class FakeApplicationSupervisor:
    __slots__ = ("_decision", "call_count")

    def __init__(self, decision: ApplicationRouteDecision) -> None:
        self._decision = ApplicationRouteDecision.model_validate(
            copy.deepcopy(decision.model_dump(mode="python"))
        )
        self.call_count = 0

    def route(self, request: ApplicationQuestionRequest) -> ApplicationRouteDecision:
        self.call_count += 1
        checked = ApplicationQuestionRequest.model_validate(
            copy.deepcopy(request.model_dump(mode="python"))
        )
        if (
            self._decision.request_id != checked.request_id
            or self._decision.request_fingerprint != checked.fingerprint
        ):
            raise ValueError("fake supervisor request mismatch")
        return ApplicationRouteDecision.model_validate(
            copy.deepcopy(self._decision.model_dump(mode="python"))
        )


class FakeApplicationContext:
    __slots__ = ("_value", "call_count")

    def __init__(self, value: EngineeringContextSnapshot | None) -> None:
        self._value = (
            None
            if value is None
            else EngineeringContextSnapshot.model_validate(
                copy.deepcopy(value.model_dump(mode="python"))
            )
        )
        self.call_count = 0

    def get_context(self, request):
        self.call_count += 1
        return (
            None
            if self._value is None
            else EngineeringContextSnapshot.model_validate(
                copy.deepcopy(self._value.model_dump(mode="python"))
            )
        )


class FakeApplicationModelGateway:
    __slots__ = ("_cancel_async", "_content", "_failure", "_proposals", "_questions", "call_count")

    def __init__(
        self,
        *,
        content: str = "No. GPIO12 is reserved by verified project evidence.",
        failure: Exception | None = None,
        cancel_async: bool = False,
        proposals: tuple[ModelRequirementProposal, ...] = (),
        questions: tuple[ModelOpenQuestion, ...] = (),
    ) -> None:
        self._content = content
        self._failure = failure
        self._cancel_async = cancel_async
        self._proposals = tuple(proposals)
        self._questions = tuple(questions)
        self.call_count = 0

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.call_count += 1
        if self._failure is not None:
            raise self._failure
        return ModelResponse(
            content=self._content,
            model_name=request.model_policy.model_name,
            provider=request.model_policy.primary_provider,
            confidence=request.engineering_context.confidence,
            provenance=request.engineering_context.provenance,
            requirement_proposals=self._proposals,
            open_questions=self._questions,
        )

    async def agenerate(self, request: ModelRequest) -> ModelResponse:
        if self._cancel_async:
            raise asyncio.CancelledError
        return self.generate(request)


class FakeApplicationAuditSink:
    __slots__ = ("_events", "_fail_writes", "_order")

    def __init__(self, *, fail_writes: bool = False) -> None:
        self._events: dict[str, ApplicationAuditEvent] = {}
        self._order: list[str] = []
        self._fail_writes = fail_writes

    def append(self, event: ApplicationAuditEvent) -> ApplicationAuditEvent:
        if self._fail_writes:
            raise ApplicationAuditUnavailable()
        checked = ApplicationAuditEvent.model_validate(
            copy.deepcopy(event.model_dump(mode="python"))
        )
        existing = self._events.get(checked.event_id)
        if existing is not None:
            if existing != checked:
                raise ApplicationAuditConflict()
            return ApplicationAuditEvent.model_validate(
                copy.deepcopy(existing.model_dump(mode="python"))
            )
        self._events[checked.event_id] = checked
        self._order.append(checked.event_id)
        return ApplicationAuditEvent.model_validate(
            copy.deepcopy(checked.model_dump(mode="python"))
        )

    def get(self, event_id: str) -> ApplicationAuditEvent | None:
        value = self._events.get(event_id)
        return (
            None
            if value is None
            else ApplicationAuditEvent.model_validate(
                copy.deepcopy(value.model_dump(mode="python"))
            )
        )

    @property
    def events(self) -> tuple[ApplicationAuditEvent, ...]:
        return tuple(
            ApplicationAuditEvent.model_validate(
                copy.deepcopy(self._events[event_id].model_dump(mode="python"))
            )
            for event_id in self._order
        )


class FixedApplicationClock:
    __slots__ = ("_value",)

    def __init__(self, value: datetime) -> None:
        self._value = copy.deepcopy(value)

    def now(self) -> datetime:
        return copy.deepcopy(self._value)


__all__ = (
    "FakeApplicationAuditSink",
    "FakeApplicationContext",
    "FakeApplicationModelGateway",
    "FakeApplicationSupervisor",
    "FixedApplicationClock",
)
