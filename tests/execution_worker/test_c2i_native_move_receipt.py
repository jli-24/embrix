"""A fake C2-I result cannot be projected as Native verification evidence."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json

import pytest

from embedded_copilot.execution_worker.contracts import (
    NativeMoveOfflineResult,
    OperationReceipt,
    OperationStatus,
    OperationType,
)


FP = "sha256:" + "3" * 64


def _receipt(**changes):
    fields = {
        "operation_id": "native-move-1", "tenant_id": "tenant-1", "project_id": "project-1",
        "operation_type": OperationType.KICAD_NATIVE_FOOTPRINT_MOVE,
        "status": OperationStatus.SUCCEEDED, "tool_execution_status": "SUCCEEDED",
        "flash_verification_status": "NOT_APPLICABLE",
        "target_runtime_verification_status": "NOT_APPLICABLE",
        "created_at": datetime(2026, 9, 30, 8, tzinfo=UTC),
        "native_move_offline": NativeMoveOfflineResult(
            candidate_id="native-move-candidate-1", candidate_fingerprint=FP,
            mapping_review_id="mapping-review-1", mapping_review_fingerprint=FP,
            import_revision_id="eda-import-1", import_revision_fingerprint=FP,
            source_set_fingerprint=FP, diff_fingerprint=FP,
            fake_result_status="SUCCEEDED", workspace_apply_status="NOT_RUN",
            native_read_back_status="NOT_RUN", drc_status="NOT_RUN",
            circuit_verified=False,
        ),
    }
    return OperationReceipt(**{**fields, **changes})


def test_c2i_fake_receipt_has_no_native_completion_claim() -> None:
    receipt = _receipt()
    assert receipt.status is OperationStatus.SUCCEEDED
    assert receipt.native_move_offline.native_read_back_status == "NOT_RUN"
    assert receipt.native_move_offline.drc_status == "NOT_RUN"
    assert receipt.native_move_offline.circuit_verified is False
    assert receipt.workspace_mutations == ()


def test_c2i_receipt_rejects_missing_or_forged_native_evidence() -> None:
    with pytest.raises((TypeError, ValueError)):
        _receipt(native_move_offline=None)
    with pytest.raises((TypeError, ValueError)):
        NativeMoveOfflineResult(
            candidate_id="native-move-candidate-1", candidate_fingerprint=FP,
            mapping_review_id="mapping-review-1", mapping_review_fingerprint=FP,
            import_revision_id="eda-import-1", import_revision_fingerprint=FP,
            source_set_fingerprint=FP, diff_fingerprint=FP,
            fake_result_status="SUCCEEDED", workspace_apply_status="APPLIED",
            native_read_back_status="NOT_RUN", drc_status="NOT_RUN", circuit_verified=False,
        )


def test_c2i_additive_receipt_field_preserves_legacy_fingerprint_without_correlation() -> None:
    current = OperationReceipt(
        operation_id="test-legacy-1", tenant_id="tenant-1", project_id="project-1",
        operation_type=OperationType.TEST, status=OperationStatus.SUCCEEDED,
        tool_execution_status="SUCCEEDED", flash_verification_status="NOT_APPLICABLE",
        target_runtime_verification_status="NOT_VERIFIED",
        created_at=datetime(2026, 9, 30, 8, tzinfo=UTC),
    )
    historical = current.model_dump(mode="json", exclude={"fingerprint", "native_move_offline", "correlation"})
    encoded = json.dumps(historical, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    historical["fingerprint"] = "sha256:" + hashlib.sha256(encoded).hexdigest()
    restored = OperationReceipt.model_validate(historical, strict=False)
    assert restored.fingerprint == historical["fingerprint"]
    assert restored.native_move_offline is None and restored.correlation is None
