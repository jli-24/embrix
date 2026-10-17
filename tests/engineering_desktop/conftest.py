from datetime import UTC, datetime, timedelta

import pytest

from embedded_copilot.application import (
    ApplicationAuditEvent,
    ApplicationAuditEventType,
    ApplicationErrorCode,
    ApplicationQuestionRequest,
    ApplicationRoute,
    ApplicationRunResult,
    ApplicationRunStatus,
)
from embedded_copilot.engineering_desktop import (
    DesktopModelProviderView,
    DesktopModelSettingsView,
    DesktopProvider,
    DesktopRuntimeComponent,
    DesktopRuntimeComponentView,
    DesktopRuntimeStatus,
    EngineeringDesktopRuntime,
    FakeApplicationAdapter,
    FakeAuditSink,
    FakeCapabilityAdapter,
    FakeModelSettingsAdapter,
    FakeSessionAdapter,
    FakeWorkspaceAdapter,
    FixedDesktopClock,
)
from embedded_copilot.engineering_qa import (
    EngineeringAnswer,
    EngineeringQuestion,
    ModelInfo,
)
from embedded_copilot.engineering_workspace import (
    EngineeringWorkspaceSnapshot,
    WorkspaceDomain,
    WorkspaceLifecycleStatus,
    WorkspaceLineageLink,
    WorkspaceObjectKind,
    WorkspaceObjectReference,
    WorkspaceRelationType,
    WorkspaceTimelineEntry,
)
from embedded_copilot.engineering_workspace.contracts import deterministic_id

NOW = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
FP_A = "sha256:" + "a" * 64
FP_B = "sha256:" + "b" * 64


def application_request() -> ApplicationQuestionRequest:
    return ApplicationQuestionRequest(
        request_id="request1",
        trace_id="trace1",
        project_id="project1",
        requester="engineer1",
        question="Explain the timer constraint",
        constraints=(),
        requested_at=NOW,
    )


def application_result(
    status: ApplicationRunStatus = ApplicationRunStatus.COMPLETED,
) -> ApplicationRunResult:
    request = application_request()
    answer = None
    context = None
    error = ApplicationErrorCode.UNSUPPORTED_ROUTE
    route = ApplicationRoute.UNKNOWN
    evidence: tuple[str, ...] = ()
    if status is ApplicationRunStatus.COMPLETED:
        question = EngineeringQuestion(
            question_id="question1",
            project_id=request.project_id,
            question=request.question,
            constraints=(),
        )
        answer = EngineeringAnswer(
            question_id=question.question_id,
            content="The timer uses the configured clock source.",
            confidence=0.8,
            evidence_refs=("source1",),
            model_info=ModelInfo(
                provider="openai", model_name="model1", finish_reason="stop"
            ),
        )
        context = FP_A
        error = None
        route = ApplicationRoute.KNOWLEDGE
        evidence = answer.evidence_refs
    event_type = {
        ApplicationRunStatus.COMPLETED: ApplicationAuditEventType.QUESTION_ANSWERED,
        ApplicationRunStatus.REJECTED: ApplicationAuditEventType.QUESTION_REJECTED,
        ApplicationRunStatus.FAILED: ApplicationAuditEventType.QUESTION_FAILED,
    }[status]
    event = ApplicationAuditEvent(
        event_id=ApplicationAuditEvent.event_id_for(request.fingerprint, event_type),
        event_type=event_type,
        request_id=request.request_id,
        trace_id=request.trace_id,
        project_id=request.project_id,
        requester=request.requester,
        route=route,
        status=status,
        question_fingerprint=request.fingerprint,
        context_fingerprint=context,
        answer_fingerprint=None if answer is None else answer.fingerprint,
        model_info_fingerprint=None
        if answer is None
        else answer.model_info.fingerprint,
        evidence_refs=evidence,
        error_code=error,
        timestamp=NOW,
    )
    return ApplicationRunResult.create(
        request=request,
        route=route,
        status=status,
        context_fingerprint=context,
        answer=answer,
        error_code=error,
        audit_event=event,
    )


def workspace_snapshot() -> EngineeringWorkspaceSnapshot:
    reference = WorkspaceObjectReference(
        project_id="project1",
        source_domain=WorkspaceDomain.PCB,
        source_kind=WorkspaceObjectKind.ANALYSIS,
        source_object_id="analysis1",
        source_fingerprint=FP_A,
        occurred_at=NOW,
        status=WorkspaceLifecycleStatus.ACTIVE,
    )
    lineage = WorkspaceLineageLink(
        source_domain=WorkspaceDomain.ENGINEERING_CONTEXT,
        source_kind=WorkspaceObjectKind.OVERLAY,
        source_object_id="overlay1",
        source_fingerprint=FP_B,
        relation_type=WorkspaceRelationType.SUPPORTS,
    )
    entry = WorkspaceTimelineEntry(
        entry_id=deterministic_id("workspace-entry", reference.fingerprint, "1"),
        sequence=1,
        reference=reference,
        lineage=(lineage,),
    )
    return EngineeringWorkspaceSnapshot(
        workspace_id="workspace1",
        project_id="project1",
        source_references=(reference,),
        timeline=(entry,),
        generated_at=NOW,
    )


@pytest.fixture
def desktop_runtime():
    clock = FixedDesktopClock(NOW)
    sessions = FakeSessionAdapter()
    audit = FakeAuditSink()
    runtime = EngineeringDesktopRuntime(
        sessions=sessions,
        workspaces=FakeWorkspaceAdapter((workspace_snapshot(),)),
        applications=FakeApplicationAdapter(application_result()),
        capabilities=FakeCapabilityAdapter(
            tuple(
                DesktopRuntimeComponentView(
                    component=component,
                    status=DesktopRuntimeStatus.READY,
                )
                for component in DesktopRuntimeComponent
            )
        ),
        model_settings=FakeModelSettingsAdapter(
            DesktopModelSettingsView(
                providers=(
                    DesktopModelProviderView(
                        provider=DesktopProvider.OPENAI,
                        model="model1",
                        capabilities=("KNOWLEDGE",),
                        status=DesktopRuntimeStatus.READY,
                    ),
                ),
                generated_at=NOW,
            )
        ),
        audit=audit,
        clock=clock,
    )
    return runtime, clock, sessions, audit


@pytest.fixture
def expiry() -> datetime:
    return NOW + timedelta(hours=1)
