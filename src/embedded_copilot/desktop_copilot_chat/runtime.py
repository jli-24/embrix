from __future__ import annotations

import copy
import hashlib
import inspect
import json
import logging
import threading

from embedded_copilot.application import (
    ApplicationQuestionRequest,
    ApplicationRoute,
    ApplicationRouteDecision,
    ApplicationRoutingContext,
    ApplicationRunResult,
    ApplicationRunStatus,
)
from embedded_copilot.engineering_requirements import RequirementContext, RequirementLifecycle
from embedded_copilot.engineering_research import EngineeringResearchSnapshot
from embedded_copilot.engineering_copilot import EngineeringPlan, PlanReviewDecision
from embedded_copilot.runtime_diagnostics import (
    attach_exception_diagnostics,
    exception_diagnostics,
)
from embedded_copilot.multimodal.context import (
    AttachmentBinding,
    AttachmentBindingConflict,
)

from .contracts import identifier, utc_datetime
from .exceptions import (
    ChatApplicationFailed,
    ChatConflict,
    ChatRejected,
    ChatUnavailable,
)
from .models import (
    AnalysisState,
    AnalysisTraceStage,
    ChatAnswerStatus,
    ChatAnswerView,
    ChatAuditEvent,
    ChatAuditEventType,
    ChatContextReference,
    ChatEvent,
    ChatEventType,
    ChatEvidenceView,
    ChatProjectReference,
    ChatRecommendationView,
    ChatReferenceKind,
    ChatRuntimePanelView,
    ChatSession,
    ChatSessionOpenRequest,
    ChatSessionStatus,
    ChatTraceEvent,
    DesktopChatRequest,
)
from .ports import (
    ChatAttachmentBindingPort,
    ChatAuditSinkPort,
    ChatClockPort,
    ChatConversationPort,
    ChatEnvironmentProjectionPort,
    ChatEventStorePort,
    ChatProjectProjectionPort,
    ChatSessionStorePort,
    ChatTraceStorePort,
    ChatTransmissionApprovalPort,
    DesktopApplicationPort,
)
from .response_contract import resolve_response_type

_CHUNK_SIZE = 384
_logger = logging.getLogger(__name__)


def _accepts_keyword(method: object, name: str) -> bool:
    """Return whether a legacy-compatible callable accepts a keyword.

    Do not use a broad ``except TypeError`` around application calls: a
    TypeError raised by the application itself is an execution failure and
    must not be reinterpreted as a legacy signature mismatch.
    """

    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        # The current DesktopApplicationPort requires these keywords.  If a
        # callable cannot be inspected, invoke it once and preserve its
        # original exception boundary.
        return True
    return name in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def _chat_failure_code(value: object) -> str:
    code = getattr(value, "value", value)
    safe = {
        "EXTERNAL_EVIDENCE_UNAVAILABLE": "COPILOT_CHAT_EXTERNAL_EVIDENCE_UNAVAILABLE",
        "CONTEXT_UNAVAILABLE": "CONTEXT_BUILD_UNAVAILABLE",
        "EMPTY_PROJECT": "COPILOT_CHAT_EMPTY_PROJECT",
        "NO_ANALYZABLE_SOURCE": "COPILOT_CHAT_NO_ANALYZABLE_SOURCE",
        "INSUFFICIENT_EVIDENCE": "COPILOT_CHAT_INSUFFICIENT_EVIDENCE",
        "PROVIDER_NOT_CONFIGURED": "COPILOT_CHAT_PROVIDER_NOT_CONFIGURED",
        "PROVIDER_AUTH_FAILED": "PROVIDER_AUTH_FAILED",
        "PROVIDER_NETWORK_UNAVAILABLE": "PROVIDER_NETWORK_UNAVAILABLE",
        "PROVIDER_MODEL_UNAVAILABLE": "PROVIDER_MODEL_UNAVAILABLE",
        "PROVIDER_CONFIG_INVALID": "PROVIDER_CONFIG_INVALID",
        "PROVIDER_TRANSPORT_FAILED": "PROVIDER_TRANSPORT_FAILED",
        "PROVIDER_PARSE_FAILED": "PROVIDER_PARSE_FAILED",
        "PROVIDER_CONNECTION_UNVERIFIED": "PROVIDER_CONNECTION_UNVERIFIED",
        "ANALYSIS_RUNTIME_FAILED": "ANALYSIS_RUNTIME_FAILED",
        "PROVIDER_UNAVAILABLE": "COPILOT_CHAT_PROVIDER_UNAVAILABLE",
        "MODEL_UNAVAILABLE": "COPILOT_CHAT_PROVIDER_UNAVAILABLE",
        "ANSWER_REJECTED": "COPILOT_CHAT_ANALYSIS_RUNTIME_FAILED",
        "ANSWER_SCHEMA_INVALID": "ANSWER_SCHEMA_INVALID",
        "ANSWER_CONTRACT_MISMATCH": "ANSWER_CONTRACT_MISMATCH",
        "ANSWER_EMPTY": "ANSWER_EMPTY",
        "ANSWER_REJECTED_UNSAFE": "ANSWER_REJECTED_UNSAFE",
        "ANSWER_REJECTED_EVIDENCE_MISMATCH": "ANSWER_REJECTED_EVIDENCE_MISMATCH",
        "EXTERNAL_TRANSMISSION_APPROVAL_REQUIRED": "EXTERNAL_TRANSMISSION_APPROVAL_REQUIRED",
        "EXTERNAL_TRANSMISSION_DENIED": "EXTERNAL_TRANSMISSION_DENIED",
        "EXTERNAL_TRANSMISSION_APPROVAL_EXPIRED": "EXTERNAL_TRANSMISSION_APPROVAL_EXPIRED",
        "EXTERNAL_TRANSMISSION_APPROVAL_MISMATCH": "EXTERNAL_TRANSMISSION_APPROVAL_MISMATCH",
        "APPLICATION_UNAVAILABLE": "COPILOT_CHAT_ANALYSIS_RUNTIME_FAILED",
        "CLARIFICATION_REQUIRED": "CLARIFICATION_REQUIRED",
    }
    return safe.get(str(code), "COPILOT_CHAT_ANALYSIS_RUNTIME_FAILED")


def _copy(value: object, model: type, *, field: str):
    if type(value) is not model:
        raise ChatUnavailable(f"{field} returned an invalid type")
    try:
        material = copy.deepcopy(value.model_dump(mode="python"))
        material["fingerprint"] = "sha256:" + "0" * 64
        return model.model_validate(material)
    except (TypeError, ValueError) as error:
        raise ChatUnavailable(f"{field} returned invalid content") from error


class _RequestExecution:
    __slots__ = ("event", "fingerprint", "result")

    def __init__(self, fingerprint: str) -> None:
        self.event = threading.Event()
        self.fingerprint = fingerprint
        self.result: ChatAnswerView | None = None


def _request_idempotency_fingerprint(
    session: ChatSession, request: DesktopChatRequest
) -> str:
    material = {
        "tenant_id": session.tenant_id,
        "requester": session.requester,
        "session_id": session.session_id,
        "project_id": session.project_id,
        "question": request.application_question.question,
        "constraints": request.application_question.constraints,
        "external_search": request.external_search,
        "context_references": tuple(
            sorted(
                (
                    item.reference_kind.value,
                    item.fingerprint,
                )
                for item in request.context_references
            )
        ),
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class DesktopCopilotChatRuntime:
    __slots__ = (
        "_application",
        "_attachments",
        "_audit",
        "_clock",
        "_conversations",
        "_environment",
        "_events",
        "_idempotency",
        "_idempotency_lock",
        "_projects",
        "_sessions",
        "_traces",
        "_transmission_approval",
    )

    def __init__(
        self,
        *,
        application: DesktopApplicationPort,
        projects: ChatProjectProjectionPort,
        environment: ChatEnvironmentProjectionPort,
        sessions: ChatSessionStorePort,
        audit: ChatAuditSinkPort,
        events: ChatEventStorePort,
        clock: ChatClockPort,
        attachments: ChatAttachmentBindingPort,
        conversations: ChatConversationPort | None = None,
        traces: ChatTraceStorePort | None = None,
        transmission_approval: ChatTransmissionApprovalPort | None = None,
    ) -> None:
        self._application = application
        self._projects = projects
        self._environment = environment
        self._sessions = sessions
        self._audit = audit
        self._events = events
        self._idempotency = {}
        self._idempotency_lock = threading.RLock()
        self._clock = clock
        self._attachments = attachments
        self._conversations = conversations
        self._traces = traces
        self._transmission_approval = transmission_approval

    @property
    def application_runtime(self) -> DesktopApplicationPort:
        """Expose the composed application boundary to governed action routes."""

        return self._application

    def set_conversation_port(self, conversations: ChatConversationPort | None) -> None:
        self._conversations = conversations

    def _begin_turn(self, session: ChatSession, request: DesktopChatRequest) -> None:
        if self._conversations is None:
            return
        try:
            self._conversations.begin_turn(
                session.session_id,
                request_id=request.application_question.request_id,
                trace_id=request.application_question.trace_id,
                question=request.application_question.question,
                created_at=request.requested_at,
                context_references=tuple(item.model_dump(mode="json") for item in request.context_references),
            )
        except Exception as error:
            raise ChatUnavailable("conversation history is unavailable") from error

    def _fail_turn(
        self,
        session: ChatSession,
        request_id: str,
        code: str | None,
        *,
        context_references: tuple[ChatContextReference, ...] = (),
        analysis_state: AnalysisState = AnalysisState.FAILED,
        analysis_reason_code: str | None = None,
        route: str | None = None,
        trace_id: str | None = None,
        terminal_event: str | None = "ANSWER_FAILED",
        answer_text: str | None = None,
        requirement_context: RequirementContext | None = None,
        research_snapshot: EngineeringResearchSnapshot | None = None,
        engineering_plan: EngineeringPlan | None = None,
        engineering_plan_review: PlanReviewDecision | None = None,
        architecture_design: object | None = None,
        architecture_review: object | None = None,
        design_truth: object | None = None,
    ) -> None:
        if self._conversations is None:
            return
        try:
            values = dict(
                context_references=tuple(
                    item.model_dump(mode="json") for item in context_references
                ),
                analysis_state=analysis_state.value,
                analysis_reason_code=analysis_reason_code,
                route=route,
                trace_id=trace_id,
                terminal_event=terminal_event,
                answer_text=answer_text,
                requirement_context=requirement_context,
                research_snapshot=research_snapshot,
                engineering_plan=engineering_plan,
                engineering_plan_review=engineering_plan_review,
                architecture_design=architecture_design,
                architecture_review=architecture_review,
                design_truth=design_truth,
            )
            try:
                self._conversations.fail_turn(
                    session.session_id, request_id, code, self._now(), **values
                )
            except TypeError:
                values.pop("requirement_context", None)
                values.pop("research_snapshot", None)
                values.pop("engineering_plan", None)
                values.pop("engineering_plan_review", None)
                values.pop("architecture_design", None)
                values.pop("architecture_review", None)
                values.pop("design_truth", None)
                self._conversations.fail_turn(
                    session.session_id, request_id, code, self._now(), **values
                )
        except Exception as error:
            raise ChatUnavailable("conversation history is unavailable") from error

    def _complete_turn(self, session: ChatSession, request_id: str, answer: ChatAnswerView, *, route: str | None = None, trace_id: str | None = None, analysis_state: AnalysisState = AnalysisState.COMPLETED, analysis_reason_code: str | None = None, requirement_context: RequirementContext | None = None, research_snapshot: EngineeringResearchSnapshot | None = None, engineering_plan: EngineeringPlan | None = None, engineering_plan_review: PlanReviewDecision | None = None, architecture_design: object | None = None, architecture_review: object | None = None, design_truth: object | None = None) -> None:
        if self._conversations is None:
            return
        try:
            values = dict(
                route=route,
                trace_id=trace_id,
                terminal_event="ANSWER_COMPLETED",
                analysis_state=analysis_state.value,
                analysis_reason_code=analysis_reason_code,
                requirement_context=requirement_context,
                research_snapshot=research_snapshot,
                engineering_plan=engineering_plan,
                engineering_plan_review=engineering_plan_review,
                architecture_design=architecture_design,
                architecture_review=architecture_review,
                design_truth=design_truth,
            )
            try:
                self._conversations.complete_turn(
                    session.session_id, request_id, answer, answer.created_at, **values
                )
            except TypeError:
                values.pop("analysis_state", None)
                values.pop("analysis_reason_code", None)
                values.pop("requirement_context", None)
                values.pop("research_snapshot", None)
                values.pop("engineering_plan", None)
                values.pop("engineering_plan_review", None)
                values.pop("architecture_design", None)
                values.pop("architecture_review", None)
                values.pop("design_truth", None)
                self._conversations.complete_turn(
                    session.session_id, request_id, answer, answer.created_at, **values
                )
        except Exception as error:
            raise ChatUnavailable("conversation history is unavailable") from error

    @staticmethod
    def _source_type(reference: ChatContextReference) -> str:
        material = reference.display_label.upper()
        for source in ("HIL", "EDA", "FIRMWARE", "POWER", "MEMORY"):
            if source in material:
                return source
        return reference.reference_kind.value

    @staticmethod
    def _evidence_status(reference: ChatContextReference) -> str:
        if reference.reference_kind is ChatReferenceKind.MEMORY:
            return "APPROVED"
        if reference.reference_kind is ChatReferenceKind.WORKSPACE:
            return "PROJECTED"
        return "OBSERVED"

    @staticmethod
    def _context_references(
        session: ChatSession,
        result: ApplicationRunResult,
    ) -> tuple[ChatContextReference, ...]:
        if result.context is None:
            return ()
        evidence = (
            *result.context.memory_evidence,
            *result.context.graph_evidence,
            *result.context.knowledge_evidence,
            *result.context.multimodal_evidence,
        )
        return tuple(
            ChatContextReference(
                tenant_id=session.tenant_id,
                project_id=session.project_id,
                reference_id=item.evidence_id,
                reference_kind=ChatReferenceKind.EVIDENCE,
                source_fingerprint=item.source_fingerprint,
                display_label=item.subject,
            )
            for item in evidence
        )

    @staticmethod
    def _context_evidence_by_id(
        result: ApplicationRunResult,
    ) -> dict[str, object]:
        if result.context is None:
            return {}
        return {
            item.evidence_id: item
            for item in (
                *result.context.memory_evidence,
                *result.context.graph_evidence,
                *result.context.knowledge_evidence,
                *result.context.multimodal_evidence,
            )
        }

    def _now(self):
        return utc_datetime(self._clock.now(), field="chat clock")

    def _session(
        self, session_id: str, *, requester: str, tenant_id: str
    ) -> ChatSession:
        raw = self._sessions.get(session_id)
        if raw is None:
            raise ChatRejected("chat session was not found")
        session = _copy(raw, ChatSession, field="chat session")
        if session.requester != requester or session.tenant_id != tenant_id:
            raise ChatRejected("chat session identity mismatch")
        self._project(tenant_id, session.project_id, requester=requester)
        if (
            session.status is ChatSessionStatus.OPEN
            and session.expires_at <= self._now()
        ):
            expired = ChatSession(
                session_id=session.session_id,
                tenant_id=session.tenant_id,
                project_id=session.project_id,
                requester=session.requester,
                created_at=session.created_at,
                expires_at=session.expires_at,
                status=ChatSessionStatus.EXPIRED,
            )
            self._save_session(expired)
            raise ChatRejected("chat session is expired")
        if session.status is not ChatSessionStatus.OPEN:
            raise ChatRejected("chat session is not open")
        return session

    def _save_session(self, session: ChatSession) -> ChatSession:
        try:
            stored = _copy(
                self._sessions.save(session), ChatSession, field="chat session"
            )
        except ChatUnavailable:
            raise
        except Exception as error:
            raise ChatUnavailable("chat session store is unavailable") from error
        if stored != session:
            raise ChatUnavailable("chat session return was altered")
        return stored

    def _audit_event(
        self,
        session: ChatSession,
        event_type: ChatAuditEventType,
        request_id: str | None = None,
        answer_fingerprint: str | None = None,
    ) -> ChatAuditEvent:
        event = ChatAuditEvent(
            event_id=ChatAuditEvent.event_id_for(
                session.fingerprint, event_type, request_id
            ),
            event_type=event_type,
            session_fingerprint=session.fingerprint,
            project_id=session.project_id,
            request_id=request_id,
            answer_fingerprint=answer_fingerprint,
            timestamp=self._now(),
        )
        try:
            stored = _copy(
                self._audit.append(event), ChatAuditEvent, field="chat audit"
            )
        except ChatUnavailable:
            raise
        except Exception as error:
            raise ChatUnavailable("chat audit is unavailable") from error
        if stored != event:
            raise ChatUnavailable("chat audit return was altered")
        return stored

    def _event(
        self,
        session: ChatSession,
        event_type: ChatEventType,
        *,
        answer_chunk: str | None = None,
        answer_fingerprint: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        analysis_state: AnalysisState | None = None,
        reason_code: str | None = None,
    ) -> ChatEvent:
        try:
            prior = self._events.read_after(session.session_id, None) or ()
            sequence = prior[-1].sequence + 1 if prior else 1
            event = ChatEvent(
                event_id=f"chat-{session.session_id}-{sequence}",
                sequence=sequence,
                session_id=session.session_id,
                event_type=event_type,
                answer_chunk=answer_chunk,
                answer_fingerprint=answer_fingerprint,
                timestamp=self._now(),
                request_id=request_id,
                trace_id=trace_id,
                analysis_state=analysis_state,
                reason_code=reason_code,
                terminal_event=(event_type.value if event_type in {ChatEventType.ANSWER_COMPLETED, ChatEventType.ANSWER_FAILED} else None),
            )
            stored = _copy(self._events.append(event), ChatEvent, field="chat event")
        except ChatUnavailable:
            raise
        except Exception as error:
            raise ChatUnavailable("chat event store is unavailable") from error
        if stored != event:
            raise ChatUnavailable("chat event return was altered")
        return stored

    def _trace(
        self,
        request: DesktopChatRequest,
        session: ChatSession,
        stage: AnalysisTraceStage,
        *,
        route: str | None = None,
        agent_identity: str | None = None,
        service_identity: str | None = None,
        source_status: str | None = None,
        evidence_count: int | None = None,
        gate_result: str | None = None,
        provider_configured: bool | None = None,
        gateway_invocation_count: int = 0,
        reason_code: str | None = None,
        terminal_event: str | None = None,
        original_exception_type: str | None = None,
        original_exception_hash: str | None = None,
        exception_layer: str | None = None,
        exception_stage: str | None = None,
        traceback_fingerprint: str | None = None,
    ) -> None:
        if self._traces is None:
            return
        trace_id = request.application_question.trace_id
        event = ChatTraceEvent(
            event_id=f"analysis-trace-{trace_id}-{stage.value}",
            trace_id=trace_id,
            request_id=request.application_question.request_id,
            project_id=session.project_id,
            conversation_id=session.session_id,
            stage=stage,
            route=route,
            agent_identity=agent_identity,
            service_identity=service_identity,
            source_status=source_status,
            evidence_count=evidence_count,
            gate_result=gate_result,
            provider_configured=provider_configured,
            gateway_invocation_count=gateway_invocation_count,
            reason_code=reason_code,
            terminal_event=terminal_event,
            original_exception_type=original_exception_type,
            original_exception_hash=original_exception_hash,
            exception_layer=exception_layer,
            exception_stage=exception_stage,
            traceback_fingerprint=traceback_fingerprint,
            timestamp=self._now(),
        )
        if self._traces.append(event) != event:
            raise ChatUnavailable("chat trace return was altered")

    @staticmethod
    def _route_agent(route: str) -> str:
        return {
            "GREENFIELD": "SupervisorAgent",
            "GREENFIELD_DISCOVERY": "SupervisorAgent",
            "GREENFIELD_DESIGN": "SupervisorAgent",
            "BROWNFIELD": "FirmwareAgent",
            "BROWNFIELD_ANALYSIS": "FirmwareAgent",
            "FIRMWARE": "FirmwareAgent",
            "DEBUG": "DebugAgent",
            "DEBUG_ANALYSIS": "DebugAgent",
            "KNOWLEDGE": "KnowledgeAgent",
        }.get(route, "SupervisorAgent")

    @staticmethod
    def _analysis_state_for_result(result: ApplicationRunResult) -> AnalysisState:
        if result.route in {
            ApplicationRoute.GREENFIELD,
            ApplicationRoute.GREENFIELD_DISCOVERY,
            ApplicationRoute.GREENFIELD_DESIGN,
        }:
            lifecycle = None if result.requirement_context is None else result.requirement_context.lifecycle.value
            return {
                "DISCOVERY_STARTED": AnalysisState.DISCOVERY_STARTED,
                "REQUIREMENTS_PARTIAL": AnalysisState.REQUIREMENTS_PARTIAL,
                "REQUIREMENTS_REFINING": AnalysisState.REQUIREMENTS_REFINING,
                "REQUIREMENTS_READY": AnalysisState.REQUIREMENTS_READY,
                "DESIGN_READY": AnalysisState.DESIGN_READY,
            }.get(lifecycle, AnalysisState.REQUIREMENTS_PARTIAL)
        return AnalysisState.COMPLETED

    @staticmethod
    def _clarification_text() -> str:
        return (
            "我还不能确定你希望分析哪一部分。\n\n"
            "你可以告诉我：\n"
            "• 设计一个新的功能或模块\n"
            "• 分析已有源码\n"
            "• 排查编译/运行问题\n"
            "• 查找某个函数、外设或调用关系"
        )

    def _trace_application_result(self, request: DesktopChatRequest, session: ChatSession, result: ApplicationRunResult) -> None:
        route = result.route.value
        if route == ApplicationRoute.CLARIFICATION_REQUIRED.value:
            common = {
                "route": route,
                "agent_identity": "SupervisorAgent",
                "source_status": "NOT_APPLICABLE",
                "evidence_count": 0,
                "provider_configured": False,
                "gateway_invocation_count": 0,
            }
            self._trace(
                request, session, AnalysisTraceStage.ROUTE_SELECT,
                service_identity="SupervisorRuntime", gate_result="PASS",
                reason_code="CLARIFICATION_REQUIRED", **common,
            )
            self._trace(
                request, session, AnalysisTraceStage.MODEL_GATEWAY,
                service_identity="ModelGatewayService", gate_result="NOT_INVOKED",
                reason_code="CLARIFICATION_REQUIRED", **common,
            )
            return
        greenfield = route in {
            "GREENFIELD",
            "GREENFIELD_DISCOVERY",
            "GREENFIELD_DESIGN",
        }
        agent = {
            "GREENFIELD": "SupervisorAgent",
            "GREENFIELD_DISCOVERY": "SupervisorAgent",
            "GREENFIELD_DESIGN": "SupervisorAgent",
            "FIRMWARE": "FirmwareAgent",
            "BROWNFIELD_ANALYSIS": "FirmwareAgent",
            "DEBUG": "DebugAgent",
            "DEBUG_ANALYSIS": "DebugAgent",
            "KNOWLEDGE": "KnowledgeAgent",
        }.get(route, "SupervisorAgent")
        context_evidence = () if result.context is None else (
            *result.context.memory_evidence,
            *result.context.graph_evidence,
            *result.context.knowledge_evidence,
            *result.context.multimodal_evidence,
        )
        code = None if result.error_code is None else result.error_code.value
        trace_code = (
            "CONTEXT_BUILD_UNAVAILABLE"
            if code == "CONTEXT_UNAVAILABLE"
            else code
        )
        configured = code not in {
            "PROVIDER_NOT_CONFIGURED",
            "PROVIDER_CONNECTION_UNVERIFIED",
        }
        evidence_count = 0 if greenfield else len(context_evidence)
        source_status = (
            "EMPTY_PROJECT"
            if greenfield
            else "AVAILABLE"
            if result.context is not None
            else "UNAVAILABLE"
        )
        common = {
            "route": route,
            "agent_identity": agent,
            "source_status": source_status,
            "evidence_count": evidence_count,
        }
        context_ready = result.context is not None or (
            greenfield and result.requirement_context is not None
        )
        self._trace(request, session, AnalysisTraceStage.ROUTE_SELECT, service_identity="SupervisorRuntime", gate_result="PASS", reason_code="GREENFIELD" if greenfield else "ROUTE_SELECTED", **common)
        self._trace(request, session, AnalysisTraceStage.CONTEXT_BUILD, service_identity="EngineeringContextService", gate_result="PASS" if context_ready else "FAIL", reason_code="GREENFIELD_CONTEXT_READY" if greenfield and context_ready else "CONTEXT_READY" if context_ready else "CONTEXT_BUILD_UNAVAILABLE", **common)
        self._trace(request, session, AnalysisTraceStage.SOURCE_SNAPSHOT, service_identity="ProjectEngineeringEvidence", gate_result="EMPTY_PROJECT" if greenfield else "PASS", reason_code="EMPTY_PROJECT" if greenfield else "SOURCE_SNAPSHOT_READY", **common)
        self._trace(request, session, AnalysisTraceStage.FIRMWARE_PARSE, service_identity="FirmwareParser", gate_result="NOT_APPLICABLE_FOR_GREENFIELD" if greenfield else "PASS", reason_code="GREENFIELD" if greenfield else "FIRMWARE_PARSE_COMPLETED", **common)
        self._trace(request, session, AnalysisTraceStage.EVIDENCE_BUILD, service_identity="EngineeringContextService", gate_result="GREENFIELD_CONTEXT" if greenfield else "PASS", reason_code="GREENFIELD_CONTEXT_PRESENT" if greenfield else "EVIDENCE_READY", **common)
        self._trace(request, session, AnalysisTraceStage.EVIDENCE_GATE, service_identity="EvidenceGate", gate_result="NOT_APPLICABLE_FOR_GREENFIELD" if greenfield else "PASS" if result.context is not None else "FAIL", reason_code="GREENFIELD" if greenfield else code or "EVIDENCE_READY", **common)
        self._trace(request, session, AnalysisTraceStage.PROVIDER_RESOLVE, service_identity="ModelProviderResolver", gate_result="UNAVAILABLE" if code == "PROVIDER_NOT_CONFIGURED" else "AVAILABLE" if configured else "UNAVAILABLE", provider_configured=configured, reason_code="GREENFIELD_CONTEXT_READY_PROVIDER_UNAVAILABLE" if greenfield and code == "PROVIDER_NOT_CONFIGURED" else trace_code or "PROVIDER_READY", **common)
        gateway_invocation_count = result.gateway_invocation_count
        exception_diagnostics = {
            field: getattr(result, field, None)
            for field in (
                "original_exception_type",
                "original_exception_hash",
                "exception_layer",
                "exception_stage",
                "traceback_fingerprint",
            )
        }
        self._trace(
            request,
            session,
            AnalysisTraceStage.MODEL_GATEWAY,
            service_identity="ModelGatewayService",
            gate_result=(
                "PASS"
                if result.answer is not None
                else "RESPONSE_REJECTED"
                if result.provider_response_present
                else "UNAVAILABLE"
                if gateway_invocation_count
                else "NOT_INVOKED"
            ),
            provider_configured=configured,
            gateway_invocation_count=gateway_invocation_count,
            reason_code=trace_code or "ANSWER_COMPLETED",
            **exception_diagnostics,
            **common,
        )

    def _project(
        self, tenant_id: str, project_id: str, *, requester: str
    ) -> ChatProjectReference:
        try:
            project = _copy(
                self._projects.project_for_requester(tenant_id, project_id, requester),
                ChatProjectReference,
                field="chat project projection",
            )
        except (ChatRejected, ChatUnavailable):
            raise
        except Exception as error:
            raise ChatUnavailable("chat project projection is unavailable") from error
        if project.tenant_id != tenant_id or project.project_id != project_id:
            raise ChatRejected("chat project projection mismatch")
        return project

    def open_session(self, request: ChatSessionOpenRequest) -> ChatSession:
        checked = _copy(request, ChatSessionOpenRequest, field="chat session request")
        now = self._now()
        if checked.expires_at <= now:
            raise ChatRejected("chat session expiry is invalid")
        self._project(checked.tenant_id, checked.project_id, requester=checked.requester)
        try:
            existing_raw = self._sessions.get(checked.session_id)
        except Exception as error:
            raise ChatUnavailable("chat session store is unavailable") from error
        if existing_raw is not None:
            existing = _copy(existing_raw, ChatSession, field="chat session")
            if (
                existing.tenant_id != checked.tenant_id
                or existing.project_id != checked.project_id
                or existing.requester != checked.requester
                or existing.expires_at != checked.expires_at
                or existing.status is not ChatSessionStatus.OPEN
            ):
                raise ChatConflict("chat session identity conflicts")
            self._audit_event(existing, ChatAuditEventType.CHAT_SESSION_OPENED)
            return existing
        session = ChatSession(
            session_id=checked.session_id,
            tenant_id=checked.tenant_id,
            project_id=checked.project_id,
            requester=checked.requester,
            created_at=now,
            expires_at=checked.expires_at,
            status=ChatSessionStatus.OPEN,
        )
        stored = self._save_session(session)
        self._audit_event(stored, ChatAuditEventType.CHAT_SESSION_OPENED)
        return stored

    def close_session(
        self, session_id: str, *, requester: str, tenant_id: str
    ) -> ChatSession:
        session = self._session(session_id, requester=requester, tenant_id=tenant_id)
        closed = ChatSession(
            session_id=session.session_id,
            tenant_id=session.tenant_id,
            project_id=session.project_id,
            requester=session.requester,
            created_at=session.created_at,
            expires_at=session.expires_at,
            status=ChatSessionStatus.CLOSED,
        )
        stored = self._save_session(closed)
        self._audit_event(stored, ChatAuditEventType.CHAT_CLOSED)
        return stored

    def _references(self, session: ChatSession) -> tuple[ChatContextReference, ...]:
        try:
            values = self._projects.references(session.tenant_id, session.project_id)
            references = tuple(
                _copy(value, ChatContextReference, field="chat context projection")
                for value in values
            )
        except ChatUnavailable:
            raise
        except Exception as error:
            raise ChatUnavailable("chat context projection is unavailable") from error
        if any(
            value.tenant_id != session.tenant_id
            or value.project_id != session.project_id
            for value in references
        ):
            raise ChatRejected("chat context projection mismatch")
        try:
            attachment_references = tuple(
                self._attachment_reference(session, item)
                for item in self._attachments.list(session.session_id)
            )
        except ChatUnavailable:
            raise
        except Exception as error:
            raise ChatUnavailable("chat attachment projection is unavailable") from error
        return references + attachment_references

    @staticmethod
    def _attachment_reference(
        session: ChatSession, binding: AttachmentBinding
    ) -> ChatContextReference:
        checked = AttachmentBinding.model_validate(
            copy.deepcopy(binding.model_dump(mode="python"))
        )
        if checked.session_id != session.session_id:
            raise ChatUnavailable("chat attachment return was altered")
        material = json.dumps(
            checked.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return ChatContextReference(
            tenant_id=session.tenant_id,
            project_id=session.project_id,
            reference_id=checked.input.reference_id,
            reference_kind=ChatReferenceKind.WORKSPACE,
            source_fingerprint=f"sha256:{hashlib.sha256(material).hexdigest()}",
            display_label=checked.basename,
        )

    def bind_attachment(
        self,
        binding: AttachmentBinding,
        *,
        requester: str,
        tenant_id: str,
    ) -> ChatContextReference:
        checked = AttachmentBinding.model_validate(
            copy.deepcopy(binding.model_dump(mode="python"))
        )
        session = self._session(
            checked.session_id, requester=requester, tenant_id=tenant_id
        )
        try:
            self._attachments.bind(checked)
        except AttachmentBindingConflict as error:
            raise ChatConflict("chat attachment reference conflicts") from error
        except Exception as error:
            raise ChatUnavailable("chat attachment binding is unavailable") from error
        return self._attachment_reference(session, checked)

    def _prepare_submit(
        self,
        request: DesktopChatRequest,
        *,
        requester: str,
        tenant_id: str,
    ) -> tuple[DesktopChatRequest, ChatSession, ApplicationQuestionRequest]:
        checked = _copy(request, DesktopChatRequest, field="desktop chat request")
        session = self._session(
            checked.session_id, requester=requester, tenant_id=tenant_id
        )
        if (
            checked.project_reference != self._project(tenant_id, session.project_id, requester=requester)
            or checked.application_question.requester != requester
            or checked.application_question.project_id != session.project_id
        ):
            raise ChatRejected("desktop chat request binding mismatch")
        available = {item.fingerprint for item in self._references(session)}
        if any(
            item.fingerprint not in available for item in checked.context_references
        ):
            raise ChatRejected("chat context reference is unavailable")
        application_request = ApplicationQuestionRequest.model_validate(
            {
                **copy.deepcopy(checked.application_question.model_dump(mode="python")),
                "external_search": checked.external_search,
                "tenant_id": session.tenant_id,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        return checked, session, application_request

    def _claim_request(
        self,
        key: tuple[str, str, str],
        fingerprint: str,
    ) -> tuple[_RequestExecution, bool, ChatAnswerView | None]:
        while True:
            with self._idempotency_lock:
                existing = self._idempotency.get(key)
                if existing is None:
                    execution = _RequestExecution(fingerprint)
                    self._idempotency[key] = execution
                    return execution, True, None
                if existing.fingerprint != fingerprint:
                    raise ChatConflict("chat request fingerprint conflicts")
                if existing.result is not None:
                    return (
                        existing,
                        False,
                        _copy(existing.result, ChatAnswerView, field="cached chat answer"),
                    )
            existing.event.wait()

    def _complete_request(
        self,
        key: tuple[str, str, str],
        execution: _RequestExecution,
        answer: ChatAnswerView,
    ) -> None:
        cached = _copy(answer, ChatAnswerView, field="chat answer")
        with self._idempotency_lock:
            if self._idempotency.get(key) is not execution:
                raise ChatUnavailable("chat request idempotency state is invalid")
            execution.result = cached
            execution.event.set()

    def _fail_request(
        self, key: tuple[str, str, str], execution: _RequestExecution
    ) -> None:
        with self._idempotency_lock:
            if self._idempotency.get(key) is execution:
                del self._idempotency[key]
            execution.event.set()

    def submit(
        self,
        request: DesktopChatRequest,
        *,
        requester: str,
        tenant_id: str,
    ) -> ChatAnswerView:
        checked, session, application_request = self._prepare_submit(
            request, requester=requester, tenant_id=tenant_id
        )
        key = (session.tenant_id, session.session_id, application_request.request_id)
        fingerprint = _request_idempotency_fingerprint(session, checked)
        execution, owner, replay = self._claim_request(key, fingerprint)
        if not owner:
            if replay is None:
                raise ChatUnavailable("cached chat answer is unavailable")
            return replay
        try:
            answer = self._submit_prepared(
                checked,
                session,
                application_request,
                requester=requester,
            )
            self._complete_request(key, execution, answer)
            return answer
        except BaseException:
            self._fail_request(key, execution)
            raise

    def _submit_prepared(
        self,
        checked: DesktopChatRequest,
        session: ChatSession,
        application_request: ApplicationQuestionRequest,
        *,
        requester: str,
    ) -> ChatAnswerView:
        routing_context: ApplicationRoutingContext | None = None
        routing_context_reader = getattr(self._conversations, "routing_context", None)
        if callable(routing_context_reader):
            try:
                raw_context = routing_context_reader(session.session_id)
                if raw_context is not None:
                    routing_context = ApplicationRoutingContext.model_validate(
                        copy.deepcopy(raw_context.model_dump(mode="python"))
                    )
            except Exception as error:
                raise ChatApplicationFailed("COPILOT_CHAT_ANALYSIS_RUNTIME_FAILED") from error
        route_decision: ApplicationRouteDecision | None = None
        classify = getattr(self._application, "classify", None)
        if callable(classify):
            try:
                raw_decision = (
                    classify(application_request, routing_context=routing_context)
                    if _accepts_keyword(classify, "routing_context")
                    else classify(application_request)
                )
                if isinstance(raw_decision, ApplicationRouteDecision):
                    route_decision = ApplicationRouteDecision.model_validate(
                        copy.deepcopy(raw_decision.model_dump(mode="python"))
                    )
            except Exception as error:
                raise ChatApplicationFailed("COPILOT_CHAT_ANALYSIS_RUNTIME_FAILED") from error
        clarification_route = (
            route_decision is not None
            and route_decision.route is ApplicationRoute.CLARIFICATION_REQUIRED
        )
        self._trace(checked, session, AnalysisTraceStage.UI_SUBMIT, service_identity="DesktopChatUI", gate_result="PASS", reason_code="REQUEST_ACCEPTED")
        self._trace(checked, session, AnalysisTraceStage.CHAT_REQUEST_RECEIVED, service_identity="DesktopChatRuntime", gate_result="PASS", reason_code="REQUEST_RECEIVED")
        self._trace(checked, session, AnalysisTraceStage.PROJECT_RESOLVE, service_identity="ProjectCatalog", gate_result="PASS", reason_code="PROJECT_RESOLVED")
        self._trace(checked, session, AnalysisTraceStage.CONVERSATION_RESOLVE, service_identity="ConversationStore", gate_result="PASS", reason_code="CONVERSATION_RESOLVED")
        self._audit_event(
            session,
            ChatAuditEventType.CHAT_REQUESTED,
            checked.application_question.request_id,
        )
        self._event(
            session,
            ChatEventType.CHAT_STARTED,
            request_id=checked.application_question.request_id,
            trace_id=checked.application_question.trace_id,
            analysis_state=(
                AnalysisState.DISCOVERY_STARTED
                if route_decision is not None
                and route_decision.route
                in {
                    ApplicationRoute.GREENFIELD,
                    ApplicationRoute.GREENFIELD_DISCOVERY,
                    ApplicationRoute.GREENFIELD_DESIGN,
                }
                else AnalysisState.REQUESTED
            ),
            reason_code=(
                "DISCOVERY_STARTED"
                if route_decision is not None
                and route_decision.route
                in {
                    ApplicationRoute.GREENFIELD,
                    ApplicationRoute.GREENFIELD_DISCOVERY,
                    ApplicationRoute.GREENFIELD_DESIGN,
                }
                else "REQUESTED"
            ),
        )
        self._begin_turn(session, checked)
        # Ordinary local-first requirement conversations are covered by the
        # project context policy and must not interrupt the user with a
        # per-request transmission prompt.  Keep the existing approval port
        # for an explicitly requested external search (the sensitive-scope
        # boundary); clarification never dispatches externally.
        requires_transmission_approval = checked.external_search and not clarification_route
        if self._transmission_approval is not None and requires_transmission_approval:
            try:
                self._transmission_approval.consume(
                    tenant_id=session.tenant_id,
                    requester=requester,
                    session_id=session.session_id,
                    project_id=session.project_id,
                    request_id=checked.application_question.request_id,
                    reference_fingerprints=tuple(
                        item.fingerprint for item in checked.context_references
                    ),
                )
                self._trace(
                    checked,
                    session,
                    AnalysisTraceStage.TRANSMISSION_APPROVAL,
                    service_identity="TransmissionApproval",
                    gate_result="PASS",
                    reason_code="TRANSMISSION_APPROVED",
                )
            except Exception as error:
                code = getattr(error, "code", "EXTERNAL_TRANSMISSION_APPROVAL_REQUIRED")
                if not isinstance(code, str) or not code:
                    code = "EXTERNAL_TRANSMISSION_APPROVAL_REQUIRED"
                self._trace(
                    checked,
                    session,
                    AnalysisTraceStage.TRANSMISSION_APPROVAL,
                    service_identity="TransmissionApproval",
                    gate_result="FAIL",
                    reason_code=code,
                    terminal_event="ANSWER_FAILED",
                )
                self._audit_event(
                    session,
                    ChatAuditEventType.CHAT_FAILED,
                    checked.application_question.request_id,
                )
                self._fail_turn(
                    session,
                    checked.application_question.request_id,
                    code,
                    context_references=checked.context_references,
                    analysis_state=AnalysisState.FAILED,
                    analysis_reason_code=code,
                    route=None if route_decision is None else route_decision.route.value,
                    trace_id=checked.application_question.trace_id,
                )
                self._event(
                    session,
                    ChatEventType.ANSWER_FAILED,
                    request_id=checked.application_question.request_id,
                    trace_id=checked.application_question.trace_id,
                    analysis_state=AnalysisState.FAILED,
                    reason_code=code,
                )
                raise ChatApplicationFailed(code) from error
        self._trace(checked, session, AnalysisTraceStage.APPLICATION_RUNTIME_ENTER, service_identity="ApplicationRuntime", gate_result="PASS", reason_code="RUNTIME_ENTER")
        try:
            if route_decision is None:
                answer = self._application.answer
                raw_result = (
                    answer(application_request, routing_context=routing_context)
                    if _accepts_keyword(answer, "routing_context")
                    else answer(application_request)
                )
            else:
                answer = self._application.answer
                kwargs = {}
                if _accepts_keyword(answer, "route_decision"):
                    kwargs["route_decision"] = route_decision
                if _accepts_keyword(answer, "routing_context"):
                    kwargs["routing_context"] = routing_context
                raw_result = answer(application_request, **kwargs)
            result = _copy(raw_result, ApplicationRunResult, field="application result")
        except Exception as error:
            attach_exception_diagnostics(
                error,
                error,
                exception_layer="DesktopCopilotChatRuntime",
                exception_stage="APPLICATION_RUNTIME",
            )
            diagnostics = exception_diagnostics(error)
            self._trace(
                checked,
                session,
                AnalysisTraceStage.MODEL_GATEWAY,
                service_identity="ModelGatewayService",
                gate_result="NOT_INVOKED",
                gateway_invocation_count=0,
                reason_code="APPLICATION_UNAVAILABLE",
                **diagnostics,
            )
            self._audit_event(
                session,
                ChatAuditEventType.CHAT_FAILED,
                checked.application_question.request_id,
            )
            self._event(session, ChatEventType.ANSWER_FAILED, request_id=checked.application_question.request_id, trace_id=checked.application_question.trace_id, analysis_state=AnalysisState.FAILED, reason_code="COPILOT_CHAT_ANALYSIS_RUNTIME_FAILED")
            self._fail_turn(session, checked.application_question.request_id, "COPILOT_CHAT_ANALYSIS_RUNTIME_FAILED", route=(None if route_decision is None else route_decision.route.value), trace_id=checked.application_question.trace_id, analysis_reason_code="ANALYSIS_RUNTIME_FAILED")
            raise ChatApplicationFailed() from error
        self._trace_application_result(checked, session, result)
        legacy_default_tenant_request = (
            checked.application_question.tenant_id == "default-tenant"
        )
        if (
            result.project_id != session.project_id
            or (
                result.question_fingerprint != application_request.fingerprint
                and not (
                    legacy_default_tenant_request
                    and result.question_fingerprint
                    == checked.application_question.fingerprint
                )
            )
            or result.status is not ApplicationRunStatus.COMPLETED
            or result.answer is None
        ):
            clarification = (
                result.route is ApplicationRoute.CLARIFICATION_REQUIRED
                or (
                    result.error_code is not None
                    and result.error_code.value == "CLARIFICATION_REQUIRED"
                )
            )
            if clarification:
                reason = "CLARIFICATION_REQUIRED"
                self._audit_event(
                    session,
                    ChatAuditEventType.CHAT_FAILED,
                    checked.application_question.request_id,
                )
                self._fail_turn(
                    session,
                    checked.application_question.request_id,
                    None,
                    context_references=(),
                    analysis_state=AnalysisState.WAITING_FOR_CLARIFICATION,
                    analysis_reason_code=reason,
                    route=result.route.value,
                    trace_id=checked.application_question.trace_id,
                    terminal_event="ANSWER_FAILED",
                    answer_text=self._clarification_text(),
                )
                self._trace(
                    checked,
                    session,
                    AnalysisTraceStage.ANSWER_PERSIST,
                    route=result.route.value,
                    agent_identity="SupervisorAgent",
                    service_identity="ConversationStore",
                    source_status="NOT_APPLICABLE",
                    evidence_count=0,
                    gate_result="NOT_APPLICABLE",
                    provider_configured=False,
                    gateway_invocation_count=0,
                    reason_code=reason,
                )
                self._event(
                    session,
                    ChatEventType.ANSWER_FAILED,
                    request_id=checked.application_question.request_id,
                    trace_id=checked.application_question.trace_id,
                    analysis_state=AnalysisState.WAITING_FOR_CLARIFICATION,
                    reason_code=reason,
                )
                self._trace(
                    checked,
                    session,
                    AnalysisTraceStage.SSE_TERMINAL,
                    route=result.route.value,
                    agent_identity="SupervisorAgent",
                    service_identity="SSE",
                    source_status="NOT_APPLICABLE",
                    evidence_count=0,
                    gate_result="TERMINAL",
                    provider_configured=False,
                    gateway_invocation_count=0,
                    reason_code=reason,
                    terminal_event="ANSWER_FAILED",
                )
                self._trace(
                    checked,
                    session,
                    AnalysisTraceStage.UI_PROJECTION,
                    route=result.route.value,
                    agent_identity="SupervisorAgent",
                    service_identity="DesktopProjection",
                    source_status="NOT_APPLICABLE",
                    evidence_count=0,
                    gate_result="PASS",
                    provider_configured=False,
                    gateway_invocation_count=0,
                    reason_code=reason,
                    terminal_event="ANSWER_FAILED",
                )
                raise ChatApplicationFailed(reason)
            self._audit_event(
                session,
                ChatAuditEventType.CHAT_FAILED,
                checked.application_question.request_id,
            )
            failure_state = (
                AnalysisState.PROVIDER_UNAVAILABLE
                if result.error_code is not None and result.error_code.value in {
                    "PROVIDER_NOT_CONFIGURED",
                    "PROVIDER_UNAVAILABLE",
                    "MODEL_UNAVAILABLE",
                    "PROVIDER_AUTH_FAILED",
                    "PROVIDER_NETWORK_UNAVAILABLE",
                    "PROVIDER_MODEL_UNAVAILABLE",
                    "PROVIDER_CONFIG_INVALID",
                    "PROVIDER_CONNECTION_UNVERIFIED",
                }
                else AnalysisState.INSUFFICIENT_EVIDENCE
                if result.error_code is not None and result.error_code.value == "INSUFFICIENT_EVIDENCE"
                else AnalysisState.REQUIREMENTS_PARTIAL
                if result.route in {
                    ApplicationRoute.GREENFIELD,
                    ApplicationRoute.GREENFIELD_DISCOVERY,
                    ApplicationRoute.GREENFIELD_DESIGN,
                }
                and result.error_code is not None
                and result.error_code.value == "CONTEXT_UNAVAILABLE"
                else AnalysisState.FAILED
            )
            error_code = _chat_failure_code(result.error_code)
            self._fail_turn(
                session,
                checked.application_question.request_id,
                error_code,
                context_references=self._context_references(session, result),
                analysis_state=failure_state,
                    analysis_reason_code=("GREENFIELD_CONTEXT_READY_PROVIDER_UNAVAILABLE" if failure_state is AnalysisState.PROVIDER_UNAVAILABLE and result.route in {ApplicationRoute.GREENFIELD, ApplicationRoute.GREENFIELD_DISCOVERY, ApplicationRoute.GREENFIELD_DESIGN} else error_code.removeprefix("COPILOT_CHAT_")),
                route=result.route.value,
                trace_id=checked.application_question.trace_id,
                requirement_context=result.requirement_context,
                research_snapshot=result.research_snapshot,
                engineering_plan=result.engineering_plan,
                engineering_plan_review=result.engineering_plan_review,
                architecture_design=result.architecture_design,
                architecture_review=result.architecture_review,
                design_truth=result.design_truth,
            )
            failure_context_references = self._context_references(session, result)
            failure_source_status = (
                "EMPTY_PROJECT"
                if result.route in {ApplicationRoute.GREENFIELD, ApplicationRoute.GREENFIELD_DISCOVERY, ApplicationRoute.GREENFIELD_DESIGN}
                else "AVAILABLE"
                if result.context is not None
                else "UNAVAILABLE"
            )
            self._trace(checked, session, AnalysisTraceStage.ANSWER_PERSIST, route=result.route.value, agent_identity=self._route_agent(result.route.value), service_identity="ConversationStore", source_status=failure_source_status, evidence_count=len(failure_context_references), gate_result="PASS", provider_configured=result.error_code.value != "PROVIDER_NOT_CONFIGURED" if result.error_code is not None else None, reason_code=error_code)
            self._event(session, ChatEventType.ANSWER_FAILED, request_id=checked.application_question.request_id, trace_id=checked.application_question.trace_id, analysis_state=failure_state, reason_code=error_code)
            self._trace(checked, session, AnalysisTraceStage.SSE_TERMINAL, route=result.route.value, agent_identity=self._route_agent(result.route.value), service_identity="SSE", source_status=failure_source_status, evidence_count=len(failure_context_references), gate_result="TERMINAL", provider_configured=result.error_code.value != "PROVIDER_NOT_CONFIGURED" if result.error_code is not None else None, reason_code=error_code, terminal_event="ANSWER_FAILED")
            self._trace(checked, session, AnalysisTraceStage.UI_PROJECTION, route=result.route.value, agent_identity=self._route_agent(result.route.value), service_identity="DesktopProjection", source_status=failure_source_status, evidence_count=len(failure_context_references), gate_result="PASS", provider_configured=result.error_code.value != "PROVIDER_NOT_CONFIGURED" if result.error_code is not None else None, reason_code=error_code, terminal_event="ANSWER_FAILED")
            code = _chat_failure_code(result.error_code)
            _logger.info(
                "desktop_runtime_failure_trace",
                extra={
                    "event_name": "desktop_runtime_failure_trace",
                    "request_id": checked.application_question.request_id,
                    "trace_id": checked.application_question.trace_id,
                    "project_id": checked.application_question.project_id,
                    "conversation_id": checked.session_id,
                    "route": result.route.value,
                    "agent": (
                        "SupervisorAgent"
                        if result.route.value == "GREENFIELD"
                        else {
                            "FIRMWARE": "FirmwareAgent",
                            "DEBUG": "DebugAgent",
                            "KNOWLEDGE": "KnowledgeAgent",
                        }.get(result.route.value, "SupervisorAgent")
                    ),
                    "service_identities": (
                        "EngineeringContextService",
                        "EvidenceGate",
                        "ModelGatewayService",
                    ),
                    "evidence_count": len(self._context_references(session, result)),
                    "gate_result": (
                        "PASS" if result.context is not None else "FAIL_CLOSED"
                    ),
                    "model_gateway_invocation_count": (
                        result.gateway_invocation_count
                    ),
                    "provider_status": (
                        "RESPONSE_RECEIVED"
                        if result.provider_response_present
                        else
                        "UNAVAILABLE"
                        if result.gateway_invocation_count
                        else "NOT_INVOKED"
                    ),
                    "provider_response_present": result.provider_response_present,
                    "answer_validation_stage": result.answer_validation_stage,
                    "proposed_requirements_count": result.proposed_requirements_count,
                    "open_questions_count": result.open_questions_count,
                    "answer_text_present": result.answer_text_present,
                    "original_exception_type": result.original_exception_type,
                    "original_exception_hash": result.original_exception_hash,
                    "exception_layer": result.exception_layer,
                    "exception_stage": result.exception_stage,
                    "traceback_fingerprint": result.traceback_fingerprint,
                    "reason_code": code,
                    "terminal_event": "ANSWER_FAILED",
                },
            )
            raise ChatApplicationFailed(code)
        answer_time = self._now()
        request_evidence_references = tuple(
            item for item in checked.context_references
            if item.reference_kind is not ChatReferenceKind.PROPOSAL
        )
        context_references = self._context_references(session, result)
        evidence_references = tuple(
            {
                item.fingerprint: item
                for item in (*request_evidence_references, *context_references)
            }.values()
        )
        context_evidence = self._context_evidence_by_id(result)
        answer = ChatAnswerView(
            answer_id=result.answer.answer_id,
            status=ChatAnswerStatus.COMPLETED,
            answer_text=result.answer.content,
            response_type=resolve_response_type(
                checked.application_question.question,
                report_confirmation_allowed=(
                    result.requirement_context is not None
                    and result.requirement_context.lifecycle
                    in {
                        RequirementLifecycle.REQUIREMENTS_READY,
                        RequirementLifecycle.DESIGN_READY,
                    }
                ),
            ),
            evidence_references=evidence_references,
            evidence_details=tuple(
                ChatEvidenceView(
                    source_type=(
                        "EXTERNAL_OFFICIAL"
                        if str(getattr(context_evidence.get(item.reference_id), "source_reference", "")).startswith("https://")
                        else self._source_type(item)
                    ),
                    label=item.display_label,
                    reference_id=item.reference_id,
                    status=(
                        str(getattr(context_evidence.get(item.reference_id), "verification_status", self._evidence_status(item)))
                    ),
                    summary=getattr(context_evidence.get(item.reference_id), "summary", None),
                    source_uri=(
                        getattr(context_evidence.get(item.reference_id), "source_reference", None)
                        if str(getattr(context_evidence.get(item.reference_id), "source_reference", "")).startswith("https://")
                        else None
                    ),
                    source_fingerprint=item.source_fingerprint,
                    observed_at=answer_time,
                )
                for item in evidence_references
            ),
            recommendations=tuple(
                ChatRecommendationView(
                    review=item.display_label,
                    reason=None,
                    reference_id=item.reference_id,
                    source_fingerprint=item.source_fingerprint,
                )
                for item in checked.context_references
                if item.reference_kind is ChatReferenceKind.PROPOSAL
            ),
            created_at=answer_time,
        )
        self._complete_turn(
            session,
            checked.application_question.request_id,
            answer,
            route=result.route.value,
            trace_id=checked.application_question.trace_id,
            analysis_state=self._analysis_state_for_result(result),
            analysis_reason_code="ANSWER_COMPLETED",
            requirement_context=result.requirement_context,
            research_snapshot=result.research_snapshot,
            engineering_plan=result.engineering_plan,
            engineering_plan_review=result.engineering_plan_review,
            architecture_design=result.architecture_design,
            architecture_review=result.architecture_review,
            design_truth=result.design_truth,
        )
        context_evidence = () if result.context is None else (
            *result.context.memory_evidence,
            *result.context.graph_evidence,
            *result.context.knowledge_evidence,
            *result.context.multimodal_evidence,
        )
        self._trace(checked, session, AnalysisTraceStage.ANSWER_PERSIST, route=result.route.value, agent_identity=self._route_agent(result.route.value), service_identity="ConversationStore", source_status="AVAILABLE", evidence_count=len(context_evidence), gate_result="PASS", provider_configured=True, gateway_invocation_count=result.gateway_invocation_count, reason_code="ANSWER_COMPLETED")
        self._audit_event(
            session,
            ChatAuditEventType.CHAT_COMPLETED,
            checked.application_question.request_id,
            answer.fingerprint,
        )
        for offset in range(0, len(answer.answer_text), _CHUNK_SIZE):
                self._event(
                session,
                ChatEventType.ANSWER_CHUNK,
                answer_chunk=answer.answer_text[offset : offset + _CHUNK_SIZE],
                request_id=checked.application_question.request_id,
                trace_id=checked.application_question.trace_id,
                analysis_state=self._analysis_state_for_result(result),
            )
        self._event(
            session,
            ChatEventType.ANSWER_COMPLETED,
            answer_fingerprint=answer.fingerprint,
            request_id=checked.application_question.request_id,
            trace_id=checked.application_question.trace_id,
            analysis_state=self._analysis_state_for_result(result),
            reason_code="ANSWER_COMPLETED",
        )
        self._trace(checked, session, AnalysisTraceStage.SSE_TERMINAL, route=result.route.value, agent_identity=self._route_agent(result.route.value), service_identity="SSE", source_status="AVAILABLE", evidence_count=len(context_evidence), gate_result="TERMINAL", provider_configured=True, gateway_invocation_count=result.gateway_invocation_count, reason_code="ANSWER_COMPLETED", terminal_event="ANSWER_COMPLETED")
        self._trace(checked, session, AnalysisTraceStage.UI_PROJECTION, route=result.route.value, agent_identity=self._route_agent(result.route.value), service_identity="DesktopProjection", source_status="AVAILABLE", evidence_count=len(context_evidence), gate_result="PASS", provider_configured=True, gateway_invocation_count=result.gateway_invocation_count, reason_code="ANSWER_COMPLETED", terminal_event="ANSWER_COMPLETED")
        _logger.info(
            "desktop_runtime_trace",
            extra={
                "event_name": "desktop_runtime_trace",
                "request_id": checked.application_question.request_id,
                "trace_id": checked.application_question.trace_id,
                "project_id": checked.application_question.project_id,
                "conversation_id": checked.session_id,
                "route": result.route.value,
                "agent": {
                    "GREENFIELD": "SupervisorAgent",
                    "FIRMWARE": "FirmwareAgent",
                    "DEBUG": "DebugAgent",
                    "KNOWLEDGE": "KnowledgeAgent",
                }.get(result.route.value, "SupervisorAgent"),
                "service_identities": (
                    "EngineeringContextService",
                    "EvidenceGate",
                    "ModelGatewayService",
                ),
                "evidence_count": len(context_evidence),
                "gate_result": "PASS",
                "finding_count": sum(
                    1
                    for item in context_evidence
                    if getattr(getattr(item, "evidence_status", None), "value", None) == "CANDIDATE"
                ),
                "provider": result.answer.model_info.provider,
                "model": result.answer.model_info.model_name,
                "provider_status": "AVAILABLE",
                "model_gateway_invocation_count": result.gateway_invocation_count,
                "provider_response_present": result.provider_response_present,
                "answer_validation_stage": result.answer_validation_stage,
                "proposed_requirements_count": result.proposed_requirements_count,
                "open_questions_count": result.open_questions_count,
                "answer_text_present": result.answer_text_present,
                "reason_code": "ANSWER_COMPLETED",
                "terminal_event": "ANSWER_COMPLETED",
                "request_completed": True,
            },
        )
        return answer

    def classify(
        self,
        request: DesktopChatRequest,
        *,
        requester: str,
        tenant_id: str,
    ) -> ApplicationRouteDecision:
        """Perform route preflight without persistence, approval, context, or provider work."""
        checked = _copy(request, DesktopChatRequest, field="desktop chat request")
        session = self._session(checked.session_id, requester=requester, tenant_id=tenant_id)
        if (
            checked.project_reference != self._project(tenant_id, session.project_id, requester=requester)
            or checked.application_question.requester != requester
            or checked.application_question.project_id != session.project_id
        ):
            raise ChatRejected("desktop chat request binding mismatch")
        application_request = ApplicationQuestionRequest.model_validate(
            {
                **copy.deepcopy(checked.application_question.model_dump(mode="python")),
                "external_search": checked.external_search,
                "tenant_id": session.tenant_id,
                "fingerprint": "sha256:" + "0" * 64,
            }
        )
        classify = getattr(self._application, "classify", None)
        if not callable(classify):
            raise ChatUnavailable("route preflight is unavailable")
        routing_context: ApplicationRoutingContext | None = None
        routing_context_reader = getattr(self._conversations, "routing_context", None)
        if callable(routing_context_reader):
            try:
                raw_context = routing_context_reader(session.session_id)
                if raw_context is not None:
                    routing_context = ApplicationRoutingContext.model_validate(
                        copy.deepcopy(raw_context.model_dump(mode="python"))
                    )
            except Exception as error:
                raise ChatUnavailable("route preflight is unavailable") from error
        raw = (
            classify(application_request, routing_context=routing_context)
            if _accepts_keyword(classify, "routing_context")
            else classify(application_request)
        )
        return ApplicationRouteDecision.model_validate(
            copy.deepcopy(raw.model_dump(mode="python"))
        )

    def project(
        self, session_id: str, *, requester: str, tenant_id: str
    ) -> ChatProjectReference:
        session = self._session(session_id, requester=requester, tenant_id=tenant_id)
        return self._project(session.tenant_id, session.project_id, requester=requester)

    def references(
        self, session_id: str, *, requester: str, tenant_id: str
    ) -> tuple[ChatContextReference, ...]:
        session = self._session(session_id, requester=requester, tenant_id=tenant_id)
        return self._references(session)

    def runtime_panel(
        self, session_id: str, *, requester: str, tenant_id: str
    ) -> ChatRuntimePanelView:
        session = self._session(session_id, requester=requester, tenant_id=tenant_id)
        try:
            panel = _copy(
                self._environment.panel(session.tenant_id, session.project_id),
                ChatRuntimePanelView,
                field="chat runtime panel",
            )
        except ChatUnavailable:
            raise
        except Exception as error:
            raise ChatUnavailable("chat runtime panel is unavailable") from error
        return panel

    def events_after(
        self,
        session_id: str,
        *,
        requester: str,
        tenant_id: str,
        last_event_id: str | None,
    ) -> tuple[ChatEvent, ...] | None:
        self._session(session_id, requester=requester, tenant_id=tenant_id)
        try:
            values = self._events.read_after(session_id, last_event_id)
        except Exception as error:
            raise ChatUnavailable("chat event store is unavailable") from error
        if values is None:
            return None
        return tuple(_copy(value, ChatEvent, field="chat event") for value in values)

    def trace(
        self,
        session_id: str,
        trace_id: str,
        *,
        requester: str,
        tenant_id: str,
    ) -> tuple[ChatTraceEvent, ...]:
        session = self._session(session_id, requester=requester, tenant_id=tenant_id)
        checked_trace_id = identifier(trace_id, field="trace_id")
        if self._traces is None:
            raise ChatUnavailable("chat trace store is unavailable")
        try:
            values = self._traces.read(checked_trace_id)
        except ChatUnavailable:
            raise
        except Exception as error:
            raise ChatUnavailable("chat trace store is unavailable") from error
        projected = tuple(
            _copy(value, ChatTraceEvent, field="chat trace event") for value in values
        )
        if any(value.trace_id != checked_trace_id or value.project_id != session.project_id for value in projected):
            raise ChatRejected("chat trace does not belong to the chat session")
        historical = tuple(value for value in projected if value.conversation_id != session.session_id)
        if historical:
            lookup = getattr(self._conversations, "conversation_for_session", None)
            if not callable(lookup):
                raise ChatRejected("chat trace does not belong to the chat session")
            try:
                current = lookup(session.session_id)
                sources = {value.conversation_id: lookup(value.conversation_id) for value in historical}
            except Exception as error:
                raise ChatUnavailable("conversation history is unavailable") from error
            if current is None or any(
                source is None
                or source.conversation_id != current.conversation_id
                or source.project_id != session.project_id
                or source.tenant_id != session.tenant_id
                or source.requester != session.requester
                for source in sources.values()
            ) or (
                current.project_id != session.project_id
                or current.tenant_id != session.tenant_id
                or current.requester != session.requester
            ) or any(
                not any(
                    turn.trace_id == value.trace_id and turn.request_id == value.request_id
                    for turn in current.turns
                )
                for value in historical
            ):
                raise ChatRejected("chat trace does not belong to the chat session")
        return projected


__all__ = ("DesktopCopilotChatRuntime",)
