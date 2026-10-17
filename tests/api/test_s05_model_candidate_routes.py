from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import SecretStr

from embedded_copilot.api.main import create_app
from embedded_copilot.api.project_catalog_routes import (
    _candidate_items,
    _map,
    _schematic_blocked,
    router,
)
from embedded_copilot.application.task_model_authority import CatalogTaskAuthoritySource
from embedded_copilot.component_recommendation import catalog as component_catalog
from embedded_copilot.core.config import Settings
from embedded_copilot.engineering_copilot import (
    ArchitectureDesign,
    ArchitectureDesignStatus,
    HardwareSelectionDecision,
    approve_architecture_design,
    approve_engineering_plan,
    generate_engineering_workflow,
)
from embedded_copilot.engineering_copilot.schematic_planning import (
    SchematicPlanningBlocked,
)
from embedded_copilot.engineering_requirements import (
    generate_architecture_design,
    generate_engineering_plan,
    merge_requirement_context,
)
from embedded_copilot.model_gateway import (
    BoundModelIdentity,
    ModelInvocationService,
    ModelResponse,
    ModelSelectionResult,
    PaidModelPolicy,
    SQLiteModelGatewayStore,
)
from embedded_copilot.model_gateway.invocation.contracts import (
    InvocationMode,
    InvocationStatus,
)
from embedded_copilot.production_release.credentials import InMemoryCredentialProvider
from embedded_copilot.production_release.provider import ProviderConnectionStatus
from embedded_copilot.production_release.runtime import ProductionReleaseRuntime
from embedded_copilot.project_catalog import (
    ProjectCatalogNotFound,
    ProjectCreationMode,
    ProjectCreationRequest,
    ProjectLocationReference,
    ProjectType,
)
from embedded_copilot.project_catalog.models import ConversationDetail
from embedded_copilot.project_catalog.runtime import ProjectCatalogRuntime
from tests.application.test_task_model_authority import _detail
from tests.engineering_copilot.test_hardware_selection_g1 import (
    _catalog as hardware_catalog,
)
from tests.engineering_copilot.test_schematic_planning_g2 import (
    _catalog as schematic_catalog,
)
from tests.model_gateway.test_invocation_service import IDENTITY, _Selection
from tests.production_acceptance.test_v71_production_provider_switching import (
    _transport,
    _UsagePort,
)
from tests.project_catalog.test_s05_model_candidate_runtime import (
    _base_detail,
    _runtime,
    _schematic_detail,
)


def test_only_known_schematic_blocked_reasons_use_structured_409() -> None:
    request = Request({"type": "http", "state": {"trace_id": "trace-test"}, "headers": []})
    for code in (
        "POWER_SOURCE_UNRESOLVED", "POWER_NET_INCOMPLETE",
        "INTERFACE_PROTOCOL_UNRESOLVED", "PIN_BINDING_UNRESOLVED",
    ):
        response = _schematic_blocked(request, code)
        assert response.status_code == 409
        assert json.loads(response.body) == {
            "status": "BLOCKED", "operation_id": "SCHEMATIC_DESIGN_PROPOSAL",
            "blocking_reasons": [code], "reason_code": code,
            "error": code, "trace_id": "trace-test",
        }
        assert _map(request, SchematicPlanningBlocked(code)).status_code == 422
    unknown = _map(request, SchematicPlanningBlocked("UNTRUSTED_REASON"))
    assert unknown.status_code == 422
    assert json.loads(unknown.body)["error"] == "SCHEMATIC_PLANNING_REJECTED"


class Catalog:
    def __init__(self):
        self.detail = _detail()
        self.writes = 0
        self.owner_revoked = False

    def get_project_for_requester(self, project_id, *, tenant_id, requester):
        if (project_id, tenant_id, requester) != ("project-1", "tenant-1", "user-1"):
            raise ProjectCatalogNotFound("project unavailable")
        return object()

    def conversation(self, project_id, conversation_id, *, tenant_id, requester):
        if self.owner_revoked:
            raise ProjectCatalogNotFound("project unavailable")
        if (project_id, conversation_id, tenant_id, requester) != ("project-1", "conversation-1", "tenant-1", "user-1"):
            raise ValueError("not owner")
        return self.detail

    def model_operation_candidate_context(self, project_id, conversation_id, **kwargs):
        kwargs["authority_source"].revalidate(kwargs["authority_reference"])
        if kwargs["operation_id"] == "SCHEMATIC_DESIGN_PROPOSAL":
            return {"nets": [{"net_id": "net-1", "endpoint_binding_ids": ["binding-1"]}], "unresolved": ["termination"], "project_supply_candidate_fingerprint": "sha256:" + "c" * 64, "project_supply_review_fingerprint": "sha256:" + "d" * 64}
        return {"choices": [{"slot_id": "slot-1", "source_id": "part-1", "source_fingerprint": "sha256:" + "a" * 64}], "unresolved": ["bitrate"]}

    def create_hardware_model_candidate_proposal(self, project_id, conversation_id, **kwargs):
        self.writes += 1
        kwargs["authority_source"].revalidate(kwargs["authority_reference"])
        selected = kwargs["candidate_selections"]
        proposal = SimpleNamespace(status=SimpleNamespace(value="REVIEW"), fingerprint="sha256:" + "b" * 64)
        return SimpleNamespace(hardware_selection=SimpleNamespace(proposal=proposal)), selected

    def create_schematic_model_candidate_proposal(self, project_id, conversation_id, **kwargs):
        self.writes += 1
        kwargs["authority_source"].revalidate(kwargs["authority_reference"])
        reviewed = kwargs["review_items"]
        proposal = SimpleNamespace(status=SimpleNamespace(value="REVIEW"), fingerprint="sha256:" + "b" * 64)
        return SimpleNamespace(schematic_planning=SimpleNamespace(proposal=proposal)), reviewed


class Invocation:
    def __init__(self, structured_output=None, *, status=InvocationStatus.SUCCESS):
        self.structured_output = structured_output
        self.status = status
        self.calls = 0
        self.last_request = None

    def invoke(self, request, *, authority_source=None):
        self.calls += 1
        self.last_request = request
        assert request.invocation_mode is InvocationMode.TASK
        assert request.authority_reference == authority_source.revalidate(request.authority_reference)
        return SimpleNamespace(
            status=self.status, error_code="MODEL_CANDIDATE_UNAVAILABLE" if self.status is not InvocationStatus.SUCCESS else None,
            receipt=SimpleNamespace(fingerprint="sha256:" + "c" * 64) if self.status is InvocationStatus.SUCCESS else None,
            response=SimpleNamespace(structured_output=self.structured_output) if self.status is InvocationStatus.SUCCESS else None,
        )


def _client(catalog: Catalog, invocation: Invocation):
    app = FastAPI()
    app.state.project_catalog_runtime = catalog
    app.state.model_invocation_service = invocation

    @app.middleware("http")
    async def trace(request: Request, call_next):
        request.state.trace_id = "trace-1"
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def _body(catalog: Catalog):
    return {"request_id": "request-1", "plan_id": catalog.detail.engineering_plan.plan_id,
            "phase_id": "phase-hardware-design", "plan_task_id": "phase-hardware-design-task-1"}


def test_hardware_model_route_uses_task_authority_and_returns_reviewed_candidate() -> None:
    catalog = Catalog()
    invocation = Invocation('{"selections":[{"slot_id":"slot-1","source_id":"part-1","source_fingerprint":"sha256:' + 'a' * 64 + '"}]}')
    response = _client(catalog, invocation).post(
        "/api/v1/copilot/projects/project-1/conversations/conversation-1/hardware-selection/model-candidate-proposal",
        json=_body(catalog), headers={"X-Copilot-Requester": "user-1", "X-Environment-Tenant": "tenant-1"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "REVIEW"
    assert response.json()["candidate_review_items"] == [["slot-1", "part-1", "sha256:" + "a" * 64]]
    assert catalog.writes == invocation.calls == 1
    assert "part-1" in invocation.last_request.payload.messages[0].content


def test_schematic_model_route_uses_its_own_approved_operation() -> None:
    catalog = Catalog()
    detail = catalog.detail
    requirement = merge_requirement_context(
        detail.requirement_context, project_id=detail.project_id,
        conversation_id=detail.conversation_id, turn_id="turn-schematic",
        utterance="需要原理图", confirmation_requested=True,
    )
    decided_at = datetime(2026, 9, 2, tzinfo=UTC)
    plan, review = approve_engineering_plan(
        generate_engineering_plan(requirement), reviewer="user-1", decided_at=decided_at,
    )
    architecture, architecture_review = approve_architecture_design(
        generate_architecture_design(requirement, plan), reviewer="user-1", decided_at=decided_at,
    )
    catalog.detail = ConversationDetail(**{
        **detail.model_dump(mode="python", exclude={"fingerprint"}),
        "requirement_context": requirement,
        "engineering_plan": plan, "engineering_plan_review": review,
        "architecture_design": architecture, "architecture_review": architecture_review,
        "engineering_workflow": generate_engineering_workflow(architecture, plan),
    })
    invocation = Invocation('{"review_items":[{"net_id":"net-1","endpoint_binding_ids":["binding-1"]}]}')
    response = _client(catalog, invocation).post(
        "/api/v1/copilot/projects/project-1/conversations/conversation-1/schematic-plan/model-candidate-proposal",
        json={**_body(catalog), "phase_id": "phase-pcb-design", "plan_task_id": "phase-pcb-design-task-1"},
        headers={"X-Copilot-Requester": "user-1", "X-Environment-Tenant": "tenant-1"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "REVIEW"
    assert response.json()["stage_id"] == "SCHEMATIC_DESIGN"
    assert response.json()["candidate_review_items"] == [["net-1", ["binding-1"]]]
    assert invocation.last_request.authority_reference.operation_id == "SCHEMATIC_DESIGN_PROPOSAL"
    assert "net-1" in invocation.last_request.payload.messages[0].content
    assert catalog.writes == invocation.calls == 1


def test_hardware_model_route_rejects_wrong_stage_before_gateway() -> None:
    catalog = Catalog()
    invocation = Invocation("{}")
    body = _body(catalog)
    body["stage_id"] = "SCHEMATIC_DESIGN"
    response = _client(catalog, invocation).post(
        "/api/v1/copilot/projects/project-1/conversations/conversation-1/hardware-selection/model-candidate-proposal",
        json=body, headers={"X-Copilot-Requester": "user-1", "X-Environment-Tenant": "tenant-1"},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "TASK_STAGE_BINDING_MISMATCH"
    assert catalog.writes == invocation.calls == 0


def test_hardware_model_route_rejects_malformed_response_without_write() -> None:
    catalog = Catalog()
    invocation = Invocation('{"selections":[],"selections":[]}')
    response = _client(catalog, invocation).post(
        "/api/v1/copilot/projects/project-1/conversations/conversation-1/hardware-selection/model-candidate-proposal",
        json=_body(catalog), headers={"X-Copilot-Requester": "user-1", "X-Environment-Tenant": "tenant-1"},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "MODEL_CANDIDATE_REJECTED"
    assert catalog.writes == 0 and invocation.calls == 1


def test_candidate_parser_rejects_non_finite_and_boolean_ids() -> None:
    for raw in ('{"selections":NaN}', '{"selections":[{"slot_id":true,"source_id":"p","source_fingerprint":"f"}]}'):
        try:
            _candidate_items(raw, "HARDWARE_SELECTION_PROPOSAL")
        except ValueError:
            pass
        else:
            raise AssertionError("invalid candidate accepted")


def test_production_create_app_session_and_single_invocation_boundary(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("<html>desktop</html>", encoding="utf-8")
    project_root = tmp_path / "projects"
    project_root.mkdir()
    runtime = ProductionReleaseRuntime.create(
        Settings(environment="production", production_state_root=tmp_path / "state",
                 project_root=project_root, desktop_static_assets=assets, _env_file=None),
        credentials=InMemoryCredentialProvider(),
    )
    app = create_app(settings=runtime.settings, production_release_runtime=runtime)
    catalog = Catalog()
    invocation = Invocation('{"selections":[{"slot_id":"slot-1","source_id":"part-1","source_fingerprint":"sha256:' + 'a' * 64 + '"}]}')
    path = "/api/v1/copilot/projects/project-1/conversations/conversation-1/hardware-selection/model-candidate-proposal"
    with TestClient(app) as client:
        app.state.project_catalog_runtime = catalog
        assert app.state.model_invocation_service is not None
        # Replace only the bound provider path with an isolated fake after
        # production composition has installed the canonical service.
        app.state.model_invocation_service = invocation
        assert client.post(path, json=_body(catalog)).status_code == 401
        assert invocation.calls == catalog.writes == 0
        nonce = runtime.sessions.issue_bootstrap(requester="user-1", tenant_id="tenant-1")
        assert client.post("/api/v1/copilot/desktop/system/bootstrap/exchange", json={"nonce": nonce}).status_code == 200
        bad_stage = {**_body(catalog), "stage_id": "SCHEMATIC_DESIGN"}
        assert client.post(path, json=bad_stage).status_code == 409
        assert client.post(path.replace("project-1", "project-2"), json=_body(catalog)).status_code in {403, 404}
        assert invocation.calls == catalog.writes == 0
        approved = client.post(path, json=_body(catalog))
        assert approved.status_code == 200, approved.text
        assert invocation.calls == catalog.writes == 1


def test_production_session_with_real_invocation_service_and_fake_gateway(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("<html>desktop</html>", encoding="utf-8")
    project_root = tmp_path / "projects"
    project_root.mkdir()
    runtime = ProductionReleaseRuntime.create(
        Settings(environment="production", production_state_root=tmp_path / "state",
                 project_root=project_root, desktop_static_assets=assets, _env_file=None),
        credentials=InMemoryCredentialProvider(),
    )
    app = create_app(settings=runtime.settings, production_release_runtime=runtime)
    catalog = Catalog()
    structured = '{"selections":[{"slot_id":"slot-1","source_id":"part-1","source_fingerprint":"sha256:' + 'a' * 64 + '"}]}'

    class Gateway:
        calls = 0

        def generate_bound(self, identity, request):
            self.calls += 1
            return ModelResponse(content="Candidate available", structured_output=structured,
                                 model_name=identity.model_id, provider=identity.provider_id,
                                 provider_id=identity.provider_id, endpoint_id=identity.endpoint_id,
                                 credential_ref=identity.credential_ref, request_id=request.request_id,
                                 provenance=request.engineering_context.provenance)

        async def agenerate_bound(self, identity, request):
            return self.generate_bound(identity, request)

    gateway = Gateway()
    class RecordingStore(SQLiteModelGatewayStore):
        failure = None

        def complete_attempt(self, attempt, receipt):
            try:
                return super().complete_attempt(attempt, receipt)
            except Exception as error:
                self.failure = error
                raise

    store = RecordingStore(tmp_path / "isolated-gateway.sqlite3")

    class Selection(_Selection):
        revoke_on_snapshot = False

        def policy_source(self, task_profile):
            return "STAGE_POLICY"

        def execution_snapshot(self, task_profile, selection):
            snapshot = super().execution_snapshot(task_profile, selection)
            if self.revoke_on_snapshot:
                catalog.owner_revoked = True
            return snapshot

    selection = Selection(ModelSelectionResult(selected_model=IDENTITY, selection_reason="PROJECT_FIXED_MODEL"))
    class RecordingService(ModelInvocationService):
        last_result = None

        def invoke(self, request, *, authority_source=None):
            self.last_result = super().invoke(request, authority_source=authority_source)
            return self.last_result

    invocation_service = RecordingService(selection_resolver=selection, gateway=gateway, receipt_store=store)
    path = "/api/v1/copilot/projects/project-1/conversations/conversation-1/hardware-selection/model-candidate-proposal"
    with TestClient(app) as client:
        app.state.project_catalog_runtime = catalog
        app.state.model_invocation_service = invocation_service
        nonce = runtime.sessions.issue_bootstrap(requester="user-1", tenant_id="tenant-1")
        assert client.post("/api/v1/copilot/desktop/system/bootstrap/exchange", json={"nonce": nonce}).status_code == 200
        success = client.post(path, json=_body(catalog))
        assert success.status_code == 503, success.text
        assert success.json()["error"] == "BUDGET_EVIDENCE_UNAVAILABLE"
        assert gateway.calls == catalog.writes == 0
        assert store.get_attempt_for_project("project-1", "request-1").status.value == "PREPARED"
        selection.revoke_on_snapshot = True
        stale = client.post(path, json={**_body(catalog), "request_id": "request-2"})
        assert stale.status_code == 404, stale.text
        assert stale.json()["error"] == "TASK_AUTHORITY_OWNER_UNAVAILABLE"
        assert gateway.calls == catalog.writes == 0


def test_production_session_real_catalog_real_invocation_and_hardware_validator(tmp_path: Path) -> None:
    catalog_root = tmp_path / "catalog"
    catalog_root.mkdir()
    catalog, conversation = _runtime(catalog_root, hardware_catalog())
    detail = _base_detail(conversation)
    catalog.persist_conversation_detail(detail)
    from embedded_copilot.application.task_model_authority import (
        CatalogTaskAuthoritySource,
    )
    source = CatalogTaskAuthoritySource(
        catalog, tenant_id=detail.tenant_id, requester=detail.requester,
        project_id=detail.project_id, conversation_id=detail.conversation_id,
        plan_id=detail.engineering_plan.plan_id, phase_id="phase-hardware-design",
        plan_task_id="phase-hardware-design-task-1", operation_id="HARDWARE_SELECTION_PROPOSAL",
    )
    authority, _ = source.resolve_current()
    preview = catalog.model_operation_candidate_context(
        detail.project_id, detail.conversation_id, tenant_id=detail.tenant_id, requester=detail.requester,
        operation_id="HARDWARE_SELECTION_PROPOSAL", authority_source=source, authority_reference=authority,
    )
    import json
    selections = [next(choice for choice in preview["choices"] if choice["slot_id"] == slot)
                  for slot in preview["required_slots"]]
    structured = json.dumps({"selections": selections})
    assert _candidate_items(structured, "HARDWARE_SELECTION_PROPOSAL")

    class Gateway:
        calls = 0

        def generate_bound(self, identity, request):
            self.calls += 1
            return ModelResponse(content="Candidate available", structured_output=structured,
                                 model_name=identity.model_id, provider=identity.provider_id,
                                 provider_id=identity.provider_id, endpoint_id=identity.endpoint_id,
                                 credential_ref=identity.credential_ref, request_id=request.request_id,
                                 provenance=request.engineering_context.provenance)

        async def agenerate_bound(self, identity, request):
            return self.generate_bound(identity, request)

    class Selection(_Selection):
        def policy_source(self, task_profile):
            return "STAGE_POLICY"

    gateway = Gateway()
    store = SQLiteModelGatewayStore(tmp_path / "isolated-gateway.sqlite3")
    invocation = ModelInvocationService(
        selection_resolver=Selection(ModelSelectionResult(selected_model=IDENTITY, selection_reason="PROJECT_FIXED_MODEL")),
        gateway=gateway, receipt_store=store,
    )
    invocation, guarded_sends = _qualified_candidate_http_service(tmp_path,structured,store)
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("<html>desktop</html>", encoding="utf-8")
    project_root = tmp_path / "release-projects"
    project_root.mkdir()
    release = ProductionReleaseRuntime.create(
        Settings(environment="production", production_state_root=tmp_path / "release-state",
                 project_root=project_root, desktop_static_assets=assets, _env_file=None),
        credentials=InMemoryCredentialProvider(),
    )
    app = create_app(settings=release.settings, production_release_runtime=release)
    path = f"/api/v1/copilot/projects/{detail.project_id}/conversations/{detail.conversation_id}/hardware-selection/model-candidate-proposal"
    body = {"request_id": "request-real-catalog", "plan_id": detail.engineering_plan.plan_id,
            "phase_id": "phase-hardware-design", "plan_task_id": "phase-hardware-design-task-1"}
    with TestClient(app) as client:
        app.state.project_catalog_runtime = catalog
        app.state.model_invocation_service = invocation
        nonce = release.sessions.issue_bootstrap(requester="engineer-1", tenant_id="tenant-1")
        assert client.post("/api/v1/copilot/desktop/system/bootstrap/exchange", json={"nonce": nonce}).status_code == 200
        response = client.post(path, json=body)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "REVIEW"
        assert len(response.json()["candidate_review_items"]) == len(preview["required_slots"])
        assert len(guarded_sends) == 1
        assert catalog.conversation(detail.project_id, detail.conversation_id,
                                    tenant_id="tenant-1", requester="engineer-1").hardware_selection.status.value == "REVIEW"
        attempt = store.get_attempt_for_project(detail.project_id, body["request_id"])
        assert attempt.invocation_mode is InvocationMode.TASK
        assert attempt.authority_reference == authority
        assert attempt.gateway_call_count == 1 and attempt.transport_send_count is None
        assert store.list_receipts_for_project(detail.project_id,request_id=body["request_id"],limit=1)[0].transport_send_count == 1
        assert attempt.attempt_schema_version == 3


def test_production_session_real_catalog_real_invocation_and_schematic_validator(tmp_path: Path) -> None:
    catalog_root = tmp_path / "catalog"
    catalog_root.mkdir()
    catalog, conversation = _runtime(catalog_root, schematic_catalog())
    detail = _schematic_detail(_base_detail(conversation))
    catalog.persist_conversation_detail(detail)
    source = CatalogTaskAuthoritySource(
        catalog, tenant_id=detail.tenant_id, requester=detail.requester,
        project_id=detail.project_id, conversation_id=detail.conversation_id,
        plan_id=detail.engineering_plan.plan_id, phase_id="phase-pcb-design",
        plan_task_id="phase-pcb-design-task-1", operation_id="SCHEMATIC_DESIGN_PROPOSAL",
    )
    authority, _ = source.resolve_current()
    with pytest.raises(SchematicPlanningBlocked, match="POWER_SOURCE_UNRESOLVED"):
        catalog.model_operation_candidate_context(
            detail.project_id, detail.conversation_id, tenant_id=detail.tenant_id, requester=detail.requester,
            operation_id="SCHEMATIC_DESIGN_PROPOSAL", authority_source=source, authority_reference=authority,
        )
    structured = "{}"

    class Gateway:
        calls = 0

        def generate_bound(self, identity, request):
            self.calls += 1
            return ModelResponse(
                content="Candidate available", structured_output=structured,
                model_name=identity.model_id, provider=identity.provider_id,
                provider_id=identity.provider_id, endpoint_id=identity.endpoint_id,
                credential_ref=identity.credential_ref, request_id=request.request_id,
                provenance=request.engineering_context.provenance,
            )

        async def agenerate_bound(self, identity, request):
            return self.generate_bound(identity, request)

    class Selection(_Selection):
        def policy_source(self, task_profile):
            return "STAGE_POLICY"

    gateway = Gateway()
    store = SQLiteModelGatewayStore(tmp_path / "isolated-gateway.sqlite3")
    invocation = ModelInvocationService(
        selection_resolver=Selection(ModelSelectionResult(selected_model=IDENTITY, selection_reason="PROJECT_FIXED_MODEL")),
        gateway=gateway, receipt_store=store,
    )
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("<html>desktop</html>", encoding="utf-8")
    project_root = tmp_path / "release-projects"
    project_root.mkdir()
    release = ProductionReleaseRuntime.create(
        Settings(environment="production", production_state_root=tmp_path / "release-state",
                 project_root=project_root, desktop_static_assets=assets, _env_file=None),
        credentials=InMemoryCredentialProvider(),
    )
    app = create_app(settings=release.settings, production_release_runtime=release)
    path = f"/api/v1/copilot/projects/{detail.project_id}/conversations/{detail.conversation_id}/schematic-plan/model-candidate-proposal"
    body = {"request_id": "request-real-schematic", "plan_id": detail.engineering_plan.plan_id,
            "phase_id": "phase-pcb-design", "plan_task_id": "phase-pcb-design-task-1"}
    with TestClient(app) as client:
        app.state.project_catalog_runtime = catalog
        app.state.model_invocation_service = invocation
        nonce = release.sessions.issue_bootstrap(requester="engineer-1", tenant_id="tenant-1")
        assert client.post("/api/v1/copilot/desktop/system/bootstrap/exchange", json={"nonce": nonce}).status_code == 200
        response = client.post(path, json=body)
        assert response.status_code == 409, response.text
        assert response.json()["status"] == "BLOCKED"
        assert response.json()["reason_code"] == "POWER_SOURCE_UNRESOLVED"
        assert gateway.calls == 0
        saved = catalog.conversation(detail.project_id, detail.conversation_id,
                                     tenant_id="tenant-1", requester="engineer-1")
        assert saved.schematic_planning is None
        attempt = store.get_attempt_for_project(detail.project_id, body["request_id"])
        assert attempt is None


@pytest.mark.parametrize("candidate_case", ["valid", "missing", "stale_fingerprint", "inapplicable_slot"])
def test_source_checkout_default_reaches_hardware_candidate_under_governed_policy(
    tmp_path: Path, candidate_case: str,
) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("<html>desktop</html>", encoding="utf-8")
    projects = tmp_path / "projects"
    projects.mkdir()
    release = ProductionReleaseRuntime.create(
        Settings(environment="production", production_state_root=tmp_path / "release-state",
                 project_root=projects, desktop_static_assets=assets, _env_file=None),
        credentials=InMemoryCredentialProvider(),
    )
    app = create_app(settings=release.settings, production_release_runtime=release)
    with TestClient(app) as client:
        assert app.state.hardware_component_source.records[0].mpn == "STM32F103C8T6"
        catalog = app.state.project_catalog_runtime
        location = catalog.locations(tenant_id="tenant-1", requester="engineer-1")[0]
        preview = catalog.preview(ProjectCreationRequest(
            name="Default STM32", project_type=ProjectType.STM32,
            creation_mode=ProjectCreationMode.CREATE_NEW,
            location=ProjectLocationReference(location_id=location.location_id, fingerprint=location.fingerprint),
        ), tenant_id="tenant-1", requester="engineer-1")
        project = catalog.create(preview.preview_id, preview.fingerprint, confirmed_at=datetime.now(UTC),
                                 tenant_id="tenant-1", requester="engineer-1")
        # This fixture explicitly publishes its existing reviewed record through
        # the owned production host; default startup never publishes package data.
        from embedded_copilot.project_catalog.source_publication import ComponentSourcePublishCommandV1
        record = app.state.hardware_component_source.records[0]
        app.state.source_publication_host.publish(
            tenant_id="tenant-1", project_id=project.project_id, requester="engineer-1",
            command=ComponentSourcePublishCommandV1(
                schema_version=1, request_id="test-default-qualified-source",
                source_id=record.source_id, expected_head_publication_fingerprint=None, record=record,
            ),
        )
        conversation = catalog.create_conversation(project.project_id, tenant_id="tenant-1", requester="engineer-1").conversation
        base = _base_detail(conversation)
        requirement = merge_requirement_context(
            base.requirement_context, project_id=base.project_id, conversation_id=base.conversation_id,
            turn_id="turn-schematic", utterance="需要原理图", confirmation_requested=True,
        )
        plan, plan_review = approve_engineering_plan(
            generate_engineering_plan(requirement), reviewer="engineer-1", decided_at=datetime.now(UTC),
        )
        architecture = generate_architecture_design(requirement, plan)
        controller = next(item for item in architecture.system_components if item.category == "CONTROLLER")
        controller_fields = controller.model_dump(mode="python")
        controller_fields.update(name="STM32F103C8T6", fingerprint="sha256:" + "0" * 64)
        controller = type(controller).create(**controller_fields)
        kept = tuple(item for item in architecture.system_components if item.level.value != "HARDWARE") + (controller,)
        kept_ids = {item.component_id for item in kept}
        boundary_fields = architecture.boundary.model_dump(mode="python")
        boundary_fields.update(hardware_component_ids=(controller.component_id,), fingerprint="sha256:" + "0" * 64)
        boundary = type(architecture.boundary).create(**boundary_fields)
        architecture_fields = architecture.model_dump(mode="python")
        architecture_fields.update(system_components=kept, interfaces=tuple(
            item for item in architecture.interfaces
            if item.source_component_id in kept_ids and item.target_component_id in kept_ids
        ), data_flow=tuple(
            item for item in architecture.data_flow
            if item.source_component_id in kept_ids and item.target_component_id in kept_ids
        ), control_flow=tuple(
            item for item in architecture.control_flow
            if item.source_component_id in kept_ids and item.target_component_id in kept_ids
        ), boundary=boundary, status=ArchitectureDesignStatus.PROPOSED,
            fingerprint="sha256:" + "0" * 64)
        approved_architecture, review = approve_architecture_design(
            ArchitectureDesign.create(**architecture_fields), reviewer="engineer-1", decided_at=datetime.now(UTC),
        )
        detail_fields = base.model_dump(mode="python")
        detail_fields.update(requirement_context=requirement, engineering_plan=plan,
                             engineering_plan_review=plan_review,
                             architecture_design=approved_architecture, architecture_review=review,
                             engineering_workflow=generate_engineering_workflow(approved_architecture, plan),
                             fingerprint="sha256:" + "0" * 64)
        detail = ConversationDetail.model_validate(detail_fields)
        catalog.persist_conversation_detail(detail)
        authority_source = CatalogTaskAuthoritySource(
            catalog, tenant_id=detail.tenant_id, requester=detail.requester,
            project_id=detail.project_id, conversation_id=detail.conversation_id,
            plan_id=detail.engineering_plan.plan_id, phase_id="phase-hardware-design",
            plan_task_id="phase-hardware-design-task-1", operation_id="HARDWARE_SELECTION_PROPOSAL",
        )
        authority, _ = authority_source.resolve_current()
        context = catalog.model_operation_candidate_context(
            detail.project_id, detail.conversation_id, tenant_id=detail.tenant_id,
            requester=detail.requester, operation_id="HARDWARE_SELECTION_PROPOSAL",
            authority_source=authority_source, authority_reference=authority,
        )
        assert len(context["required_slots"]) == 1
        assert len(context["choices"]) == 1
        candidate = dict(context["choices"][0])
        if candidate_case == "missing":
            candidate["source_id"] = "unknown-source"
        elif candidate_case == "stale_fingerprint":
            candidate["source_fingerprint"] = "sha256:" + "0" * 64
        elif candidate_case == "inapplicable_slot":
            candidate["slot_id"] = "unrelated-slot"
        structured = json.dumps({"selections": [candidate]})

        class FakeGateway:
            calls = 0

            def generate_bound(self, identity, request):
                self.calls += 1
                return ModelResponse(
                    content="Candidate available", structured_output=structured,
                    model_name=identity.model_id, provider=identity.provider_id,
                    provider_id=identity.provider_id, endpoint_id=identity.endpoint_id,
                    credential_ref=identity.credential_ref, request_id=request.request_id,
                    provenance=request.engineering_context.provenance,
                )

            async def agenerate_bound(self, identity, request):
                return self.generate_bound(identity, request)

        management = app.state.model_gateway_management_service
        provider_service = app.state.provider_configuration_service
        transport_calls: list[tuple[str, str]] = []
        provider_service._http_transport = _transport(transport_calls)
        provider_service.save_configuration(provider="zhipu", model="glm-4")
        provider_service.save_credential(SecretStr("test-only-secret"), provider="zhipu")
        assert provider_service.test_connection().connection_status is ProviderConnectionStatus.AVAILABLE
        model = management.model_profile(provider_service.projection().model_profile_id or "")
        assert model is not None
        endpoint = management.endpoint_profile(model.endpoint_id)
        assert endpoint is not None
        identity = BoundModelIdentity.from_profile(model, endpoint.credential_ref or "credential:zhipu")
        management.set_account_usage_port(_UsagePort())
        management.set_global_policy(management.global_policy().model_copy(
            update={"paid_model_policy": PaidModelPolicy.ALLOW},
        ))
        store = management.receipt_store
        assert store is not None
        nonce = release.sessions.issue_bootstrap(requester="engineer-1", tenant_id="tenant-1")
        assert client.post("/api/v1/copilot/desktop/system/bootstrap/exchange", json={"nonce": nonce}).status_code == 200
        path = f"/api/v1/copilot/projects/{detail.project_id}/conversations/{detail.conversation_id}/hardware-selection/model-candidate-proposal"
        transport_count = len(transport_calls)
        no_policy = client.post(path, json={
            "request_id": "request-default-no-policy", "plan_id": detail.engineering_plan.plan_id,
            "phase_id": "phase-hardware-design", "plan_task_id": "phase-hardware-design-task-1",
        })
        assert no_policy.status_code == 503, no_policy.text
        assert no_policy.json()["error"] == "MODEL_SELECTION_PENDING_ROUTER"
        assert len(transport_calls) == transport_count
        allowed = client.post("/api/v1/model-gateway/governance/allowed-models", json={
            "scope": "GLOBAL", "project_id": None, "stage_id": None,
            **identity.model_dump(mode="json"),
        })
        assert allowed.status_code == 201, allowed.text
        stage = client.put(
            f"/api/v1/model-gateway/governance/stage-policies/{detail.project_id}/HARDWARE_SELECTION",
            json={"selection_mode": "FIXED_MODEL", "fixed_model": identity.model_dump(mode="json")},
        )
        assert stage.status_code == 200, stage.text
        import httpx
        from tests.model_gateway.test_budget_gate import OfflineQualifiedBudget
        from embedded_copilot.model_gateway.invocation.budget import TrustedBudgetQualification
        guarded_sends=[]
        def stage_response(request):
            guarded_sends.append(request.content)
            return httpx.Response(200,json={"id":"offline-stage","model":identity.model_id,
                "choices":[{"message":{"content":structured}}],
                "usage":{"prompt_tokens":2,"completion_tokens":3,"total_tokens":5}})
        provider_service._http_transport=httpx.MockTransport(stage_response)
        provider_service._refresh_canonical_binding()
        class QualifiedMockHost(OfflineQualifiedBudget):
            def qualify(self,**kwargs):return TrustedBudgetQualification(True,True,True,True)
        actual_gateway=app.state.model_invocation_service._gateway
        app.state.model_invocation_service = ModelInvocationService(
            selection_resolver=app.state.model_selection_resolver, gateway=actual_gateway,
            receipt_store=store,trusted_budget_source=QualifiedMockHost(),
        )
        response = client.post(path, json={
            "request_id": "request-default-source", "plan_id": detail.engineering_plan.plan_id,
            "phase_id": "phase-hardware-design", "plan_task_id": "phase-hardware-design-task-1",
        })
        assert len(guarded_sends) == 1, response.text
        assert len(transport_calls) == transport_count
        if candidate_case != "valid":
            assert response.status_code == 422, response.text
            assert response.json()["error"] == "HARDWARE_SELECTION_REJECTED"
            assert catalog.conversation(detail.project_id, detail.conversation_id,
                                        tenant_id="tenant-1", requester="engineer-1").hardware_selection is None
            return
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "REVIEW"
        saved = catalog.conversation(detail.project_id, detail.conversation_id,
                                     tenant_id="tenant-1", requester="engineer-1")
        assert saved.hardware_selection.proposal.selected_components[0].source.source_id == component_catalog._APPROVED_SOURCE_IDS[0]
        restarted = ProjectCatalogRuntime(
            project_root=release.settings.project_root,
            state_database=release.settings.desktop_state_database,
            hardware_component_source=component_catalog.load_approved_component_source(),
            initialize_async=False,
        )
        read_back = restarted.conversation(detail.project_id, detail.conversation_id,
                                           tenant_id="tenant-1", requester="engineer-1")
        assert read_back.hardware_selection.proposal.fingerprint == saved.hardware_selection.proposal.fingerprint
        assert read_back.hardware_selection.proposal.selected_components[0].source.power_facts[0].nominal_voltage is None
        approved = catalog.decide_hardware_selection(
            detail.project_id, detail.conversation_id, HardwareSelectionDecision.APPROVED,
            tenant_id="tenant-1", requester="engineer-1", decided_at=datetime.now(UTC),
        )
        assert approved.hardware_selection.status.value == "APPROVED"
        blocked = client.post(
            f"/api/v1/copilot/projects/{detail.project_id}/conversations/{detail.conversation_id}/schematic-plan/model-candidate-proposal",
            json={"request_id": "request-default-schematic", "plan_id": detail.engineering_plan.plan_id,
                  "phase_id": "phase-pcb-design", "plan_task_id": "phase-pcb-design-task-1"},
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json().get("blocking_reasons") == ["POWER_SOURCE_UNRESOLVED"], blocked.text
        assert len(guarded_sends) == 1
        assert catalog.conversation(detail.project_id, detail.conversation_id,
                                    tenant_id="tenant-1", requester="engineer-1").schematic_planning is None
        assert store.get_attempt_for_project(detail.project_id, "request-default-schematic") is None


@pytest.mark.parametrize("failure", ["missing", "version", "digest", "source_id"])
def test_source_checkout_default_hardware_candidate_fails_closed_for_invalid_resource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    resource = tmp_path / "invalid-approved-components.json"
    payload = json.loads(component_catalog._approved_resource_path().read_bytes())
    if failure == "version":
        payload["schema_version"] = 2
    elif failure == "source_id":
        payload["records"][0]["source_id"] = "unapproved"
    if failure == "missing":
        packaged_module = tmp_path / "packaged" / "embedded_copilot" / "component_recommendation" / "catalog.py"
        packaged_module.parent.mkdir(parents=True)
        assert component_catalog._approved_resource_path().is_file()
        monkeypatch.setattr(component_catalog, "__file__", str(packaged_module))
    if failure != "missing":
        resource.write_text(json.dumps(payload), encoding="utf-8")
    if failure in {"version", "source_id"}:
        monkeypatch.setattr(component_catalog, "_APPROVED_RESOURCE_SHA256", hashlib.sha256(resource.read_bytes()).hexdigest())
    if failure != "missing":
        monkeypatch.setattr(component_catalog, "_approved_resource_path", lambda: resource)
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("<html>desktop</html>", encoding="utf-8")
    project_root = tmp_path / "projects"
    project_root.mkdir()
    release = ProductionReleaseRuntime.create(
        Settings(environment="production", production_state_root=tmp_path / "release-state",
                 project_root=project_root, desktop_static_assets=assets, _env_file=None),
        credentials=InMemoryCredentialProvider(),
    )
    app = create_app(settings=release.settings, production_release_runtime=release)
    invocation = Invocation("{}")
    with TestClient(app) as client:
        assert app.state.hardware_component_source is None
        catalog = app.state.project_catalog_runtime
        location = catalog.locations(tenant_id="tenant-1", requester="engineer-1")[0]
        preview = catalog.preview(ProjectCreationRequest(
            name="S05 Default Composition", project_type=ProjectType.STM32,
            creation_mode=ProjectCreationMode.CREATE_NEW,
            location=ProjectLocationReference(location_id=location.location_id, fingerprint=location.fingerprint),
        ), tenant_id="tenant-1", requester="engineer-1")
        project = catalog.create(preview.preview_id, preview.fingerprint, confirmed_at=datetime.now(UTC),
                                 tenant_id="tenant-1", requester="engineer-1")
        conversation = catalog.create_conversation(project.project_id, tenant_id="tenant-1", requester="engineer-1").conversation
        detail = _base_detail(conversation)
        catalog.persist_conversation_detail(detail)
        path = f"/api/v1/copilot/projects/{detail.project_id}/conversations/{detail.conversation_id}/hardware-selection/model-candidate-proposal"
        body = {"request_id": "request-no-source", "plan_id": detail.engineering_plan.plan_id,
                "phase_id": "phase-hardware-design", "plan_task_id": "phase-hardware-design-task-1"}
        app.state.model_invocation_service = invocation
        nonce = release.sessions.issue_bootstrap(requester="engineer-1", tenant_id="tenant-1")
        assert client.post("/api/v1/copilot/desktop/system/bootstrap/exchange", json={"nonce": nonce}).status_code == 200
        response = client.post(path, json=body)
        assert response.status_code == 422, response.text
        assert response.json()["error"] == "HARDWARE_SELECTION_REJECTED"
        assert invocation.calls == 0
        assert catalog.conversation(
            detail.project_id, detail.conversation_id,
            tenant_id="tenant-1", requester="engineer-1",
        ).hardware_selection is None


def _p1_presend_schematic_catalog():
    catalog = Catalog()
    detail = catalog.detail
    requirement = merge_requirement_context(
        detail.requirement_context, project_id=detail.project_id,
        conversation_id=detail.conversation_id, turn_id="turn-schematic",
        utterance="需要原理图", confirmation_requested=True,
    )
    now = datetime(2026, 9, 2, tzinfo=UTC)
    plan, review = approve_engineering_plan(
        generate_engineering_plan(requirement), reviewer="user-1", decided_at=now,
    )
    architecture, architecture_review = approve_architecture_design(
        generate_architecture_design(requirement, plan), reviewer="user-1", decided_at=now,
    )
    catalog.detail = ConversationDetail(**{
        **detail.model_dump(mode="python", exclude={"fingerprint"}),
        "requirement_context": requirement,
        "engineering_plan": plan, "engineering_plan_review": review,
        "architecture_design": architecture, "architecture_review": architecture_review,
        "engineering_workflow": generate_engineering_workflow(architecture, plan),
    })
    return catalog


def test_schematic_model_route_rechecks_p1_before_invocation(tmp_path):
    class DriftingCatalog(Catalog):
        def __init__(self):
            super().__init__()
            self.context_reads = 0
        def model_operation_candidate_context(self, project_id, conversation_id, **kwargs):
            context = super().model_operation_candidate_context(project_id, conversation_id, **kwargs)
            if kwargs["operation_id"] == "SCHEMATIC_DESIGN_PROPOSAL":
                self.context_reads += 1
                if self.context_reads >= 2:
                    return {**context, "project_supply_review_fingerprint": "sha256:" + "e" * 64}
            return context
    catalog = DriftingCatalog()
    catalog.detail = _p1_presend_schematic_catalog().detail
    invocation = Invocation('{}')
    response = _client(catalog, invocation).post(
        "/api/v1/copilot/projects/project-1/conversations/conversation-1/schematic-plan/model-candidate-proposal",
        json={**_body(catalog), "phase_id": "phase-pcb-design", "plan_task_id": "phase-pcb-design-task-1"},
        headers={"X-Copilot-Requester": "user-1", "X-Environment-Tenant": "tenant-1"},
    )
    assert response.status_code == 409
    assert catalog.context_reads >= 2
    assert invocation.calls == catalog.writes == 0


def test_schematic_p1_drift_during_governed_selection_blocks_provider(tmp_path):
    class ChangingCatalog(Catalog):
        drift = False
        def model_operation_candidate_context(self, project_id, conversation_id, **kwargs):
            context = super().model_operation_candidate_context(project_id, conversation_id, **kwargs)
            if self.drift and kwargs["operation_id"] == "SCHEMATIC_DESIGN_PROPOSAL":
                return {**context, "project_supply_review_fingerprint": "sha256:" + "e" * 64}
            return context
    catalog = ChangingCatalog()
    catalog.detail = _p1_presend_schematic_catalog().detail
    class Selection(_Selection):
        def policy_source(self, task_profile):
            return "STAGE_POLICY"
        def select_governed_model(self, task_profile, *, request_override=None):
            result = super().select_governed_model(task_profile, request_override=request_override)
            catalog.drift = True
            return result
    class Gateway:
        calls = 0
        def generate_bound(self, identity, request):
            self.calls += 1
            raise AssertionError("P1 drift reached provider")
        async def agenerate_bound(self, identity, request):
            return self.generate_bound(identity, request)
    gateway = Gateway()
    store = SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3")
    invocation = ModelInvocationService(
        selection_resolver=Selection(ModelSelectionResult(
            selected_model=IDENTITY, selection_reason="PROJECT_FIXED_MODEL",
        )), gateway=gateway, receipt_store=store,
    )
    response = _client(catalog, invocation).post(
        "/api/v1/copilot/projects/project-1/conversations/conversation-1/schematic-plan/model-candidate-proposal",
        json={**_body(catalog), "request_id": "request-p1-drift",
              "phase_id": "phase-pcb-design", "plan_task_id": "phase-pcb-design-task-1"},
        headers={"X-Copilot-Requester": "user-1", "X-Environment-Tenant": "tenant-1"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"] == "TASK_AUTHORITY_STALE"
    assert gateway.calls == catalog.writes == 0
    attempt = store.get_attempt_for_project("project-1", "request-p1-drift")
    assert attempt is not None
    assert attempt.status.value == "PREPARED"
    catalog.drift = False
    retry = _client(catalog, invocation).post(
        "/api/v1/copilot/projects/project-1/conversations/conversation-1/schematic-plan/model-candidate-proposal",
        json={**_body(catalog), "request_id": "request-p1-drift",
              "phase_id": "phase-pcb-design", "plan_task_id": "phase-pcb-design-task-1"},
        headers={"X-Copilot-Requester": "user-1", "X-Environment-Tenant": "tenant-1"},
    )
    assert retry.status_code == 503, retry.text
    assert retry.json()["error"] == "MODEL_INVOCATION_ALREADY_ATTEMPTED"
    assert gateway.calls == 0


def test_schematic_p1_drift_during_second_execution_snapshot_blocks_provider(tmp_path):
    class ChangingCatalog(Catalog):
        drift = False
        def model_operation_candidate_context(self, project_id, conversation_id, **kwargs):
            context = super().model_operation_candidate_context(project_id, conversation_id, **kwargs)
            if self.drift and kwargs["operation_id"] == "SCHEMATIC_DESIGN_PROPOSAL":
                return {**context, "project_supply_review_fingerprint": "sha256:" + "e" * 64}
            return context
    catalog = ChangingCatalog()
    catalog.detail = _p1_presend_schematic_catalog().detail
    class Selection(_Selection):
        snapshot_calls = 0
        def policy_source(self, task_profile):
            return "STAGE_POLICY"
        def execution_snapshot(self, task_profile, selection):
            snapshot = super().execution_snapshot(task_profile, selection)
            self.snapshot_calls += 1
            if self.snapshot_calls == 2:
                catalog.drift = True
            return snapshot
    class Gateway:
        calls = 0
        def generate_bound(self, identity, request):
            self.calls += 1
            raise AssertionError("P1 drift reached provider")
        async def agenerate_bound(self, identity, request):
            return self.generate_bound(identity, request)
    gateway = Gateway()
    store = SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3")
    selection = Selection(ModelSelectionResult(
        selected_model=IDENTITY, selection_reason="PROJECT_FIXED_MODEL",
    ))
    invocation = ModelInvocationService(
        selection_resolver=selection, gateway=gateway, receipt_store=store,
    )
    response = _client(catalog, invocation).post(
        "/api/v1/copilot/projects/project-1/conversations/conversation-1/schematic-plan/model-candidate-proposal",
        json={**_body(catalog), "request_id": "request-p1-snapshot-drift",
              "phase_id": "phase-pcb-design", "plan_task_id": "phase-pcb-design-task-1"},
        headers={"X-Copilot-Requester": "user-1", "X-Environment-Tenant": "tenant-1"},
    )
    assert selection.snapshot_calls >= 2
    assert response.status_code == 409, response.text
    assert response.json()["error"] == "TASK_AUTHORITY_STALE"
    assert gateway.calls == catalog.writes == 0
    attempt = store.get_attempt_for_project("project-1", "request-p1-snapshot-drift")
    assert attempt is not None and attempt.status.value == "PREPARED"


@pytest.mark.parametrize("drift", ["review", "circuit"])
def test_real_catalog_p1_review_or_circuit_drift_after_context_blocks_provider(tmp_path, monkeypatch, drift):
    from tests.project_catalog.test_p1_project_supply import _approved_runtime

    catalog, detail, relation, selectors, _circuit, _source_port = _approved_runtime(tmp_path, monkeypatch)
    state = catalog.create_project_supply_candidate(
        detail.project_id, detail.conversation_id, tenant_id=detail.tenant_id,
        requester=detail.requester, selectors=selectors, relations=(relation,),
    )
    catalog.review_project_supply_candidate(
        detail.project_id, detail.conversation_id,
        state.candidate.candidate_id, state.candidate.fingerprint, "APPROVED",
        tenant_id=detail.tenant_id, requester=detail.requester,
    )
    original_context = catalog.model_operation_candidate_context
    context_reads = 0
    def drift_after_first_read(*args, **kwargs):
        nonlocal context_reads
        context_reads += 1
        context = original_context(*args, **kwargs)
        if context_reads == 1:
            if drift == "review":
                replaced = {**relation, "evidence_refs": tuple(reversed(relation["evidence_refs"]))}
                newer = catalog.create_project_supply_candidate(
                    detail.project_id, detail.conversation_id, tenant_id=detail.tenant_id,
                    requester=detail.requester, selectors=selectors, relations=(replaced,),
                )
                catalog.review_project_supply_candidate(
                    detail.project_id, detail.conversation_id,
                    newer.candidate.candidate_id, newer.candidate.fingerprint, "REJECTED",
                    tenant_id=detail.tenant_id, requester=detail.requester,
                )
            else:
                monkeypatch.setattr(catalog, "current_circuit_intents", lambda *a, **k: ())
        return context
    monkeypatch.setattr(catalog, "model_operation_candidate_context", drift_after_first_read)
    invocation = Invocation('{}')
    response = _client(catalog, invocation).post(
        f"/api/v1/copilot/projects/{detail.project_id}/conversations/{detail.conversation_id}/schematic-plan/model-candidate-proposal",
        json={"request_id": "request-p1-real-drift", "plan_id": detail.engineering_plan.plan_id,
              "phase_id": "phase-pcb-design", "plan_task_id": "phase-pcb-design-task-1"},
        headers={"X-Copilot-Requester": detail.requester, "X-Environment-Tenant": detail.tenant_id},
    )
    assert response.status_code in {409, 422}, response.text
    assert context_reads >= 2
    assert invocation.calls == 0



def _qualified_candidate_http_service(tmp_path,structured,store):
    """Explicit mock host/transport fixture; never qualifies production resources."""
    import httpx
    from tests.model_gateway.test_budget_gate import BudgetSelection,OfflineQualifiedBudget
    from embedded_copilot.model_gateway import ModelGatewayService
    from embedded_copilot.model_gateway.adapters.protocol_http import ProtocolHTTPProviderAdapter
    from embedded_copilot.model_gateway.transport import ResolvedTransportEntry
    from embedded_copilot.model_gateway.profiles import ProtocolFamily
    calls=[]
    def handler(request):
        calls.append(request.content)
        return httpx.Response(200,json={"id":"offline-stage","model":IDENTITY.model_id,"output_text":structured,
            "usage":{"input_tokens":2,"output_tokens":3,"total_tokens":5}})
    transport=httpx.MockTransport(handler)
    adapter=ProtocolHTTPProviderAdapter(provider=IDENTITY.provider_id,endpoint_id=IDENTITY.endpoint_id,
        base_url="https://offline.invalid/v1",protocol_family=ProtocolFamily.OPENAI_RESPONSES,
        api_key="offline-fixture-placeholder",http_transport=transport,async_transport=transport)
    entry=ResolvedTransportEntry(provider_id=IDENTITY.provider_id,endpoint_id=IDENTITY.endpoint_id,credential_ref=IDENTITY.credential_ref,
        adapter=adapter,connection_id="fake-connection",credential_revision="fake-credential-revision",endpoint_revision="fake-endpoint-revision")
    gateway=ModelGatewayService(providers={IDENTITY.provider_id:adapter},transport_entries=(entry,))
    class Selection(BudgetSelection):
        def policy_source(self,task_profile):return "STAGE_POLICY"
    service=ModelInvocationService(selection_resolver=Selection(ModelSelectionResult(selected_model=IDENTITY,selection_reason="PROJECT_FIXED_MODEL")),
        gateway=gateway,receipt_store=store,trusted_budget_source=OfflineQualifiedBudget())
    return service,calls
