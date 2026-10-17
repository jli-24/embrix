"""C2-I typed operation binds one immutable C1 observation and mapping review."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from embedded_copilot.execution_worker.contracts import (
    KiCadNativeFootprintMoveOperation,
    OperationRequest,
    OperationType,
    native_move_operation_id,
    native_move_operation_nonce,
)


FP = "sha256:" + "2" * 64
NOW = datetime(2026, 9, 30, 8, tzinfo=UTC)


def _operation(**changes):
    candidate_id = "native-move-1"
    review_id = "mapping-review-1"
    fields = {
        "operation_id": native_move_operation_id("tenant-1", "project-1", candidate_id, FP, review_id, FP),
        "tenant_id": "tenant-1", "project_id": "project-1", "requester_id": "owner-1",
        "target_id": "project-root", "target_fingerprint": FP,
        "workspace_snapshot_id": "workspace-snapshot-1", "workspace_fingerprint": FP,
        "proposal_id": candidate_id, "proposal_fingerprint": FP,
        "operation_type": OperationType.KICAD_NATIVE_FOOTPRINT_MOVE,
        "created_at": NOW, "expires_at": NOW + timedelta(minutes=10),
        "nonce": native_move_operation_nonce(review_id, FP),
        "mapping_review_id": review_id, "mapping_review_fingerprint": FP,
        "mapping_reviewed_at": NOW,
        "import_revision_id": "eda-import-1", "import_revision_fingerprint": FP,
        "source_set_fingerprint": FP, "manifest_fingerprint": FP,
        "parser_ruleset_id": "kicad-pcb-footprint-20240108-r1", "parser_ruleset_fingerprint": FP,
        "supported_subset_id": "kicad-footprint-placement-r1", "supported_subset_fingerprint": FP,
        "native_entry_relative_path": "board.kicad_pro", "pcb_relative_path": "board.kicad_pcb",
        "pcb_sha256": FP,
        "native_uuid": "01234567-89ab-4cde-8f01-23456789abcd",
        "native_locator": "/kicad_pcb/footprint[0]",
        "before_x_mm": 10.0, "before_y_mm": 20.0,
        "after_x_mm": 11.0, "after_y_mm": 20.0, "rotation_degrees": 90.0,
        "diff_fingerprint": FP,
    }
    return KiCadNativeFootprintMoveOperation(**{**fields, **changes})


def test_c2i_operation_is_distinct_from_legacy_pcb_write() -> None:
    operation = _operation()
    assert OperationRequest(operation=operation).operation.operation_type is OperationType.KICAD_NATIVE_FOOTPRINT_MOVE
    assert operation.proposal_id == "native-move-1"
    assert operation.mapping_review_id == "mapping-review-1"


@pytest.mark.parametrize("change", [
    {"operation_id": "native-move-forged"},
    {"nonce": "forged-nonce"},
    {"created_at": NOW + timedelta(seconds=1)},
    {"pcb_relative_path": "../board.kicad_pcb"},
    {"native_uuid": "missing"},
    {"before_x_mm": float("nan")},
    {"after_y_mm": float("inf")},
    {"parser_ruleset_id": "inventory-only-r1"},
])
def test_c2i_operation_rejects_unbound_or_unsupported_input(change: dict) -> None:
    with pytest.raises((TypeError, ValueError)):
        _operation(**change)
