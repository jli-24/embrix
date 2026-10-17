from __future__ import annotations

import httpx
import logging

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.model_gateway import (
    DeepSeekProviderAdapter,
    ModelPolicy,
    ModelRequest,
)


def test_deepseek_adapter_uses_fixed_compatible_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.deepseek.com/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}]
            },
        )

    context = EngineeringContextSnapshot.create(
        project_id="project-1",
        task_description="task",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.5,
        provenance=(),
        verification_status="UNVERIFIED",
    )
    response = DeepSeekProviderAdapter(
        api_key="test-key", http_transport=httpx.MockTransport(handler)
    ).generate(
        ModelRequest(
            request_id="request-1",
            task="task",
            engineering_context=context,
            system_instruction="Use evidence.",
            model_policy=ModelPolicy(
                primary_provider="deepseek", model_name="deepseek-chat"
            ),
        )
    )
    assert response.provider == "deepseek"


def test_deepseek_adapter_logs_only_safe_call_identity(caplog) -> None:
    context = EngineeringContextSnapshot.create(
        project_id="project-1",
        task_description="task",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.5,
        provenance=(),
        verification_status="UNVERIFIED",
    )
    request = ModelRequest(
        request_id="request-safe-log",
        task="task",
        engineering_context=context,
        system_instruction="Use evidence.",
        model_policy=ModelPolicy(
            primary_provider="deepseek", model_name="deepseek-chat"
        ),
    )
    adapter = DeepSeekProviderAdapter(
        api_key="secret-key",
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": "answer"},
                            "finish_reason": "stop",
                        }
                    ]
                },
            )
        ),
    )

    with caplog.at_level(logging.INFO, logger="embedded_copilot.model_gateway.adapters.openai_compatible"):
        adapter.generate(request)

    record = next(record for record in caplog.records if record.getMessage() == "model_provider_call")
    assert record.provider == "deepseek"
    assert record.model == "deepseek-chat"
    assert record.request_id == "request-safe-log"
    assert record.latency_ms >= 0
    assert "secret-key" not in record.getMessage()
    assert "task" not in record.getMessage()
