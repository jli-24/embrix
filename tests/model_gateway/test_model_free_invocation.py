import asyncio

import pytest

from embedded_copilot.model_gateway import (
    EngineeringStage,
    ModelCapability,
    ModelImageInput,
    ModelInvocationStatus,
    ModelPolicy,
    ModelRequest,
    ModelSelectionResult,
)
from embedded_copilot.model_gateway.invocation.contracts import (
    InvocationMode,
    ModelInvocationGenerationOptions,
    ModelInvocationPayload,
    ModelInvocationRequest,
)
from embedded_copilot.model_gateway.invocation.service import ModelInvocationService
from tests.model_gateway.test_invocation_service import (
    IDENTITY,
    _Gateway,
    _request,
    _Selection,
)


def test_conversion_discards_model_and_builder_restores_selected_identity():
    old = _request()
    request = ModelInvocationRequest.from_model_request(
        old, stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT
    )
    assert "canonical_request" not in type(request).model_fields
    assert "model_policy" not in type(request.payload).model_fields
    assert request.request_override is None
    final = ModelInvocationService._build_request(request, IDENTITY)
    assert final.model_policy.model_name == IDENTITY.model_id
    assert final.endpoint_id == IDENTITY.endpoint_id
    assert final.credential_ref == IDENTITY.credential_ref
    assert final.model_policy.fallback_providers == ()
    assert final.routing_strategy is None
    assert final.task == old.task


def test_legacy_model_hints_are_not_promoted_to_request_override():
    source = _request()
    legacy = ModelRequest(
        **{
            **source.model_dump(mode="python", exclude={"fingerprint"}),
            "model_policy": ModelPolicy(
                primary_provider="legacy-provider", model_name="legacy-model"
            ),
            "preferred_model": "legacy-profile",
            "preferred_provider": "legacy-provider",
            "routing_strategy": "legacy-router",
        }
    )
    request = ModelInvocationRequest.from_model_request(
        legacy, stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT
    )

    assert request.request_override is None
    final = ModelInvocationService._build_request(request, IDENTITY)
    assert final.model_policy.model_name == IDENTITY.model_id
    assert final.preferred_model == IDENTITY.model_profile_id
    assert final.preferred_provider == IDENTITY.provider_id
    assert final.routing_strategy is None


def test_compatibility_conversion_preserves_payload_content_and_image_capability():
    source = _request()
    old = ModelRequest(
        **{
            **source.model_dump(mode="python", exclude={"fingerprint"}),
            "attachments": ("artifact://firmware/source.c",),
            "images": (
                ModelImageInput(
                    content_ref="artifact://board/photo.png", mime_type="image/png"
                ),
            ),
            "conversation_id": "conversation-1",
        }
    )

    request = ModelInvocationRequest.from_model_request(
        old, stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT
    )

    assert request.payload.messages == old.messages
    assert request.payload.attachments == old.attachments
    assert request.payload.images == old.images
    assert request.payload.conversation_id == old.conversation_id
    assert ModelCapability.VISION in request.required_capabilities


@pytest.mark.parametrize(
    "values",
    [
        {"temperature": -1.0},
        {"temperature": float("nan")},
        {"max_tokens": True},
        {"max_tokens": 0},
        {"tool_definitions": (("name", "x" * 4097),)},
    ],
)
def test_options_validate_before_selection(values):
    with pytest.raises((TypeError, ValueError)):
        ModelInvocationGenerationOptions(**values)


def _input():
    snapshot = _request().engineering_context
    return ModelInvocationRequest(
        request_id="request-1",
        project_id="project-1",
        stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT,
        invocation_mode=InvocationMode.CHAT,
        task_type="FIRMWARE",
        payload=ModelInvocationPayload(
            task="firmware task",
            engineering_context=snapshot,
            system_instruction="Use supplied evidence only.",
        ),
        trace_context={"trace_id": "trace-1"},
        generation_options=ModelInvocationGenerationOptions(
            temperature=0.4,
            max_tokens=777,
            tool_definitions=(("tool", "definition"),),
            structured_output_schema=(("schema", "object"),),
        ),
    )


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "reason",
    ["PROJECT_FIXED_MODEL", "AUTO_ROUTER_PENDING", "CANDIDATE_GROUP_PENDING"],
)
def test_selection_once_and_no_builder_on_pending(asynchronous, reason):
    selected = IDENTITY if reason == "PROJECT_FIXED_MODEL" else None
    selector = _Selection(
        ModelSelectionResult(selected_model=selected, selection_reason=reason)
    )
    gateway = _Gateway()

    class Service(ModelInvocationService):
        builds = 0

        def _build_request(self, request, identity):
            self.builds += 1
            result = super()._build_request(request, identity)
            assert result.model_policy.temperature == 0.4
            assert result.max_tokens == 777
            assert result.tool_definitions == request.generation_options.tool_definitions
            assert (
                result.structured_output_schema
                == request.generation_options.structured_output_schema
            )
            return result

    receipts = []
    service = Service(selection_resolver=selector, gateway=gateway, receipt_store=receipts)
    request = _input()
    result = asyncio.run(service.ainvoke(request)) if asynchronous else service.invoke(request)
    assert selector.calls == 1
    assert service.builds == gateway.calls == int(selected is not None)
    assert receipts[0].selected_model == selected
    assert receipts[0].provider_request_count == result.provider_request_count == 0
    assert receipts[0].gateway_call_count == int(selected is not None)
    assert receipts[0].gateway_response_observed is (selected is not None)
    assert receipts[0].transport_send_count is None
    assert result.status == (
        ModelInvocationStatus.SUCCESS
        if selected
        else ModelInvocationStatus.REJECTED
    )


def test_images_union_explicit_capabilities_and_reject_model_injection():
    request = _input()
    content = request.payload.model_dump(mode="python", exclude={"fingerprint"})
    content["images"] = (
        ModelImageInput(content_ref="artifact://image-1", mime_type="image/png"),
    )
    payload = ModelInvocationPayload(**content)
    material = request.model_dump(mode="python")
    material.update(payload=payload, required_capabilities=(ModelCapability.TEXT,))
    checked = ModelInvocationRequest.model_validate(material)
    assert set(checked.required_capabilities) == {ModelCapability.TEXT, ModelCapability.VISION}
    with pytest.raises(ValueError):
        ModelInvocationPayload(**content, model_policy={"model_name": "bad"})


def test_payload_integrity_and_identity_specific_final_fingerprint():
    request = _input()
    content = request.payload.model_dump(mode="python")
    content["task"] = "tampered"
    with pytest.raises(ValueError):
        ModelInvocationPayload.model_validate(content)
    other = type(IDENTITY)(
        **{**IDENTITY.model_dump(mode="python"), "model_id": "different-model"}
    )
    first = ModelInvocationService._build_request(request, IDENTITY)
    second = ModelInvocationService._build_request(request, other)
    assert first.fingerprint != second.fingerprint
    assert first.fingerprint != request.payload.fingerprint
