from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from embedded_copilot.agents.debug import DebugAgent
from embedded_copilot.application.exceptions import ApplicationInsufficientEvidence
from embedded_copilot.application.models import ApplicationQuestionRequest
from embedded_copilot.debug_intelligence import DebugIntelligenceService
from embedded_copilot.engineering_context import EngineeringContextEvidence
from embedded_copilot.engineering_qa import EngineeringQuestion, PromptBuilder
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
    DebugResult,
    ErrorCode,
    ErrorDetail,
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
from embedded_copilot.tools.debug_tool import DebugLogInput

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DebugAgentAnalysis:
    facts: tuple[str, ...]
    evidence: tuple[str, ...]
    analysis: tuple[str, ...]
    confidence: float
    limitations: tuple[str, ...]
    context_evidence: tuple[EngineeringContextEvidence, ...]


class _DeterministicDebugTool:
    def __init__(self, service: DebugIntelligenceService, request: ApplicationQuestionRequest, context: object) -> None:
        self._service = service
        self._request = request
        self._context = context

    def _safe_source(self, question: str) -> str:
        values = [question]
        for field in ("memory_evidence", "graph_evidence", "knowledge_evidence", "multimodal_evidence"):
            for item in getattr(self._context, field, ()):
                summary = getattr(item, "summary", "")
                fact_value = getattr(item, "fact_value", "")
                if isinstance(summary, str):
                    values.append(summary[:512])
                if isinstance(fact_value, str):
                    values.append(fact_value[:256])
        return "\n".join(values)[:100_000]

    async def invoke(self, payload: DebugLogInput) -> ToolResult[DebugResult]:
        try:
            result = self._service.analyze(
                tenant_id=self._request.tenant_id,
                project_id=self._request.project_id,
                artifact_id=self._request.request_id,
                source=self._safe_source(payload.log),
                source_reference=f"artifact:{self._request.request_id}",
            )
            level = "high" if result.confidence >= 0.8 else "medium" if result.confidence >= 0.5 else "low"
            return ToolResult(
                status=ToolStatus.SUCCESS,
                data=DebugResult(
                    problem_type="structured_debug_artifact",
                    evidence=list(result.observations),
                    root_cause=list(result.root_cause_candidates),
                    confidence=level,
                    solution=list(result.validation_steps),
                    next_steps=list(result.missing_evidence),
                ),
            )
        except ValueError:
            return ToolResult(
                status=ToolStatus.ERROR,
                error=ErrorDetail(code=ErrorCode.VALIDATION_ERROR, message="Debug artifact is invalid.", retryable=False),
            )


def _initial_state(request: ApplicationQuestionRequest) -> AgentState:
    return AgentState(
        trace_id=request.trace_id,
        user_input=request.question,
        intent=Intent.DEBUG,
        selected_agents=[AgentName.DEBUG],
        messages=[MessageRecord(role="user", content=request.question)],
        results=[],
        sources=[],
        errors=[],
        final_answer="",
        status=WorkflowStatus.ROUTING,
    )


class DebugAgentApplicationAdapter:
    """Runs the existing DebugAgent and exposes only typed specialist output."""

    def __init__(self, service: DebugIntelligenceService | None = None) -> None:
        self._service = service or DebugIntelligenceService()

    async def aanalyze(self, request: ApplicationQuestionRequest, context: object) -> DebugAgentAnalysis:
        logger.info(
            "debug_agent_execution",
            extra={"event_name": "agent_execution", "agent_name": "debug", "trace_id": request.trace_id, "project_id": request.project_id, "service_identity": "DebugIntelligenceService"},
        )
        agent = DebugAgent(tool=_DeterministicDebugTool(self._service, request, context))
        update = await agent.run(_initial_state(request))
        results = update.get("results", ())
        if not isinstance(results, list) or not results or not isinstance(results[-1], DebugResult):
            raise ApplicationInsufficientEvidence()
        data = results[-1]
        # Re-run the bounded deterministic parser to retain evidence objects; the Agent remains the formal executor.
        parsed = self._service.analyze(
            tenant_id=request.tenant_id,
            project_id=request.project_id,
            artifact_id=request.request_id,
            source=_DeterministicDebugTool(self._service, request, context)._safe_source(request.question),
            source_reference=f"artifact:{request.request_id}",
        )
        logger.info(
            "debug_agent_runtime_trace",
            extra={
                "event_name": "agent_execution",
                "agent_name": "debug",
                "trace_id": request.trace_id,
                "project_id": request.project_id,
                "service_identity": "DebugIntelligenceService",
                "evidence_count": len(parsed.evidence),
                "finding_count": len(parsed.root_cause_candidates),
            },
        )
        return DebugAgentAnalysis(
            facts=tuple(data.evidence),
            evidence=tuple(item.evidence_id for item in parsed.evidence),
            analysis=tuple(data.root_cause),
            confidence={"low": 0.25, "medium": 0.6, "high": 0.9}[data.confidence],
            limitations=tuple(data.next_steps),
            context_evidence=parsed.evidence,
        )

    def analyze(self, request: ApplicationQuestionRequest, context: object) -> DebugAgentAnalysis:
        import asyncio
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.aanalyze(request, context))
        raise RuntimeError("synchronous DebugAgent execution is unavailable in an event loop")
class DebugPromptAdapter:
    def request_for(self, question: EngineeringQuestion, snapshot, analysis: dict[str, object], policy: ModelPolicy | None, *, generation_options: ModelInvocationGenerationOptions | None = None) -> ModelRequest | ModelInvocationRequest:
        base = PromptBuilder(model_policy=policy, generation_options=generation_options, stage_id=EngineeringStage.DEBUGGING).build(question, snapshot)
        structured = json.dumps(analysis, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        material = (base.payload if isinstance(base, ModelInvocationRequest) else base).model_dump(mode="python")
        material["system_instruction"] = (base.payload if isinstance(base, ModelInvocationRequest) else base).system_instruction + " Debug specialist analysis:" + structured
        material["task_type"] = "DEBUG"
        material.pop("prompt", None)
        material.pop("system_prompt", None)
        material["fingerprint"] = "sha256:" + "0" * 64
        if isinstance(base, ModelInvocationRequest):
            task_type = material.pop("task_type")
            envelope = base.model_dump(mode="python")
            envelope.update(payload=ModelInvocationPayload.model_validate(material), task_type=task_type)
            return ModelInvocationRequest.model_validate(envelope)
        return ModelRequest.model_validate(material)


__all__ = ("DebugAgentAnalysis", "DebugAgentApplicationAdapter", "DebugPromptAdapter")
