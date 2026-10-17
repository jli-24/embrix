from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace
from datetime import UTC, datetime, timedelta

import pytest

from embedded_copilot.application import (
    EngineeringObjectFlowError,
    EvidenceReceiptReference,
    EvidenceReferenceStatus,
    OperationReceiptEvidenceResolver,
)
from embedded_copilot.engineering_copilot import (
    SchematicErcResult,
    SchematicErcStatus,
)
from embedded_copilot.execution_worker.contracts import (
    OperationReceipt,
    OperationStatus,
    OperationType,
    SchematicErcOperation,
    WorkspaceMutationResult,
)

from .fixtures import request

NOW = datetime(2026, 9, 21, 12, tzinfo=UTC)
TENANT = "tenant-receipt"
PROJECT = "project-receipt"
ARTIFACT = "sha256:" + "a" * 64
TARGET = "hardware/main.kicad_sch"


def test_canonical_evidence_module_imports_before_execution_package() -> None:
    result = subprocess.run(
        [sys.executable, "-B", "-c", "from embedded_copilot.engineering_runtime.evidence import CanonicalOperationReceiptEvidenceResolver"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


class ReceiptSource:
    def __init__(self, receipts, operations) -> None:
        self.receipts = receipts
        self.operations = operations

    def get_receipt(self, *, tenant_id, project_id, operation_id):
        return self.receipts.get((tenant_id, project_id, operation_id))

    def get_operation(self, *, tenant_id, project_id, operation_id):
        return self.operations.get((tenant_id, project_id, operation_id))


def _source_receipt() -> OperationReceipt:
    return OperationReceipt(
        operation_id="write-receipt-op",
        tenant_id=TENANT,
        project_id=PROJECT,
        operation_type=OperationType.KICAD_SCHEMATIC_WRITE,
        status=OperationStatus.SUCCEEDED,
        tool_execution_status="SUCCEEDED",
        flash_verification_status="NOT_APPLICABLE",
        target_runtime_verification_status="NOT_APPLICABLE",
        workspace_mutations=(
            WorkspaceMutationResult(
                relative_path=TARGET,
                content_fingerprint=ARTIFACT,
                recovery_state="AVAILABLE",
            ),
        ),
        workspace_apply_receipt_fingerprint=ARTIFACT,
        firmware_evidence_parse="UNAVAILABLE",
        created_at=NOW,
    )


def _erc_operation(source_receipt: OperationReceipt) -> SchematicErcOperation:
    return SchematicErcOperation(
        operation_id="erc-receipt-op",
        tenant_id=TENANT,
        project_id=PROJECT,
        requester_id="engineer",
        target_id="target-schematic",
        target_fingerprint=ARTIFACT,
        workspace_snapshot_id="snapshot-schematic",
        workspace_fingerprint=ARTIFACT,
        proposal_id="proposal-schematic",
        proposal_fingerprint=ARTIFACT,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=5),
        nonce="erc-nonce",
        conversation_id="conversation-receipt",
        schematic_artifact_ref=TARGET,
        schematic_artifact_fingerprint=ARTIFACT,
        source_operation_id=source_receipt.operation_id,
        source_operation_receipt_fingerprint=source_receipt.fingerprint,
        target_relative_path=TARGET,
        schematic_revision="r1",
    )


def _source() -> tuple[ReceiptSource, EvidenceReceiptReference]:
    source_receipt = _source_receipt()
    operation = _erc_operation(source_receipt)
    result = SchematicErcResult.create(
        operation_id=operation.operation_id,
        schematic_artifact_ref=TARGET,
        schematic_content_fingerprint=ARTIFACT,
        kicad_version="10.0.5",
        exit_status=0,
        started_at=NOW,
        finished_at=NOW,
        receipt_ref=source_receipt.fingerprint,
        status=SchematicErcStatus.ERC_PASS,
    )
    receipt = OperationReceipt(
        operation_id=operation.operation_id,
        tenant_id=TENANT,
        project_id=PROJECT,
        operation_type=OperationType.KICAD_SCHEMATIC_ERC,
        status=OperationStatus.SUCCEEDED,
        tool_execution_status="SUCCEEDED",
        flash_verification_status="NOT_APPLICABLE",
        target_runtime_verification_status="NOT_APPLICABLE",
        evidence_references=("erc-evidence-reference",),
        safe_diagnostics_summary="KICAD_ERC_PASS",
        schematic_erc_status="ERC_PASS",
        schematic_erc_result_fingerprint=result.fingerprint,
        schematic_erc_result=result,
        created_at=NOW,
    )
    store = ReceiptSource(
        {
            (TENANT, PROJECT, source_receipt.operation_id): source_receipt,
            (TENANT, PROJECT, receipt.operation_id): receipt,
        },
        {
            (TENANT, PROJECT, operation.operation_id): operation,
            (TENANT, PROJECT, source_receipt.operation_id): SimpleNamespace(
                operation_id=source_receipt.operation_id,
                tenant_id=TENANT, project_id=PROJECT,
                operation_type=OperationType.KICAD_SCHEMATIC_WRITE,
                target_relative_path=TARGET, content_fingerprint=ARTIFACT,
            ),
        },
    )
    reference = EvidenceReceiptReference(
        reference_id="erc-evidence-reference",
        receipt_id=receipt.operation_id,
        tenant_id=TENANT,
        project_id=PROJECT,
        source="caller-supplied",
        tool="caller-supplied",
        scope="SCHEMATIC_ERC",
        result_summary="caller-supplied",
        occurred_at=NOW,
        source_fingerprint=ARTIFACT,
        receipt_fingerprint=receipt.fingerprint,
        artifact_ref=TARGET,
        artifact_fingerprint=ARTIFACT,
        schematic_revision="r1",
        tool_version="10.0.5",
        execution_result="ERC_PASS",
    )
    return store, reference


def test_resolver_qualifies_canonical_worker_receipt() -> None:
    store, reference = _source()
    resolved = OperationReceiptEvidenceResolver(store).resolve(
        request(tenant_id=TENANT, project_id=PROJECT), reference
    )

    assert resolved.verification_status is EvidenceReferenceStatus.VERIFIED
    assert resolved.source == "KICAD_EXECUTION_WORKER"
    assert resolved.tool == "kicad-cli sch erc"
    assert resolved.result_summary == "KICAD_ERC_PASS"
    assert resolved.receipt_fingerprint == reference.receipt_fingerprint


def test_resolver_rejects_caller_verified_or_stale_artifact() -> None:
    store, reference = _source()
    resolver = OperationReceiptEvidenceResolver(store)

    with pytest.raises(EngineeringObjectFlowError, match="self-report"):
        resolver.resolve(
            request(tenant_id=TENANT, project_id=PROJECT),
            reference.model_copy(update={"verification_status": EvidenceReferenceStatus.VERIFIED}),
        )

    with pytest.raises(EngineeringObjectFlowError, match="binding"):
        resolver.resolve(
            request(tenant_id=TENANT, project_id=PROJECT),
            reference.model_copy(update={"artifact_fingerprint": "sha256:" + "b" * 64}),
        )
