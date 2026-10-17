from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass
from datetime import datetime

from pydantic import field_validator, model_validator

from embedded_copilot.application import (
    ApplicationQuestionRequest,
    EngineeringQABinding,
)
from embedded_copilot.engineering_context import (
    EngineeringContextProviderPort,
    EngineeringContextRequest,
    EngineeringContextSnapshot,
)
from embedded_copilot.engineering_qa import EngineeringAnswer
from embedded_copilot.model_gateway import ModelGatewayPort, ModelPolicy

from .audit import (
    MultimodalApplicationAuditEvent,
    MultimodalApplicationAuditEventType,
    MultimodalApplicationAuditReference,
    MultimodalApplicationErrorCode,
    MultimodalApplicationRoute,
    MultimodalApplicationStatus,
)
from .contracts import (
    FingerprintedContract,
    fingerprint,
    utc_datetime,
)
from .evidence import MultimodalContextProjector
from .exceptions import (
    MultimodalAuditConflict,
    MultimodalAuditUnavailable,
    MultimodalContextUnavailable,
    MultimodalEvidenceMissing,
    MultimodalRejected,
)
from .models import EngineeringArtifact, EngineeringObservation
from .ports import (
    MultimodalApplicationAuditSinkPort,
    MultimodalClockPort,
)
from .service import MultimodalEngineeringService


class MultimodalApplicationRequest(FingerprintedContract):
    question: ApplicationQuestionRequest
    artifact: EngineeringArtifact

    @field_validator("question", mode="before")
    @classmethod
    def _question(cls, value: object) -> ApplicationQuestionRequest:
        return ApplicationQuestionRequest.model_validate(copy.deepcopy(value))

    @field_validator("artifact", mode="before")
    @classmethod
    def _artifact(cls, value: object) -> EngineeringArtifact:
        return EngineeringArtifact.model_validate(copy.deepcopy(value))

    @model_validator(mode="after")
    def _project(self):
        if self.question.project_id != self.artifact.project_id:
            raise ValueError("multimodal application project mismatch")
        return self


class MultimodalApplicationResult(FingerprintedContract):
    request_fingerprint: str
    artifact_fingerprint: str
    route: MultimodalApplicationRoute
    status: MultimodalApplicationStatus
    observation: EngineeringObservation | None
    context_fingerprint: str | None
    answer: EngineeringAnswer | None
    error_code: MultimodalApplicationErrorCode | None
    audit: MultimodalApplicationAuditReference
    completed_at: datetime

    @field_validator("request_fingerprint", "artifact_fingerprint", mode="before")
    @classmethod
    def _fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator("completed_at", mode="before")
    @classmethod
    def _completed_at(cls, value: object):
        return utc_datetime(value, field="completed_at")

    @model_validator(mode="after")
    def _state(self):
        if self.route is not MultimodalApplicationRoute.VISION:
            raise ValueError("multimodal application route mismatch")
        expected_event = (
            MultimodalApplicationAuditEventType.VISION_ANALYSIS_COMPLETED
            if self.status is MultimodalApplicationStatus.COMPLETED
            else MultimodalApplicationAuditEventType.VISION_ANALYSIS_FAILED
        )
        if self.audit.event_type is not expected_event:
            raise ValueError("multimodal result audit mismatch")
        if self.status is MultimodalApplicationStatus.COMPLETED:
            if (
                self.observation is None
                or self.context_fingerprint is None
                or self.answer is None
                or self.error_code is not None
            ):
                raise ValueError("completed multimodal result is incomplete")
        elif self.answer is not None or self.error_code is None:
            raise ValueError("failed multimodal result is invalid")
        return self


@dataclass(frozen=True, slots=True)
class MultimodalApplicationDependencies:
    multimodal_service: MultimodalEngineeringService
    context_provider: EngineeringContextProviderPort
    model_gateway: ModelGatewayPort
    model_policy: ModelPolicy
    audit_sink: MultimodalApplicationAuditSinkPort
    clock: MultimodalClockPort

    def __post_init__(self) -> None:
        if (
            type(self.multimodal_service) is not MultimodalEngineeringService
            or not isinstance(self.context_provider, EngineeringContextProviderPort)
            or not isinstance(self.model_gateway, ModelGatewayPort)
            or not isinstance(self.audit_sink, MultimodalApplicationAuditSinkPort)
            or not isinstance(self.clock, MultimodalClockPort)
        ):
            raise TypeError("multimodal application dependency is invalid")
        object.__setattr__(
            self,
            "model_policy",
            ModelPolicy.model_validate(
                copy.deepcopy(self.model_policy.model_dump(mode="python"))
            ),
        )


class _BoundContextProvider:
    __slots__ = ("_snapshot",)

    def __init__(self, snapshot: EngineeringContextSnapshot) -> None:
        self._snapshot = EngineeringContextSnapshot.model_validate(
            copy.deepcopy(snapshot.model_dump(mode="python"))
        )

    def get_context(self, request: EngineeringContextRequest):
        if (
            request.project_id != self._snapshot.project_id
            or request.task != self._snapshot.task_description
        ):
            return None
        return EngineeringContextSnapshot.model_validate(
            copy.deepcopy(self._snapshot.model_dump(mode="python"))
        )


class MultimodalApplicationRuntime:
    __slots__ = ("_dependencies", "_projector")

    def __init__(self, dependencies: MultimodalApplicationDependencies) -> None:
        if type(dependencies) is not MultimodalApplicationDependencies:
            raise TypeError("multimodal application dependencies are invalid")
        self._dependencies = dependencies
        self._projector = MultimodalContextProjector()

    @staticmethod
    def _request(value: object) -> MultimodalApplicationRequest:
        if type(value) is not MultimodalApplicationRequest:
            raise TypeError("multimodal application request is invalid")
        return MultimodalApplicationRequest.model_validate(
            copy.deepcopy(value.model_dump(mode="python"))
        )

    def _base_context(
        self, request: MultimodalApplicationRequest
    ) -> EngineeringContextSnapshot | None:
        try:
            context_request = EngineeringContextRequest(
                project_id=request.question.project_id,
                task=request.question.question,
                constraints=request.question.constraints,
            )
            raw = self._dependencies.context_provider.get_context(context_request)
            if raw is None:
                return None
            checked = EngineeringContextSnapshot.model_validate(
                copy.deepcopy(raw.model_dump(mode="python"))
            )
            if (
                checked.project_id != request.question.project_id
                or checked.task_description != request.question.question
            ):
                raise ValueError("base context binding mismatch")
            return checked
        except Exception as error:
            raise MultimodalContextUnavailable() from error

    def _answer(
        self,
        request: MultimodalApplicationRequest,
        context: EngineeringContextSnapshot,
    ) -> EngineeringAnswer:
        evidence = EngineeringQABinding(
            context_provider=_BoundContextProvider(context),
            model_gateway=self._dependencies.model_gateway,
            model_policy=self._dependencies.model_policy,
        ).answer(request.question)
        return EngineeringAnswer.model_validate(
            copy.deepcopy(evidence.answer.model_dump(mode="python"))
        )

    async def _aanswer(
        self,
        request: MultimodalApplicationRequest,
        context: EngineeringContextSnapshot,
    ) -> EngineeringAnswer:
        evidence = await EngineeringQABinding(
            context_provider=_BoundContextProvider(context),
            model_gateway=self._dependencies.model_gateway,
            model_policy=self._dependencies.model_policy,
        ).aanswer(request.question)
        return EngineeringAnswer.model_validate(
            copy.deepcopy(evidence.answer.model_dump(mode="python"))
        )

    @staticmethod
    def _failure_code(error: Exception) -> MultimodalApplicationErrorCode:
        if isinstance(error, MultimodalEvidenceMissing):
            return MultimodalApplicationErrorCode.EVIDENCE_MISSING
        if isinstance(error, MultimodalRejected):
            return MultimodalApplicationErrorCode.VISION_UNAVAILABLE
        if isinstance(error, MultimodalContextUnavailable):
            return MultimodalApplicationErrorCode.CONTEXT_UNAVAILABLE
        module = type(error).__module__
        if (
            "application" in module
            or "engineering_qa" in module
            or "model_gateway" in module
        ):
            return MultimodalApplicationErrorCode.ANSWER_UNAVAILABLE
        return MultimodalApplicationErrorCode.VISION_UNAVAILABLE

    def _audit(
        self,
        *,
        request: MultimodalApplicationRequest,
        status: MultimodalApplicationStatus,
        observation: EngineeringObservation | None,
        context: EngineeringContextSnapshot | None,
        answer: EngineeringAnswer | None,
        error_code: MultimodalApplicationErrorCode | None,
    ) -> MultimodalApplicationAuditEvent:
        event_type = (
            MultimodalApplicationAuditEventType.VISION_ANALYSIS_COMPLETED
            if status is MultimodalApplicationStatus.COMPLETED
            else MultimodalApplicationAuditEventType.VISION_ANALYSIS_FAILED
        )
        event_id = MultimodalApplicationAuditEvent.event_id_for(
            request.question.fingerprint, request.artifact.fingerprint, event_type
        )
        try:
            existing_raw = self._dependencies.audit_sink.get(event_id)
            if existing_raw is not None:
                existing = MultimodalApplicationAuditEvent.model_validate(
                    copy.deepcopy(existing_raw.model_dump(mode="python"))
                )
                expected = (
                    event_type,
                    request.question.request_id,
                    request.question.trace_id,
                    request.question.project_id,
                    request.question.requester,
                    status,
                    request.question.fingerprint,
                    request.artifact.fingerprint,
                    observation.fingerprint if observation else None,
                    context.fingerprint if context else None,
                    answer.fingerprint if answer else None,
                    self._dependencies.multimodal_service.model_reference.fingerprint,
                    error_code,
                )
                actual = (
                    existing.event_type,
                    existing.request_id,
                    existing.trace_id,
                    existing.project_id,
                    existing.requester,
                    existing.status,
                    existing.question_fingerprint,
                    existing.artifact_fingerprint,
                    existing.observation_fingerprint,
                    existing.context_fingerprint,
                    existing.answer_fingerprint,
                    existing.model_reference_fingerprint,
                    existing.error_code,
                )
                if actual != expected:
                    raise MultimodalAuditConflict()
                return existing
            event = MultimodalApplicationAuditEvent(
                event_id=event_id,
                event_type=event_type,
                request_id=request.question.request_id,
                trace_id=request.question.trace_id,
                project_id=request.question.project_id,
                requester=request.question.requester,
                route=MultimodalApplicationRoute.VISION,
                status=status,
                question_fingerprint=request.question.fingerprint,
                artifact_fingerprint=request.artifact.fingerprint,
                observation_fingerprint=(
                    observation.fingerprint if observation else None
                ),
                context_fingerprint=context.fingerprint if context else None,
                answer_fingerprint=answer.fingerprint if answer else None,
                model_reference_fingerprint=(
                    self._dependencies.multimodal_service.model_reference.fingerprint
                ),
                error_code=error_code,
                timestamp=self._dependencies.clock.now(),
            )
            saved_raw = self._dependencies.audit_sink.append(copy.deepcopy(event))
            saved = MultimodalApplicationAuditEvent.model_validate(
                copy.deepcopy(saved_raw.model_dump(mode="python"))
            )
            if saved != event:
                raise MultimodalAuditUnavailable()
            return saved
        except MultimodalAuditConflict:
            raise
        except Exception as error:
            raise MultimodalAuditUnavailable() from error

    def _result(
        self,
        *,
        request: MultimodalApplicationRequest,
        status: MultimodalApplicationStatus,
        observation: EngineeringObservation | None,
        context: EngineeringContextSnapshot | None,
        answer: EngineeringAnswer | None,
        error_code: MultimodalApplicationErrorCode | None,
    ) -> MultimodalApplicationResult:
        event = self._audit(
            request=request,
            status=status,
            observation=observation,
            context=context,
            answer=answer,
            error_code=error_code,
        )
        return MultimodalApplicationResult(
            request_fingerprint=request.fingerprint,
            artifact_fingerprint=request.artifact.fingerprint,
            route=MultimodalApplicationRoute.VISION,
            status=status,
            observation=observation,
            context_fingerprint=context.fingerprint if context else None,
            answer=answer,
            error_code=error_code,
            audit=MultimodalApplicationAuditReference.from_event(event),
            completed_at=event.timestamp,
        )

    def answer(
        self, request: MultimodalApplicationRequest
    ) -> MultimodalApplicationResult:
        checked = self._request(request)
        observation = context = answer = None
        try:
            observation = self._dependencies.multimodal_service.analyze(
                checked.artifact
            )
            base = self._base_context(checked)
            context = self._projector.fuse(
                question=checked.question,
                observation=observation,
                base_snapshot=base,
            )
            answer = self._answer(checked, context)
        except Exception as error:  # noqa: BLE001 - application boundary mapping
            return self._result(
                request=checked,
                status=MultimodalApplicationStatus.FAILED,
                observation=observation,
                context=context,
                answer=None,
                error_code=self._failure_code(error),
            )
        return self._result(
            request=checked,
            status=MultimodalApplicationStatus.COMPLETED,
            observation=observation,
            context=context,
            answer=answer,
            error_code=None,
        )

    async def aanswer(
        self, request: MultimodalApplicationRequest
    ) -> MultimodalApplicationResult:
        checked = self._request(request)
        observation = context = answer = None
        try:
            observation = await self._dependencies.multimodal_service.aanalyze(
                checked.artifact
            )
            base = self._base_context(checked)
            context = self._projector.fuse(
                question=checked.question,
                observation=observation,
                base_snapshot=base,
            )
            answer = await self._aanswer(checked, context)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - application boundary mapping
            return self._result(
                request=checked,
                status=MultimodalApplicationStatus.FAILED,
                observation=observation,
                context=context,
                answer=None,
                error_code=self._failure_code(error),
            )
        return self._result(
            request=checked,
            status=MultimodalApplicationStatus.COMPLETED,
            observation=observation,
            context=context,
            answer=answer,
            error_code=None,
        )


__all__ = (
    "MultimodalApplicationDependencies",
    "MultimodalApplicationRequest",
    "MultimodalApplicationResult",
    "MultimodalApplicationRuntime",
)
