from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from embedded_copilot.api.copilot_workspace_routes import router
from embedded_copilot.api.desktop_identity import TrustedDesktopIdentity
from embedded_copilot.engineering_workspace_apply.models import (
    ApplyApprovalGrant,
    ApplyReceipt,
    ChangePreview,
    ChangeSet,
    OperationStatus,
    RecoveryStatus,
    ValidationStatus,
)


HEADERS = {"X-Copilot-Requester": "engineer-1", "X-Environment-Tenant": "tenant-1"}
FP = "sha256:" + "a" * 64


class _Port:
    def __init__(self) -> None:
        self.changeset = None

    def create_changeset(self, *, tenant_id, requester_id, request):
        assert tenant_id == "tenant-1"
        assert requester_id == "engineer-1"
        self.changeset = type("ChangeSetView", (), {})()
        # The route contract deliberately accepts a canonical object only in production;
        # this fake returns a fully shaped object through the real model in integration.
        raise RuntimeError("fixture does not exercise domain composition")


def _app(port=None) -> FastAPI:
    application = FastAPI()
    application.state.workspace_apply_port = port
    application.state.desktop_identity_provider = type(
        "Provider",
        (),
        {"resolve": lambda self, request: TrustedDesktopIdentity("engineer-1", "tenant-1", "TEST")},
    )()

    @application.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-workspace"
        return await call_next(request)

    application.include_router(router)
    return application


def test_workspace_routes_are_unavailable_without_explicit_controlled_port() -> None:
    with TestClient(_app()) as client:
        response = client.post(
            "/api/v1/copilot/workspace/apply-approvals",
            headers=HEADERS,
            json={
                "project_id": "project-1",
                "changeset_id": "changeset-1",
                "changeset_fingerprint": FP,
                "reviewer_id": "reviewer-1",
                "nonce": "nonce-1",
            },
        )
    assert response.status_code == 503
    assert response.json()["error"] == "WORKSPACE_APPLY_UNAVAILABLE"


def test_workspace_route_rejects_absolute_path_fields_at_validation_boundary() -> None:
    with TestClient(_app(object())) as client:
        response = client.post(
            "/api/v1/copilot/workspace/changesets",
            headers=HEADERS,
            json={
                "project_id": "project-1",
                "target_id": "target-1",
                "proposal_id": "proposal-1",
                "proposal_fingerprint": FP,
                "absolute_path": "C:\\private\\source.c",
            },
        )
    assert response.status_code == 422
