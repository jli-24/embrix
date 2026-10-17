from __future__ import annotations

import asyncio
import copy
import inspect
import logging

from pydantic import ValidationError

from embedded_copilot.agents.supervisor import classify_intent
from embedded_copilot.engineering_context import (
    EngineeringContextExternalEvidenceUnavailable,
    EngineeringContextProviderPort,
    EngineeringContextRequest,
    EngineeringContextSnapshot,
    EngineeringContextUnavailable,
)
from embedded_copilot.engineering_context.models import ContextSourceType
from embedded_copilot.engineering_copilot import PlanStatus
from embedded_copilot.engineering_intelligence import EvidenceGate, EvidenceGateRejected
from embedded_copilot.engineering_qa import (
    EngineeringAnswer,
    EngineeringAnswerRejected,
    EngineeringQAService,
    EngineeringQuestion,
    InsufficientEvidence,
    ModelInfo,
)
from embedded_copilot.engineering_qa.conversation_policy import (
    ConversationMode,
    clean_response_content,
    fallback_response,
    prepare_response_content,
    resolve_conversation_mode,
)
from embedded_copilot.engineering_requirements import (
    RequirementContext,
    is_plan_approval_request,
    merge_requirement_context,
)
from embedded_copilot.firmware_engineering import is_firmware_build_request
from embedded_copilot.model_gateway import (
    EngineeringStage,
    ModelGatewayError,
    ModelGatewayPort,
    ModelInvocationGenerationOptions,
    ModelInvocationPort,
    ModelInvocationRequest,
    ModelInvocationStatus,
    ModelPolicy,
    validate_model_response,
)
from embedded_copilot.runtime_diagnostics import (
    attach_exception_diagnostics,
    record_application_runtime_not_reached,
    record_application_runtime_stage,
    record_binding_engineering_qa_not_reached,
    record_binding_engineering_qa_stage,
    record_engineering_qa_not_reached,
    record_engineering_qa_stage,
    record_prompt_pipeline_not_reached,
    record_prompt_pipeline_stage,
)

from .exceptions import (
    ApplicationAnswerRejected,
    ApplicationContextUnavailable,
    ApplicationExternalEvidenceUnavailable,
    ApplicationInsufficientEvidence,
    ApplicationModelUnavailable,
)
from .models import (
    ApplicationAnswerEvidence,
    ApplicationQuestionRequest,
    ApplicationRoute,
    ApplicationRouteDecision,
    ApplicationRoutingContext,
)
from .ports import (
    ApplicationDebugAgentPort,
    ApplicationDebugPromptPort,
    ApplicationFirmwareAgentPort,
    ApplicationFirmwarePromptPort,
)

_AUTO = "sha256:" + "0" * 64

logger = logging.getLogger(__name__)
_APPLICATION_EVIDENCE_TELEMETRY_EVENT = "application_evidence_boundary_telemetry"
_APPLICATION_EVIDENCE_STAGES = (
    "E0_EVIDENCE_BUILD_ENTER",
    "E1_REQUIREMENT_CONTEXT_BIND",
    "E2_PROJECT_CONTEXT_BIND",
    "E3_EVIDENCE_COLLECTION",
    "E4_EVIDENCE_VALIDATE",
    "E5_APPLICATION_ANSWER_COMMIT",
)


class _InvocationGatewayAdapter:
    """Compatibility ModelGatewayPort backed by the governed invocation port."""

    __slots__ = ("_invocation", "_stage_id", "_task_type")

    def __init__(self, invocation: ModelInvocationPort, stage_id: EngineeringStage, task_type: str | None) -> None:
        if not isinstance(invocation, ModelInvocationPort):
            raise TypeError("model invocation service is invalid")
        self._invocation = invocation
        self._stage_id = stage_id
        self._task_type = task_type

    @property
    def is_configured(self) -> bool:
        return True

    @property
    def failure_code(self) -> str | None:
        return None

    @staticmethod
    def _response(result):
        if result.status is ModelInvocationStatus.SUCCESS and result.response is not None:
            return result.response
        code = result.error_code or "MODEL_GATEWAY_EXECUTION_FAILED"
        error = ModelGatewayError(code)
        error.reason_code = code
        error.gateway_invocation_count = result.provider_request_count
        error.provider_response_present = False
        if result.status is not ModelInvocationStatus.SUCCESS:
            error.validation_stage = "MODEL_GOVERNANCE"
        raise error

    def generate(self, request):
        invocation_request = request if isinstance(request, ModelInvocationRequest) else ModelInvocationRequest.from_model_request(
            request, stage_id=self._stage_id, task_type=self._task_type
        )
        return self._response(self._invocation.invoke(invocation_request))

    async def agenerate(self, request):
        invocation_request = request if isinstance(request, ModelInvocationRequest) else ModelInvocationRequest.from_model_request(
            request, stage_id=self._stage_id, task_type=self._task_type
        )
        return self._response(await self._invocation.ainvoke(invocation_request))


def _engineering_task_text(value: str) -> str:
    """Adapt an already validated UI question to single-line internal contracts."""

    return " ".join(line.strip() for line in value.splitlines() if line.strip())


def _record_application_evidence_telemetry(
    stage: str,
    status: str,
    *,
    requirement_context_present: bool,
    project_context_present: bool,
    project_id_present: bool | None = None,
    source_reference_count: int | None = None,
    evidence_count: int,
    answer_text_length: int,
    validation_error_code: str = "-",
) -> None:
    """Record only fixed, bounded ApplicationAnswerEvidence diagnostics."""

    logger.info(
        _APPLICATION_EVIDENCE_TELEMETRY_EVENT,
        extra={
            "event_name": _APPLICATION_EVIDENCE_TELEMETRY_EVENT,
            "evidence_stage": stage,
            "telemetry_status": status,
            "requirement_context_present": requirement_context_present,
            "project_context_present": project_context_present,
            "project_id_present": (
                project_context_present
                if project_id_present is None
                else project_id_present
            ),
            "source_reference_count": (
                evidence_count
                if source_reference_count is None
                else source_reference_count
            ),
            "evidence_count": evidence_count,
            "answer_text_length": answer_text_length,
            "validation_error_code": validation_error_code,
        },
    )


def _record_application_evidence_not_reached(
    stages: tuple[str, ...],
    *,
    requirement_context_present: bool,
    project_context_present: bool,
    project_id_present: bool | None = None,
    source_reference_count: int | None = None,
    evidence_count: int,
    answer_text_length: int,
) -> None:
    for stage in stages:
        _record_application_evidence_telemetry(
            stage,
            "NOT_REACHED",
            requirement_context_present=requirement_context_present,
            project_context_present=project_context_present,
            project_id_present=project_id_present,
            source_reference_count=source_reference_count,
            evidence_count=evidence_count,
            answer_text_length=answer_text_length,
            validation_error_code="-",
        )


def _request(value: object) -> ApplicationQuestionRequest:
    if type(value) is not ApplicationQuestionRequest:
        raise TypeError("application question is invalid")
    return ApplicationQuestionRequest.model_validate(
        copy.deepcopy(value.model_dump(mode="python"))
    )


def _engineering_qa_service_boundary_flags(
    service: object,
) -> tuple[bool, bool, bool]:
    """Return bounded method/signature/dependency flags without object content."""

    method = getattr(service, "answer", None)
    method_exists = callable(method)
    signature_compatible = False
    if method_exists:
        try:
            signature = inspect.signature(method)
            positional = tuple(
                parameter
                for parameter in signature.parameters.values()
                if parameter.kind
                in {
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                }
            )
            required = tuple(
                parameter
                for parameter in positional
                if parameter.default is inspect.Parameter.empty
            )
            signature_compatible = len(required) == 1
        except (TypeError, ValueError):
            signature_compatible = False
    # EngineeringQAService owns the validated model policy through its
    # PromptBuilder; it deliberately does not expose a separate ``_policy``
    # slot.  Checking that non-existent slot made a healthy service report a
    # failed dependency resolution at the binding boundary.
    dependency_resolved = all(
        hasattr(service, field)
        for field in ("_context_provider", "_gateway", "_prompt_builder")
    )
    return method_exists, signature_compatible, dependency_resolved


def _evidence_policy(project_mode: str | None, answer_contract: str) -> str:
    """Resolve the application evidence policy without changing the answer schema."""

    normalized = (
        project_mode.strip().upper()
        if isinstance(project_mode, str) and project_mode.strip()
        else None
    )
    if normalized is not None:
        return "CREATE_NEW" if normalized == "CREATE_NEW" else "BROWNFIELD"
    return "CREATE_NEW" if answer_contract == "GREENFIELD" else "BROWNFIELD"


def _create_new_evidence_flags(
    requirement_context: RequirementContext | None,
    *,
    answer_text_present: bool,
) -> tuple[bool, bool, bool]:
    """Return bounded requirement/design/plan evidence presence flags."""

    if requirement_context is None:
        return False, answer_text_present, False
    requirement_present = bool(
        requirement_context.standard_fields
        or requirement_context.requirements
        or requirement_context.constraints
        or requirement_context.proposed_items
    )
    design_present = answer_text_present
    lifecycle = getattr(requirement_context.lifecycle, "value", requirement_context.lifecycle)
    plan_present = bool(
        requirement_context.requirements
        or requirement_context.constraints
        or requirement_context.proposed_items
        or requirement_context.open_questions
        or lifecycle in {"REQUIREMENTS_READY", "DESIGN_READY"}
    )
    return requirement_present, design_present, plan_present


class CanonicalSupervisorBinding:
    __slots__ = ("_project_mode",)

    def __init__(self, project_mode=None) -> None:
        if project_mode is not None and not callable(project_mode):
            raise TypeError("project mode resolver is invalid")
        self._project_mode = project_mode

    def route(
        self,
        request: ApplicationQuestionRequest,
        routing_context: ApplicationRoutingContext | None = None,
    ) -> ApplicationRouteDecision:
        checked = _request(request)
        try:
            decision = classify_intent(checked.question)
            context = routing_context or ApplicationRoutingContext()
            creation_mode = context.project_creation_mode
            if self._project_mode is not None:
                project = self._project_mode(checked.project_id, checked.tenant_id)
                creation_mode = getattr(
                    getattr(project, "creation_mode", None), "value", None
                )
            normalized = checked.question.casefold()
            if is_firmware_build_request(checked.question):
                # Conversation classification is deliberately side-effect free.
                # The resulting Firmware route still requires the explicit
                # Build Request, authorization, approval, and ExecutionWorker
                # boundaries before any compiler can run.
                return ApplicationRouteDecision.for_request(
                    checked, route=ApplicationRoute.FIRMWARE
                )
            conversation_mode = resolve_conversation_mode(
                checked.question,
                requirement_context=context.requirement_context,
            )
            if decision.intent.value.upper() == "DEBUG" or any(
                marker in normalized
                for marker in ("超时", "崩溃", "hardfault", "hard fault", "panic", "报错", "日志")
            ):
                return ApplicationRouteDecision.for_request(
                    checked, route=ApplicationRoute.DEBUG_ANALYSIS
                )
            design_request = (
                conversation_mode in {ConversationMode.DOCUMENT, ConversationMode.DESIGN}
                or decision.design_intent
                or any(
                    marker in normalized
                    for marker in ("总体架构", "系统架构", "架构方案", "设计方案")
                )
            )
            existing_requirement = context.requirement_context
            # A design route is a projection of a confirmed requirement. A
            # first-turn design request remains backward-compatible, while a
            # persisted partial context must continue through clarification.
            explicit_confirmation = any(
                marker in normalized
                for marker in (
                    "开始设计",
                    "进入架构设计",
                    "需求已确认",
                    "需求明确",
                    "可以开始设计",
                    "requirements are ready",
                    "确认需求",
                )
            )
            if existing_requirement is None:
                design_allowed = True
            elif (
                existing_requirement.effective_status.value == "CONFIRMED"
                and context.engineering_plan is not None
                and context.engineering_plan.status is PlanStatus.APPROVED
            ):
                design_allowed = True
            elif explicit_confirmation:
                preview = merge_requirement_context(
                    existing_requirement,
                    project_id=checked.project_id,
                    conversation_id=existing_requirement.conversation_id,
                    turn_id=checked.request_id,
                    utterance=checked.question,
                    confirmation_requested=True,
                )
                design_allowed = (
                    preview.effective_status.value == "CONFIRMED"
                    and context.engineering_plan is not None
                    and context.engineering_plan.status is PlanStatus.APPROVED
                )
            else:
                design_allowed = False
            if is_plan_approval_request(checked.question):
                design_allowed = False
            if creation_mode == "CREATE_NEW" and design_request and design_allowed:
                return ApplicationRouteDecision.for_request(
                    checked, route=ApplicationRoute.GREENFIELD_DESIGN
                )
            greenfield_signal = any(
                marker in normalized
                for marker in (
                    "stm32", "esp32", "温控器", "温度控制", "bootloader", "引导加载",
                    "做个", "做一个", "开发一个", "实现一个", "设计一个",
                    "手机控制", "手机查看", "远程监控", "传感器", "执行器",
                )
            )
            knowledge_question = any(
                marker in normalized
                for marker in (
                    "是什么", "有什么区别", "区别", "比较", "哪个更适合", "对比",
                    "what is", "difference", "compare", "which is better",
                    "datasheet", "数据手册", "reference manual",
                )
            )
            if creation_mode == "CREATE_NEW" and greenfield_signal and not knowledge_question:
                return ApplicationRouteDecision.for_request(
                    checked, route=ApplicationRoute.GREENFIELD_DISCOVERY
                )
            if decision.intent.value.upper() == "KNOWLEDGE" or any(
                marker in normalized
                for marker in (
                    "是什么", "有什么区别", "区别", "比较", "哪个更适合", "对比",
                    "what is", "difference", "compare", "which is better",
                    "datasheet", "数据手册", "reference manual",
                )
            ):
                return ApplicationRouteDecision.for_request(
                    checked, route=ApplicationRoute.KNOWLEDGE
                )
            if creation_mode == "ATTACH_EXISTING" and (
                decision.intent.value.upper() in {"FIRMWARE", "BROWNFIELD"}
                or any(
                    marker in normalized
                    for marker in ("symbol", "哪个文件", "在哪里", "定位", "调用关系", "代码分析", "source")
                )
            ):
                return ApplicationRouteDecision.for_request(
                    checked, route=ApplicationRoute.BROWNFIELD_ANALYSIS
                )
            if creation_mode == "CREATE_NEW":
                greenfield_signal = greenfield_signal or context.requirement_context is not None or context.previous_route in {
                    ApplicationRoute.GREENFIELD_DISCOVERY.value,
                    ApplicationRoute.GREENFIELD_DESIGN.value,
                    ApplicationRoute.GREENFIELD.value,
                }
                if greenfield_signal:
                    return ApplicationRouteDecision.for_request(
                        checked, route=ApplicationRoute.GREENFIELD_DISCOVERY
                    )
            if decision.intent.value.upper() == "FIRMWARE":
                return ApplicationRouteDecision.for_request(
                    checked, route=ApplicationRoute.FIRMWARE
                )
            return ApplicationRouteDecision.for_request(
                checked, route=ApplicationRoute.CLARIFICATION_REQUIRED
            )
        except Exception as error:
            raise ApplicationContextUnavailable() from error


class _BoundContextProvider:
    __slots__ = ("_request", "_snapshot")

    def __init__(
        self,
        request: EngineeringContextRequest,
        snapshot: EngineeringContextSnapshot,
    ) -> None:
        self._request = copy.deepcopy(request)
        self._snapshot = EngineeringContextSnapshot.model_validate(
            copy.deepcopy(snapshot.model_dump(mode="python"))
        )

    def get_context(self, request: EngineeringContextRequest):
        if request != self._request:
            raise ApplicationContextUnavailable()
        return EngineeringContextSnapshot.model_validate(
            copy.deepcopy(self._snapshot.model_dump(mode="python"))
        )


class EngineeringQABinding:
    __slots__ = (
        "_answer_contract",
        "_context",
        "_gateway",
        "_policy",
        "_generation_options",
        "_stage_id",
        "_require_evidence",
        "_task_type",
    )

    def __init__(
        self,
        *,
        context_provider: EngineeringContextProviderPort,
        model_gateway: ModelGatewayPort | None = None,
        model_policy: ModelPolicy | None = None,
        generation_options: ModelInvocationGenerationOptions | None = None,
        model_invocation_service: ModelInvocationPort | None = None,
        stage_id: EngineeringStage = EngineeringStage.REQUIREMENT_ANALYSIS,
        require_evidence: bool = True,
        answer_contract: str = "EVIDENCE_BACKED",
        task_type: str | None = None,
    ) -> None:
        if not isinstance(context_provider, EngineeringContextProviderPort):
            raise TypeError("engineering context provider is invalid")
        if model_invocation_service is None and not isinstance(model_gateway, ModelGatewayPort):
            raise TypeError("model gateway is invalid")
        self._context = context_provider
        self._gateway = (
            _InvocationGatewayAdapter(model_invocation_service, stage_id, task_type)
            if model_invocation_service is not None
            else model_gateway
        )
        self._policy = None if model_invocation_service is not None or model_policy is None else ModelPolicy.model_validate(copy.deepcopy(model_policy.model_dump(mode="python")))
        if model_invocation_service is None and self._policy is None:
            raise TypeError("legacy model policy is required")
        self._generation_options = (generation_options or ModelInvocationGenerationOptions()) if model_invocation_service is not None else None
        self._stage_id = stage_id
        if type(require_evidence) is not bool:
            raise TypeError("require_evidence is invalid")
        if answer_contract not in {"EVIDENCE_BACKED", "GREENFIELD"}:
            raise ValueError("answer contract is invalid")
        self._require_evidence = require_evidence
        self._answer_contract = answer_contract
        self._task_type = task_type

    def _load(
        self, request: ApplicationQuestionRequest, *, require_evidence: bool | None = None
    ) -> tuple[EngineeringQuestion, EngineeringContextRequest, EngineeringContextSnapshot]:
        require_evidence = self._require_evidence if require_evidence is None else require_evidence
        task_text = _engineering_task_text(request.question)
        question = EngineeringQuestion(
            question_id=request.request_id,
            project_id=request.project_id,
            question=task_text,
            constraints=request.constraints,
        )
        context_request = EngineeringContextRequest(
            project_id=request.project_id,
            tenant_id=request.tenant_id,
            task=task_text,
            constraints=request.constraints,
            external_search=request.external_search,
        )
        try:
            raw = self._context.get_context(copy.deepcopy(context_request))
        except EngineeringContextExternalEvidenceUnavailable as error:
            wrapped = ApplicationExternalEvidenceUnavailable()
            attach_exception_diagnostics(
                wrapped,
                error,
                exception_layer="EngineeringQABinding",
                exception_stage="CONTEXT_LOAD",
            )
            raise wrapped from error
        except EngineeringContextUnavailable as error:
            if self._answer_contract != "GREENFIELD":
                raise ApplicationContextUnavailable() from error
            # A new project may have no source/evidence projection yet.  Keep
            # the Greenfield lifecycle alive with an explicitly unverified,
            # empty snapshot; Brownfield remains fail-closed above.
            raw = None
        except Exception as error:
            wrapped = ApplicationContextUnavailable()
            attach_exception_diagnostics(
                wrapped,
                error,
                exception_layer="EngineeringQABinding",
                exception_stage="CONTEXT_LOAD",
            )
            raise wrapped from error
        if raw is None:
            if require_evidence:
                raise ApplicationInsufficientEvidence()
            snapshot = EngineeringContextSnapshot.create(
                project_id=request.project_id,
                tenant_id=request.tenant_id,
                task_description=task_text,
                memory_evidence=(),
                graph_evidence=(),
                knowledge_evidence=(),
                multimodal_evidence=(),
                conflicts=(),
                confidence=0.0,
                provenance=(),
                verification_status="UNVERIFIED",
            )
        else:
            try:
                snapshot = EngineeringContextSnapshot.model_validate(
                    copy.deepcopy(raw.model_dump(mode="python"))
                )
            except Exception as error:
                wrapped = ApplicationContextUnavailable()
                attach_exception_diagnostics(
                    wrapped,
                    error,
                    exception_layer="EngineeringQABinding",
                    exception_stage="CONTEXT_SNAPSHOT_VALIDATE",
                )
                raise wrapped from error
        if (
            snapshot.project_id != request.project_id
            or snapshot.tenant_id != request.tenant_id
            or snapshot.task_description != question.question
        ):
            raise ApplicationContextUnavailable()
        evidence = (
            *snapshot.memory_evidence,
            *snapshot.graph_evidence,
            *snapshot.knowledge_evidence,
            *snapshot.multimodal_evidence,
        )
        current_evidence = tuple(
            item for item in evidence
            if item.source_type is not ContextSourceType.KNOWLEDGE_EVOLUTION
            and not (
                item.source_type is ContextSourceType.ENGINEERING_MEMORY
                and bool(item.provenance)
            )
        )
        if require_evidence and not current_evidence:
            raise ApplicationInsufficientEvidence()
        return question, context_request, snapshot

    def _provider_is_configured(self) -> bool:
        return bool(getattr(self._gateway, "is_configured", True))

    def _raise_model_unavailable(self, snapshot: EngineeringContextSnapshot) -> None:
        error = ApplicationModelUnavailable(
            context=snapshot,
            configured=self._provider_is_configured(),
            reason_code=getattr(self._gateway, "failure_code", None),
        )
        attach_exception_diagnostics(
            error,
            error,
            exception_layer="EngineeringQABinding",
            exception_stage="PROVIDER_RESOLVE",
        )
        raise error

    @staticmethod
    def _with_firmware_evidence(
        snapshot: EngineeringContextSnapshot, values: object
    ) -> EngineeringContextSnapshot:
        if not isinstance(values, tuple):
            raise ApplicationContextUnavailable()
        checked = tuple(
            item
            for item in values
            if getattr(item, "source_type", None) is ContextSourceType.FIRMWARE_SOURCE
        )
        if len(checked) != len(values):
            raise ApplicationContextUnavailable()
        existing = {item.evidence_id: item for item in snapshot.knowledge_evidence}
        for item in checked:
            if item.project_id != snapshot.project_id:
                raise ApplicationContextUnavailable()
            previous = existing.get(item.evidence_id)
            if previous is not None and previous.fingerprint != item.fingerprint:
                raise ApplicationContextUnavailable()
            existing[item.evidence_id] = item
        combined = tuple(existing[key] for key in sorted(existing))
        evidence = (
            *snapshot.memory_evidence,
            *snapshot.graph_evidence,
            *combined,
            *snapshot.multimodal_evidence,
        )
        if not evidence:
            raise ApplicationInsufficientEvidence()
        provenance = tuple(item.source() for item in evidence)
        confidence_values = tuple(
            item.confidence for item in evidence if item.confidence is not None
        )
        return EngineeringContextSnapshot.create(
            project_id=snapshot.project_id,
            tenant_id=snapshot.tenant_id,
            task_description=snapshot.task_description,
            memory_evidence=snapshot.memory_evidence,
            graph_evidence=snapshot.graph_evidence,
            knowledge_evidence=combined,
            multimodal_evidence=snapshot.multimodal_evidence,
            conflicts=snapshot.conflicts,
            confidence=min(confidence_values) if confidence_values else 0.0,
            provenance=provenance,
            verification_status=snapshot.verification_status,
        )

    @staticmethod
    def _rejection(error: EngineeringAnswerRejected) -> ApplicationAnswerRejected:
        wrapped = ApplicationAnswerRejected(
            reason_code=error.reason_code,
            validation_stage=error.validation_stage,
            gateway_invocation_count=error.gateway_invocation_count,
            provider_response_present=error.provider_response_present,
            proposed_requirements_count=error.proposed_requirements_count,
            open_questions_count=error.open_questions_count,
            answer_text_present=error.answer_text_present,
        )
        attach_exception_diagnostics(
            wrapped,
            error,
            exception_layer="EngineeringQABinding",
            exception_stage="ENGINEERING_QA_EXCEPTION",
        )
        return wrapped

    @staticmethod
    def _model_unavailable(
        error: Exception,
        snapshot: EngineeringContextSnapshot,
        *,
        reason_code: str | None = None,
    ) -> ApplicationModelUnavailable:
        unavailable = ApplicationModelUnavailable(
            context=snapshot,
            configured=True,
            reason_code=(
                reason_code
                if reason_code is not None
                else getattr(error, "reason_code", "MODEL_UNAVAILABLE")
            ),
        )
        attach_exception_diagnostics(
            unavailable,
            error,
            exception_layer="EngineeringQABinding",
            exception_stage="ENGINEERING_QA_EXCEPTION",
        )
        for field in (
            "gateway_invocation_count",
            "provider_response_present",
            "validation_stage",
            "proposed_requirements_count",
            "open_questions_count",
            "answer_text_present",
        ):
            if hasattr(error, field):
                setattr(unavailable, field, getattr(error, field))
        return unavailable

    @staticmethod
    def _evidence(
        question: EngineeringQuestion,
        snapshot: EngineeringContextSnapshot,
        answer: object,
        requirement_context: RequirementContext | None = None,
        *,
        project_mode: str | None = None,
        answer_contract: str = "EVIDENCE_BACKED",
    ) -> ApplicationAnswerEvidence:
        proposal_count = (
            len(answer.requirement_proposals)
            if isinstance(getattr(answer, "requirement_proposals", None), tuple)
            else 0
        )
        question_count = (
            len(answer.open_questions)
            if isinstance(getattr(answer, "open_questions", None), tuple)
            else 0
        )
        answer_text_present = bool(
            isinstance(getattr(answer, "content", None), str)
            and answer.content.strip()
        )
        answer_text_length = (
            len(answer.content) if isinstance(getattr(answer, "content", None), str) else 0
        )
        evidence = (
            *snapshot.memory_evidence,
            *snapshot.graph_evidence,
            *snapshot.knowledge_evidence,
            *snapshot.multimodal_evidence,
        )
        evidence_count = len(evidence)
        project_context_present = snapshot is not None
        requirement_context_present = requirement_context is not None
        _record_application_evidence_telemetry(
            "E0_EVIDENCE_BUILD_ENTER",
            "PASS",
            requirement_context_present=requirement_context_present,
            project_context_present=project_context_present,
            evidence_count=evidence_count,
            answer_text_length=answer_text_length,
        )
        try:
            checked = EngineeringAnswer.model_validate(copy.deepcopy(answer))
        except (TypeError, ValueError, ValidationError) as error:
            _record_application_evidence_telemetry(
                "E4_EVIDENCE_VALIDATE",
                "FAIL",
                requirement_context_present=requirement_context_present,
                project_context_present=project_context_present,
                evidence_count=evidence_count,
                answer_text_length=answer_text_length,
                validation_error_code="ANSWER_CONTRACT_MISMATCH",
            )
            _record_application_evidence_not_reached(
                ("E1_REQUIREMENT_CONTEXT_BIND", "E2_PROJECT_CONTEXT_BIND", "E3_EVIDENCE_COLLECTION", "E5_APPLICATION_ANSWER_COMMIT"),
                requirement_context_present=requirement_context_present,
                project_context_present=project_context_present,
                evidence_count=evidence_count,
                answer_text_length=answer_text_length,
            )
            raise ApplicationAnswerRejected(
                reason_code="ANSWER_CONTRACT_MISMATCH",
                validation_stage="APPLICATION_ANSWER_REVALIDATION",
                proposed_requirements_count=proposal_count,
                open_questions_count=question_count,
                answer_text_present=answer_text_present,
            ) from error
        try:
            checked_requirements = (
                None
                if requirement_context is None
                else RequirementContext.model_validate(
                    copy.deepcopy(requirement_context), strict=False
                )
            )
        except (TypeError, ValueError, ValidationError) as error:
            _record_application_evidence_telemetry(
                "E1_REQUIREMENT_CONTEXT_BIND",
                "FAIL",
                requirement_context_present=requirement_context_present,
                project_context_present=project_context_present,
                evidence_count=evidence_count,
                answer_text_length=answer_text_length,
                validation_error_code="ANSWER_CONTRACT_MISMATCH",
            )
            _record_application_evidence_not_reached(
                ("E2_PROJECT_CONTEXT_BIND", "E3_EVIDENCE_COLLECTION", "E4_EVIDENCE_VALIDATE", "E5_APPLICATION_ANSWER_COMMIT"),
                requirement_context_present=requirement_context_present,
                project_context_present=project_context_present,
                evidence_count=evidence_count,
                answer_text_length=answer_text_length,
            )
            raise ApplicationAnswerRejected(
                reason_code="ANSWER_CONTRACT_MISMATCH",
                validation_stage="APPLICATION_REQUIREMENT_CONTEXT_REVALIDATION",
                proposed_requirements_count=proposal_count,
                open_questions_count=question_count,
                answer_text_present=answer_text_present,
            ) from error
        mode = resolve_conversation_mode(
            question.question,
            requirement_context=checked_requirements,
        )
        content = clean_response_content(
            prepare_response_content(
                checked.content,
                question=question.question,
                requirement_context=checked_requirements,
            ),
            mode=mode,
        ) or fallback_response(mode)
        if content != checked.content:
            answer_material = checked.model_dump(mode="python")
            answer_material["answer_id"] = ""
            answer_material["content"] = content
            answer_material["fingerprint"] = _AUTO
            checked = EngineeringAnswer.model_validate(answer_material)
            proposal_count = len(checked.requirement_proposals)
            question_count = len(checked.open_questions)
            answer_text_present = bool(checked.content.strip())
            answer_text_length = len(checked.content)
        _record_application_evidence_telemetry(
            "E1_REQUIREMENT_CONTEXT_BIND",
            "PASS",
            requirement_context_present=requirement_context_present,
            project_context_present=project_context_present,
            evidence_count=evidence_count,
            answer_text_length=answer_text_length,
        )
        evidence_policy = _evidence_policy(project_mode, answer_contract)
        requirement_evidence_present, design_evidence_present, plan_evidence_present = (
            _create_new_evidence_flags(
                checked_requirements,
                answer_text_present=answer_text_present,
            )
            if evidence_policy == "CREATE_NEW"
            else (False, False, False)
        )
        expected_refs = tuple(sorted(item.evidence_id for item in evidence))
        policy_failure: str | None = None
        if evidence_policy == "CREATE_NEW":
            if not requirement_evidence_present:
                policy_failure = "CREATE_NEW_REQUIREMENT_EVIDENCE_MISSING"
            elif not design_evidence_present:
                policy_failure = "CREATE_NEW_DESIGN_EVIDENCE_MISSING"
            elif not plan_evidence_present:
                policy_failure = "CREATE_NEW_PLAN_EVIDENCE_MISSING"
        elif not evidence:
            policy_failure = "SOURCE_REFERENCE_REQUIRED"
        if policy_failure is not None:
            _record_application_evidence_telemetry(
                "E4_EVIDENCE_VALIDATE",
                "FAIL",
                requirement_context_present=requirement_context_present,
                project_context_present=project_context_present,
                evidence_count=evidence_count,
                answer_text_length=answer_text_length,
                validation_error_code=policy_failure,
            )
            _record_application_evidence_not_reached(
                ("E5_APPLICATION_ANSWER_COMMIT",),
                requirement_context_present=requirement_context_present,
                project_context_present=project_context_present,
                evidence_count=evidence_count,
                answer_text_length=answer_text_length,
            )
            raise ApplicationAnswerRejected(
                reason_code="ANSWER_CONTRACT_MISMATCH",
                validation_stage=(
                    "APPLICATION_SOURCE_REFERENCE_REQUIRED"
                    if evidence_policy == "BROWNFIELD"
                    else "APPLICATION_EVIDENCE_POLICY"
                ),
                proposed_requirements_count=proposal_count,
                open_questions_count=question_count,
                answer_text_present=answer_text_present,
            )
        binding_stage: str | None = None
        binding_stage_code: str | None = None
        if checked_requirements is not None and checked_requirements.project_id != question.project_id:
            binding_stage = "APPLICATION_REQUIREMENT_PROJECT_BINDING"
            binding_stage_code = "ANSWER_CONTRACT_MISMATCH"
        elif question.project_id != snapshot.project_id:
            binding_stage = "APPLICATION_CONTEXT_PROJECT_BINDING"
            binding_stage_code = "ANSWER_CONTRACT_MISMATCH"
        elif question.question != snapshot.task_description:
            binding_stage = "APPLICATION_CONTEXT_TASK_BINDING"
            binding_stage_code = "ANSWER_CONTRACT_MISMATCH"
        elif checked.question_id != question.question_id:
            binding_stage = "APPLICATION_ANSWER_QUESTION_BINDING"
            binding_stage_code = "ANSWER_CONTRACT_MISMATCH"
        elif checked.evidence_refs != expected_refs:
            binding_stage = "APPLICATION_EVIDENCE_REFERENCE_BINDING"
            binding_stage_code = "ANSWER_CONTRACT_MISMATCH"

        if binding_stage in {
            "APPLICATION_REQUIREMENT_PROJECT_BINDING",
            "APPLICATION_CONTEXT_PROJECT_BINDING",
            "APPLICATION_CONTEXT_TASK_BINDING",
            "APPLICATION_ANSWER_QUESTION_BINDING",
        }:
            _record_application_evidence_telemetry(
                "E2_PROJECT_CONTEXT_BIND",
                "FAIL",
                requirement_context_present=requirement_context_present,
                project_context_present=project_context_present,
                evidence_count=evidence_count,
                answer_text_length=answer_text_length,
                validation_error_code=binding_stage_code or "ANSWER_CONTRACT_MISMATCH",
            )
            _record_application_evidence_not_reached(
                ("E3_EVIDENCE_COLLECTION", "E4_EVIDENCE_VALIDATE", "E5_APPLICATION_ANSWER_COMMIT"),
                requirement_context_present=requirement_context_present,
                project_context_present=project_context_present,
                evidence_count=evidence_count,
                answer_text_length=answer_text_length,
            )
            raise ApplicationAnswerRejected(
                reason_code="ANSWER_CONTRACT_MISMATCH",
                validation_stage=binding_stage,
                proposed_requirements_count=proposal_count,
                open_questions_count=question_count,
                answer_text_present=answer_text_present,
            )
        _record_application_evidence_telemetry(
            "E2_PROJECT_CONTEXT_BIND",
            "PASS",
            requirement_context_present=requirement_context_present,
            project_context_present=project_context_present,
            evidence_count=evidence_count,
            answer_text_length=answer_text_length,
        )
        if binding_stage == "APPLICATION_EVIDENCE_REFERENCE_BINDING":
            _record_application_evidence_telemetry(
                "E3_EVIDENCE_COLLECTION",
                "FAIL",
                requirement_context_present=requirement_context_present,
                project_context_present=project_context_present,
                evidence_count=evidence_count,
                answer_text_length=answer_text_length,
                validation_error_code=binding_stage_code or "ANSWER_CONTRACT_MISMATCH",
            )
            _record_application_evidence_not_reached(
                ("E4_EVIDENCE_VALIDATE", "E5_APPLICATION_ANSWER_COMMIT"),
                requirement_context_present=requirement_context_present,
                project_context_present=project_context_present,
                evidence_count=evidence_count,
                answer_text_length=answer_text_length,
            )
            raise ApplicationAnswerRejected(
                reason_code="ANSWER_CONTRACT_MISMATCH",
                validation_stage=binding_stage,
                proposed_requirements_count=proposal_count,
                open_questions_count=question_count,
                answer_text_present=answer_text_present,
            )
        _record_application_evidence_telemetry(
            "E3_EVIDENCE_COLLECTION",
            "PASS",
            requirement_context_present=requirement_context_present,
            project_context_present=project_context_present,
            evidence_count=evidence_count,
            answer_text_length=answer_text_length,
        )
        try:
            result = ApplicationAnswerEvidence(
                question=question,
                context=snapshot,
                answer=checked,
                requirement_context=checked_requirements,
            )
        except (TypeError, ValueError, ValidationError) as error:
            _record_application_evidence_telemetry(
                "E4_EVIDENCE_VALIDATE",
                "FAIL",
                requirement_context_present=requirement_context_present,
                project_context_present=project_context_present,
                evidence_count=evidence_count,
                answer_text_length=answer_text_length,
                validation_error_code="ANSWER_CONTRACT_MISMATCH",
            )
            _record_application_evidence_not_reached(
                ("E5_APPLICATION_ANSWER_COMMIT",),
                requirement_context_present=requirement_context_present,
                project_context_present=project_context_present,
                evidence_count=evidence_count,
                answer_text_length=answer_text_length,
            )
            raise ApplicationAnswerRejected(
                reason_code="ANSWER_CONTRACT_MISMATCH",
                validation_stage="APPLICATION_EVIDENCE_FINGERPRINT",
                proposed_requirements_count=proposal_count,
                open_questions_count=question_count,
                answer_text_present=answer_text_present,
            ) from error
        _record_application_evidence_telemetry(
            "E4_EVIDENCE_VALIDATE",
            "PASS",
            requirement_context_present=requirement_context_present,
            project_context_present=project_context_present,
            evidence_count=evidence_count,
            answer_text_length=answer_text_length,
        )
        _record_application_evidence_telemetry(
            "E5_APPLICATION_ANSWER_COMMIT",
            "PASS",
            requirement_context_present=requirement_context_present,
            project_context_present=project_context_present,
            evidence_count=evidence_count,
            answer_text_length=answer_text_length,
        )
        return result

    def answer(
        self,
        request: ApplicationQuestionRequest,
        requirement_context: RequirementContext | None = None,
        *,
        project_mode: str | None = None,
        project_type: str | None = None,
    ) -> ApplicationAnswerEvidence:
        record_binding_engineering_qa_stage(
            "B0_BINDING_ANSWER_CALL_START",
            "PASS",
            dependency_container_resolved=all(
                hasattr(self, field)
                for field in ("_context", "_gateway", "_policy")
            ),
        )
        checked = _request(request)
        try:
            question, context_request, snapshot = self._load(checked)
        except Exception as error:
            record_binding_engineering_qa_stage(
                "B4_ENGINEERING_QA_EXCEPTION", "FAIL", error=error,
                exception_layer="EngineeringQABinding",
                exception_stage="CONTEXT_LOAD",
            )
            record_binding_engineering_qa_not_reached(
                (
                    "B1_ENGINEERING_QA_SERVICE_RESOLVE_START",
                    "B2_ENGINEERING_QA_SERVICE_RESOLVE_END",
                    "B3_ENGINEERING_QA_METHOD_CALL_START",
                )
            )
            record_engineering_qa_not_reached(
                (
                    "Q0_ENGINEERING_QA_ENTER",
                    "Q1_PROMPT_SERVICE_RESOLVE",
                    "Q2_PROMPT_BUILD_REQUEST",
                    "Q3_MODEL_REQUEST_CONSTRUCTION",
                    "Q4_ENGINEERING_QA_EXCEPTION",
                )
            )
            record_prompt_pipeline_not_reached(
                (
                    "P0_ENGINEERING_QA_SERVICE_ENTER",
                    "P1_TASK_REQUEST_CONSTRUCTION_START",
                    "P2_REQUIREMENT_CONTEXT_SERIALIZE",
                    "P3_GREENFIELD_PROMPT_SELECT",
                    "P4_PROMPT_BUILDER_CALL",
                    "P5_MODEL_REQUEST_CREATED",
                )
            )
            raise
        record_binding_engineering_qa_stage(
            "B1_ENGINEERING_QA_SERVICE_RESOLVE_START",
            "PASS",
            dependency_container_resolved=True,
        )
        try:
            service = EngineeringQAService(
                context_provider=_BoundContextProvider(context_request, snapshot),
                model_gateway=self._gateway,
                model_policy=self._policy,
                generation_options=self._generation_options,
                stage_id=self._stage_id,
                require_evidence=self._require_evidence,
                tenant_id=checked.tenant_id,
                answer_contract=self._answer_contract,
                requirement_context=(
                    requirement_context
                    if self._answer_contract == "GREENFIELD"
                    else None
                ),
                project_mode=project_mode,
                project_type=project_type,
                task_type=self._task_type,
            )
        except Exception as error:  # noqa: BLE001 - fixed, redacted boundary event
            record_binding_engineering_qa_stage(
                "B2_ENGINEERING_QA_SERVICE_RESOLVE_END", "FAIL", error=error,
                exception_layer="EngineeringQABinding",
                exception_stage="SERVICE_CONSTRUCTOR",
            )
            record_binding_engineering_qa_stage(
                "B4_ENGINEERING_QA_EXCEPTION", "FAIL", error=error,
                exception_layer="EngineeringQABinding",
                exception_stage="SERVICE_CONSTRUCTOR",
            )
            record_binding_engineering_qa_not_reached(
                ("B3_ENGINEERING_QA_METHOD_CALL_START",)
            )
            record_engineering_qa_stage(
                "Q0_ENGINEERING_QA_ENTER",
                "FAIL",
                error=error,
                engineering_qa_service_instance_available=False,
            )
            record_engineering_qa_stage(
                "Q1_PROMPT_SERVICE_RESOLVE",
                "FAIL",
                error=error,
                prompt_service_dependency_available=False,
                prompt_builder_dependency_available=False,
                request_builder_dependency_available=False,
            )
            record_engineering_qa_not_reached(
                (
                    "Q2_PROMPT_BUILD_REQUEST",
                    "Q3_MODEL_REQUEST_CONSTRUCTION",
                    "Q4_ENGINEERING_QA_EXCEPTION",
                )
            )
            record_prompt_pipeline_stage(
                "P0_ENGINEERING_QA_SERVICE_ENTER",
                "FAIL",
                error=error,
                engineering_qa_service_available=False,
            )
            record_prompt_pipeline_not_reached(
                (
                    "P1_TASK_REQUEST_CONSTRUCTION_START",
                    "P2_REQUIREMENT_CONTEXT_SERIALIZE",
                    "P3_GREENFIELD_PROMPT_SELECT",
                    "P4_PROMPT_BUILDER_CALL",
                    "P5_MODEL_REQUEST_CREATED",
                )
            )
            record_application_runtime_stage(
                "A6_PROMPT_REQUEST_BUILD", "FAIL", error=error
            )
            record_application_runtime_not_reached(("A7_PROMPT_BUILDER_ENTER",))
            raise
        service_method_exists, method_signature_compatible, dependency_resolved = (
            _engineering_qa_service_boundary_flags(service)
        )
        record_binding_engineering_qa_stage(
            "B2_ENGINEERING_QA_SERVICE_RESOLVE_END",
            "PASS",
            binding_service_instance_exists=True,
            service_constructor_completed=True,
            service_method_exists=service_method_exists,
            method_signature_compatible=method_signature_compatible,
            dependency_container_resolved=dependency_resolved,
        )
        if not self._provider_is_configured():
            self._raise_model_unavailable(snapshot)
        record_binding_engineering_qa_stage(
            "B3_ENGINEERING_QA_METHOD_CALL_START",
            "PASS"
            if service_method_exists and method_signature_compatible
            else "FAIL",
            reason_code=(
                None
                if service_method_exists and method_signature_compatible
                else "ENGINEERING_QA_METHOD_SIGNATURE_INVALID"
            ),
            binding_service_instance_exists=True,
            service_constructor_completed=True,
            service_method_exists=service_method_exists,
            method_signature_compatible=method_signature_compatible,
            dependency_container_resolved=dependency_resolved,
        )
        try:
            answer = service.answer(question)
        except InsufficientEvidence as error:
            raise ApplicationInsufficientEvidence() from error
        except EngineeringAnswerRejected as error:
            raise self._rejection(error) from error
        except ModelGatewayError as error:
            raise self._model_unavailable(error, snapshot) from error
        except (TypeError, ValueError, ValidationError) as error:
            record_binding_engineering_qa_stage(
                "B4_ENGINEERING_QA_EXCEPTION",
                "FAIL",
                error=error,
                exception_layer="EngineeringQABinding",
                exception_stage="ENGINEERING_QA_EXCEPTION",
            )
            wrapped = ApplicationAnswerRejected(
                validation_stage="ENGINEERING_QA_VALIDATION",
                gateway_invocation_count=0,
                provider_response_present=False,
            )
            attach_exception_diagnostics(
                wrapped,
                error,
                exception_layer="EngineeringQABinding",
                exception_stage="ENGINEERING_QA_VALIDATION",
            )
            raise wrapped from error
        except Exception as error:
            record_binding_engineering_qa_stage(
                "B4_ENGINEERING_QA_EXCEPTION", "FAIL", error=error,
                exception_layer="EngineeringQABinding",
                exception_stage="ENGINEERING_QA_EXCEPTION",
            )
            raise self._model_unavailable(
                error,
                snapshot,
                reason_code="ANALYSIS_RUNTIME_FAILED",
            ) from error
        record_binding_engineering_qa_not_reached(("B4_ENGINEERING_QA_EXCEPTION",))
        return self._evidence(
            question,
            snapshot,
            answer,
            requirement_context,
            project_mode=project_mode,
            answer_contract=self._answer_contract,
        )


    async def aanswer(
        self,
        request: ApplicationQuestionRequest,
        requirement_context: RequirementContext | None = None,
        *,
        project_mode: str | None = None,
        project_type: str | None = None,
    ) -> ApplicationAnswerEvidence:
        record_binding_engineering_qa_stage(
            "B0_BINDING_ANSWER_CALL_START",
            "PASS",
            dependency_container_resolved=all(
                hasattr(self, field)
                for field in ("_context", "_gateway", "_policy")
            ),
        )
        checked = _request(request)
        try:
            question, context_request, snapshot = self._load(checked)
        except Exception as error:
            record_binding_engineering_qa_stage(
                "B4_ENGINEERING_QA_EXCEPTION", "FAIL", error=error,
                exception_layer="EngineeringQABinding",
                exception_stage="CONTEXT_LOAD",
            )
            record_binding_engineering_qa_not_reached(
                (
                    "B1_ENGINEERING_QA_SERVICE_RESOLVE_START",
                    "B2_ENGINEERING_QA_SERVICE_RESOLVE_END",
                    "B3_ENGINEERING_QA_METHOD_CALL_START",
                )
            )
            record_engineering_qa_not_reached(
                (
                    "Q0_ENGINEERING_QA_ENTER",
                    "Q1_PROMPT_SERVICE_RESOLVE",
                    "Q2_PROMPT_BUILD_REQUEST",
                    "Q3_MODEL_REQUEST_CONSTRUCTION",
                    "Q4_ENGINEERING_QA_EXCEPTION",
                )
            )
            record_prompt_pipeline_not_reached(
                (
                    "P0_ENGINEERING_QA_SERVICE_ENTER",
                    "P1_TASK_REQUEST_CONSTRUCTION_START",
                    "P2_REQUIREMENT_CONTEXT_SERIALIZE",
                    "P3_GREENFIELD_PROMPT_SELECT",
                    "P4_PROMPT_BUILDER_CALL",
                    "P5_MODEL_REQUEST_CREATED",
                )
            )
            raise
        record_binding_engineering_qa_stage(
            "B1_ENGINEERING_QA_SERVICE_RESOLVE_START",
            "PASS",
            dependency_container_resolved=True,
        )
        try:
            service = EngineeringQAService(
                context_provider=_BoundContextProvider(context_request, snapshot),
                model_gateway=self._gateway,
                model_policy=self._policy,
                generation_options=self._generation_options,
                stage_id=self._stage_id,
                require_evidence=self._require_evidence,
                tenant_id=checked.tenant_id,
                answer_contract=self._answer_contract,
                requirement_context=(
                    requirement_context
                    if self._answer_contract == "GREENFIELD"
                    else None
                ),
                project_mode=project_mode,
                project_type=project_type,
            )
        except Exception as error:  # noqa: BLE001 - fixed, redacted boundary event
            record_binding_engineering_qa_stage(
                "B2_ENGINEERING_QA_SERVICE_RESOLVE_END", "FAIL", error=error,
                exception_layer="EngineeringQABinding",
                exception_stage="SERVICE_CONSTRUCTOR",
            )
            record_binding_engineering_qa_stage(
                "B4_ENGINEERING_QA_EXCEPTION", "FAIL", error=error,
                exception_layer="EngineeringQABinding",
                exception_stage="SERVICE_CONSTRUCTOR",
            )
            record_binding_engineering_qa_not_reached(
                ("B3_ENGINEERING_QA_METHOD_CALL_START",)
            )
            record_engineering_qa_stage(
                "Q0_ENGINEERING_QA_ENTER",
                "FAIL",
                error=error,
                engineering_qa_service_instance_available=False,
            )
            record_engineering_qa_stage(
                "Q1_PROMPT_SERVICE_RESOLVE",
                "FAIL",
                error=error,
                prompt_service_dependency_available=False,
                prompt_builder_dependency_available=False,
                request_builder_dependency_available=False,
            )
            record_engineering_qa_not_reached(
                (
                    "Q2_PROMPT_BUILD_REQUEST",
                    "Q3_MODEL_REQUEST_CONSTRUCTION",
                    "Q4_ENGINEERING_QA_EXCEPTION",
                )
            )
            record_prompt_pipeline_stage(
                "P0_ENGINEERING_QA_SERVICE_ENTER",
                "FAIL",
                error=error,
                engineering_qa_service_available=False,
            )
            record_prompt_pipeline_not_reached(
                (
                    "P1_TASK_REQUEST_CONSTRUCTION_START",
                    "P2_REQUIREMENT_CONTEXT_SERIALIZE",
                    "P3_GREENFIELD_PROMPT_SELECT",
                    "P4_PROMPT_BUILDER_CALL",
                    "P5_MODEL_REQUEST_CREATED",
                )
            )
            record_application_runtime_stage(
                "A6_PROMPT_REQUEST_BUILD", "FAIL", error=error
            )
            record_application_runtime_not_reached(("A7_PROMPT_BUILDER_ENTER",))
            raise
        service_method_exists, method_signature_compatible, dependency_resolved = (
            _engineering_qa_service_boundary_flags(service)
        )
        record_binding_engineering_qa_stage(
            "B2_ENGINEERING_QA_SERVICE_RESOLVE_END",
            "PASS",
            binding_service_instance_exists=True,
            service_constructor_completed=True,
            service_method_exists=service_method_exists,
            method_signature_compatible=method_signature_compatible,
            dependency_container_resolved=dependency_resolved,
        )
        if not self._provider_is_configured():
            self._raise_model_unavailable(snapshot)
        record_binding_engineering_qa_stage(
            "B3_ENGINEERING_QA_METHOD_CALL_START",
            "PASS"
            if service_method_exists and method_signature_compatible
            else "FAIL",
            reason_code=(
                None
                if service_method_exists and method_signature_compatible
                else "ENGINEERING_QA_METHOD_SIGNATURE_INVALID"
            ),
            binding_service_instance_exists=True,
            service_constructor_completed=True,
            service_method_exists=service_method_exists,
            method_signature_compatible=method_signature_compatible,
            dependency_container_resolved=dependency_resolved,
        )
        try:
            answer = await service.aanswer(question)
        except asyncio.CancelledError:
            raise
        except InsufficientEvidence as error:
            raise ApplicationInsufficientEvidence() from error
        except EngineeringAnswerRejected as error:
            raise self._rejection(error) from error
        except ModelGatewayError as error:
            raise self._model_unavailable(error, snapshot) from error
        except (TypeError, ValueError, ValidationError) as error:
            record_binding_engineering_qa_stage(
                "B4_ENGINEERING_QA_EXCEPTION",
                "FAIL",
                error=error,
                exception_layer="EngineeringQABinding",
                exception_stage="ENGINEERING_QA_EXCEPTION",
            )
            wrapped = ApplicationAnswerRejected(
                validation_stage="ENGINEERING_QA_VALIDATION",
                gateway_invocation_count=0,
                provider_response_present=False,
            )
            attach_exception_diagnostics(
                wrapped,
                error,
                exception_layer="EngineeringQABinding",
                exception_stage="ENGINEERING_QA_VALIDATION",
            )
            raise wrapped from error
        except Exception as error:
            record_binding_engineering_qa_stage(
                "B4_ENGINEERING_QA_EXCEPTION", "FAIL", error=error
            )
            raise self._model_unavailable(
                error,
                snapshot,
                reason_code="ANALYSIS_RUNTIME_FAILED",
            ) from error
        record_binding_engineering_qa_not_reached(("B4_ENGINEERING_QA_EXCEPTION",))
        return self._evidence(
            question,
            snapshot,
            answer,
            requirement_context,
            project_mode=project_mode,
            answer_contract=self._answer_contract,
        )


def _firmware_analysis(value: object, snapshot: EngineeringContextSnapshot) -> dict[str, object]:
    material = {
        field: getattr(value, field, None)
        for field in ("facts", "evidence", "analysis", "confidence", "limitations")
    }
    for field in ("facts", "evidence", "analysis", "limitations"):
        values = material[field]
        if not isinstance(values, tuple) or not all(
            isinstance(item, str) and item.strip() for item in values
        ):
            raise ApplicationAnswerRejected()
    confidence = material["confidence"]
    if type(confidence) is not float or not 0.0 <= confidence <= 1.0:
        raise ApplicationAnswerRejected()
    evidence_ids = {
        item.evidence_id
        for item in (
            *snapshot.memory_evidence,
            *snapshot.graph_evidence,
            *snapshot.knowledge_evidence,
            *snapshot.multimodal_evidence,
        )
    }
    if not material["evidence"] or not set(material["evidence"]).issubset(evidence_ids):
        raise ApplicationAnswerRejected()
    return material


class FirmwareQABinding(EngineeringQABinding):
    """Existing FirmwareAgent analysis followed by the single model answer path."""

    __slots__ = ("_firmware_agent", "_firmware_prompt")

    def __init__(
        self,
        *,
        context_provider: EngineeringContextProviderPort,
        model_gateway: ModelGatewayPort | None = None,
        model_policy: ModelPolicy | None = None,
        generation_options: ModelInvocationGenerationOptions | None = None,
        model_invocation_service: ModelInvocationPort | None = None,
        firmware_agent: ApplicationFirmwareAgentPort,
        firmware_prompt: ApplicationFirmwarePromptPort,
    ) -> None:
        super().__init__(
            context_provider=context_provider,
            model_gateway=model_gateway,
            model_policy=model_policy,
            generation_options=generation_options,
            model_invocation_service=model_invocation_service,
            stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT,
            task_type="FIRMWARE",
        )
        if not isinstance(firmware_agent, ApplicationFirmwareAgentPort):
            raise TypeError("firmware agent is invalid")
        if not isinstance(firmware_prompt, ApplicationFirmwarePromptPort):
            raise TypeError("firmware prompt is invalid")
        self._firmware_agent = firmware_agent
        self._firmware_prompt = firmware_prompt

    def answer(self, request: ApplicationQuestionRequest) -> ApplicationAnswerEvidence:
        checked = _request(request)
        question, _, snapshot = self._load(checked, require_evidence=False)
        agent_result = self._firmware_agent.analyze(checked, snapshot)
        specialist_evidence = getattr(agent_result, "context_evidence", ())
        try:
            EvidenceGate().require(
                specialist_evidence,
                tenant_id=checked.tenant_id,
                project_id=checked.project_id,
            )
        except EvidenceGateRejected as error:
            raise ApplicationInsufficientEvidence() from error
        snapshot = self._with_firmware_evidence(
            snapshot, specialist_evidence
        )
        analysis = _firmware_analysis(agent_result, snapshot)
        if not self._provider_is_configured():
            self._raise_model_unavailable(snapshot)
        try:
            response = self._gateway.generate(
                self._firmware_prompt.request_for(question, snapshot, analysis, self._policy, **({"generation_options": self._generation_options} if self._generation_options is not None else {}))
            )
            answer = self._evidence(question, snapshot, self._answer(question, snapshot, response))
            logger.info(
                "firmware_model_gateway_completed",
                extra={
                    "event_name": "model_gateway_completed",
                    "trace_id": checked.trace_id,
                    "project_id": checked.project_id,
                    "provider": answer.answer.model_info.provider,
                    "model": answer.answer.model_info.model_name,
                    "evidence_count": len(specialist_evidence),
                    "request_completed": True,
                },
            )
            return answer
        except ModelGatewayError as error:
            raise self._model_unavailable(error, snapshot) from error
        except (TypeError, ValueError, ValidationError) as error:
            raise ApplicationAnswerRejected() from error

    async def aanswer(
        self, request: ApplicationQuestionRequest
    ) -> ApplicationAnswerEvidence:
        checked = _request(request)
        question, _, snapshot = self._load(checked, require_evidence=False)
        agent_result = await self._firmware_agent.aanalyze(checked, snapshot)
        specialist_evidence = getattr(agent_result, "context_evidence", ())
        try:
            EvidenceGate().require(
                specialist_evidence,
                tenant_id=checked.tenant_id,
                project_id=checked.project_id,
            )
        except EvidenceGateRejected as error:
            raise ApplicationInsufficientEvidence() from error
        snapshot = self._with_firmware_evidence(
            snapshot, specialist_evidence
        )
        analysis = _firmware_analysis(agent_result, snapshot)
        if not self._provider_is_configured():
            self._raise_model_unavailable(snapshot)
        try:
            response = await self._gateway.agenerate(
                self._firmware_prompt.request_for(question, snapshot, analysis, self._policy, **({"generation_options": self._generation_options} if self._generation_options is not None else {}))
            )
            answer = self._evidence(question, snapshot, self._answer(question, snapshot, response))
            logger.info(
                "firmware_model_gateway_completed",
                extra={
                    "event_name": "model_gateway_completed",
                    "trace_id": checked.trace_id,
                    "project_id": checked.project_id,
                    "provider": answer.answer.model_info.provider,
                    "model": answer.answer.model_info.model_name,
                    "evidence_count": len(specialist_evidence),
                    "request_completed": True,
                },
            )
            return answer
        except asyncio.CancelledError:
            raise
        except ModelGatewayError as error:
            raise self._model_unavailable(error, snapshot) from error
        except (TypeError, ValueError, ValidationError) as error:
            raise ApplicationAnswerRejected() from error

    @staticmethod
    def _answer(
        question: EngineeringQuestion,
        snapshot: EngineeringContextSnapshot,
        response: object,
    ) -> EngineeringAnswer:
        checked = validate_model_response(response)
        if checked.provenance != snapshot.provenance:
            raise ApplicationAnswerRejected()
        refs = tuple(
            sorted(
                item.evidence_id
                for item in (
                    *snapshot.memory_evidence,
                    *snapshot.graph_evidence,
                    *snapshot.knowledge_evidence,
                    *snapshot.multimodal_evidence,
                )
            )
        )
        confidence = (
            snapshot.confidence
            if checked.confidence is None
            else min(snapshot.confidence, checked.confidence)
        )
        return EngineeringAnswer(
            question_id=question.question_id,
            content=checked.content,
            confidence=confidence,
            evidence_refs=refs,
            model_info=ModelInfo(
                provider=checked.provider,
                model_name=checked.model_name,
                finish_reason=checked.finish_reason,
                usage=checked.usage,
            ),
        )


class DebugQABinding(EngineeringQABinding):
    """Existing DebugAgent analysis followed by the single model answer path."""

    __slots__ = ("_debug_agent", "_debug_prompt")

    def __init__(
        self,
        *,
        context_provider: EngineeringContextProviderPort,
        model_gateway: ModelGatewayPort | None = None,
        model_policy: ModelPolicy | None = None,
        generation_options: ModelInvocationGenerationOptions | None = None,
        model_invocation_service: ModelInvocationPort | None = None,
        debug_agent: ApplicationDebugAgentPort,
        debug_prompt: ApplicationDebugPromptPort,
    ) -> None:
        super().__init__(
            context_provider=context_provider,
            model_gateway=model_gateway,
            model_policy=model_policy,
            generation_options=generation_options,
            model_invocation_service=model_invocation_service,
            stage_id=EngineeringStage.DEBUGGING,
            task_type="DEBUG",
        )
        if not isinstance(debug_agent, ApplicationDebugAgentPort):
            raise TypeError("debug agent is invalid")
        if not isinstance(debug_prompt, ApplicationDebugPromptPort):
            raise TypeError("debug prompt is invalid")
        self._debug_agent = debug_agent
        self._debug_prompt = debug_prompt

    @staticmethod
    def _with_debug_evidence(
        snapshot: EngineeringContextSnapshot, values: object
    ) -> EngineeringContextSnapshot:
        if not isinstance(values, tuple):
            raise ApplicationContextUnavailable()
        existing = {item.evidence_id: item for item in snapshot.knowledge_evidence}
        for item in values:
            if getattr(item, "source_type", None) is not ContextSourceType.DEBUG:
                raise ApplicationAnswerRejected()
            if item.project_id != snapshot.project_id or item.tenant_id != snapshot.tenant_id:
                raise ApplicationContextUnavailable()
            previous = existing.get(item.evidence_id)
            if previous is not None and previous.fingerprint != item.fingerprint:
                raise ApplicationContextUnavailable()
            existing[item.evidence_id] = item
        combined = tuple(existing[key] for key in sorted(existing))
        evidence = (
            *snapshot.memory_evidence,
            *snapshot.graph_evidence,
            *combined,
            *snapshot.multimodal_evidence,
        )
        if not evidence:
            raise ApplicationInsufficientEvidence()
        confidence_values = tuple(item.confidence for item in evidence if item.confidence is not None)
        return EngineeringContextSnapshot.create(
            project_id=snapshot.project_id,
            tenant_id=snapshot.tenant_id,
            task_description=snapshot.task_description,
            memory_evidence=snapshot.memory_evidence,
            graph_evidence=snapshot.graph_evidence,
            knowledge_evidence=combined,
            multimodal_evidence=snapshot.multimodal_evidence,
            conflicts=snapshot.conflicts,
            confidence=min(confidence_values) if confidence_values else 0.0,
            provenance=tuple(item.source() for item in evidence),
            verification_status=snapshot.verification_status,
        )

    @staticmethod
    def _analysis(value: object, snapshot: EngineeringContextSnapshot) -> dict[str, object]:
        material = {
            field: getattr(value, field, None)
            for field in ("facts", "evidence", "analysis", "confidence", "limitations")
        }
        for field in ("facts", "evidence", "analysis", "limitations"):
            values = material[field]
            if not isinstance(values, tuple) or not all(isinstance(item, str) and item.strip() for item in values):
                raise ApplicationAnswerRejected()
        if type(material["confidence"]) is not float or not 0.0 <= material["confidence"] <= 1.0:
            raise ApplicationAnswerRejected()
        evidence_ids = {
            item.evidence_id
            for item in (
                *snapshot.memory_evidence,
                *snapshot.graph_evidence,
                *snapshot.knowledge_evidence,
                *snapshot.multimodal_evidence,
            )
        }
        if not material["evidence"] or not set(material["evidence"]).issubset(evidence_ids):
            raise ApplicationAnswerRejected()
        return material

    @staticmethod
    def _answer(
        question: EngineeringQuestion,
        snapshot: EngineeringContextSnapshot,
        response: object,
    ) -> EngineeringAnswer:
        checked = validate_model_response(response)
        if checked.provenance != snapshot.provenance:
            raise ApplicationAnswerRejected()
        refs = tuple(
            sorted(
                item.evidence_id
                for item in (
                    *snapshot.memory_evidence,
                    *snapshot.graph_evidence,
                    *snapshot.knowledge_evidence,
                    *snapshot.multimodal_evidence,
                )
            )
        )
        confidence = (
            snapshot.confidence
            if checked.confidence is None
            else min(snapshot.confidence, checked.confidence)
        )
        return EngineeringAnswer(
            question_id=question.question_id,
            content=checked.content,
            confidence=confidence,
            evidence_refs=refs,
            model_info=ModelInfo(
                provider=checked.provider,
                model_name=checked.model_name,
                finish_reason=checked.finish_reason,
                usage=checked.usage,
            ),
        )

    def answer(self, request: ApplicationQuestionRequest) -> ApplicationAnswerEvidence:
        checked = _request(request)
        question, _, snapshot = self._load(checked, require_evidence=False)
        agent_result = self._debug_agent.analyze(checked, snapshot)
        specialist_evidence = getattr(agent_result, "context_evidence", ())
        try:
            EvidenceGate().require(
                specialist_evidence,
                tenant_id=checked.tenant_id,
                project_id=checked.project_id,
            )
        except EvidenceGateRejected as error:
            raise ApplicationInsufficientEvidence() from error
        snapshot = self._with_debug_evidence(snapshot, specialist_evidence)
        analysis = self._analysis(agent_result, snapshot)
        try:
            response = self._gateway.generate(self._debug_prompt.request_for(question, snapshot, analysis, self._policy, **({"generation_options": self._generation_options} if self._generation_options is not None else {})))
            answer = self._evidence(question, snapshot, self._answer(question, snapshot, response))
            logger.info(
                "debug_model_gateway_completed",
                extra={
                    "event_name": "model_gateway_completed",
                    "trace_id": checked.trace_id,
                    "project_id": checked.project_id,
                    "provider": answer.answer.model_info.provider,
                    "model": answer.answer.model_info.model_name,
                    "evidence_count": len(specialist_evidence),
                    "request_completed": True,
                },
            )
            return answer
        except ModelGatewayError as error:
            raise self._model_unavailable(error, snapshot) from error
        except (TypeError, ValueError, ValidationError) as error:
            raise ApplicationAnswerRejected() from error

    async def aanswer(self, request: ApplicationQuestionRequest) -> ApplicationAnswerEvidence:
        checked = _request(request)
        question, _, snapshot = self._load(checked, require_evidence=False)
        agent_result = await self._debug_agent.aanalyze(checked, snapshot)
        specialist_evidence = getattr(agent_result, "context_evidence", ())
        try:
            EvidenceGate().require(
                specialist_evidence,
                tenant_id=checked.tenant_id,
                project_id=checked.project_id,
            )
        except EvidenceGateRejected as error:
            raise ApplicationInsufficientEvidence() from error
        snapshot = self._with_debug_evidence(snapshot, specialist_evidence)
        analysis = self._analysis(agent_result, snapshot)
        try:
            response = await self._gateway.agenerate(self._debug_prompt.request_for(question, snapshot, analysis, self._policy, **({"generation_options": self._generation_options} if self._generation_options is not None else {})))
            answer = self._evidence(question, snapshot, self._answer(question, snapshot, response))
            logger.info(
                "debug_model_gateway_completed",
                extra={
                    "event_name": "model_gateway_completed",
                    "trace_id": checked.trace_id,
                    "project_id": checked.project_id,
                    "provider": answer.answer.model_info.provider,
                    "model": answer.answer.model_info.model_name,
                    "evidence_count": len(specialist_evidence),
                    "request_completed": True,
                },
            )
            return answer
        except asyncio.CancelledError:
            raise
        except ModelGatewayError as error:
            raise self._model_unavailable(error, snapshot) from error
        except (TypeError, ValueError, ValidationError) as error:
            raise ApplicationAnswerRejected() from error

__all__ = ("CanonicalSupervisorBinding", "DebugQABinding", "EngineeringQABinding", "FirmwareQABinding")
