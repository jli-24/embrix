from __future__ import annotations

import httpx

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.model_gateway import (
    ModelPolicy,
    ModelRequest,
    QwenProviderAdapter,
)


def test_qwen_adapter_uses_fixed_compatible_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert (
            str(request.url)
            == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        )
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
    response = QwenProviderAdapter(
        api_key="test-key", http_transport=httpx.MockTransport(handler)
    ).generate(
        ModelRequest(
            request_id="request-1",
            task="task",
            engineering_context=context,
            system_instruction="Use evidence.",
            model_policy=ModelPolicy(primary_provider="qwen", model_name="qwen-plus"),
        )
    )
    assert response.provider == "qwen"
