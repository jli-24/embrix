from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum

from pydantic import field_validator, model_validator

from .contracts import (
    FingerprintedContract,
    MultimodalContract,
    fingerprint,
    identifier,
    utc_datetime,
)


class MultimodalApplicationRoute(StrEnum):
    VISION = "VISION"


class MultimodalApplicationStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class MultimodalApplicationAuditEventType(StrEnum):
    VISION_ANALYSIS_COMPLETED = "VISION_ANALYSIS_COMPLETED"
    VISION_ANALYSIS_FAILED = "VISION_ANALYSIS_FAILED"


class MultimodalApplicationErrorCode(StrEnum):
    VISION_UNAVAILABLE = "VISION_UNAVAILABLE"
    EVIDENCE_MISSING = "EVIDENCE_MISSING"
    CONTEXT_UNAVAILABLE = "CONTEXT_UNAVAILABLE"
    ANSWER_UNAVAILABLE = "ANSWER_UNAVAILABLE"
    APPLICATION_UNAVAILABLE = "APPLICATION_UNAVAILABLE"


class MultimodalApplicationAuditEvent(FingerprintedContract):
    event_id: str
    event_type: MultimodalApplicationAuditEventType
    request_id: str
    trace_id: str
    project_id: str
    requester: str
    route: MultimodalApplicationRoute
    status: MultimodalApplicationStatus
    question_fingerprint: str
    artifact_fingerprint: str
    observation_fingerprint: str | None
    context_fingerprint: str | None
    answer_fingerprint: str | None
    model_reference_fingerprint: str
    error_code: MultimodalApplicationErrorCode | None
    timestamp: datetime

    @field_validator(
        "event_id", "request_id", "trace_id", "project_id", "requester", mode="before"
    )
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator(
        "question_fingerprint",
        "artifact_fingerprint",
        "model_reference_fingerprint",
        mode="before",
    )
    @classmethod
    def _required_fps(cls, value: object, info) -> str:
        return fingerprint(value, field=info.field_name)

    @field_validator(
        "observation_fingerprint",
        "context_fingerprint",
        "answer_fingerprint",
        mode="before",
    )
    @classmethod
    def _optional_fps(cls, value: object, info) -> str | None:
        return None if value is None else fingerprint(value, field=info.field_name)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _timestamp(cls, value: object) -> datetime:
        return utc_datetime(value, field="timestamp")

    @model_validator(mode="after")
    def _state(self):
        expected_type = (
            MultimodalApplicationAuditEventType.VISION_ANALYSIS_COMPLETED
            if self.status is MultimodalApplicationStatus.COMPLETED
            else MultimodalApplicationAuditEventType.VISION_ANALYSIS_FAILED
        )
        if (
            self.event_type is not expected_type
            or self.route is not MultimodalApplicationRoute.VISION
        ):
            raise ValueError("multimodal audit status mismatch")
        expected_id = self.event_id_for(
            self.question_fingerprint, self.artifact_fingerprint, self.event_type
        )
        if self.event_id != expected_id:
            raise ValueError("multimodal audit event id mismatch")
        if self.status is MultimodalApplicationStatus.COMPLETED:
            if (
                self.observation_fingerprint is None
                or self.context_fingerprint is None
                or self.answer_fingerprint is None
                or self.error_code is not None
            ):
                raise ValueError("completed multimodal audit is incomplete")
        elif self.answer_fingerprint is not None or self.error_code is None:
            raise ValueError("failed multimodal audit is invalid")
        return self

    @staticmethod
    def event_id_for(
        question_fingerprint: str,
        artifact_fingerprint: str,
        event_type: MultimodalApplicationAuditEventType,
    ) -> str:
        material = (
            f"{fingerprint(question_fingerprint)}|"
            f"{fingerprint(artifact_fingerprint)}|{event_type.value}"
        )
        return "vision-event-" + hashlib.sha256(material.encode()).hexdigest()[:48]


class MultimodalApplicationAuditReference(MultimodalContract):
    event_id: str
    event_type: MultimodalApplicationAuditEventType
    fingerprint: str

    @field_validator("event_id", mode="before")
    @classmethod
    def _event_id(cls, value: object) -> str:
        return identifier(value, field="event_id")

    @field_validator("fingerprint", mode="before")
    @classmethod
    def _fp(cls, value: object) -> str:
        return fingerprint(value)

    @classmethod
    def from_event(
        cls, event: MultimodalApplicationAuditEvent
    ) -> MultimodalApplicationAuditReference:
        return cls(
            event_id=event.event_id,
            event_type=event.event_type,
            fingerprint=event.fingerprint,
        )


__all__ = tuple(name for name in globals() if name.startswith("MultimodalApplication"))
