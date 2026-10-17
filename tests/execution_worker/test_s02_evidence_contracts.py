"""Independent schematic operations never inherit write permissions."""
import json
from datetime import timedelta
from pathlib import Path

import pytest

from embedded_copilot.execution_worker.contracts import (
    ApprovalPurpose,
    OperationRequest,
    OperationType,
    SchematicParseOperation,
    SchematicReadBackOperation,
    approval_purpose_for_operation,
)
from tests.execution_worker.test_c2i_native_move_contract import FP, NOW


def operation(model=SchematicParseOperation, **changes):
    fields = {"operation_id": "parse-1", "tenant_id": "tenant-1", "project_id": "project-1",
                  "requester_id": "owner-1", "target_id": "project-root", "target_fingerprint": FP,
                  "workspace_snapshot_id": "snapshot-1", "workspace_fingerprint": FP,
                  "proposal_id": "plan-1", "proposal_fingerprint": FP, "created_at": NOW,
                  "expires_at": NOW + timedelta(minutes=10), "nonce": "parse-nonce",
                  "conversation_id": "conversation-1", "source_write_operation_id": "write-1",
                  "source_write_receipt_fingerprint": FP, "schematic_plan_id": "plan-1",
                  "schematic_plan_fingerprint": FP, "artifact_path": "hardware/main.kicad_sch",
                  "artifact_fingerprint": FP}
    return model(**(fields | changes))


@pytest.mark.parametrize("model", [SchematicParseOperation, SchematicReadBackOperation])
def test_independent_operations_roundtrip_and_have_proposal_approval(model):
    value = operation(model)
    assert value.operation_type not in {OperationType.KICAD_SCHEMATIC_WRITE, OperationType.KICAD_SCHEMATIC_ERC}
    assert approval_purpose_for_operation(value.operation_type) is ApprovalPurpose.PROPOSAL
    assert OperationRequest.model_validate_json(OperationRequest(operation=value).model_dump_json()).operation == value


@pytest.mark.parametrize("changes", [{"artifact_path": "../hardware/main.kicad_sch"},
                                    {"artifact_path": "hardware/other.kicad_sch"},
                                    {"source_write_receipt_fingerprint": "sha256:bad"},
                                    {"schematic_plan_id": "other-plan"}])
def test_independent_operations_reject_unbound_input(changes):
    with pytest.raises((TypeError, ValueError)):
        operation(**changes)


def test_archived_pre_change_operation_and_receipt_keep_exact_fingerprint():
    from embedded_copilot.execution_worker.contracts import (
        OperationReceipt,
        SchematicErcOperation,
    )
    archive = json.loads(Path(__file__).with_name("fixtures").joinpath("s02_legacy_operation_receipt_v1.json").read_text(encoding="utf-8"))
    for name, model in (("operation", SchematicErcOperation), ("receipt", OperationReceipt)):
        restored = model.model_validate_json(json.dumps(archive[name]), strict=False)
        assert restored.fingerprint == archive[name]["fingerprint"]
        assert restored.model_dump(mode="json") == archive[name]
