from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import SecretStr, ValidationError

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.model_gateway import (
    BoundModelIdentity,
    CapabilityEvidence,
    CapabilityProvenance,
    CapabilityStatus,
    EngineeringStage,
    ModelAvailability,
    ModelCapability,
    ModelGatewayRejected,
    ModelGatewayService,
    ModelImageInput,
    ModelProviderSettings,
    ModelPolicy,
    ModelProfile,
    ModelRequest,
    ModelResponse,
    ModelSelectionError,
    ModelSelectionService,
    ModelSelectionTaskProfile,
    ProviderCapability,
    RecordProvenance,
    ProtocolFamily,
    ProtocolHTTPProviderAdapter,
    VerificationStatus,
    create_model_provider,
)


NOW = datetime(2026, 9, 14, tzinfo=UTC)
IDENTITY = BoundModelIdentity(
    provider_id="relay",
    endpoint_id="endpoint-relay",
    credential_ref="credential:relay",
    model_profile_id="model-relay-vision",
    model_id="vision-model",
)


def _snapshot() -> EngineeringContextSnapshot:
    return EngineeringContextSnapshot.create(
        project_id="project-1",
        task_description="inspect a PCB image",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.5,
        provenance=(),
        verification_status="UNVERIFIED",
    )


def _evidence(
    capability: ModelCapability,
    *,
    provider_id: str = "relay",
    credential_revision: str | None = "credential-revision-1",
) -> CapabilityEvidence:
    return CapabilityEvidence(
        capability=capability,
        provenance=CapabilityProvenance.PROBED,
        status=CapabilityStatus.PASS,
        source="capability_probe",
        observed_at=NOW,
        provider_id=provider_id,
        endpoint_id="endpoint-relay",
        credential_ref="credential:relay",
        model_profile_id="model-relay-vision",
        model_id="vision-model",
        credential_revision=credential_revision,
        endpoint_revision="endpoint-revision-1",
    )


def _model(*, capability_sources: tuple[CapabilityEvidence, ...] | None = None) -> ModelProfile:
    capabilities = (ModelCapability.TEXT, ModelCapability.VISION)
    return ModelProfile(
        model_profile_id=IDENTITY.model_profile_id,
        provider_id=IDENTITY.provider_id,
        endpoint_id=IDENTITY.endpoint_id,
        remote_model_id=IDENTITY.model_id,
        display_name="Vision model",
        advertised_family="vision",
        capabilities=capabilities,
        capability_sources=capability_sources
        or tuple(_evidence(capability) for capability in capabilities),
        availability=ModelAvailability.AVAILABLE,
        credential_revision="credential-revision-1",
        endpoint_revision="endpoint-revision-1",
        verification_status=VerificationStatus.VERIFIED,
        record_provenance=RecordProvenance.ENDPOINT_DISCOVERY,
    )


def _task() -> ModelSelectionTaskProfile:
    return ModelSelectionTaskProfile(
        request_id="request-1",
        project_id="project-1",
        stage=EngineeringStage.REQUIREMENT_ANALYSIS,
        task_type="VISION",
        required_capabilities=(ModelCapability.VISION,),
    )


def test_selection_rejects_capability_evidence_bound_to_another_identity() -> None:
    wrong = _evidence(ModelCapability.VISION, provider_id="other-provider")
    model = _model(
        capability_sources=(_evidence(ModelCapability.TEXT), wrong),
    )
    selector = ModelSelectionService(
        models=(model,),
        credential_ref_by_endpoint={"endpoint-relay": "credential:relay"},
        available_credential_refs=("credential:relay",),
        endpoint_health={"endpoint-relay": "AVAILABLE"},
        quota_available_by_endpoint={"endpoint-relay": True},
    )

    with pytest.raises(ModelSelectionError, match="MODEL_CAPABILITY_MISMATCH"):
        selector.select_model(_task(), None, (), request_override=IDENTITY)


def test_selection_rejects_stale_capability_evidence() -> None:
    stale = _evidence(ModelCapability.VISION, credential_revision="credential-revision-old")
    model = _model(capability_sources=(_evidence(ModelCapability.TEXT), stale))
    selector = ModelSelectionService(
        models=(model,),
        credential_ref_by_endpoint={"endpoint-relay": "credential:relay"},
        available_credential_refs=("credential:relay",),
        endpoint_health={"endpoint-relay": "AVAILABLE"},
        quota_available_by_endpoint={"endpoint-relay": True},
    )

    with pytest.raises(ModelSelectionError, match="MODEL_CAPABILITY_MISMATCH"):
        selector.select_model(_task(), None, (), request_override=IDENTITY)


def test_image_input_is_reference_only_and_bounded() -> None:
    image = ModelImageInput(
        content_ref="artifact://project-1/pcb.png",
        mime_type="image/png",
        size_bytes=1024,
    )
    request = ModelRequest(
        request_id="request-1",
        task="inspect the PCB image",
        engineering_context=_snapshot(),
        system_instruction="Use supplied evidence only.",
        model_policy=ModelPolicy(primary_provider="relay", model_name="vision-model"),
        images=(image,),
    )
    assert request.images == (image,)
    assert "VISION" in request.required_capabilities
    assert request.fingerprint.startswith("sha256:")

    with pytest.raises(ValidationError):
        ModelImageInput(
            content_ref="C:\\private\\pcb.png",
            mime_type="image/png",
        )
    with pytest.raises(ValidationError):
        ModelImageInput(
            content_ref="artifact://project-1/pcb.svg",
            mime_type="image/svg+xml",
        )


class _TextOnlyProvider:
    capabilities = (ProviderCapability.TEXT,)

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        raise AssertionError("vision request reached a text-only provider")

    async def agenerate(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        raise AssertionError("vision request reached a text-only provider")


def test_gateway_rejects_image_input_before_text_only_provider_call() -> None:
    provider = _TextOnlyProvider()
    gateway = ModelGatewayService(providers={"relay": provider})
    request = ModelRequest(
        request_id="request-1",
        task="inspect the PCB image",
        engineering_context=_snapshot(),
        system_instruction="Use supplied evidence only.",
        model_policy=ModelPolicy(primary_provider="relay", model_name="vision-model"),
        images=(
            ModelImageInput(
                content_ref="artifact://project-1/pcb.png",
                mime_type="image/png",
            ),
        ),
    )

    with pytest.raises(ModelGatewayRejected) as error:
        gateway.generate(request)

    assert error.value.reason_code == "VISION_INPUT_NOT_SUPPORTED"
    assert provider.calls == 0


def test_openai_protocol_adapter_keeps_image_reference_in_canonical_wire_payload() -> None:
    request = ModelRequest(
        request_id="request-1",
        task="inspect the PCB image",
        engineering_context=_snapshot(),
        system_instruction="Use supplied evidence only.",
        model_policy=ModelPolicy(primary_provider="relay", model_name="vision-model"),
        images=(
            ModelImageInput(
                content_ref="https://example.test/pcb.png",
                mime_type="image/png",
            ),
        ),
    )
    adapter = ProtocolHTTPProviderAdapter(
        provider="relay",
        endpoint_id="endpoint-relay",
        base_url="https://example.test/v1",
        protocol_family=ProtocolFamily.OPENAI_CHAT,
        api_key=SecretStr("offline-test-key"),
        supports_image_input=True,
    )

    payload = adapter._payload(request)
    user_message = payload["messages"][1]
    assert ProviderCapability.VISION in adapter.capabilities
    assert isinstance(user_message["content"], list)
    assert user_message["content"][1] == {
        "type": "image_url",
        "image_url": {"url": "https://example.test/pcb.png", "detail": "auto"},
    }


def test_existing_provider_factory_exposes_explicit_image_capability_opt_in() -> None:
    provider = create_model_provider(
        ModelProviderSettings(
            provider="custom",
            model_name="vision-model",
            api_key=SecretStr("offline-test-key"),
            base_url="https://example.test/v1",
            protocol_family=ProtocolFamily.OPENAI_CHAT,
            supports_image_input=True,
        )
    )
    assert ProviderCapability.VISION in provider.capabilities
