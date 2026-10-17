from __future__ import annotations

import pytest

from embedded_copilot.agents.supervisor import classify_intent, supervisor_node
from embedded_copilot.agents.workflow import create_initial_state
from embedded_copilot.schemas.state import AgentName, Intent, WorkflowStatus


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("ESP32如何配置SPI？", Intent.KNOWLEDGE),
        ("生成ESP32 FreeRTOS LED任务", Intent.FIRMWARE),
        ("分析这个Guru Meditation Error\nBacktrace: 0x40081234", Intent.DEBUG),
        ("请解释这段 C 代码：int value = 1;", Intent.FIRMWARE),
        ("FreeRTOS task 是什么？", Intent.KNOWLEDGE),
        ("How does a FreeRTOS task work?", Intent.KNOWLEDGE),
        ("Generate an ESP32 FreeRTOS task", Intent.FIRMWARE),
    ],
)
def test_classifier_routes_v010_requests(message: str, expected: Intent) -> None:
    decision = classify_intent(message)

    assert decision.intent is expected
    assert decision.reason


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("I2C在哪里初始化？", Intent.FIRMWARE),
        ("STM32 I2C HAL API是什么？", Intent.KNOWLEDGE),
        ("sensor_read为什么失败？", Intent.FIRMWARE),
        ("HardFault崩溃原因", Intent.DEBUG),
    ],
)
def test_classifier_distinguishes_firmware_source_semantics(
    message: str, expected: Intent
) -> None:
    assert classify_intent(message).intent is expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "请为当前 qdwq 的 STM32 新建工程设计系统需求与启动架构，不依赖现有源码。",
            True,
        ),
        ("sensor_read 在哪里实现？", False),
        ("请定位不存在的 sensor_read symbol", False),
    ],
)
def test_classifier_marks_only_design_requests_as_greenfield_eligible(
    message: str, expected: bool
) -> None:
    assert classify_intent(message).design_intent is expected


def test_unknown_intent_does_not_select_an_agent() -> None:
    state = create_initial_state("你好", trace_id="trace-1")

    update = supervisor_node(state)

    assert update["intent"] is Intent.UNKNOWN
    assert update["selected_agents"] == []
    assert update["status"] is WorkflowStatus.NEEDS_CLARIFICATION


def test_supervisor_selects_exactly_one_specialist() -> None:
    state = create_initial_state("ESP32如何配置SPI？", trace_id="trace-1")

    update = supervisor_node(state)

    assert update["selected_agents"] == [AgentName.KNOWLEDGE]
    assert update["status"] is WorkflowStatus.RUNNING
