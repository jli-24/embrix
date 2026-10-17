from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from embedded_copilot.agents.firmware import FirmwareAgent
from embedded_copilot.application.exceptions import ApplicationInsufficientEvidence
from embedded_copilot.application.models import ApplicationQuestionRequest
from embedded_copilot.engineering_context import (
    EngineeringContextEvidence,
    EngineeringContextSnapshot,
)
from embedded_copilot.engineering_qa import EngineeringQuestion, PromptBuilder
from embedded_copilot.firmware_intelligence.service import (
    FirmwareIntelligenceResult,
    FirmwareIntelligenceService,
)
from embedded_copilot.firmware_intelligence.source import FirmwareSourceUnavailable
from embedded_copilot.model_gateway import (
    EngineeringStage,
    ModelPolicy,
    ModelRequest,
)
from embedded_copilot.model_gateway.invocation.contracts import (
    ModelInvocationGenerationOptions,
    ModelInvocationPayload,
    ModelInvocationRequest,
)
from embedded_copilot.schemas.result import (
    ErrorCode,
    ErrorDetail,
    FirmwareResult,
    ToolResult,
    ToolStatus,
)
from embedded_copilot.schemas.state import (
    AgentName,
    AgentState,
    Intent,
    MessageRecord,
    WorkflowStatus,
)
from embedded_copilot.tools.code_tool import CodeAnalysisInput

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FirmwareAgentAnalysis:
    """Internal specialist result; it is not a user-facing answer or DTO."""

    facts: tuple[str, ...]
    evidence: tuple[str, ...]
    analysis: tuple[str, ...]
    confidence: float
    limitations: tuple[str, ...]
    context_evidence: tuple[EngineeringContextEvidence, ...] = ()


class _ContextFirmwareAnalysisTool:
    """Deterministic bridge used by the existing FirmwareAgent.

    Milestone 1 activates the existing agent without adding a second model
    answer path. The tool only packages already verified context as structured
    specialist input; ApplicationRuntime owns the later model call.
    """

    __slots__ = ("_context", "_firmware")

    def __init__(
        self, context: object, firmware: FirmwareIntelligenceResult | None = None
    ) -> None:
        self._context = context
        self._firmware = firmware

    async def invoke(
        self, payload: CodeAnalysisInput
    ) -> ToolResult[FirmwareResult]:
        evidence = tuple(
            item
            for group in (
                getattr(self._context, "memory_evidence", ()),
                getattr(self._context, "graph_evidence", ()),
                getattr(self._context, "knowledge_evidence", ()),
                getattr(self._context, "multimodal_evidence", ()),
            )
            for item in group
        )
        source_evidence = () if self._firmware is None else self._firmware.evidence
        all_evidence = (*evidence, *source_evidence)
        if not all_evidence:
            return ToolResult[FirmwareResult](
                status=ToolStatus.ERROR,
                error=ErrorDetail(
                    code=ErrorCode.RETRIEVAL_ERROR,
                    message="Firmware evidence is unavailable.",
                    retryable=False,
                ),
            )
        facts = tuple(
            str(getattr(item, "summary", ""))[:512]
            for item in evidence
            if str(getattr(item, "summary", "")).strip()
        ) + (() if self._firmware is None else self._firmware.facts)
        references = tuple(
            str(getattr(item, "evidence_id", ""))
            for item in all_evidence
            if str(getattr(item, "evidence_id", "")).strip()
        )
        analysis = {
            "facts": facts,
            "evidence": references,
            "analysis": (
                "Firmware task routed to the existing FirmwareAgent.",
                *(() if self._firmware is None else self._firmware.analysis),
            ),
            "confidence": (
                float(getattr(self._context, "confidence", 0.0))
                if self._firmware is None
                else self._firmware.confidence
            ),
            "limitations": (
                ("Firmware source evidence is unavailable.",)
                if self._firmware is None
                else self._firmware.limitations
            ),
        }
        return ToolResult[FirmwareResult](
            status=ToolStatus.SUCCESS,
            data=FirmwareResult(
                language=payload.language,
                platform=payload.platform,
                code="",
                explanation=json.dumps(
                    analysis, ensure_ascii=False, separators=(",", ":")
                ),
                limitations=list(analysis["limitations"]),
            ),
        )


def _initial_state(request: ApplicationQuestionRequest) -> AgentState:
    return AgentState(
        trace_id=request.trace_id,
        user_input=request.question,
        intent=Intent.FIRMWARE,
        selected_agents=[AgentName.FIRMWARE],
        messages=[MessageRecord(role="user", content=request.question)],
        results=[],
        sources=[],
        errors=[],
        final_answer="",
        status=WorkflowStatus.ROUTING,
    )


def _analysis(
    value: object,
    *,
    context_evidence: tuple[EngineeringContextEvidence, ...] = (),
) -> FirmwareAgentAnalysis:
    if not isinstance(value, FirmwareResult):
        raise ValueError("firmware agent result is invalid")
    try:
        raw = json.loads(value.explanation)
    except (TypeError, ValueError) as error:
        raise ValueError("firmware agent analysis is invalid") from error
    if not isinstance(raw, dict):
        raise ValueError("firmware agent analysis is invalid")
    facts = tuple(item for item in raw.get("facts", ()) if isinstance(item, str))
    evidence = tuple(item for item in raw.get("evidence", ()) if isinstance(item, str))
    findings = tuple(item for item in raw.get("analysis", ()) if isinstance(item, str))
    limitations = tuple(
        item for item in raw.get("limitations", ()) if isinstance(item, str)
    )
    confidence = raw.get("confidence")
    if type(confidence) is not float or not 0.0 <= confidence <= 1.0:
        raise ValueError("firmware agent confidence is invalid")
    if not evidence:
        raise ValueError("firmware agent evidence is empty")
    return FirmwareAgentAnalysis(
        facts=facts,
        evidence=evidence,
        analysis=findings,
        confidence=confidence,
        limitations=limitations,
        context_evidence=context_evidence,
    )


class FirmwareAgentApplicationAdapter:
    """Adapt the existing FirmwareAgent to the Application Runtime Port."""

    __slots__ = ("_intelligence",)

    def __init__(self, intelligence: FirmwareIntelligenceService | None = None) -> None:
        if intelligence is not None and not isinstance(
            intelligence, FirmwareIntelligenceService
        ):
            raise TypeError("firmware intelligence service is invalid")
        self._intelligence = intelligence

    def analyze(
        self, request: ApplicationQuestionRequest, context: object
    ) -> FirmwareAgentAnalysis:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.aanalyze(request, context))
        raise RuntimeError("synchronous FirmwareAgent execution is unavailable in an event loop")

    async def aanalyze(
        self, request: ApplicationQuestionRequest, context: object
    ) -> FirmwareAgentAnalysis:
        logger.info(
            "firmware_agent_execution",
            extra={
                "event_name": "agent_selected",
                "agent_name": "firmware",
                "trace_id": request.trace_id,
            },
        )
        firmware = None
        if self._intelligence is not None:
            try:
                firmware = self._intelligence.analyze(
                    request.project_id,
                    request.question,
                    tenant_id=request.tenant_id,
                    context_evidence=tuple(
                        item
                        for group in (
                            getattr(context, "memory_evidence", ()),
                            getattr(context, "graph_evidence", ()),
                            getattr(context, "knowledge_evidence", ()),
                            getattr(context, "multimodal_evidence", ()),
                        )
                        for item in group
                    ),
                )
            except (FirmwareSourceUnavailable, ValueError) as error:
                raise ApplicationInsufficientEvidence() from error
            if not firmware.evidence:
                reason_code = "NO_ANALYZABLE_SOURCE"
                try:
                    source_snapshot = self._intelligence.source_snapshot(
                        request.project_id
                    )
                    if (
                        not source_snapshot.sources
                        and not source_snapshot.artifacts
                        and self._intelligence.is_empty_project(request.project_id)
                    ):
                        reason_code = "EMPTY_PROJECT"
                except (FirmwareSourceUnavailable, OSError, ValueError):
                    pass
                raise ApplicationInsufficientEvidence(
                    context=context, reason_code=reason_code
                )
            logger.info(
                "firmware_agent_runtime_trace",
                extra={
                    "event_name": "agent_execution",
                    "agent_name": "firmware",
                    "trace_id": request.trace_id,
                    "project_id": request.project_id,
                    "source_provider": "FirmwareSourceProvider",
                    "parser": "TreeSitterCodeParser",
                    "evidence_count": len(firmware.evidence),
                },
            )
        elif not any(
            getattr(context, field, ())
            for field in (
                "memory_evidence",
                "graph_evidence",
                "knowledge_evidence",
                "multimodal_evidence",
            )
        ):
            raise ApplicationInsufficientEvidence(context=context)
        agent = FirmwareAgent(tool=_ContextFirmwareAnalysisTool(context, firmware))
        update = await agent.run(_initial_state(request))
        results = update.get("results", ())
        if not isinstance(results, list) or not results:
            raise ValueError("firmware agent returned no result")
        return _analysis(
            results[-1],
            context_evidence=() if firmware is None else firmware.evidence,
        )
class FirmwarePromptAdapter:
    """Build the final model request outside the Application security boundary."""

    def request_for(
        self,
        question: EngineeringQuestion,
        snapshot: EngineeringContextSnapshot,
        analysis: dict[str, object],
        policy: ModelPolicy | None,
        *, generation_options: ModelInvocationGenerationOptions | None = None,
    ) -> ModelRequest | ModelInvocationRequest:
        base = PromptBuilder(model_policy=policy, generation_options=generation_options, stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT).build(question, snapshot)
        structured = json.dumps(
            analysis, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        material = (base.payload if isinstance(base, ModelInvocationRequest) else base).model_dump(mode="python")
        material["system_instruction"] = (
            (base.payload if isinstance(base, ModelInvocationRequest) else base).system_instruction + " Firmware specialist analysis:" + structured
        )
        material["task_type"] = "FIRMWARE"
        material.pop("prompt", None)
        material.pop("system_prompt", None)
        material["fingerprint"] = "sha256:" + "0" * 64
        if isinstance(base, ModelInvocationRequest):
            task_type = material.pop("task_type")
            envelope = base.model_dump(mode="python")
            envelope.update(payload=ModelInvocationPayload.model_validate(material), task_type=task_type)
            return ModelInvocationRequest.model_validate(envelope)
        return ModelRequest.model_validate(material)


__all__ = (
    "FirmwareAgentAnalysis",
    "FirmwareAgentApplicationAdapter",
    "FirmwarePromptAdapter",
)
