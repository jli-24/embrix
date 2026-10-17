from __future__ import annotations

import copy
from datetime import datetime
from pathlib import PurePath

from pydantic import Field, field_validator

from embedded_copilot.application import ApplicationQuestionRequest
from embedded_copilot.desktop_copilot_chat import (
    ChatContextReference,
    ChatContract,
    ChatProjectReference,
    ChatReferenceKind,
    ChatSessionOpenRequest,
    ChatTraceEvent,
    DesktopChatRequest,
    FingerprintedChatContract,
    identifier,
    safe_text,
    utc_datetime,
)
from embedded_copilot.input.loader import DEFAULT_MAX_ATTACHMENT_SIZE_BYTES
from embedded_copilot.multimodal.context import AttachmentBinding
from embedded_copilot.multimodal.models import MultimodalInput, MultimodalInputType

_ATTACHMENT_SUMMARY = "User-selected read-only attachment reference"


def _json_datetime(value: object, *, field: str) -> datetime:
    if type(value) is str:
        try:
            value = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"{field} is invalid") from error
    return utc_datetime(value, field=field)


def _json_tuple(value: object, *, field: str) -> tuple[object, ...]:
    if type(value) not in {list, tuple}:
        raise TypeError(f"{field} is invalid")
    return tuple(value)


def _application_question(value: object) -> ApplicationQuestionRequest:
    if type(value) is ApplicationQuestionRequest:
        value = value.model_dump(mode="python")
    if type(value) is not dict:
        raise TypeError("application question is invalid")
    material = copy.deepcopy(value)
    material["constraints"] = _json_tuple(
        material.get("constraints", ()), field="application constraints"
    )
    material["requested_at"] = _json_datetime(
        material.get("requested_at"), field="application requested_at"
    )
    return ApplicationQuestionRequest.model_validate(material)


def _context_reference(value: object) -> ChatContextReference:
    if type(value) is ChatContextReference:
        value = value.model_dump(mode="python")
    if type(value) is not dict:
        raise TypeError("chat context reference is invalid")
    material = copy.deepcopy(value)
    kind = material.get("reference_kind")
    if type(kind) is str:
        material["reference_kind"] = ChatReferenceKind(kind)
    return ChatContextReference.model_validate(material)


class CopilotChatApiModel(ChatContract):
    pass


class CopilotChatTraceView(FingerprintedChatContract):
    """Safe read-only projection of the persisted chat execution trace."""

    event_id: str
    trace_id: str
    request_id: str
    project_id: str
    conversation_id: str
    stage: str
    route: str | None = None
    agent_identity: str | None = None
    service_identity: str | None = None
    source_status: str | None = None
    evidence_count: int | None = None
    gate_result: str | None = None
    provider_configured: bool | None = None
    gateway_invocation_count: int
    reason_code: str | None = None
    terminal_event: str | None = None
    timestamp: datetime

    @classmethod
    def from_event(cls, event: ChatTraceEvent) -> CopilotChatTraceView:
        return cls(
            event_id=event.event_id,
            trace_id=event.trace_id,
            request_id=event.request_id,
            project_id=event.project_id,
            conversation_id=event.conversation_id,
            stage=event.stage.value,
            route=event.route,
            agent_identity=event.agent_identity,
            service_identity=event.service_identity,
            source_status=event.source_status,
            evidence_count=event.evidence_count,
            gate_result=event.gate_result,
            provider_configured=event.provider_configured,
            gateway_invocation_count=event.gateway_invocation_count,
            reason_code=event.reason_code,
            terminal_event=event.terminal_event,
            timestamp=event.timestamp,
        )


class CopilotChatSessionOpenInput(CopilotChatApiModel):
    session_id: str
    project_id: str
    expires_at: datetime

    @field_validator("session_id", "project_id", mode="before")
    @classmethod
    def _ids(cls, value: object, info) -> str:
        return identifier(value, field=info.field_name)

    @field_validator("expires_at", mode="before")
    @classmethod
    def _expires(cls, value: object) -> datetime:
        return _json_datetime(value, field="expires_at")

    def to_request(self, *, requester: str, tenant_id: str) -> ChatSessionOpenRequest:
        return ChatSessionOpenRequest(
            session_id=self.session_id,
            tenant_id=tenant_id,
            project_id=self.project_id,
            requester=requester,
            expires_at=self.expires_at,
        )


class CopilotChatInput(CopilotChatApiModel):
    project_reference: ChatProjectReference
    application_question: ApplicationQuestionRequest
    external_search: bool = False
    context_references: tuple[ChatContextReference, ...]
    requested_at: datetime

    @field_validator("project_reference", mode="before")
    @classmethod
    def _project(cls, value: object) -> ChatProjectReference:
        return ChatProjectReference.model_validate(value)

    @field_validator("application_question", mode="before")
    @classmethod
    def _question(cls, value: object) -> ApplicationQuestionRequest:
        return _application_question(value)

    @field_validator("external_search", mode="before")
    @classmethod
    def _external_search(cls, value: object) -> bool:
        if type(value) is not bool:
            raise ValueError("external_search is invalid")
        return value

    @field_validator("context_references", mode="before")
    @classmethod
    def _references(cls, value: object) -> tuple[ChatContextReference, ...]:
        return tuple(
            _context_reference(item)
            for item in _json_tuple(value, field="context_references")
        )

    @field_validator("requested_at", mode="before")
    @classmethod
    def _requested(cls, value: object) -> datetime:
        return _json_datetime(value, field="requested_at")

    def to_request(self, *, session_id: str) -> DesktopChatRequest:
        application_question = ApplicationQuestionRequest.model_validate(
            {
                **self.application_question.model_dump(mode="python"),
                "external_search": self.external_search,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        return DesktopChatRequest(
            session_id=session_id,
            project_reference=self.project_reference,
            application_question=application_question,
            external_search=self.external_search,
            context_references=self.context_references,
            requested_at=self.requested_at,
        )


class CopilotChatAttachmentInput(CopilotChatApiModel):
    reference_id: str
    type: MultimodalInputType
    basename: str
    summary: str
    size_bytes: int = Field(ge=0, le=DEFAULT_MAX_ATTACHMENT_SIZE_BYTES)
    created_at: datetime

    @field_validator("type", mode="before")
    @classmethod
    def _type_value(cls, value: object) -> MultimodalInputType:
        return value if isinstance(value, MultimodalInputType) else MultimodalInputType(value)

    @field_validator("reference_id", mode="before")
    @classmethod
    def _reference_id(cls, value: object) -> str:
        return identifier(value, field="attachment reference_id")

    @field_validator("type")
    @classmethod
    def _type(cls, value: MultimodalInputType) -> MultimodalInputType:
        if value is MultimodalInputType.TEXT:
            raise ValueError("attachment type must be IMAGE or FILE")
        return value

    @field_validator("basename", mode="before")
    @classmethod
    def _basename(cls, value: object) -> str:
        basename = safe_text(value, field="attachment basename", maximum=255)
        if (
            PurePath(basename).name != basename
            or "/" in basename
            or "\\" in basename
            or basename in {".", ".."}
        ):
            raise ValueError("attachment basename must not contain a path")
        return basename

    @field_validator("summary", mode="before")
    @classmethod
    def _summary(cls, value: object) -> str:
        summary = safe_text(value, field="attachment summary", maximum=512)
        if summary != _ATTACHMENT_SUMMARY:
            raise ValueError("attachment summary is invalid")
        return summary

    @field_validator("created_at", mode="before")
    @classmethod
    def _created_at(cls, value: object) -> datetime:
        return _json_datetime(value, field="attachment created_at")

    def to_binding(self, *, session_id: str) -> AttachmentBinding:
        return AttachmentBinding(
            session_id=session_id,
            input=MultimodalInput(
                type=self.type,
                reference_id=self.reference_id,
                summary=self.summary,
            ),
            basename=self.basename,
            size_bytes=self.size_bytes,
            created_at=self.created_at,
        )
