from __future__ import annotations

import httpx
import pytest

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.model_gateway import (
    ModelGatewayService,
    ModelGatewayProviderFailure,
    ModelProviderSettings,
    ModelRequest,
    create_model_provider,
)


def test_gateway_revalidates_a_factory_created_real_provider_response() -> None:
    settings = ModelProviderSettings.from_environment(
        {
            "MODEL_PROVIDER": "openai",
            "OPENAI_API_KEY": "test-key",
            "OPENAI_MODEL": "gpt-test",
        }
    )
    provider = create_model_provider(
        settings=settings,
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": "Grounded answer."},
                            "finish_reason": "stop",
                        }
                    ]
                },
            )
        ),
    )
    context = EngineeringContextSnapshot.create(
        project_id="project-1",
        task_description="check GPIO12",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.5,
        provenance=(),
        verification_status="UNVERIFIED",
    )
    response = ModelGatewayService(providers={"openai": provider}).generate(
        ModelRequest(
            request_id="request-1",
            task="check GPIO12",
            engineering_context=context,
            system_instruction="Use supplied evidence only.",
            model_policy=settings.to_model_policy(),
        )
    )

    assert response.content == "Grounded answer."
    assert response.provider == "openai"
    assert response.model_name == "gpt-test"


def test_gateway_distinguishes_provider_parse_from_transport_failure() -> None:
    settings = ModelProviderSettings.from_environment(
        {
            "MODEL_PROVIDER": "openai",
            "OPENAI_API_KEY": "test-key",
            "OPENAI_MODEL": "gpt-test",
        }
    )
    malformed = create_model_provider(
        settings=settings,
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"choices": []})
        ),
    )

    with pytest.raises(ModelGatewayProviderFailure) as raised:
        ModelGatewayService(providers={"openai": malformed}).generate(
            ModelRequest(
                request_id="request-parse-failure",
                task="check GPIO12",
                engineering_context=EngineeringContextSnapshot.create(
                    project_id="project-1",
                    task_description="check GPIO12",
                    memory_evidence=(),
                    graph_evidence=(),
                    knowledge_evidence=(),
                    multimodal_evidence=(),
                    conflicts=(),
                    confidence=0.0,
                    provenance=(),
                    verification_status="VERIFIED",
                ),
                system_instruction="Use supplied evidence only.",
                model_policy=settings.to_model_policy(),
            )
        )

    assert raised.value.reason_code == "PROVIDER_PARSE_FAILED"
