from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from embedded_copilot.api.desktop_identity import ProductionLocalSessionIdentityAdapter
from embedded_copilot.api.model_gateway_routes import router
from embedded_copilot.model_gateway import (
    BoundModelIdentity,
    CandidateGroup,
    CandidateSpec,
    CapabilityEvidence,
    CapabilityProvenance,
    CapabilityStatus,
    CredentialProfile,
    DiscoveryStatus,
    EndpointProfile,
    EndpointType,
    EngineeringStage,
    ModelAvailability,
    ModelCapability,
    ModelGatewayManagementService,
    ModelProfile,
    ProtocolFamily,
    ProviderProfile,
    RecordProvenance,
    VerificationStatus,
)
from embedded_copilot.production_release.session import LocalSessionManager
from embedded_copilot.project_catalog import (
    ProjectCatalogForbidden,
    ProjectCatalogRuntime,
)


def _client() -> tuple[TestClient, ModelGatewayManagementService]:
    app = FastAPI()
    app.include_router(router)
    service = ModelGatewayManagementService(clock=lambda: datetime(2026, 9, 12, tzinfo=UTC), id_factory=lambda prefix: f"{prefix}-1")
    service.add_provider(ProviderProfile(provider_id="openai", display_name="OpenAI", supported_protocols=(ProtocolFamily.OPENAI_CHAT,)))
    service.add_endpoint(EndpointProfile(endpoint_id="endpoint-openai", provider_id="openai", display_name="OpenAI", endpoint_type=EndpointType.OFFICIAL, protocol_family=ProtocolFamily.OPENAI_CHAT, base_url="https://api.openai.com/v1", credential_ref="credential:openai"))
    service.add_credential(CredentialProfile(credential_id="credential-openai", provider_id="openai", display_name="Key", secret_ref="credential:openai"))
    service.add_model(ModelProfile(model_profile_id="model-openai-gpt", provider_id="openai", endpoint_id="endpoint-openai", remote_model_id="gpt-test", display_name="GPT test", advertised_family="GPT", capabilities=(ModelCapability.TEXT,), capability_sources=(CapabilityEvidence(capability=ModelCapability.TEXT, provenance=CapabilityProvenance.PROVIDER_METADATA, status=CapabilityStatus.PASS, provider_id="openai", endpoint_id="endpoint-openai", credential_ref="credential:openai", model_profile_id="model-openai-gpt", model_id="gpt-test"),), availability=ModelAvailability.AVAILABLE, verification_status=VerificationStatus.VERIFIED))
    app.state.model_gateway_management_service = service
    sessions = LocalSessionManager()
    token = sessions.exchange(sessions.issue_bootstrap(requester="owner-1", tenant_id="tenant-1"))
    app.state.desktop_identity_provider = ProductionLocalSessionIdentityAdapter(sessions)

    class _Catalog(ProjectCatalogRuntime):
        def __init__(self) -> None:
            pass

        def get_project_for_requester(self, project_id, *, tenant_id, requester):
            if project_id != "project-a" or tenant_id != "tenant-1" or requester != "owner-1":
                raise ProjectCatalogForbidden("project access denied")
            return object()

    app.state.project_catalog_runtime = _Catalog()
    app.state.test_sessions = sessions
    client = TestClient(app)
    client.cookies.set("EmbeddedCopilotSession", token)
    return client, service


def _identity() -> dict[str, str]:
    return {"provider_id": "openai", "endpoint_id": "endpoint-openai", "credential_ref": "credential:openai", "model_profile_id": "model-openai-gpt", "model_id": "gpt-test"}


def test_allowed_policy_checks_existing_and_new_project_before_write() -> None:
    client, service = _client()
    original = client.post("/api/v1/model-gateway/governance/allowed-models", json={
        "scope": "PROJECT", "project_id": "project-a", **_identity(),
    })
    assert original.status_code == 201
    policy_id = original.json()["id"]
    initial = service.allowed_model_policy(policy_id)

    new_scope = client.put(f"/api/v1/model-gateway/governance/allowed-models/{policy_id}", json={
        "scope": "PROJECT", "project_id": "project-b", **_identity(),
    })
    assert new_scope.status_code == 403
    assert service.allowed_model_policy(policy_id) == initial

    client.cookies.clear()
    anonymous = client.patch(f"/api/v1/model-gateway/governance/allowed-models/{policy_id}/enabled?enabled=false")
    assert anonymous.status_code == 401
    assert service.allowed_model_policy(policy_id) == initial

    sessions = client.app.state.test_sessions
    other = sessions.exchange(sessions.issue_bootstrap(requester="other-owner", tenant_id="tenant-1"))
    client.cookies.set("EmbeddedCopilotSession", other)
    old_scope = client.put(f"/api/v1/model-gateway/governance/allowed-models/{policy_id}", json={
        "scope": "GLOBAL", **_identity(),
    })
    assert old_scope.status_code == 403
    enabled = client.patch(f"/api/v1/model-gateway/governance/allowed-models/{policy_id}/enabled?enabled=false")
    assert enabled.status_code == 403
    assert service.allowed_model_policy(policy_id) == initial


def test_project_governance_fails_closed_without_catalog() -> None:
    client, service = _client()
    client.app.state.project_catalog_runtime = None
    before = service.project_default_model_policy("project-a")
    response = client.put("/api/v1/model-gateway/governance/project-defaults/project-a", json={
        "selection_mode": "AUTO",
    })
    assert response.status_code == 503
    assert service.project_default_model_policy("project-a") == before


def test_allowed_model_create_update_enable_disable_and_scope_projection() -> None:
    client, _ = _client()
    payload = {"scope": "GLOBAL", **_identity(), "enabled": True, "priority": 4}
    created = client.post("/api/v1/model-gateway/governance/allowed-models", json=payload)
    assert created.status_code == 201
    policy_id = created.json()["id"]
    disabled = client.patch(f"/api/v1/model-gateway/governance/allowed-models/{policy_id}/enabled?enabled=false")
    assert disabled.status_code == 200 and disabled.json()["enabled"] is False
    updated = client.put(f"/api/v1/model-gateway/governance/allowed-models/{policy_id}", json={**payload, "enabled": True, "priority": 2})
    assert updated.status_code == 200 and updated.json()["priority"] == 2
    listed = client.get("/api/v1/model-gateway/governance/allowed-models")
    assert listed.status_code == 200 and listed.json()[0]["provider_id"] == "openai"
    projected = client.get("/api/v1/model-gateway/governance/available-models?scope=PROJECT&project_id=project-a")
    assert projected.status_code == 200
    assert projected.json()[0]["policy_scope"] == "GLOBAL"

    project_override = client.post(
        "/api/v1/model-gateway/governance/allowed-models",
        json={"scope": "PROJECT", "project_id": "project-a", **_identity(), "enabled": False},
    )
    assert project_override.status_code == 201
    projected_disabled = client.get("/api/v1/model-gateway/governance/available-models?scope=PROJECT&project_id=project-a")
    assert projected_disabled.json()[0]["allowed"] is False
    assert projected_disabled.json()[0]["policy_scope"] == "PROJECT"
    assert projected_disabled.json()[0]["policy_id"] == project_override.json()["id"]


def test_stage_assignment_save_update_delete_and_capability_warning() -> None:
    client, _ = _client()
    allowed = client.post("/api/v1/model-gateway/governance/allowed-models", json={"scope": "GLOBAL", **_identity()})
    assert allowed.status_code == 201
    stage_payload = {"selection_mode": "FIXED_MODEL", "fixed_model": _identity(), "candidate_models": []}
    rejected = client.put("/api/v1/model-gateway/governance/stage-policies/project-a/PCB_REVIEW", json=stage_payload)
    assert rejected.status_code == 422
    assert rejected.json()["detail"] == "MODEL_CAPABILITY_MISMATCH"
    stage_models = client.get("/api/v1/model-gateway/governance/available-models?scope=STAGE&project_id=project-a&stage_id=PCB_REVIEW")
    assert stage_models.status_code == 200
    assert stage_models.json()[0]["capability_warning"] == "MISSING_CAPABILITY:REASONING,VISION"
    auto = client.put(
        "/api/v1/model-gateway/governance/stage-policies/project-a/PCB_REVIEW",
        json={"selection_mode": "AUTO", "candidate_models": [], "candidate_group_id": None, "candidate_group_revision": None},
    )
    assert auto.status_code == 200
    assert auto.json()["selection_mode"] == "AUTO"
    assert auto.json()["candidate_group_id"] is None
    assert auto.json()["candidate_group_revision"] is None
    assert client.delete("/api/v1/model-gateway/governance/stage-policies/project-a/PCB_REVIEW").status_code == 204


def test_stage_fixed_model_legacy_payload_and_response_remain_compatible() -> None:
    client, _ = _client()
    client.post("/api/v1/model-gateway/governance/allowed-models", json={"scope": "GLOBAL", **_identity()})
    url = "/api/v1/model-gateway/governance/stage-policies/project-a/REQUIREMENT_ANALYSIS"

    saved = client.put(
        url,
        json={"selection_mode": "FIXED_MODEL", "fixed_model": _identity(), "candidate_models": [], "candidate_group_id": None, "candidate_group_revision": None},
    )

    assert saved.status_code == 200
    assert saved.json()["fixed_model"] == _identity()
    assert saved.json()["candidate_group_id"] is None
    assert saved.json()["candidate_group_revision"] is None
    listed = client.get("/api/v1/model-gateway/governance/stage-policies/project-a")
    assert listed.status_code == 200
    assert listed.json()[0]["fixed_model"] == _identity()


def test_stage_candidate_group_reference_accepts_valid_group_and_rejects_invalid_bindings() -> None:
    client, service = _client()
    service.add_candidate_group(
        CandidateGroup(
            group_id="group-a",
            stage=EngineeringStage.REQUIREMENT_ANALYSIS,
            candidates=(CandidateSpec(identity=BoundModelIdentity(**_identity()), priority=1),),
            revision=1,
        )
    )
    url = "/api/v1/model-gateway/governance/stage-policies/project-a/REQUIREMENT_ANALYSIS"
    payload = {"mode": "CANDIDATE_GROUP", "candidate_group_id": "group-a", "candidate_group_revision": 1}

    saved = client.put(url, json=payload)

    assert saved.status_code == 200
    assert saved.json()["selection_mode"] == "CANDIDATE_GROUP"
    assert saved.json()["candidate_group_id"] == "group-a"
    assert saved.json()["candidate_group_revision"] == 1
    assert saved.json()["fixed_model"] is None
    assert saved.json()["candidate_models"] == []
    assert client.get("/api/v1/model-gateway/governance/stage-policies/project-a").json()[0]["candidate_group_id"] == "group-a"

    unknown = client.put(url, json={**payload, "candidate_group_id": "missing"})
    revision = client.put(url, json={**payload, "candidate_group_revision": 2})
    invalid_revision = client.put(url, json={**payload, "candidate_group_revision": 0})
    missing_revision = client.put(url, json={"mode": "CANDIDATE_GROUP", "candidate_group_id": "group-a"})
    conflict = client.put(url, json={**payload, "fixed_model": _identity()})
    assert (unknown.status_code, unknown.json()["detail"]) == (422, "MODEL_SELECTION_FAILED")
    assert (revision.status_code, revision.json()["detail"]) == (422, "MODEL_SELECTION_FAILED")
    assert invalid_revision.status_code == 422
    assert missing_revision.status_code == 422
    assert conflict.status_code == 422
    assert service.stage_model_policy("project-a", EngineeringStage.REQUIREMENT_ANALYSIS).candidate_group_revision == 1


def test_provider_endpoint_and_credential_mismatch_fail_closed() -> None:
    client, _ = _client()
    bad = {"scope": "GLOBAL", **{**_identity(), "provider_id": "deepseek"}}
    response = client.post("/api/v1/model-gateway/governance/allowed-models", json=bad)
    assert response.status_code == 422
    assert response.json()["detail"] == "MODEL_PROVIDER_MISMATCH"


def test_project_default_assignment_is_scoped_and_restart_safe() -> None:
    client, service = _client()
    allowed = client.post("/api/v1/model-gateway/governance/allowed-models", json={"scope": "GLOBAL", **_identity()})
    assert allowed.status_code == 201
    payload = {"selection_mode": "FIXED_MODEL", "fixed_model": _identity(), "candidate_models": []}
    saved = client.put("/api/v1/model-gateway/governance/project-defaults/project-a", json=payload)
    assert saved.status_code == 200
    assert saved.json()["project_id"] == "project-a"
    fetched = client.get("/api/v1/model-gateway/governance/project-defaults/project-a")
    assert fetched.status_code == 200
    assert fetched.json()["fixed_model"]["model_profile_id"] == "model-openai-gpt"
    assert client.get("/api/v1/model-gateway/governance/project-defaults/project-b").status_code == 404
    assert service.project_default_model_policy("project-b") is None
    assert client.delete("/api/v1/model-gateway/governance/project-defaults/project-a").status_code == 204


def test_assignment_shape_errors_are_fixed_422_responses() -> None:
    client, _ = _client()
    malformed = {"selection_mode": "FIXED_MODEL", "fixed_model": None, "candidate_models": []}
    stage = client.put("/api/v1/model-gateway/governance/stage-policies/project-a/REQUIREMENT_ANALYSIS", json=malformed)
    project = client.put("/api/v1/model-gateway/governance/project-defaults/project-a", json=malformed)
    assert stage.status_code == 422 and stage.json()["detail"] == "POLICY_INVALID"
    assert project.status_code == 422 and project.json()["detail"] == "POLICY_INVALID"


def test_governance_projection_derives_can_enable_and_keeps_identity_separate_from_availability() -> None:
    client, service = _client()
    model = service.model_profile("model-openai-gpt")
    assert model is not None
    service.models.upsert(model.model_copy(update={"availability": ModelAvailability.MODEL_UNAVAILABLE, "availability_status": ModelAvailability.MODEL_UNAVAILABLE}))

    response = client.get("/api/v1/model-gateway/governance/available-models?scope=GLOBAL")

    assert response.status_code == 200
    row = response.json()[0]
    assert row["identity_status"] == "VALID"
    assert row["availability_status"] == "MODEL_UNAVAILABLE"
    assert row["can_enable"] is False
    assert row["enable_block_reason"] == "MODEL_AVAILABILITY_GATE_FAILED"


def test_policy_revalidates_full_identity_and_does_not_accept_client_can_enable() -> None:
    client, service = _client()
    invalid_identity = {**_identity(), "model_id": "deepseek-chat"}
    mismatch = client.post(
        "/api/v1/model-gateway/governance/allowed-models",
        json={"scope": "GLOBAL", **invalid_identity, "enabled": True},
    )
    forged = client.post(
        "/api/v1/model-gateway/governance/allowed-models",
        json={"scope": "GLOBAL", **_identity(), "enabled": True, "can_enable": True},
    )

    assert mismatch.status_code == 422
    assert mismatch.json()["detail"] == "MODEL_MODEL_ID_MISMATCH"
    assert forged.status_code == 422
    assert service.list_allowed_model_policies() == ()


def test_model_write_cannot_forge_endpoint_discovery_or_verification() -> None:
    client, service = _client()
    source = service.model_profile("model-openai-gpt")
    assert source is not None
    forged = source.model_copy(
        update={
            "model_profile_id": "model-openai-gpt-4o",
            "remote_model_id": "gpt-4o",
            "display_name": "GPT-4o",
            "record_provenance": RecordProvenance.ENDPOINT_DISCOVERY,
            "discovery_status": DiscoveryStatus.DISCOVERED,
            "availability": ModelAvailability.AVAILABLE,
            "availability_status": ModelAvailability.AVAILABLE,
            "verification_status": VerificationStatus.VERIFIED,
        }
    )

    response = client.post("/api/v1/model-gateway/models", json=forged.model_dump(mode="json"))

    assert response.status_code == 201
    assert response.json()["verification_status"] == "UNVERIFIED"
    stored = service.model_profile("model-openai-gpt-4o")
    assert stored is not None
    assert stored.record_provenance.value == "EXPLICIT_CONFIGURATION"


def test_official_catalog_is_separate_metadata_projection() -> None:
    client, _ = _client()

    available = client.get("/api/v1/model-gateway/governance/available-models?scope=GLOBAL")
    catalog = client.get("/api/v1/model-gateway/governance/official-catalog?provider_id=openai")

    assert available.status_code == 200
    assert catalog.status_code == 200
    assert {item["model_id"] for item in available.json()} == {"gpt-test"}
    assert {item["model_id"] for item in catalog.json()} == {"gpt-4o", "gpt-4o-mini"}
    assert all(item["record_type"] == "OFFICIAL_CATALOG" and item["can_enable"] is False for item in catalog.json())
    assert all(item["availability_status"] == "UNVERIFIED" for item in catalog.json())


def test_stages_are_the_ten_backend_values() -> None:
    client, _ = _client()
    response = client.get("/api/v1/model-gateway/governance/stages")

    assert response.status_code == 200
    assert len(response.json()) == 10
    assert {item["stage_id"] for item in response.json()} == {
        "REQUIREMENT_ANALYSIS", "TECH_RESEARCH", "SYSTEM_ARCHITECTURE",
        "HARDWARE_SELECTION", "SCHEMATIC_DESIGN", "PCB_REVIEW",
        "FIRMWARE_DEVELOPMENT", "DEBUGGING", "SIMULATION", "DOCUMENTATION",
    }
