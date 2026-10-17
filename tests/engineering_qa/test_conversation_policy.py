from __future__ import annotations

import pytest

from embedded_copilot.engineering_qa.conversation_policy import (
    ConversationMode,
    clean_response_content,
    fallback_response,
    prepare_response_content,
    resolve_conversation_mode,
    should_offer_requirement_report,
)
from embedded_copilot.engineering_requirements import (
    RequirementContext,
    RequirementLifecycle,
)


def _ready_context() -> RequirementContext:
    return RequirementContext(
        project_id="project-1",
        conversation_id="conversation-1",
        lifecycle=RequirementLifecycle.REQUIREMENTS_READY,
    )


@pytest.mark.parametrize(
    ("question", "expected"),
    (
        ("我要做一个 STM32 智能小车", ConversationMode.EXPLORATION),
        ("设计系统架构", ConversationMode.DESIGN),
        ("下一步怎么设计", ConversationMode.DESIGN),
        ("请为当前项目新建工程设计系统需求", ConversationMode.DESIGN),
        ("生成需求分析报告", ConversationMode.DOCUMENT),
        ("当前需求已经确认，生成需求报告", ConversationMode.DOCUMENT),
        ("生成 PID 调试报告", ConversationMode.DOCUMENT),
        ("生成电机控制测试报告", ConversationMode.DOCUMENT),
        ("修改代码并测试", ConversationMode.EXECUTION),
        ("PID调试完成，生成问题处理报告", ConversationMode.DOCUMENT),
        ("请修改代码并生成报告", ConversationMode.DOCUMENT),
        ("设计系统架构并运行测试", ConversationMode.EXECUTION),
    ),
)
def test_mode_resolution_uses_explicit_priority(
    question: str, expected: ConversationMode
) -> None:
    assert resolve_conversation_mode(question) is expected


@pytest.mark.parametrize("question", ("是", "确认生成", "生成报告"))
def test_ready_context_accepts_only_explicit_report_confirmation(question: str) -> None:
    assert (
        resolve_conversation_mode(question, requirement_context=_ready_context())
        is ConversationMode.DOCUMENT
    )


@pytest.mark.parametrize("question", ("好", "好的", "继续", "可以", "下一步"))
def test_ambiguous_acknowledgements_do_not_enter_document(question: str) -> None:
    assert (
        resolve_conversation_mode(question, requirement_context=_ready_context())
        is ConversationMode.EXPLORATION
    )
    assert should_offer_requirement_report(
        question, requirement_context=_ready_context()
    ) is True


def test_response_cleaning_hides_legacy_report_labels_without_erasing_content() -> None:
    content = (
        "# 项目需求分析\n"
        "已确认需求：STM32。\n"
        "目前还需要确认：电池容量。\n"
        "CONFIRMED\n"
        "UNKNOWN\n"
        "Requirement ID: req-1\n"
        "REQUIREMENTS_READY\n"
        "TARGET_PLATFORM: STM32\n"
        "建议先确认电源和驱动能力。"
    )

    cleaned = clean_response_content(content, mode=ConversationMode.EXPLORATION)

    assert "建议先确认电源和驱动能力" in cleaned
    assert "项目需求分析" not in cleaned
    assert "CONFIRMED" not in cleaned
    assert "UNKNOWN" not in cleaned
    assert "Requirement ID" not in cleaned
    assert "REQUIREMENTS_READY" not in cleaned
    assert "TARGET_PLATFORM" not in cleaned
    assert "目前还需要确认" not in cleaned


def test_response_cleaning_removes_context_prefix_without_erasing_following_text() -> None:
    cleaned = clean_response_content(
        "根据当前工程信息\n"
        "当前工程信息：STM32F103。\n"
        "已经确定：使用 STM32CubeMX。\n"
        "后续可以开始硬件设计。",
        mode=ConversationMode.EXPLORATION,
    )

    assert "根据当前工程信息" not in cleaned
    assert "当前工程信息" not in cleaned
    assert "已经确定" not in cleaned
    assert "STM32F103" in cleaned
    assert "STM32CubeMX" in cleaned
    assert "后续可以开始硬件设计" in cleaned


def test_response_cleaning_removes_raw_context_lines_and_json() -> None:
    cleaned = clean_response_content(
        "当前工程上下文:\n"
        "Project: STM32 Smart Car\n"
        '{"project_id":"project-1","lifecycle":"REQUIREMENTS_READY",'
        '"requirements":[]}\n'
        "可以继续确认传感器。",
        mode=ConversationMode.EXPLORATION,
    )

    assert cleaned == "可以继续确认传感器。"


def test_document_mode_keeps_requested_document_heading_but_hides_internal_state() -> None:
    cleaned = clean_response_content(
        "# 项目需求分析报告\n"
        "REQUIREMENTS_READY\n"
        "已确认需求：STM32。\n"
        "项目目标是完成小车控制。",
        mode=ConversationMode.DOCUMENT,
    )

    assert "# 项目需求分析报告" in cleaned
    assert "REQUIREMENTS_READY" not in cleaned
    assert "已确认需求" not in cleaned
    assert "项目目标是完成小车控制" in cleaned


def test_response_cleaning_has_a_natural_fallback_when_only_private_state_remains() -> None:
    assert clean_response_content(
        "UNKNOWN\nCONFIRMED\nREQUIREMENTS_READY",
        mode=ConversationMode.EXPLORATION,
    ) == ""
    assert fallback_response(ConversationMode.EXPLORATION).startswith("明白了。")


def test_ready_context_strategy_always_asks_before_generating_report() -> None:
    prepared = prepare_response_content(
        "# 项目需求分析\n已确认需求：STM32。",
        question="继续",
        requirement_context=_ready_context(),
    )

    assert prepared == "当前项目需求已经整理完成，是否生成需求分析报告？"
