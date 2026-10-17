from datetime import UTC, datetime

import pytest

from embedded_copilot.engineering_eda import (
    EDAArtifactReference,
    EDAArtifactType,
    EDABackend,
    EDAExecutionStatus,
    EDAOperationReceipt,
    EDAReadBackStatus,
    EDAVerificationStatus,
)


FP = "sha256:" + "1" * 64
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def board(content: str = FP) -> EDAArtifactReference:
    return EDAArtifactReference(
        project_id="project-stm32-control",
        artifact_id="board-1",
        artifact_type=EDAArtifactType.BOARD,
        revision="pcb-r1",
        format_version="kicad-2024",
        basename="control.kicad_pcb",
        byte_size=10,
        source_reference="workspace://board-1",
        content_fingerprint=content,
        captured_at=NOW,
    )


def test_receipt_requires_execution_readback_verification_chain() -> None:
    after = board("sha256:" + "2" * 64)
    receipt = EDAOperationReceipt(
        operation_id="eda-op-1",
        backend=EDABackend.KICAD,
        operation_fingerprint=FP,
        artifact_before=board(),
        artifact_after=after,
        approval_fingerprint="sha256:" + "3" * 64,
        execution_status=EDAExecutionStatus.EXECUTION_SUCCESS,
        readback_status=EDAReadBackStatus.READ_BACK_PASS,
        verification_status=EDAVerificationStatus.VERIFICATION_PASS,
        readback_fingerprint=after.content_fingerprint,
        verification_fingerprint="sha256:" + "4" * 64,
        created_at=NOW,
    )
    assert receipt.readback_status is EDAReadBackStatus.READ_BACK_PASS


def test_verification_cannot_pass_without_readback() -> None:
    with pytest.raises(ValueError, match="read-back pass"):
        EDAOperationReceipt(
            operation_id="eda-op-1",
            backend=EDABackend.KICAD,
            operation_fingerprint=FP,
            artifact_before=board(),
            artifact_after=board("sha256:" + "2" * 64),
            approval_fingerprint="sha256:" + "3" * 64,
            execution_status=EDAExecutionStatus.EXECUTION_SUCCESS,
            readback_status=EDAReadBackStatus.READ_BACK_FAIL,
            verification_status=EDAVerificationStatus.VERIFICATION_PASS,
            readback_fingerprint=None,
            verification_fingerprint="sha256:" + "4" * 64,
            created_at=NOW,
        )

