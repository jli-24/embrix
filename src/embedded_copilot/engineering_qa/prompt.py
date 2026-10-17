from __future__ import annotations

import copy
import json

from embedded_copilot.diagnostics import (
    record_engineering_qa_stage,
    record_prompt_pipeline_not_reached,
    record_prompt_pipeline_stage,
)
from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.engineering_requirements import RequirementContext
from embedded_copilot.model_gateway import EngineeringStage, ModelPolicy, ModelRequest
from embedded_copilot.model_gateway.contracts import safe_text
from embedded_copilot.model_gateway.invocation.contracts import (
    InvocationMode,
    ModelInvocationGenerationOptions,
    ModelInvocationPayload,
    ModelInvocationRequest,
    ModelInvocationTraceContext,
)

from .contracts import EngineeringQuestion
from .conversation_policy import (
    ConversationMode,
    resolve_conversation_mode,
    should_offer_requirement_report,
)

_PERSONA = (
    "You are Embrix Engineering Copilot, an experienced engineering collaborator. "
    "Your default behavior is engineering conversation, not requirements-document generation. "
    "When a user describes an engineering idea, understand the goal, ask only the necessary "
    "engineering questions, and continue collaboratively toward design and execution. "
    "RequirementContext is private internal engineering state used by later reasoning, design, "
    "firmware, debug, and verification work. Never expose its JSON, field names, lifecycle, "
    "raw context sections, or internal reasoning structures. Never expose CONFIRMED, UNKNOWN, "
    "PROPOSED, OPEN_QUESTION, Requirement ID, or their Chinese status labels. "
    "Requirement, Architecture, Debug, Test, and Change reports are lifecycle artifacts, not the "
    "default chat format. "
    "Use supplied evidence naturally, do not invent hardware parameters, preserve conflicts, "
    "and never claim source, build, execution, hardware, or test verification without evidence."
)

_EXPLORATION_POLICY = (
    "Internal conversation policy: exploration. Acknowledge the user's goal in one short sentence, "
    "then ask two to four decisive questions such as application scenario, MCU and development "
    "environment, core functions, communication, sensing, or autonomy. Do not produce a full "
    "requirements report, PRD, requirement table, status list, or complete architecture by default. "
    "End by stating what can be designed after the answers arrive. If the user asks a focused factual "
    "question, answer it directly and ask no unnecessary questions."
)

_DESIGN_POLICY = (
    "Internal conversation policy: design. Provide a concise engineering design covering system "
    "architecture, hardware, software architecture, control or data flow, and verification. "
    "Separate supplied facts from engineering proposals without exposing internal status labels. "
    "Do not turn the answer into a requirements-management report unless the user explicitly asks for one."
)

_DOCUMENT_POLICY = (
    "Internal conversation policy: document. The user explicitly requested an engineering document "
    "or report, so a formal structure is allowed. Generate only the requested document type and scope. "
    "The document may contain background, requirements, architecture, acceptance criteria, debug, "
    "test, or change sections as appropriate, but must never expose internal Agent states, raw Context, "
    "JSON, private field names, or hidden reasoning."
)

_DOCUMENT_TYPE_POLICIES = (
    ("需求分析报告", "Include the project goal, requirements, acceptance criteria, and unresolved questions."),
    ("需求报告", "Include the project goal, requirements, acceptance criteria, and unresolved questions."),
    ("架构报告", "Include component boundaries, data or control flow, key assumptions, and verification points."),
    ("架构文档", "Include component boundaries, data or control flow, key assumptions, and verification points."),
    (
        "调试报告",
        "Include the observed symptom, cause analysis or bounded candidate causes, troubleshooting process, parameter changes, and verification result.",
    ),
    ("测试报告", "Include the test environment, test cases, evidence, results, and limitations."),
    ("变更报告", "Include the change scope, rationale, changed items, verification, and remaining risks."),
)

_EXECUTION_POLICY = (
    "Internal conversation policy: execution. First restate the requested engineering task briefly, "
    "then state the bounded plan and use only existing approved execution paths when they are available. "
    "Return the real result and evidence. Never simulate a completed edit, build, test, debug action, "
    "or hardware operation, and never imply that this response itself grants execution permission."
)

_SYSTEM = _PERSONA + " " + _EXPLORATION_POLICY
_GREENFIELD_SYSTEM = _SYSTEM

_MODE_POLICIES = {
    ConversationMode.EXPLORATION: _EXPLORATION_POLICY,
    ConversationMode.DESIGN: _DESIGN_POLICY,
    ConversationMode.DOCUMENT: _DOCUMENT_POLICY,
    ConversationMode.EXECUTION: _EXECUTION_POLICY,
}


def _document_type_instruction(question: str) -> str:
    normalized = question.casefold()
    for marker, instruction in _DOCUMENT_TYPE_POLICIES:
        if marker.casefold() in normalized:
            return " " + instruction
    return ""


def _instruction(
    question: str,
    *,
    requirement_context: RequirementContext | None,
) -> str:
    mode = resolve_conversation_mode(
        question, requirement_context=requirement_context
    )
    instruction = _PERSONA + " " + _MODE_POLICIES[mode]
    if mode is ConversationMode.DOCUMENT:
        instruction += _document_type_instruction(question)
    if should_offer_requirement_report(
        question, requirement_context=requirement_context
    ):
        instruction += (
            " The internal RequirementContext is complete, but the user has not explicitly "
            "requested or confirmed a report. Ask exactly: 当前项目需求已经整理完成，是否生成需求分析报告？ "
            "Do not generate that report in this turn. The standalone acknowledgements 好、好的、继续、可以、下一步 "
            "are not report confirmation."
        )
    return instruction

_GREENFIELD_CONTEXT_CHUNK_SIZE = 180


class PromptBuilder:
    def __init__(
        self,
        *,
        model_policy: ModelPolicy | None = None,
        answer_contract: str = "EVIDENCE_BACKED",
        generation_options: ModelInvocationGenerationOptions | None = None,
        stage_id: EngineeringStage = EngineeringStage.REQUIREMENT_ANALYSIS,
    ) -> None:
        if answer_contract not in {"EVIDENCE_BACKED", "GREENFIELD"}:
            raise ValueError("answer contract is invalid")
        self._policy = model_policy
        self._options = generation_options
        self._stage = stage_id
        if self._policy is None and self._options is None:
            # Direct PromptBuilder callers are the legacy/development boundary.
            # Governed callers always provide generation options and therefore
            # never construct this compatibility policy.
            self._policy = ModelPolicy(primary_provider="local", model_name="offline")
        self._answer_contract = answer_contract

    @staticmethod
    def _serialize(snapshot: EngineeringContextSnapshot) -> str:
        groups = (
            ("Memory Evidence", snapshot.memory_evidence),
            ("Graph Evidence", snapshot.graph_evidence),
            ("Knowledge Evidence", snapshot.knowledge_evidence),
            ("Multimodal Evidence", snapshot.multimodal_evidence),
        )
        sections: list[str] = []
        for title, values in groups:
            payload = [item.model_dump(mode="json") for item in sorted(values, key=lambda item: item.evidence_id)]
            sections.append(f"{title}:" + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        conflicts = [item.model_dump(mode="json") for item in sorted(snapshot.conflicts, key=lambda item: item.conflict_id)]
        provenance = [item.model_dump(mode="json") for item in sorted(snapshot.provenance, key=lambda item: item.source_id)]
        sections.append("Conflicts:" + json.dumps(conflicts, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        sections.append("Provenance:" + json.dumps(provenance, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return " ".join(sections)

    @staticmethod
    def _metadata(value: str | None, *, field: str) -> str:
        if value is None:
            return "UNKNOWN"
        if not isinstance(value, str):
            raise TypeError(f"{field} is invalid")
        checked = value.strip()
        if not checked or len(checked) > 128 or "\n" in checked or "\r" in checked:
            raise ValueError(f"{field} is invalid")
        return checked

    @classmethod
    def _greenfield_constraints(
        cls,
        constraints: tuple[str, ...],
        *,
        requirement_context: RequirementContext | None,
        project_mode: str | None,
        project_type: str | None,
    ) -> tuple[str, ...]:
        mode = cls._metadata(project_mode or "CREATE_NEW", field="project_mode")
        kind = cls._metadata(project_type, field="project_type")
        if requirement_context is None:
            serialized = "null"
        else:
            checked = RequirementContext.model_validate(
                copy.deepcopy(requirement_context.model_dump(mode="python"))
            )
            serialized = json.dumps(
                checked.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        safe_text(
            serialized,
            field="requirement_context",
            maximum=16384,
        )
        chunks = tuple(
            serialized[index : index + _GREENFIELD_CONTEXT_CHUNK_SIZE]
            for index in range(0, len(serialized), _GREENFIELD_CONTEXT_CHUNK_SIZE)
        ) or ("null",)
        total = len(chunks)
        projection = (
            f"Project mode: {mode}",
            f"Project type: {kind}",
            *tuple(
                f"RequirementContext part {index}/{total}: {chunk} [end part]"
                for index, chunk in enumerate(chunks, start=1)
            ),
        )
        return (*constraints, *projection)

    def build(
        self,
        question: EngineeringQuestion,
        snapshot: EngineeringContextSnapshot,
        *,
        requirement_context: RequirementContext | None = None,
        project_mode: str | None = None,
        project_type: str | None = None,
        task_type: str | None = None,
    ) -> ModelRequest | ModelInvocationRequest:
        checked_question = EngineeringQuestion.model_validate(copy.deepcopy(question.model_dump(mode="python")))
        checked_snapshot = EngineeringContextSnapshot.model_validate(copy.deepcopy(snapshot.model_dump(mode="python")))
        instruction = _instruction(
            checked_question.question,
            requirement_context=(
                requirement_context
                if self._answer_contract == "GREENFIELD"
                else None
            ),
        )
        constraints = checked_question.constraints
        if self._answer_contract == "GREENFIELD":
            try:
                constraints = self._greenfield_constraints(
                    constraints,
                    requirement_context=requirement_context,
                    project_mode=project_mode,
                    project_type=project_type,
                )
            except Exception as error:  # noqa: BLE001 - fixed, redacted event
                record_prompt_pipeline_stage(
                    "P2_REQUIREMENT_CONTEXT_SERIALIZE",
                    "FAIL",
                    error=error,
                    context_projection_valid=False,
                )
                record_prompt_pipeline_not_reached(
                    (
                        "P3_GREENFIELD_PROMPT_SELECT",
                        "P4_PROMPT_BUILDER_CALL",
                        "P5_MODEL_REQUEST_CREATED",
                    )
                )
                raise
            record_prompt_pipeline_stage(
                "P2_REQUIREMENT_CONTEXT_SERIALIZE",
                "PASS",
                context_projection_valid=True,
            )
            prompt_available = isinstance(_GREENFIELD_SYSTEM, str) and bool(
                _GREENFIELD_SYSTEM.strip()
            )
            if prompt_available:
                record_prompt_pipeline_stage(
                    "P3_GREENFIELD_PROMPT_SELECT",
                    "PASS",
                    prompt_module_loaded=True,
                    greenfield_prompt_available=True,
                )
            else:
                error = ValueError("greenfield prompt unavailable")
                record_prompt_pipeline_stage(
                    "P3_GREENFIELD_PROMPT_SELECT",
                    "FAIL",
                    error=error,
                    prompt_module_loaded=True,
                    greenfield_prompt_available=False,
                )
                record_prompt_pipeline_not_reached(
                    ("P4_PROMPT_BUILDER_CALL", "P5_MODEL_REQUEST_CREATED")
                )
        try:
            content = {"task": checked_question.question, "engineering_context": checked_snapshot,
                "system_instruction": instruction + " Context:" + self._serialize(checked_snapshot), "constraints": constraints}
            if self._options is not None:
                request = ModelInvocationRequest(request_id="request-" + checked_question.question_id,
                    project_id=checked_question.project_id, stage_id=self._stage, invocation_mode=InvocationMode.CHAT,
                    task_type=task_type or self._stage.value, payload=ModelInvocationPayload(**content),
                    generation_options=self._options,
                    trace_context=ModelInvocationTraceContext(trace_id="request-" + checked_question.question_id))
            else:
                request = ModelRequest(**content, request_id="request-" + checked_question.question_id,
                    model_policy=self._policy, task_type=task_type)

        except Exception as error:  # noqa: BLE001 - fixed, redacted event
            record_engineering_qa_stage(
                "Q3_MODEL_REQUEST_CONSTRUCTION",
                "FAIL",
                error=error,
                model_request_schema_valid=False,
            )
            record_prompt_pipeline_stage(
                "P5_MODEL_REQUEST_CREATED",
                "FAIL",
                error=error,
                model_request_schema_valid=False,
            )
            raise
        record_prompt_pipeline_stage(
            "P5_MODEL_REQUEST_CREATED",
            "PASS",
            model_request_schema_valid=True,
        )
        record_engineering_qa_stage(
            "Q3_MODEL_REQUEST_CONSTRUCTION",
            "PASS",
            model_request_schema_valid=True,
        )
        return request


__all__ = ("PromptBuilder",)
