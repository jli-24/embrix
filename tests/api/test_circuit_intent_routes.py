from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import Response

from embedded_copilot.api.desktop_identity import TrustedDesktopIdentity
from embedded_copilot.api.main import create_app
from embedded_copilot.api.project_catalog_routes import router
from embedded_copilot.application.circuit_publication import (
    CircuitPreviewCommand,
    CircuitPublicationService,
)
from embedded_copilot.application.desktop_composition import (
    build_project_first_chat_runtime,
)
from embedded_copilot.application.engineering_runtime_integration import (
    CircuitIntentSpec,
)
from embedded_copilot.core.config import Settings
from embedded_copilot.execution_worker import (
    ExecutionOperationService,
    SQLiteExecutionStore,
)
from embedded_copilot.multimodal.context import ProcessLocalAttachmentBindingRepository
from embedded_copilot.project_catalog import ProjectCatalogForbidden
from embedded_copilot.project_catalog.models import (
    CircuitProposalCandidate,
    ConversationTurn,
    ConversationTurnStatus,
)
from tests.application.fixtures import NOW
from tests.application.test_circuit_publication import (
    _Application,
    _Catalog,
    _execution,
)
from tests.application.test_desktop_composition import _catalog, _project
from tests.application.test_engineering_runtime_integration import (
    FP,
    FixtureQualifiedReceiptReferencePort,
    _runtime,
)


class _OwnerCatalog(_Catalog):
    def get_project_for_requester(
        self, project_id: str, *, tenant_id: str, requester: str,
    ) -> SimpleNamespace:
        if requester != "engineer-1":
            raise ProjectCatalogForbidden("project requester mismatch")
        return super().get_project_for_requester(
            project_id, tenant_id=tenant_id, requester=requester,
        )


class _IdentityProvider:
    def __init__(self, requester: str, tenant_id: str = "default-tenant") -> None:
        self.requester = requester
        self.tenant_id = tenant_id

    def resolve(self, request: Request) -> TrustedDesktopIdentity:
        del request
        return TrustedDesktopIdentity(self.requester, self.tenant_id, "TEST")


def _app(
    service: CircuitPublicationService | None, *, requester: str = "engineer-1",
    tenant_id: str = "default-tenant",
) -> FastAPI:
    application = FastAPI()
    application.state.circuit_publication_service = service
    application.state.desktop_identity_provider = _IdentityProvider(requester, tenant_id)

    @application.middleware("http")
    async def trace(
        request: Request, call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.trace_id = "trace-circuit-api"
        return await call_next(request)

    application.include_router(router)
    return application


def _candidate_and_service() -> tuple[CircuitProposalCandidate, CircuitPublicationService, _OwnerCatalog]:
    application, _, request, routing, spec, receipt_port, context_provider = _runtime(
        receipt_port=FixtureQualifiedReceiptReferencePort()
    )
    turn = ConversationTurn(
        turn_id="turn-1", request_id=request.request_id, trace_id=request.trace_id,
        route="GREENFIELD_DESIGN", status=ConversationTurnStatus.COMPLETED,
        question=request.question, answer_text="Design analysis",
        created_at=NOW, completed_at=NOW,
    )
    detail = SimpleNamespace(
        project_id="project-1", conversation_id="conversation-1",
        fingerprint=FP, turns=(turn,), requirement_context=routing.requirement_context,
        architecture_design=None, design_truth=routing.design_truth,
    )
    catalog = _OwnerCatalog(detail, routing)
    execution, erc_operation_id = _execution()
    service = CircuitPublicationService(
        catalog=catalog,
        application=_Application(application, receipt_port, context_provider),
        execution=execution,
    )
    candidate = service.preview(
        project_id="project-1", tenant_id="default-tenant", requester="engineer-1",
        command=CircuitPreviewCommand(
            conversation_id="conversation-1", turn_id="turn-1",
            circuit_intent=spec, erc_operation_id=erc_operation_id,
            idempotency_key="circuit-route-1",
        ),
    )
    return candidate, service, catalog


def test_circuit_decision_route_binds_owner_and_reviewed_proposal() -> None:
    candidate, service, catalog = _candidate_and_service()
    payload = {
        "proposal_id": candidate.proposal.proposal_id,
        "proposal_fingerprint": candidate.proposal.fingerprint,
        "base_revision": candidate.base_revision,
        "base_fingerprint": candidate.base_fingerprint,
        "context_fingerprint": candidate.context_fingerprint,
        "evidence_receipt_fingerprint": candidate.evidence_reference.receipt_fingerprint,
        "decision": "APPROVE",
    }
    url = "/api/v1/copilot/projects/project-1/circuit-intents/decision"
    with TestClient(_app(service, requester="other-user")) as client:
        forbidden = client.post(url, json=payload)
    assert forbidden.status_code == 403
    assert forbidden.json()["error"] == "PROJECT_CATALOG_FORBIDDEN"

    with TestClient(_app(service)) as client:
        changed = client.post(url, json={
            **payload, "proposal_fingerprint": "sha256:" + "f" * 64,
        })
        approved = client.post(url, json=payload)
    assert changed.status_code == 409
    assert changed.json()["error"] == "PROJECT_CATALOG_CONFLICT"
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPLIED"
    assert approved.json()["revision"] == "r1"
    assert approved.json()["object_fingerprint"] == catalog.publication.circuit.fingerprint


def test_circuit_preview_route_accepts_only_erc_operation_id() -> None:
    candidate, service, _ = _candidate_and_service()
    spec = CircuitIntentSpec.model_validate({
        name: getattr(candidate.proposal.proposed_object, name)
        for name in CircuitIntentSpec.model_fields
    }, strict=False)
    payload = {
        "conversation_id": candidate.conversation_id,
        "turn_id": candidate.turn_id,
        "circuit_intent": spec.model_dump(mode="json"),
        "erc_operation_id": candidate.evidence_reference.receipt_id,
        "idempotency_key": candidate.idempotency_key,
    }
    url = "/api/v1/copilot/projects/project-1/circuit-intents/preview"
    with TestClient(_app(service)) as client:
        valid = client.post(url, json=payload)
        legacy = client.post(url, json={
            **{key: value for key, value in payload.items() if key != "erc_operation_id"},
            "evidence_reference": candidate.evidence_reference.model_dump(mode="json"),
        })
    with TestClient(_app(service, requester="other-user")) as client:
        forbidden = client.post(url, json=payload)
    assert valid.status_code == 200, valid.json()
    assert valid.json()["evidence_receipt_id"] == payload["erc_operation_id"]
    assert legacy.status_code == 422
    assert forbidden.status_code == 403


def test_circuit_decision_route_fails_closed_without_service() -> None:
    candidate, _, _ = _candidate_and_service()
    with TestClient(_app(None)) as client:
        response = client.post("/api/v1/copilot/projects/project-1/circuit-intents/decision", json={
            "proposal_id": candidate.proposal.proposal_id,
            "proposal_fingerprint": candidate.proposal.fingerprint,
            "context_fingerprint": candidate.context_fingerprint,
            "evidence_receipt_fingerprint": candidate.evidence_reference.receipt_fingerprint,
            "decision": "APPROVE",
        })
    assert response.status_code == 503
    assert response.json()["error"] == "CIRCUIT_PUBLICATION_UNAVAILABLE"


def test_circuit_route_uses_production_chat_catalog_and_execution_composition(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    project = _project(catalog)
    execution = ExecutionOperationService(
        store=SQLiteExecutionStore(tmp_path / "state" / "execution.sqlite3")
    )
    chat = build_project_first_chat_runtime(
        database=tmp_path / "state" / "desktop.sqlite3",
        project_catalog=catalog,
        approved_memory_store=None,
        environment=None,
        attachments=ProcessLocalAttachmentBindingRepository(),
        provider_environment={},
        execution_port=execution,
    )
    assert chat is not None
    application = _app(None, tenant_id="tenant-1")
    application.state.project_catalog_runtime = catalog
    application.state.desktop_copilot_chat_runtime = chat
    payload = {
        "proposal_id": "proposal-missing",
        "proposal_fingerprint": FP,
        "context_fingerprint": FP,
        "evidence_receipt_fingerprint": FP,
        "decision": "APPROVE",
    }
    url = f"/api/v1/copilot/projects/{project.project_id}/circuit-intents/decision"
    with TestClient(application) as client:
        unavailable = client.post(url, json=payload)
    assert unavailable.status_code == 503
    assert unavailable.json()["error"] == "CIRCUIT_PUBLICATION_UNAVAILABLE"

    application.state.execution_operation_port = execution
    with TestClient(application) as client:
        missing = client.post(url, json=payload)
    assert missing.status_code == 503
    assert missing.json()["error"] == "PROJECT_CATALOG_UNAVAILABLE"

    application.state.desktop_identity_provider = _IdentityProvider("other-user", "tenant-1")
    with TestClient(application) as client:
        forbidden = client.post(url, json=payload)
    assert forbidden.status_code == 403
    assert forbidden.json()["error"] == "PROJECT_CATALOG_FORBIDDEN"


def test_circuit_route_is_wired_by_real_create_app(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    project = _project(catalog)
    application = create_app(
        settings=Settings(
            environment="test",
            desktop_state_database=tmp_path / "state" / "desktop.sqlite3",
            desktop_project_metadata_directory=tmp_path / "metadata",
            project_root=tmp_path / "projects",
            _env_file=None,
        ),
        project_catalog_runtime=catalog,
        desktop_identity_provider=_IdentityProvider("engineer-1", "tenant-1"),
    )
    payload = {
        "proposal_id": "proposal-missing",
        "proposal_fingerprint": FP,
        "context_fingerprint": FP,
        "evidence_receipt_fingerprint": FP,
        "decision": "APPROVE",
    }
    url = f"/api/v1/copilot/projects/{project.project_id}/circuit-intents/decision"
    with TestClient(application) as client:
        assert application.state.desktop_copilot_chat_runtime is not None
        assert application.state.execution_operation_port is not None
        missing = client.post(url, json=payload)
        application.state.desktop_identity_provider = _IdentityProvider("other-user", "tenant-1")
        forbidden = client.post(url, json=payload)
    assert missing.status_code == 503
    assert missing.json()["error"] == "PROJECT_CATALOG_UNAVAILABLE"
    assert forbidden.status_code == 403
    assert forbidden.json()["error"] == "PROJECT_CATALOG_FORBIDDEN"


def test_real_create_app_route_supports_offline_circuit_preview_review_chain(tmp_path: Path) -> None:
    production_catalog = _catalog(tmp_path)
    _project(production_catalog)
    candidate, offline_service, catalog = _candidate_and_service()
    catalog.candidate = None
    spec = CircuitIntentSpec.model_validate({
        name: getattr(candidate.proposal.proposed_object, name)
        for name in CircuitIntentSpec.model_fields
    }, strict=False)
    application = create_app(
        settings=Settings(
            environment="test",
            desktop_state_database=tmp_path / "state" / "desktop.sqlite3",
            desktop_project_metadata_directory=tmp_path / "metadata",
            project_root=tmp_path / "projects",
            _env_file=None,
        ),
        project_catalog_runtime=production_catalog,
        desktop_identity_provider=_IdentityProvider("engineer-1", "default-tenant"),
    )
    url = "/api/v1/copilot/projects/project-1/circuit-intents"
    with TestClient(application) as client:
        assert application.state.desktop_copilot_chat_runtime is not None
        assert application.state.execution_operation_port is not None
        application.state.project_catalog_runtime = catalog
        application.state.desktop_copilot_chat_runtime = SimpleNamespace(
            application_runtime=offline_service._application
        )
        application.state.execution_operation_port = offline_service._execution
        preview = client.post(f"{url}/preview", json={
            "conversation_id": candidate.conversation_id,
            "turn_id": candidate.turn_id,
            "circuit_intent": spec.model_dump(mode="json"),
            "erc_operation_id": candidate.evidence_reference.receipt_id,
            "idempotency_key": candidate.idempotency_key,
        })
        assert preview.status_code == 200, preview.json()
        shown = preview.json()
        decision = client.post(f"{url}/decision", json={
            "proposal_id": shown["proposal_id"],
            "proposal_fingerprint": shown["proposal_fingerprint"],
            "base_revision": shown["base_revision"],
            "base_fingerprint": shown["base_fingerprint"],
            "context_fingerprint": shown["context_fingerprint"],
            "evidence_receipt_fingerprint": shown["evidence_receipt_fingerprint"],
            "decision": "APPROVE",
        })
    assert decision.status_code == 200, decision.json()
    assert decision.json()["status"] == "APPLIED"
    assert catalog.publication is not None
