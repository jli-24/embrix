from __future__ import annotations

from datetime import UTC, datetime

import pytest

from embedded_copilot.model_gateway import (
    BoundModelIdentity,
    CapabilityEvidence,
    CapabilityProvenance,
    CapabilityStatus,
    CredentialProfile,
    EndpointProfile,
    EndpointType,
    EngineeringStage,
    ModelAvailability,
    ModelCapability,
    ModelGatewayManagementService,
    ModelProfile,
    ModelSelectionError,
    ModelSelectionService,
    ModelSelectionTaskProfile,
    PolicyScope,
    ProtocolFamily,
    ProviderProfile,
    RecordProvenance,
    SelectionMode,
    StageModelPolicy,
    VerificationStatus,
)

NOW = datetime(2026, 9, 12, tzinfo=UTC)


def _service() -> tuple[ModelGatewayManagementService, BoundModelIdentity]:
    service = ModelGatewayManagementService(clock=lambda: NOW, id_factory=lambda prefix: f"{prefix}-1")
    service.add_provider(ProviderProfile(provider_id="openai", display_name="OpenAI", supported_protocols=(ProtocolFamily.OPENAI_CHAT,)))
    service.add_endpoint(EndpointProfile(endpoint_id="endpoint-openai", provider_id="openai", display_name="OpenAI", endpoint_type=EndpointType.OFFICIAL, protocol_family=ProtocolFamily.OPENAI_CHAT, base_url="https://api.openai.com/v1", credential_ref="credential:openai"))
    service.add_credential(CredentialProfile(credential_id="credential-openai", provider_id="openai", display_name="Key", secret_ref="credential:openai"))
    model = ModelProfile(
        model_profile_id="model-openai-gpt",
        provider_id="openai",
        endpoint_id="endpoint-openai",
        remote_model_id="gpt-test",
        display_name="GPT test",
        advertised_family="GPT",
        capabilities=(ModelCapability.TEXT, ModelCapability.VISION, ModelCapability.REASONING),
        capability_sources=tuple(CapabilityEvidence(capability=item, provenance=CapabilityProvenance.PROVIDER_METADATA, status=CapabilityStatus.PASS, provider_id="openai", endpoint_id="endpoint-openai", credential_ref="credential:openai", model_profile_id="model-openai-gpt", model_id="gpt-test") for item in (ModelCapability.TEXT, ModelCapability.VISION, ModelCapability.REASONING)),
        availability=ModelAvailability.AVAILABLE,
        verification_status=VerificationStatus.VERIFIED,
        record_provenance=RecordProvenance.ENDPOINT_DISCOVERY,
    )
    service.add_model(model)
    return service, BoundModelIdentity(provider_id="openai", endpoint_id="endpoint-openai", credential_ref="credential:openai", model_profile_id=model.model_profile_id, model_id=model.remote_model_id)


def test_allowed_pool_scope_overlay_and_isolation() -> None:
    service, identity = _service()
    global_policy = service.create_allowed_model_policy(scope=PolicyScope.GLOBAL, project_id=None, stage_id=None, identity=identity)
    assert global_policy.enabled is True
    project = service.create_allowed_model_policy(scope=PolicyScope.PROJECT, project_id="project-a", stage_id=None, identity=identity, enabled=False)
    assert [item.id for item in service.effective_allowed_model_policies(project_id="project-a", stage_id=EngineeringStage.REQUIREMENT_ANALYSIS)] == []
    assert [item.id for item in service.effective_allowed_model_policies(project_id="project-b", stage_id=EngineeringStage.REQUIREMENT_ANALYSIS)] == [global_policy.id]
    service.set_allowed_model_enabled(project.id, True)
    assert [item.id for item in service.effective_allowed_model_policies(project_id="project-a", stage_id=EngineeringStage.REQUIREMENT_ANALYSIS)] == [project.id]


def test_stage_policy_fixed_requires_capabilities_and_allowed_identity() -> None:
    service, identity = _service()
    service.create_allowed_model_policy(scope=PolicyScope.GLOBAL, project_id=None, stage_id=None, identity=identity)
    policy = StageModelPolicy(id="stage-1", project_id="project-a", stage_id=EngineeringStage.PCB_REVIEW, selection_mode=SelectionMode.FIXED_MODEL, fixed_model=identity, created_at=NOW, updated_at=NOW)
    service.set_stage_model_policy(policy)
    result = service.select_governed_model(ModelSelectionTaskProfile(request_id="request-1", project_id="project-a", stage=EngineeringStage.PCB_REVIEW, task_type="PCB_REVIEW", required_capabilities=()),)
    assert result.selected_model == identity
    assert result.selection_reason == "STAGE_FIXED_MODEL"


def test_request_override_requires_effective_allowed_policy_for_project_and_stage() -> None:
    service, identity = _service()
    task = ModelSelectionTaskProfile(request_id="request-1", project_id="project-a", stage=EngineeringStage.REQUIREMENT_ANALYSIS, task_type="REQUIREMENT_ANALYSIS")
    with pytest.raises(ModelSelectionError, match="MODEL_NOT_ALLOWED"):
        service.select_governed_model(task, request_override=identity)

    service.create_allowed_model_policy(scope=PolicyScope.GLOBAL, project_id=None, stage_id=None, identity=identity)
    service.create_allowed_model_policy(scope=PolicyScope.PROJECT, project_id="project-a", stage_id=None, identity=identity, enabled=False)
    with pytest.raises(ModelSelectionError, match="MODEL_NOT_ALLOWED"):
        service.select_governed_model(task, request_override=identity)

    service.create_allowed_model_policy(scope=PolicyScope.STAGE, project_id="project-a", stage_id=EngineeringStage.REQUIREMENT_ANALYSIS, identity=identity)
    result = service.select_governed_model(task, request_override=identity)
    assert result.selected_model == identity
    service.create_allowed_model_policy(scope=PolicyScope.PROJECT, project_id="project-b", stage_id=None, identity=identity, enabled=False)
    with pytest.raises(ModelSelectionError, match="MODEL_NOT_ALLOWED"):
        service.select_governed_model(task.model_copy(update={"project_id": "project-b", "stage": EngineeringStage.DEBUGGING}), request_override=identity)
    bad = identity.model_copy(update={"provider_id": "deepseek"})
    with pytest.raises(ModelSelectionError, match="MODEL_PROVIDER_MISMATCH"):
        service.select_governed_model(task, request_override=bad)


def test_allowed_policy_does_not_preserve_a_stale_verified_model() -> None:
    service, identity = _service()
    service.create_allowed_model_policy(scope=PolicyScope.GLOBAL, project_id=None, stage_id=None, identity=identity)
    task = ModelSelectionTaskProfile(request_id="request-1", project_id="project-a", stage=EngineeringStage.REQUIREMENT_ANALYSIS, task_type="REQUIREMENT_ANALYSIS")
    model = service.model_profile(identity.model_profile_id)
    assert model is not None
    service.models.upsert(model.model_copy(update={"verification_status": VerificationStatus.UNVERIFIED}))

    with pytest.raises(ModelSelectionError, match="MODEL_VERIFICATION_GATE_FAILED"):
        service.select_governed_model(task, request_override=identity)


def test_auto_returns_candidates_without_scoring_or_selection() -> None:
    service, identity = _service()
    service.create_allowed_model_policy(scope=PolicyScope.GLOBAL, project_id=None, stage_id=None, identity=identity)
    result = service.select_governed_model(ModelSelectionTaskProfile(request_id="request-1", project_id="project-a", stage=EngineeringStage.REQUIREMENT_ANALYSIS, task_type="REQUIREMENT_ANALYSIS"))
    assert result.selected_model is None
    assert result.selection_reason == "ALLOWED_POOL_ROUTER_INPUT"
    assert result.fallback_candidates == (identity,)


def test_model_capability_mismatch_is_fail_closed() -> None:
    service, identity = _service()
    service.create_allowed_model_policy(scope=PolicyScope.GLOBAL, project_id=None, stage_id=None, identity=identity)
    task = ModelSelectionTaskProfile(request_id="request-1", project_id="project-a", stage=EngineeringStage.FIRMWARE_DEVELOPMENT, task_type="FIRMWARE_DEVELOPMENT")
    with pytest.raises(ModelSelectionError, match="MODEL_CAPABILITY_MISMATCH"):
        service.select_governed_model(task)


def test_policy_model_identity_is_provider_endpoint_and_credential_bound() -> None:
    service, identity = _service()
    mismatched = identity.model_copy(update={"credential_ref": "credential:other"})
    with pytest.raises(ModelSelectionError, match="MODEL_CREDENTIAL_MISMATCH"):
        service.create_allowed_model_policy(scope=PolicyScope.GLOBAL, project_id=None, stage_id=None, identity=mismatched)


def test_override_cannot_bypass_endpoint_or_quota_gates() -> None:
    service, identity = _service()
    task = ModelSelectionTaskProfile(request_id="request-1", project_id="project-a", stage=EngineeringStage.REQUIREMENT_ANALYSIS, task_type="REQUIREMENT_ANALYSIS")
    selector = ModelSelectionService(
        models=service.list_models(),
        credential_ref_by_endpoint={"endpoint-openai": "credential:openai"},
        available_credential_refs=("credential:openai",),
        endpoint_health={"endpoint-openai": "QUOTA_EXHAUSTED"},
    )
    with pytest.raises(ModelSelectionError, match="MODEL_QUOTA_GATE_FAILED"):
        selector.select_model(task, None, (), request_override=identity)


def test_unknown_capability_evidence_fails_closed_even_when_declared() -> None:
    service, identity = _service()
    task = ModelSelectionTaskProfile(request_id="request-1", project_id="project-a", stage=EngineeringStage.REQUIREMENT_ANALYSIS, task_type="REQUIREMENT_ANALYSIS", required_capabilities=(ModelCapability.VISION,))
    model = service.list_models()[0].model_copy(update={"capability_sources": (service.list_models()[0].capability_sources[0],)})
    selector = ModelSelectionService(
        models=(model,),
        credential_ref_by_endpoint={"endpoint-openai": "credential:openai"},
        available_credential_refs=("credential:openai",),
    )
    with pytest.raises(ModelSelectionError, match="MODEL_CAPABILITY_MISMATCH"):
        selector.select_model(task, None, (), request_override=identity)


def test_official_catalog_can_never_become_an_enabled_policy_identity() -> None:
    service, identity = _service()
    official = service.list_models()[0].model_copy(
        update={"record_provenance": RecordProvenance.OFFICIAL_CATALOG}
    )
    service.models.upsert(official)

    with pytest.raises(ModelSelectionError, match="MODEL_PROVENANCE_GATE_FAILED"):
        service.create_allowed_model_policy(
            scope=PolicyScope.GLOBAL,
            project_id=None,
            stage_id=None,
            identity=identity,
        )
