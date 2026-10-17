from __future__ import annotations

import copy
import re
import unicodedata

from .exceptions import CapabilityPolicyRejected
from .models import (
    AgentCapabilityRequest,
    CapabilityPolicyDecision,
    CapabilityPolicyGrant,
    CapabilityRouteDecision,
    CapabilityType,
)

_PRIORITY = (
    CapabilityType.DEBUG_ANALYSIS,
    CapabilityType.REPAIR_ANALYSIS,
    CapabilityType.OPTIMIZATION_ANALYSIS,
    CapabilityType.EDA_ANALYSIS,
    CapabilityType.PCB_ANALYSIS,
    CapabilityType.HARDWARE_HIL_TEST,
    CapabilityType.HARDWARE_TEST,
    CapabilityType.VISION,
    CapabilityType.FIRMWARE_ANALYSIS,
    CapabilityType.KNOWLEDGE,
)

_MARKERS = {
    CapabilityType.DEBUG_ANALYSIS: (
        "error:",
        "panic",
        "hardfault",
        "hard fault",
        "backtrace",
        "crash",
        "debug log",
        "编译错误",
        "链接错误",
        "崩溃",
        "调试日志",
    ),
    CapabilityType.REPAIR_ANALYSIS: (
        "repair",
        "fix failure",
        "remediate",
        "修复",
        "故障修复",
        "修复建议",
    ),
    CapabilityType.OPTIMIZATION_ANALYSIS: (
        "optimize",
        "optimization",
        "trade-off",
        "优化",
        "性能优化",
        "功耗优化",
        "成本优化",
    ),
    CapabilityType.EDA_ANALYSIS: (
        "erc",
        "drc",
        "eda review",
        "kicad project",
        "设计规则",
        "原理图检查",
    ),
    CapabilityType.PCB_ANALYSIS: (
        "pcb",
        "kicad",
        "netlist",
        "bom",
        "board layout",
        "schematic connectivity",
        "电路板",
        "网表",
        "物料清单",
    ),
    CapabilityType.HARDWARE_HIL_TEST: (
        "hardware-in-the-loop",
        "hil test",
        "fixture test",
        "power measurement",
        "timing metric",
        "硬件在环",
        "夹具测试",
        "功耗测量",
        "时序测量",
    ),
    CapabilityType.HARDWARE_TEST: (
        "hardware test",
        "uart observation",
        "boot log",
        "board health",
        "硬件测试",
        "串口观察",
        "启动日志",
    ),
    CapabilityType.VISION: (
        "image",
        "screenshot",
        "photo",
        "waveform",
        "pdf page",
        "图像",
        "截图",
        "照片",
        "波形",
        "页面",
    ),
    CapabilityType.FIRMWARE_ANALYSIS: (
        "firmware",
        "c code",
        "esp-idf",
        "stm32 hal",
        "freertos",
        "driver",
        "固件",
        "代码",
        "驱动",
    ),
    CapabilityType.KNOWLEDGE: (
        "datasheet",
        "manual",
        "what is",
        "how does",
        "explain",
        "数据手册",
        "手册",
        "是什么",
        "原理",
        "解释",
    ),
}


def _normalized_question(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).strip().casefold()
    return re.sub(r"\s+", " ", normalized)


def classify_request(request: AgentCapabilityRequest) -> CapabilityRouteDecision:
    checked = AgentCapabilityRequest.model_validate(
        copy.deepcopy(request.model_dump(mode="python"))
    )
    question = _normalized_question(checked.application_request.question)
    matches: dict[CapabilityType, tuple[str, ...]] = {}
    for capability in _PRIORITY:
        found = tuple(
            f"{capability.value.casefold()}-{index}"
            for index, marker in enumerate(_MARKERS[capability], start=1)
            if marker in question
        )
        if found:
            matches[capability] = found
    candidates = tuple(item for item in _PRIORITY if item in matches)
    selected = candidates[0] if candidates else None
    rule_ids = tuple(rule for item in candidates for rule in matches[item])
    return CapabilityRouteDecision(
        request_id=checked.application_request.request_id,
        request_fingerprint=checked.fingerprint,
        candidates=candidates,
        selected_capability=selected,
        matched_rule_ids=rule_ids,
        reason_code="priority_match" if selected is not None else "route_not_found",
    )


class StaticCapabilityPolicy:
    __slots__ = ("_grants",)

    def __init__(self, grants: tuple[CapabilityPolicyGrant, ...]) -> None:
        if type(grants) is not tuple:
            raise TypeError("capability grants must be a tuple")
        checked = tuple(
            CapabilityPolicyGrant.model_validate(
                copy.deepcopy(item.model_dump(mode="python"))
            )
            for item in grants
        )
        identities = tuple((item.project_id, item.requester) for item in checked)
        if len(identities) != len(set(identities)):
            raise CapabilityPolicyRejected("duplicate capability policy grant")
        self._grants = {
            identity: item for identity, item in zip(identities, checked, strict=True)
        }

    def evaluate(
        self, request: AgentCapabilityRequest, route: CapabilityRouteDecision
    ) -> CapabilityPolicyDecision:
        checked_request = AgentCapabilityRequest.model_validate(
            copy.deepcopy(request.model_dump(mode="python"))
        )
        checked_route = CapabilityRouteDecision.model_validate(
            copy.deepcopy(route.model_dump(mode="python"))
        )
        if (
            checked_route.request_id != checked_request.application_request.request_id
            or checked_route.request_fingerprint != checked_request.fingerprint
        ):
            raise CapabilityPolicyRejected("policy route binding mismatch")
        grant = self._grants.get(
            (
                checked_request.application_request.project_id,
                checked_request.application_request.requester,
            )
        )
        capability = checked_route.selected_capability
        allowed = (
            grant is not None
            and capability is not None
            and capability in grant.capabilities
        )
        return CapabilityPolicyDecision(
            request_fingerprint=checked_request.fingerprint,
            route_fingerprint=checked_route.fingerprint,
            capability=capability,
            allowed=allowed,
            grant_fingerprint=grant.fingerprint if grant is not None else None,
            reason_code="capability_allowed" if allowed else "capability_denied",
        )


__all__ = ("StaticCapabilityPolicy", "classify_request")
