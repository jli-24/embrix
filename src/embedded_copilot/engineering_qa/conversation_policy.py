from __future__ import annotations

import re
import unicodedata
from enum import StrEnum

from embedded_copilot.engineering_requirements import (
    RequirementContext,
    RequirementLifecycle,
)


class ConversationMode(StrEnum):
    """Internal response mode; it is not part of a public contract."""

    EXPLORATION = "EXPLORATION"
    DESIGN = "DESIGN"
    DOCUMENT = "DOCUMENT"
    EXECUTION = "EXECUTION"


_DOCUMENT_PHRASES = (
    "生成需求分析报告",
    "生成需求报告",
    "输出需求文档",
    "生成需求文档",
    "制作项目说明书",
    "制作项目规格说明书",
    "生成架构文档",
    "生成架构报告",
    "生成技术报告",
    "生成测试报告",
    "生成调试报告",
    "生成问题处理报告",
    "生成变更报告",
    "生成报告",
    "输出报告",
    "制作报告",
    "写报告",
    "生成文档",
    "输出文档",
    "generate a requirements report",
    "generate a requirements document",
    "generate a report",
    "write a report",
    "generate an architecture document",
    "generate a test report",
    "generate a debug report",
)

_EXECUTION_PHRASES = (
    "修改代码",
    "改代码",
    "编译",
    "构建",
    "运行测试",
    "执行测试",
    "调试错误",
    "调试",
    "执行工程任务",
    "执行任务",
    "modify code",
    "change code",
    "compile",
    "build",
    "run tests",
    "debug",
    "execute the task",
)

_DESIGN_PHRASES = (
    "设计方案",
    "架构设计",
    "技术路线",
    "硬件方案",
    "软件方案",
    "系统架构",
    "控制方案",
    "怎么设计",
    "如何设计",
    "工程设计",
    "新建工程设计",
    "design solution",
    "system architecture",
    "technical approach",
    "hardware design",
    "software design",
)

_QUALIFIED_DOCUMENT_TYPES = (
    "需求分析报告",
    "需求报告",
    "架构报告",
    "架构文档",
    "调试报告",
    "测试报告",
    "变更报告",
)

_DOCUMENT_ACTIONS = ("生成", "输出", "制作", "写")

_EXPLICIT_CONFIRMATIONS = frozenset(
    {
        "是",
        "确认",
        "确认生成",
        "确认生成报告",
        "开始生成",
        "开始生成报告",
        "生成吧",
        "请生成报告",
    }
)

_PRIVATE_STATUS_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:CONFIRMED|UNKNOWN|PROPOSED|OPEN_QUESTION|Requirement\s+ID|"
    r"DISCOVERY_STARTED|REQUIREMENTS_PARTIAL|REQUIREMENTS_REFINING|"
    r"REQUIREMENTS_READY|DESIGN_READY|TARGET_PLATFORM|PRODUCT_GOAL|"
    r"HARDWARE_CONSTRAINT|SOFTWARE_CONSTRAINT)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_PRIVATE_CHINESE_PATTERN = re.compile(
    r"(?:已确认需求|已确认|待确认|未知需求|需求状态|需求编号|需求分类)"
)
_PRIVATE_LINE_PATTERN = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:CONFIRMED|UNKNOWN|PROPOSED|OPEN_QUESTION|"
    r"Requirement\s+ID|DISCOVERY_STARTED|REQUIREMENTS_PARTIAL|"
    r"REQUIREMENTS_REFINING|REQUIREMENTS_READY|DESIGN_READY|TARGET_PLATFORM|"
    r"PRODUCT_GOAL|HARDWARE_CONSTRAINT|SOFTWARE_CONSTRAINT|已确认需求|待确认|"
    r"未知需求|需求状态|需求编号|需求分类)"
    r"\s*(?::|：|$)",
    re.IGNORECASE,
)
_REPORT_HEADING_PATTERN = re.compile(
    r"^\s*#{0,6}\s*(?:项目需求分析|项目需求分析报告|已确认需求|未确认需求|需求推断)\s*:?[ \t]*$"
)
_RAW_CONTEXT_LINE_PATTERN = re.compile(
    r"^\s*(?:Project(?:\s+(?:mode|type))?|Current\s+project\s+context|"
    r"工程上下文|当前工程上下文|RequirementContext(?:\s+part)?|"
    r"Memory\s+Evidence|Graph\s+Evidence|Knowledge\s+Evidence|"
    r"Multimodal\s+Evidence|Conflicts|Provenance)\s*:",
    re.IGNORECASE,
)
_RAW_CONTEXT_KEY_PATTERN = re.compile(
    r"(?:\"(?:project_id|conversation_id|standard_fields|requirements|constraints|"
    r"proposed_items|open_questions|lifecycle|project_creation_mode|"
    r"target_platform|product_goal)\"\s*:|"
    r"RequirementContext\s+part\s+\d+/\d+)",
    re.IGNORECASE,
)


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).strip().casefold()
    return re.sub(r"\s+", " ", normalized)


def _without_terminal_punctuation(value: str) -> str:
    return value.strip().rstrip("。！？!?，,；;、 \t")


def _context_is_ready(requirement_context: RequirementContext | None) -> bool:
    return requirement_context is not None and requirement_context.lifecycle in {
        RequirementLifecycle.REQUIREMENTS_READY,
        RequirementLifecycle.DESIGN_READY,
    }


def _has_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase.casefold() in text for phrase in phrases)


def _is_document_request(text: str) -> bool:
    if _has_phrase(text, _DOCUMENT_PHRASES):
        return True
    return any(
        action in text and document_type in text
        for action in _DOCUMENT_ACTIONS
        for document_type in _QUALIFIED_DOCUMENT_TYPES
    )


def _is_explicit_confirmation(text: str) -> bool:
    return _without_terminal_punctuation(text) in _EXPLICIT_CONFIRMATIONS


def resolve_conversation_mode(
    question: str,
    *,
    requirement_context: RequirementContext | None = None,
) -> ConversationMode:
    """Resolve the private conversation policy without changing application routes."""

    if not isinstance(question, str):
        raise TypeError("question is invalid")
    normalized = _normalize(question)
    if _is_document_request(normalized):
        return ConversationMode.DOCUMENT
    if _context_is_ready(requirement_context) and _is_explicit_confirmation(normalized):
        return ConversationMode.DOCUMENT
    if _has_phrase(normalized, _EXECUTION_PHRASES):
        return ConversationMode.EXECUTION
    if _has_phrase(normalized, _DESIGN_PHRASES):
        return ConversationMode.DESIGN
    return ConversationMode.EXPLORATION


def should_offer_requirement_report(
    question: str,
    *,
    requirement_context: RequirementContext | None = None,
) -> bool:
    """Return whether a ready context needs an explicit report confirmation."""

    return _context_is_ready(requirement_context) and resolve_conversation_mode(
        question, requirement_context=requirement_context
    ) is ConversationMode.EXPLORATION


def prepare_response_content(
    content: str,
    *,
    question: str,
    requirement_context: RequirementContext | None = None,
) -> str:
    """Apply response strategy before the final text-cleaning safety boundary."""

    if not isinstance(content, str):
        raise TypeError("response content is invalid")
    if should_offer_requirement_report(
        question, requirement_context=requirement_context
    ):
        return "当前项目需求已经整理完成，是否生成需求分析报告？"
    return content


def clean_response_content(content: str, *, mode: ConversationMode) -> str:
    """Remove accidental internal-state and raw-context leakage from user text."""

    if not isinstance(content, str):
        raise TypeError("response content is invalid")
    if not isinstance(mode, ConversationMode):
        raise TypeError("conversation mode is invalid")

    lines: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if _PRIVATE_LINE_PATTERN.match(line):
            continue
        if mode is not ConversationMode.DOCUMENT and _REPORT_HEADING_PATTERN.match(line):
            continue
        if _RAW_CONTEXT_LINE_PATTERN.match(line) or _RAW_CONTEXT_KEY_PATTERN.search(line):
            continue
        lines.append(line)

    cleaned = "\n".join(lines)
    cleaned = re.sub(
        r"(?m)^\s*(?:根据当前工程信息|当前工程信息|已经确定)\s*[:：]?\s*",
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"(?m)^\s*目前还需要确认\s*[:：]?\s*",
        "接下来需要确认：",
        cleaned,
    )
    cleaned = re.sub(
        r"(?m)^\s*尚未确认信息\s*[:：]?\s*",
        "接下来需要补充：",
        cleaned,
    )
    cleaned = re.sub(
        r"(?m)^\s*当前需求理解\s*[:：]?\s*",
        "",
        cleaned,
    )
    cleaned = _PRIVATE_STATUS_PATTERN.sub("", cleaned)
    cleaned = _PRIVATE_CHINESE_PATTERN.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip(" \n\r\t:：")


def fallback_response(mode: ConversationMode) -> str:
    """Return a short safe response if cleaning removes the entire answer."""

    return {
        ConversationMode.EXPLORATION: (
            "明白了。为了确定设计方向，我需要确认目标功能、开发平台以及通信或自主功能需求。"
        ),
        ConversationMode.DESIGN: (
            "我可以继续整理系统架构、硬件、软件和验证方案，请补充当前缺少的关键约束。"
        ),
        ConversationMode.DOCUMENT: "请明确需要生成的工程文档类型和范围。",
        ConversationMode.EXECUTION: (
            "我会先确认目标和影响范围，再通过已有受控工程链路执行，并返回真实结果。"
        ),
    }[mode]


__all__ = (
    "ConversationMode",
    "clean_response_content",
    "fallback_response",
    "prepare_response_content",
    "resolve_conversation_mode",
    "should_offer_requirement_report",
)
