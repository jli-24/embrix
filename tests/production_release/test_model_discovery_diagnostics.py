from __future__ import annotations

import json
import logging

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from embedded_copilot.api.desktop_identity import ProductionLocalSessionIdentityAdapter
from embedded_copilot.api.model_gateway_routes import router
from embedded_copilot.model_gateway import (
    ModelGatewayManagementService,
    ProtocolFamily,
    ProviderProfile,
)
from embedded_copilot.model_gateway.profiles import EndpointProfile, EndpointType
from embedded_copilot.production_release.credentials import InMemoryCredentialProvider
from embedded_copilot.production_release.model_discovery import (
    ModelDiscoveryFailure,
    OfficialModelDiscovery,
    OfficialModelProfileDiscovery,
)
from embedded_copilot.production_release.session import LocalSessionManager


def _endpoint() -> EndpointProfile:
    return EndpointProfile(
        endpoint_id="endpoint-deepseek",
        provider_id="deepseek",
        display_name="DeepSeek endpoint",
        endpoint_type=EndpointType.OFFICIAL,
        protocol_family=ProtocolFamily.OPENAI_CHAT,
        base_url="https://api.deepseek.com/v1",
        credential_ref="credential:deepseek",
    )


def _credentials() -> InMemoryCredentialProvider:
    credentials = InMemoryCredentialProvider()
    credentials.set("CHAT_PROVIDER_API_KEY:deepseek", b"sk-secret-value")
    return credentials


def _failure(response: httpx.Response) -> object:
    discovery = OfficialModelDiscovery(
        _credentials(),
        transport=httpx.MockTransport(lambda request: response),
    )
    with pytest.raises(ModelDiscoveryFailure) as raised:
        discovery.discover(_endpoint())
    return raised.value.evidence


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "HTTP_401_AUTH_FAILED"),
        (403, "HTTP_403_PERMISSION_DENIED"),
        (500, "PROVIDER_5XX"),
        (502, "PROVIDER_5XX"),
        (503, "PROVIDER_5XX"),
    ],
)
def test_http_discovery_failures_preserve_status_and_classification(
    status: int, expected: str
) -> None:
    evidence = _failure(
        httpx.Response(
            status,
            json={"error": {"code": "provider_error", "type": "server", "message": "safe failure"}},
            headers={"content-type": "application/json", "x-ds-trace-id": "trace-123"},
        )
    )

    assert evidence.provider_http_status == status
    assert evidence.classification == expected
    assert evidence.failure_stage == "HTTP_STATUS"
    assert evidence.provider_error_code == "provider_error"
    assert evidence.provider_error_type == "server"
    assert evidence.provider_error_message_sanitized == "safe failure"
    assert evidence.provider_request_id == "trace-123"


def test_discovery_distinguishes_rate_limit_and_quota() -> None:
    rate_limited = _failure(
        httpx.Response(
            429,
            json={"error": {"code": "rate_limit_exceeded", "message": "try later"}},
        )
    )
    quota_exhausted = _failure(
        httpx.Response(
            429,
            json={"error": {"code": "insufficient_quota", "message": "quota exhausted"}},
        )
    )

    assert rate_limited.classification == "HTTP_429_RATE_LIMITED"
    assert quota_exhausted.classification == "QUOTA_EXHAUSTED"


@pytest.mark.parametrize(
    ("error", "stage", "expected"),
    [
        (httpx.ConnectTimeout("connect timeout"), "TCP_CONNECT", "NETWORK_TIMEOUT"),
        (httpx.ConnectError("getaddrinfo failed"), "DNS", "DNS_FAILED"),
        (httpx.ConnectError("CERTIFICATE_VERIFY_FAILED"), "TLS", "TLS_FAILED"),
    ],
)
def test_network_discovery_failures_are_classified(
    error: httpx.HTTPError, stage: str, expected: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    discovery = OfficialModelDiscovery(_credentials(), transport=httpx.MockTransport(handler))
    with pytest.raises(ModelDiscoveryFailure) as raised:
        discovery.discover(_endpoint())

    assert raised.value.evidence.failure_stage == stage
    assert raised.value.evidence.classification == expected
    assert raised.value.evidence.provider_http_status is None


def test_parse_and_schema_failures_preserve_success_http_status() -> None:
    parse_failure = _failure(httpx.Response(200, text="not-json"))
    schema_failure = _failure(httpx.Response(200, json={"items": []}))

    assert parse_failure.provider_http_status == 200
    assert parse_failure.failure_stage == "JSON_PARSE"
    assert parse_failure.classification == "RESPONSE_PARSE_FAILED"
    assert schema_failure.provider_http_status == 200
    assert schema_failure.failure_stage == "MODEL_SCHEMA_VALIDATION"
    assert schema_failure.classification == "MODEL_DISCOVERY_SCHEMA_INVALID"


def test_discovery_failure_redacts_credentials_and_preserves_safe_provider_data(
    caplog: pytest.LogCaptureFixture,
) -> None:
    response = httpx.Response(
        401,
        json={
            "error": {
                "code": "invalid_api_key",
                "type": "authentication_error",
                "message": "safe message Authorization: Bearer sk-xxx Cookie: session=secret-cookie",
            },
            "request_id": "provider-request-1",
        },
        headers={"content-type": "application/json"},
    )
    with caplog.at_level(logging.WARNING, logger="embedded_copilot.production_release.model_discovery"):
        evidence = _failure(response)

    serialized = json.dumps(evidence.as_dict(), ensure_ascii=False)
    logged = " ".join(record.getMessage() + repr(record.__dict__) for record in caplog.records)
    assert "safe message" in serialized
    assert "provider-request-1" in serialized
    assert "sk-xxx" not in serialized
    assert "secret-cookie" not in serialized
    assert "sk-xxx" not in logged
    assert "secret-cookie" not in logged


def test_profile_adapter_preserves_canonical_success_projection() -> None:
    discovery = OfficialModelProfileDiscovery(
        OfficialModelDiscovery(
            _credentials(),
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]},
                )
            ),
        )
    )

    models = discovery.discover(_endpoint())

    assert {item.remote_model_id for item in models} == {"deepseek-chat", "deepseek-reasoner"}
    assert all(item.provider_id == "deepseek" for item in models)
    assert all(item.endpoint_id == "endpoint-deepseek" for item in models)


def test_api_returns_safe_discovery_error_envelope() -> None:
    app = FastAPI()
    app.include_router(router)
    management = ModelGatewayManagementService()
    management.add_provider(
        ProviderProfile(
            provider_id="deepseek",
            display_name="DeepSeek",
            supported_protocols=(ProtocolFamily.OPENAI_CHAT,),
        )
    )
    management.add_endpoint(_endpoint())
    management.set_discovery_port(
        OfficialModelProfileDiscovery(
            OfficialModelDiscovery(
                _credentials(),
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        401,
                        json={"error": {"code": "invalid_api_key", "message": "safe"}},
                    )
                ),
            )
        )
    )
    app.state.model_gateway_management_service = management

    sessions = LocalSessionManager()
    token = sessions.exchange(sessions.issue_bootstrap(requester="owner-1", tenant_id="tenant-1"))
    app.state.desktop_identity_provider = ProductionLocalSessionIdentityAdapter(sessions)
    client = TestClient(app)
    path = "/api/v1/model-gateway/endpoints/endpoint-deepseek/discover"
    assert client.post(path).status_code == 401
    client.cookies.set("EmbeddedCopilotSession", token)
    response = client.post(path)

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["reason_code"] == "HTTP_401_AUTH_FAILED"
    assert detail["failure_stage"] == "HTTP_STATUS"
    assert detail["provider_http_status"] == 401
    assert detail["provider_error_code"] == "invalid_api_key"
    assert "secret" not in response.text
