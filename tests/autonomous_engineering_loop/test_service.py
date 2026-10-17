from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from embedded_copilot.autonomous_engineering_loop import (
    ApprovedExecutionBundle,
    AutonomousEngineeringLoopService,
    InMemoryLoopState,
    LoopPolicy,
    LoopRejected,
    LoopState,
)
from embedded_copilot.engineering_context import (
    ContextSourceType,
    EngineeringContextEvidence,
    EngineeringContextRequest,
    EngineeringContextSnapshot,
)
from embedded_copilot.execution import (
    ApprovalReference,
    BuildArtifactReference,
    BuildRequest,
    BuildResult,
    ExecutionCapability,
    ExecutionRequest,
    ExecutionStatus,
    FlashRequest,
    FlashResult,
)
from embedded_copilot.failure_analysis import FailureAnalysisService, FailureObservation
from embedded_copilot.repair_proposal import RepairProposalService, RepairTargetType
from embedded_copilot.verification_loop import VerificationService
from embedded_copilot.workspace_runtime import (
    WorkspaceInspectionRequest,
    create_workspace_runtime,
)


def _context() -> EngineeringContextSnapshot:
    evidence = EngineeringContextEvidence.create(
        evidence_id="evidence-1",
        project_id="project-1",
        source_type=ContextSourceType.KNOWLEDGE_GATEWAY,
        subject="build",
        summary="Verified build configuration evidence",
        source_reference="knowledge:1",
        source_fingerprint="sha256:" + "a" * 64,
        confidence=0.8,
        verification_status="VERIFIED",
    )
    return EngineeringContextSnapshot.create(
        project_id="project-1",
        task_description="repair build failure",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(evidence,),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.8,
        provenance=(evidence.source(),),
        verification_status="VERIFIED",
    )


class _Context:
    def get_context(self, request):
        return _context()


def _failure_observation() -> FailureObservation:
    result = BuildResult.create(
        request_id="initial-build",
        status=ExecutionStatus.FAILED,
        exit_code=2,
        artifact=None,
        logs_summary="build output redacted",
    )
    return FailureObservation.from_build(
        result, project_id="project-1", target_id="target-1"
    )


class _Execution:
    def __init__(self, outcomes: list[ExecutionStatus]) -> None:
        self.outcomes = outcomes
        self.build_calls = 0
        self.flash_calls = 0

    def build(self, request: BuildRequest) -> BuildResult:
        self.build_calls += 1
        status = self.outcomes.pop(0)
        artifact = (
            BuildArtifactReference.from_bytes(
                project_id="project-1",
                workspace_id=request.workspace_snapshot.workspace_id,
                target_id="target-1",
                content=b"firmware",
            )
            if status is ExecutionStatus.SUCCEEDED
            else None
        )
        return BuildResult.create(
            request_id=request.request.request_id,
            status=status,
            exit_code=0 if status is ExecutionStatus.SUCCEEDED else 2,
            artifact=artifact,
            logs_summary="build completed" if artifact else "build output redacted",
        )

    def flash(self, request):
        self.flash_calls += 1
        raise AssertionError("flash must be explicit and is absent from this bundle")

    def collect_logs(self, request):
        raise AssertionError("debug is absent from this bundle")


class _CancelledExecution(_Execution):
    def build(self, request: BuildRequest) -> BuildResult:
        raise asyncio.CancelledError


class _MismatchedFlashExecution(_Execution):
    def flash(self, request: FlashRequest) -> FlashResult:
        self.flash_calls += 1
        return FlashResult.create(
            request_id="unexpected-flash-request",
            status=ExecutionStatus.SUCCEEDED,
            exit_code=0,
            firmware_fingerprint=request.artifact.firmware_fingerprint,
            logs_summary="flash completed",
        )


def _workspace_snapshot(root: Path):
    (root / "src").mkdir()
    (root / "src" / "main.c").write_text("int main(void) {}\n", encoding="utf-8")
    return (
        create_workspace_runtime(root)
        .workspace_port()
        .inspect_workspace(
            WorkspaceInspectionRequest(
                workspace_id="workspace-1", relative_paths=("src/main.c",)
            )
        )
    )


def _bundle(
    preparation,
    workspace_snapshot,
    *,
    request_id: str,
    include_flash: bool = False,
) -> ApprovedExecutionBundle:
    proposal_fp = preparation.proposal.fingerprint
    approval = ApprovalReference.create(
        approval_id=f"approval-{request_id}",
        project_id="project-1",
        target_id="target-1",
        capability=ExecutionCapability.BUILD,
        proposal_fingerprint=proposal_fp,
        expires_at="2030-01-01T00:00:00Z",
    )
    request = ExecutionRequest.create(
        request_id=request_id,
        project_id="project-1",
        target_id="target-1",
        proposal_fingerprint=proposal_fp,
        capability=ExecutionCapability.BUILD,
        approval=approval,
        timeout_seconds=5.0,
        cancellation_token="cancel-1",
    )
    values = {
        "loop_id": preparation.snapshot.loop_id,
        "project_id": "project-1",
        "target_id": "target-1",
        "proposal_fingerprint": proposal_fp,
        "handoff": preparation.handoff,
        "build_request": BuildRequest.create(
            request=request, workspace_snapshot=workspace_snapshot
        ),
    }
    if include_flash:
        artifact = BuildArtifactReference.from_bytes(
            project_id="project-1",
            workspace_id=workspace_snapshot.workspace_id,
            target_id="target-1",
            content=b"firmware",
        )
        flash_approval = ApprovalReference.create(
            approval_id=f"flash-approval-{request_id}",
            project_id="project-1",
            target_id="target-1",
            capability=ExecutionCapability.FLASH,
            proposal_fingerprint=proposal_fp,
            expires_at="2030-01-01T00:00:00Z",
        )
        flash_request = ExecutionRequest.create(
            request_id=f"flash-{request_id}",
            project_id="project-1",
            target_id="target-1",
            proposal_fingerprint=proposal_fp,
            capability=ExecutionCapability.FLASH,
            approval=flash_approval,
            timeout_seconds=5.0,
            cancellation_token="cancel-1",
        )
        values["flash_request"] = FlashRequest.create(
            request=flash_request,
            workspace_snapshot=workspace_snapshot,
            artifact=artifact,
        )
    return ApprovedExecutionBundle.create(**values)


def _service(execution) -> AutonomousEngineeringLoopService:
    return AutonomousEngineeringLoopService(
        state_port=InMemoryLoopState(),
        failure_analysis=FailureAnalysisService(context_provider=_Context()),
        repair_proposals=RepairProposalService(),
        verification=VerificationService(),
        execution=execution,
    )


def _start(service, *, policy: LoopPolicy | None = None):
    return service.start(
        _failure_observation(),
        EngineeringContextRequest(project_id="project-1", task="build failure"),
        loop_id="loop-1",
        target_type=RepairTargetType.CONFIGURATION_CHANGE,
        affected_component="build_config",
        governance_reference="external-review-1",
        policy=policy,
    )


def test_explicit_approved_build_transitions_to_success_without_flash(
    tmp_path: Path,
) -> None:
    execution = _Execution([ExecutionStatus.SUCCEEDED])
    service = _service(execution)
    preparation = _start(service)
    result = service.execute_approved(
        _bundle(preparation, _workspace_snapshot(tmp_path), request_id="build-1")
    )
    assert result.snapshot.state is LoopState.SUCCESS
    assert execution.build_calls == 1
    assert execution.flash_calls == 0


def test_bundle_identity_mismatch_is_rejected(tmp_path: Path) -> None:
    service = _service(_Execution([ExecutionStatus.SUCCEEDED]))
    preparation = _start(service)
    bundle = _bundle(preparation, _workspace_snapshot(tmp_path), request_id="build-1")
    material = bundle.model_dump(mode="python")
    material["loop_id"] = "loop-2"
    material.pop("fingerprint")
    with pytest.raises(LoopRejected):
        service.execute_approved(ApprovedExecutionBundle.create(**material))


def test_cancellation_returns_to_waiting_approval(tmp_path: Path) -> None:
    service = _service(_CancelledExecution([]))
    preparation = _start(service)
    with pytest.raises(asyncio.CancelledError):
        service.execute_approved(
            _bundle(preparation, _workspace_snapshot(tmp_path), request_id="build-1")
        )
    assert service.get_snapshot("loop-1").state is LoopState.WAITING_APPROVAL


def test_three_failed_explicit_attempts_escalate(tmp_path: Path) -> None:
    service = _service(_Execution([ExecutionStatus.FAILED] * 3))
    workspace_snapshot = _workspace_snapshot(tmp_path)
    preparation = _start(service, policy=LoopPolicy.create(max_retry=3))
    for index in range(3):
        result = service.execute_approved(
            _bundle(preparation, workspace_snapshot, request_id=f"build-{index}")
        )
        if index < 2:
            assert result.snapshot.state is LoopState.FAILED
            preparation = service.retry(
                _failure_observation(),
                EngineeringContextRequest(project_id="project-1", task="build failure"),
                loop_id="loop-1",
                target_type=RepairTargetType.CONFIGURATION_CHANGE,
                affected_component="build_config",
                governance_reference="external-review-1",
            )
    assert result.snapshot.state is LoopState.ESCALATED
    assert result.snapshot.retry_count == 3


def test_mismatched_flash_result_is_rejected_and_escalated(tmp_path: Path) -> None:
    execution = _MismatchedFlashExecution([ExecutionStatus.SUCCEEDED])
    service = _service(execution)
    preparation = _start(service)

    with pytest.raises(LoopRejected):
        service.execute_approved(
            _bundle(
                preparation,
                _workspace_snapshot(tmp_path),
                request_id="build-1",
                include_flash=True,
            )
        )

    assert execution.flash_calls == 1
    assert service.get_snapshot("loop-1").state is LoopState.ESCALATED
