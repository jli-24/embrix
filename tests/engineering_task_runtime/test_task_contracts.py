from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from embedded_copilot.engineering_copilot.common import ZERO
from embedded_copilot.engineering_copilot.contracts import (
    EngineeringPlan,
    EngineeringPlanPhase,
    PlanReviewDecision,
    PlanReviewStatus,
    PlanStatus,
)
from embedded_copilot.engineering_task_runtime.contracts import (
    CoordinationMetadata,
    EngineeringTaskCoordination,
    PlanReference,
    ProjectReference,
    RequestReference,
    StageReference,
    StageSourceReference,
    TaskIdentity,
    validate_coordination,
)
from embedded_copilot.engineering_task_runtime.exceptions import (
    EngineeringTaskRejected,
    EngineeringTaskUnavailable,
)
from embedded_copilot.engineering_task_runtime.models import EngineeringTaskRequest

NOW = datetime(2026, 9, 28, tzinfo=UTC)


class Source:
    bound_tenant_id = "tenant-1"
    bound_project_id = "project-1"
    bound_principal_id = "engineer-1"

    def __init__(self, request: EngineeringTaskRequest, plan: EngineeringPlan, review: PlanReviewDecision):
        self.request = request
        self.plan = plan
        self.review = review
        self.calls: list[str] = []
        self.operation_count = 0
        self.provider_calls = 0
        self.adapter_calls = 0

    def resolve_task_request(self, task_id: str) -> EngineeringTaskRequest | None:
        self.calls.append("request")
        return self.request if task_id == self.request.task_id else None

    def resolve_plan(self, task_id: str, plan_id: str) -> tuple[EngineeringPlan, PlanReviewDecision] | None:
        self.calls.append("plan")
        return (self.plan, self.review) if task_id == self.request.task_id and plan_id == self.plan.plan_id else None


def fixture() -> tuple[EngineeringTaskCoordination, Source]:
    request = EngineeringTaskRequest(
        task_id="task-1", tenant_id="tenant-1", project_id="project-1",
        objective="Design controller", created_at=NOW,
    )
    phase = EngineeringPlanPhase.create(
        phase_id="phase-1", plan_id="plan-1", project_id="project-1",
        name="Design", description="Design controller",
    )
    proposed = EngineeringPlan.create(
        plan_id="plan-1", requirement_id="requirement-1", project_id="project-1",
        context_fingerprint="sha256:" + "a" * 64, tasks=(), phases=(phase,), confidence=0.8,
    )
    data = proposed.model_dump(mode="python")
    data.update(status=PlanStatus.APPROVED, fingerprint=ZERO)
    approved = EngineeringPlan.model_validate(data, strict=False)
    review = PlanReviewDecision.create(
        plan_id="plan-1", project_id="project-1", proposal_fingerprint=approved.content_fingerprint,
        status=PlanReviewStatus.APPROVED, reviewer="engineer-1", decided_at=NOW,
    )
    coordination = EngineeringTaskCoordination(
        task_identity=TaskIdentity(task_id=request.task_id, tenant_id=request.tenant_id, project_id=request.project_id),
        request_reference=RequestReference(task_id=request.task_id, request_fingerprint=request.fingerprint),
        project_reference=ProjectReference(tenant_id=request.tenant_id, project_id=request.project_id),
        plan_reference=PlanReference(plan_id=approved.plan_id, content_fingerprint=approved.content_fingerprint, review_fingerprint=review.fingerprint),
        current_stage_reference=StageReference(
            stage_id=phase.phase_id,
            plan_reference=PlanReference(plan_id=approved.plan_id, content_fingerprint=approved.content_fingerprint, review_fingerprint=review.fingerprint),
            source_reference=StageSourceReference(source_type="APPROVED_PLAN_PHASE", phase_id=phase.phase_id, phase_fingerprint=phase.fingerprint),
        ),
        coordination_metadata=CoordinationMetadata(reference_id="coord-1"),
    )
    return coordination, Source(request, approved, review)


def changed(value: EngineeringTaskCoordination, **changes: object) -> EngineeringTaskCoordination:
    data = value.model_dump(mode="python")
    data.update(changes)
    data["fingerprint"] = ZERO
    return EngineeringTaskCoordination.model_validate(data)


def test_valid_chain_is_read_only_and_compatible() -> None:
    value, source = fixture()
    assert validate_coordination(value, source) == value
    assert source.calls == ["request", "plan"]
    assert source.operation_count == source.provider_calls == source.adapter_calls == 0
    assert "objective" not in value.model_dump_json()
    assert "EngineeringPlanStep" not in value.model_dump_json()
    assert source.request.task_id == value.task_identity.task_id
    assert source.request.fingerprint == value.request_reference.request_fingerprint
    assert source.plan.content_fingerprint == value.plan_reference.content_fingerprint
    assert source.plan.phases[0].fingerprint == value.current_stage_reference.source_reference.phase_fingerprint


@pytest.mark.parametrize("field,changed_value", [
    ("task_identity", {"task_id": "task-2", "tenant_id": "tenant-1", "project_id": "project-1"}),
    ("task_identity", {"task_id": "task-1", "tenant_id": "tenant-2", "project_id": "project-1"}),
    ("task_identity", {"task_id": "task-1", "tenant_id": "tenant-1", "project_id": "project-2"}),
    ("project_reference", {"tenant_id": "tenant-1", "project_id": "project-2"}),
    ("request_reference", {"task_id": "task-1", "request_fingerprint": "sha256:" + "b" * 64}),
    ("plan_reference", {"plan_id": "plan-2", "content_fingerprint": "sha256:" + "b" * 64, "review_fingerprint": "sha256:" + "c" * 64}),
])
def test_client_recomputed_fingerprint_cannot_replace_authority(field: str, changed_value: dict[str, str]) -> None:
    value, source = fixture()
    updates: dict[str, object] = {field: changed_value}
    if field == "task_identity":
        updates["request_reference"] = {
            "task_id": changed_value["task_id"],
            "request_fingerprint": value.request_reference.request_fingerprint,
        }
        updates["project_reference"] = {
            "tenant_id": changed_value["tenant_id"],
            "project_id": changed_value["project_id"],
        }
    elif field == "plan_reference":
        stage = value.current_stage_reference.model_dump(mode="python")
        stage.update(plan_reference=changed_value, fingerprint=ZERO)
        updates["current_stage_reference"] = stage
    if field == "project_reference":
        with pytest.raises(ValidationError):
            changed(value, **updates)
    else:
        with pytest.raises(EngineeringTaskRejected):
            validate_coordination(changed(value, **updates), source)
    assert source.operation_count == source.provider_calls == source.adapter_calls == 0


def test_source_identity_cannot_be_selected_by_client() -> None:
    value, source = fixture()
    source.bound_tenant_id = "tenant-2"
    with pytest.raises(EngineeringTaskRejected, match="coordination reference rejected"):
        validate_coordination(value, source)
    assert source.calls == []


@pytest.mark.parametrize("mutation", ["missing_request", "missing_plan", "unapproved_plan", "rejected_review", "wrong_review_fingerprint", "wrong_project", "tampered_request"])
def test_source_missing_or_invalid_fails_closed(mutation: str) -> None:
    value, source = fixture()
    if mutation == "missing_request":
        source.resolve_task_request = lambda task_id: None
    elif mutation == "missing_plan":
        source.resolve_plan = lambda task_id, plan_id: None
    elif mutation == "unapproved_plan":
        data = source.plan.model_dump(mode="python")
        data.update(status=PlanStatus.PROPOSED, fingerprint=ZERO)
        source.plan = EngineeringPlan.model_validate(data, strict=False)
    elif mutation == "rejected_review":
        data = source.review.model_dump(mode="python")
        data.update(status=PlanReviewStatus.REJECTED, fingerprint=ZERO)
        source.review = PlanReviewDecision.model_validate(data, strict=False)
    elif mutation == "wrong_review_fingerprint":
        data = source.review.model_dump(mode="python")
        data.update(proposal_fingerprint="sha256:" + "b" * 64, fingerprint=ZERO)
        source.review = PlanReviewDecision.model_validate(data, strict=False)
    elif mutation == "wrong_project":
        source.plan = source.plan.model_copy(update={"project_id": "project-2"})
    else:
        source.request = source.request.model_copy(update={"objective": "tampered"})
    if mutation in {"rejected_review", "wrong_review_fingerprint"}:
        assert source.review.fingerprint != value.plan_reference.review_fingerprint
        plan_ref = value.plan_reference.model_dump(mode="python")
        plan_ref.update(review_fingerprint=source.review.fingerprint, fingerprint=ZERO)
        stage = value.current_stage_reference.model_dump(mode="python")
        stage.update(plan_reference=plan_ref, fingerprint=ZERO)
        value = changed(value, plan_reference=plan_ref, current_stage_reference=stage)
        assert value.plan_reference.review_fingerprint == source.review.fingerprint
    with pytest.raises(EngineeringTaskRejected):
        validate_coordination(value, source)


def test_same_plan_with_synchronized_wrong_content_fingerprint_is_rejected() -> None:
    value, source = fixture()
    plan_ref = value.plan_reference.model_dump(mode="python")
    plan_ref.update(content_fingerprint="sha256:" + "b" * 64, fingerprint=ZERO)
    stage = value.current_stage_reference.model_dump(mode="python")
    stage.update(plan_reference=plan_ref, fingerprint=ZERO)
    forged = changed(value, plan_reference=plan_ref, current_stage_reference=stage)
    assert forged.plan_reference.plan_id == source.plan.plan_id
    assert source.resolve_plan(value.task_identity.task_id, source.plan.plan_id) is not None
    with pytest.raises(EngineeringTaskRejected):
        validate_coordination(forged, source)


@pytest.mark.parametrize("stage_id,phase_id,phase_fp", [
    ("phase-other", "phase-other", "sha256:" + "b" * 64),
    ("unknown", "unknown", "sha256:" + "b" * 64),
    ("phase-1", "phase-1", "sha256:" + "b" * 64),
])
def test_unknown_or_foreign_stage_rejected(stage_id: str, phase_id: str, phase_fp: str) -> None:
    value, source = fixture()
    data = value.current_stage_reference.model_dump(mode="python")
    data.update(stage_id=stage_id, source_reference={"source_type": "APPROVED_PLAN_PHASE", "phase_id": phase_id, "phase_fingerprint": phase_fp}, fingerprint=ZERO)
    with pytest.raises(EngineeringTaskRejected):
        validate_coordination(changed(value, current_stage_reference=data), source)


def test_phase_from_another_valid_plan_is_rejected() -> None:
    value, source = fixture()
    other_phase = EngineeringPlanPhase.create(
        phase_id="phase-2", plan_id="plan-2", project_id="project-1",
        name="Other design", description="Other controller",
    )
    other_plan = EngineeringPlan.create(
        plan_id="plan-2", requirement_id="requirement-2", project_id="project-1",
        context_fingerprint="sha256:" + "a" * 64,
        tasks=(), phases=(other_phase,), confidence=0.8,
    )
    assert other_plan.phases[0].fingerprint == other_phase.fingerprint
    assert source.plan.phases[0].phase_id != other_phase.phase_id
    stage = value.current_stage_reference.model_dump(mode="python")
    stage.update(
        stage_id=other_phase.phase_id,
        source_reference={
            "source_type": "APPROVED_PLAN_PHASE",
            "phase_id": other_phase.phase_id,
            "phase_fingerprint": other_phase.fingerprint,
        },
        fingerprint=ZERO,
    )
    with pytest.raises(EngineeringTaskRejected):
        validate_coordination(changed(value, current_stage_reference=stage), source)


def test_nested_model_copy_is_revalidated() -> None:
    value, source = fixture()
    forged = value.model_copy(update={"plan_reference": value.plan_reference.model_copy(update={"plan_id": "plan-other"})})
    with pytest.raises(EngineeringTaskRejected):
        validate_coordination(forged, source)


def test_source_exception_propagates_without_fallback() -> None:
    value, source = fixture()
    def fail(task_id: str) -> None:
        raise RuntimeError("secret C:\\sensitive\\path")
    source.resolve_task_request = fail
    with pytest.raises(EngineeringTaskUnavailable) as error:
        validate_coordination(value, source)
    assert "secret" not in str(error.value)
    assert "sensitive" not in str(error.value)
    assert source.calls == []


def test_missing_plan_identity_is_invalid() -> None:
    value, _ = fixture()
    data = value.plan_reference.model_dump(mode="python")
    data.update(plan_id="", fingerprint=ZERO)
    with pytest.raises(ValidationError):
        PlanReference.model_validate(data)


def test_contract_does_not_enter_operation_worker_model_or_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    from embedded_copilot.execution_worker.adapters import BuildToolAdapter
    from embedded_copilot.execution_worker.service import ExecutionOperationService
    from embedded_copilot.execution_worker.worker import ExecutionWorker
    from embedded_copilot.model_gateway.service import ModelGatewayService

    calls: list[str] = []
    def forbidden(name: str):
        def call(*args: object, **kwargs: object) -> None:
            calls.append(name)
            raise AssertionError(name)
        return call

    monkeypatch.setattr(ExecutionOperationService, "create_operation", forbidden("operation"))
    monkeypatch.setattr(ExecutionWorker, "run_once", forbidden("worker"))
    monkeypatch.setattr(ModelGatewayService, "generate", forbidden("provider"))
    monkeypatch.setattr(BuildToolAdapter, "execute", forbidden("adapter"))
    value, source = fixture()
    assert validate_coordination(value, source) == value
    assert calls == []
