from __future__ import annotations

import hashlib
import re
from typing import Iterable

from embedded_copilot.engineering_copilot import (
    EngineeringPlan,
    EngineeringPlanPhase,
    EngineeringPlanTask,
    EngineeringTask,
    PlanEvidenceBinding,
    PlanOutput,
    PlanPhaseStatus,
    PlanProvenance,
    PlanRisk,
    PlanRiskLevel,
    PlanStatus,
    PlanTaskStatus,
    PlanTaskType,
    PlanVerificationStrategy,
    transition_engineering_plan,
)
from embedded_copilot.engineering_copilot.contracts import PlanModelOperationBinding
from embedded_copilot.model_gateway.governance import EngineeringStage

from .models import (
    AcceptanceCriterion,
    OpenQuestion,
    RequirementConflict,
    RequirementConflictSeverity,
    RequirementConflictStatus,
    RequirementContext,
    RequirementItem,
    RequirementItemStatus,
    RequirementLifecycle,
    RequirementPriority,
    RequirementProvenance,
    RequirementStatus,
    StandardRequirementField,
    _identifier,
)

_ACCURACY = re.compile(
    r"(?:精度|准确度)\s*(?:为|要求|达到)?\s*(±\s*[0-9]+(?:\.[0-9]+)?\s*°?\s*[CF])",
    re.IGNORECASE,
)
_GOAL = re.compile(r"(?:做个|做一个|开发一个|实现一个|设计一个)\s*([^，。,.!?！？\n]{1,48})")
_NUMBERED_VALUE = re.compile(
    r"(?P<label>续航|响应时间|成本|预算|尺寸|重量)\s*(?:要求|不超过|小于|为|是)?\s*(?P<value>[0-9]+(?:\.[0-9]+)?\s*[A-Za-z%°℃元小时分钟米厘米毫米kg千克]*)",
    re.IGNORECASE,
)
_STRUCTURED_ACCEPTANCE = re.compile(
    r"Given[:：]\s*(?P<given>.+?)\s+When[:：]\s*(?P<when>.+?)\s+Then[:：]\s*(?P<then>.+?)\s+Verification[:：]\s*(?P<verification>.+)$",
    re.IGNORECASE,
)
_NAMED_VALUE = re.compile(
    r"(?P<label>输入|输出|硬件范围|软件范围|交付物)\s*(?:为|包括|包含|是)?\s*(?P<value>[^，。；;]+)",
)

_CONSTRAINT_CATEGORIES = {
    "BATTERY_LIFE",
    "BUDGET",
    "COMMUNICATION_DISTANCE",
    "FORM_FACTOR",
    "POWER_CONSUMPTION",
    "RESPONSE_TIME",
    "WEIGHT",
}
_MULTI_VALUE_CATEGORIES = {"FUNCTIONAL"}
_FUNCTION_VALUES = {
    "巡线": "LINE_FOLLOWING",
    "循迹": "LINE_FOLLOWING",
    "避障": "OBSTACLE_AVOIDANCE",
    "遥控": "REMOTE_CONTROL",
    "自动导航": "AUTONOMOUS_NAVIGATION",
    "测温": "TEMPERATURE_SENSING",
    "温度控制": "TEMPERATURE_CONTROL",
    "显示": "STATUS_DISPLAY",
}
_QUESTION_CATALOG: tuple[tuple[str, str], ...] = (
    ("PRODUCT_GOAL", "这个项目主要要实现什么产品目标？"),
    ("TARGET_PLATFORM", "目标 MCU 或硬件平台是什么？"),
    ("FUNCTIONAL_SCOPE", "首个版本必须支持哪些核心功能？"),
    ("POWER_SOURCE", "设备采用什么供电方式，是否有续航要求？"),
    ("MOTOR", "如果包含运动机构，请确认电机类型和驱动方式。"),
    ("COMMUNICATION", "是否需要通信或远程控制？请确认协议和范围。"),
)
_PLAN_APPROVAL_PHRASES = frozenset(
    {"开始", "批准", "批准计划", "同意该计划", "同意计划", "approve plan", "approve"}
)


def _id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _priority(utterance: str) -> RequirementPriority:
    lowered = utterance.casefold()
    if any(marker in lowered for marker in ("must", "必须", "强制", "一定要")):
        return RequirementPriority.MUST
    if any(marker in lowered for marker in ("could", "可以有", "最好有")):
        return RequirementPriority.COULD
    if any(marker in lowered for marker in ("won't", "wont", "暂不", "不需要", "不做")):
        return RequirementPriority.WONT
    return RequirementPriority.SHOULD


def _item(
    *,
    project_id: str,
    conversation_id: str,
    turn_id: str,
    category: str,
    value: str,
    provenance: RequirementProvenance,
    status: RequirementItemStatus,
    priority: RequirementPriority = RequirementPriority.SHOULD,
    supersedes: str | None = None,
) -> RequirementItem:
    return RequirementItem(
        requirement_id=_id("requirement", project_id, conversation_id, category, value, turn_id),
        project_id=project_id,
        conversation_id=conversation_id,
        category=category,
        normalized_value=value.strip(),
        status=status,
        priority=priority,
        source_turn_id=turn_id,
        provenance=provenance,
        supersedes=supersedes,
    )


def _copy_item(item: RequirementItem, **updates: object) -> RequirementItem:
    material = item.model_dump(mode="python")
    material.update(updates)
    material["fingerprint"] = "sha256:" + "0" * 64
    return RequirementItem.model_validate(material, strict=False)


def _copy_context(context: RequirementContext, **updates: object) -> RequirementContext:
    material = context.model_dump(mode="python")
    material.update(updates)
    material["fingerprint"] = "sha256:" + "0" * 64
    return RequirementContext.model_validate(material, strict=False)


def _explicit_items(
    project_id: str, conversation_id: str, turn_id: str, utterance: str
) -> tuple[RequirementItem, ...]:
    lowered = utterance.casefold()
    values: list[tuple[str, str]] = []
    if "stm32" in lowered:
        values.append(("TARGET_PLATFORM", "STM32"))
    elif "esp32" in lowered:
        values.append(("TARGET_PLATFORM", "ESP32"))
    if "freertos" in lowered or "free rtos" in lowered:
        values.append(("RTOS", "FreeRTOS"))
    elif "zephyr" in lowered:
        values.append(("RTOS", "Zephyr"))
    if "stm32" in lowered and any(marker in utterance for marker in ("小车", "车")):
        values.append(("HARDWARE_SCOPE", "MOBILE_ROBOT"))
    if "只控制加热" in utterance or "仅控制加热" in utterance:
        values.append(("CONTROL_MODE", "HEATING"))
    if "低功耗" in utterance or "低功率" in utterance:
        values.append(("POWER_CONSUMPTION", "LOW_POWER"))
    if "电池" in utterance or "锂电" in utterance:
        values.append(("POWER_SOURCE", "BATTERY"))
    negative = any(marker in utterance for marker in ("不要", "不需要", "无需", "不做", "去掉", "删掉"))
    if not negative and any(marker in lowered for marker in ("imu", "陀螺仪", "加速度计")):
        values.append(("SENSOR", "IMU"))
    if not negative and any(marker in lowered for marker in ("屏幕", "显示屏", "display", "screen")):
        values.append(("DISPLAY", "DISPLAY"))
    if "gps" in lowered and not negative:
        values.append(("NAVIGATION", "GPS"))
    if "rtc" in lowered and not negative:
        values.append(("TIMEKEEPING", "RTC"))
    if "振动" in utterance or "vibration" in lowered:
        if not negative:
            values.append(("FEEDBACK", "VIBRATION"))
    if "usb" in lowered:
        values.append(("CONNECTIVITY", "USB_CHARGING_ONLY" if "不需要数据" in utterance or "只需要充电" in utterance or "仅需要充电" in utterance else "USB"))
    if "直流电机" in utterance or "dc motor" in lowered:
        values.append(("MOTOR", "DC_MOTOR"))
    elif "步进电机" in utterance:
        values.append(("MOTOR", "STEPPER_MOTOR"))
    elif "舵机" in utterance:
        values.append(("MOTOR", "SERVO"))
    elif "电机" in utterance:
        values.append(("MOTOR", "MOTOR_UNSPECIFIED"))
    if "蓝牙" in utterance or "ble" in lowered:
        values.append(("COMMUNICATION", "BLUETOOTH"))
    if "wifi" in lowered or "wi-fi" in lowered:
        values.append(("COMMUNICATION", "WIFI"))
    if any(marker in utterance for marker in ("手机看", "手机查看", "手机控制", "远程监控")):
        values.append(("REMOTE_MONITORING", "REQUIRED"))
    for marker, normalized in _FUNCTION_VALUES.items():
        if marker in utterance:
            values.append(("FUNCTIONAL", normalized))
    for match in _NAMED_VALUE.finditer(utterance):
        category = {
            "输入": "INPUT",
            "输出": "OUTPUT",
            "硬件范围": "HARDWARE_SCOPE",
            "软件范围": "SOFTWARE_SCOPE",
            "交付物": "DELIVERABLE",
        }[match.group("label")]
        values.append((category, match.group("value").strip()))
    if "需要原理图" in utterance:
        values.append(("DELIVERABLE", "SCHEMATIC"))
    if "需要 PCB" in utterance or "需要PCB" in utterance:
        values.append(("DELIVERABLE", "PCB"))
    if "需要固件" in utterance or "需要 firmware" in lowered:
        values.append(("DELIVERABLE", "FIRMWARE"))
    accuracy = _ACCURACY.search(utterance)
    if accuracy:
        values.append(("ACCURACY", accuracy.group(1).replace(" ", "")))
    for match in _NUMBERED_VALUE.finditer(utterance):
        category = {
            "续航": "BATTERY_LIFE",
            "响应时间": "RESPONSE_TIME",
            "成本": "BUDGET",
            "预算": "BUDGET",
            "尺寸": "FORM_FACTOR",
            "重量": "WEIGHT",
        }[match.group("label")]
        values.append((category, match.group("value").replace(" ", "")))
    goal = _GOAL.search(utterance)
    if goal:
        values.append(("PRODUCT_GOAL", goal.group(1).strip()))
    priority = _priority(utterance)
    return tuple(
        _item(
            project_id=project_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            category=category,
            value=value,
            provenance=RequirementProvenance.USER_UTTERANCE,
            status=RequirementItemStatus.CONFIRMED,
            priority=priority,
        )
        for category, value in values
    )


def _replace_confirmed(
    existing: tuple[RequirementItem, ...], additions: tuple[RequirementItem, ...]
) -> tuple[RequirementItem, ...]:
    result = list(existing)
    for addition in additions:
        current = next(
            (
                item
                for item in result
                if item.category == addition.category
                and item.status is RequirementItemStatus.CONFIRMED
                and addition.category not in _MULTI_VALUE_CATEGORIES
            ),
            None,
        )
        if current is not None and current.normalized_value == addition.normalized_value:
            continue
        if current is not None:
            replacement_id = addition.requirement_id
            addition = _copy_item(
                addition,
                supersedes=current.requirement_id,
            )
            result = [
                item
                if item is not current
                else _copy_item(
                    item,
                    status=RequirementItemStatus.SUPERSEDED,
                    superseded_by=replacement_id,
                )
                for item in result
            ]
        result.append(addition)
    return tuple(result)


def _active(items: Iterable[RequirementItem]) -> tuple[RequirementItem, ...]:
    return tuple(item for item in items if item.status is RequirementItemStatus.CONFIRMED)


def _question_values(
    *,
    project_id: str,
    conversation_id: str,
    turn_id: str,
    confirmed: tuple[RequirementItem, ...],
    existing: tuple[OpenQuestion, ...],
    provider_questions: tuple[dict[str, str], ...],
) -> tuple[OpenQuestion, ...]:
    categories = {item.category for item in confirmed}
    resolved_categories = set(categories)
    if "FUNCTIONAL" in categories:
        resolved_categories.add("FUNCTIONAL_SCOPE")
    if "REMOTE_MONITORING" in categories:
        resolved_categories.add("COMMUNICATION")
    questions = [question for question in existing if question.category not in resolved_categories]
    missing = {question.category for question in questions}
    if "PRODUCT_GOAL" not in categories:
        missing.add("PRODUCT_GOAL")
    if ("PRODUCT_GOAL" in categories or "FUNCTIONAL" in categories) and "TARGET_PLATFORM" not in categories:
        missing.add("TARGET_PLATFORM")
    if "PRODUCT_GOAL" in categories and "FUNCTIONAL" not in categories:
        missing.add("FUNCTIONAL_SCOPE")
    if "MOBILE_ROBOT" in {item.normalized_value for item in confirmed if item.category == "HARDWARE_SCOPE"}:
        missing.update(category for category in ("POWER_SOURCE", "MOTOR") if category not in categories)
    if "REMOTE_MONITORING" in categories or "COMMUNICATION" in categories:
        missing.discard("COMMUNICATION")
    for category, question in _QUESTION_CATALOG:
        if category in missing and not any(item.category == category for item in questions):
            questions.append(
                OpenQuestion(
                    question_id=_id("question", project_id, conversation_id, category),
                    category=category,
                    question=question,
                    source_turn_id=turn_id,
                    provenance=RequirementProvenance.SYSTEM,
                )
            )
    for provider_question in provider_questions:
        category = provider_question.get("category", "OTHER")
        value = provider_question.get("question", "")
        if value and not any(item.category == category and item.question == value for item in questions):
            questions.append(
                OpenQuestion(
                    question_id=_id("question", project_id, conversation_id, category, value),
                    category=category,
                    question=value,
                    source_turn_id=turn_id,
                    provenance=RequirementProvenance.PROVIDER_RESPONSE,
                )
            )
    return tuple(questions[:8])


def _acceptance_criteria(
    *,
    project_id: str,
    conversation_id: str,
    turn_id: str,
    confirmed: tuple[RequirementItem, ...],
    existing: tuple[AcceptanceCriterion, ...],
) -> tuple[AcceptanceCriterion, ...]:
    criteria = list(existing)
    for item in confirmed:
        if item.category != "FUNCTIONAL":
            continue
        if any(criterion.requirement_id == item.requirement_id for criterion in criteria):
            continue
        labels = {
            "LINE_FOLLOWING": ("存在清晰轨迹", "小车启动并沿轨迹运行", "小车持续沿轨迹行驶", "轨迹测试，记录偏离和丢线次数"),
            "OBSTACLE_AVOIDANCE": ("前方存在障碍物", "小车接近障碍物", "小车停止或绕行且不碰撞", "障碍物场景测试，记录碰撞结果"),
            "REMOTE_CONTROL": ("控制端和设备已连接", "用户发送控制指令", "设备按指令执行动作", "通信联调，逐条核对指令与响应"),
            "TEMPERATURE_CONTROL": ("温度传感器可用", "温度偏离设定值", "控制器按要求调节温度", "温度阶跃测试，记录稳态误差"),
        }.get(item.normalized_value)
        if labels is None:
            labels = ("系统处于可测试状态", "触发该功能", f"系统完成 {item.normalized_value}", "功能测试并保留测试记录")
        criteria.append(
            AcceptanceCriterion(
                criterion_id=_id("criterion", project_id, conversation_id, item.requirement_id),
                project_id=project_id,
                conversation_id=conversation_id,
                requirement_id=item.requirement_id,
                given=labels[0],
                when=labels[1],
                then=labels[2],
                verification=labels[3],
                status=RequirementItemStatus.PROPOSED,
                source_turn_id=turn_id,
                provenance=RequirementProvenance.SYSTEM,
            )
        )
    return tuple(criteria)


def _explicit_acceptance(
    *,
    project_id: str,
    conversation_id: str,
    turn_id: str,
    utterance: str,
) -> tuple[AcceptanceCriterion, ...]:
    match = _STRUCTURED_ACCEPTANCE.search(utterance)
    if match is None:
        return ()
    return (
        AcceptanceCriterion(
            criterion_id=_id("criterion", project_id, conversation_id, turn_id),
            project_id=project_id,
            conversation_id=conversation_id,
            given=match.group("given").strip(),
            when=match.group("when").strip(),
            then=match.group("then").strip(),
            verification=match.group("verification").strip(),
            status=RequirementItemStatus.CONFIRMED,
            source_turn_id=turn_id,
            provenance=RequirementProvenance.USER_UTTERANCE,
        ),
    )


def _conflicts(
    *,
    project_id: str,
    conversation_id: str,
    turn_id: str,
    confirmed: tuple[RequirementItem, ...],
    existing: tuple[RequirementConflict, ...],
) -> tuple[RequirementConflict, ...]:
    conflicts = list(existing)
    by_category = {item.category: item for item in confirmed}
    if "POWER_CONSUMPTION" in by_category and "BATTERY_LIFE" in by_category:
        ids = (by_category["POWER_CONSUMPTION"].requirement_id, by_category["BATTERY_LIFE"].requirement_id)
        conflict_id = _id("conflict", project_id, conversation_id, *ids)
        if not any(item.conflict_id == conflict_id for item in conflicts):
            conflicts.append(
                RequirementConflict(
                    conflict_id=conflict_id,
                    project_id=project_id,
                    conversation_id=conversation_id,
                    requirement_ids=ids,
                    description="低功耗与当前续航指标的实现取舍需要确认功耗预算和测试条件。",
                    severity=RequirementConflictSeverity.NON_BLOCKING,
                    status=RequirementConflictStatus.OPEN,
                    source_turn_id=turn_id,
                    provenance=RequirementProvenance.SYSTEM,
                )
            )
    if "BUDGET" in by_category and "FORM_FACTOR" in by_category:
        ids = (by_category["BUDGET"].requirement_id, by_category["FORM_FACTOR"].requirement_id)
        conflict_id = _id("conflict", project_id, conversation_id, *ids)
        if not any(item.conflict_id == conflict_id for item in conflicts):
            conflicts.append(
                RequirementConflict(
                    conflict_id=conflict_id,
                    project_id=project_id,
                    conversation_id=conversation_id,
                    requirement_ids=ids,
                    description="成本与尺寸目标可能存在资源取舍，需要明确优先级。",
                    severity=RequirementConflictSeverity.NON_BLOCKING,
                    status=RequirementConflictStatus.OPEN,
                    source_turn_id=turn_id,
                    provenance=RequirementProvenance.SYSTEM,
                )
            )
    return tuple(conflicts)


def _explicit_confirmation(utterance: str) -> bool:
    normalized = " ".join(utterance.casefold().split())
    return any(
        marker in normalized
        for marker in (
            "开始设计",
            "进入架构设计",
            "需求已确认",
            "需求明确",
            "可以开始设计",
            "requirements are ready",
            "确认需求",
        )
    )


def is_plan_approval_request(utterance: str) -> bool:
    return " ".join(utterance.casefold().split()) in _PLAN_APPROVAL_PHRASES


def _complete_for_confirmation(context: RequirementContext) -> bool:
    confirmed = _active((*context.requirements, *context.constraints))
    categories = {item.category for item in confirmed}
    required = {"PRODUCT_GOAL", "TARGET_PLATFORM", "FUNCTIONAL"}
    if not required.issubset(categories) or context.open_questions or context.conflicts:
        return False
    return bool(context.acceptance_criteria)


def _product_status(context: RequirementContext, *, confirmation_requested: bool) -> RequirementStatus:
    if context.status is RequirementStatus.ARCHIVED:
        return RequirementStatus.ARCHIVED
    if confirmation_requested:
        return RequirementStatus.CONFIRMED if _complete_for_confirmation(context) else RequirementStatus.CLARIFICATION_REQUIRED
    if context.open_questions or not context.requirements:
        return RequirementStatus.CLARIFICATION_REQUIRED
    return RequirementStatus.ANALYZING


def merge_requirement_context(
    previous: RequirementContext | None,
    *,
    project_id: str,
    conversation_id: str,
    turn_id: str,
    utterance: str,
    project_type: str | None = None,
    proposals: tuple[dict[str, str], ...] = (),
    provider_questions: tuple[dict[str, str], ...] = (),
    lifecycle: RequirementLifecycle | None = None,
    confirmation_requested: bool | None = None,
) -> RequirementContext:
    """Merge one turn into the canonical, side-effect-free requirement context.

    User facts are the only source that creates ``CONFIRMED`` items. Provider
    proposals/questions remain explicitly non-authoritative.
    """

    base = previous or RequirementContext(project_id=project_id, conversation_id=conversation_id)
    if base.project_id != project_id or base.conversation_id != conversation_id:
        raise ValueError("requirement context identity mismatch")
    additions = _explicit_items(project_id, conversation_id, turn_id, utterance)
    all_confirmed = _replace_confirmed((*base.requirements, *base.constraints), additions)
    requirements = tuple(item for item in all_confirmed if item.category not in _CONSTRAINT_CATEGORIES)
    constraints = tuple(item for item in all_confirmed if item.category in _CONSTRAINT_CATEGORIES)

    addition_categories = {item.category for item in additions}
    standard = [item for item in base.standard_fields if item.category not in addition_categories]
    categories = {item.category for item in _active((*requirements, *constraints))}
    for category in ("PRODUCT_GOAL", "TARGET_PLATFORM"):
        if category not in categories and not any(item.category == category for item in standard):
            standard.append(StandardRequirementField(category=category))
    if "REMOTE_MONITORING" in categories and "WIRELESS_TECHNOLOGY" not in categories:
        if not any(item.category == "WIRELESS_TECHNOLOGY" for item in standard):
            standard.append(StandardRequirementField(category="WIRELESS_TECHNOLOGY"))

    proposed_items = list(base.proposed_items)
    confirmed_keys = {(item.category, item.normalized_value) for item in additions}
    proposed_items = [
        _copy_item(
            item,
            status=RequirementItemStatus.SUPERSEDED,
            superseded_by=next(
                (
                    confirmed.requirement_id
                    for confirmed in (*requirements, *constraints)
                    if (confirmed.category, confirmed.normalized_value)
                    == (item.category, item.normalized_value)
                ),
                item.requirement_id,
            ),
        )
        if (item.category, item.normalized_value) in confirmed_keys
        else item
        for item in proposed_items
    ]
    for proposal in proposals:
        category = proposal.get("category", "OTHER")
        value = proposal.get("value", "").strip()
        if not value or any(
            item.category == category
            and item.normalized_value == value
            and item.status is RequirementItemStatus.CONFIRMED
            for item in (*requirements, *constraints)
        ) or any(
            item.category == category
            and item.normalized_value == value
            and item.status is RequirementItemStatus.PROPOSED
            for item in proposed_items
        ):
            continue
        proposed_items.append(
            _item(
                project_id=project_id,
                conversation_id=conversation_id,
                turn_id=turn_id,
                category=category,
                value=value,
                provenance=RequirementProvenance.PROVIDER_RESPONSE,
                status=RequirementItemStatus.PROPOSED,
            )
        )

    confirmed = _active((*requirements, *constraints))
    questions = _question_values(
        project_id=project_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        confirmed=confirmed,
        existing=base.open_questions,
        provider_questions=provider_questions,
    )
    criteria = _acceptance_criteria(
        project_id=project_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        confirmed=confirmed,
        existing=base.acceptance_criteria,
    )
    explicit_criteria = _explicit_acceptance(
        project_id=project_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        utterance=utterance,
    )
    criteria = tuple(
        (*criteria, *explicit_criteria)
    )
    conflicts = _conflicts(
        project_id=project_id,
        conversation_id=conversation_id,
        turn_id=turn_id,
        confirmed=confirmed,
        existing=base.conflicts,
    )
    requested = _explicit_confirmation(utterance) if confirmation_requested is None else confirmation_requested
    provisional = RequirementContext(
        project_id=project_id,
        conversation_id=conversation_id,
        standard_fields=tuple(standard),
        requirements=requirements,
        constraints=constraints,
        proposed_items=tuple(proposed_items),
        open_questions=questions,
        acceptance_criteria=criteria,
        conflicts=conflicts,
        status=RequirementStatus.DRAFT,
        lifecycle=lifecycle or base.lifecycle,
    )
    status = _product_status(provisional, confirmation_requested=requested)
    if (
        not requested
        and not additions
        and base.effective_status is RequirementStatus.CONFIRMED
    ):
        # Provider-only enrichment must never reopen an already confirmed
        # requirement; its proposals remain separately marked PROPOSED.
        status = RequirementStatus.CONFIRMED
    next_lifecycle = lifecycle
    if next_lifecycle is None:
        next_lifecycle = (
            RequirementLifecycle.REQUIREMENTS_READY
            if status is RequirementStatus.CONFIRMED
            else RequirementLifecycle.REQUIREMENTS_REFINING
            if previous is not None
            else RequirementLifecycle.REQUIREMENTS_PARTIAL
        )
    if status is RequirementStatus.CONFIRMED and lifecycle is None:
        next_lifecycle = RequirementLifecycle.DESIGN_READY
    return _copy_context(provisional, status=status, lifecycle=next_lifecycle)


def transition_requirement(
    context: RequirementContext,
    target: RequirementStatus,
    *,
    explicit_confirmation: bool = False,
) -> RequirementContext:
    """Apply the product-facing state machine with fail-closed transitions."""

    if not isinstance(target, RequirementStatus):
        raise TypeError("requirement status is invalid")
    checked = RequirementContext.model_validate(context.model_dump(mode="python"), strict=False)
    allowed = {
        RequirementStatus.DRAFT: {RequirementStatus.ANALYZING, RequirementStatus.CLARIFICATION_REQUIRED, RequirementStatus.ARCHIVED},
        RequirementStatus.ANALYZING: {RequirementStatus.CLARIFICATION_REQUIRED, RequirementStatus.CONFIRMED, RequirementStatus.ARCHIVED},
        RequirementStatus.CLARIFICATION_REQUIRED: {RequirementStatus.ANALYZING, RequirementStatus.CONFIRMED, RequirementStatus.ARCHIVED},
        RequirementStatus.CONFIRMED: {RequirementStatus.ARCHIVED},
        RequirementStatus.ARCHIVED: set(),
    }
    if target not in allowed[checked.effective_status]:
        raise ValueError("invalid requirement status transition")
    if target is RequirementStatus.CONFIRMED and (not explicit_confirmation or not _complete_for_confirmation(checked)):
        raise ValueError("requirement confirmation gate rejected")
    lifecycle = RequirementLifecycle.DESIGN_READY if target is RequirementStatus.CONFIRMED else checked.lifecycle
    return _copy_context(checked, status=target, lifecycle=lifecycle)


def resolve_requirement_conflict(
    context: RequirementContext,
    conflict_id: str,
    resolution: str,
    *,
    turn_id: str,
) -> RequirementContext:
    """Record a user-provided conflict resolution without changing requirements."""

    checked = RequirementContext.model_validate(context.model_dump(mode="python"), strict=False)
    conflict = next((item for item in checked.conflicts if item.conflict_id == conflict_id), None)
    if conflict is None:
        raise ValueError("requirement conflict is unavailable")
    if conflict.status is RequirementConflictStatus.RESOLVED:
        raise ValueError("requirement conflict is already resolved")
    material = conflict.model_dump(mode="python")
    material.update(
        {
            "status": RequirementConflictStatus.RESOLVED,
            "resolution": resolution,
            "source_turn_id": _identifier(turn_id, field="turn_id"),
            "provenance": RequirementProvenance.USER_UTTERANCE,
            "fingerprint": "sha256:" + "0" * 64,
        }
    )
    updated = RequirementConflict.model_validate(material, strict=False)
    return _copy_context(
        checked,
        conflicts=tuple(updated if item.conflict_id == conflict_id else item for item in checked.conflicts),
    )


def to_engineering_requirement(context: RequirementContext):
    """Adapt the canonical context to the existing legacy planner contract."""

    from embedded_copilot.engineering_copilot import EngineeringRequirement

    active = _active((*context.requirements, *context.constraints))
    description = next((item.normalized_value for item in active if item.category == "PRODUCT_GOAL"), "未命名工程需求")
    constraints = tuple(
        f"{item.category}: {item.normalized_value}"
        for item in context.constraints
        if item.status is RequirementItemStatus.CONFIRMED
    )
    return EngineeringRequirement.create(
        requirement_id=_id("engineering-requirement", context.project_id, context.conversation_id),
        project_id=context.project_id,
        description=description,
        constraints=constraints,
        evidence_refs=(),
    )


def build_engineering_plan(context: RequirementContext, engineering_context):
    """Build the canonical, deterministic plan from the confirmed context.

    ``engineering_context`` remains accepted for compatibility with the existing
    application port. Requirement planning itself does not call a provider or an
    execution capability.
    """

    return generate_engineering_plan(context)


def _plan_provenance(
    *, project_id: str, source_type: str, source_id: str, source_fingerprint: str
) -> PlanProvenance:
    return PlanProvenance.create(
        project_id=project_id,
        source_type=source_type,
        source_id=source_id,
        source_fingerprint=source_fingerprint,
    )


def _phase_bindings(
    context: RequirementContext,
    plan_id: str,
    phase_id: str,
    categories: set[str],
) -> tuple[PlanEvidenceBinding, ...]:
    items = tuple(
        item
        for item in _active((*context.requirements, *context.constraints))
        if not categories or item.category in categories
    )
    bindings = [
        PlanEvidenceBinding.create(
            binding_id=_id("binding", plan_id, phase_id, "item", item.requirement_id),
            plan_id=plan_id,
            phase_id=phase_id,
            project_id=context.project_id,
            source_type="REQUIREMENT_ITEM",
            source_id=item.requirement_id,
            source_fingerprint=item.fingerprint,
            relationship="supports phase scope",
            provenance=(
                _plan_provenance(
                    project_id=context.project_id,
                    source_type="REQUIREMENT_CONTEXT",
                    source_id=context.conversation_id,
                    source_fingerprint=context.fingerprint,
                ),
            ),
        )
        for item in items
    ]
    for criterion in context.acceptance_criteria:
        if phase_id.endswith("verification") or phase_id.endswith("simulation"):
            bindings.append(
                PlanEvidenceBinding.create(
                    binding_id=_id("binding", plan_id, phase_id, "criterion", criterion.criterion_id),
                    plan_id=plan_id,
                    phase_id=phase_id,
                    project_id=context.project_id,
                    source_type="ACCEPTANCE_CRITERION",
                    source_id=criterion.criterion_id,
                    source_fingerprint=criterion.fingerprint,
                    relationship="defines verification target",
                    provenance=(
                        _plan_provenance(
                            project_id=context.project_id,
                            source_type="REQUIREMENT_CONTEXT",
                            source_id=context.conversation_id,
                            source_fingerprint=context.fingerprint,
                        ),
                    ),
                )
            )
    return tuple(bindings)


def _phase(
    *,
    context: RequirementContext,
    plan_id: str,
    phase_key: str,
    name: str,
    description: str,
    dependencies: tuple[str, ...],
    categories: set[str],
    task_specs: tuple[tuple[str, PlanTaskType], ...],
    output_specs: tuple[tuple[str, str], ...],
    verification_method: str,
    completed: bool = False,
) -> EngineeringPlanPhase:
    phase_id = f"phase-{phase_key}"
    provenance = (
        _plan_provenance(
            project_id=context.project_id,
            source_type="REQUIREMENT_CONTEXT",
            source_id=context.conversation_id,
            source_fingerprint=context.fingerprint,
        ),
    )
    tasks = tuple(
        EngineeringPlanTask.create(
            task_id=f"{phase_id}-task-{index}",
            phase_id=phase_id,
            plan_id=plan_id,
            project_id=context.project_id,
            title=title,
            type=task_type,
            status=PlanTaskStatus.COMPLETED if completed else PlanTaskStatus.PENDING,
            dependencies=(),
            provenance=provenance,
        )
        for index, (title, task_type) in enumerate(task_specs, start=1)
    )
    outputs = tuple(
        PlanOutput.create(
            output_id=f"{phase_id}-output-{index}",
            phase_id=phase_id,
            plan_id=plan_id,
            project_id=context.project_id,
            name=name,
            output_type=output_type,
            status=PlanTaskStatus.COMPLETED if completed else PlanTaskStatus.PENDING,
            provenance=provenance,
        )
        for index, (name, output_type) in enumerate(output_specs, start=1)
    )
    criteria = (
        tuple(context.acceptance_criteria)
        if phase_id.endswith(("simulation", "verification"))
        else ()
    )
    verification = (
        PlanVerificationStrategy.create(
            strategy_id=f"{phase_id}-verification",
            plan_id=plan_id,
            project_id=context.project_id,
            criterion_ids=tuple(item.criterion_id for item in criteria),
            method=verification_method,
            evidence_required=("review_record", "verification_result"),
            provenance=provenance,
        ),
    )
    return EngineeringPlanPhase.create(
        phase_id=phase_id,
        plan_id=plan_id,
        project_id=context.project_id,
        name=name,
        description=description,
        status=PlanPhaseStatus.COMPLETED if completed else PlanPhaseStatus.PENDING,
        dependencies=dependencies,
        inputs=("RequirementContext",),
        tasks=tasks,
        outputs=outputs,
        verification=verification,
        evidence_bindings=_phase_bindings(context, plan_id, phase_id, categories),
        provenance=provenance,
    )


def generate_engineering_plan(context: RequirementContext) -> EngineeringPlan:
    """Generate a reviewable plan using only confirmed requirement facts."""

    checked = RequirementContext.model_validate(context.model_dump(mode="python"), strict=False)
    if checked.effective_status is not RequirementStatus.CONFIRMED:
        raise ValueError("engineering plan requires confirmed requirements")
    if checked.open_questions or checked.conflicts or not checked.acceptance_criteria:
        raise ValueError("engineering plan completeness gate rejected")

    requirement = to_engineering_requirement(checked)
    plan_id = _id("plan", checked.project_id, checked.conversation_id, checked.fingerprint)
    context_provenance = _plan_provenance(
        project_id=checked.project_id,
        source_type="REQUIREMENT_CONTEXT",
        source_id=checked.conversation_id,
        source_fingerprint=checked.fingerprint,
    )
    confirmed_categories = {
        item.category for item in _active((*checked.requirements, *checked.constraints))
    }
    if not {"PRODUCT_GOAL", "TARGET_PLATFORM", "FUNCTIONAL"}.issubset(
        confirmed_categories
    ):
        raise ValueError("engineering plan completeness gate rejected")
    phases: list[EngineeringPlanPhase] = []
    phases.append(
        _phase(
            context=checked,
            plan_id=plan_id,
            phase_key="requirement-confirmation",
            name="Requirement Confirmation",
            description="确认目标、平台、功能、约束和验收标准。",
            dependencies=(),
            categories=confirmed_categories,
            task_specs=(("确认已表达的工程需求", PlanTaskType.REVIEW),),
            output_specs=(("Confirmed RequirementContext", "REQUIREMENT_CONTEXT"),),
            verification_method="Requirement review",
            completed=True,
        )
    )
    phases.append(
        _phase(
            context=checked,
            plan_id=plan_id,
            phase_key="system-architecture",
            name="System Architecture",
            description="定义系统边界、模块职责、接口和验证入口。",
            dependencies=(phases[-1].phase_id,),
            categories={"PRODUCT_GOAL", "TARGET_PLATFORM", "FUNCTIONAL", "INPUT", "OUTPUT"},
            task_specs=(("定义系统边界", PlanTaskType.DESIGN), ("定义模块接口", PlanTaskType.DESIGN)),
            output_specs=(("Architecture Document", "DOCUMENT"),),
            verification_method="Architecture review",
        )
    )
    has_hardware = bool(confirmed_categories & {"HARDWARE_SCOPE", "POWER_SOURCE", "MOTOR", "INPUT", "OUTPUT"})
    has_firmware = bool(confirmed_categories & {"TARGET_PLATFORM", "RTOS", "FUNCTIONAL", "SOFTWARE_SCOPE", "OUTPUT"})
    has_pcb = has_hardware and any(
        item.normalized_value.casefold() in {"pcb", "schematic", "原理图", "印刷电路板"}
        for item in _active((*checked.requirements, *checked.constraints))
        if item.category == "DELIVERABLE"
    )
    phase_specs: list[tuple[str, str, set[str], tuple[tuple[str, PlanTaskType], ...], tuple[tuple[str, str], ...], str]] = []
    if has_hardware:
        phase_specs.append(("hardware-design", "Hardware Design", {"HARDWARE_SCOPE", "POWER_SOURCE", "MOTOR", "INPUT", "OUTPUT"}, (("选择 MCU 和关键器件", PlanTaskType.DESIGN), ("设计电源与外设映射", PlanTaskType.DESIGN)), (("Hardware Design Proposal", "DOCUMENT"),), "Hardware design review"))
    if has_pcb:
        phase_specs.append(("pcb-design", "PCB Design", {"HARDWARE_SCOPE", "POWER_SOURCE", "MOTOR", "DELIVERABLE"}, (("整理原理图与连接关系", PlanTaskType.DESIGN), ("规划 PCB 布局", PlanTaskType.DESIGN)), (("Schematic", "SCHEMATIC"), ("BOM", "BOM"), ("PCB Proposal", "PCB")), "Schematic and PCB review"))
    if has_firmware:
        phase_specs.append(("firmware-development", "Firmware Development", {"TARGET_PLATFORM", "RTOS", "FUNCTIONAL", "SOFTWARE_SCOPE"}, (("定义固件模块", PlanTaskType.IMPLEMENTATION), ("定义构建与软件验证入口", PlanTaskType.VERIFICATION)), (("Firmware Source Proposal", "SOURCE_CODE"), ("Build Artifact Specification", "BUILD_ARTIFACT")), "Firmware review and software test"))
    phase_specs.append(("simulation", "Simulation", {"FUNCTIONAL", "INPUT", "OUTPUT"}, (("定义仿真场景", PlanTaskType.SIMULATION),), (("Simulation Scenario", "SIMULATION"),), "Simulation test"))
    phase_specs.append(("verification", "Verification", {"FUNCTIONAL", "ACCURACY", "BATTERY_LIFE", "RESPONSE_TIME"}, (("建立验收证据矩阵", PlanTaskType.VERIFICATION),), (("Verification Report", "REPORT"),), "Acceptance criteria verification"))
    for key, name, categories, task_specs, output_specs, method in phase_specs:
        dependencies = (phases[-1].phase_id,)
        phase = _phase(
            context=checked,
            plan_id=plan_id,
            phase_key=key,
            name=name,
            description=f"围绕已确认需求执行{name}阶段的 Proposal 规划。",
            dependencies=dependencies,
            categories=categories,
            task_specs=task_specs,
            output_specs=output_specs,
            verification_method=method,
        )
        phases.append(phase)

    item_provenance = tuple(
        _plan_provenance(
            project_id=checked.project_id,
            source_type="REQUIREMENT_ITEM",
            source_id=item.requirement_id,
            source_fingerprint=item.fingerprint,
        )
        for item in _active((*checked.requirements, *checked.constraints))
    )
    risks: list[PlanRisk] = []
    if {"MOTOR", "POWER_SOURCE"}.issubset(confirmed_categories):
        risks.append(
            PlanRisk.create(
                risk_id=_id("risk", plan_id, "power"),
                plan_id=plan_id,
                project_id=checked.project_id,
                category="POWER",
                description="电机堵转电流可能超过供电能力。",
                impact=PlanRiskLevel.HIGH,
                probability=PlanRiskLevel.MEDIUM,
                mitigation="在硬件设计阶段核算堵转电流并选择具备裕量的供电方案。",
                provenance=item_provenance,
            )
        )
    risks.append(
        PlanRisk.create(
            risk_id=_id("risk", plan_id, "requirement-change"),
            plan_id=plan_id,
            project_id=checked.project_id,
            category="REQUIREMENT",
            description="需求变化可能导致阶段输出和验证范围调整。",
            impact=PlanRiskLevel.MEDIUM,
            probability=PlanRiskLevel.MEDIUM,
            mitigation="在每个阶段评审前重新核对 RequirementContext 和 Acceptance Criteria。",
            provenance=(context_provenance,),
        )
    )
    strategy = tuple(item for phase in phases for item in phase.verification)
    model_operation_bindings = tuple(
        PlanModelOperationBinding.create(
            phase_id=phase.phase_id,
            plan_task_id=task.task_id,
            operation_id=operation_id,
            stage_id=stage_id,
        )
        for phase in phases
        for task in phase.tasks
        for phase_id, task_title, operation_id, stage_id in (
            (
                "phase-hardware-design", "选择 MCU 和关键器件",
                "HARDWARE_SELECTION_PROPOSAL", EngineeringStage.HARDWARE_SELECTION,
            ),
            (
                "phase-pcb-design", "整理原理图与连接关系",
                "SCHEMATIC_DESIGN_PROPOSAL", EngineeringStage.SCHEMATIC_DESIGN,
            ),
        )
        if phase.phase_id == phase_id and task.title == task_title
    )
    legacy_tasks = (
        EngineeringTask.create(
            task_id=f"{plan_id}-analysis",
            sequence=1,
            title="分析工程需求",
            description=requirement.description,
            depends_on=(),
            risk="需求或约束可能仍需评审",
            evidence_refs=(),
        ),
        EngineeringTask.create(
            task_id=f"{plan_id}-proposal",
            sequence=2,
            title="生成工程 Proposal",
            description="依据已确认 RequirementContext 生成只读工程计划。",
            depends_on=(f"{plan_id}-analysis",),
            risk="Proposal 不等于已批准实现",
            evidence_refs=(),
        ),
        EngineeringTask.create(
            task_id=f"{plan_id}-verification",
            sequence=3,
            title="人工审核与验证",
            description="核验阶段、风险、证据绑定和验收策略。",
            depends_on=(f"{plan_id}-proposal",),
            risk="未经审批不得执行",
            evidence_refs=(),
        ),
    )
    draft = EngineeringPlan.create(
        plan_id=plan_id,
        requirement_id=requirement.requirement_id,
        project_id=checked.project_id,
        context_fingerprint=checked.fingerprint,
        tasks=legacy_tasks,
        title=requirement.description,
        objective=requirement.description,
        status=PlanStatus.DRAFT,
        phases=tuple(phases),
        plan_risks=tuple(risks),
        verification_strategy=strategy,
        evidence_bindings=tuple(binding for phase in phases for binding in phase.evidence_bindings),
        provenance=(context_provenance,),
        risks=tuple(risk.description for risk in risks),
        evidence_refs=tuple(
            binding.source_id
            for phase in phases
            for binding in phase.evidence_bindings
        ),
        notes=(),
        confidence=1.0,
        model_operation_schema_version=2,
        model_operation_bindings=model_operation_bindings,
    )
    generating = transition_engineering_plan(draft, PlanStatus.GENERATING)
    return transition_engineering_plan(generating, PlanStatus.PROPOSED)


def generate_architecture_design(context: RequirementContext, plan: EngineeringPlan):
    """Compatibility entry point for the deterministic architecture service."""

    from embedded_copilot.engineering_copilot.architecture_service import (
        generate_architecture_design as _generate_architecture_design,
    )

    return _generate_architecture_design(context, plan)


def transition_architecture_design(design, target):
    """Expose architecture lifecycle through the engineering service boundary."""

    from embedded_copilot.engineering_copilot.architecture_service import (
        transition_architecture_design as _transition_architecture_design,
    )

    return _transition_architecture_design(design, target)


def approve_architecture_design(design, review=None, *, reviewer=None, decided_at=None):
    """Expose architecture review through the engineering service boundary."""

    from embedded_copilot.engineering_copilot.architecture_service import (
        approve_architecture_design as _approve_architecture_design,
    )

    return _approve_architecture_design(
        design, review, reviewer=reviewer, decided_at=decided_at
    )


__all__ = (
    "build_engineering_plan",
    "generate_engineering_plan",
    "generate_architecture_design",
    "approve_architecture_design",
    "transition_architecture_design",
    "is_plan_approval_request",
    "merge_requirement_context",
    "resolve_requirement_conflict",
    "to_engineering_requirement",
    "transition_requirement",
)
