"""Offline canonical ERC status projection; GET never grants or enqueues."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from embedded_copilot.api.copilot_execution_models import OperationStatusResponse
from embedded_copilot.api.copilot_execution_routes import router
from embedded_copilot.execution_worker.contracts import (
    OperationStatus,
    OperationStatusView,
    RecoveryState,
    SchematicErcOperation,
)
from embedded_copilot.project_catalog import ProjectCatalogForbidden
from tests.execution_worker.test_schematic_erc_g4 import _operation

HEADERS = {"X-Copilot-Requester": "engineer-g4", "X-Environment-Tenant": "tenant-g4"}
URL = "/api/v1/copilot/operations/erc-operation-g4/status?project_id=project-g4"


def canonical(**updates):
    now = datetime.now(UTC)
    return SchematicErcOperation(
        **{
            **_operation().model_dump(mode="python", exclude={"fingerprint"}),
            "created_at": now,
            "expires_at": now + timedelta(minutes=10),
            "source_parse_operation_id": "parse-canonical",
            "source_parse_receipt_fingerprint": "sha256:" + "b" * 64,
            **updates,
        }
    )


class Port:
    def __init__(self, operation):
        self.operation = operation
        self.status = OperationStatusView(
            operation_id="erc-operation-g4",
            tenant_id="tenant-g4",
            project_id="project-g4",
            operation_type=operation.operation_type,
            status=OperationStatus.WAIT_APPROVAL,
            approval_id=None,
            worker_id=None,
            recovery_state=RecoveryState.NONE,
            cancellation_requested=False,
            updated_at=datetime.now(UTC),
            safe_detail="awaiting approval",
        )
        self.reads = []

    def get_status(self, **scope):
        return self.status

    def get_operation(self, **scope):
        self.reads.append(scope)
        return self.operation

    def issue_approval_for_id(self, **scope):
        raise AssertionError("GET cannot approve")

    def enqueue_by_id(self, **scope):
        raise AssertionError("GET cannot enqueue")


class Catalog:
    def __init__(self):
        self.current = True

    def get_project_for_requester(self, project_id, *, tenant_id, requester):
        if (project_id, tenant_id, requester) != (
            "project-g4",
            "tenant-g4",
            "engineer-g4",
        ):
            raise ProjectCatalogForbidden("wrong Owner")

    def revalidate_schematic_evidence_operation(self, operation):
        return self.current


def app(port, catalog):
    value = FastAPI()
    value.state.execution_operation_port = port
    value.state.project_catalog_runtime = catalog
    value.include_router(router)
    return value


def test_get_projects_exact_canonical_fields_with_owner_currentness_without_mutation():
    operation = canonical()
    port, catalog = Port(operation), Catalog()
    with TestClient(app(port, catalog)) as client:
        response = client.get(URL, headers=HEADERS)
    assert response.status_code == 200, response.text
    context = response.json()["schematic_approval_context"]
    names = (
        "fingerprint",
        "requester_id",
        "expires_at",
        "conversation_id",
        "schematic_artifact_fingerprint",
        "source_operation_id",
        "source_operation_receipt_fingerprint",
        "source_parse_operation_id",
        "source_parse_receipt_fingerprint",
        "proposal_id",
        "proposal_fingerprint",
    )
    assert context == {key: operation.model_dump(mode="json")[key] for key in names}
    assert port.reads == [
        {
            "tenant_id": "tenant-g4",
            "project_id": "project-g4",
            "operation_id": "erc-operation-g4",
        }
    ]
    with pytest.raises(ValidationError):
        OperationStatusResponse.model_validate(
            {
                **response.json(),
                "schematic_approval_context": {**context, "command": "forbidden"},
            },
            strict=False,
        )


@pytest.mark.parametrize(
    "case",
    [
        "legacy",
        "missing",
        "no-parse",
        "expired",
        "cancelled",
        "recovery",
        "stale",
        "running",
    ],
)
def test_unavailable_context_cannot_be_used_as_an_approval_entry(case):
    operation = canonical()
    if case == "no-parse":
        operation = canonical(
            source_parse_operation_id=None, source_parse_receipt_fingerprint=None
        )
    if case == "expired":
        operation = canonical(
            created_at=datetime.now(UTC) - timedelta(minutes=20),
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    port, catalog = Port(operation), Catalog()
    if case == "legacy":
        port.get_operation = None
    if case == "missing":
        port.operation = None
    if case == "cancelled":
        port.status = OperationStatusView(
            **{
                **port.status.model_dump(mode="python", exclude={"fingerprint"}),
                "cancellation_requested": True,
            }
        )
    if case == "recovery":
        port.status = OperationStatusView(
            **{
                **port.status.model_dump(mode="python", exclude={"fingerprint"}),
                "recovery_state": RecoveryState.TARGET_STATE_UNKNOWN,
            }
        )
    if case == "running":
        port.status = OperationStatusView(
            **{
                **port.status.model_dump(mode="python", exclude={"fingerprint"}),
                "status": OperationStatus.RUNNING,
            }
        )
    if case == "stale":
        catalog.current = False
    with TestClient(app(port, catalog)) as client:
        response = client.get(URL, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json().get("schematic_approval_context") is None


@pytest.mark.parametrize(
    "field,value",
    [("tenant_id", "foreign"), ("project_id", "foreign"), ("operation_id", "foreign")],
)
def test_foreign_canonical_object_is_never_projected(field, value):
    port, catalog = Port(canonical(**{field: value})), Catalog()
    with TestClient(app(port, catalog)) as client:
        response = client.get(URL, headers=HEADERS)
    assert response.status_code == 422
    assert "schematic_approval_context" not in response.text


def test_same_tenant_other_owner_and_anonymous_read_no_canonical_object():
    port, catalog = Port(canonical()), Catalog()
    with TestClient(app(port, catalog)) as client:
        assert (
            client.get(
                URL, headers={**HEADERS, "X-Copilot-Requester": "other"}
            ).status_code
            == 403
        )
        assert client.get(URL).status_code == 401
    assert port.reads == []


def test_legacy_erc_direct_approval_api_rejects_without_grant_or_queue(tmp_path):
    from embedded_copilot.execution_worker.service import ExecutionOperationService
    from embedded_copilot.execution_worker.store import SQLiteExecutionStore

    operation = canonical(
        source_parse_operation_id=None, source_parse_receipt_fingerprint=None
    )
    store = SQLiteExecutionStore(tmp_path / "execution.sqlite3")
    original_status = Port(operation).status
    store.create_operation_transition(
        operation, original_status, occurred_at=original_status.updated_at
    )
    service = ExecutionOperationService(
        store=store, schematic_evidence_authorizer=lambda _operation: True
    )
    with TestClient(app(service, Catalog())) as client:
        response = client.post(
            "/api/v1/copilot/operations/erc-operation-g4/approval?project_id=project-g4",
            headers=HEADERS,
            json={
                "reviewer_id": "engineer-g4",
                "nonce": "legacy-direct-post",
                "expires_at": (datetime.now(UTC) + timedelta(minutes=2)).isoformat(),
            },
        )
    assert response.status_code == 422, response.text
    assert response.json()["error"] == "EXECUTION_APPROVAL_REJECTED"
    assert (
        store.get_status(
            operation.tenant_id, operation.project_id, operation.operation_id
        )
        == original_status
    )
    with store._connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM v66_objects WHERE kind='approval'"
            ).fetchone()[0]
            == 0
        )
        assert connection.execute("SELECT COUNT(*) FROM v66_queue").fetchone()[0] == 0
