from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.model_gateway import ModelPolicy, ModelRequest

from .common import validate_copy
from .contracts import (
    EngineeringPlan,
    EngineeringPlanNote,
    EngineeringRequirement,
    EngineeringTask,
    PlanReviewDecision,
    PlanReviewStatus,
    PlanStatus,
)


_PLAN_TRANSITIONS: dict[PlanStatus, frozenset[PlanStatus]] = {
    PlanStatus.DRAFT: frozenset({PlanStatus.GENERATING, PlanStatus.CANCELLED, PlanStatus.SUPERSEDED}),
    PlanStatus.GENERATING: frozenset({PlanStatus.PROPOSED, PlanStatus.REJECTED, PlanStatus.CANCELLED, PlanStatus.SUPERSEDED}),
    PlanStatus.PROPOSED: frozenset({PlanStatus.REVIEWING, PlanStatus.REJECTED, PlanStatus.CANCELLED, PlanStatus.SUPERSEDED}),
    PlanStatus.REVIEWING: frozenset({PlanStatus.APPROVED, PlanStatus.REJECTED, PlanStatus.CANCELLED, PlanStatus.SUPERSEDED}),
    PlanStatus.APPROVED: frozenset({PlanStatus.EXECUTING, PlanStatus.CANCELLED, PlanStatus.SUPERSEDED}),
    PlanStatus.EXECUTING: frozenset({PlanStatus.COMPLETED, PlanStatus.CANCELLED, PlanStatus.SUPERSEDED}),
    PlanStatus.COMPLETED: frozenset(),
    PlanStatus.REJECTED: frozenset(),
    PlanStatus.CANCELLED: frozenset(),
    PlanStatus.SUPERSEDED: frozenset(),
}


def _rebuild_plan(plan: EngineeringPlan, **updates: object) -> EngineeringPlan:
    values = plan.model_dump(mode="python")
    values.update(updates)
    values["fingerprint"] = "sha256:" + "0" * 64
    return EngineeringPlan.model_validate(values, strict=False)


def transition_engineering_plan(
    plan: EngineeringPlan, target: PlanStatus
) -> EngineeringPlan:
    """Apply only the explicit plan lifecycle; this never executes a plan."""

    checked = EngineeringPlan.model_validate(plan.model_dump(mode="python"), strict=False)
    if not isinstance(target, PlanStatus):
        raise TypeError("plan status is invalid")
    if target not in _PLAN_TRANSITIONS[checked.status]:
        raise ValueError("invalid engineering plan status transition")
    return _rebuild_plan(checked, status=target)


def approve_engineering_plan(
    plan: EngineeringPlan,
    review: PlanReviewDecision | None = None,
    *,
    reviewer: str | None = None,
    decided_at: datetime | None = None,
) -> tuple[EngineeringPlan, PlanReviewDecision]:
    """Approve the current proposal after binding the decision to its content."""

    checked = EngineeringPlan.model_validate(plan.model_dump(mode="python"), strict=False)
    if checked.status not in {PlanStatus.PROPOSED, PlanStatus.REVIEWING}:
        raise ValueError("engineering plan is not awaiting review")
    reviewing = (
        checked
        if checked.status is PlanStatus.REVIEWING
        else transition_engineering_plan(checked, PlanStatus.REVIEWING)
    )
    if review is not None:
        checked_review = PlanReviewDecision.model_validate(
            review.model_dump(mode="python"), strict=False
        )
        if (
            checked_review.plan_id != reviewing.plan_id
            or checked_review.project_id != reviewing.project_id
            or checked_review.proposal_fingerprint != reviewing.content_fingerprint
            or checked_review.status is not PlanReviewStatus.APPROVED
        ):
            raise ValueError("engineering plan review binding rejected")
        if reviewer is not None and reviewer != checked_review.reviewer:
            raise ValueError("engineering plan reviewer mismatch")
        if decided_at is not None and decided_at != checked_review.decided_at:
            raise ValueError("engineering plan decision time mismatch")
        decision = checked_review
    else:
        if reviewer is None:
            raise TypeError("reviewer is required")
        decision = PlanReviewDecision.create(
            plan_id=reviewing.plan_id,
            project_id=reviewing.project_id,
            proposal_fingerprint=reviewing.content_fingerprint,
            status=PlanReviewStatus.APPROVED,
            reviewer=reviewer,
            decided_at=decided_at or datetime.now(UTC),
        )
    return _rebuild_plan(reviewing, status=PlanStatus.APPROVED), decision


class EngineeringPlannerService:
    def __init__(self, model_gateway=None):
        self._model_gateway = model_gateway

    def plan(
        self, requirement: EngineeringRequirement, context: EngineeringContextSnapshot
    ) -> EngineeringPlan:
        req = validate_copy(requirement, EngineeringRequirement)
        ctx = validate_copy(context, EngineeringContextSnapshot)
        if (
            req.project_id != ctx.project_id
            or ctx.verification_status.value == "REQUIRES_REVIEW"
        ):
            raise ValueError("context binding rejected")
        available = {
            e.evidence_id
            for e in (
                *ctx.memory_evidence,
                *ctx.graph_evidence,
                *ctx.knowledge_evidence,
                *ctx.multimodal_evidence,
            )
        }
        if any(ref not in available for ref in req.evidence_refs):
            raise ValueError("evidence reference rejected")
        seed = hashlib.sha256(req.requirement_id.encode()).hexdigest()[:12]
        analysis = EngineeringTask.create(
            task_id=f"task-{seed}-analysis",
            sequence=1,
            title="分析工程需求",
            description=(
                req.description
                + ("；约束：" + "；".join(req.constraints) if req.constraints else "")
            ),
            depends_on=(),
            risk="证据或约束可能不完整",
            evidence_refs=req.evidence_refs,
        )
        design = EngineeringTask.create(
            task_id=f"task-{seed}-design",
            sequence=2,
            title="生成工程 Proposal",
            description="依据已验证 Evidence 生成只读工程 Proposal",
            depends_on=(analysis.task_id,),
            risk="Proposal 不等于已批准实现",
            evidence_refs=req.evidence_refs,
        )
        verification = EngineeringTask.create(
            task_id=f"task-{seed}-verification",
            sequence=3,
            title="人工审核与验证",
            description="核验 Proposal、风险、Evidence 和执行审批边界",
            depends_on=(design.task_id,),
            risk="未经审核不得执行或提升为工程事实",
            evidence_refs=req.evidence_refs,
        )
        notes: tuple[EngineeringPlanNote, ...] = ()
        if self._model_gateway is not None:
            try:
                # Optional notes are deliberately isolated from structural fields.
                request = ModelRequest(
                    request_id=f"note-{req.requirement_id}",
                    task=req.description,
                    engineering_context=ctx,
                    system_instruction="仅生成非权威说明；不得新增事实、任务或执行建议。",
                    constraints=req.constraints,
                    model_policy=ModelPolicy(
                        primary_provider="local", model_name="offline", temperature=0.0
                    ),
                )
                response = self._model_gateway.generate(request)
                if getattr(response, "content", None):
                    notes = (EngineeringPlanNote.create(note=response.content[:2048]),)
            except Exception:  # noqa: BLE001 - optional notes are fail-open
                notes = ()
        return EngineeringPlan.create(
            plan_id=f"plan-{seed}",
            requirement_id=req.requirement_id,
            project_id=req.project_id,
            context_fingerprint=ctx.fingerprint,
            tasks=(analysis, design, verification),
            risks=(
                "需要工程师审核并验证",
                *(f"约束：{constraint}" for constraint in req.constraints),
            ),
            evidence_refs=req.evidence_refs,
            notes=notes,
            confidence=float(ctx.confidence),
        )


__all__ = (
    "EngineeringPlannerService",
    "approve_engineering_plan",
    "transition_engineering_plan",
)
