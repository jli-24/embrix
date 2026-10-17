from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest
from pydantic import ValidationError

from embedded_copilot.application.task_firmware_build import FirmwareBuildTaskService
from embedded_copilot.engineering_copilot.common import ZERO
from embedded_copilot.engineering_copilot.contracts import (
    EngineeringPlan,
    EngineeringPlanPhase,
    PlanOutput,
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
)
from embedded_copilot.engineering_task_runtime.exceptions import EngineeringTaskRejected
from embedded_copilot.engineering_task_runtime.models import EngineeringTaskRequest
from embedded_copilot.execution_worker.adapters import FirmwareBuildToolAdapter
from embedded_copilot.execution_worker.artifacts import ArtifactStore
from embedded_copilot.execution_worker.contracts import (
    ExecutionProposal,
    ExecutionTarget,
    OperationStatus,
    OperationType,
    TargetBindingStatus,
)
from embedded_copilot.execution_worker.registry import (
    ExecutionToolRegistry,
    ToolResolution,
)
from embedded_copilot.execution_worker.service import (
    ExecutionOperationService,
    ExecutionRejected,
)
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from embedded_copilot.execution_worker.worker import ExecutionWorker
from embedded_copilot.firmware_engineering import (
    FirmwareBuildConfiguration,
    FirmwareBuildRequest,
    FirmwareSourceArtifact,
)

FP = "sha256:" + "1" * 64
NOW = datetime.now(UTC)


class Source:
    bound_tenant_id = "tenant-1"
    bound_project_id = "project-1"
    bound_principal_id = "requester-1"

    def __init__(self, request, plan, review):
        self.request, self.plan, self.review = request, plan, review

    def resolve_task_request(self, task_id):
        return self.request if task_id == self.request.task_id else None

    def resolve_plan(self, task_id, plan_id):
        return (self.plan, self.review) if task_id == self.request.task_id and plan_id == self.plan.plan_id else None


def fixture(*, output_type: str = "BUILD_ARTIFACT", extra_stage: bool = False):
    request = EngineeringTaskRequest(
        task_id="task-1", tenant_id="tenant-1", project_id="project-1",
        objective="Build firmware", created_at=NOW,
    )
    output = PlanOutput.create(
        output_id="output-1", phase_id="phase-1", plan_id="plan-1",
        project_id="project-1", name="Firmware build", output_type=output_type,
    )
    phase = EngineeringPlanPhase.create(
        phase_id="phase-1", plan_id="plan-1", project_id="project-1",
        name="Firmware development", description="Build firmware", outputs=(output,),
    )
    phases = (phase,)
    if extra_stage:
        second_output = PlanOutput.create(
            output_id="output-2", phase_id="phase-2", plan_id="plan-1",
            project_id="project-1", name="Second firmware build", output_type="BUILD_ARTIFACT",
        )
        phases += (EngineeringPlanPhase.create(
            phase_id="phase-2", plan_id="plan-1", project_id="project-1",
            name="Second firmware build", description="Second build", outputs=(second_output,),
        ),)
    proposed = EngineeringPlan.create(
        plan_id="plan-1", requirement_id="requirement-1", project_id="project-1",
        context_fingerprint=FP, tasks=(), phases=phases, confidence=0.8,
    )
    data = proposed.model_dump(mode="python")
    data.update(status=PlanStatus.APPROVED, fingerprint=ZERO)
    plan = EngineeringPlan.model_validate(data, strict=False)
    review = PlanReviewDecision.create(
        plan_id="plan-1", project_id="project-1",
        proposal_fingerprint=plan.content_fingerprint,
        status=PlanReviewStatus.APPROVED, reviewer="requester-1", decided_at=NOW,
    )
    plan_ref = PlanReference(
        plan_id=plan.plan_id, content_fingerprint=plan.content_fingerprint,
        review_fingerprint=review.fingerprint,
    )
    coordination = EngineeringTaskCoordination(
        task_identity=TaskIdentity(task_id=request.task_id, tenant_id=request.tenant_id, project_id=request.project_id),
        request_reference=RequestReference(task_id=request.task_id, request_fingerprint=request.fingerprint),
        project_reference=ProjectReference(tenant_id=request.tenant_id, project_id=request.project_id),
        plan_reference=plan_ref,
        current_stage_reference=StageReference(
            stage_id=phase.phase_id, plan_reference=plan_ref,
            source_reference=StageSourceReference(
                source_type="APPROVED_PLAN_PHASE", phase_id=phase.phase_id,
                phase_fingerprint=phase.fingerprint,
            ),
        ),
        coordination_metadata=CoordinationMetadata(reference_id="coord-1"),
    )
    return coordination, Source(request, plan, review)


def build_request(*, cmake: bool = False, source_digest: str = FP, source_size: int = 10) -> FirmwareBuildRequest:
    configuration = FirmwareBuildConfiguration(
        target_kind="STM32F103C8T6" if cmake else "STM32F446RE",
        build_system="CMAKE" if cmake else "ARM_NONE_EABI_GCC",
        toolchain_id="ARM_NONE_EABI_GCC", toolchain_version="15.3.1" if cmake else "13", profile="DEBUG",
        build_directory="build" if cmake else ".",
        expected_artifact_types=("ELF",),
    )
    return FirmwareBuildRequest.create(
        project_id="project-1", firmware_reference="firmware-1", build_profile="DEBUG",
        firmware_spec_id="spec-1", firmware_spec_fingerprint=FP,
        implementation_id="implementation-1", implementation_fingerprint=FP,
        target_kind=configuration.target_kind, build_system=configuration.build_system,
        toolchain_id="ARM_NONE_EABI_GCC", toolchain_version=configuration.toolchain_version,
        configuration=configuration,
        source_artifacts=(FirmwareSourceArtifact(relative_path="main.c", content_fingerprint=source_digest, size_bytes=source_size, source_revision=FP),),
        expected_source_revision=FP, actual_source_revision=FP,
    )


def proposal(execution: ExecutionOperationService, request: FirmwareBuildRequest, *, target_fingerprint: str = FP) -> ExecutionProposal:
    return execution.create_proposal(ExecutionProposal(
        proposal_id="proposal-1", tenant_id="tenant-1", project_id="project-1",
        requester_id="requester-1", target_id="target-1",
        target_fingerprint=target_fingerprint,
        workspace_snapshot_id="snapshot-1", workspace_fingerprint=FP,
        approved_workspace_operation_id="workspace-operation-1",
        approved_workspace_receipt_fingerprint=FP,
        operation_type=OperationType.BUILD, toolchain_id=request.toolchain_id,
        toolchain_version=request.toolchain_version,
        build_configuration=request.configuration.profile, created_at=NOW,
        source_revision=FP, firmware_build_request_fingerprint=request.fingerprint,
    ))


def submit(service: FirmwareBuildTaskService, *, target_id: str = "target-1", request=None, target_fingerprint: str = FP, snapshot_id: str = "snapshot-1"):
    coordination, source = fixture()
    checked_request = request or build_request()
    bound = proposal(service._execution, build_request())
    return service.submit(
        coordination, source, checked_request, requester_id="requester-1",
        target_id=target_id, target_fingerprint=target_fingerprint,
        workspace_snapshot_id=snapshot_id, workspace_fingerprint=FP,
        proposal_id=bound.proposal_id, proposal_fingerprint=bound.fingerprint,
        expires_at=NOW + timedelta(minutes=30),
    )


def test_same_intent_reuses_persisted_operation_and_changed_intent_conflicts(tmp_path) -> None:
    path = tmp_path / "operations.sqlite"
    first = submit(FirmwareBuildTaskService(ExecutionOperationService(store=SQLiteExecutionStore(path))))
    second = submit(FirmwareBuildTaskService(ExecutionOperationService(store=SQLiteExecutionStore(path))))
    assert second.operation_reference == first.operation_reference
    assert second.coordination_status == "BLOCKED"
    with pytest.raises(Exception, match="conflict"):
        submit(FirmwareBuildTaskService(ExecutionOperationService(store=SQLiteExecutionStore(path))), target_id="target-2")


def test_refresh_rejects_other_approved_build_stage_in_same_plan(tmp_path) -> None:
    service = FirmwareBuildTaskService(ExecutionOperationService(
        store=SQLiteExecutionStore(tmp_path / "operations.sqlite")
    ))
    coordination, source = fixture(extra_stage=True)
    request = build_request()
    bound = proposal(service._execution, request)
    state = service.submit(
        coordination, source, request, requester_id="requester-1",
        target_id="target-1", target_fingerprint=FP,
        workspace_snapshot_id="snapshot-1", workspace_fingerprint=FP,
        proposal_id=bound.proposal_id, proposal_fingerprint=bound.fingerprint,
        expires_at=NOW + timedelta(minutes=30),
    )
    other = source.plan.phases[1]
    other_stage = StageReference(
        stage_id=other.phase_id, plan_reference=coordination.plan_reference,
        source_reference=StageSourceReference(
            source_type="APPROVED_PLAN_PHASE", phase_id=other.phase_id,
            phase_fingerprint=other.fingerprint,
        ),
    )
    second_coordination = EngineeringTaskCoordination.model_validate({
        **coordination.model_dump(mode="python"),
        "current_stage_reference": other_stage,
        "fingerprint": ZERO,
    })
    with pytest.raises(EngineeringTaskRejected, match="binding"):
        service.refresh(
            second_coordination, source, request,
            operation_id=state.operation_reference.operation_id,
        )


def test_projection_rejects_cross_bound_references(tmp_path) -> None:
    state = submit(FirmwareBuildTaskService(ExecutionOperationService(
        store=SQLiteExecutionStore(tmp_path / "operations.sqlite")
    )))
    bad_stage = state.stage_reference.model_copy(update={"plan_reference": state.plan_reference.model_copy(update={"plan_id": "other"})})
    with pytest.raises(ValidationError):
        type(state).model_validate({**state.model_dump(mode="python"), "stage_reference": bad_stage, "fingerprint": ZERO})
    with pytest.raises(ValidationError):
        type(state).model_validate({**state.model_dump(mode="python"), "coordination_status": "BUILD_PASS", "fingerprint": ZERO})


@pytest.mark.parametrize("bad_field", ["project", "plan", "stage"])
def test_service_rejects_invalid_bindings_before_operation(tmp_path, bad_field) -> None:
    store = SQLiteExecutionStore(tmp_path / "operations.sqlite")
    service = FirmwareBuildTaskService(ExecutionOperationService(store=store))
    coordination, source = fixture()
    request = build_request()
    if bad_field == "project":
        request = request.model_copy(update={"project_id": "other"})
    elif bad_field == "plan":
        coordination = coordination.model_copy(update={
            "plan_reference": coordination.plan_reference.model_copy(update={"content_fingerprint": FP})
        })
    else:
        coordination = coordination.model_copy(update={
            "current_stage_reference": coordination.current_stage_reference.model_copy(update={
                "stage_id": "other"
            })
        })
    with pytest.raises((EngineeringTaskRejected, ValidationError)):
        service.submit(
            coordination, source, request, requester_id="requester-1",
            target_id="target-1", target_fingerprint=FP,
            workspace_snapshot_id="snapshot-1", workspace_fingerprint=FP,
            proposal_id="proposal-1", proposal_fingerprint=FP,
            expires_at=NOW + timedelta(minutes=30),
        )
    assert store.list_operations("tenant-1", "project-1") == ()


def test_task_service_does_not_call_worker_adapter_or_provider(tmp_path, monkeypatch) -> None:
    from embedded_copilot.model_gateway.service import ModelGatewayService

    def forbidden(*args, **kwargs):
        raise AssertionError("task service crossed execution/provider boundary")

    monkeypatch.setattr(ExecutionWorker, "run_once", forbidden)
    monkeypatch.setattr(FirmwareBuildToolAdapter, "execute", forbidden)
    monkeypatch.setattr(ModelGatewayService, "generate", forbidden)
    execution = ExecutionOperationService(store=SQLiteExecutionStore(tmp_path / "operations.sqlite"))
    service = FirmwareBuildTaskService(execution)
    state = submit(service)
    coordination, source = fixture()
    assert service.refresh(
        coordination, source, build_request(),
        operation_id=state.operation_reference.operation_id,
    ).coordination_status == "BLOCKED"


def test_nonbuild_approved_phase_and_source_drift_are_rejected(tmp_path) -> None:
    service = FirmwareBuildTaskService(ExecutionOperationService(store=SQLiteExecutionStore(tmp_path / "operations.sqlite")))
    coordination, source = fixture(output_type="SOURCE_CODE")
    with pytest.raises(EngineeringTaskRejected, match="stage"):
        service.submit(
            coordination, source, build_request(), requester_id="requester-1",
            target_id="target-1", target_fingerprint=FP,
            workspace_snapshot_id="snapshot-1", workspace_fingerprint=FP,
            proposal_id="proposal-1", proposal_fingerprint=FP,
            expires_at=NOW + timedelta(minutes=30),
        )
    coordination, source = fixture()
    original = source.resolve_plan
    calls = 0

    def drifting(task_id, plan_id):
        nonlocal calls
        calls += 1
        plan, review = original(task_id, plan_id)
        if calls == 1:
            return plan, review
        changed_phase = EngineeringPlanPhase.create(
            phase_id="phase-1", plan_id="plan-1", project_id="project-1",
            name="Changed", description="Changed", outputs=plan.phases[0].outputs,
        )
        changed_plan = EngineeringPlan.create(
            plan_id="plan-1", requirement_id="requirement-1", project_id="project-1",
            context_fingerprint=FP, tasks=(), phases=(changed_phase,), confidence=0.8,
        )
        data = changed_plan.model_dump(mode="python")
        data.update(status=PlanStatus.APPROVED, fingerprint=ZERO)
        return EngineeringPlan.model_validate(data, strict=False), review

    source.resolve_plan = drifting
    with pytest.raises(EngineeringTaskRejected, match="stage"):
        service.submit(
            coordination, source, build_request(), requester_id="requester-1",
            target_id="target-1", target_fingerprint=FP,
            workspace_snapshot_id="snapshot-1", workspace_fingerprint=FP,
            proposal_id="proposal-1", proposal_fingerprint=FP,
            expires_at=NOW + timedelta(minutes=30),
        )


@pytest.mark.parametrize("changed_intent", [False, True])
def test_concurrent_submissions_have_one_persisted_operation(tmp_path, changed_intent) -> None:
    database = tmp_path / "operations.sqlite"
    barrier = Barrier(2)
    stores = (SQLiteExecutionStore(database), SQLiteExecutionStore(database))

    class RacingExecution(ExecutionOperationService):
        def create_operation(self, request):
            barrier.wait(timeout=5)
            return super().create_operation(request)

    def attempt(target_id: str, store):
        service = FirmwareBuildTaskService(RacingExecution(store=store))
        try:
            return submit(service, target_id=target_id)
        except EngineeringTaskRejected as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (
            pool.submit(attempt, "target-1", stores[0]),
            pool.submit(attempt, "target-2" if changed_intent else "target-1", stores[1]),
        )
        results = tuple(future.result(timeout=10) for future in futures)
    persisted = SQLiteExecutionStore(database).list_operations("tenant-1", "project-1")
    assert len(persisted) == 1
    if changed_intent:
        assert sum(isinstance(item, EngineeringTaskRejected) for item in results) == 1
        assert sum(not isinstance(item, EngineeringTaskRejected) for item in results) == 1
    else:
        assert all(not isinstance(item, EngineeringTaskRejected) for item in results)
        assert results[0].operation_reference == results[1].operation_reference


class Targets:
    def __init__(self, target):
        self.target = target

    def get_bound(self, **kwargs):
        return self.target

    def revalidate(self, target):
        return target


class Workspace:
    def __init__(self, root: Path):
        self.root = root

    def resolve(self, **kwargs):
        return self.root


class Discovery:
    def __init__(self, executable: Path):
        self.executable = executable

    def resolve(self, definition):
        return ToolResolution(definition, self.executable, True, "AVAILABLE", "TOOL_RESOLVED")


class Runner:
    def __init__(self, *, fail: bool):
        self.fail = fail
        self.calls = 0

    def run(self, argv, *, cwd, timeout_seconds, environment, cancellation):
        self.calls += 1
        if self.calls == 1:
            return 0, b"configured", b""
        if self.fail:
            return 1, b"", b"compiler error"
        output = Path(argv[argv.index("--build") + 1]) / "firmware.elf"
        output.parent.mkdir(parents=True, exist_ok=True)
        elf = bytearray(64)
        elf[:4] = b"\x7fELF"
        elf[4:6] = b"\x01\x01"
        elf[18:20] = (40).to_bytes(2, "little")
        elf[24:28] = (1).to_bytes(4, "little")
        output.write_bytes(elf)
        return 0, b"build complete", b""


def cmake_workspace(root: Path) -> FirmwareBuildRequest:
    for name in (
        "CMakeLists.txt", "cmake/arm-none-eabi.cmake", "linker/STM32F103C8Tx_FLASH.ld",
        "vendor/st/STM32F1xx/Source/Templates/gcc/startup_stm32f103xb.s",
        "vendor/st/STM32F1xx/Source/Templates/system_stm32f1xx.c",
        "vendor/arm/CMSIS/Core/Include", "vendor/st/STM32F1xx/Include",
    ):
        path = root / name
        if "." in Path(name).name:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("placeholder", encoding="utf-8")
        else:
            path.mkdir(parents=True, exist_ok=True)
    content = b"int main(void) { return 0; }"
    (root / "main.c").write_bytes(content)
    return build_request(
        cmake=True, source_digest="sha256:" + hashlib.sha256(content).hexdigest(),
        source_size=len(content),
    )


@pytest.mark.parametrize(
    "fail, expected, phase_drift",
    [(False, "BUILD_PASS", False), (True, "BUILD_FAILED", False), (False, "APPROVAL_STALE", True)],
)
def test_cmake_worker_receipt_evidence_projection(tmp_path, monkeypatch, fail, expected, phase_drift) -> None:
    request = cmake_workspace(tmp_path)
    tool = tmp_path / "toolchain" / "bin" / "arm-none-eabi-gcc.exe"
    tool.parent.mkdir(parents=True)
    tool.write_bytes(b"stub")
    for name, env in (("cmake.exe", "EMBRIX_CMAKE_EXECUTABLE"), ("ninja.exe", "EMBRIX_NINJA_EXECUTABLE")):
        executable = tmp_path / name
        executable.write_bytes(b"stub")
        monkeypatch.setenv(env, str(executable))
    target = ExecutionTarget(
        target_id="target-1", tenant_id="tenant-1", project_id="project-1",
        target_kind="STM32F103C8T6", board_family="STM32F1", mcu_family="STM32F103C8T6",
        stable_identity_hash="stable-1", candidate_id="candidate-1",
        binding_status=TargetBindingStatus.BOUND, bound_by="requester-1", bound_at=NOW,
    )
    store = SQLiteExecutionStore(tmp_path / "operations.sqlite")
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    execution = ExecutionOperationService(store=store, artifact_store=artifact_store, clock=lambda: NOW)
    task_service = FirmwareBuildTaskService(execution)
    coordination, source = fixture()
    bound = proposal(execution, request, target_fingerprint=target.fingerprint)
    state = task_service.submit(
        coordination, source, request, requester_id="requester-1",
        target_id=target.target_id, target_fingerprint=target.fingerprint,
        workspace_snapshot_id="snapshot-1", workspace_fingerprint=FP,
        proposal_id=bound.proposal_id, proposal_fingerprint=bound.fingerprint,
        expires_at=NOW + timedelta(minutes=30),
    )
    operation_id = state.operation_reference.operation_id
    assert state.coordination_status == "BLOCKED"
    assert store.get_status("tenant-1", "project-1", operation_id).status is OperationStatus.WAIT_APPROVAL
    assert store.get_receipt("tenant-1", "project-1", operation_id) is None
    with pytest.raises(ExecutionRejected):
        execution.enqueue_by_id(
            tenant_id="tenant-1", project_id="project-1", operation_id=operation_id,
            approval_id="missing-approval",
        )
    assert ExecutionWorker(
        worker_id="worker-before-approval", store=store,
        registry=ExecutionToolRegistry(Discovery(tool)), workspace_resolver=Workspace(tmp_path),
        targets=Targets(target), artifact_store=artifact_store,
        build=FirmwareBuildToolAdapter(runner=Runner(fail=fail)),
        allowed_operation_types=(OperationType.BUILD,), clock=lambda: NOW,
    ).run_once() is None
    grant = execution.issue_approval_for_id(
        tenant_id="tenant-1", project_id="project-1", operation_id=operation_id,
        reviewer_id="reviewer-1", nonce="approval-1", expires_at=NOW + timedelta(minutes=10),
    )
    execution.enqueue_by_id(
        tenant_id="tenant-1", project_id="project-1", operation_id=operation_id,
        approval_id=grant.grant_id,
    )
    current_target = Targets(target)
    current_workspace = Workspace(tmp_path)

    def revalidate_build_context(operation) -> bool:
        try:
            current_task = task_service.refresh(
                coordination, source, request, operation_id=operation.operation_id,
            )
        except EngineeringTaskRejected:
            return False
        current_proposal = store.get_proposal("tenant-1", "project-1", operation.proposal_id)
        bound_target = current_target.get_bound(
            tenant_id="tenant-1", project_id="project-1", target_id=operation.target_id,
        )
        workspace_root = current_workspace.resolve(
            tenant_id="tenant-1", project_id="project-1",
            snapshot_id=operation.workspace_snapshot_id,
            snapshot_fingerprint=operation.workspace_fingerprint,
        )
        source_artifact = request.source_artifacts[0]
        source_content = (workspace_root / source_artifact.relative_path).read_bytes()
        return bool(
            current_task.task_id == source.request.task_id
            and current_task.plan_reference == coordination.plan_reference
            and current_task.stage_reference == coordination.current_stage_reference
            and current_task.operation_reference.operation_fingerprint == operation.fingerprint
            and operation.correlation is not None
            and operation.correlation.task_id == source.request.task_id
            and operation.requester_id == source.bound_principal_id
            and current_proposal == bound
            and operation.proposal_fingerprint == bound.fingerprint
            and bound_target == target
            and operation.target_fingerprint == target.fingerprint
            and operation.workspace_snapshot_id == "snapshot-1"
            and operation.workspace_fingerprint == FP
            and workspace_root == tmp_path
            and operation.firmware_build_request == request
            and operation.firmware_build_request_fingerprint == request.fingerprint
            and len(request.source_artifacts) == 1
            and source_artifact.content_fingerprint
            == "sha256:" + hashlib.sha256(source_content).hexdigest()
            and source_artifact.size_bytes == len(source_content)
        )

    if phase_drift:
        changed_phase = EngineeringPlanPhase.create(
            phase_id="phase-1", plan_id="plan-1", project_id="project-1",
            name="Firmware development", description="Changed after approval",
            outputs=source.plan.phases[0].outputs,
        )
        changed_plan = EngineeringPlan.create(
            plan_id="plan-1", requirement_id="requirement-1", project_id="project-1",
            context_fingerprint=FP, tasks=(), phases=(changed_phase,), confidence=0.8,
        )
        source.plan = EngineeringPlan.model_validate({
            **changed_plan.model_dump(mode="python"),
            "status": PlanStatus.APPROVED,
            "fingerprint": ZERO,
        }, strict=False)
    runner = Runner(fail=fail)
    worker = ExecutionWorker(
        worker_id="worker-1", store=store,
        registry=ExecutionToolRegistry(Discovery(tool)), workspace_resolver=current_workspace,
        targets=current_target, artifact_store=artifact_store,
        build=FirmwareBuildToolAdapter(runner=runner),
        allowed_operation_types=(OperationType.BUILD,), clock=lambda: NOW,
        build_context_revalidator=revalidate_build_context,
        build_sandbox_root=tmp_path.parent / (tmp_path.name + "-build-sandbox"),
    )
    terminal = worker.run_once()
    assert terminal is not None
    if phase_drift:
        assert terminal.status is OperationStatus.FAILED
        assert terminal.safe_detail == "build approval is stale"
        assert runner.calls == 0
        return
    assert runner.calls == 2, (terminal.status, terminal.safe_detail)
    assert terminal.status is (OperationStatus.FAILED if fail else OperationStatus.SUCCEEDED), terminal.safe_detail
    refreshed = task_service.refresh(coordination, source, request, operation_id=operation_id)
    assert refreshed.coordination_status == expected
    assert refreshed.receipt_reference is not None
    assert refreshed.receipt_reference.evidence_fingerprint is not None
    assert refreshed.receipt_reference.artifact_ids
    original_receipt = execution.get_receipt

    def stale_receipt(**kwargs):
        receipt = original_receipt(**kwargs)
        return receipt.model_copy(update={"project_id": "project-other"})

    execution.get_receipt = stale_receipt
    stale = task_service.refresh(coordination, source, request, operation_id=operation_id)
    assert stale.coordination_status == "BLOCKED"
    assert stale.receipt_reference is None
    execution.get_receipt = original_receipt
    original_status = execution.get_status
    receipt = original_receipt(tenant_id="tenant-1", project_id="project-1", operation_id=operation_id)
    evidence_data = receipt.build_evidence.model_dump(mode="python")
    evidence_data.update(request_fingerprint=FP, fingerprint=ZERO)
    stale_evidence = type(receipt.build_evidence).model_validate(evidence_data)
    receipt_data = receipt.model_dump(mode="python")
    receipt_data.update(build_evidence=stale_evidence, fingerprint=ZERO)
    stale_receipt = type(receipt).model_validate(receipt_data)
    status = original_status(tenant_id="tenant-1", project_id="project-1", operation_id=operation_id)
    status_data = status.model_dump(mode="python")
    status_data.update(receipt_fingerprint=stale_receipt.fingerprint, fingerprint=ZERO)
    stale_status = type(status).model_validate(status_data)
    execution.get_receipt = lambda **kwargs: stale_receipt
    execution.get_status = lambda **kwargs: stale_status
    stale = task_service.refresh(coordination, source, request, operation_id=operation_id)
    assert stale.coordination_status == "BLOCKED"
    assert stale.receipt_reference is None
    partial_evidence_data = receipt.build_evidence.model_dump(mode="python")
    partial_evidence_data.update(
        request_fingerprint=FP,
        output_artifact_ids=("foreign-artifact",),
        log_artifact_id="foreign-artifact",
        raw_evidence_artifact_id="foreign-artifact",
        fingerprint=ZERO,
    )
    partial_evidence = type(receipt.build_evidence).model_validate(partial_evidence_data)
    partial_receipt_data = receipt.model_dump(mode="python")
    partial_receipt_data.update(build_result=None, build_evidence=partial_evidence, fingerprint=ZERO)
    partial_receipt = type(receipt).model_validate(partial_receipt_data)
    partial_status_data = status.model_dump(mode="python")
    partial_status_data.update(receipt_fingerprint=partial_receipt.fingerprint, fingerprint=ZERO)
    partial_status = type(status).model_validate(partial_status_data)
    execution.get_receipt = lambda **kwargs: partial_receipt
    execution.get_status = lambda **kwargs: partial_status
    partial = task_service.refresh(coordination, source, request, operation_id=operation_id)
    assert partial.coordination_status == "UNVERIFIED"
    assert partial.receipt_reference is not None
    assert partial.receipt_reference.receipt_fingerprint == partial_receipt.fingerprint
    assert partial.receipt_reference.evidence_fingerprint is None
    assert partial.receipt_reference.artifact_ids == ()


@pytest.mark.parametrize(
    "fail, expected, phase_drift",
    [(False, "BUILD_PASS", False), (True, "BUILD_FAILED", False), (False, "APPROVAL_STALE", True)],
)
def test_cmake_receipt_projection_after_slow_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    fail: bool, expected: str, phase_drift: bool,
) -> None:
    monkeypatch.setitem(globals(), "NOW", datetime.now(UTC) - timedelta(minutes=11))
    test_cmake_worker_receipt_evidence_projection(
        tmp_path, monkeypatch, fail, expected, phase_drift,
    )
