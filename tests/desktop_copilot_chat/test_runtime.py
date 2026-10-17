from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import threading
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from embedded_copilot.application import (
    ApplicationErrorCode,
    ApplicationQuestionRequest,
    ApplicationRoute,
    ApplicationRouteDecision,
    ApplicationRoutingContext,
    ApplicationRunStatus,
)
from embedded_copilot.desktop_copilot_chat import (
    AnalysisState,
    AnalysisTraceStage,
    ChatApplicationFailed,
    ChatAuditEventType,
    ChatConflict,
    ChatEventType,
    ChatRejected,
    ChatSessionOpenRequest,
    ChatUnavailable,
    DesktopChatRequest,
    DesktopCopilotChatRuntime,
)
from embedded_copilot.desktop_copilot_chat.adapters import (
    FakeChatApplicationAdapter,
    FakeChatAuditSink,
    FakeChatEnvironmentProjection,
    FakeChatEventStore,
    FakeChatProjectProjection,
    FakeChatSessionStore,
    FixedChatClock,
)
from embedded_copilot.engineering_requirements import RequirementContext
from embedded_copilot.multimodal.context import (
    AttachmentBinding,
    ProcessLocalAttachmentBindingRepository,
)
from embedded_copilot.multimodal.models import MultimodalInput, MultimodalInputType

from .factories import (
    NOW,
    PROJECT,
    REQUESTER,
    TENANT,
    application_result,
    expires_at,
    panel,
    project,
    question,
    reference,
)


class _TraceStore:
    def __init__(self) -> None:
        self.events = []

    def append(self, event):
        self.events.append(event)
        return event

    def read(self, trace_id):
        return tuple(event for event in self.events if event.trace_id == trace_id)


class _TimelineConversation:
    def __init__(self, timeline) -> None:
        self.timeline = timeline

    def begin_turn(self, *args, **kwargs):
        self.timeline.append("TURN_BEGIN")

    def complete_turn(self, *args, **kwargs):
        self.timeline.append("TURN_COMPLETED_PERSISTED")

    def fail_turn(self, *args, **kwargs):
        self.timeline.append("TURN_FAILED_PERSISTED")


class _TimelineEventStore(FakeChatEventStore):
    def __init__(self, timeline, *, capacity: int = 32) -> None:
        super().__init__(capacity=capacity)
        self.timeline = timeline

    def append(self, event):
        self.timeline.append(event.event_type.value)
        return super().append(event)


class _ClarificationApplication(FakeChatApplicationAdapter):
    def classify(self, request):
        return ApplicationRouteDecision.for_request(
            request, route=ApplicationRoute.CLARIFICATION_REQUIRED
        )


class _ApprovalProbe:
    def __init__(self):
        self.calls = 0

    def consume(self, **kwargs):
        self.calls += 1
        raise AssertionError("clarification must not consume transmission approval")


class _RoutingContextConversation:
    def routing_context(self, session_id):
        assert session_id == "session-1"
        return ApplicationRoutingContext(
            project_creation_mode="CREATE_NEW",
            project_type="STM32",
            conversation_id="conversation-1",
        )


class _RoutingContextClassifier:
    def __init__(self) -> None:
        self.observed_context = None

    def classify(self, request, *, routing_context=None):
        self.observed_context = routing_context
        route = (
            ApplicationRoute.GREENFIELD_DISCOVERY
            if routing_context is not None
            and routing_context.project_creation_mode == "CREATE_NEW"
            else ApplicationRoute.CLARIFICATION_REQUIRED
        )
        return ApplicationRouteDecision.for_request(request, route=route)


class _EchoApplication:
    def __init__(self) -> None:
        self.calls = 0

    def answer(self, request):
        self.calls += 1
        return application_result(request)


class _ClassifyingEchoApplication(_EchoApplication):
    def __init__(self) -> None:
        super().__init__()
        self.classify_calls = 0

    def classify(self, request):
        self.classify_calls += 1
        return ApplicationRouteDecision.for_request(
            request, route=ApplicationRoute.KNOWLEDGE
        )


class _BarrierSessionStore(FakeChatSessionStore):
    def __init__(self) -> None:
        super().__init__()
        self.barrier = None

    def get(self, session_id):
        value = super().get(session_id)
        if self.barrier is not None:
            self.barrier.wait(timeout=5)
        return value


class _CountingTransmissionApproval:
    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.calls = 0

    def consume(self, **kwargs):
        self.calls += 1
        return self.delegate.consume(**kwargs)


def _runtime(*, capacity: int = 32, traces=None, conversations=None, timeline=None, request=None):
    request = request or question()
    application = FakeChatApplicationAdapter(application_result(request))
    audit = FakeChatAuditSink()
    events = (
        FakeChatEventStore(capacity=capacity)
        if timeline is None
        else _TimelineEventStore(timeline, capacity=capacity)
    )
    runtime = DesktopCopilotChatRuntime(
        application=application,
        projects=FakeChatProjectProjection((project(),), (reference(),), {(TENANT, PROJECT): REQUESTER}),
        environment=FakeChatEnvironmentProjection({(TENANT, PROJECT): panel()}),
        sessions=FakeChatSessionStore(),
        audit=audit,
        events=events,
        clock=FixedChatClock(NOW),
        attachments=ProcessLocalAttachmentBindingRepository(),
        conversations=conversations,
        traces=traces,
    )
    return runtime, application, audit, events


def _open(runtime: DesktopCopilotChatRuntime, *, session_id: str = "session-1"):
    return runtime.open_session(
        ChatSessionOpenRequest(
            session_id=session_id,
            tenant_id=TENANT,
            project_id=PROJECT,
            requester=REQUESTER,
            expires_at=expires_at(),
        )
    )


def _request(*, request_id: str = "request-1", external_search: bool = False) -> DesktopChatRequest:
    return DesktopChatRequest(
        session_id="session-1",
        project_reference=project(),
        application_question=question(request_id=request_id, external_search=external_search),
        external_search=external_search,
        context_references=(reference(),),
        requested_at=NOW,
    )


def _attachment() -> AttachmentBinding:
    return AttachmentBinding(
        session_id="session-1",
        input=MultimodalInput(
            type=MultimodalInputType.FILE,
            reference_id="attachment-1",
            summary="User-selected read-only attachment reference",
        ),
        basename="sensor_config.h",
        size_bytes=1024,
        created_at=NOW,
    )


def test_runtime_calls_application_once_and_replays_safe_chunk_events() -> None:
    runtime, application, audit, _ = _runtime()
    _open(runtime)
    answer = runtime.submit(_request(), requester=REQUESTER, tenant_id=TENANT)
    events = runtime.events_after(
        "session-1", requester=REQUESTER, tenant_id=TENANT, last_event_id=None
    )
    assert application.calls == 1
    assert answer.evidence_references == (reference(),)
    assert [event.event_type for event in events] == [
        ChatEventType.CHAT_STARTED,
        ChatEventType.ANSWER_CHUNK,
        ChatEventType.ANSWER_COMPLETED,
    ]
    assert "answer_text" not in events[-1].model_dump()
    assert [event.event_type for event in audit._items.values()] == [
        ChatAuditEventType.CHAT_SESSION_OPENED,
        ChatAuditEventType.CHAT_REQUESTED,
        ChatAuditEventType.CHAT_COMPLETED,
    ]
    assert all(
        "question" not in event.model_dump() and "answer_text" not in event.model_dump()
        for event in audit._items.values()
    )


def test_same_request_id_and_fingerprint_replays_without_second_execution() -> None:
    runtime, application, audit, events = _runtime()
    _open(runtime)
    request = _request()

    first = runtime.submit(request, requester=REQUESTER, tenant_id=TENANT)
    second = runtime.submit(request, requester=REQUESTER, tenant_id=TENANT)

    assert second == first
    assert application.calls == 1
    assert len(audit._items) == 3
    assert [event.event_type for event in events.read_after("session-1", None)] == [
        ChatEventType.CHAT_STARTED,
        ChatEventType.ANSWER_CHUNK,
        ChatEventType.ANSWER_COMPLETED,
    ]


def test_same_request_id_with_different_fingerprint_fails_closed() -> None:
    runtime, application, _, _ = _runtime()
    _open(runtime)
    runtime.submit(_request(), requester=REQUESTER, tenant_id=TENANT)
    original = question()
    changed = ApplicationQuestionRequest(
        request_id=original.request_id,
        trace_id=original.trace_id,
        project_id=original.project_id,
        requester=original.requester,
        question="A different logical request",
        constraints=original.constraints,
        external_search=original.external_search,
        requested_at=original.requested_at,
    )
    conflicting = DesktopChatRequest(
        session_id="session-1",
        project_reference=project(),
        application_question=changed,
        context_references=(reference(),),
        requested_at=NOW,
    )

    with pytest.raises(ChatConflict):
        runtime.submit(conflicting, requester=REQUESTER, tenant_id=TENANT)
    assert application.calls == 1


def test_same_logical_request_replays_when_transport_metadata_changes() -> None:
    runtime, _, _, _ = _runtime()
    runtime._application = _EchoApplication()
    _open(runtime)
    first_request = _request()
    second_question = ApplicationQuestionRequest(
        request_id="request-1",
        trace_id="trace-2",
        project_id=PROJECT,
        requester=REQUESTER,
        question="Why is measured power 120mA?",
        constraints=(),
        external_search=False,
        requested_at=NOW + timedelta(seconds=1),
    )
    second_request = DesktopChatRequest(
        session_id="session-1",
        project_reference=project(),
        application_question=second_question,
        context_references=(reference(),),
        requested_at=NOW + timedelta(seconds=1),
    )

    first = runtime.submit(first_request, requester=REQUESTER, tenant_id=TENANT)
    second = runtime.submit(second_request, requester=REQUESTER, tenant_id=TENANT)

    assert second == first
    assert runtime._application.calls == 1


def test_different_request_id_executes_independently() -> None:
    runtime, _, _, _ = _runtime()
    application = _EchoApplication()
    runtime._application = application
    _open(runtime)

    runtime.submit(_request(request_id="request-1"), requester=REQUESTER, tenant_id=TENANT)
    runtime.submit(_request(request_id="request-2"), requester=REQUESTER, tenant_id=TENANT)

    assert application.calls == 2


def test_failed_request_is_not_cached_and_can_retry() -> None:
    runtime, application, _, _ = _runtime()
    _open(runtime)
    request = _request()
    application.fail = True

    with pytest.raises(ChatApplicationFailed):
        runtime.submit(request, requester=REQUESTER, tenant_id=TENANT)

    application.fail = False
    answer = runtime.submit(request, requester=REQUESTER, tenant_id=TENANT)

    assert answer.status.value == "COMPLETED"
    assert application.calls == 2


def test_concurrent_same_request_replays_one_canonical_execution() -> None:
    runtime, _, _, _ = _runtime()
    application = _ClassifyingEchoApplication()
    runtime._application = application
    sessions = _BarrierSessionStore()
    runtime._sessions = sessions
    _open(runtime)
    sessions.barrier = threading.Barrier(2)
    request = _request()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(runtime.submit, request, requester=REQUESTER, tenant_id=TENANT)
            for _ in range(2)
        ]
        answers = [future.result(timeout=5) for future in futures]

    assert answers[0] == answers[1]
    assert application.classify_calls == 1
    assert application.calls == 1


def test_managed_provider_path_replays_sequential_duplicate_after_one_approval(
    tmp_path,
) -> None:
    from embedded_copilot.production_release.credentials import InMemoryCredentialProvider
    from embedded_copilot.production_release.provider import (
        ProviderConfigurationService,
        TransmissionApprovalService,
        TransmissionDecision,
    )

    provider = ProviderConfigurationService(
        configuration_path=tmp_path / "production.json",
        credentials=InMemoryCredentialProvider(),
    )
    approval = _CountingTransmissionApproval(
        TransmissionApprovalService(provider, ttl_seconds=60 * 60 * 24 * 3650)
    )
    receipt = approval.delegate.issue(
        tenant_id=TENANT,
        requester=REQUESTER,
        session_id="session-1",
        project_id=PROJECT,
        request_id="request-1",
        reference_fingerprints=(reference().fingerprint,),
        decision=TransmissionDecision.APPROVED,
        now=NOW,
    )
    assert receipt.decision is TransmissionDecision.APPROVED
    managed_question = question(external_search=True)
    runtime, application, _, _ = _runtime(request=managed_question)
    runtime._transmission_approval = approval
    _open(runtime)
    request = _request(external_search=True)

    first = runtime.submit(request, requester=REQUESTER, tenant_id=TENANT)
    second = runtime.submit(request, requester=REQUESTER, tenant_id=TENANT)

    assert second == first
    assert application.calls == 1
    assert approval.calls == 1


def test_managed_provider_path_replays_concurrent_duplicate_after_one_approval(
    tmp_path,
) -> None:
    from embedded_copilot.production_release.credentials import InMemoryCredentialProvider
    from embedded_copilot.production_release.provider import (
        ProviderConfigurationService,
        TransmissionApprovalService,
        TransmissionDecision,
    )

    provider = ProviderConfigurationService(
        configuration_path=tmp_path / "production.json",
        credentials=InMemoryCredentialProvider(),
    )
    approval = _CountingTransmissionApproval(
        TransmissionApprovalService(provider, ttl_seconds=60 * 60 * 24 * 3650)
    )
    receipt = approval.delegate.issue(
        tenant_id=TENANT,
        requester=REQUESTER,
        session_id="session-1",
        project_id=PROJECT,
        request_id="request-1",
        reference_fingerprints=(reference().fingerprint,),
        decision=TransmissionDecision.APPROVED,
        now=NOW,
    )
    assert receipt.decision is TransmissionDecision.APPROVED
    managed_question = question(external_search=True)
    runtime, application, _, _ = _runtime(request=managed_question)
    sessions = _BarrierSessionStore()
    runtime._sessions = sessions
    runtime._transmission_approval = approval
    _open(runtime)
    sessions.barrier = threading.Barrier(2)
    request = _request(external_search=True)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(runtime.submit, request, requester=REQUESTER, tenant_id=TENANT)
            for _ in range(2)
        ]
        answers = [future.result(timeout=5) for future in futures]

    assert answers[0] == answers[1]
    assert application.calls == 1
    assert approval.calls == 1


def test_normal_local_first_chat_skips_per_request_transmission_approval() -> None:
    runtime, application, _, _ = _runtime()
    approval = _ApprovalProbe()
    runtime._transmission_approval = approval
    _open(runtime)

    answer = runtime.submit(_request(), requester=REQUESTER, tenant_id=TENANT)

    assert answer.status.value == "COMPLETED"
    assert application.calls == 1
    assert approval.calls == 0


def test_classify_injects_conversation_routing_context() -> None:
    runtime, _, _, _ = _runtime(conversations=_RoutingContextConversation())
    classifier = _RoutingContextClassifier()
    runtime._application = classifier
    _open(runtime)

    result = runtime.classify(_request(), requester=REQUESTER, tenant_id=TENANT)

    assert result.route is ApplicationRoute.GREENFIELD_DISCOVERY
    assert classifier.observed_context == ApplicationRoutingContext(
        project_creation_mode="CREATE_NEW",
        project_type="STM32",
        conversation_id="conversation-1",
    )


def test_clarification_persists_waiting_state_without_approval_or_provider() -> None:
    request = _request(request_id="clarification-request")
    application = _ClarificationApplication(
        application_result(
            question(request_id=request.application_question.request_id),
            status=ApplicationRunStatus.REJECTED,
            route=ApplicationRoute.CLARIFICATION_REQUIRED,
            error_code=ApplicationErrorCode.CLARIFICATION_REQUIRED,
        )
    )
    approval = _ApprovalProbe()
    runtime = DesktopCopilotChatRuntime(
        application=application,
        projects=FakeChatProjectProjection((project(),), (reference(),), {(TENANT, PROJECT): REQUESTER}),
        environment=FakeChatEnvironmentProjection({(TENANT, PROJECT): panel()}),
        sessions=FakeChatSessionStore(),
        audit=FakeChatAuditSink(),
        events=FakeChatEventStore(),
        clock=FixedChatClock(NOW),
        attachments=ProcessLocalAttachmentBindingRepository(),
        transmission_approval=approval,
    )
    _open(runtime)

    with pytest.raises(ChatApplicationFailed, match="CLARIFICATION_REQUIRED"):
        runtime.submit(request, requester=REQUESTER, tenant_id=TENANT)

    events = runtime.events_after("session-1", requester=REQUESTER, tenant_id=TENANT, last_event_id=None)
    assert events is not None
    assert [event.event_type for event in events].count(ChatEventType.ANSWER_FAILED) == 1
    terminal = events[-1]
    assert terminal.analysis_state is AnalysisState.WAITING_FOR_CLARIFICATION
    assert terminal.reason_code == "CLARIFICATION_REQUIRED"
    assert approval.calls == 0


def test_completion_persists_turn_before_terminal_event_and_projection_trace() -> None:
    timeline = []
    traces = _TraceStore()
    runtime, _, _, _ = _runtime(
        traces=traces,
        conversations=_TimelineConversation(timeline),
        timeline=timeline,
    )
    _open(runtime)

    runtime.submit(_request(), requester=REQUESTER, tenant_id=TENANT)

    assert timeline.index("TURN_COMPLETED_PERSISTED") < timeline.index("ANSWER_COMPLETED")
    stages = [event.stage for event in traces.events]
    assert stages.index(AnalysisTraceStage.ANSWER_PERSIST) < stages.index(AnalysisTraceStage.SSE_TERMINAL)
    assert stages.index(AnalysisTraceStage.SSE_TERMINAL) < stages.index(AnalysisTraceStage.UI_PROJECTION)


def test_resumed_session_reads_only_its_own_persisted_turn_trace() -> None:
    traces = _TraceStore()
    runtime, _, _, _ = _runtime(traces=traces)
    _open(runtime)
    runtime.submit(_request(), requester=REQUESTER, tenant_id=TENANT)
    original = traces.events[0]
    _open(runtime, session_id="session-2")
    _open(runtime, session_id="session-3")

    same_conversation = SimpleNamespace(
        conversation_id="conversation-1", project_id=PROJECT, tenant_id=TENANT,
        requester=REQUESTER, turns=(SimpleNamespace(trace_id=original.trace_id, request_id=original.request_id),),
    )
    other_conversation = SimpleNamespace(
        conversation_id="conversation-2", project_id=PROJECT, tenant_id=TENANT,
        requester=REQUESTER, turns=(),
    )

    class _Conversations:
        def conversation_for_session(self, session_id):
            return {"session-1": same_conversation, "session-2": same_conversation, "session-3": other_conversation}.get(session_id)

    runtime.set_conversation_port(_Conversations())
    assert runtime.trace("session-2", original.trace_id, requester=REQUESTER, tenant_id=TENANT) == tuple(traces.events)
    assert runtime.trace("session-2", "missing-trace", requester=REQUESTER, tenant_id=TENANT) == ()
    with pytest.raises(ChatRejected):
        runtime.trace("session-3", original.trace_id, requester=REQUESTER, tenant_id=TENANT)
    with pytest.raises(ChatRejected):
        runtime.trace("session-2", original.trace_id, requester="other-requester", tenant_id=TENANT)
    same_conversation.turns = ()
    with pytest.raises(ChatRejected):
        runtime.trace("session-2", original.trace_id, requester=REQUESTER, tenant_id=TENANT)


def test_resumed_session_rejects_trace_when_conversation_binding_is_unavailable() -> None:
    traces = _TraceStore()
    runtime, _, _, _ = _runtime(traces=traces)
    _open(runtime)
    runtime.submit(_request(), requester=REQUESTER, tenant_id=TENANT)
    _open(runtime, session_id="session-2")
    with pytest.raises(ChatRejected):
        runtime.trace("session-2", traces.events[0].trace_id, requester=REQUESTER, tenant_id=TENANT)


def test_runtime_rejects_cross_identity_and_unavailable_reference() -> None:
    runtime, _, _, _ = _runtime()
    _open(runtime)
    with pytest.raises(ChatRejected):
        runtime.submit(_request(), requester="other-requester", tenant_id=TENANT)
    other_reference = reference().model_copy(update={"project_id": "project-2"})
    with pytest.raises(ValidationError):
        DesktopChatRequest(
            session_id="session-1",
            project_reference=project(),
            application_question=question(),
            context_references=(other_reference,),
            requested_at=NOW,
        )


def test_runtime_records_failure_and_stale_event_cursor_requires_resync() -> None:
    runtime, application, audit, _ = _runtime(capacity=2)
    _open(runtime)
    application.fail = True
    with pytest.raises(ChatApplicationFailed):
        runtime.submit(_request(), requester=REQUESTER, tenant_id=TENANT)
    failed = runtime.events_after(
        "session-1", requester=REQUESTER, tenant_id=TENANT, last_event_id=None
    )
    assert [event.event_type for event in failed] == [
        ChatEventType.CHAT_STARTED,
        ChatEventType.ANSWER_FAILED,
    ]
    assert any(
        event.event_type is ChatAuditEventType.CHAT_FAILED
        for event in audit._items.values()
    )
    assert (
        runtime.events_after(
            "session-1", requester=REQUESTER, tenant_id=TENANT, last_event_id="stale"
        )
        is None
    )


def test_application_type_error_is_not_retried_as_signature_fallback() -> None:
    class _TypeErrorApplication:
        def __init__(self) -> None:
            self.calls = 0

        def answer(self, request, *, routing_context=None, route_decision=None):
            del request, routing_context, route_decision
            self.calls += 1
            raise TypeError("application failure")

    traces = _TraceStore()
    runtime, _, _, _ = _runtime(traces=traces)
    application = _TypeErrorApplication()
    runtime._application = application
    _open(runtime)

    with pytest.raises(ChatApplicationFailed):
        runtime.submit(_request(), requester=REQUESTER, tenant_id=TENANT)

    assert application.calls == 1
    gateway_trace = next(
        event for event in traces.events if event.stage is AnalysisTraceStage.MODEL_GATEWAY
    )
    assert gateway_trace.gate_result == "NOT_INVOKED"
    assert gateway_trace.reason_code == "APPLICATION_UNAVAILABLE"
    assert gateway_trace.original_exception_type == "TypeError"
    assert gateway_trace.exception_layer == "DESKTOPCOPILOTCHATRUNTIME"
    assert gateway_trace.exception_stage == "APPLICATION_RUNTIME"
    assert gateway_trace.traceback_fingerprint is not None


def test_answer_schema_failure_trace_uses_observed_gateway_diagnostics() -> None:
    request = _request(request_id="schema-failure-request")
    traces = _TraceStore()
    application = FakeChatApplicationAdapter(
        application_result(
            question(request_id=request.application_question.request_id),
            status=ApplicationRunStatus.FAILED,
            route=ApplicationRoute.GREENFIELD_DISCOVERY,
            error_code=ApplicationErrorCode.ANSWER_SCHEMA_INVALID,
            gateway_invocation_count=1,
            provider_response_present=True,
            answer_validation_stage="MODEL_RESPONSE_NORMALIZATION",
            answer_rejection_reason=ApplicationErrorCode.ANSWER_SCHEMA_INVALID,
            answer_text_present=True,
        )
    )
    runtime = DesktopCopilotChatRuntime(
        application=application,
        projects=FakeChatProjectProjection((project(),), (reference(),), {(TENANT, PROJECT): REQUESTER}),
        environment=FakeChatEnvironmentProjection({(TENANT, PROJECT): panel()}),
        sessions=FakeChatSessionStore(),
        audit=FakeChatAuditSink(),
        events=FakeChatEventStore(),
        clock=FixedChatClock(NOW),
        attachments=ProcessLocalAttachmentBindingRepository(),
        traces=traces,
    )
    _open(runtime)

    with pytest.raises(ChatApplicationFailed, match="ANSWER_SCHEMA_INVALID"):
        runtime.submit(request, requester=REQUESTER, tenant_id=TENANT)

    gateway_trace = next(
        event for event in traces.events if event.stage is AnalysisTraceStage.MODEL_GATEWAY
    )
    assert gateway_trace.gateway_invocation_count == 1
    assert gateway_trace.gate_result == "RESPONSE_REJECTED"
    assert gateway_trace.reason_code == "ANSWER_SCHEMA_INVALID"
    terminals = runtime.events_after(
        "session-1", requester=REQUESTER, tenant_id=TENANT, last_event_id=None
    )
    assert terminals is not None
    assert [event.event_type for event in terminals].count(ChatEventType.ANSWER_FAILED) == 1
    assert [event.event_type for event in terminals].count(ChatEventType.ANSWER_COMPLETED) == 0
    serialized = " ".join(str(event.model_dump(mode="json")) for event in traces.events)
    assert "Authorization" not in serialized
    assert "test-key" not in serialized


def test_greenfield_context_failure_keeps_partial_projection_and_does_not_become_answer_contract() -> None:
    request = _request(request_id="greenfield-context-failure-request")
    traces = _TraceStore()
    application = FakeChatApplicationAdapter(
        application_result(
            question(request_id=request.application_question.request_id),
            status=ApplicationRunStatus.FAILED,
            route=ApplicationRoute.GREENFIELD_DISCOVERY,
            error_code=ApplicationErrorCode.CONTEXT_UNAVAILABLE,
            requirement_context=RequirementContext(
                project_id=PROJECT,
                conversation_id=request.session_id,
            ),
        )
    )
    runtime = DesktopCopilotChatRuntime(
        application=application,
        projects=FakeChatProjectProjection((project(),), (reference(),), {(TENANT, PROJECT): REQUESTER}),
        environment=FakeChatEnvironmentProjection({(TENANT, PROJECT): panel()}),
        sessions=FakeChatSessionStore(),
        audit=FakeChatAuditSink(),
        events=FakeChatEventStore(),
        clock=FixedChatClock(NOW),
        attachments=ProcessLocalAttachmentBindingRepository(),
        traces=traces,
    )
    _open(runtime)

    with pytest.raises(ChatApplicationFailed, match="CONTEXT_BUILD_UNAVAILABLE"):
        runtime.submit(request, requester=REQUESTER, tenant_id=TENANT)

    context_trace = next(
        event for event in traces.events if event.stage is AnalysisTraceStage.CONTEXT_BUILD
    )
    assert context_trace.gate_result == "PASS"
    assert context_trace.reason_code == "GREENFIELD_CONTEXT_READY"
    gateway_trace = next(
        event for event in traces.events if event.stage is AnalysisTraceStage.MODEL_GATEWAY
    )
    assert gateway_trace.gate_result == "NOT_INVOKED"
    assert gateway_trace.reason_code == "CONTEXT_BUILD_UNAVAILABLE"
    terminal = runtime.events_after(
        "session-1", requester=REQUESTER, tenant_id=TENANT, last_event_id=None
    )[-1]
    assert terminal.event_type is ChatEventType.ANSWER_FAILED
    assert terminal.analysis_state is AnalysisState.REQUIREMENTS_PARTIAL
    assert terminal.reason_code == "CONTEXT_BUILD_UNAVAILABLE"


def test_brownfield_context_failure_remains_fail_closed() -> None:
    request = _request(request_id="brownfield-context-failure-request")
    application = FakeChatApplicationAdapter(
        application_result(
            question(request_id=request.application_question.request_id),
            status=ApplicationRunStatus.FAILED,
            route=ApplicationRoute.BROWNFIELD_ANALYSIS,
            error_code=ApplicationErrorCode.CONTEXT_UNAVAILABLE,
        )
    )
    runtime = DesktopCopilotChatRuntime(
        application=application,
        projects=FakeChatProjectProjection((project(),), (reference(),), {(TENANT, PROJECT): REQUESTER}),
        environment=FakeChatEnvironmentProjection({(TENANT, PROJECT): panel()}),
        sessions=FakeChatSessionStore(),
        audit=FakeChatAuditSink(),
        events=FakeChatEventStore(),
        clock=FixedChatClock(NOW),
        attachments=ProcessLocalAttachmentBindingRepository(),
    )
    _open(runtime)

    with pytest.raises(ChatApplicationFailed, match="CONTEXT_BUILD_UNAVAILABLE"):
        runtime.submit(request, requester=REQUESTER, tenant_id=TENANT)

    terminal = runtime.events_after(
        "session-1", requester=REQUESTER, tenant_id=TENANT, last_event_id=None
    )[-1]
    assert terminal.event_type is ChatEventType.ANSWER_FAILED
    assert terminal.analysis_state is AnalysisState.FAILED
    assert terminal.reason_code == "CONTEXT_BUILD_UNAVAILABLE"


def test_session_open_is_idempotent_but_non_owner_identity_is_rejected() -> None:
    runtime, _, _, _ = _runtime()
    first = _open(runtime)
    assert _open(runtime) == first
    with pytest.raises(ChatRejected):
        runtime.open_session(
            ChatSessionOpenRequest(
                session_id="session-1",
                tenant_id=TENANT,
                project_id=PROJECT,
                requester="other-requester",
                expires_at=expires_at(),
            )
        )


def test_session_open_fails_closed_without_owner_bound_project_port() -> None:
    runtime, _, _, _ = _runtime()

    class TenantOnlyProjects:
        def project(self, tenant_id, project_id):
            return project()

        def references(self, tenant_id, project_id):
            return (reference(),)

    runtime._projects = TenantOnlyProjects()
    with pytest.raises(ChatUnavailable):
        _open(runtime)


def test_runtime_binds_attachment_metadata_as_session_reference() -> None:
    runtime, application, _, _ = _runtime()
    _open(runtime)

    attachment = runtime.bind_attachment(
        _attachment(), requester=REQUESTER, tenant_id=TENANT
    )
    references = runtime.references(
        "session-1", requester=REQUESTER, tenant_id=TENANT
    )

    assert attachment.reference_kind.value == "WORKSPACE"
    assert attachment.reference_id == "attachment-1"
    assert attachment.display_label == "sensor_config.h"
    assert attachment in references
    request = _request().model_copy(update={"context_references": (attachment,)})
    answer = runtime.submit(request, requester=REQUESTER, tenant_id=TENANT)
    assert application.calls == 1
    assert answer.evidence_references == (attachment,)
    assert "content" not in attachment.model_dump()


def test_runtime_rejects_attachment_for_wrong_identity() -> None:
    runtime, _, _, _ = _runtime()
    _open(runtime)
    with pytest.raises(ChatRejected):
        runtime.bind_attachment(
            _attachment(), requester=REQUESTER, tenant_id="tenant-2"
        )
