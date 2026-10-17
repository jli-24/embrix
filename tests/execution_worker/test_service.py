from __future__ import annotations

import sqlite3
import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from embedded_copilot.correlation import CorrelationContext
from embedded_copilot.api.copilot_execution_models import status_response
from embedded_copilot.execution_worker.contracts import (
    ApprovalPurpose,
    ApprovalStatus,
    BuildOperation,
    BuildProvenanceType,
    CancellationRequest,
    OperationApprovalGrant,
    OperationEvent,
    OperationEventType,
    OperationRequest,
    OperationStatus,
    OperationStatusView,
    RecoveryState,
    TestOperation as ExecutionTestOperation,
    ToolchainIdentity,
    OperationType,
    TargetDiscoveryCandidate,
    ExecutionTarget,
    ExecutionProposal,
    TargetBindingStatus,
    RecoveryDecision,
)
from embedded_copilot.execution_worker.service import ExecutionOperationService, ExecutionRejected
from embedded_copilot.execution_worker.store import SQLiteExecutionStore
from embedded_copilot.firmware_engineering import (
    FirmwareBuildConfiguration,
    FirmwareBuildRequest,
    FirmwareSourceArtifact,
)


FP = "sha256:" + "2" * 64
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _firmware_request() -> FirmwareBuildRequest:
    configuration = FirmwareBuildConfiguration(
        target_kind="STM32F446RE", build_system="CMAKE",
        toolchain_id="CMAKE_NINJA_BUILD", toolchain_version="3",
        profile="DEBUG", build_directory=".", expected_artifact_types=("ELF",),
    )
    return FirmwareBuildRequest.create(
        project_id="project-1", firmware_reference="firmware-1",
        build_profile="DEBUG", firmware_spec_id="spec-1",
        firmware_spec_fingerprint=FP, implementation_id="implementation-1",
        implementation_fingerprint=FP, target_kind="STM32F446RE",
        build_system="CMAKE", toolchain_id="CMAKE_NINJA_BUILD",
        toolchain_version="3", configuration=configuration,
        source_artifacts=(FirmwareSourceArtifact(
            relative_path="main.c", content_fingerprint=FP,
            size_bytes=10, source_revision=FP,
        ),),
        expected_source_revision=FP, actual_source_revision=FP,
    )


def _operation() -> BuildOperation:
    now = datetime.now(UTC)
    return BuildOperation(
        operation_id="operation-build-1", tenant_id="tenant-1", project_id="project-1", requester_id="requester-1",
        target_id="target-1", target_fingerprint=FP, workspace_snapshot_id="snapshot-1", workspace_fingerprint=FP,
        proposal_id="proposal-1", proposal_fingerprint=FP, created_at=now, expires_at=now + timedelta(minutes=10),
        nonce="nonce-1", toolchain=ToolchainIdentity(toolchain_id="CMAKE_NINJA_BUILD", tool_version="3", tool_family="CMAKE_NINJA", supported_operation=OperationType.BUILD, supported_target="STM32F446RE"),
        configuration="DEBUG", expected_artifact_type="ELF",
        correlation=CorrelationContext(
            request_id="request-execution-1",
            trace_id="trace-execution-1",
            project_id="project-1",
            operation_id="operation-build-1",
        ),
    )


def _test_operation() -> ExecutionTestOperation:
    return ExecutionTestOperation(
        operation_id="operation-test-1",
        tenant_id="tenant-1",
        project_id="project-1",
        requester_id="requester-1",
        target_id="target-1",
        target_fingerprint=FP,
        workspace_snapshot_id="snapshot-1",
        workspace_fingerprint=FP,
        proposal_id="proposal-1",
        proposal_fingerprint=FP,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
        nonce="nonce-test-1",
        test_suite_id="firmware-smoke",
        test_profile_id="default-validation",
    )


def _proposal() -> ExecutionProposal:
    return ExecutionProposal(
        proposal_id="proposal-canonical-1",
        tenant_id="tenant-1",
        project_id="project-1",
        requester_id="requester-1",
        target_id="target-1",
        target_fingerprint=FP,
        workspace_snapshot_id="snapshot-1",
        workspace_fingerprint=FP,
        approved_workspace_operation_id="workspace-operation-1",
        approved_workspace_receipt_fingerprint=FP,
        operation_type=OperationType.TEST,
        toolchain_id="PYTEST",
        toolchain_version="1",
        build_configuration="SMOKE",
        created_at=NOW,
    )


def _operation_for_proposal(proposal: ExecutionProposal) -> ExecutionTestOperation:
    return ExecutionTestOperation(
        operation_id="operation-proposal-1",
        tenant_id=proposal.tenant_id,
        project_id=proposal.project_id,
        requester_id=proposal.requester_id,
        target_id=proposal.target_id,
        target_fingerprint=proposal.target_fingerprint,
        workspace_snapshot_id=proposal.workspace_snapshot_id,
        workspace_fingerprint=proposal.workspace_fingerprint,
        proposal_id=proposal.proposal_id,
        proposal_fingerprint=proposal.fingerprint,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
        nonce="nonce-proposal-1",
        test_suite_id="firmware-smoke",
        test_profile_id="default-validation",
    )


def _build_proposal() -> ExecutionProposal:
    return ExecutionProposal(
        proposal_id="proposal-1",
        tenant_id="tenant-1",
        project_id="project-1",
        requester_id="requester-1",
        target_id="target-1",
        target_fingerprint=FP,
        workspace_snapshot_id="snapshot-1",
        workspace_fingerprint=FP,
        approved_workspace_operation_id="workspace-operation-1",
        approved_workspace_receipt_fingerprint=FP,
        operation_type=OperationType.BUILD,
        toolchain_id="CMAKE_NINJA_BUILD",
        toolchain_version="3",
        build_configuration="DEBUG",
        created_at=NOW,
        source_revision=FP,
        firmware_build_request_fingerprint=_firmware_request().fingerprint,
    )


def _build_operation_for_proposal(proposal: ExecutionProposal) -> BuildOperation:
    request = _firmware_request()
    return BuildOperation.model_validate(
        {
            **_operation().model_dump(mode="python"),
            "proposal_fingerprint": proposal.fingerprint,
            "toolchain": ToolchainIdentity(
                toolchain_id="CMAKE_NINJA_BUILD", tool_version="3",
                tool_family="CMAKE", supported_operation=OperationType.BUILD,
                supported_target="STM32F446RE",
            ),
            "firmware_build_request_id": request.firmware_reference,
            "firmware_build_request_fingerprint": request.fingerprint,
            "firmware_spec_id": request.firmware_spec_id,
            "firmware_spec_fingerprint": request.firmware_spec_fingerprint,
            "implementation_id": request.implementation_id,
            "implementation_fingerprint": request.implementation_fingerprint,
            "source_artifacts": request.source_artifacts,
            "build_system": request.build_system,
            "firmware_build_request": request,
            "fingerprint": "sha256:" + "0" * 64,
        }
    )


def test_existing_snapshot_proposal_has_no_apply_receipt_and_binds_build(tmp_path) -> None:
    payload = _build_proposal().model_dump(mode="python")
    payload.pop("approved_workspace_operation_id")
    payload.pop("approved_workspace_receipt_fingerprint")
    payload["provenance"] = BuildProvenanceType.EXISTING_SNAPSHOT
    payload.update(
        plan_id="plan-1", plan_fingerprint=FP,
        build_configuration_fingerprint=FP, catalog_context_fingerprint=FP,
    )
    payload["fingerprint"] = "sha256:" + "0" * 64
    proposal = ExecutionProposal.model_validate(payload)
    service = ExecutionOperationService(store=SQLiteExecutionStore(tmp_path / "execution.sqlite3"))
    service.create_proposal(proposal)
    operation = _build_operation_for_proposal(proposal)
    service.create_operation(OperationRequest(operation=operation))
    assert service.get_operation(tenant_id="tenant-1", project_id="project-1", operation_id=operation.operation_id) == operation


def test_build_provenance_rejects_receipt_mismatch() -> None:
    payload = _build_proposal().model_dump(mode="python")
    payload["fingerprint"] = "sha256:" + "0" * 64
    payload["provenance"] = BuildProvenanceType.EXISTING_SNAPSHOT
    payload.update(
        plan_id="plan-1", plan_fingerprint=FP,
        build_configuration_fingerprint=FP, catalog_context_fingerprint=FP,
    )
    with pytest.raises(ValueError, match="cannot claim workspace apply"):
        ExecutionProposal.model_validate(payload)
    payload["provenance"] = BuildProvenanceType.APPLIED_CHANGE
    payload["approved_workspace_receipt_fingerprint"] = None
    with pytest.raises(ValueError, match="requires workspace receipt"):
        ExecutionProposal.model_validate(payload)


def test_existing_snapshot_requires_confirmed_catalog_binding() -> None:
    payload = _build_proposal().model_dump(mode="python")
    payload.pop("approved_workspace_operation_id")
    payload.pop("approved_workspace_receipt_fingerprint")
    payload.update(provenance=BuildProvenanceType.EXISTING_SNAPSHOT, fingerprint="sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="confirmed catalog context"):
        ExecutionProposal.model_validate(payload)


def test_legacy_applied_proposal_fingerprint_remains_readable() -> None:
    payload = _build_proposal().model_dump(mode="json")
    new_fields = {"fingerprint", "provenance", "plan_id", "plan_fingerprint", "build_configuration_fingerprint", "catalog_context_fingerprint"}
    legacy = {key: value for key, value in payload.items() if key not in new_fields}
    for field in new_fields - {"fingerprint"}:
        payload.pop(field)
    payload["fingerprint"] = "sha256:" + hashlib.sha256(json.dumps(legacy, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    restored = ExecutionProposal.model_validate_json(json.dumps(payload))
    assert restored.fingerprint == payload["fingerprint"]
    assert restored.provenance is BuildProvenanceType.APPLIED_CHANGE


def test_build_requires_persisted_proposal_before_waiting_for_approval(tmp_path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    service = ExecutionOperationService(store=store)
    operation = _operation()

    with pytest.raises(ExecutionRejected, match="proposal not found"):
        service.create_operation(OperationRequest(operation=operation))

    assert store.get_operation(operation.tenant_id, operation.project_id, operation.operation_id) is None
    assert service.get_status(
        tenant_id=operation.tenant_id,
        project_id=operation.project_id,
        operation_id=operation.operation_id,
    ) is None


@pytest.mark.parametrize(
    "mismatch", ("proposal_fingerprint", "target_id", "toolchain_id", "toolchain_version", "configuration")
)
def test_build_rejects_proposal_binding_conflict(tmp_path, mismatch: str) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    service = ExecutionOperationService(store=store)
    proposal = service.create_proposal(_build_proposal())
    operation = _build_operation_for_proposal(proposal)
    payload = operation.model_dump(mode="python")
    if mismatch in {"toolchain_id", "toolchain_version"}:
        payload["toolchain"] = {
            **payload["toolchain"],
            "toolchain_id": "OTHER_BUILD" if mismatch == "toolchain_id" else operation.toolchain.toolchain_id,
            "tool_version": "4" if mismatch == "toolchain_version" else operation.toolchain.tool_version,
            "fingerprint": "sha256:" + "0" * 64,
        }
    else:
        payload[mismatch] = "sha256:" + "0" * 64 if mismatch == "proposal_fingerprint" else "other"
    payload["fingerprint"] = "sha256:" + "0" * 64
    if mismatch == "toolchain_id":
        with pytest.raises(ValueError, match="firmware build request binding mismatch"):
            BuildOperation.model_validate(payload)
        return
    conflicting = BuildOperation.model_validate(payload)

    with pytest.raises(ExecutionRejected, match="proposal identity conflict"):
        service.create_operation(OperationRequest(operation=conflicting))
    assert store.get_operation(operation.tenant_id, operation.project_id, operation.operation_id) is None


@pytest.mark.parametrize("field", ("source_revision", "firmware_build_request_fingerprint"))
def test_build_rejects_proposal_source_binding_conflict(tmp_path, field: str) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    service = ExecutionOperationService(store=store)
    payload = _build_proposal().model_dump(mode="python")
    payload[field] = "sha256:" + "9" * 64
    payload["fingerprint"] = "sha256:" + "0" * 64
    proposal = service.create_proposal(ExecutionProposal.model_validate(payload))
    operation = _build_operation_for_proposal(proposal)

    with pytest.raises(ExecutionRejected, match="proposal identity conflict"):
        service.create_operation(OperationRequest(operation=operation))


def test_build_rejects_legacy_proposal_without_source_binding(tmp_path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    service = ExecutionOperationService(store=store)
    payload = _build_proposal().model_dump(mode="python")
    payload.update(source_revision=None, firmware_build_request_fingerprint=None,
                   fingerprint="sha256:" + "0" * 64)
    proposal = service.create_proposal(ExecutionProposal.model_validate(payload))
    operation = _build_operation_for_proposal(proposal)

    with pytest.raises(ExecutionRejected, match="proposal identity conflict"):
        service.create_operation(OperationRequest(operation=operation))


@pytest.mark.parametrize("field", ("source_artifacts", "build_directory"))
def test_build_rejects_top_level_inputs_that_differ_from_bound_request(tmp_path, field: str) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    service = ExecutionOperationService(store=store)
    proposal = service.create_proposal(_build_proposal())
    operation = _build_operation_for_proposal(proposal)
    payload = operation.model_dump(mode="python")
    if field == "source_artifacts":
        changed = payload["source_artifacts"][0]
        payload[field] = ({**changed, "content_fingerprint": "sha256:" + "9" * 64},)
    else:
        payload[field] = "build"
    payload["fingerprint"] = "sha256:" + "0" * 64
    conflicting = BuildOperation.model_validate(payload)

    with pytest.raises(ExecutionRejected, match="proposal identity conflict"):
        service.create_operation(OperationRequest(operation=conflicting))


def test_build_matching_proposal_is_idempotent(tmp_path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    service = ExecutionOperationService(store=store)
    proposal = service.create_proposal(_build_proposal())
    operation = _build_operation_for_proposal(proposal)
    request = OperationRequest(operation=operation)

    assert service.create_operation(request) == request
    assert service.create_operation(request) == request
    changed = BuildOperation.model_validate({
        **operation.model_dump(mode="python"),
        "nonce": "nonce-changed",
        "fingerprint": "sha256:" + "0" * 64,
    })
    with pytest.raises(ExecutionRejected, match="operation identity conflict"):
        service.create_operation(OperationRequest(operation=changed))
    status = service.get_status(
        tenant_id=operation.tenant_id,
        project_id=operation.project_id,
        operation_id=operation.operation_id,
    )
    assert status is not None and status.status is OperationStatus.WAIT_APPROVAL
    assert tuple(event.event_type for event in store.list_operation_events(
        operation.tenant_id, operation.project_id, operation.operation_id
    )) == (OperationEventType.OPERATION_CREATED,)


def test_persisted_proposal_creates_typed_operation_waiting_for_approval(tmp_path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    service = ExecutionOperationService(store=store, clock=lambda: NOW)
    proposal = service.create_proposal(_proposal())
    operation = _operation_for_proposal(proposal)

    created = service.create_operation(OperationRequest(operation=operation))
    status = service.get_status(
        tenant_id=operation.tenant_id,
        project_id=operation.project_id,
        operation_id=operation.operation_id,
    )

    assert created.operation == operation
    assert status is not None
    assert status.operation_type is OperationType.TEST
    assert status.status is OperationStatus.WAIT_APPROVAL
    assert status.approval_id is None


def test_persisted_proposal_identity_conflict_fails_closed(tmp_path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    service = ExecutionOperationService(store=store, clock=lambda: NOW)
    proposal = service.create_proposal(_proposal())
    operation = _operation_for_proposal(proposal)
    conflicting = ExecutionTestOperation.model_validate(
        {
            **operation.model_dump(mode="python"),
            "target_id": "target-other",
            "fingerprint": "sha256:" + "0" * 64,
        }
    )

    with pytest.raises(ExecutionRejected, match="proposal identity conflict"):
        service.create_operation(OperationRequest(operation=conflicting))
    assert service.get_status(
        tenant_id=operation.tenant_id,
        project_id=operation.project_id,
        operation_id=operation.operation_id,
    ) is None


def test_build_approval_is_purpose_bound_and_queue_is_persistent(tmp_path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    service = ExecutionOperationService(store=store)
    proposal = service.create_proposal(_build_proposal())
    request = OperationRequest(operation=_build_operation_for_proposal(proposal))
    service.create_operation(request)
    approval = service.issue_approval(operation=request, reviewer_id="reviewer-1", nonce="approval-nonce", expires_at=datetime.now(UTC) + timedelta(minutes=5))
    assert approval.approval_purpose.value == "BUILD"
    status = service.enqueue(operation=request, approval_id=approval.grant_id)
    assert status.status is OperationStatus.QUEUED
    restored = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    restored_operation = restored.get_operation("tenant-1", "project-1", "operation-build-1")
    restored_status = restored.get_status("tenant-1", "project-1", "operation-build-1")
    assert restored_status.status is OperationStatus.QUEUED
    assert restored_operation.correlation.request_id == "request-execution-1"
    assert restored_status.correlation.trace_id == "trace-execution-1"
    assert restored_status.correlation.approval_id == approval.grant_id
    assert restored_status.correlation.workflow_run_id is None
    assert status_response(restored_status).correlation == restored_status.correlation
    events = restored.list_operation_events("tenant-1", "project-1", "operation-build-1")
    assert events[-1].correlation.approval_id == approval.grant_id


def test_operation_correlation_scope_and_secret_values_fail_closed() -> None:
    payload = _operation().model_dump(mode="python")
    payload["fingerprint"] = "sha256:" + "0" * 64
    payload["correlation"] = CorrelationContext(
        request_id="request-other",
        trace_id="trace-other",
        project_id="project-other",
        operation_id="operation-build-1",
    )
    with pytest.raises(ValueError, match="correlation binding mismatch"):
        BuildOperation.model_validate(payload)
    with pytest.raises(ValueError):
        CorrelationContext(
            request_id="api_key:secret",
            trace_id="trace-other",
            project_id="project-1",
        )


def test_legacy_operation_without_correlation_keeps_its_original_fingerprint() -> None:
    payload = _operation().model_dump(mode="json")
    payload.pop("correlation")
    payload.pop("fingerprint")
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    payload["fingerprint"] = "sha256:" + hashlib.sha256(encoded).hexdigest()

    restored = BuildOperation.model_validate(payload, strict=False)

    assert restored.correlation is None


def test_tombstone_denies_new_operation(tmp_path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    store.tombstone_project("tenant-1", "project-1", datetime.now(UTC))
    with pytest.raises(ExecutionRejected, match="tombstoned"):
        ExecutionOperationService(store=store).create_operation(OperationRequest(operation=_operation()))


def test_discovery_is_not_a_trusted_target_and_binding_is_project_scoped(tmp_path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    registry = __import__("embedded_copilot.execution_worker.targets", fromlist=["SQLiteTargetRegistry"]).SQLiteTargetRegistry(store)
    candidate = TargetDiscoveryCandidate(
        candidate_id="candidate-new-device", tenant_id="tenant-1", project_id="project-1",
        target_kind="STM32F446RE", model="STM32F446RE", observed_identity_hash="probe-new",
        discovered_at=datetime.now(UTC),
    )
    registry.discover(candidate)
    assert registry.get_bound(tenant_id="tenant-1", project_id="project-1", target_id="target-new") is None
    target = ExecutionTarget(
        target_id="target-new", tenant_id="tenant-1", project_id="project-1", target_kind="STM32F446RE",
        board_family="NUCLEO-F446RE", mcu_family="STM32F446RE", stable_identity_hash="probe-new",
        candidate_id=candidate.candidate_id, binding_status=TargetBindingStatus.BOUND,
        bound_by="operator-1", bound_at=datetime.now(UTC),
    )
    assert registry.bind(target).target_id == "target-new"
    assert registry.get_bound(tenant_id="tenant-2", project_id="project-1", target_id="target-new") is None


def test_test_operation_uses_existing_approval_queue_and_cancellation_lifecycle(tmp_path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    service = ExecutionOperationService(store=store, clock=lambda: NOW)
    operation = _test_operation()
    request = OperationRequest(operation=operation)

    service.create_operation(request)
    waiting = service.get_status(
        tenant_id=operation.tenant_id,
        project_id=operation.project_id,
        operation_id=operation.operation_id,
    )
    approval = service.issue_approval(
        operation=request,
        reviewer_id="reviewer-1",
        nonce="approval-test-1",
        expires_at=NOW + timedelta(minutes=5),
    )
    queued = service.enqueue(operation=request, approval_id=approval.grant_id)
    cancelled = service.request_cancellation(
        CancellationRequest(
            request_id="cancellation-test-1",
            tenant_id=operation.tenant_id,
            project_id=operation.project_id,
            operation_id=operation.operation_id,
            requester_id=operation.requester_id,
            reason="operator requested cancellation",
            requested_at=NOW + timedelta(seconds=1),
        )
    )

    assert waiting is not None
    assert waiting.status is OperationStatus.WAIT_APPROVAL
    assert approval.approval_purpose is ApprovalPurpose.TEST
    assert approval.operation_type is OperationType.TEST
    assert queued.status is OperationStatus.QUEUED
    assert cancelled.status is OperationStatus.CANCEL_REQUESTED
    assert cancelled.recovery_state is RecoveryState.NONE
    replayed = service.read_operation_events_after(
        tenant_id=operation.tenant_id,
        project_id=operation.project_id,
        last_event_id=None,
    )
    assert replayed is not None
    assert service.list_operation_events(
        tenant_id=operation.tenant_id,
        project_id=operation.project_id,
        operation_id=operation.operation_id,
    ) == replayed
    assert service.read_operation_events_after(
        tenant_id=operation.tenant_id,
        project_id=operation.project_id,
        last_event_id="operation-event-unknown",
    ) is None
    with sqlite3.connect(tmp_path / "execution.sqlite3") as connection:
        events = tuple(
            OperationEvent.model_validate_json(row[0])
            for row in connection.execute(
                "SELECT body FROM v66_operation_events ORDER BY sequence"
            ).fetchall()
        )
    assert tuple(event.event_type for event in events) == (
        OperationEventType.OPERATION_CREATED,
        OperationEventType.APPROVAL_ISSUED,
        OperationEventType.OPERATION_QUEUED,
        OperationEventType.CANCELLATION_REQUESTED,
    )
    assert all(event.operation_fingerprint == operation.fingerprint for event in events)


def test_create_operation_rolls_back_snapshot_when_event_append_fails(
    tmp_path, monkeypatch
) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    operation = _test_operation()

    def fail_event(*args, **kwargs):
        raise sqlite3.IntegrityError("synthetic event failure")

    monkeypatch.setattr(store, "_append_event_in_transaction", fail_event)

    with pytest.raises(sqlite3.IntegrityError, match="synthetic event failure"):
        ExecutionOperationService(store=store, clock=lambda: NOW).create_operation(
            OperationRequest(operation=operation)
        )

    assert store.get_operation("tenant-1", "project-1", operation.operation_id) is None
    assert store.get_status("tenant-1", "project-1", operation.operation_id) is None


def test_idempotent_create_rejects_same_type_with_changed_payload(tmp_path) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    service = ExecutionOperationService(store=store, clock=lambda: NOW)
    operation = _test_operation()
    service.create_operation(OperationRequest(operation=operation))
    changed = ExecutionTestOperation.model_validate(
        {
            **operation.model_dump(mode="python"),
            "nonce": "nonce-test-changed",
            "fingerprint": "sha256:" + "0" * 64,
        }
    )

    with pytest.raises(ExecutionRejected, match="identity conflict"):
        service.create_operation(OperationRequest(operation=changed))

    assert store.get_operation(
        operation.tenant_id, operation.project_id, operation.operation_id
    ) == operation


def test_recovery_resolution_updates_status_and_event_atomically(tmp_path) -> None:
    database = tmp_path / "execution.sqlite3"
    store = SQLiteExecutionStore(database)
    service = ExecutionOperationService(store=store, clock=lambda: NOW)
    operation = _test_operation()
    service.create_operation(OperationRequest(operation=operation))
    waiting = store.get_status("tenant-1", "project-1", operation.operation_id)
    recovery_required = OperationStatusView.model_validate(
        {
            **waiting.model_dump(mode="python"),
            "status": OperationStatus.RECOVERY_REQUIRED,
            "recovery_state": RecoveryState.INTERRUPTED,
            "fingerprint": "sha256:" + "0" * 64,
        }
    )
    store.save_status(recovery_required)

    resolved = service.apply_recovery_decision(
        RecoveryDecision(
            decision_id="recovery-test-1",
            tenant_id=operation.tenant_id,
            project_id=operation.project_id,
            operation_id=operation.operation_id,
            reviewer_id="reviewer-1",
            decision="MARK_FAILED",
            decided_at=NOW + timedelta(seconds=1),
        )
    )

    assert resolved.status is OperationStatus.FAILED
    assert resolved.recovery_state is RecoveryState.RECOVERED
    with sqlite3.connect(database) as connection:
        event = OperationEvent.model_validate_json(
            connection.execute(
                "SELECT body FROM v66_operation_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()[0]
        )
    assert event.event_type is OperationEventType.RECOVERY_RESOLVED
    assert event.recovery_state is RecoveryState.RECOVERED


@pytest.mark.parametrize(
    ("cross_type", "cross_purpose"),
    (
        (OperationType.BUILD, ApprovalPurpose.BUILD),
        (OperationType.FLASH, ApprovalPurpose.FLASH),
    ),
)
def test_test_operation_rejects_cross_purpose_approval(
    tmp_path,
    cross_type: OperationType,
    cross_purpose: ApprovalPurpose,
) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    service = ExecutionOperationService(store=store, clock=lambda: NOW)
    operation = _test_operation()
    request = OperationRequest(operation=operation)
    service.create_operation(request)
    grant = OperationApprovalGrant(
        grant_id=f"approval-{cross_type.value.casefold()}",
        tenant_id=operation.tenant_id,
        project_id=operation.project_id,
        requester_id=operation.requester_id,
        approval_purpose=cross_purpose,
        operation_id=operation.operation_id,
        operation_type=cross_type,
        operation_fingerprint=operation.fingerprint,
        proposal_id=operation.proposal_id,
        proposal_fingerprint=operation.proposal_fingerprint,
        target_id=operation.target_id,
        target_fingerprint=operation.target_fingerprint,
        workspace_snapshot_id=operation.workspace_snapshot_id,
        workspace_fingerprint=operation.workspace_fingerprint,
        reviewer_id="reviewer-1",
        status=ApprovalStatus.ISSUED,
        expires_at=NOW + timedelta(minutes=5),
        nonce=f"nonce-{cross_type.value.casefold()}",
        issued_at=NOW,
    )
    store.save_approval(grant)

    with pytest.raises(ExecutionRejected, match="approval binding mismatch"):
        service.enqueue(operation=request, approval_id=grant.grant_id)


@pytest.mark.parametrize(
    ("updates", "grant_id"),
    (
        ({"target_id": "target-other"}, "approval-wrong-target"),
        ({"workspace_snapshot_id": "snapshot-other"}, "approval-wrong-workspace"),
        ({"proposal_id": "proposal-other"}, "approval-wrong-proposal"),
        ({"expires_at": NOW - timedelta(seconds=1)}, "approval-expired"),
    ),
)
def test_enqueue_rejects_expired_or_identity_mismatched_approval(
    tmp_path, updates: dict[str, object], grant_id: str
) -> None:
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    service = ExecutionOperationService(store=store, clock=lambda: NOW)
    operation = _test_operation()
    request = OperationRequest(operation=operation)
    service.create_operation(request)
    valid = service.issue_approval(
        operation=request,
        reviewer_id="reviewer-1",
        nonce="approval-valid",
        expires_at=NOW + timedelta(minutes=5),
    )
    forged = OperationApprovalGrant.model_validate(
        {
            **valid.model_dump(mode="python"),
            **updates,
            "grant_id": grant_id,
            "nonce": grant_id,
            "fingerprint": "sha256:" + "0" * 64,
        }
    )
    store.save_approval(forged)

    with pytest.raises(ExecutionRejected, match="approval binding mismatch"):
        service.enqueue(operation=request, approval_id=forged.grant_id)
    waiting = service.get_status(
        tenant_id=operation.tenant_id,
        project_id=operation.project_id,
        operation_id=operation.operation_id,
    )
    assert waiting is not None and waiting.status is OperationStatus.WAIT_APPROVAL


@pytest.mark.parametrize(
    ("operation_type", "expected_status"),
    (
        (OperationType.BUILD, OperationStatus.CANCEL_REQUESTED),
        (OperationType.TEST, OperationStatus.CANCEL_REQUESTED),
        (OperationType.UART_OBSERVATION, OperationStatus.CANCEL_REQUESTED),
        (OperationType.HIL, OperationStatus.CANCEL_REQUESTED),
        (OperationType.FLASH, OperationStatus.CANCEL_PENDING),
    ),
)
def test_running_cancellation_preserves_existing_operation_semantics(
    tmp_path,
    operation_type: OperationType,
    expected_status: OperationStatus,
) -> None:
    store = SQLiteExecutionStore(tmp_path / f"{operation_type.value}.sqlite3")
    service = ExecutionOperationService(store=store, clock=lambda: NOW)
    operation_id = f"operation-{operation_type.value.casefold()}"
    store.save_status(
        OperationStatusView(
            operation_id=operation_id,
            tenant_id="tenant-1",
            project_id="project-1",
            operation_type=operation_type,
            status=OperationStatus.RUNNING,
            approval_id="approval-1",
            worker_id="worker-1",
            recovery_state=RecoveryState.NONE,
            cancellation_requested=False,
            updated_at=NOW,
            safe_detail="operation running",
        )
    )

    updated = service.request_cancellation(
        CancellationRequest(
            request_id=f"cancel-{operation_type.value.casefold()}",
            tenant_id="tenant-1",
            project_id="project-1",
            operation_id=operation_id,
            requester_id="requester-1",
            reason="operator requested cancellation",
            requested_at=NOW + timedelta(seconds=1),
        )
    )

    assert updated.status is expected_status
