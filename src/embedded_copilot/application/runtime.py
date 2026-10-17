from __future__ import annotations

import asyncio
import copy
from datetime import datetime

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.engineering_copilot import (
    ArchitectureDesignStatus,
    DesignTruth,
    PlanStatus,
    SchematicPlan,
    approve_architecture_design,
    approve_engineering_plan,
    generate_design_truth,
    is_architecture_approval_request,
    is_architecture_generation_request,
    transition_architecture_design,
    transition_engineering_plan,
)
from embedded_copilot.engineering_qa import (
    EngineeringAnswer,
    EngineeringQuestion,
    ModelInfo,
)
from embedded_copilot.engineering_requirements import (
    RequirementContext,
    RequirementLifecycle,
    RequirementStatus,
    build_engineering_plan,
    generate_architecture_design,
    is_plan_approval_request,
    merge_requirement_context,
)
from embedded_copilot.engineering_research import (
    EngineeringResearchService,
    EngineeringResearchSnapshot,
    RequirementDecisionAction,
    extract_reference_urls,
    is_requirement_change_feedback,
)
from embedded_copilot.engineering_runtime import CircuitIntentObject
from embedded_copilot.firmware_engineering import FirmwareBuildRequest
from embedded_copilot.runtime_diagnostics import (
    attach_exception_diagnostics,
    exception_diagnostics,
    record_application_runtime_not_reached,
    record_application_runtime_outer_not_reached,
    record_application_runtime_outer_stage,
    record_application_runtime_stage,
)

from .bindings import DebugQABinding, EngineeringQABinding, FirmwareQABinding
from .container import ApplicationDependencyContainer
from .engineering_runtime_integration import (
    CircuitIntentSpec,
    EngineeringObjectFlowResult,
    EngineeringObjectIntegrationPort,
    EvidenceReceiptReference,
)
from .exceptions import (
    ApplicationAnswerRejected,
    ApplicationAuditConflict,
    ApplicationAuditUnavailable,
    ApplicationContextUnavailable,
    ApplicationExternalEvidenceUnavailable,
    ApplicationInsufficientEvidence,
    ApplicationModelUnavailable,
)
from .models import (
    ApplicationAnswerEvidence,
    ApplicationAuditEvent,
    ApplicationAuditEventType,
    ApplicationErrorCode,
    ApplicationQuestionRequest,
    ApplicationRoute,
    ApplicationRouteDecision,
    ApplicationRoutingContext,
    ApplicationRunResult,
    ApplicationRunStatus,
)

_AUTO = "sha256:" + "0" * 64


class ApplicationRuntime:
    __slots__ = (
        "_answer_port",
        "_greenfield_answer_port",
        "_firmware_answer_port",
        "_debug_answer_port",
        "_container",
    )

    def __init__(self, container: ApplicationDependencyContainer) -> None:
        if type(container) is not ApplicationDependencyContainer:
            raise TypeError("application container is invalid")
        self._container = container
        self._answer_port = EngineeringQABinding(
            context_provider=container.context_provider,
            model_gateway=container.model_gateway,
            model_invocation_service=container.model_invocation_service,
            model_policy=container.model_policy,
            generation_options=container.generation_options,
            task_type="GENERAL_CONVERSATION",
        )
        self._greenfield_answer_port = self._answer_port
        self._greenfield_answer_port = EngineeringQABinding(
            context_provider=container.context_provider,
            model_gateway=container.model_gateway,
            model_invocation_service=container.model_invocation_service,
            model_policy=container.model_policy,
            generation_options=container.generation_options,
            task_type="REQUIREMENT_CONVERSATION",
            require_evidence=False,
            answer_contract="GREENFIELD",
        )
        self._firmware_answer_port = (
            None
            if container.firmware_agent is None
            else FirmwareQABinding(
                context_provider=container.context_provider,
                model_gateway=container.model_gateway,
                model_invocation_service=container.model_invocation_service,
                model_policy=container.model_policy,
                generation_options=container.generation_options,
                firmware_agent=container.firmware_agent,
                firmware_prompt=container.firmware_prompt,
            )
        )
        self._debug_answer_port = (
            None
            if container.debug_agent is None
            else DebugQABinding(
                context_provider=container.context_provider,
                model_gateway=container.model_gateway,
                model_invocation_service=container.model_invocation_service,
                model_policy=container.model_policy,
                generation_options=container.generation_options,
                debug_agent=container.debug_agent,
                debug_prompt=container.debug_prompt,
            )
        )

    @staticmethod
    def _request(value: object) -> ApplicationQuestionRequest:
        if type(value) is not ApplicationQuestionRequest:
            raise TypeError("application question is invalid")
        return ApplicationQuestionRequest.model_validate(
            copy.deepcopy(value.model_dump(mode="python"))
        )

    def _route(
        self,
        request: ApplicationQuestionRequest,
        routing_context: ApplicationRoutingContext | None = None,
    ) -> ApplicationRouteDecision:
        try:
            try:
                raw = self._container.agent_runtime.route(
                    copy.deepcopy(request), routing_context=routing_context
                )
            except TypeError:
                raw = self._container.agent_runtime.route(copy.deepcopy(request))
            checked = ApplicationRouteDecision.model_validate(
                copy.deepcopy(raw.model_dump(mode="python"))
            )
        except Exception:  # noqa: BLE001 - fail closed at an injected Port boundary
            return ApplicationRouteDecision.for_request(
                request, route=ApplicationRoute.CLARIFICATION_REQUIRED
            )
        if (
            checked.request_id != request.request_id
            or checked.request_fingerprint != request.fingerprint
        ):
            return ApplicationRouteDecision.for_request(
                request, route=ApplicationRoute.CLARIFICATION_REQUIRED
            )
        return checked

    def classify(
        self,
        request: ApplicationQuestionRequest,
        *,
        routing_context: ApplicationRoutingContext | None = None,
    ) -> ApplicationRouteDecision:
        """Return the deterministic route without preparing context or invoking a provider."""
        return self._route(self._request(request), routing_context)

    def create_firmware_build_operation(
        self,
        request: ApplicationQuestionRequest,
        build_request: FirmwareBuildRequest,
        *,
        target_id: str,
        target_fingerprint: str,
        workspace_snapshot_id: str,
        workspace_fingerprint: str,
        proposal_id: str,
        proposal_fingerprint: str,
        approval_nonce: str,
        expires_at: datetime,
        routing_context: ApplicationRoutingContext | None = None,
    ) -> object:
        """Create one governed firmware operation from an application action.

        The application question is only used for supervisor classification and
        correlation identity.  Build facts and proposal identity must already
        exist in the typed ``FirmwareBuildRequest``; this method does not infer
        or synthesize them from natural-language content.
        """

        checked = self._request(request)
        checked_build = FirmwareBuildRequest.model_validate(
            copy.deepcopy(build_request.model_dump(mode="python"))
        )
        if checked_build.project_id != checked.project_id:
            raise ValueError("firmware build project does not match application request")
        decision = self._route(checked, routing_context)
        if decision.route is not ApplicationRoute.FIRMWARE:
            raise ValueError("application request is not a firmware build action")
        bridge = self._container.firmware_build_execution_bridge
        create_operation = getattr(bridge, "create_operation", None)
        if not callable(create_operation):
            raise ValueError("firmware build execution bridge is unavailable")
        return create_operation(
            checked_build,
            tenant_id=checked.tenant_id,
            requester_id=checked.requester,
            target_id=target_id,
            target_fingerprint=target_fingerprint,
            workspace_snapshot_id=workspace_snapshot_id,
            workspace_fingerprint=workspace_fingerprint,
            proposal_id=proposal_id,
            proposal_fingerprint=proposal_fingerprint,
            approval_nonce=approval_nonce,
            expires_at=expires_at,
            application_request=checked,
        )

    def run_engineering_object_flow(
        self,
        request: ApplicationQuestionRequest,
        routing_context: ApplicationRoutingContext,
        circuit_intent: CircuitIntentSpec,
        evidence_reference: EvidenceReceiptReference,
        integration: EngineeringObjectIntegrationPort | None = None,
        reviewer: str | None = None,
        schematic_plan: SchematicPlan | None = None,
        approved_circuit_intent: CircuitIntentObject | None = None,
    ) -> EngineeringObjectFlowResult:
        """Route through Supervisor/Aevum, then invoke the explicit object adapter.

        This opt-in method is separate from ``answer()`` so existing
        conversational behavior cannot silently mutate engineering objects.
        """
        checked = self._request(request)
        if type(routing_context) is not ApplicationRoutingContext:
            raise TypeError("application routing context is invalid")
        if integration is None:
            factory = self._container.engineering_object_integration_factory
            if factory is None:
                raise TypeError("engineering object integration port is invalid")
            integration = factory(checked.project_id)
        if not isinstance(integration, EngineeringObjectIntegrationPort):
            raise TypeError("engineering object integration port is invalid")
        route = self._route(checked, routing_context)
        prepared = integration.prepare(
            checked,
            routing_context,
            circuit_intent,
            evidence_reference,
            route=route,
            schematic_plan=schematic_plan,
            approved_circuit_intent=approved_circuit_intent,
        )
        if reviewer is None:
            return prepared
        return integration.commit(integration.review(prepared, reviewer=reviewer))

    @staticmethod
    def _is_greenfield(route: ApplicationRoute) -> bool:
        return route in {
            ApplicationRoute.GREENFIELD,
            ApplicationRoute.GREENFIELD_DISCOVERY,
            ApplicationRoute.GREENFIELD_DESIGN,
        }

    @classmethod
    def _evidence_project_mode(
        cls,
        route: ApplicationRoute,
        routing_context: ApplicationRoutingContext | None,
    ) -> str:
        raw = None if routing_context is None else routing_context.project_creation_mode
        if isinstance(raw, str) and raw.strip():
            return "CREATE_NEW" if raw.strip().upper() == "CREATE_NEW" else "BROWNFIELD"
        return "CREATE_NEW" if cls._is_greenfield(route) else "BROWNFIELD"

    @classmethod
    def _greenfield_context(
        cls,
        request: ApplicationQuestionRequest,
        route: ApplicationRoute,
        routing_context: ApplicationRoutingContext | None,
    ) -> RequirementContext:
        previous = None if routing_context is None else routing_context.requirement_context
        if (
            previous is not None
            and previous.effective_status is RequirementStatus.CONFIRMED
            and is_plan_approval_request(request.question)
        ):
            return RequirementContext.model_validate(
                copy.deepcopy(previous.model_dump(mode="python")), strict=False
            )
        if previous is not None and is_requirement_change_feedback(request.question):
            return RequirementContext.model_validate(
                copy.deepcopy(previous.model_dump(mode="python")), strict=False
            )
        conversation_id = (
            request.project_id
            if routing_context is None or routing_context.conversation_id is None
            else routing_context.conversation_id
        )
        normalized = " ".join(request.question.casefold().split())
        report_continuation = normalized in {
            "好",
            "好的",
            "继续",
            "可以",
            "下一步",
            "是",
            "确认",
            "确认生成",
            "确认生成报告",
            "开始生成",
            "开始生成报告",
            "生成吧",
            "请生成报告",
        }
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
        lifecycle = (
            previous.lifecycle
            if previous is not None
            and previous.lifecycle in {
                RequirementLifecycle.REQUIREMENTS_READY,
                RequirementLifecycle.DESIGN_READY,
            }
            and report_continuation
            else None
        )
        return merge_requirement_context(
            previous,
            project_id=request.project_id,
            conversation_id=conversation_id,
            turn_id=request.request_id,
            utterance=request.question,
            project_type=None if routing_context is None else routing_context.project_type,
            lifecycle=lifecycle,
            confirmation_requested=explicit_confirmation,
        )

    @classmethod
    def _research_for_turn(
        cls,
        request: ApplicationQuestionRequest,
        route: ApplicationRoute,
        routing_context: ApplicationRoutingContext | None,
        service: EngineeringResearchService | None,
    ) -> EngineeringResearchSnapshot | None:
        if not cls._is_greenfield(route) or service is None:
            return None if routing_context is None else routing_context.research_snapshot
        direct_urls = extract_reference_urls(request.question)
        if routing_context is not None and routing_context.research_snapshot is not None and not direct_urls:
            return routing_context.research_snapshot
        conversation_id = (
            request.project_id
            if routing_context is None or routing_context.conversation_id is None
            else routing_context.conversation_id
        )
        brief = service.build_initial_requirement_brief(
            project_id=request.project_id,
            conversation_id=conversation_id,
            turn_id=request.request_id,
            utterance=request.question,
        )
        return service.research(brief, reference_urls=direct_urls)

    @staticmethod
    def _attach_research(
        evidence: ApplicationAnswerEvidence,
        snapshot: EngineeringResearchSnapshot | None,
    ) -> ApplicationAnswerEvidence:
        if snapshot is None:
            return evidence
        pending = tuple(
            item for item in snapshot.suggestions
            if item.user_decision is RequirementDecisionAction.PENDING
        )
        if not pending:
            return evidence
        questions = "\n".join(
            f"{index}. {item.question_to_user or item.suggested_requirement}"
            for index, item in enumerate(pending, start=1)
        )
        summary = (
            "\n\n参考项目调研发现以下需求仍需用户确认（仅为带来源的候选，不是工程事实）：\n"
            f"{questions}\n请通过 ACCEPT、REJECT、MODIFY 或 DEFER 确认。"
        )
        answer_values = evidence.answer.model_dump(mode="python")
        answer_values.update(
            {"content": f"{evidence.answer.content}{summary}"[:16384], "answer_id": "", "fingerprint": _AUTO}
        )
        answer = EngineeringAnswer.model_validate(answer_values, strict=False)
        return evidence.model_copy(update={"answer": answer, "fingerprint": _AUTO})

    @classmethod
    def _plan_for_turn(
        cls,
        request: ApplicationQuestionRequest,
        requirement_context: RequirementContext,
        routing_context: ApplicationRoutingContext | None,
    ):
        previous_plan = None if routing_context is None else routing_context.engineering_plan
        previous_review = None if routing_context is None else routing_context.engineering_plan_review
        plan = previous_plan
        review = previous_review
        if plan is not None and plan.context_fingerprint != requirement_context.fingerprint:
            if plan.status not in {PlanStatus.SUPERSEDED, PlanStatus.COMPLETED}:
                plan = transition_engineering_plan(plan, PlanStatus.SUPERSEDED)
            review = None
        if requirement_context.effective_status is RequirementStatus.CONFIRMED:
            if (
                is_plan_approval_request(request.question)
                and plan is not None
                and plan.context_fingerprint == requirement_context.fingerprint
                and plan.status in {PlanStatus.PROPOSED, PlanStatus.REVIEWING}
            ):
                plan, review = approve_engineering_plan(
                    plan,
                    reviewer=request.requester,
                    decided_at=request.requested_at,
                )
            elif plan is None or plan.status is PlanStatus.SUPERSEDED:
                try:
                    plan = build_engineering_plan(requirement_context, None)
                except ValueError as error:
                    if "completeness gate" not in str(error):
                        raise
                    return None, None
                review = None
        return plan, review

    @classmethod
    def _architecture_for_turn(
        cls,
        request: ApplicationQuestionRequest,
        requirement_context: RequirementContext,
        engineering_plan,
        routing_context: ApplicationRoutingContext | None,
    ):
        """Resolve only the architecture proposal/review state for this turn."""

        previous = None if routing_context is None else routing_context.architecture_design
        previous_review = None if routing_context is None else routing_context.architecture_review
        design = previous
        review = previous_review
        if design is not None and (
            design.context_fingerprint != requirement_context.fingerprint
            or engineering_plan is None
            or design.plan_id != engineering_plan.plan_id
            or design.plan_fingerprint != engineering_plan.fingerprint
        ):
            if design.status not in {
                ArchitectureDesignStatus.SUPERSEDED,
                ArchitectureDesignStatus.INVALIDATED,
            }:
                design = transition_architecture_design(design, ArchitectureDesignStatus.SUPERSEDED)
            review = None
        if engineering_plan is None or engineering_plan.status is not PlanStatus.APPROVED:
            return design, review
        if is_architecture_approval_request(request.question):
            if design is None or design.status not in {
                ArchitectureDesignStatus.PROPOSED,
                ArchitectureDesignStatus.REVIEWING,
            }:
                return design, review
            return approve_architecture_design(
                design,
                reviewer=request.requester,
                decided_at=request.requested_at,
            )
        if is_architecture_generation_request(request.question) and (
            design is None
            or design.status
            in {
                ArchitectureDesignStatus.SUPERSEDED,
                ArchitectureDesignStatus.INVALIDATED,
            }
        ):
            design = generate_architecture_design(requirement_context, engineering_plan)
            review = None
        return design, review

    @staticmethod
    def _design_truth_for_turn(
        architecture_design, conversation_id: str | None = None
    ) -> DesignTruth | None:
        if architecture_design is None or architecture_design.status is not ArchitectureDesignStatus.APPROVED:
            return None
        return generate_design_truth(
            architecture_design, conversation_id=conversation_id
        )

    @classmethod
    def _merge_provider_requirements(
        cls,
        request: ApplicationQuestionRequest,
        route: ApplicationRoute,
        evidence: ApplicationAnswerEvidence,
        routing_context: ApplicationRoutingContext | None,
    ) -> ApplicationAnswerEvidence:
        if not cls._is_greenfield(route):
            return evidence
        if not evidence.answer.requirement_proposals and not evidence.answer.open_questions:
            return evidence
        base = evidence.requirement_context or cls._greenfield_context(
            request, route, routing_context
        )
        merged = merge_requirement_context(
            base,
            project_id=request.project_id,
            conversation_id=base.conversation_id,
            turn_id=request.request_id,
            utterance="provider proposal",
            proposals=tuple(
                {"category": item.category, "value": item.value}
                for item in evidence.answer.requirement_proposals
            ),
            provider_questions=tuple(
                {"category": item.category, "question": item.question}
                for item in evidence.answer.open_questions
            ),
            lifecycle=base.lifecycle,
        )
        return evidence.model_copy(
            update={"requirement_context": merged, "fingerprint": _AUTO}
        )

    @staticmethod
    def _attach_plan(
        evidence: ApplicationAnswerEvidence,
        engineering_plan,
        engineering_plan_review,
    ) -> ApplicationAnswerEvidence:
        answer = evidence.answer
        if engineering_plan is not None and engineering_plan.status in {
            PlanStatus.PROPOSED,
            PlanStatus.REVIEWING,
        }:
            phase_names = "、".join(phase.name for phase in engineering_plan.phases)
            summary = (
                f"工程计划摘要：{engineering_plan.title}\n"
                f"阶段：{phase_names}\n"
                "当前计划等待用户审批。请回复“批准计划”或“同意该计划”后进入架构设计。"
            )
            answer_values = answer.model_dump(mode="python")
            answer_values.update(
                {
                    "content": f"{answer.content}\n\n{summary}"[:16384],
                    "answer_id": "",
                    "fingerprint": _AUTO,
                }
            )
            answer = EngineeringAnswer.model_validate(answer_values, strict=False)
        return ApplicationAnswerEvidence.model_validate(
            evidence.model_copy(
                update={
                    "answer": answer,
                    "engineering_plan": engineering_plan,
                    "engineering_plan_review": engineering_plan_review,
                    "fingerprint": _AUTO,
                }
            ).model_dump(mode="python"),
            strict=False,
        )

    @staticmethod
    def _attach_architecture(
        evidence: ApplicationAnswerEvidence,
        architecture_design,
        architecture_review,
        design_truth: DesignTruth | None = None,
    ) -> ApplicationAnswerEvidence:
        answer = evidence.answer
        if architecture_design is not None and architecture_design.status in {
            ArchitectureDesignStatus.PROPOSED,
            ArchitectureDesignStatus.REVIEWING,
        }:
            summary = (
                f"系统架构摘要：{architecture_design.title}\n"
                f"组件：{'、'.join(item.name for item in architecture_design.system_components)}\n"
                "当前架构等待用户审批。请回复“批准架构”或“同意该架构”。"
            )
            answer_values = answer.model_dump(mode="python")
            answer_values.update(
                {
                    "content": f"{answer.content}\n\n{summary}"[:16384],
                    "answer_id": "",
                    "fingerprint": _AUTO,
                }
            )
            answer = EngineeringAnswer.model_validate(answer_values, strict=False)
        return ApplicationAnswerEvidence.model_validate(
            evidence.model_copy(
                update={
                    "answer": answer,
                    "architecture_design": architecture_design,
                    "architecture_review": architecture_review,
                    "design_truth": design_truth,
                    "fingerprint": _AUTO,
                }
            ).model_dump(mode="python"),
            strict=False,
        )

    @staticmethod
    def _architecture_approval_evidence(
        request: ApplicationQuestionRequest,
        requirement_context: RequirementContext | None,
        engineering_plan,
        engineering_plan_review,
        architecture_design,
        architecture_review,
        design_truth: DesignTruth | None = None,
    ) -> ApplicationAnswerEvidence:
        """Build a local state-transition acknowledgement without a provider call."""

        if (
            requirement_context is None
            or engineering_plan is None
            or architecture_design is None
            or architecture_review is None
        ):
            raise ValueError("architecture approval evidence requires current state")
        question = EngineeringQuestion(
            question_id=request.request_id,
            project_id=request.project_id,
            question=request.question,
            constraints=request.constraints,
        )
        context = EngineeringContextSnapshot.create(
            project_id=request.project_id,
            tenant_id=request.tenant_id,
            task_description=request.question,
            memory_evidence=(),
            graph_evidence=(),
            knowledge_evidence=(),
            multimodal_evidence=(),
            conflicts=(),
            confidence=1.0,
            provenance=(),
            verification_status="UNVERIFIED",
        )
        answer = EngineeringAnswer(
            question_id=question.question_id,
            content="系统架构已批准，并已生成当前 Design Truth 快照。当前没有原理图、PCB 或 Firmware 证据时，不会补造引脚和固件映射；后续工程设计需由用户明确请求。",
            confidence=1.0,
            evidence_refs=(),
            model_info=ModelInfo(
                provider="deterministic",
                model_name="architecture-service",
                finish_reason="STATE_TRANSITION",
            ),
        )
        return ApplicationAnswerEvidence(
            question=question,
            context=context,
            answer=answer,
            requirement_context=requirement_context,
            engineering_plan=engineering_plan,
            engineering_plan_review=engineering_plan_review,
            architecture_design=architecture_design,
            architecture_review=architecture_review,
            design_truth=design_truth,
        )

    @staticmethod
    def _failure_code(error: Exception) -> ApplicationErrorCode:
        if isinstance(error, ApplicationExternalEvidenceUnavailable):
            return ApplicationErrorCode.EXTERNAL_EVIDENCE_UNAVAILABLE
        if isinstance(error, ApplicationInsufficientEvidence):
            if error.reason_code == ApplicationErrorCode.EMPTY_PROJECT.value:
                return ApplicationErrorCode.EMPTY_PROJECT
            if error.reason_code == ApplicationErrorCode.NO_ANALYZABLE_SOURCE.value:
                return ApplicationErrorCode.NO_ANALYZABLE_SOURCE
            return ApplicationErrorCode.INSUFFICIENT_EVIDENCE
        if isinstance(error, ApplicationContextUnavailable):
            return ApplicationErrorCode.CONTEXT_UNAVAILABLE
        if isinstance(error, ApplicationModelUnavailable):
            if error.reason_code in {
                "PROVIDER_AUTH_FAILED",
                "PROVIDER_NETWORK_UNAVAILABLE",
                "PROVIDER_MODEL_UNAVAILABLE",
                "PROVIDER_CONFIG_INVALID",
                "PROVIDER_TRANSPORT_FAILED",
                "PROVIDER_PARSE_FAILED",
                "ANSWER_EMPTY",
                "ANSWER_SCHEMA_INVALID",
                "ANSWER_CONTRACT_MISMATCH",
                "ANSWER_REJECTED_UNSAFE",
                "ANSWER_REJECTED_EVIDENCE_MISMATCH",
                "PROVIDER_CONNECTION_UNVERIFIED",
                "PROVIDER_UNAVAILABLE",
                "ANALYSIS_RUNTIME_FAILED",
            }:
                return ApplicationErrorCode(error.reason_code)
            return (
                ApplicationErrorCode.MODEL_UNAVAILABLE
                if error.configured
                else ApplicationErrorCode.PROVIDER_NOT_CONFIGURED
            )
        if isinstance(error, ApplicationAnswerRejected):
            if error.reason_code in {
                "ANSWER_SCHEMA_INVALID",
                "ANSWER_CONTRACT_MISMATCH",
                "ANSWER_EMPTY",
                "ANSWER_REJECTED_UNSAFE",
                "ANSWER_REJECTED_EVIDENCE_MISMATCH",
            }:
                return ApplicationErrorCode(error.reason_code)
            return ApplicationErrorCode.ANSWER_REJECTED
        return ApplicationErrorCode.APPLICATION_UNAVAILABLE

    @staticmethod
    def _event_type(status: ApplicationRunStatus) -> ApplicationAuditEventType:
        return {
            ApplicationRunStatus.COMPLETED: ApplicationAuditEventType.QUESTION_ANSWERED,
            ApplicationRunStatus.REJECTED: ApplicationAuditEventType.QUESTION_REJECTED,
            ApplicationRunStatus.FAILED: ApplicationAuditEventType.QUESTION_FAILED,
        }[status]

    def _audit(
        self,
        *,
        request: ApplicationQuestionRequest,
        route: ApplicationRoute,
        status: ApplicationRunStatus,
        evidence: ApplicationAnswerEvidence | None,
        error_code: ApplicationErrorCode | None,
        context=None,
        requirement_context: RequirementContext | None = None,
    ) -> ApplicationAuditEvent:
        event_type = self._event_type(status)
        event_id = ApplicationAuditEvent.event_id_for(request.fingerprint, event_type)
        context_fingerprint = evidence.context.fingerprint if evidence else (
            None if context is None else context.fingerprint
        )
        answer_fingerprint = evidence.answer.fingerprint if evidence else None
        model_info_fingerprint = evidence.answer.model_info.fingerprint if evidence else None
        evidence_refs = evidence.answer.evidence_refs if evidence else ()
        try:
            existing_raw = self._container.audit_sink.get(event_id)
        except Exception as error:
            raise ApplicationAuditUnavailable() from error
        if existing_raw is not None:
            try:
                existing = ApplicationAuditEvent.model_validate(
                    copy.deepcopy(existing_raw.model_dump(mode="python"))
                )
            except Exception as error:
                raise ApplicationAuditUnavailable() from error
            expected = (
                event_type,
                request.request_id,
                request.trace_id,
                request.project_id,
                request.requester,
                route,
                status,
                request.fingerprint,
                context_fingerprint,
                answer_fingerprint,
                model_info_fingerprint,
                evidence_refs,
                error_code,
            )
            actual = (
                existing.event_type,
                existing.request_id,
                existing.trace_id,
                existing.project_id,
                existing.requester,
                existing.route,
                existing.status,
                existing.question_fingerprint,
                existing.context_fingerprint,
                existing.answer_fingerprint,
                existing.model_info_fingerprint,
                existing.evidence_refs,
                existing.error_code,
            )
            if actual != expected:
                raise ApplicationAuditConflict()
            return existing
        try:
            timestamp = self._container.clock.now()
            if timestamp < request.requested_at:
                raise ValueError("application clock precedes request")
            event = ApplicationAuditEvent.create(
                event_type=event_type,
                request=request,
                route=route,
                status=status,
                context_fingerprint=context_fingerprint,
                answer_fingerprint=answer_fingerprint,
                model_info_fingerprint=model_info_fingerprint,
                evidence_refs=evidence_refs,
                error_code=error_code,
                timestamp=timestamp,
            )
            saved_raw = self._container.audit_sink.append(copy.deepcopy(event))
            saved = ApplicationAuditEvent.model_validate(
                copy.deepcopy(saved_raw.model_dump(mode="python"))
            )
        except ApplicationAuditConflict:
            raise
        except Exception as error:
            raise ApplicationAuditUnavailable() from error
        if saved != event:
            raise ApplicationAuditUnavailable()
        return saved

    def _result(
        self,
        *,
        request: ApplicationQuestionRequest,
        route: ApplicationRoute,
        status: ApplicationRunStatus,
        evidence: ApplicationAnswerEvidence | None,
        error_code: ApplicationErrorCode | None,
        context=None,
        requirement_context: RequirementContext | None = None,
        research_snapshot: EngineeringResearchSnapshot | None = None,
        engineering_plan=None,
        engineering_plan_review=None,
        architecture_design=None,
        architecture_review=None,
        design_truth: DesignTruth | None = None,
        gateway_invocation_count: int = 0,
        provider_response_present: bool = False,
        answer_validation_stage: str | None = None,
        answer_rejection_reason: ApplicationErrorCode | None = None,
        proposed_requirements_count: int = 0,
        open_questions_count: int = 0,
        answer_text_present: bool = False,
        original_exception_type: str | None = None,
        original_exception_hash: str | None = None,
        exception_layer: str | None = None,
        exception_stage: str | None = None,
        traceback_fingerprint: str | None = None,
    ) -> ApplicationRunResult:
        event = self._audit(
            request=request,
            route=route,
            status=status,
            evidence=evidence,
            error_code=error_code,
            context=context,
        )
        return ApplicationRunResult.create(
            request=request,
            route=route,
            status=status,
            context_fingerprint=evidence.context.fingerprint if evidence else (
                None if context is None else context.fingerprint
            ),
            context=evidence.context if evidence else context,
            answer=evidence.answer if evidence else None,
            requirement_context=(
                requirement_context
                if requirement_context is not None
                else (None if evidence is None else evidence.requirement_context)
            ),
            engineering_plan=engineering_plan,
            engineering_plan_review=engineering_plan_review,
            architecture_design=architecture_design,
            architecture_review=architecture_review,
            design_truth=(
                design_truth
                if design_truth is not None
                else (None if evidence is None else evidence.design_truth)
            ),
            research_snapshot=research_snapshot,
            error_code=error_code,
            gateway_invocation_count=gateway_invocation_count,
            provider_response_present=provider_response_present,
            answer_validation_stage=answer_validation_stage,
            answer_rejection_reason=answer_rejection_reason,
            proposed_requirements_count=proposed_requirements_count,
            open_questions_count=open_questions_count,
            answer_text_present=answer_text_present,
            original_exception_type=original_exception_type,
            original_exception_hash=original_exception_hash,
            exception_layer=exception_layer,
            exception_stage=exception_stage,
            traceback_fingerprint=traceback_fingerprint,
            audit_event=event,
        )

    def _answer_binding(self, route: ApplicationRoute):
        if route in {
            ApplicationRoute.GREENFIELD,
            ApplicationRoute.GREENFIELD_DISCOVERY,
            ApplicationRoute.GREENFIELD_DESIGN,
        }:
            return self._greenfield_answer_port
        if route is ApplicationRoute.KNOWLEDGE:
            return self._answer_port
        if route is ApplicationRoute.FIRMWARE:
            return self._firmware_answer_port
        if route in {ApplicationRoute.BROWNFIELD, ApplicationRoute.BROWNFIELD_ANALYSIS}:
            return self._firmware_answer_port
        if route in {ApplicationRoute.DEBUG, ApplicationRoute.DEBUG_ANALYSIS}:
            return self._debug_answer_port
        return None

    def answer(
        self,
        request: ApplicationQuestionRequest,
        *,
        route_decision: ApplicationRouteDecision | None = None,
        routing_context: ApplicationRoutingContext | None = None,
    ) -> ApplicationRunResult:
        record_application_runtime_outer_stage("X0_ANSWER_ENTER", "PASS")
        try:
            checked = self._request(request)
        except Exception as error:  # noqa: BLE001 - preserve existing boundary behavior
            attach_exception_diagnostics(
                error,
                error,
                exception_layer="ApplicationRuntime",
                exception_stage="A0_REQUEST_ENTER",
            )
            record_application_runtime_stage(
                "A0_REQUEST_ENTER", "FAIL", error=error,
                exception_layer="ApplicationRuntime",
                exception_stage="A0_REQUEST_ENTER",
            )
            record_application_runtime_not_reached(
                (
                    "A1_CONVERSATION_LOAD",
                    "A2_PROJECT_CONTEXT_BIND",
                    "A3_REQUIREMENT_CONTEXT_BIND",
                    "A4_TASK_MODE_RESOLVE",
                    "A5_ANSWER_BINDING_CREATE",
                    "A6_PROMPT_REQUEST_BUILD",
                    "A7_PROMPT_BUILDER_ENTER",
                )
            )
            raise
        record_application_runtime_stage("A0_REQUEST_ENTER", "PASS")
        # Conversation resolution is completed by DesktopChatRuntime before this
        # boundary.  Reaching ApplicationRuntime with a validated request is the
        # only safe, content-free signal available here.
        record_application_runtime_stage("A1_CONVERSATION_LOAD", "PASS")
        decision = route_decision or self._route(checked, routing_context)
        try:
            requirement_context = (
                self._greenfield_context(checked, decision.route, routing_context)
                if self._is_greenfield(decision.route)
                else (
                    None
                    if routing_context is None or decision.route is not ApplicationRoute.KNOWLEDGE
                    else routing_context.requirement_context
                )
            )
        except Exception as error:  # noqa: BLE001 - content-free boundary telemetry
            attach_exception_diagnostics(
                error,
                error,
                exception_layer="ApplicationRuntime",
                exception_stage="A2_PROJECT_CONTEXT_BIND",
            )
            record_application_runtime_stage(
                "A2_PROJECT_CONTEXT_BIND", "FAIL", error=error,
                exception_layer="ApplicationRuntime",
                exception_stage="A2_PROJECT_CONTEXT_BIND",
            )
            record_application_runtime_not_reached(
                (
                    "A3_REQUIREMENT_CONTEXT_BIND",
                    "A4_TASK_MODE_RESOLVE",
                    "A5_ANSWER_BINDING_CREATE",
                    "A6_PROMPT_REQUEST_BUILD",
                    "A7_PROMPT_BUILDER_ENTER",
                )
            )
            raise
        record_application_runtime_stage("A2_PROJECT_CONTEXT_BIND", "PASS")
        research_snapshot = self._research_for_turn(
            checked,
            decision.route,
            routing_context,
            self._container.research_service,
        )
        if self._is_greenfield(decision.route):
            if requirement_context is None:
                error = ValueError("greenfield requirement context unavailable")
                attach_exception_diagnostics(
                    error,
                    error,
                    exception_layer="ApplicationRuntime",
                    exception_stage="A3_REQUIREMENT_CONTEXT_BIND",
                )
                record_application_runtime_stage(
                    "A3_REQUIREMENT_CONTEXT_BIND", "FAIL", error=error,
                    exception_layer="ApplicationRuntime",
                    exception_stage="A3_REQUIREMENT_CONTEXT_BIND",
                )
                record_application_runtime_not_reached(
                    (
                        "A4_TASK_MODE_RESOLVE",
                        "A5_ANSWER_BINDING_CREATE",
                        "A6_PROMPT_REQUEST_BUILD",
                        "A7_PROMPT_BUILDER_ENTER",
                    )
                )
                raise error
            record_application_runtime_stage("A3_REQUIREMENT_CONTEXT_BIND", "PASS")

        engineering_plan = None
        engineering_plan_review = None
        architecture_design = None
        architecture_review = None
        design_truth = None
        if self._is_greenfield(decision.route) and requirement_context is not None:
            engineering_plan, engineering_plan_review = self._plan_for_turn(
                checked, requirement_context, routing_context
            )
            if engineering_plan is None or engineering_plan.status is not PlanStatus.APPROVED:
                if decision.route is ApplicationRoute.GREENFIELD_DESIGN:
                    decision = ApplicationRouteDecision.for_request(
                        checked, route=ApplicationRoute.GREENFIELD_DISCOVERY
                    )
            elif is_plan_approval_request(checked.question):
                decision = ApplicationRouteDecision.for_request(
                    checked, route=ApplicationRoute.GREENFIELD_DESIGN
                )
            architecture_design, architecture_review = self._architecture_for_turn(
                checked, requirement_context, engineering_plan, routing_context
            )
            design_truth = self._design_truth_for_turn(
                architecture_design,
                None if routing_context is None else routing_context.conversation_id,
            )

        try:
            project_mode = (
                self._evidence_project_mode(decision.route, routing_context)
                if self._is_greenfield(decision.route)
                else None
            )
        except Exception as error:  # noqa: BLE001 - content-free boundary telemetry
            attach_exception_diagnostics(
                error,
                error,
                exception_layer="ApplicationRuntime",
                exception_stage="A4_TASK_MODE_RESOLVE",
            )
            record_application_runtime_stage(
                "A4_TASK_MODE_RESOLVE", "FAIL", error=error,
                exception_layer="ApplicationRuntime",
                exception_stage="A4_TASK_MODE_RESOLVE",
            )
            record_application_runtime_not_reached(
                (
                    "A5_ANSWER_BINDING_CREATE",
                    "A6_PROMPT_REQUEST_BUILD",
                    "A7_PROMPT_BUILDER_ENTER",
                )
            )
            raise
        record_application_runtime_stage("A4_TASK_MODE_RESOLVE", "PASS")

        if is_architecture_approval_request(checked.question):
            if (
                architecture_design is None
                or architecture_design.status is not ArchitectureDesignStatus.APPROVED
                or architecture_review is None
            ):
                return self._result(
                    request=checked,
                    route=ApplicationRoute.GREENFIELD_DISCOVERY,
                    status=ApplicationRunStatus.REJECTED,
                    evidence=None,
                    error_code=ApplicationErrorCode.CLARIFICATION_REQUIRED,
                    requirement_context=requirement_context,
                    research_snapshot=research_snapshot,
                    engineering_plan=engineering_plan,
                    engineering_plan_review=engineering_plan_review,
                    architecture_design=architecture_design,
                    architecture_review=architecture_review,
                )
            evidence = self._architecture_approval_evidence(
                checked,
                requirement_context,
                engineering_plan,
                engineering_plan_review,
                architecture_design,
                architecture_review,
                design_truth,
            )
            return self._result(
                request=checked,
                route=ApplicationRoute.GREENFIELD_DISCOVERY,
                status=ApplicationRunStatus.COMPLETED,
                evidence=evidence,
                error_code=None,
                requirement_context=requirement_context,
                research_snapshot=research_snapshot,
                engineering_plan=engineering_plan,
                engineering_plan_review=engineering_plan_review,
                architecture_design=architecture_design,
                architecture_review=architecture_review,
                gateway_invocation_count=0,
                provider_response_present=False,
                answer_validation_stage="STATE_TRANSITION",
                answer_text_present=True,
            )

        try:
            binding = self._answer_binding(decision.route)
        except Exception as error:  # noqa: BLE001 - content-free boundary telemetry
            attach_exception_diagnostics(
                error,
                error,
                exception_layer="ApplicationRuntime",
                exception_stage="A5_ANSWER_BINDING_CREATE",
            )
            record_application_runtime_stage(
                "A5_ANSWER_BINDING_CREATE", "FAIL", error=error,
                exception_layer="ApplicationRuntime",
                exception_stage="A5_ANSWER_BINDING_CREATE",
            )
            record_application_runtime_not_reached(
                ("A6_PROMPT_REQUEST_BUILD", "A7_PROMPT_BUILDER_ENTER")
            )
            raise
        if binding is None:
            code = (
                ApplicationErrorCode.CLARIFICATION_REQUIRED
                if decision.route in {ApplicationRoute.UNKNOWN, ApplicationRoute.CLARIFICATION_REQUIRED}
                else ApplicationErrorCode.UNSUPPORTED_ROUTE
            )
            record_application_runtime_stage(
                "A5_ANSWER_BINDING_CREATE", "FAIL", reason_code=code.value
            )
            record_application_runtime_not_reached(
                ("A6_PROMPT_REQUEST_BUILD", "A7_PROMPT_BUILDER_ENTER")
            )
            record_application_runtime_outer_stage(
                "X1_AFTER_BINDING", "FAIL", reason_code=code.value
            )
            record_application_runtime_outer_not_reached(
                (
                    "X2_BEFORE_ENGINEERING_QA_CALL",
                    "X3_ENGINEERING_QA_EXCEPTION_CAUGHT",
                    "X4_OUTER_EXCEPTION_MAPPING",
                )
            )
            return self._result(
                request=checked,
                route=decision.route,
                status=ApplicationRunStatus.REJECTED,
                evidence=None,
                error_code=code,
                requirement_context=requirement_context,
                research_snapshot=research_snapshot,
                engineering_plan=engineering_plan,
                engineering_plan_review=engineering_plan_review,
                architecture_design=architecture_design,
                architecture_review=architecture_review,
            )
        record_application_runtime_stage("A5_ANSWER_BINDING_CREATE", "PASS")
        record_application_runtime_outer_stage("X1_AFTER_BINDING", "PASS")
        binding_call_completed = False
        record_application_runtime_outer_stage(
            "X2_BEFORE_ENGINEERING_QA_CALL", "PASS"
        )
        try:
            if self._is_greenfield(decision.route):
                evidence = binding.answer(
                    checked,
                    requirement_context=requirement_context,
                    project_mode=project_mode,
                    project_type=(
                        None if routing_context is None else routing_context.project_type
                    ),
                )
            elif decision.route is ApplicationRoute.KNOWLEDGE:
                evidence = binding.answer(
                    checked,
                    requirement_context=requirement_context,
                )
            else:
                evidence = binding.answer(checked)
            binding_call_completed = True
            if not is_requirement_change_feedback(checked.question):
                evidence = self._merge_provider_requirements(
                    checked, decision.route, evidence, routing_context
                )
            evidence = self._attach_research(evidence, research_snapshot)
            if architecture_design is not None and evidence.requirement_context is not None and architecture_design.context_fingerprint != evidence.requirement_context.fingerprint and architecture_design.status not in {
                ArchitectureDesignStatus.SUPERSEDED,
                ArchitectureDesignStatus.INVALIDATED,
            }:
                architecture_design = transition_architecture_design(architecture_design, ArchitectureDesignStatus.SUPERSEDED)
                architecture_review = None
                design_truth = None
            if self._is_greenfield(decision.route) and evidence.requirement_context is not None:
                engineering_plan, engineering_plan_review = self._plan_for_turn(
                    checked, evidence.requirement_context, routing_context
                )
                evidence = self._attach_plan(
                    evidence, engineering_plan, engineering_plan_review
                )
                evidence = self._attach_architecture(
                    evidence, architecture_design, architecture_review, design_truth
                )
                if (
                    decision.route is ApplicationRoute.GREENFIELD_DESIGN
                    and (
                        engineering_plan is None
                        or engineering_plan.status is not PlanStatus.APPROVED
                    )
                ):
                    decision = ApplicationRouteDecision.for_request(
                        checked, route=ApplicationRoute.GREENFIELD_DISCOVERY
                    )
        except Exception as error:  # noqa: BLE001 - map injected Service failures
            attach_exception_diagnostics(
                error,
                error,
                exception_layer="ApplicationRuntime",
                exception_stage="X3_ENGINEERING_QA_EXCEPTION_CAUGHT",
            )
            diagnostics = exception_diagnostics(error)
            record_application_runtime_outer_stage(
                "X3_ENGINEERING_QA_EXCEPTION_CAUGHT",
                "FAIL" if not binding_call_completed else "NOT_REACHED",
                error=error if not binding_call_completed else None,
                exception_layer=diagnostics["exception_layer"],
                exception_stage=diagnostics["exception_stage"],
            )
            error_code = self._failure_code(error)
            record_application_runtime_outer_stage(
                "X4_OUTER_EXCEPTION_MAPPING",
                "FAIL",
                error=error,
                reason_code=error_code.value,
                exception_layer=diagnostics["exception_layer"],
                exception_stage=diagnostics["exception_stage"],
            )
            return self._result(
                request=checked,
                route=decision.route,
                status=ApplicationRunStatus.FAILED,
                evidence=None,
                context=getattr(error, "context", None),
                error_code=error_code,
                requirement_context=requirement_context,
                research_snapshot=research_snapshot,
                gateway_invocation_count=getattr(error, "gateway_invocation_count", 0),
                provider_response_present=getattr(error, "provider_response_present", False),
                answer_validation_stage=getattr(error, "validation_stage", None),
                answer_rejection_reason=(
                    error_code
                    if error_code
                    in {
                        ApplicationErrorCode.ANSWER_REJECTED,
                        ApplicationErrorCode.ANSWER_SCHEMA_INVALID,
                        ApplicationErrorCode.ANSWER_CONTRACT_MISMATCH,
                        ApplicationErrorCode.ANSWER_EMPTY,
                        ApplicationErrorCode.ANSWER_REJECTED_UNSAFE,
                        ApplicationErrorCode.ANSWER_REJECTED_EVIDENCE_MISMATCH,
                    }
                    else None
                ),
                proposed_requirements_count=getattr(error, "proposed_requirements_count", 0),
                open_questions_count=getattr(error, "open_questions_count", 0),
                answer_text_present=getattr(error, "answer_text_present", False),
                original_exception_type=diagnostics["original_exception_type"],
                original_exception_hash=diagnostics["original_exception_hash"],
                exception_layer=diagnostics["exception_layer"],
                exception_stage=diagnostics["exception_stage"],
                traceback_fingerprint=diagnostics["traceback_fingerprint"],
                engineering_plan=engineering_plan,
                engineering_plan_review=engineering_plan_review,
                architecture_design=architecture_design,
                architecture_review=architecture_review,
            )
        record_application_runtime_outer_not_reached(
            ("X3_ENGINEERING_QA_EXCEPTION_CAUGHT", "X4_OUTER_EXCEPTION_MAPPING")
        )
        return self._result(
            request=checked,
            route=decision.route,
            status=ApplicationRunStatus.COMPLETED,
            evidence=evidence,
            error_code=None,
            requirement_context=(
                None if evidence is None else evidence.requirement_context
            ),
            research_snapshot=research_snapshot,
            gateway_invocation_count=1,
            provider_response_present=True,
            answer_validation_stage="COMPLETE",
            proposed_requirements_count=len(evidence.answer.requirement_proposals),
            open_questions_count=len(evidence.answer.open_questions),
            answer_text_present=bool(evidence.answer.content.strip()),
            engineering_plan=engineering_plan,
            engineering_plan_review=engineering_plan_review,
            architecture_design=architecture_design,
            architecture_review=architecture_review,
        )

    async def aanswer(
        self,
        request: ApplicationQuestionRequest,
        *,
        route_decision: ApplicationRouteDecision | None = None,
        routing_context: ApplicationRoutingContext | None = None,
    ) -> ApplicationRunResult:
        record_application_runtime_outer_stage("X0_ANSWER_ENTER", "PASS")
        try:
            checked = self._request(request)
        except Exception as error:  # noqa: BLE001 - preserve existing boundary behavior
            attach_exception_diagnostics(
                error,
                error,
                exception_layer="ApplicationRuntime",
                exception_stage="A0_REQUEST_ENTER",
            )
            record_application_runtime_stage(
                "A0_REQUEST_ENTER", "FAIL", error=error,
                exception_layer="ApplicationRuntime",
                exception_stage="A0_REQUEST_ENTER",
            )
            record_application_runtime_not_reached(
                (
                    "A1_CONVERSATION_LOAD",
                    "A2_PROJECT_CONTEXT_BIND",
                    "A3_REQUIREMENT_CONTEXT_BIND",
                    "A4_TASK_MODE_RESOLVE",
                    "A5_ANSWER_BINDING_CREATE",
                    "A6_PROMPT_REQUEST_BUILD",
                    "A7_PROMPT_BUILDER_ENTER",
                )
            )
            raise
        record_application_runtime_stage("A0_REQUEST_ENTER", "PASS")
        record_application_runtime_stage("A1_CONVERSATION_LOAD", "PASS")
        decision = route_decision or self._route(checked, routing_context)
        try:
            requirement_context = (
                self._greenfield_context(checked, decision.route, routing_context)
                if self._is_greenfield(decision.route)
                else (
                    None
                    if routing_context is None or decision.route is not ApplicationRoute.KNOWLEDGE
                    else routing_context.requirement_context
                )
            )
        except Exception as error:  # noqa: BLE001 - content-free boundary telemetry
            attach_exception_diagnostics(
                error,
                error,
                exception_layer="ApplicationRuntime",
                exception_stage="A2_PROJECT_CONTEXT_BIND",
            )
            record_application_runtime_stage(
                "A2_PROJECT_CONTEXT_BIND", "FAIL", error=error,
                exception_layer="ApplicationRuntime",
                exception_stage="A2_PROJECT_CONTEXT_BIND",
            )
            record_application_runtime_not_reached(
                (
                    "A3_REQUIREMENT_CONTEXT_BIND",
                    "A4_TASK_MODE_RESOLVE",
                    "A5_ANSWER_BINDING_CREATE",
                    "A6_PROMPT_REQUEST_BUILD",
                    "A7_PROMPT_BUILDER_ENTER",
                )
            )
            raise
        record_application_runtime_stage("A2_PROJECT_CONTEXT_BIND", "PASS")
        research_snapshot = self._research_for_turn(
            checked,
            decision.route,
            routing_context,
            self._container.research_service,
        )
        if self._is_greenfield(decision.route):
            if requirement_context is None:
                error = ValueError("greenfield requirement context unavailable")
                attach_exception_diagnostics(
                    error,
                    error,
                    exception_layer="ApplicationRuntime",
                    exception_stage="A3_REQUIREMENT_CONTEXT_BIND",
                )
                record_application_runtime_stage(
                    "A3_REQUIREMENT_CONTEXT_BIND", "FAIL", error=error,
                    exception_layer="ApplicationRuntime",
                    exception_stage="A3_REQUIREMENT_CONTEXT_BIND",
                )
                record_application_runtime_not_reached(
                    (
                        "A4_TASK_MODE_RESOLVE",
                        "A5_ANSWER_BINDING_CREATE",
                        "A6_PROMPT_REQUEST_BUILD",
                        "A7_PROMPT_BUILDER_ENTER",
                    )
                )
                raise error
            record_application_runtime_stage("A3_REQUIREMENT_CONTEXT_BIND", "PASS")

        engineering_plan = None
        engineering_plan_review = None
        design_truth = None
        if self._is_greenfield(decision.route) and requirement_context is not None:
            engineering_plan, engineering_plan_review = self._plan_for_turn(
                checked, requirement_context, routing_context
            )
            if engineering_plan is None or engineering_plan.status is not PlanStatus.APPROVED:
                if decision.route is ApplicationRoute.GREENFIELD_DESIGN:
                    decision = ApplicationRouteDecision.for_request(
                        checked, route=ApplicationRoute.GREENFIELD_DISCOVERY
                    )
            elif is_plan_approval_request(checked.question):
                decision = ApplicationRouteDecision.for_request(
                    checked, route=ApplicationRoute.GREENFIELD_DESIGN
                )
            architecture_design = None
            architecture_review = None
            architecture_design, architecture_review = self._architecture_for_turn(
                checked, requirement_context, engineering_plan, routing_context
            )
            design_truth = self._design_truth_for_turn(
                architecture_design,
                None if routing_context is None else routing_context.conversation_id,
            )
        else:
            architecture_design = None
            architecture_review = None

        try:
            project_mode = (
                self._evidence_project_mode(decision.route, routing_context)
                if self._is_greenfield(decision.route)
                else None
            )
        except Exception as error:  # noqa: BLE001 - content-free boundary telemetry
            attach_exception_diagnostics(
                error,
                error,
                exception_layer="ApplicationRuntime",
                exception_stage="A4_TASK_MODE_RESOLVE",
            )
            record_application_runtime_stage(
                "A4_TASK_MODE_RESOLVE", "FAIL", error=error,
                exception_layer="ApplicationRuntime",
                exception_stage="A4_TASK_MODE_RESOLVE",
            )
            record_application_runtime_not_reached(
                (
                    "A5_ANSWER_BINDING_CREATE",
                    "A6_PROMPT_REQUEST_BUILD",
                    "A7_PROMPT_BUILDER_ENTER",
                )
            )
            raise
        record_application_runtime_stage("A4_TASK_MODE_RESOLVE", "PASS")

        if is_architecture_approval_request(checked.question):
            if (
                architecture_design is None
                or architecture_design.status is not ArchitectureDesignStatus.APPROVED
                or architecture_review is None
            ):
                return self._result(
                    request=checked,
                    route=ApplicationRoute.GREENFIELD_DISCOVERY,
                    status=ApplicationRunStatus.REJECTED,
                    evidence=None,
                    error_code=ApplicationErrorCode.CLARIFICATION_REQUIRED,
                    requirement_context=requirement_context,
                    research_snapshot=research_snapshot,
                    engineering_plan=engineering_plan,
                    engineering_plan_review=engineering_plan_review,
                    architecture_design=architecture_design,
                    architecture_review=architecture_review,
                )
            evidence = self._architecture_approval_evidence(
                checked,
                requirement_context,
                engineering_plan,
                engineering_plan_review,
                architecture_design,
                architecture_review,
                design_truth,
            )
            return self._result(
                request=checked,
                route=ApplicationRoute.GREENFIELD_DISCOVERY,
                status=ApplicationRunStatus.COMPLETED,
                evidence=evidence,
                error_code=None,
                requirement_context=requirement_context,
                research_snapshot=research_snapshot,
                engineering_plan=engineering_plan,
                engineering_plan_review=engineering_plan_review,
                architecture_design=architecture_design,
                architecture_review=architecture_review,
                gateway_invocation_count=0,
                provider_response_present=False,
                answer_validation_stage="STATE_TRANSITION",
                answer_text_present=True,
            )

        try:
            binding = self._answer_binding(decision.route)
        except Exception as error:  # noqa: BLE001 - content-free boundary telemetry
            attach_exception_diagnostics(
                error,
                error,
                exception_layer="ApplicationRuntime",
                exception_stage="A5_ANSWER_BINDING_CREATE",
            )
            record_application_runtime_stage(
                "A5_ANSWER_BINDING_CREATE", "FAIL", error=error,
                exception_layer="ApplicationRuntime",
                exception_stage="A5_ANSWER_BINDING_CREATE",
            )
            record_application_runtime_not_reached(
                ("A6_PROMPT_REQUEST_BUILD", "A7_PROMPT_BUILDER_ENTER")
            )
            raise
        if binding is None:
            code = (
                ApplicationErrorCode.CLARIFICATION_REQUIRED
                if decision.route in {ApplicationRoute.UNKNOWN, ApplicationRoute.CLARIFICATION_REQUIRED}
                else ApplicationErrorCode.UNSUPPORTED_ROUTE
            )
            record_application_runtime_stage(
                "A5_ANSWER_BINDING_CREATE", "FAIL", reason_code=code.value
            )
            record_application_runtime_not_reached(
                ("A6_PROMPT_REQUEST_BUILD", "A7_PROMPT_BUILDER_ENTER")
            )
            record_application_runtime_outer_stage(
                "X1_AFTER_BINDING", "FAIL", reason_code=code.value
            )
            record_application_runtime_outer_not_reached(
                (
                    "X2_BEFORE_ENGINEERING_QA_CALL",
                    "X3_ENGINEERING_QA_EXCEPTION_CAUGHT",
                    "X4_OUTER_EXCEPTION_MAPPING",
                )
            )
            return self._result(
                request=checked,
                route=decision.route,
                status=ApplicationRunStatus.REJECTED,
                evidence=None,
                error_code=code,
                requirement_context=requirement_context,
                research_snapshot=research_snapshot,
                engineering_plan=engineering_plan,
                engineering_plan_review=engineering_plan_review,
                architecture_design=architecture_design,
                architecture_review=architecture_review,
            )
        record_application_runtime_stage("A5_ANSWER_BINDING_CREATE", "PASS")
        record_application_runtime_outer_stage("X1_AFTER_BINDING", "PASS")
        binding_call_completed = False
        record_application_runtime_outer_stage(
            "X2_BEFORE_ENGINEERING_QA_CALL", "PASS"
        )
        try:
            if self._is_greenfield(decision.route):
                evidence = await binding.aanswer(
                    checked,
                    requirement_context=requirement_context,
                    project_mode=project_mode,
                    project_type=(
                        None if routing_context is None else routing_context.project_type
                    ),
                )
            elif decision.route is ApplicationRoute.KNOWLEDGE:
                evidence = await binding.aanswer(
                    checked,
                    requirement_context=requirement_context,
                )
            else:
                evidence = await binding.aanswer(checked)
            binding_call_completed = True
            if not is_requirement_change_feedback(checked.question):
                evidence = self._merge_provider_requirements(
                    checked, decision.route, evidence, routing_context
                )
            evidence = self._attach_research(evidence, research_snapshot)
            if architecture_design is not None and evidence.requirement_context is not None and architecture_design.context_fingerprint != evidence.requirement_context.fingerprint and architecture_design.status not in {
                ArchitectureDesignStatus.SUPERSEDED,
                ArchitectureDesignStatus.INVALIDATED,
            }:
                architecture_design = transition_architecture_design(architecture_design, ArchitectureDesignStatus.SUPERSEDED)
                architecture_review = None
                design_truth = None
            if self._is_greenfield(decision.route) and evidence.requirement_context is not None:
                engineering_plan, engineering_plan_review = self._plan_for_turn(
                    checked, evidence.requirement_context, routing_context
                )
                evidence = self._attach_plan(
                    evidence, engineering_plan, engineering_plan_review
                )
                evidence = self._attach_architecture(
                    evidence, architecture_design, architecture_review, design_truth
                )
                if (
                    decision.route is ApplicationRoute.GREENFIELD_DESIGN
                    and (
                        engineering_plan is None
                        or engineering_plan.status is not PlanStatus.APPROVED
                    )
                ):
                    decision = ApplicationRouteDecision.for_request(
                        checked, route=ApplicationRoute.GREENFIELD_DISCOVERY
                    )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - map injected Service failures
            attach_exception_diagnostics(
                error,
                error,
                exception_layer="ApplicationRuntime",
                exception_stage="X3_ENGINEERING_QA_EXCEPTION_CAUGHT",
            )
            diagnostics = exception_diagnostics(error)
            record_application_runtime_outer_stage(
                "X3_ENGINEERING_QA_EXCEPTION_CAUGHT",
                "FAIL" if not binding_call_completed else "NOT_REACHED",
                error=error if not binding_call_completed else None,
                exception_layer=diagnostics["exception_layer"],
                exception_stage=diagnostics["exception_stage"],
            )
            error_code = self._failure_code(error)
            record_application_runtime_outer_stage(
                "X4_OUTER_EXCEPTION_MAPPING",
                "FAIL",
                error=error,
                reason_code=error_code.value,
                exception_layer=diagnostics["exception_layer"],
                exception_stage=diagnostics["exception_stage"],
            )
            return self._result(
                request=checked,
                route=decision.route,
                status=ApplicationRunStatus.FAILED,
                evidence=None,
                context=getattr(error, "context", None),
                error_code=error_code,
                requirement_context=requirement_context,
                research_snapshot=research_snapshot,
                gateway_invocation_count=getattr(error, "gateway_invocation_count", 0),
                provider_response_present=getattr(error, "provider_response_present", False),
                answer_validation_stage=getattr(error, "validation_stage", None),
                answer_rejection_reason=(
                    error_code
                    if error_code
                    in {
                        ApplicationErrorCode.ANSWER_REJECTED,
                        ApplicationErrorCode.ANSWER_SCHEMA_INVALID,
                        ApplicationErrorCode.ANSWER_CONTRACT_MISMATCH,
                        ApplicationErrorCode.ANSWER_EMPTY,
                        ApplicationErrorCode.ANSWER_REJECTED_UNSAFE,
                        ApplicationErrorCode.ANSWER_REJECTED_EVIDENCE_MISMATCH,
                    }
                    else None
                ),
                proposed_requirements_count=getattr(error, "proposed_requirements_count", 0),
                open_questions_count=getattr(error, "open_questions_count", 0),
                answer_text_present=getattr(error, "answer_text_present", False),
                original_exception_type=diagnostics["original_exception_type"],
                original_exception_hash=diagnostics["original_exception_hash"],
                exception_layer=diagnostics["exception_layer"],
                exception_stage=diagnostics["exception_stage"],
                traceback_fingerprint=diagnostics["traceback_fingerprint"],
                engineering_plan=engineering_plan,
                engineering_plan_review=engineering_plan_review,
                architecture_design=architecture_design,
                architecture_review=architecture_review,
            )
        record_application_runtime_outer_not_reached(
            ("X3_ENGINEERING_QA_EXCEPTION_CAUGHT", "X4_OUTER_EXCEPTION_MAPPING")
        )
        return self._result(
            request=checked,
            route=decision.route,
            status=ApplicationRunStatus.COMPLETED,
            evidence=evidence,
            error_code=None,
            requirement_context=(
                None if evidence is None else evidence.requirement_context
            ),
            research_snapshot=research_snapshot,
            gateway_invocation_count=1,
            provider_response_present=True,
            answer_validation_stage="COMPLETE",
            proposed_requirements_count=len(evidence.answer.requirement_proposals),
            open_questions_count=len(evidence.answer.open_questions),
            answer_text_present=bool(evidence.answer.content.strip()),
            engineering_plan=engineering_plan,
            engineering_plan_review=engineering_plan_review,
            architecture_design=architecture_design,
            architecture_review=architecture_review,
        )


def create_application_runtime(
    container: ApplicationDependencyContainer,
) -> ApplicationRuntime:
    return ApplicationRuntime(container)


__all__ = ("ApplicationRuntime", "create_application_runtime")
