"""Synthetic legacy records produced by actual before-contract SHA
f25c9b7efe28f31ed1905fe979798f62f63b00ca1d7ab20d67e8fc720b10e3c4.
These are test records, not reconstructed historical project assets.
"""
import json
import sqlite3

import pytest

from embedded_copilot.model_gateway import SQLiteModelGatewayStore
from embedded_copilot.model_gateway.invocation.contracts import (
    ModelExecutionSnapshot,
    ModelInvocationAttempt,
    ModelInvocationReceipt,
)

RECORDS = json.loads(r'''
{
  "snapshot": {
    "project_id": "project-1",
    "stage_id": "HARDWARE_SELECTION",
    "selected_model": {
      "provider_id": "relay",
      "endpoint_id": "endpoint-relay",
      "credential_ref": "credential:relay",
      "model_profile_id": "model-relay-firmware",
      "model_id": "firmware-model"
    },
    "connection_id": "fake-connection",
    "credential_revision": "fake-credential-revision",
    "endpoint_revision": "fake-endpoint-revision",
    "policy_source": "STAGE_POLICY",
    "policy_fingerprint": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
    "selection_fingerprint": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
    "fingerprint": "sha256:0826022b6a5290b6cac0ddf391c829dd9cbf21296a5137d184761c22990d841c"
  },
  "attempt1": {
    "attempt_schema_version": 1,
    "project_id": "project-1",
    "request_id": "legacy-1",
    "request_fingerprint": "sha256:4f259cf3f057e37893421bede66f13b6cbab3ce0382d252babaa0892ea6bacb0",
    "snapshot": {
      "project_id": "project-1",
      "stage_id": "HARDWARE_SELECTION",
      "selected_model": {
        "provider_id": "relay",
        "endpoint_id": "endpoint-relay",
        "credential_ref": "credential:relay",
        "model_profile_id": "model-relay-firmware",
        "model_id": "firmware-model"
      },
      "connection_id": "fake-connection",
      "credential_revision": "fake-credential-revision",
      "endpoint_revision": "fake-endpoint-revision",
      "policy_source": "STAGE_POLICY",
      "policy_fingerprint": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
      "selection_fingerprint": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
      "fingerprint": "sha256:0826022b6a5290b6cac0ddf391c829dd9cbf21296a5137d184761c22990d841c"
    },
    "status": "PREPARED",
    "confirmed_provider_request_count": null,
    "invocation_mode": null,
    "authority_reference": null,
    "gateway_call_count": null,
    "gateway_response_observed": false,
    "transport_send_count": null,
    "receipt_fingerprint": null,
    "fingerprint": "sha256:4486bd2c01260543582c188c725ec5df89b3aefe84a1f94fea63192aa6a0d030"
  },
  "attempt2": {
    "attempt_schema_version": 2,
    "project_id": "project-1",
    "request_id": "legacy-2",
    "request_fingerprint": "sha256:4f259cf3f057e37893421bede66f13b6cbab3ce0382d252babaa0892ea6bacb0",
    "snapshot": {
      "project_id": "project-1",
      "stage_id": "HARDWARE_SELECTION",
      "selected_model": {
        "provider_id": "relay",
        "endpoint_id": "endpoint-relay",
        "credential_ref": "credential:relay",
        "model_profile_id": "model-relay-firmware",
        "model_id": "firmware-model"
      },
      "connection_id": "fake-connection",
      "credential_revision": "fake-credential-revision",
      "endpoint_revision": "fake-endpoint-revision",
      "policy_source": "STAGE_POLICY",
      "policy_fingerprint": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
      "selection_fingerprint": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
      "fingerprint": "sha256:0826022b6a5290b6cac0ddf391c829dd9cbf21296a5137d184761c22990d841c"
    },
    "status": "PREPARED",
    "confirmed_provider_request_count": null,
    "invocation_mode": "CHAT",
    "authority_reference": null,
    "gateway_call_count": null,
    "gateway_response_observed": false,
    "transport_send_count": null,
    "receipt_fingerprint": null,
    "fingerprint": "sha256:02dd704d9c2cb3593b6d7002253e022fd17acca76dbf037a3e19a890ffbb409c"
  },
  "receipt5": {
    "schema_version": 5,
    "request_id": "request-1",
    "trace_context": {
      "trace_id": "request-1",
      "parent_span_id": null,
      "correlation": {
        "request_id": "request-1",
        "trace_id": "request-1",
        "project_id": "project-1",
        "workflow_run_id": null,
        "task_id": null,
        "operation_id": null,
        "approval_id": null
      }
    },
    "project_id": "project-1",
    "stage_id": "FIRMWARE_DEVELOPMENT",
    "invocation_mode": "CHAT",
    "authority_reference": null,
    "task_type": "FIRMWARE",
    "policy_source": "STAGE_POLICY",
    "selection_mode": "FIXED_MODEL",
    "selection_reason": "STAGE_FIXED_MODEL",
    "selected_model": {
      "provider_id": "relay",
      "endpoint_id": "endpoint-relay",
      "credential_ref": "credential:relay",
      "model_profile_id": "model-relay-firmware",
      "model_id": "firmware-model"
    },
    "connection_id": null,
    "candidate_selection_evidence": null,
    "auto_selection_evidence": null,
    "credential_revision": "credential-revision-1",
    "required_capabilities": [],
    "input_modalities": [
      "text"
    ],
    "image_count": 0,
    "gates": [
      {
        "name": "capability",
        "status": "PASS",
        "detail": null
      },
      {
        "name": "availability",
        "status": "PASS",
        "detail": null
      },
      {
        "name": "credential",
        "status": "PASS",
        "detail": null
      },
      {
        "name": "endpoint_health",
        "status": "PASS",
        "detail": null
      },
      {
        "name": "quota",
        "status": "PASS",
        "detail": null
      }
    ],
    "started_at": "2026-09-13T12:00:00Z",
    "completed_at": "2026-09-13T12:00:00Z",
    "latency_ms": 0.0,
    "provider_request_id": null,
    "http_status": null,
    "result": "SUCCESS",
    "error_code": null,
    "provider_request_count": 0,
    "gateway_call_count": 1,
    "gateway_response_observed": true,
    "transport_send_count": null,
    "usage": null,
    "fingerprint": "sha256:aac4853c08deab9c1b99ada1a6395bcb2d55f60c406c257429caf5901920bff0"
  }
}
''')

@pytest.mark.parametrize("name,model", [
    ("snapshot", ModelExecutionSnapshot), ("attempt1", ModelInvocationAttempt),
    ("attempt2", ModelInvocationAttempt), ("receipt5", ModelInvocationReceipt),
])
def test_actual_before_contract_records_keep_original_fingerprints(name, model):
    original = RECORDS[name]
    parsed = model.model_validate_json(json.dumps(original))
    assert parsed.fingerprint == original["fingerprint"]
    assert parsed.model_dump(mode="json") == original


def test_store_reopen_does_not_rewrite_or_upgrade_original_json(tmp_path):
    path = tmp_path/"legacy-budget.db"
    SQLiteModelGatewayStore(path)
    originals = {name:json.dumps(RECORDS[name], separators=(",", ":")) for name in ("attempt1", "attempt2", "receipt5")}
    with sqlite3.connect(path) as db:
        for name in ("attempt1", "attempt2"):
            value = RECORDS[name]
            db.execute("INSERT INTO model_invocation_attempts(project_id,request_id,payload) VALUES(?,?,?)", (value["project_id"], value["request_id"], originals[name]))
        receipt = RECORDS["receipt5"]
        db.execute("INSERT INTO model_invocation_receipts(fingerprint,schema_version,payload,created_at) VALUES(?,?,?,?)", (receipt["fingerprint"], 5, originals["receipt5"], receipt["completed_at"]))
    reopened = SQLiteModelGatewayStore(path)
    assert reopened.get_attempt_for_project("project-1", "legacy-1").attempt_schema_version == 1
    assert reopened.get_attempt_for_project("project-1", "legacy-2").attempt_schema_version == 2
    assert reopened.get_receipt(receipt["fingerprint"]).schema_version == 5
    with sqlite3.connect(path) as db:
        for name in ("attempt1", "attempt2"):
            assert db.execute("SELECT payload FROM model_invocation_attempts WHERE request_id=?", (RECORDS[name]["request_id"],)).fetchone()[0] == originals[name]
        assert db.execute("SELECT payload FROM model_invocation_receipts WHERE fingerprint=?", (receipt["fingerprint"],)).fetchone()[0] == originals["receipt5"]
        assert db.execute("SELECT COUNT(*) FROM model_invocation_budget_claims").fetchone()[0] == 0
