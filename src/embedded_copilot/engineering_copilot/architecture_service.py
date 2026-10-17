from __future__ import annotations

import hashlib
from collections.abc import Iterable
from copy import deepcopy
from datetime import UTC, datetime

from .common import validate_copy
from .contracts import (
    ArchitectureBoundary,
    ArchitectureComponent,
    ArchitectureConfidence,
    ArchitectureDecision,
    ArchitectureDecisionType,
    ArchitectureDesign,
    ArchitectureDesignStatus,
    ArchitectureEvidenceBinding,
    ArchitectureFlow,
    ArchitectureFlowType,
    ArchitectureInterface,
    ArchitectureInterfaceType,
    ArchitectureLevel,
    ArchitectureReviewDecision,
    ArchitectureReviewFindingStatus,
    ArchitectureReviewStatus,
    ArchitectureVerification,
    EngineeringPlan,
    PlanProvenance,
    PlanStatus,
)

_ARCHITECTURE_TRANSITIONS: dict[ArchitectureDesignStatus, frozenset[ArchitectureDesignStatus]] = {
    ArchitectureDesignStatus.DRAFT: frozenset(
        {ArchitectureDesignStatus.GENERATING, ArchitectureDesignStatus.CANCELLED, ArchitectureDesignStatus.SUPERSEDED}
    ),
    ArchitectureDesignStatus.GENERATING: frozenset(
        {ArchitectureDesignStatus.PROPOSED, ArchitectureDesignStatus.REJECTED, ArchitectureDesignStatus.CANCELLED, ArchitectureDesignStatus.SUPERSEDED}
    ),
    ArchitectureDesignStatus.PROPOSED: frozenset(
        {ArchitectureDesignStatus.REVIEWING, ArchitectureDesignStatus.REJECTED, ArchitectureDesignStatus.CANCELLED, ArchitectureDesignStatus.SUPERSEDED}
    ),
    ArchitectureDesignStatus.REVIEWING: frozenset(
        {ArchitectureDesignStatus.APPROVED, ArchitectureDesignStatus.REJECTED, ArchitectureDesignStatus.CANCELLED, ArchitectureDesignStatus.SUPERSEDED}
    ),
    ArchitectureDesignStatus.APPROVED: frozenset(
        {ArchitectureDesignStatus.IMPLEMENTING, ArchitectureDesignStatus.CANCELLED, ArchitectureDesignStatus.SUPERSEDED}
    ),
    ArchitectureDesignStatus.IMPLEMENTING: frozenset(
        {ArchitectureDesignStatus.COMPLETED, ArchitectureDesignStatus.CANCELLED, ArchitectureDesignStatus.SUPERSEDED}
    ),
    ArchitectureDesignStatus.COMPLETED: frozenset(),
    ArchitectureDesignStatus.REJECTED: frozenset(),
    ArchitectureDesignStatus.CANCELLED: frozenset(),
    ArchitectureDesignStatus.SUPERSEDED: frozenset(),
    ArchitectureDesignStatus.INVALIDATED: frozenset(),
}


def _id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def _normalized_request(value: str) -> str:
    return " ".join(value.casefold().split())


def is_architecture_generation_request(value: str) -> bool:
    normalized = _normalized_request(value)
    return normalized in {
        "开始系统设计",
        "开始架构设计",
        "生成系统架构",
        "设计系统架构",
        "开始 system design",
        "generate system architecture",
        "start system design",
    }


def is_architecture_approval_request(value: str) -> bool:
    normalized = _normalized_request(value)
    return normalized in {
        "批准架构",
        "同意该架构",
        "同意架构",
        "确认系统架构",
        "approve architecture",
        "approve system architecture",
    }


def _rebuild_design(design: ArchitectureDesign, **updates: object) -> ArchitectureDesign:
    values = deepcopy(design.model_dump(mode="python"))
    values.update(updates)
    values["fingerprint"] = "sha256:" + "0" * 64
    return ArchitectureDesign.model_validate(values, strict=False)


def transition_architecture_design(
    design: ArchitectureDesign, target: ArchitectureDesignStatus
) -> ArchitectureDesign:
    """Apply the architecture lifecycle without opening an implementation port."""

    checked = validate_copy(design, ArchitectureDesign)
    if not isinstance(target, ArchitectureDesignStatus):
        raise TypeError("architecture design status is invalid")
    if target not in _ARCHITECTURE_TRANSITIONS[checked.status]:
        raise ValueError("invalid architecture design status transition")
    return _rebuild_design(checked, status=target)


def approve_architecture_design(
    design: ArchitectureDesign,
    review: ArchitectureReviewDecision | None = None,
    *,
    reviewer: str | None = None,
    decided_at: datetime | None = None,
) -> tuple[ArchitectureDesign, ArchitectureReviewDecision]:
    """Approve a proposal only when the review is bound to its current content."""

    checked = validate_copy(design, ArchitectureDesign)
    if checked.status not in {
        ArchitectureDesignStatus.PROPOSED,
        ArchitectureDesignStatus.REVIEWING,
    }:
        raise ValueError("architecture design is not awaiting review")
    reviewing = (
        checked
        if checked.status is ArchitectureDesignStatus.REVIEWING
        else transition_architecture_design(checked, ArchitectureDesignStatus.REVIEWING)
    )
    valid_binding_ids = {item.binding_id for item in reviewing.evidence_bindings}
    if review is not None:
        checked_review = validate_copy(review, ArchitectureReviewDecision)
        if (
            checked_review.architecture_id != reviewing.architecture_id
            or checked_review.plan_id != reviewing.plan_id
            or checked_review.project_id != reviewing.project_id
            or checked_review.proposal_fingerprint != reviewing.content_fingerprint
            or checked_review.status is not ArchitectureReviewStatus.APPROVED
        ):
            raise ValueError("architecture review binding rejected")
        if reviewer is not None and reviewer != checked_review.reviewer:
            raise ValueError("architecture reviewer mismatch")
        if decided_at is not None and decided_at.astimezone(UTC) != checked_review.decided_at:
            raise ValueError("architecture decision time mismatch")
        if any(
            finding.status is ArchitectureReviewFindingStatus.OPEN
            for finding in checked_review.findings
        ):
            raise ValueError("open architecture review findings prevent approval")
        if any(
            binding_id not in valid_binding_ids
            for finding in checked_review.findings
            for binding_id in finding.evidence_binding_ids
        ):
            raise ValueError("architecture review evidence binding rejected")
        decision = checked_review
    else:
        if reviewer is None:
            raise TypeError("reviewer is required")
        decision = ArchitectureReviewDecision.create(
            architecture_id=reviewing.architecture_id,
            plan_id=reviewing.plan_id,
            project_id=reviewing.project_id,
            proposal_fingerprint=reviewing.content_fingerprint,
            status=ArchitectureReviewStatus.APPROVED,
            reviewer=reviewer,
            decided_at=decided_at or datetime.now(UTC),
            findings=(),
        )
    return _rebuild_design(reviewing, status=ArchitectureDesignStatus.APPROVED), decision


def _confirmed_items(context) -> tuple:
    from embedded_copilot.engineering_requirements.models import RequirementItemStatus

    return tuple(
        item
        for item in (*context.requirements, *context.constraints)
        if item.status is RequirementItemStatus.CONFIRMED
    )


def _provenance(
    project_id: str, source_type: str, source_id: str, source_fingerprint: str
) -> PlanProvenance:
    return PlanProvenance.create(
        project_id=project_id,
        source_type=source_type,
        source_id=source_id,
        source_fingerprint=source_fingerprint,
    )


def _relevant_evidence(context, categories: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    categories = set(categories)
    items = _confirmed_items(context)
    selected = tuple(item for item in items if not categories or item.category in categories)
    if not selected:
        selected = items
    requirement_ids = tuple(item.requirement_id for item in selected)
    selected_requirements = set(requirement_ids)
    criteria = tuple(
        criterion
        for criterion in context.acceptance_criteria
        if criterion.requirement_id is None or criterion.requirement_id in selected_requirements
    )
    if not criteria:
        criteria = tuple(context.acceptance_criteria)
    return requirement_ids, tuple(item.criterion_id for item in criteria)


def _binding(
    *,
    context,
    plan: EngineeringPlan,
    architecture_id: str,
    source_type: str,
    source_id: str,
    source_fingerprint: str,
    relationship: str,
    categories: Iterable[str],
    ordinal: int,
) -> ArchitectureEvidenceBinding:
    requirement_ids, criterion_ids = _relevant_evidence(context, categories)
    if not requirement_ids or not criterion_ids:
        raise ValueError("architecture evidence requires requirements and acceptance criteria")
    context_provenance = _provenance(
        context.project_id,
        "REQUIREMENT_CONTEXT",
        context.conversation_id,
        context.fingerprint,
    )
    plan_provenance = _provenance(
        context.project_id, "ENGINEERING_PLAN", plan.plan_id, plan.fingerprint
    )
    return ArchitectureEvidenceBinding.create(
        binding_id=_id(
            "architecture-binding", architecture_id, source_type, source_id,
            relationship, str(ordinal)
        ),
        architecture_id=architecture_id,
        plan_id=plan.plan_id,
        project_id=context.project_id,
        source_type=source_type,
        source_id=source_id,
        source_fingerprint=source_fingerprint,
        requirement_ids=requirement_ids,
        acceptance_criterion_ids=criterion_ids,
        relationship=relationship,
        provenance=(context_provenance, plan_provenance),
    )


def generate_architecture_design(context, plan: EngineeringPlan) -> ArchitectureDesign:
    """Generate a deterministic architecture proposal from confirmed facts only."""

    from embedded_copilot.engineering_requirements.models import RequirementStatus

    checked_context = type(context).model_validate(
        context.model_dump(mode="python"), strict=False
    )
    checked_plan = validate_copy(plan, EngineeringPlan)
    if checked_context.effective_status is not RequirementStatus.CONFIRMED:
        raise ValueError("architecture design requires confirmed requirements")
    if checked_plan.status is not PlanStatus.APPROVED:
        raise ValueError("architecture design requires approved engineering plan")
    if checked_plan.project_id != checked_context.project_id:
        raise ValueError("architecture project binding rejected")
    if checked_plan.context_fingerprint != checked_context.fingerprint:
        raise ValueError("architecture context fingerprint binding rejected")
    if checked_context.open_questions or any(
        conflict.status.value == "OPEN" for conflict in checked_context.conflicts
    ):
        raise ValueError("architecture completeness gate rejected")
    if not checked_context.acceptance_criteria:
        raise ValueError("architecture requires acceptance criteria")
    confirmed = _confirmed_items(checked_context)
    categories = {item.category for item in confirmed}
    if not {"PRODUCT_GOAL", "TARGET_PLATFORM", "FUNCTIONAL"}.issubset(categories):
        raise ValueError("architecture completeness gate rejected")
    if any(
        binding.source_type == "REQUIREMENT_ITEM"
        and binding.source_id not in {item.requirement_id for item in confirmed}
        for binding in checked_plan.evidence_bindings
    ):
        raise ValueError("architecture plan contains unconfirmed evidence")

    architecture_id = _id(
        "architecture", checked_context.project_id, checked_plan.plan_id, checked_context.fingerprint
    )
    context_provenance = _provenance(
        checked_context.project_id,
        "REQUIREMENT_CONTEXT",
        checked_context.conversation_id,
        checked_context.fingerprint,
    )
    target = next(
        (item.normalized_value for item in confirmed if item.category == "TARGET_PLATFORM"),
        "UNKNOWN",
    )
    target_item = next(item for item in confirmed if item.category == "TARGET_PLATFORM")
    functional = {item.normalized_value for item in confirmed if item.category == "FUNCTIONAL"}
    has_robot_hardware = bool(
        {item.normalized_value for item in confirmed if item.category == "HARDWARE_SCOPE"}
        & {"MOBILE_ROBOT"}
    )
    has_motor = "MOTOR" in categories
    has_sensor = bool(functional & {"LINE_FOLLOWING", "OBSTACLE_AVOIDANCE"}) or "INPUT" in categories
    has_power = "POWER_SOURCE" in categories or has_robot_hardware
    has_communication = "COMMUNICATION" in categories

    bindings: list[ArchitectureEvidenceBinding] = []

    def add_binding(source_type: str, source_id: str, source_fp: str, relation: str, relevant: Iterable[str]):
        item = _binding(
            context=checked_context,
            plan=checked_plan,
            architecture_id=architecture_id,
            source_type=source_type,
            source_id=source_id,
            source_fingerprint=source_fp,
            relationship=relation,
            categories=relevant,
            ordinal=len(bindings),
        )
        bindings.append(item)
        return item.binding_id

    def requirement_binding(prefix: str, relevant: Iterable[str], relation: str) -> str:
        requirement_ids, _ = _relevant_evidence(checked_context, relevant)
        source = next(
            item for item in confirmed if item.requirement_id in requirement_ids
        )
        return add_binding("REQUIREMENT_ITEM", source.requirement_id, source.fingerprint, relation, relevant)

    component_specs: list[tuple[str, str, str, ArchitectureLevel, ArchitectureConfidence, tuple[str, ...]]] = [
        (
            "System",
            "SYSTEM",
            "Defines the system boundary and responsibility split.",
            ArchitectureLevel.SYSTEM,
            ArchitectureConfidence.INFERRED,
            tuple(categories),
        ),
        (
            target,
            "CONTROLLER",
            "Provides the confirmed target control platform.",
            ArchitectureLevel.HARDWARE,
            ArchitectureConfidence.EXPLICIT,
            ("TARGET_PLATFORM",),
        ),
    ]
    if has_power:
        component_specs.append(("Power", "POWER", "Provides the confirmed or required power boundary.", ArchitectureLevel.HARDWARE, ArchitectureConfidence.INFERRED, ("POWER_SOURCE", "HARDWARE_SCOPE")))
    if has_sensor:
        component_specs.append(("Sensor/Input", "SENSOR_INPUT", "Captures inputs required by confirmed functions.", ArchitectureLevel.HARDWARE, ArchitectureConfidence.INFERRED, ("FUNCTIONAL", "INPUT")))
    if has_motor:
        component_specs.append(("Motor/Actuator", "ACTUATOR", "Provides the confirmed motor or output boundary.", ArchitectureLevel.HARDWARE, ArchitectureConfidence.EXPLICIT, ("MOTOR", "OUTPUT")))
    if has_communication:
        component_specs.append(("Communication", "COMMUNICATION", "Provides the confirmed communication boundary.", ArchitectureLevel.INTERFACE, ArchitectureConfidence.EXPLICIT, ("COMMUNICATION",)))
    component_specs.extend(
        (
            ("Driver", "DRIVER", "Separates hardware access from control logic.", ArchitectureLevel.SOFTWARE, ArchitectureConfidence.INFERRED, ("TARGET_PLATFORM", "FUNCTIONAL")),
            ("Control", "CONTROL", "Coordinates confirmed control behavior.", ArchitectureLevel.SOFTWARE, ArchitectureConfidence.INFERRED, ("FUNCTIONAL",)),
            ("Application", "APPLICATION", "Owns product-level behavior and acceptance flow.", ArchitectureLevel.SOFTWARE, ArchitectureConfidence.INFERRED, ("PRODUCT_GOAL", "FUNCTIONAL")),
        )
    )
    components: list[ArchitectureComponent] = []
    component_by_category: dict[str, ArchitectureComponent] = {}
    for name, category, responsibility, level, confidence, relevant in component_specs:
        source = next((item for item in confirmed if item.category in set(relevant)), target_item)
        binding_id = add_binding(
            "REQUIREMENT_ITEM", source.requirement_id, source.fingerprint,
            "supports architecture component", relevant,
        )
        component = ArchitectureComponent.create(
            component_id=_id("architecture-component", architecture_id, category),
            architecture_id=architecture_id,
            project_id=checked_context.project_id,
            name=name,
            category=category,
            responsibility=responsibility,
            level=level,
            confidence=confidence,
            evidence_binding_ids=(binding_id,),
            provenance=(context_provenance,),
        )
        components.append(component)
        component_by_category[category] = component

    def component(category: str) -> ArchitectureComponent:
        return component_by_category[category]

    hardware_ids = tuple(
        item.component_id
        for item in components
        if item.level is ArchitectureLevel.HARDWARE
    )
    software_ids = tuple(
        item.component_id
        for item in components
        if item.level is ArchitectureLevel.SOFTWARE
    )
    boundary_source = checked_plan.phases[0]
    boundary_binding = add_binding(
        "PLAN_PHASE", boundary_source.phase_id, boundary_source.fingerprint,
        "supports hardware and software boundary", categories,
    )
    boundary = ArchitectureBoundary.create(
        architecture_id=architecture_id,
        project_id=checked_context.project_id,
        hardware_component_ids=hardware_ids,
        software_component_ids=software_ids,
        evidence_binding_ids=(boundary_binding,),
        provenance=(context_provenance,),
    )

    interfaces: list[ArchitectureInterface] = []

    def add_interface(source: ArchitectureComponent, target_component: ArchitectureComponent, interface_type: ArchitectureInterfaceType, protocol: str, relevant: Iterable[str]):
        binding_id = requirement_binding("interface", relevant, "supports architecture interface")
        interfaces.append(
            ArchitectureInterface.create(
                interface_id=_id("architecture-interface", architecture_id, source.component_id, target_component.component_id),
                architecture_id=architecture_id,
                project_id=checked_context.project_id,
                source_component_id=source.component_id,
                target_component_id=target_component.component_id,
                interface_type=interface_type,
                protocol=protocol,
                verification="review interface contract and retain verification evidence",
                confidence=ArchitectureConfidence.EXPLICIT if protocol != "UNKNOWN" else ArchitectureConfidence.UNKNOWN,
                evidence_binding_ids=(binding_id,),
                provenance=(context_provenance,),
            )
        )

    if has_sensor:
        add_interface(component("SENSOR_INPUT"), component("CONTROLLER"), ArchitectureInterfaceType.HARDWARE, "UNKNOWN", ("FUNCTIONAL", "INPUT"))
    if has_power:
        add_interface(component("POWER"), component("CONTROLLER"), ArchitectureInterfaceType.HARDWARE, "UNKNOWN", ("POWER_SOURCE", "HARDWARE_SCOPE"))
    if has_motor:
        add_interface(component("CONTROLLER"), component("ACTUATOR"), ArchitectureInterfaceType.HARDWARE, "UNKNOWN", ("MOTOR", "OUTPUT"))
    if has_communication:
        add_interface(component("CONTROLLER"), component("COMMUNICATION"), ArchitectureInterfaceType.SOFTWARE, next(item.normalized_value for item in confirmed if item.category == "COMMUNICATION"), ("COMMUNICATION",))
    add_interface(component("DRIVER"), component("CONTROL"), ArchitectureInterfaceType.SOFTWARE, "internal control interface", ("TARGET_PLATFORM", "FUNCTIONAL"))
    add_interface(component("CONTROL"), component("APPLICATION"), ArchitectureInterfaceType.SOFTWARE, "internal application interface", ("PRODUCT_GOAL", "FUNCTIONAL"))

    def flow(source: ArchitectureComponent, target_component: ArchitectureComponent, flow_type: ArchitectureFlowType, data: str, relevant: Iterable[str]) -> ArchitectureFlow:
        binding_id = requirement_binding("flow", relevant, "supports architecture flow")
        return ArchitectureFlow.create(
            flow_id=_id("architecture-flow", architecture_id, flow_type.value, source.component_id, target_component.component_id),
            architecture_id=architecture_id,
            project_id=checked_context.project_id,
            flow_type=flow_type,
            source_component_id=source.component_id,
            target_component_id=target_component.component_id,
            data_or_command=data,
            confidence=ArchitectureConfidence.INFERRED,
            evidence_binding_ids=(binding_id,),
            provenance=(context_provenance,),
        )

    data_flows = (
        flow(component("SENSOR_INPUT"), component("CONTROL"), ArchitectureFlowType.DATA, "sensor input data", ("FUNCTIONAL", "INPUT")),
    ) if has_sensor else ()
    control_flows = (
        flow(component("CONTROL"), component("ACTUATOR"), ArchitectureFlowType.CONTROL, "motion command", ("FUNCTIONAL", "MOTOR")),
    ) if has_motor else ()

    decisions: list[ArchitectureDecision] = []

    def add_decision(subject: str, selected: str, alternatives: tuple[str, ...], decision_type: ArchitectureDecisionType, confidence: ArchitectureConfidence, relevant: Iterable[str]):
        binding_id = requirement_binding("decision", relevant, "supports architecture decision")
        decisions.append(
            ArchitectureDecision.create(
                decision_id=_id("architecture-decision", architecture_id, subject),
                architecture_id=architecture_id,
                project_id=checked_context.project_id,
                subject=subject,
                selected_option=selected,
                alternatives=alternatives,
                decision_type=decision_type,
                rationale="Selected or inferred from confirmed requirement evidence; alternatives remain review candidates.",
                confidence=confidence,
                evidence_binding_ids=(binding_id,),
                provenance=(context_provenance,),
            )
        )

    add_decision("controller platform", target, ("UNKNOWN_ALTERNATIVE",), ArchitectureDecisionType.HARDWARE_SELECTION, ArchitectureConfidence.EXPLICIT, ("TARGET_PLATFORM",))
    add_decision("software layering", "Driver/Control/Application", ("flat application",), ArchitectureDecisionType.ARCHITECTURE_PATTERN, ArchitectureConfidence.INFERRED, ("PRODUCT_GOAL", "FUNCTIONAL"))

    verification: list[ArchitectureVerification] = []
    strategies = checked_plan.verification_strategy or tuple(
        item for phase in checked_plan.phases for item in phase.verification
    )
    criterion_ids = tuple(item.criterion_id for item in checked_context.acceptance_criteria)
    for index, strategy in enumerate(strategies, start=1):
        strategy_criteria = tuple(strategy.criterion_ids) or criterion_ids
        binding_id = add_binding(
            "PLAN_VERIFICATION", strategy.strategy_id, strategy.fingerprint,
            "maps approved plan verification to architecture verification", categories,
        )
        verification.append(
            ArchitectureVerification.create(
                verification_id=_id("architecture-verification", architecture_id, str(index)),
                architecture_id=architecture_id,
                project_id=checked_context.project_id,
                method=strategy.method,
                evidence_required=strategy.evidence_required or ("review_record",),
                acceptance_criterion_ids=strategy_criteria,
                evidence_binding_ids=(binding_id,),
                provenance=(context_provenance,),
            )
        )
    if not verification:
        raise ValueError("architecture requires plan verification strategy")

    constraints = tuple(
        item.normalized_value
        for item in confirmed
        if item in checked_context.constraints
    )
    draft = ArchitectureDesign.create(
        architecture_id=architecture_id,
        project_id=checked_context.project_id,
        plan_id=checked_plan.plan_id,
        context_fingerprint=checked_context.fingerprint,
        plan_fingerprint=checked_plan.fingerprint,
        title=f"{checked_plan.title} Architecture",
        objective=checked_plan.objective,
        status=ArchitectureDesignStatus.DRAFT,
        system_components=tuple(components),
        interfaces=tuple(interfaces),
        boundary=boundary,
        data_flow=data_flows,
        control_flow=control_flows,
        decisions=tuple(decisions),
        verification=tuple(verification),
        constraints=constraints,
        evidence_bindings=tuple(bindings),
        provenance=(context_provenance,),
    )
    generating = transition_architecture_design(draft, ArchitectureDesignStatus.GENERATING)
    return transition_architecture_design(generating, ArchitectureDesignStatus.PROPOSED)


__all__ = (
    "approve_architecture_design",
    "generate_architecture_design",
    "is_architecture_approval_request",
    "is_architecture_generation_request",
    "transition_architecture_design",
)
