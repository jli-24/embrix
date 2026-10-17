from __future__ import annotations

import asyncio

import pytest

from embedded_copilot.engineering_context import (
    ContextSourceType,
    EngineeringContextEvidence,
    EngineeringContextSnapshot,
)
from embedded_copilot.engineering_qa import (
    EngineeringAnswerRejected,
    EngineeringQAService,
    EngineeringQuestion,
    InsufficientEvidence,
    PromptBuilder,
)
from embedded_copilot.engineering_qa.prompt import _GREENFIELD_SYSTEM
from embedded_copilot.engineering_requirements import (
    RequirementContext,
    RequirementLifecycle,
    merge_requirement_context,
)
from embedded_copilot.model_gateway import ModelResponse


def _snapshot() -> EngineeringContextSnapshot:
    evidence = EngineeringContextEvidence.create(
        evidence_id="memory-1", project_id="project-1", source_type=ContextSourceType.ENGINEERING_MEMORY,
        subject="GPIO12", summary="GPIO12 is reserved", fact_key="gpio12.function", fact_value="reserved",
        source_reference="memory:1", source_fingerprint="sha256:" + "a" * 64,
        confidence=0.8, verification_status="APPROVED",
    )
    return EngineeringContextSnapshot.create(
        project_id="project-1", task_description="Can GPIO12 be used?", memory_evidence=(evidence,),
        graph_evidence=(), knowledge_evidence=(), multimodal_evidence=(), conflicts=(), confidence=0.8,
        provenance=(evidence.source(),), verification_status="APPROVED",
    )


class _Context:
    def __init__(self, value: EngineeringContextSnapshot | None) -> None:
        self.value = value

    def get_context(self, request):
        return self.value


class _Gateway:
    def __init__(self, content: str = "No, GPIO12 is reserved.") -> None:
        self.calls = 0
        self.last_request = None
        self.content = content

    def generate(self, request):
        self.calls += 1
        self.last_request = request
        return ModelResponse(content=self.content, model_name=request.model_policy.model_name, provider="local", confidence=0.6, provenance=request.engineering_context.provenance)

    async def agenerate(self, request):
        return self.generate(request)


def _question() -> EngineeringQuestion:
    return EngineeringQuestion(question_id="question-1", project_id="project-1", question="Can GPIO12 be used?")


def _greenfield_question(value: str = "STM32") -> EngineeringQuestion:
    return EngineeringQuestion(
        question_id="question-greenfield",
        project_id="project-1",
        question=value,
    )


def _greenfield_snapshot(value: str = "STM32") -> EngineeringContextSnapshot:
    return EngineeringContextSnapshot.create(
        project_id="project-1",
        task_description=value,
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.0,
        provenance=(),
        verification_status="UNVERIFIED",
    )


def _requirement_context(value: str = "STM32"):
    return merge_requirement_context(
        None,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="question-greenfield",
        utterance=value,
        project_type="STM32",
    )


def test_context_first_question_to_answer_and_conservative_confidence() -> None:
    gateway = _Gateway()
    answer = EngineeringQAService(context_provider=_Context(_snapshot()), model_gateway=gateway).answer(_question())
    assert answer.content == "No, GPIO12 is reserved."
    assert answer.confidence == 0.6
    assert answer.evidence_refs == ("memory-1",)
    assert answer.model_info.provider == "local"
    assert gateway.calls == 1


def test_empty_context_does_not_call_model_and_async_path_is_available() -> None:
    gateway = _Gateway()
    service = EngineeringQAService(context_provider=_Context(None), model_gateway=gateway)
    with pytest.raises(InsufficientEvidence):
        service.answer(_question())
    assert gateway.calls == 0
    answer = asyncio.run(EngineeringQAService(context_provider=_Context(_snapshot()), model_gateway=gateway).aanswer(_question()))
    assert answer.question_id == "question-1"


def test_prompt_builder_is_stable_and_includes_evidence_sections() -> None:
    request = PromptBuilder().build(_question(), _snapshot())
    assert "Memory Evidence" in request.system_instruction
    assert "Graph Evidence" in request.system_instruction
    assert "Knowledge Evidence" in request.system_instruction
    assert "Multimodal Evidence" in request.system_instruction
    assert "Conflicts" in request.system_instruction
    assert request.system_instruction == PromptBuilder().build(_question(), _snapshot()).system_instruction


def test_greenfield_prompt_contract_required_sections() -> None:
    for required in (
        "Embrix Engineering Copilot",
        "engineering conversation",
        "RequirementContext is private internal engineering state",
        "ask two to four decisive questions",
        "Do not produce a full requirements report",
    ):
        assert required in _GREENFIELD_SYSTEM
    assert "Greenfield requirement discovery contract" not in _GREENFIELD_SYSTEM


def test_greenfield_prompt_projects_discovery_context_into_user_constraints() -> None:
    request = PromptBuilder(answer_contract="GREENFIELD").build(
        _greenfield_question(),
        _greenfield_snapshot(),
        requirement_context=_requirement_context(),
        project_mode="CREATE_NEW",
        project_type="STM32",
    )

    assert "RequirementContext is private internal engineering state" in request.system_instruction
    assert "Never expose its JSON" in request.system_instruction
    assert "Do not produce a full requirements report" in request.system_instruction
    projected = " ".join(request.constraints)
    assert "CREATE_NEW" in projected
    assert "STM32" in projected
    assert "RequirementContext" in projected


def test_greenfield_prompt_pipeline_emits_redacted_boundary_events(caplog) -> None:
    with caplog.at_level("INFO", logger="embedded_copilot.runtime_diagnostics"):
        service = EngineeringQAService(
            context_provider=_Context(None),
            model_gateway=_Gateway(),
            require_evidence=False,
            answer_contract="GREENFIELD",
            requirement_context=_requirement_context(),
            project_mode="CREATE_NEW",
            project_type="STM32",
        )
        service.answer(_greenfield_question())

    records = [
        record
        for record in caplog.records
        if record.getMessage() == "prompt_request_boundary_telemetry"
    ]
    assert {
        record.prompt_pipeline_stage for record in records
    } == {
        "P0_ENGINEERING_QA_SERVICE_ENTER",
        "P1_TASK_REQUEST_CONSTRUCTION_START",
        "P2_REQUIREMENT_CONTEXT_SERIALIZE",
        "P3_GREENFIELD_PROMPT_SELECT",
        "P4_PROMPT_BUILDER_CALL",
        "P5_MODEL_REQUEST_CREATED",
    }
    assert all(record.telemetry_status == "PASS" for record in records)
    assert all(record.exception_type_hash == "-" for record in records)
    assert all(record.reason_code == "-" for record in records)
    by_stage = {record.prompt_pipeline_stage: record for record in records}
    assert by_stage["P0_ENGINEERING_QA_SERVICE_ENTER"].engineering_qa_service_available is True
    assert by_stage["P0_ENGINEERING_QA_SERVICE_ENTER"].prompt_module_loaded is True
    assert by_stage["P1_TASK_REQUEST_CONSTRUCTION_START"].constraints_valid is True
    assert by_stage["P2_REQUIREMENT_CONTEXT_SERIALIZE"].context_projection_valid is True
    assert by_stage["P3_GREENFIELD_PROMPT_SELECT"].greenfield_prompt_available is True
    assert by_stage["P5_MODEL_REQUEST_CREATED"].model_request_schema_valid is True
    assert all("question" not in record.__dict__ for record in records)
    assert all("project" not in record.__dict__ for record in records)


def test_prompt_builder_exception_preserves_original_metadata(monkeypatch, caplog) -> None:
    gateway = _Gateway()

    def fail_build(*_args, **_kwargs):
        raise RuntimeError("private prompt builder detail")

    monkeypatch.setattr(PromptBuilder, "build", fail_build)
    service = EngineeringQAService(
        context_provider=_Context(None),
        model_gateway=gateway,
        require_evidence=False,
        answer_contract="GREENFIELD",
        requirement_context=_requirement_context(),
        project_mode="CREATE_NEW",
        project_type="STM32",
    )

    with caplog.at_level("INFO", logger="embedded_copilot.runtime_diagnostics"):
        with pytest.raises(RuntimeError) as raised:
            service.answer(_greenfield_question())

    error = raised.value
    assert error.original_exception_type == "RuntimeError"
    assert len(error.original_exception_hash) == 64
    assert error.exception_layer == "ENGINEERINGQASERVICE"
    assert error.exception_stage == "A7_PROMPT_BUILDER_ENTER"
    assert len(error.traceback_fingerprint) == 64
    assert gateway.calls == 0
    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "prompt_request_boundary_telemetry"
        and record.prompt_pipeline_stage == "P4_PROMPT_BUILDER_CALL"
        and record.telemetry_status == "FAIL"
    )
    assert record.original_exception_type == "RuntimeError"
    assert record.exception_layer == "ENGINEERINGQASERVICE"
    assert record.exception_stage == "A7_PROMPT_BUILDER_ENTER"
    assert "private prompt builder detail" not in str(record.__dict__)


def test_greenfield_design_request_uses_collaborative_design_instructions() -> None:
    question = (
        "制作一个用于电赛的智能小车，生成工程需求理解、软件架构设计、模块划分、"
        "任务调度方案和后续开发建议"
    )
    request = PromptBuilder(answer_contract="GREENFIELD").build(
        _greenfield_question(question),
        _greenfield_snapshot(question),
        requirement_context=_requirement_context(question),
        project_mode="CREATE_NEW",
        project_type="STM32",
    )

    for required in (
        "Internal conversation policy: design",
        "system architecture",
        "hardware",
        "software architecture",
        "verification",
    ):
        assert required in request.system_instruction


@pytest.mark.parametrize(
    ("question", "required_instruction"),
    (
        ("生成需求分析报告", "acceptance criteria"),
        ("生成架构报告", "component boundaries"),
        ("生成 PID 调试报告", "parameter changes"),
        ("生成电机控制测试报告", "test cases"),
        ("生成变更报告", "change scope"),
    ),
)
def test_document_prompt_policy_preserves_report_type_boundaries(
    question: str,
    required_instruction: str,
) -> None:
    request = PromptBuilder(answer_contract="GREENFIELD").build(
        _greenfield_question(question),
        _greenfield_snapshot(question),
        requirement_context=_requirement_context(question),
        project_mode="CREATE_NEW",
        project_type="STM32",
    )

    assert "Internal conversation policy: document" in request.system_instruction
    assert required_instruction in request.system_instruction
    assert "raw Context" in request.system_instruction


def test_model_response_is_cleaned_before_engineering_answer_is_exposed() -> None:
    gateway = _Gateway(
        content=(
            "# 项目需求分析\n"
            "已确认需求：STM32。\n"
            "REQUIREMENTS_READY\n"
            "TARGET_PLATFORM: STM32\n"
            "请先确认传感器和电机驱动。"
        )
    )
    service = EngineeringQAService(
        context_provider=_Context(None),
        model_gateway=gateway,
        require_evidence=False,
        answer_contract="GREENFIELD",
        requirement_context=_requirement_context(),
        project_mode="CREATE_NEW",
        project_type="STM32",
    )

    answer = service.answer(_greenfield_question("我要做一个 STM32 智能小车"))

    assert answer.content == "请先确认传感器和电机驱动。"
    assert "项目需求分析" not in answer.content
    assert "已确认需求" not in answer.content
    assert "REQUIREMENTS_READY" not in answer.content
    assert "TARGET_PLATFORM" not in answer.content


def test_ready_requirement_context_requires_explicit_report_confirmation() -> None:
    ready_context = RequirementContext(
        project_id="project-1",
        conversation_id="conversation-1",
        lifecycle=RequirementLifecycle.REQUIREMENTS_READY,
    )
    service = EngineeringQAService(
        context_provider=_Context(None),
        model_gateway=_Gateway(content="# 项目需求分析\n已确认需求：STM32。"),
        require_evidence=False,
        answer_contract="GREENFIELD",
        requirement_context=ready_context,
        project_mode="CREATE_NEW",
        project_type="STM32",
    )

    answer = service.answer(_greenfield_question("继续"))

    assert answer.content == "当前项目需求已经整理完成，是否生成需求分析报告？"


def test_greenfield_contract_accepts_plain_text_without_evidence(caplog) -> None:
    gateway = _Gateway()
    service = EngineeringQAService(
        context_provider=_Context(None),
        model_gateway=gateway,
        require_evidence=False,
        answer_contract="GREENFIELD",
        requirement_context=_requirement_context(),
        project_mode="CREATE_NEW",
        project_type="STM32",
    )

    with caplog.at_level(
        "INFO", logger="embedded_copilot.engineering_qa.service"
    ):
        answer = service.answer(_question())

    assert answer.content == "No, GPIO12 is reserved."
    assert answer.evidence_refs == ()
    assert gateway.calls == 1
    assert gateway.last_request is not None
    assert "Embrix Engineering Copilot" in gateway.last_request.system_instruction
    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "provider_response_boundary_telemetry"
    )
    assert record.response_stage == "R5_GREENFIELD_CONTRACT_VALIDATE"
    assert record.validation_status == "PASS"
    assert record.validation_error_code == "-"


def test_malformed_gateway_response_reports_safe_first_bad_stage(caplog) -> None:
    class _MalformedGateway:
        def generate(self, request):
            del request
            return object()

        async def agenerate(self, request):
            return self.generate(request)

    service = EngineeringQAService(
        context_provider=_Context(None),
        model_gateway=_MalformedGateway(),
        require_evidence=False,
        answer_contract="GREENFIELD",
    )

    with caplog.at_level(
        "INFO", logger="embedded_copilot.engineering_qa.service"
    ):
        with pytest.raises(EngineeringAnswerRejected) as raised:
            service.answer(_question())

    assert raised.value.reason_code == "ANSWER_SCHEMA_INVALID"
    assert raised.value.validation_stage == "MODEL_RESPONSE_NORMALIZATION"
    assert raised.value.gateway_invocation_count == 1
    assert raised.value.provider_response_present is True
    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "provider_response_boundary_telemetry"
    )
    assert record.response_stage == "R5_GREENFIELD_CONTRACT_VALIDATE"
    assert record.validation_status == "FAIL"
    assert record.validation_error_code == "ANSWER_SCHEMA_INVALID"


def test_evidence_backed_contract_reports_provenance_mismatch() -> None:
    class _MismatchedGateway:
        def generate(self, request):
            return ModelResponse(
                content="Ungrounded answer.",
                model_name=request.model_policy.model_name,
                provider="local",
                confidence=0.5,
                provenance=(),
            )

        async def agenerate(self, request):
            return self.generate(request)

    service = EngineeringQAService(
        context_provider=_Context(_snapshot()),
        model_gateway=_MismatchedGateway(),
        answer_contract="EVIDENCE_BACKED",
    )

    with pytest.raises(EngineeringAnswerRejected) as raised:
        service.answer(_question())

    assert raised.value.reason_code == "ANSWER_REJECTED_EVIDENCE_MISMATCH"
    assert raised.value.validation_stage == "PROVENANCE_BINDING"
