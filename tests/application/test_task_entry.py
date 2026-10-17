import ast
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from embedded_copilot.application.bindings import CanonicalSupervisorBinding
from embedded_copilot.application.models import (
    ApplicationQuestionRequest,
    ApplicationRoute,
    ApplicationRouteDecision,
)
from embedded_copilot.application.task_entry import BuildBindings, EngineeringTaskEntry
from embedded_copilot.application.task_firmware_build import FirmwareBuildTaskService
from embedded_copilot.correlation import CorrelationContext
from embedded_copilot.engineering_copilot.common import ZERO
from embedded_copilot.engineering_copilot.contracts import (
    EngineeringPlan,
    EngineeringPlanPhase,
    PlanOutput,
    PlanReviewDecision,
    PlanReviewStatus,
    PlanStatus,
)
from embedded_copilot.engineering_task_runtime.build_state import (
    BuildOperationReference,
    BuildReceiptReference,
    FirmwareBuildCoordinationState,
)
from embedded_copilot.execution_worker.contracts import (
    ExecutionProposal,
    OperationStatus,
    OperationStatusView,
    OperationType,
    RecoveryState,
)
from embedded_copilot.execution_worker.service import ExecutionOperationService
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from embedded_copilot.firmware_engineering import (
    FirmwareBuildConfiguration,
    FirmwareBuildRequest,
    FirmwareSourceArtifact,
)
from embedded_copilot.project_catalog.models import (
    ConversationDetail,
    ConversationTurn,
    ConversationTurnStatus,
    ProjectCreationMode,
    ProjectInitializationStatus,
    ProjectType,
    ProjectView,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)
FP = "sha256:" + "1" * 64


class Classifier:
    def classify(self, request):
        return ApplicationRouteDecision.for_request(
            request, route=ApplicationRoute.FIRMWARE
        )


class Catalog:
    def __init__(self):
        self.calls = []
        self.detail = None

    def get_project(self, project_id, *, tenant_id):
        if (project_id, tenant_id) != ("project-1", "tenant-1"):
            raise ValueError("unauthorized")
        return ProjectView(
            project_id=project_id,
            tenant_id=tenant_id,
            name="Project",
            location_id="location-1",
            directory_name="firmware",
            creation_mode=ProjectCreationMode.ATTACH_EXISTING,
            relative_path="firmware",
            project_type=ProjectType.STM32,
            initialization_status=ProjectInitializationStatus.READY,
            created_at=NOW,
            updated_at=NOW,
            last_activity_at=NOW,
        )

    def conversation(self, project_id, conversation_id, *, tenant_id, requester):
        self.calls.append((project_id, conversation_id, tenant_id, requester))
        if (project_id, conversation_id, tenant_id, requester) != (
            "project-1",
            "conversation-1",
            "tenant-1",
            "user-1",
        ) or self.detail is None:
            raise ValueError("missing")
        return self.detail


def question():
    return ApplicationQuestionRequest(
        request_id="request-1",
        trace_id="trace-1",
        project_id="project-1",
        tenant_id="tenant-1",
        requester="user-1",
        question="帮我检查当前固件是否能构建",
        requested_at=NOW,
    )


def source_detail(*, approved=True, build_stage=True, output_name="build"):
    output = PlanOutput.create(
        output_id="output-1",
        phase_id="phase-1",
        plan_id="plan-1",
        project_id="project-1",
        name=output_name,
        output_type="BUILD_ARTIFACT" if build_stage else "DOCUMENT",
    )
    phase = EngineeringPlanPhase.create(
        phase_id="phase-1",
        plan_id="plan-1",
        project_id="project-1",
        name="Firmware",
        description="Build",
        outputs=(output,),
    )
    proposed = EngineeringPlan.create(
        plan_id="plan-1",
        requirement_id="requirement-1",
        project_id="project-1",
        context_fingerprint=FP,
        tasks=(),
        phases=(phase,),
        confidence=0.8,
    )
    plan = EngineeringPlan.model_validate(
        {
            **proposed.model_dump(mode="python"),
            "status": PlanStatus.APPROVED if approved else PlanStatus.PROPOSED,
            "fingerprint": ZERO,
        },
        strict=False,
    )
    review = PlanReviewDecision.create(
        plan_id="plan-1",
        project_id="project-1",
        proposal_fingerprint=plan.content_fingerprint,
        status=PlanReviewStatus.APPROVED if approved else PlanReviewStatus.REJECTED,
        reviewer="user-1",
        decided_at=NOW,
    )
    turn = ConversationTurn(
        turn_id="turn-1",
        request_id="request-1",
        status=ConversationTurnStatus.PENDING,
        question=question().question,
        created_at=NOW,
    )
    return ConversationDetail(
        conversation_id="conversation-1",
        project_id="project-1",
        tenant_id="tenant-1",
        requester="user-1",
        title="Firmware",
        turns=(turn,),
        created_at=NOW,
        updated_at=NOW,
        engineering_plan=plan,
        engineering_plan_review=review if approved else None,
    )


def without_plan():
    return ConversationDetail(
        conversation_id="conversation-1",
        project_id="project-1",
        tenant_id="tenant-1",
        requester="user-1",
        title="Firmware",
        turns=source_detail().turns,
        created_at=NOW,
        updated_at=NOW,
    )


def firmware_request():
    config = FirmwareBuildConfiguration(
        target_kind="STM32F446RE",
        build_system="ARM_NONE_EABI_GCC",
        toolchain_id="ARM_NONE_EABI_GCC",
        toolchain_version="13",
        profile="DEBUG",
        build_directory=".",
        expected_artifact_types=("ELF",),
    )
    return FirmwareBuildRequest.create(
        project_id="project-1",
        firmware_reference="firmware-1",
        build_profile="DEBUG",
        firmware_spec_id="spec-1",
        firmware_spec_fingerprint=FP,
        implementation_id="implementation-1",
        implementation_fingerprint=FP,
        target_kind=config.target_kind,
        build_system=config.build_system,
        toolchain_id=config.toolchain_id,
        toolchain_version=config.toolchain_version,
        configuration=config,
        source_artifacts=(
            FirmwareSourceArtifact(
                relative_path="main.c", content_fingerprint=FP, size_bytes=10,
                source_revision=FP,
            ),
        ),
        expected_source_revision=FP,
        actual_source_revision=FP,
    )


def bindings():
    return BuildBindings(
        target_id="target-1",
        target_fingerprint=FP,
        workspace_snapshot_id="snapshot-1",
        workspace_fingerprint=FP,
        proposal_id="proposal-1",
        proposal_fingerprint=execution_proposal().fingerprint,
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )


def execution_proposal():
    return ExecutionProposal(
        proposal_id="proposal-1",
        tenant_id="tenant-1",
        project_id="project-1",
        requester_id="user-1",
        target_id="target-1",
        target_fingerprint=FP,
        workspace_snapshot_id="snapshot-1",
        workspace_fingerprint=FP,
        approved_workspace_operation_id="workspace-operation-1",
        approved_workspace_receipt_fingerprint=FP,
        operation_type=OperationType.BUILD,
        toolchain_id="ARM_NONE_EABI_GCC",
        toolchain_version="13",
        build_configuration="DEBUG",
        created_at=NOW,
        source_revision=FP,
        firmware_build_request_fingerprint=firmware_request().fingerprint,
    )


def prepare(entry):
    return entry.prepare(
        question(),
        conversation_id="conversation-1",
        action="FIRMWARE_BUILD_CHECK",
        stage_id="phase-1",
        build_request=firmware_request(),
        bindings=bindings(),
    )


class ProjectedBuildService:
    """Trusted projection stub; no Operation or execution is created."""

    def __init__(self, status, *, receipt=False):
        self.status = status
        self.receipt = receipt

    def submit(self, coordination, source, build_request, **_kwargs):
        assert (
            source.resolve_task_request(coordination.task_identity.task_id) is not None
        )
        return FirmwareBuildCoordinationState(
            task_id=coordination.task_identity.task_id,
            tenant_id=coordination.task_identity.tenant_id,
            project_id=coordination.task_identity.project_id,
            plan_reference=coordination.plan_reference,
            stage_reference=coordination.current_stage_reference,
            operation_reference=BuildOperationReference(
                operation_id="operation-1",
                operation_fingerprint=FP,
                build_request_fingerprint=build_request.fingerprint,
            ),
            coordination_status=self.status,
            receipt_reference=BuildReceiptReference(
                operation_id="operation-1",
                receipt_fingerprint=FP,
                evidence_fingerprint=FP,
            )
            if self.receipt
            else None,
        )

    def refresh(self, coordination, source, build_request, *, operation_id):
        assert operation_id == "operation-1"
        return self.submit(coordination, source, build_request)


def test_informational_request_does_not_read_catalog_or_create_task():
    catalog = Catalog()
    entry = EngineeringTaskEntry(
        classifier=Classifier(),
        catalog=catalog,
        build_service=SimpleNamespace(),
        trusted_tenant_id="tenant-1",
        trusted_requester="user-1",
    )
    result = entry.prepare(question(), conversation_id="conversation-1", action=None)
    assert result.status == "INFORMATIONAL"
    assert result.prepared is None
    assert catalog.calls == []


def test_missing_authoritative_source_blocks_task_creation():
    catalog = Catalog()
    entry = EngineeringTaskEntry(
        classifier=Classifier(),
        catalog=catalog,
        build_service=SimpleNamespace(),
        trusted_tenant_id="tenant-1",
        trusted_requester="user-1",
    )
    result = entry.prepare(
        question(), conversation_id="conversation-1", action="FIRMWARE_BUILD_CHECK"
    )
    assert result.status == "SOURCE_UNAVAILABLE"
    assert result.prepared is None
    assert catalog.calls == [("project-1", "conversation-1", "tenant-1", "user-1")]


def test_approved_plan_confirmation_and_existing_service_create_wait_approval(tmp_path):
    catalog = Catalog()
    catalog.detail = source_detail()
    store = SQLiteExecutionStore(tmp_path / "operations.sqlite")
    execution = ExecutionOperationService(store=store)
    execution.create_proposal(execution_proposal())
    entry = EngineeringTaskEntry(
        classifier=Classifier(),
        catalog=catalog,
        build_service=FirmwareBuildTaskService(execution),
        trusted_tenant_id="tenant-1",
        trusted_requester="user-1",
        status_reader=execution,
    )
    ready = prepare(entry)
    assert ready.status == "READY_CONFIRMATION"
    assert ready.prepared.confirmation_binding
    assert (
        entry.submit(ready.prepared, user_confirmed=False).status
        == "CONFIRMATION_REQUIRED"
    )
    assert store.list_operations("tenant-1", "project-1") == ()
    result = entry.submit(ready.prepared, user_confirmed=True)
    assert result.status == "BLOCKED"
    assert "等待审批，尚未执行" in result.message
    assert result.user_projection().receipt_available is False
    assert "sha256:" not in result.user_projection().message
    assert len(store.list_operations("tenant-1", "project-1")) == 1
    status = execution.get_status(
        tenant_id="tenant-1", project_id="project-1", operation_id=result.operation_id
    )
    assert status.status is OperationStatus.WAIT_APPROVAL
    assert (
        execution.get_receipt(
            tenant_id="tenant-1",
            project_id="project-1",
            operation_id=result.operation_id,
        )
        is None
    )
    assert (
        store.list_operations("tenant-1", "project-1")[0].correlation.task_id
        == result.task_id
    )
    assert (
        entry.submit(ready.prepared, user_confirmed=True).operation_id
        == result.operation_id
    )
    assert (
        entry.refresh(ready.prepared, operation_id=result.operation_id).status
        == "BLOCKED"
    )


def test_plan_and_stage_fail_closed():
    catalog = Catalog()
    entry = EngineeringTaskEntry(
        classifier=Classifier(),
        catalog=catalog,
        build_service=SimpleNamespace(),
        trusted_tenant_id="tenant-1",
        trusted_requester="user-1",
    )
    catalog.detail = without_plan()
    assert prepare(entry).status == "WAITING_PLAN"
    catalog.detail = source_detail(approved=False)
    assert prepare(entry).status == "WAITING_PLAN"
    catalog.detail = source_detail(build_stage=False)
    assert prepare(entry).status == "STAGE_REJECTED"
    catalog.detail = source_detail().model_copy(
        update={
            "engineering_plan": source_detail().engineering_plan.model_copy(
                update={"fingerprint": FP}
            )
        }
    )
    assert prepare(entry).status == "SOURCE_UNAVAILABLE"


def test_changed_plan_or_source_blocks_submission(tmp_path):
    catalog = Catalog()
    catalog.detail = source_detail()
    store = SQLiteExecutionStore(tmp_path / "operations.sqlite")
    entry = EngineeringTaskEntry(
        classifier=Classifier(),
        catalog=catalog,
        build_service=FirmwareBuildTaskService(ExecutionOperationService(store=store)),
        trusted_tenant_id="tenant-1",
        trusted_requester="user-1",
    )
    ready = prepare(entry)
    assert ready.status == "READY_CONFIRMATION"
    catalog.detail = source_detail(approved=False)
    assert (
        entry.submit(ready.prepared, user_confirmed=True).status == "SOURCE_UNAVAILABLE"
    )
    assert store.list_operations("tenant-1", "project-1") == ()


def test_real_classifier_accepts_explicit_example_but_not_information_question():
    catalog = Catalog()
    catalog.detail = source_detail()
    entry = EngineeringTaskEntry(
        classifier=CanonicalSupervisorBinding(),
        catalog=catalog,
        build_service=SimpleNamespace(),
        trusted_tenant_id="tenant-1",
        trusted_requester="user-1",
    )
    assert (
        CanonicalSupervisorBinding().route(question()).route
        is ApplicationRoute.CLARIFICATION_REQUIRED
    )
    assert prepare(entry).status == "READY_CONFIRMATION"
    information = ApplicationQuestionRequest(
        request_id="request-2",
        trace_id="trace-2",
        project_id="project-1",
        tenant_id="tenant-1",
        requester="user-1",
        question="固件构建是什么",
        requested_at=NOW,
    )
    assert (
        entry.prepare(
            information, conversation_id="conversation-1", action="FIRMWARE_BUILD_CHECK"
        ).status
        == "CLASSIFICATION_REJECTED"
    )
    assert (
        entry.prepare(information, conversation_id="conversation-1", action=None).status
        == "INFORMATIONAL"
    )


def test_identity_and_forged_confirmation_fail_closed(tmp_path):
    catalog = Catalog()
    catalog.detail = source_detail()
    store = SQLiteExecutionStore(tmp_path / "operations.sqlite")
    entry = EngineeringTaskEntry(
        classifier=Classifier(),
        catalog=catalog,
        build_service=FirmwareBuildTaskService(ExecutionOperationService(store=store)),
        trusted_tenant_id="tenant-1",
        trusted_requester="user-1",
    )
    other = ApplicationQuestionRequest(
        request_id="request-1",
        trace_id="trace-1",
        project_id="project-1",
        tenant_id="other-tenant",
        requester="user-1",
        question=question().question,
        requested_at=NOW,
    )
    assert (
        entry.prepare(
            other, conversation_id="conversation-1", action="FIRMWARE_BUILD_CHECK"
        ).status
        == "IDENTITY_REJECTED"
    )
    ready = prepare(entry)
    assert (
        entry.submit(
            replace(ready.prepared, confirmation_binding="forged"), user_confirmed=True
        ).status
        == "CONFIRMATION_REQUIRED"
    )
    assert (
        entry.submit(ready.prepared, user_confirmed="true").status
        == "CONFIRMATION_REQUIRED"
    )
    assert store.list_operations("tenant-1", "project-1") == ()


def test_source_turn_change_blocks_submit(tmp_path):
    catalog = Catalog()
    catalog.detail = source_detail()
    store = SQLiteExecutionStore(tmp_path / "operations.sqlite")
    entry = EngineeringTaskEntry(
        classifier=Classifier(),
        catalog=catalog,
        build_service=FirmwareBuildTaskService(ExecutionOperationService(store=store)),
        trusted_tenant_id="tenant-1",
        trusted_requester="user-1",
    )
    ready = prepare(entry)
    changed = ConversationTurn(
        turn_id="turn-1",
        request_id="request-1",
        status=ConversationTurnStatus.PENDING,
        question="different",
        created_at=NOW,
    )
    catalog.detail = ConversationDetail(
        conversation_id="conversation-1",
        project_id="project-1",
        tenant_id="tenant-1",
        requester="user-1",
        title="Firmware",
        turns=(changed,),
        created_at=NOW,
        updated_at=NOW,
        engineering_plan=catalog.detail.engineering_plan,
        engineering_plan_review=catalog.detail.engineering_plan_review,
    )
    assert (
        entry.submit(ready.prepared, user_confirmed=True).status == "SOURCE_UNAVAILABLE"
    )
    assert store.list_operations("tenant-1", "project-1") == ()


@pytest.mark.parametrize(
    "change,expected",
    [
        ("requester", "IDENTITY_REJECTED"),
        ("project", "SOURCE_UNAVAILABLE"),
        ("conversation", "SOURCE_UNAVAILABLE"),
        ("turn", "SOURCE_UNAVAILABLE"),
    ],
)
def test_authorization_and_source_rejection(tmp_path, change, expected):
    catalog = Catalog()
    catalog.detail = source_detail()
    store = SQLiteExecutionStore(tmp_path / "operations.sqlite")
    entry = EngineeringTaskEntry(
        classifier=Classifier(),
        catalog=catalog,
        build_service=FirmwareBuildTaskService(ExecutionOperationService(store=store)),
        trusted_tenant_id="tenant-1",
        trusted_requester="user-1",
    )
    request = question()
    conversation_id = "conversation-1"
    if change == "requester":
        request = ApplicationQuestionRequest(
            **{
                **request.model_dump(mode="python", exclude={"fingerprint"}),
                "requester": "other-user",
            }
        )
    elif change == "project":
        request = ApplicationQuestionRequest(
            **{
                **request.model_dump(mode="python", exclude={"fingerprint"}),
                "project_id": "other-project",
            }
        )
    elif change == "conversation":
        conversation_id = "unknown-conversation"
    else:
        catalog.detail = without_plan().model_copy(update={"turns": ()})
    result = entry.prepare(
        request,
        conversation_id=conversation_id,
        action="FIRMWARE_BUILD_CHECK",
        stage_id="phase-1",
        build_request=firmware_request(),
        bindings=bindings(),
    )
    assert result.status == expected
    assert store.list_operations("tenant-1", "project-1") == ()


@pytest.mark.parametrize("missing", ["request", "configuration", "bindings"])
def test_missing_build_inputs_do_not_create_operation(tmp_path, missing):
    catalog = Catalog()
    catalog.detail = source_detail()
    store = SQLiteExecutionStore(tmp_path / "operations.sqlite")
    entry = EngineeringTaskEntry(
        classifier=Classifier(),
        catalog=catalog,
        build_service=FirmwareBuildTaskService(ExecutionOperationService(store=store)),
        trusted_tenant_id="tenant-1",
        trusted_requester="user-1",
    )
    request = firmware_request()
    if missing == "configuration":
        request = request.model_copy(update={"configuration": None})
    result = entry.prepare(
        question(),
        conversation_id="conversation-1",
        action="FIRMWARE_BUILD_CHECK",
        stage_id="phase-1",
        build_request=None if missing == "request" else request,
        bindings=None if missing == "bindings" else bindings(),
    )
    assert result.status == "MISSING_INFORMATION"
    assert store.list_operations("tenant-1", "project-1") == ()


@pytest.mark.parametrize("stage", [None, "unrelated-phase"])
def test_missing_or_unrelated_stage_rejected(tmp_path, stage):
    catalog = Catalog()
    catalog.detail = source_detail()
    store = SQLiteExecutionStore(tmp_path / "operations.sqlite")
    entry = EngineeringTaskEntry(
        classifier=Classifier(),
        catalog=catalog,
        build_service=FirmwareBuildTaskService(ExecutionOperationService(store=store)),
        trusted_tenant_id="tenant-1",
        trusted_requester="user-1",
    )
    result = entry.prepare(
        question(),
        conversation_id="conversation-1",
        action="FIRMWARE_BUILD_CHECK",
        stage_id=stage,
        build_request=firmware_request(),
        bindings=bindings(),
    )
    assert result.status == "STAGE_REJECTED"
    assert store.list_operations("tenant-1", "project-1") == ()


@pytest.mark.parametrize("change", ["plan", "reapproved_plan", "review", "source"])
def test_revalidation_blocks_submit_and_refresh_after_change(tmp_path, change):
    catalog = Catalog()
    catalog.detail = source_detail()
    store = SQLiteExecutionStore(tmp_path / "operations.sqlite")
    ExecutionOperationService(store=store).create_proposal(execution_proposal())
    entry = EngineeringTaskEntry(
        classifier=Classifier(),
        catalog=catalog,
        build_service=FirmwareBuildTaskService(ExecutionOperationService(store=store)),
        trusted_tenant_id="tenant-1",
        trusted_requester="user-1",
    )
    ready = prepare(entry)
    first = entry.submit(ready.prepared, user_confirmed=True)
    assert first.operation_id is not None
    if change == "plan":
        catalog.detail = source_detail(build_stage=False)
    elif change == "reapproved_plan":
        catalog.detail = source_detail(output_name="revised-build")
    elif change == "review":
        catalog.detail = catalog.detail.model_copy(
            update={"engineering_plan_review": None}
        )
    else:
        catalog.detail = without_plan()
    assert (
        entry.submit(ready.prepared, user_confirmed=True).status == "SOURCE_UNAVAILABLE"
    )
    assert (
        entry.refresh(ready.prepared, operation_id=first.operation_id).status
        == "SOURCE_UNAVAILABLE"
    )
    assert len(store.list_operations("tenant-1", "project-1")) == 1


@pytest.mark.parametrize("changed", ["intent", "build_request", "bindings"])
def test_modified_prepared_entry_cannot_be_confirmed(tmp_path, changed):
    catalog = Catalog()
    catalog.detail = source_detail()
    store = SQLiteExecutionStore(tmp_path / "operations.sqlite")
    entry = EngineeringTaskEntry(
        classifier=Classifier(),
        catalog=catalog,
        build_service=FirmwareBuildTaskService(ExecutionOperationService(store=store)),
        trusted_tenant_id="tenant-1",
        trusted_requester="user-1",
    )
    ready = prepare(entry).prepared
    if changed == "intent":
        forged = replace(
            ready, intent=replace(ready.intent, objective="other objective")
        )
    elif changed == "build_request":
        forged = replace(
            ready,
            build_request=firmware_request().model_copy(
                update={"firmware_reference": "other"}
            ),
        )
    else:
        forged = replace(
            ready, bindings=replace(ready.bindings, target_id="other-target")
        )
    assert entry.submit(forged, user_confirmed=True).status == "CONFIRMATION_REQUIRED"
    assert store.list_operations("tenant-1", "project-1") == ()


@pytest.mark.parametrize(
    "status,phrase",
    [("BUILD_PASS", "不代表硬件运行验证"), ("BUILD_FAILED", "失败证据")],
)
def test_user_projection_of_bound_receipt_state_is_sanitized(status, phrase):
    catalog = Catalog()
    catalog.detail = source_detail()
    entry = EngineeringTaskEntry(
        classifier=Classifier(),
        catalog=catalog,
        build_service=ProjectedBuildService(status, receipt=True),
        trusted_tenant_id="tenant-1",
        trusted_requester="user-1",
    )
    projection = entry.submit(
        prepare(entry).prepared, user_confirmed=True
    ).user_projection()
    assert phrase in projection.message
    assert projection.receipt_available and projection.evidence_available
    assert "sha256:" not in projection.message
    assert "Vero" not in projection.message


def test_blocked_projection_without_authoritative_wait_approval_is_generic():
    wrong_identity = OperationStatusView(
        operation_id="another-operation",
        tenant_id="tenant-1",
        project_id="project-1",
        operation_type=OperationType.BUILD,
        status=OperationStatus.WAIT_APPROVAL,
        approval_id=None,
        worker_id=None,
        recovery_state=RecoveryState.NONE,
        cancellation_requested=False,
        updated_at=NOW,
    )
    non_wait = OperationStatusView(
        operation_id="operation-1",
        tenant_id="tenant-1",
        project_id="project-1",
        operation_type=OperationType.BUILD,
        status=OperationStatus.FAILED,
        approval_id=None,
        worker_id=None,
        recovery_state=RecoveryState.NONE,
        cancellation_requested=False,
        updated_at=NOW,
    )
    correlation = CorrelationContext(
        request_id="request-1",
        trace_id="trace-1",
        project_id="project-1",
        task_id="entry-task",
        operation_id="operation-1",
    )
    wrong_type = OperationStatusView(
        operation_id="operation-1",
        tenant_id="tenant-1",
        project_id="project-1",
        operation_type=OperationType.FLASH,
        status=OperationStatus.WAIT_APPROVAL,
        approval_id=None,
        worker_id=None,
        recovery_state=RecoveryState.NONE,
        cancellation_requested=False,
        updated_at=NOW,
        correlation=correlation,
    )
    has_receipt = OperationStatusView(
        operation_id="operation-1",
        tenant_id="tenant-1",
        project_id="project-1",
        operation_type=OperationType.BUILD,
        status=OperationStatus.WAIT_APPROVAL,
        approval_id=None,
        worker_id=None,
        recovery_state=RecoveryState.NONE,
        cancellation_requested=False,
        updated_at=NOW,
        receipt_fingerprint=FP,
        correlation=correlation,
    )
    wrong_task = OperationStatusView(
        operation_id="operation-1",
        tenant_id="tenant-1",
        project_id="project-1",
        operation_type=OperationType.BUILD,
        status=OperationStatus.WAIT_APPROVAL,
        approval_id=None,
        worker_id=None,
        recovery_state=RecoveryState.NONE,
        cancellation_requested=False,
        updated_at=NOW,
        correlation=correlation.model_copy(update={"task_id": "other-task"}),
    )

    def failure(**_kwargs):
        raise RuntimeError("status unavailable")

    for reader in (
        None,
        SimpleNamespace(get_status=lambda **_kwargs: None),
        SimpleNamespace(get_status=lambda **_kwargs: wrong_identity),
        SimpleNamespace(get_status=lambda **_kwargs: non_wait),
        SimpleNamespace(get_status=lambda **_kwargs: wrong_type),
        SimpleNamespace(get_status=lambda **_kwargs: has_receipt),
        SimpleNamespace(get_status=lambda **_kwargs: wrong_task),
        SimpleNamespace(get_status=failure),
    ):
        catalog = Catalog()
        catalog.detail = source_detail()
        entry = EngineeringTaskEntry(
            classifier=Classifier(),
            catalog=catalog,
            build_service=ProjectedBuildService("BLOCKED"),
            trusted_tenant_id="tenant-1",
            trusted_requester="user-1",
            status_reader=reader,
        )
        assert (
            "等待审批"
            not in entry.submit(prepare(entry).prepared, user_confirmed=True).message
        )


@pytest.mark.parametrize("defect", ["malformed", "cross_project", "wrong_build"])
def test_build_service_output_is_revalidated_before_submit_and_refresh(defect):
    class DefectiveBuildService(ProjectedBuildService):
        def submit(self, coordination, source, build_request, **kwargs):
            state = super().submit(coordination, source, build_request, **kwargs)
            if defect == "malformed":
                return SimpleNamespace(coordination_status="BUILD_PASS")
            if defect == "cross_project":
                return state.model_copy(update={"project_id": "other-project"})
            return state.model_copy(
                update={
                    "operation_reference": state.operation_reference.model_copy(
                        update={"build_request_fingerprint": FP}
                    )
                }
            )

    catalog = Catalog()
    catalog.detail = source_detail()
    entry = EngineeringTaskEntry(
        classifier=Classifier(),
        catalog=catalog,
        build_service=DefectiveBuildService("BLOCKED"),
        trusted_tenant_id="tenant-1",
        trusted_requester="user-1",
    )
    prepared = prepare(entry).prepared
    assert entry.submit(prepared, user_confirmed=True).status == "SOURCE_UNAVAILABLE"
    assert (
        entry.refresh(prepared, operation_id="operation-1").status
        == "SOURCE_UNAVAILABLE"
    )


def test_refresh_rejects_state_for_another_operation():
    catalog = Catalog()
    catalog.detail = source_detail()
    entry = EngineeringTaskEntry(
        classifier=Classifier(),
        catalog=catalog,
        build_service=ProjectedBuildService("BLOCKED"),
        trusted_tenant_id="tenant-1",
        trusted_requester="user-1",
    )
    prepared = prepare(entry).prepared
    created = entry.submit(prepared, user_confirmed=True)
    assert created.operation_id == "operation-1"
    assert (
        entry.refresh(prepared, operation_id="other-operation").status
        == "SOURCE_UNAVAILABLE"
    )


def test_entry_has_no_direct_execution_or_approval_calls():
    source = Path("src/embedded_copilot/application/task_entry.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    forbidden = {
        "ExecutionWorker",
        "FirmwareBuildToolAdapter",
        "subprocess",
        "approve_operation",
        "issue_approval",
    }
    assert imported.isdisjoint(forbidden)
    calls = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert calls.isdisjoint(
        {"run_worker", "execute", "compile", "issue_approval", "approve_operation"}
    )
