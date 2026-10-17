from __future__ import annotations

from types import SimpleNamespace

from embedded_copilot.application import (
    ApplicationDependencyContainer,
    ApplicationRoute,
    ApplicationRoutingContext,
    ApplicationRunStatus,
    ApplicationRuntime,
    CanonicalSupervisorBinding,
    FakeApplicationAuditSink,
    FakeApplicationContext,
    FakeApplicationModelGateway,
    FixedApplicationClock,
    ModelPolicy,
)
from embedded_copilot.engineering_copilot import PlanStatus
from embedded_copilot.engineering_requirements import merge_requirement_context

from .fixtures import NOW, request, snapshot


def _confirmed_requirement():
    partial = merge_requirement_context(
        None,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        utterance="我要做一个 STM32 小车，巡线，电池供电，直流电机",
    )
    return merge_requirement_context(
        partial,
        project_id="project-1",
        conversation_id="conversation-1",
        turn_id="turn-2",
        utterance="开始设计",
        confirmation_requested=True,
    )


def _runtime(question_text: str, *, failure: Exception | None = None) -> ApplicationRuntime:
    project = SimpleNamespace(creation_mode=SimpleNamespace(value="CREATE_NEW"))
    return ApplicationRuntime(
        ApplicationDependencyContainer(
            agent_runtime=CanonicalSupervisorBinding(
                project_mode=lambda _project_id, _tenant_id: project
            ),
            context_provider=FakeApplicationContext(snapshot(task=question_text)),
            model_gateway=FakeApplicationModelGateway(
                content="plan response", failure=failure
            ),
            audit_sink=FakeApplicationAuditSink(),
            clock=FixedApplicationClock(NOW),
            model_policy=ModelPolicy(primary_provider="fake", model_name="fake-model"),
        )
    )


def test_confirmed_requirement_generates_proposed_plan_then_explicit_start_approves() -> None:
    runtime = _runtime("开始设计")
    requirement = _confirmed_requirement()
    first = runtime.answer(
        request(question="开始设计", request_id="plan-request-1"),
        routing_context=ApplicationRoutingContext(
            project_creation_mode="CREATE_NEW",
            conversation_id=requirement.conversation_id,
            requirement_context=requirement,
        ),
    )
    assert first.status is ApplicationRunStatus.COMPLETED
    assert first.route is ApplicationRoute.GREENFIELD_DISCOVERY
    assert first.engineering_plan is not None
    assert first.engineering_plan.status is PlanStatus.PROPOSED
    assert first.answer is not None
    assert "工程计划摘要" in first.answer.content
    assert "批准计划" in first.answer.content

    second = _runtime("开始").answer(
        request(question="开始", request_id="plan-request-2"),
        routing_context=ApplicationRoutingContext(
            project_creation_mode="CREATE_NEW",
            conversation_id=requirement.conversation_id,
            requirement_context=requirement,
            engineering_plan=first.engineering_plan,
        ),
    )
    assert second.status is ApplicationRunStatus.COMPLETED
    assert second.route is ApplicationRoute.GREENFIELD_DESIGN
    assert second.engineering_plan is not None
    assert second.engineering_plan.status is PlanStatus.APPROVED
    assert second.engineering_plan_review is not None


def test_design_request_without_plan_approval_stays_in_discovery() -> None:
    runtime = _runtime("进入架构设计")
    requirement = _confirmed_requirement()
    result = runtime.answer(
        request(question="进入架构设计", request_id="plan-request-3"),
        routing_context=ApplicationRoutingContext(
            project_creation_mode="CREATE_NEW",
            conversation_id=requirement.conversation_id,
            requirement_context=requirement,
        ),
    )
    assert result.route is ApplicationRoute.GREENFIELD_DISCOVERY
    assert result.engineering_plan is not None
    assert result.engineering_plan.status is PlanStatus.PROPOSED


def test_provider_unavailable_keeps_the_proposed_plan_projection() -> None:
    runtime = _runtime("开始设计", failure=RuntimeError("provider unavailable"))
    requirement = _confirmed_requirement()

    result = runtime.answer(
        request(question="开始设计", request_id="plan-request-4"),
        routing_context=ApplicationRoutingContext(
            project_creation_mode="CREATE_NEW",
            conversation_id=requirement.conversation_id,
            requirement_context=requirement,
        ),
    )

    assert result.status is ApplicationRunStatus.FAILED
    assert result.engineering_plan is not None
    assert result.engineering_plan.status is PlanStatus.PROPOSED
    assert result.requirement_context is not None
