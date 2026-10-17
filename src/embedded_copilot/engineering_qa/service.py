from __future__ import annotations

import asyncio
import copy
import logging

from pydantic import ValidationError

from embedded_copilot.diagnostics import (
    attach_exception_diagnostics,
    record_application_runtime_stage,
    record_engineering_qa_not_reached,
    record_engineering_qa_stage,
    record_prompt_pipeline_not_reached,
    record_prompt_pipeline_stage,
)
from embedded_copilot.engineering_context import (
    ContextSourceType,
    ContextVerificationStatus,
    EngineeringContextProviderPort,
    EngineeringContextRequest,
    EngineeringContextSnapshot,
)
from embedded_copilot.engineering_context.exceptions import (
    EngineeringContextUnavailable,
)
from embedded_copilot.engineering_requirements import RequirementContext
from embedded_copilot.model_gateway import (
    EngineeringStage,
    ModelGatewayPort,
    ModelPolicy,
    validate_model_response,
)
from embedded_copilot.model_gateway.invocation.contracts import (
    ModelInvocationGenerationOptions,
)

from .contracts import EngineeringAnswer, EngineeringQuestion, ModelInfo
from .conversation_policy import (
    clean_response_content,
    fallback_response,
    prepare_response_content,
    resolve_conversation_mode,
)
from .exceptions import EngineeringAnswerRejected, InsufficientEvidence
from .prompt import PromptBuilder

_logger = logging.getLogger(__name__)
_RESPONSE_TELEMETRY_EVENT = "provider_response_boundary_telemetry"


def _record_greenfield_validation(status: str, error_code: str = "-") -> None:
    _logger.info(
        _RESPONSE_TELEMETRY_EVENT,
        extra={
            "event_name": _RESPONSE_TELEMETRY_EVENT,
            "response_stage": "R5_GREENFIELD_CONTRACT_VALIDATE",
            "telemetry_status": status,
            "validation_status": status,
            "validation_error_code": error_code,
        },
    )
class EngineeringQAService:
    __slots__ = (
        "_context_provider",
        "_answer_contract",
        "_gateway",
        "_prompt_builder",
        "_project_mode",
        "_project_type",
        "_require_evidence",
        "_requirement_context",
        "_tenant_id",
        "_task_type",
    )

    def __init__(
        self,
        *,
        context_provider: EngineeringContextProviderPort,
        model_gateway: ModelGatewayPort,
        model_policy: ModelPolicy | None = None,
        generation_options: ModelInvocationGenerationOptions | None = None,
        stage_id: EngineeringStage = EngineeringStage.REQUIREMENT_ANALYSIS,
        require_evidence: bool = True,
        requirement_context: RequirementContext | None = None,
        project_mode: str | None = None,
        project_type: str | None = None,
        tenant_id: str | None = None,
        answer_contract: str = "EVIDENCE_BACKED",
        task_type: str | None = None,
    ) -> None:
        if not isinstance(context_provider, EngineeringContextProviderPort):
            raise TypeError("engineering context provider is invalid")
        if not isinstance(model_gateway, ModelGatewayPort):
            raise TypeError("model gateway port is invalid")
        if answer_contract not in {"EVIDENCE_BACKED", "GREENFIELD"}:
            raise ValueError("answer contract is invalid")
        self._context_provider = context_provider
        self._answer_contract = answer_contract
        self._gateway = model_gateway
        self._prompt_builder = PromptBuilder(
            model_policy=model_policy,
            generation_options=generation_options,
            stage_id=stage_id,
            answer_contract=answer_contract,
        )
        self._project_mode = project_mode
        self._project_type = project_type
        self._require_evidence = require_evidence
        self._requirement_context = (
            None
            if requirement_context is None
            else RequirementContext.model_validate(
                copy.deepcopy(requirement_context.model_dump(mode="python"))
            )
        )
        self._tenant_id = tenant_id
        self._task_type = task_type
        record_prompt_pipeline_stage(
            "P0_ENGINEERING_QA_SERVICE_ENTER",
            "PASS",
            engineering_qa_service_available=True,
            prompt_module_loaded=True,
        )
        record_engineering_qa_stage(
            "Q1_PROMPT_SERVICE_RESOLVE",
            "PASS",
            prompt_service_dependency_available=True,
            prompt_builder_dependency_available=True,
            request_builder_dependency_available=True,
        )

    @staticmethod
    def _question(value: object) -> EngineeringQuestion:
        if type(value) is not EngineeringQuestion:
            raise TypeError("engineering question is invalid")
        return EngineeringQuestion.model_validate(copy.deepcopy(value.model_dump(mode="python")))

    def _context(self, question: EngineeringQuestion) -> EngineeringContextSnapshot:
        request = EngineeringContextRequest(
            project_id=question.project_id,
            tenant_id=self._tenant_id or "default-tenant",
            task=question.question,
            constraints=question.constraints,
        )
        try:
            snapshot = self._context_provider.get_context(request)
        except EngineeringContextUnavailable as error:
            attach_exception_diagnostics(
                error,
                error,
                exception_layer="EngineeringQAService",
                exception_stage="CONTEXT_BUILD",
            )
            raise
        except InsufficientEvidence as error:
            attach_exception_diagnostics(
                error,
                error,
                exception_layer="EngineeringQAService",
                exception_stage="CONTEXT_BUILD",
            )
            raise
        except Exception as error:
            wrapped = InsufficientEvidence()
            attach_exception_diagnostics(
                wrapped,
                error,
                exception_layer="EngineeringQAService",
                exception_stage="CONTEXT_BUILD",
            )
            raise wrapped from error
        if snapshot is None and not self._require_evidence:
            snapshot = EngineeringContextSnapshot.create(
                project_id=question.project_id,
                tenant_id="default-tenant",
                task_description=question.question,
                memory_evidence=(),
                graph_evidence=(),
                knowledge_evidence=(),
                multimodal_evidence=(),
                conflicts=(),
                confidence=0.0,
                provenance=(),
                verification_status=ContextVerificationStatus.UNVERIFIED,
            )
        if type(snapshot) is not EngineeringContextSnapshot:
            raise InsufficientEvidence()
        try:
            checked = EngineeringContextSnapshot.model_validate(copy.deepcopy(snapshot.model_dump(mode="python")))
        except (TypeError, ValueError, ValidationError) as error:
            wrapped = InsufficientEvidence()
            attach_exception_diagnostics(
                wrapped,
                error,
                exception_layer="EngineeringQAService",
                exception_stage="CONTEXT_SNAPSHOT_VALIDATE",
            )
            raise wrapped from error
        if checked.project_id != question.project_id or checked.task_description != question.question:
            raise InsufficientEvidence()
        evidence = (*checked.memory_evidence, *checked.graph_evidence, *checked.knowledge_evidence, *checked.multimodal_evidence)
        current_evidence = tuple(
            item for item in evidence
            if item.source_type is not ContextSourceType.KNOWLEDGE_EVOLUTION
            and not (
                item.source_type is ContextSourceType.ENGINEERING_MEMORY
                and bool(item.provenance)
            )
        )
        if self._require_evidence and not current_evidence:
            raise InsufficientEvidence()
        provenance = {(item.source_id, item.source_reference, item.source_fingerprint) for item in checked.provenance}
        if any((item.evidence_id, item.source_reference, item.source_fingerprint) not in provenance for item in evidence):
            raise InsufficientEvidence()
        return checked

    @staticmethod
    def _answer(
        question: EngineeringQuestion,
        snapshot: EngineeringContextSnapshot,
        response: object,
        *,
        answer_contract: str,
        requirement_context: RequirementContext | None = None,
    ) -> EngineeringAnswer:
        raw_content = getattr(response, "content", None)
        content_field_present = isinstance(raw_content, str)
        answer_text_present = bool(content_field_present and raw_content.strip())
        try:
            checked = validate_model_response(response)
        except (TypeError, ValueError, ValidationError) as error:
            if answer_contract == "GREENFIELD":
                _record_greenfield_validation(
                    "FAIL",
                    "ANSWER_EMPTY"
                    if content_field_present and not answer_text_present
                    else "ANSWER_SCHEMA_INVALID",
                )
            wrapped = EngineeringAnswerRejected(
                reason_code=(
                    "ANSWER_EMPTY"
                    if content_field_present and not answer_text_present
                    else "ANSWER_SCHEMA_INVALID"
                ),
                validation_stage="MODEL_RESPONSE_NORMALIZATION",
                answer_text_present=answer_text_present,
            )
            attach_exception_diagnostics(
                wrapped,
                error,
                exception_layer="EngineeringQAService",
                exception_stage="MODEL_RESPONSE_NORMALIZATION",
            )
            raise wrapped from error
        proposal_count = len(checked.requirement_proposals)
        question_count = len(checked.open_questions)
        if checked.provenance != snapshot.provenance:
            if answer_contract == "GREENFIELD":
                _record_greenfield_validation(
                    "FAIL", "ANSWER_REJECTED_EVIDENCE_MISMATCH"
                )
            wrapped = EngineeringAnswerRejected(
                reason_code="ANSWER_REJECTED_EVIDENCE_MISMATCH",
                validation_stage="PROVENANCE_BINDING",
                proposed_requirements_count=proposal_count,
                open_questions_count=question_count,
                answer_text_present=True,
            )
            attach_exception_diagnostics(
                wrapped,
                None,
                exception_layer="EngineeringQAService",
                exception_stage="PROVENANCE_BINDING",
            )
            raise wrapped
        evidence_refs = tuple(sorted({item.evidence_id for item in (*snapshot.memory_evidence, *snapshot.graph_evidence, *snapshot.knowledge_evidence, *snapshot.multimodal_evidence)}))
        model_confidence = snapshot.confidence if checked.confidence is None else min(snapshot.confidence, checked.confidence)
        mode = resolve_conversation_mode(
            question.question,
            requirement_context=requirement_context,
        )
        content = clean_response_content(
            prepare_response_content(
                checked.content,
                question=question.question,
                requirement_context=requirement_context,
            ),
            mode=mode,
        ) or fallback_response(mode)
        try:
            answer = EngineeringAnswer(
                question_id=question.question_id,
                content=content,
                confidence=model_confidence,
                evidence_refs=evidence_refs,
                model_info=ModelInfo(provider=checked.provider, model_name=checked.model_name, finish_reason=checked.finish_reason, usage=checked.usage),
                requirement_proposals=checked.requirement_proposals,
                open_questions=checked.open_questions,
            )
        except (TypeError, ValueError, ValidationError) as error:
            if answer_contract == "GREENFIELD":
                _record_greenfield_validation("FAIL", "ANSWER_CONTRACT_MISMATCH")
            wrapped = EngineeringAnswerRejected(
                reason_code="ANSWER_CONTRACT_MISMATCH",
                validation_stage="ENGINEERING_ANSWER",
                proposed_requirements_count=proposal_count,
                open_questions_count=question_count,
                answer_text_present=True,
            )
            attach_exception_diagnostics(
                wrapped,
                error,
                exception_layer="EngineeringQAService",
                exception_stage="ENGINEERING_ANSWER",
            )
            raise wrapped from error
        if answer_contract == "GREENFIELD":
            _record_greenfield_validation("PASS")
        return answer

    @staticmethod
    def _mark_gateway_failure(error: Exception) -> None:
        attach_exception_diagnostics(
            error,
            error,
            exception_layer="EngineeringQAService",
            exception_stage="MODEL_GATEWAY",
        )
        for field, value in (
            ("gateway_invocation_count", 1),
            ("provider_response_present", False),
            ("validation_stage", "PROVIDER_TRANSPORT_OR_PARSE"),
            ("proposed_requirements_count", 0),
            ("open_questions_count", 0),
            ("answer_text_present", False),
        ):
            if hasattr(error, field):
                continue
            try:
                setattr(error, field, value)
            except Exception:
                continue

    def _request(self, question: EngineeringQuestion, snapshot: EngineeringContextSnapshot):
        record_application_runtime_stage("A6_PROMPT_REQUEST_BUILD", "PASS")
        record_engineering_qa_stage("Q2_PROMPT_BUILD_REQUEST", "PASS")
        record_prompt_pipeline_stage("P4_PROMPT_BUILDER_CALL", "PASS")
        try:
            record_application_runtime_stage("A7_PROMPT_BUILDER_ENTER", "PASS")
            return self._prompt_builder.build(
                question,
                snapshot,
                requirement_context=self._requirement_context,
                project_mode=self._project_mode,
                project_type=self._project_type,
                task_type=self._task_type,
            )
        except (TypeError, ValueError, ValidationError) as error:
            record_application_runtime_stage(
                "A7_PROMPT_BUILDER_ENTER", "FAIL", error=error,
                exception_layer="EngineeringQAService",
                exception_stage="A7_PROMPT_BUILDER_ENTER",
            )
            record_engineering_qa_stage(
                "Q2_PROMPT_BUILD_REQUEST", "FAIL", error=error,
                exception_layer="EngineeringQAService",
                exception_stage="A7_PROMPT_BUILDER_ENTER",
            )
            record_prompt_pipeline_stage(
                "P4_PROMPT_BUILDER_CALL", "FAIL", error=error,
                exception_layer="EngineeringQAService",
                exception_stage="A7_PROMPT_BUILDER_ENTER",
            )
            wrapped = EngineeringAnswerRejected(
                reason_code="ANSWER_CONTRACT_MISMATCH",
                validation_stage="REQUEST_BUILD",
                gateway_invocation_count=0,
                provider_response_present=False,
            )
            attach_exception_diagnostics(
                wrapped,
                error,
                exception_layer="EngineeringQAService",
                exception_stage="A7_PROMPT_BUILDER_ENTER",
            )
            raise wrapped from error
        except Exception as error:  # noqa: BLE001 - keep existing redacted mapping
            attach_exception_diagnostics(
                error,
                error,
                exception_layer="EngineeringQAService",
                exception_stage="A7_PROMPT_BUILDER_ENTER",
            )
            record_application_runtime_stage(
                "A7_PROMPT_BUILDER_ENTER", "FAIL", error=error,
                exception_layer="EngineeringQAService",
                exception_stage="A7_PROMPT_BUILDER_ENTER",
            )
            record_engineering_qa_stage(
                "Q2_PROMPT_BUILD_REQUEST", "FAIL", error=error,
                exception_layer="EngineeringQAService",
                exception_stage="A7_PROMPT_BUILDER_ENTER",
            )
            record_prompt_pipeline_stage(
                "P4_PROMPT_BUILDER_CALL", "FAIL", error=error,
                exception_layer="EngineeringQAService",
                exception_stage="A7_PROMPT_BUILDER_ENTER",
            )
            raise

    def answer(self, question: EngineeringQuestion) -> EngineeringAnswer:
        record_engineering_qa_stage(
            "Q0_ENGINEERING_QA_ENTER",
            "PASS",
            engineering_qa_service_instance_available=True,
        )
        try:
            checked_question = self._question(question)
        except Exception as error:  # noqa: BLE001 - fixed, redacted boundary event
            attach_exception_diagnostics(
                error,
                error,
                exception_layer="EngineeringQAService",
                exception_stage="Q4_ENGINEERING_QA_EXCEPTION",
            )
            record_engineering_qa_stage(
                "Q4_ENGINEERING_QA_EXCEPTION", "FAIL", error=error,
                exception_layer="EngineeringQAService",
                exception_stage="Q4_ENGINEERING_QA_EXCEPTION",
            )
            record_prompt_pipeline_stage(
                "P1_TASK_REQUEST_CONSTRUCTION_START",
                "FAIL",
                error=error,
                constraints_valid=False,
                exception_layer="EngineeringQAService",
                exception_stage="Q4_ENGINEERING_QA_EXCEPTION",
            )
            record_prompt_pipeline_not_reached(
                (
                    "P2_REQUIREMENT_CONTEXT_SERIALIZE",
                    "P3_GREENFIELD_PROMPT_SELECT",
                    "P4_PROMPT_BUILDER_CALL",
                    "P5_MODEL_REQUEST_CREATED",
                )
            )
            raise
        record_prompt_pipeline_stage(
            "P1_TASK_REQUEST_CONSTRUCTION_START",
            "PASS",
            constraints_valid=True,
        )
        try:
            snapshot = self._context(checked_question)
            request = self._request(checked_question, snapshot)
        except Exception as error:  # noqa: BLE001 - preserve redacted mapping
            attach_exception_diagnostics(
                error,
                error,
                exception_layer="EngineeringQAService",
                exception_stage="Q4_ENGINEERING_QA_EXCEPTION",
            )
            record_engineering_qa_stage(
                "Q4_ENGINEERING_QA_EXCEPTION", "FAIL", error=error,
                exception_layer="EngineeringQAService",
                exception_stage="Q4_ENGINEERING_QA_EXCEPTION",
            )
            record_prompt_pipeline_not_reached(
                (
                    "P2_REQUIREMENT_CONTEXT_SERIALIZE",
                    "P3_GREENFIELD_PROMPT_SELECT",
                    "P4_PROMPT_BUILDER_CALL",
                    "P5_MODEL_REQUEST_CREATED",
                )
            )
            raise
        record_engineering_qa_not_reached(("Q4_ENGINEERING_QA_EXCEPTION",))
        try:
            response = self._gateway.generate(request)
        except Exception as error:
            self._mark_gateway_failure(error)
            raise
        return self._answer(
            checked_question,
            snapshot,
            response,
            answer_contract=self._answer_contract,
            requirement_context=self._requirement_context,
        )

    async def aanswer(self, question: EngineeringQuestion) -> EngineeringAnswer:
        record_engineering_qa_stage(
            "Q0_ENGINEERING_QA_ENTER",
            "PASS",
            engineering_qa_service_instance_available=True,
        )
        try:
            checked_question = self._question(question)
        except Exception as error:  # noqa: BLE001 - fixed, redacted boundary event
            attach_exception_diagnostics(
                error,
                error,
                exception_layer="EngineeringQAService",
                exception_stage="Q4_ENGINEERING_QA_EXCEPTION",
            )
            record_engineering_qa_stage(
                "Q4_ENGINEERING_QA_EXCEPTION", "FAIL", error=error,
                exception_layer="EngineeringQAService",
                exception_stage="Q4_ENGINEERING_QA_EXCEPTION",
            )
            record_prompt_pipeline_stage(
                "P1_TASK_REQUEST_CONSTRUCTION_START",
                "FAIL",
                error=error,
                constraints_valid=False,
                exception_layer="EngineeringQAService",
                exception_stage="Q4_ENGINEERING_QA_EXCEPTION",
            )
            record_prompt_pipeline_not_reached(
                (
                    "P2_REQUIREMENT_CONTEXT_SERIALIZE",
                    "P3_GREENFIELD_PROMPT_SELECT",
                    "P4_PROMPT_BUILDER_CALL",
                    "P5_MODEL_REQUEST_CREATED",
                )
            )
            raise
        record_prompt_pipeline_stage(
            "P1_TASK_REQUEST_CONSTRUCTION_START",
            "PASS",
            constraints_valid=True,
        )
        try:
            snapshot = self._context(checked_question)
            request = self._request(checked_question, snapshot)
        except Exception as error:  # noqa: BLE001 - preserve redacted mapping
            attach_exception_diagnostics(
                error,
                error,
                exception_layer="EngineeringQAService",
                exception_stage="Q4_ENGINEERING_QA_EXCEPTION",
            )
            record_engineering_qa_stage(
                "Q4_ENGINEERING_QA_EXCEPTION", "FAIL", error=error,
                exception_layer="EngineeringQAService",
                exception_stage="Q4_ENGINEERING_QA_EXCEPTION",
            )
            record_prompt_pipeline_not_reached(
                (
                    "P2_REQUIREMENT_CONTEXT_SERIALIZE",
                    "P3_GREENFIELD_PROMPT_SELECT",
                    "P4_PROMPT_BUILDER_CALL",
                    "P5_MODEL_REQUEST_CREATED",
                )
            )
            raise
        record_engineering_qa_not_reached(("Q4_ENGINEERING_QA_EXCEPTION",))
        try:
            response = await self._gateway.agenerate(request)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._mark_gateway_failure(error)
            raise
        return self._answer(
            checked_question,
            snapshot,
            response,
            answer_contract=self._answer_contract,
            requirement_context=self._requirement_context,
        )


__all__ = ("EngineeringQAService",)
