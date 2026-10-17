from __future__ import annotations

import logging
import re

from pydantic import Field

from embedded_copilot.schemas.result import ContractModel
from embedded_copilot.schemas.state import (
    AgentName,
    AgentState,
    Intent,
    WorkflowStatus,
)


class RoutingDecision(ContractModel):
    intent: Intent
    reason: str = Field(min_length=1)
    design_intent: bool = False


_DEBUG_MARKERS = (
    "guru meditation",
    "hardfault",
    "hard fault",
    "backtrace",
    "panic",
    "serial log",
    "编译错误",
    "报错",
    "崩溃",
    "日志分析",
)
_FIRMWARE_ACTION_MARKERS = (
    "生成",
    "编写",
    "解释",
    "架构",
    "优化",
    "generate",
    "write",
    "create",
    "implement",
    "explain",
    "architecture",
    "optimize",
)
_FIRMWARE_OBJECT_PATTERN = re.compile(
    r"(?:c\+*\+?|code|firmware|esp-idf|stm32\s*hal|freertos|代码|任务)",
    re.IGNORECASE,
)
_FIRMWARE_SOURCE_ACTION_MARKERS = (
    "initialize",
    "initialise",
    "implementation",
    "call graph",
    "which function",
    "why fail",
    "code analysis",
    "architecture analysis",
    "locate",
    "find",
    "symbol",
    "定位",
    "查找",
    "\u521d\u59cb\u5316",
    "\u5728\u54ea\u91cc",
    "\u5982\u4f55\u5b9e\u73b0",
    "\u8c03\u7528\u5173\u7cfb",
    "\u54ea\u4e2a\u51fd\u6570",
    "\u4e3a\u4ec0\u4e48\u5931\u8d25",
    "\u4ee3\u7801\u5206\u6790",
    "\u67b6\u6784\u5206\u6790",
)
_FIRMWARE_SOURCE_OBJECT_PATTERN = re.compile(
    r"(?:c\+*\+?|code|firmware|esp-idf|stm32\s*hal|freertos|"
    r"i2c|spi|uart|gpio|dma|interrupt|hal|task|driver|"
    r"[a-z_][a-z0-9_]*\(|[a-z_]+_[a-z0-9_]+)",
    re.IGNORECASE,
)
_KNOWLEDGE_SOURCE_MARKERS = (
    "datasheet",
    "data sheet",
    "reference manual",
    "sdk",
    "api",
    "\u534f\u8bae",
    "\u6570\u636e\u624b\u518c",
)
_KNOWLEDGE_MARKERS = (
    "esp32",
    "stm32",
    "freertos",
    "uart",
    "spi",
    "i2c",
    "datasheet",
    "数据手册",
    "如何配置",
    "是什么",
)
_DESIGN_INTENT_MARKERS = (
    "设计",
    "规划",
    "需求",
    "架构",
    "方案",
    "design",
    "planning",
    "requirement",
    "requirements",
    "architecture",
    "architectural",
    "specification",
)
_SOURCE_LOOKUP_MARKERS = (
    "在哪里",
    "哪一个文件",
    "哪个文件",
    "哪一处",
    "定义在哪",
    "实现在哪",
    "where is",
    "which file",
    "defined",
    "call graph",
    "调用关系",
    "定位",
    "查找",
    "symbol",
)


def _design_intent(normalized: str) -> bool:
    if any(marker in normalized for marker in _DEBUG_MARKERS):
        return False
    if not any(marker in normalized for marker in _DESIGN_INTENT_MARKERS):
        return False
    if any(marker in normalized for marker in _SOURCE_LOOKUP_MARKERS):
        return False
    if (
        any(marker in normalized for marker in _FIRMWARE_SOURCE_ACTION_MARKERS)
        and _FIRMWARE_SOURCE_OBJECT_PATTERN.search(normalized)
    ):
        return False
    return True


def classify_intent(user_input: str) -> RoutingDecision:
    normalized = user_input.strip().lower()
    if any(marker in normalized for marker in _DEBUG_MARKERS) or "error:" in normalized:
        return RoutingDecision(
            intent=Intent.DEBUG,
            reason="The request contains failure, panic, compiler, or log evidence.",
            design_intent=False,
        )
    firmware_action = any(marker in normalized for marker in _FIRMWARE_ACTION_MARKERS)
    firmware_source_action = any(
        marker in normalized for marker in _FIRMWARE_SOURCE_ACTION_MARKERS
    )
    firmware_source_object = _FIRMWARE_SOURCE_OBJECT_PATTERN.search(normalized)
    knowledge_source = any(marker in normalized for marker in _KNOWLEDGE_SOURCE_MARKERS)
    if (
        (firmware_action and _FIRMWARE_OBJECT_PATTERN.search(normalized))
        or (
            firmware_source_action
            and firmware_source_object
            and not knowledge_source
        )
    ):
        return RoutingDecision(
            intent=Intent.FIRMWARE,
            reason="The request asks for project source, implementation, or call analysis.",
            design_intent=_design_intent(normalized),
        )
    if any(marker in normalized for marker in _KNOWLEDGE_MARKERS):
        return RoutingDecision(
            intent=Intent.KNOWLEDGE,
            reason="The request is an embedded knowledge or documentation question.",
            design_intent=_design_intent(normalized),
        )
    return RoutingDecision(
        intent=Intent.UNKNOWN,
        reason="The request does not contain enough v0.1 routing context.",
        design_intent=_design_intent(normalized),
    )


def supervisor_node(state: AgentState) -> dict[str, object]:
    decision = classify_intent(state["user_input"])
    agent_by_intent = {
        Intent.KNOWLEDGE: AgentName.KNOWLEDGE,
        Intent.FIRMWARE: AgentName.FIRMWARE,
        Intent.DEBUG: AgentName.DEBUG,
    }
    selected = agent_by_intent.get(decision.intent)
    if selected is not None:
        logging.getLogger("embedded_copilot.workflow").info(
            "agent_selected",
            extra={
                "event_name": "agent_selected",
                "trace_id": state["trace_id"],
                "agent_name": selected.value,
            },
        )
    return {
        "intent": decision.intent,
        "selected_agents": [selected] if selected is not None else [],
        "status": (
            WorkflowStatus.RUNNING
            if selected is not None
            else WorkflowStatus.NEEDS_CLARIFICATION
        ),
    }
