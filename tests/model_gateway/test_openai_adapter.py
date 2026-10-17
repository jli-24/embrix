from __future__ import annotations

import asyncio
import json
from unittest.mock import ANY

import httpx
import pytest

from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.model_gateway import (
    ModelPolicy,
    ModelProviderAnswerEmpty,
    ModelProviderError,
    ModelProviderRateLimited,
    ModelProviderTimeout,
    ModelProviderUnauthorized,
    ModelRequest,
    OpenAIProviderAdapter,
)


def _request() -> ModelRequest:
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
    return ModelRequest(
        request_id="request-1",
        task="check GPIO12",
        engineering_context=context,
        system_instruction="Use supplied evidence only.",
        constraints=("offline",),
        model_policy=ModelPolicy(
            primary_provider="openai",
            model_name="gpt-test",
            temperature=0.2,
            max_output_tokens=32,
        ),
    )


def test_openai_adapter_maps_chat_completion_without_raw_payload_storage() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "Grounded answer."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 2,
                    "total_tokens": 5,
                },
            },
        )

    adapter = OpenAIProviderAdapter(
        api_key="test-key", http_transport=httpx.MockTransport(handler)
    )
    response = adapter.generate(_request())
    assert response.content == "Grounded answer."
    assert response.provider == "openai"
    assert response.usage is not None and response.usage.total_tokens == 5
    assert seen["url"] == "https://api.openai.com/v1/chat/completions"
    assert seen["body"] == {
        "max_tokens": 32,
        "messages": ANY,
        "model": "gpt-test",
        "temperature": 0.2,
    }
    assert "Engineering context:" in seen["body"]["messages"][1]["content"]


def test_openai_adapter_emits_redacted_response_boundary_telemetry(caplog) -> None:
    adapter = OpenAIProviderAdapter(
        api_key="test-key",
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": "test answer"}, "finish_reason": "stop"}
                    ]
                },
            )
        ),
    )

    with caplog.at_level(
        "INFO",
        logger="embedded_copilot.model_gateway.adapters.openai_compatible",
    ):
        response = adapter.generate(_request())

    records = [
        record
        for record in caplog.records
        if record.getMessage() == "provider_response_boundary_telemetry"
    ]
    by_stage = {record.response_stage: record for record in records}
    assert response.content == "test answer"
    assert by_stage["R0_RAW_RESPONSE_RECEIVED"].provider_response_received is True
    assert by_stage["R1_PROVIDER_ADAPTER_PARSE"].adapter_parse_status == "PASS"
    assert by_stage["R1_PROVIDER_ADAPTER_PARSE"].parsed_field_names == (
        "choices",
        "choice",
        "message",
        "content",
    )
    assert by_stage["R3_CONTENT_EXTRACTION"].content_present is True
    assert by_stage["R3_CONTENT_EXTRACTION"].content_length == 11
    assert by_stage["R4_ANSWER_TEXT_NORMALIZE"].answer_text_present is True
    assert by_stage["R4_ANSWER_TEXT_NORMALIZE"].answer_text_length == 11
    assert by_stage["R2_MODEL_RESPONSE_OBJECT_CREATE"].model_response_created is True
    assert "test answer" not in caplog.text
    assert "test-key" not in caplog.text


def test_openai_adapter_emits_redacted_request_boundary_telemetry(caplog) -> None:
    adapter = OpenAIProviderAdapter(
        api_key="test-key",
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": "test answer"}, "finish_reason": "stop"}
                    ]
                },
            )
        ),
    )

    with caplog.at_level(
        "INFO",
        logger="embedded_copilot.model_gateway.adapters.openai_compatible",
    ):
        adapter.generate(_request())

    records = [
        record
        for record in caplog.records
        if record.getMessage() == "provider_request_boundary_telemetry"
    ]
    assert len(records) == 1
    record = records[0]
    assert record.request_model == "gpt-test"
    assert record.request_message_count == 2
    assert record.system_message_present is True
    assert record.system_prompt_length > 0
    assert record.user_message_present is True
    assert record.user_prompt_length > 0
    assert record.context_message_present is False
    assert record.context_payload_length > 0
    assert record.project_context_attached is True
    assert record.requirement_context_attached == "UNOBSERVED"
    assert record.max_tokens == 32
    assert record.temperature == 0.2
    assert record.top_p == "UNOBSERVED"
    assert record.response_format_present is False
    assert record.http_payload_valid is True
    assert record.messages_array_length == 2
    assert record.first_message_role == "system"
    assert record.last_message_role == "user"
    assert record.task_mode == "UNOBSERVED"
    assert record.project_mode == "UNOBSERVED"
    assert "test answer" not in caplog.text
    assert "test-key" not in caplog.text


def test_openai_adapter_emits_fail_closed_empty_answer_telemetry(caplog) -> None:
    adapter = OpenAIProviderAdapter(
        api_key="test-key",
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"choices": [{"message": {"content": ""}}]},
            )
        ),
    )

    with caplog.at_level(
        "INFO",
        logger="embedded_copilot.model_gateway.adapters.openai_compatible",
    ):
        with pytest.raises(ModelProviderAnswerEmpty):
            adapter.generate(_request())

    records = [
        record
        for record in caplog.records
        if record.getMessage() == "provider_response_boundary_telemetry"
    ]
    by_stage = {record.response_stage: record for record in records}
    assert by_stage["R1_PROVIDER_ADAPTER_PARSE"].adapter_parse_status == "PASS"
    assert by_stage["R3_CONTENT_EXTRACTION"].content_present is True
    assert by_stage["R4_ANSWER_TEXT_NORMALIZE"].answer_text_present is False
    assert by_stage["R4_ANSWER_TEXT_NORMALIZE"].answer_text_length == 0
    assert by_stage["R4_ANSWER_TEXT_NORMALIZE"].reason_code == "ANSWER_EMPTY"
    assert by_stage["R2_MODEL_RESPONSE_OBJECT_CREATE"].model_response_created is False


def test_openai_adapter_maps_rate_limits_and_preserves_async_execution() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(429, json={"error": "ignored"})
    )
    adapter = OpenAIProviderAdapter(
        api_key="test-key", http_transport=transport, async_transport=transport
    )
    with pytest.raises(ModelProviderRateLimited):
        adapter.generate(_request())
    with pytest.raises(ModelProviderRateLimited):
        asyncio.run(adapter.agenerate(_request()))


@pytest.mark.parametrize(
    ("response", "error"),
    (
        (httpx.Response(401, json={}), ModelProviderUnauthorized),
        (httpx.Response(200, json={"choices": []}), ModelProviderError),
    ),
)
def test_openai_adapter_redacts_authentication_and_malformed_provider_failures(
    response: httpx.Response, error: type[Exception]
) -> None:
    adapter = OpenAIProviderAdapter(
        api_key="test-key",
        http_transport=httpx.MockTransport(lambda request: response),
    )
    with pytest.raises(error) as raised:
        adapter.generate(_request())
    assert "test-key" not in str(raised.value)


def test_openai_adapter_maps_transport_timeouts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("ignored", request=request)

    adapter = OpenAIProviderAdapter(
        api_key="test-key", http_transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ModelProviderTimeout):
        adapter.generate(_request())


def test_openai_adapter_normalizes_typed_greenfield_answer() -> None:
    adapter = OpenAIProviderAdapter(
        api_key="test-key",
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "answer_text": "先确认产品目标。",
                                        "proposed_requirements": [
                                            {
                                                "category": "RTOS",
                                                "value": "FreeRTOS",
                                                "rationale": "便于任务调度",
                                            }
                                        ],
                                        "open_questions": [
                                            {
                                                "category": "PRODUCT_GOAL",
                                                "question": "这个项目要实现什么产品目标？",
                                            }
                                        ],
                                    },
                                    ensure_ascii=False,
                                )
                            },
                            "finish_reason": "stop",
                        }
                    ]
                },
            )
        ),
    )

    response = adapter.generate(_request())

    assert response.content == "先确认产品目标。"
    assert [(item.category, item.value) for item in response.requirement_proposals] == [
        ("RTOS", "FreeRTOS")
    ]
    assert [(item.category, item.question) for item in response.open_questions] == [
        ("PRODUCT_GOAL", "这个项目要实现什么产品目标？")
    ]


def test_openai_adapter_normalizes_mixed_text_and_fenced_metadata() -> None:
    content = (
        "我们先补齐关键需求。\n"
        "```json\n"
        '{"proposed_requirements":[{"category":"RTOS","value":"FreeRTOS"}],'
        '"open_questions":[{"category":"PRODUCT_GOAL","question":"产品目标是什么？"}]}\n'
        "```"
    )
    adapter = OpenAIProviderAdapter(
        api_key="test-key",
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": content},
                            "finish_reason": "stop",
                        }
                    ]
                },
            )
        ),
    )

    response = adapter.generate(_request())

    assert response.content == "我们先补齐关键需求。"
    assert len(response.requirement_proposals) == 1
    assert len(response.open_questions) == 1


def test_openai_adapter_downgrades_provider_confirmed_metadata_to_proposal() -> None:
    adapter = OpenAIProviderAdapter(
        api_key="test-key",
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "answer_text": "建议评估 FreeRTOS。",
                                        "proposed_requirements": [
                                            {
                                                "category": "RTOS",
                                                "value": "FreeRTOS",
                                                "status": "CONFIRMED",
                                            }
                                        ],
                                    },
                                    ensure_ascii=False,
                                )
                            },
                            "finish_reason": "stop",
                        }
                    ]
                },
            )
        ),
    )

    response = adapter.generate(_request())

    assert response.content == "建议评估 FreeRTOS。"
    assert len(response.requirement_proposals) == 1
    assert response.requirement_proposals[0].model_dump() == {
        "category": "RTOS",
        "value": "FreeRTOS",
        "rationale": None,
    }


def test_openai_adapter_rejects_empty_answer_with_specific_safe_type() -> None:
    adapter = OpenAIProviderAdapter(
        api_key="test-key",
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": "   "},
                            "finish_reason": "stop",
                        }
                    ]
                },
            )
        ),
    )

    with pytest.raises(ModelProviderAnswerEmpty):
        adapter.generate(_request())


def test_openai_adapter_rejects_typed_metadata_without_answer_text() -> None:
    adapter = OpenAIProviderAdapter(
        api_key="test-key",
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "proposed_requirements": [
                                            {
                                                "category": "RTOS",
                                                "value": "FreeRTOS",
                                            }
                                        ],
                                        "open_questions": [
                                            {
                                                "category": "PRODUCT_GOAL",
                                                "question": "产品目标是什么？",
                                            }
                                        ],
                                    },
                                    ensure_ascii=False,
                                )
                            },
                            "finish_reason": "stop",
                        }
                    ]
                },
            )
        ),
    )

    with pytest.raises(ModelProviderAnswerEmpty):
        adapter.generate(_request())
