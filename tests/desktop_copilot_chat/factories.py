from __future__ import annotations

from datetime import UTC, datetime, timedelta

from embedded_copilot.application import (
    ApplicationAuditEvent,
    ApplicationAuditEventType,
    ApplicationErrorCode,
    ApplicationQuestionRequest,
    ApplicationRoute,
    ApplicationRunResult,
    ApplicationRunStatus,
)
from embedded_copilot.desktop_copilot_chat import (
    ChatContextReference,
    ChatMemoryStatus,
    ChatObservationStatus,
    ChatProjectReference,
    ChatReferenceKind,
    ChatRuntimeHealth,
    ChatRuntimePanelView,
)
from embedded_copilot.engineering_qa.contracts import EngineeringAnswer, ModelInfo
from embedded_copilot.engineering_requirements import RequirementContext

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
TENANT = "tenant-1"
PROJECT = "project-1"
REQUESTER = "engineer-1"


def project() -> ChatProjectReference:
    return ChatProjectReference(
        tenant_id=TENANT,
        project_id=PROJECT,
        project_name_reference="ESP32-S3 Sensor Gateway",
        workspace_reference="workspace:esp32-s3-gateway",
    )


def reference() -> ChatContextReference:
    return ChatContextReference(
        tenant_id=TENANT,
        project_id=PROJECT,
        reference_id="hil-observation-1",
        reference_kind=ChatReferenceKind.EVIDENCE,
        source_fingerprint="sha256:" + "a" * 64,
        display_label="HIL Observation",
    )


def question(*, request_id: str = "request-1", external_search: bool = False) -> ApplicationQuestionRequest:
    return ApplicationQuestionRequest(
        request_id=request_id,
        trace_id="trace-1",
        project_id=PROJECT,
        requester=REQUESTER,
        question="Why is measured power 120mA?",
        constraints=(),
        external_search=external_search,
        requested_at=NOW,
    )


def application_result(
    request: ApplicationQuestionRequest,
    *,
    content: str = "WiFi does not enter sleep and the sensor polling rate is high.",
    status: ApplicationRunStatus = ApplicationRunStatus.COMPLETED,
    route: ApplicationRoute | None = None,
    error_code: ApplicationErrorCode | None = None,
    gateway_invocation_count: int = 0,
    provider_response_present: bool = False,
    answer_validation_stage: str | None = None,
    answer_rejection_reason: ApplicationErrorCode | None = None,
    proposed_requirements_count: int = 0,
    open_questions_count: int = 0,
    answer_text_present: bool = False,
    requirement_context: RequirementContext | None = None,
) -> ApplicationRunResult:
    if status is not ApplicationRunStatus.COMPLETED:
        selected_route = route or ApplicationRoute.UNKNOWN
        selected_error = error_code or ApplicationErrorCode.APPLICATION_UNAVAILABLE
        audit = ApplicationAuditEvent.create(
            event_type={
                ApplicationRunStatus.REJECTED: ApplicationAuditEventType.QUESTION_REJECTED,
                ApplicationRunStatus.FAILED: ApplicationAuditEventType.QUESTION_FAILED,
            }[status],
            request=request,
            route=selected_route,
            status=status,
            context_fingerprint=None,
            answer_fingerprint=None,
            model_info_fingerprint=None,
            evidence_refs=(),
            error_code=selected_error,
            timestamp=NOW,
        )
        return ApplicationRunResult.create(
            request=request,
            route=selected_route,
            status=status,
            context_fingerprint=None,
            answer=None,
            error_code=selected_error,
            gateway_invocation_count=gateway_invocation_count,
            provider_response_present=provider_response_present,
            answer_validation_stage=answer_validation_stage,
            answer_rejection_reason=answer_rejection_reason,
            proposed_requirements_count=proposed_requirements_count,
            open_questions_count=open_questions_count,
            answer_text_present=answer_text_present,
            requirement_context=requirement_context,
            audit_event=audit,
        )
    answer = EngineeringAnswer(
        question_id=request.request_id,
        content=content,
        confidence=0.8,
        evidence_refs=("evidence-1",),
        model_info=ModelInfo(
            provider="fake",
            model_name="fake-model",
            finish_reason="completed",
        ),
    )
    audit = ApplicationAuditEvent.create(
        event_type=ApplicationAuditEventType.QUESTION_ANSWERED,
        request=request,
        route=ApplicationRoute.KNOWLEDGE,
        status=ApplicationRunStatus.COMPLETED,
        context_fingerprint="sha256:" + "b" * 64,
        answer_fingerprint=answer.fingerprint,
        model_info_fingerprint=answer.model_info.fingerprint,
        evidence_refs=answer.evidence_refs,
        error_code=None,
        timestamp=NOW,
    )
    return ApplicationRunResult.create(
        request=request,
        route=ApplicationRoute.KNOWLEDGE,
        status=ApplicationRunStatus.COMPLETED,
        context_fingerprint="sha256:" + "b" * 64,
        answer=answer,
        error_code=None,
        audit_event=audit,
    )


def panel() -> ChatRuntimePanelView:
    return ChatRuntimePanelView(
        hil_status=ChatObservationStatus.OBSERVED,
        eda_status=ChatObservationStatus.UNAVAILABLE,
        memory_statuses=(ChatMemoryStatus.REVIEW_REQUIRED,),
        runtime_health=(
            ChatRuntimeHealth(
                component="Application Runtime",
                status="AVAILABLE",
                observed_at=NOW,
            ),
        ),
        generated_at=NOW,
    )


def expires_at() -> datetime:
    return NOW + timedelta(hours=1)
