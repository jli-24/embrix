"""Offline collector tests: every HTTP client is replaced before invocation."""

import asyncio
from pathlib import Path

import httpx
import pytest

from embedded_copilot.model_gateway.adapters import (
    openai_count_collector as collector,
)
from embedded_copilot.model_gateway.adapters.protocol_http import (
    ProtocolHTTPProviderAdapter,
)
from embedded_copilot.model_gateway.exceptions import (
    ModelGatewayRejected,
    ModelProviderTimeout,
)
from embedded_copilot.model_gateway.invocation.budget import digest
from embedded_copilot.model_gateway.profiles import ProtocolFamily

_PAYLOAD = (
    b'{"model":"gpt-5.6-luna","instructions":"system","input":"task",'
    b'"text":{"format":{"type":"json_object"}}}'
)


def adapter(*, base_url="https://api.openai.com/v1", custom=None, cls=None):
    kind = cls or ProtocolHTTPProviderAdapter
    return kind(
        provider="openai",
        endpoint_id="endpoint-openai",
        base_url=base_url,
        protocol_family=ProtocolFamily.OPENAI_RESPONSES,
        api_key="offline-placeholder",
        http_transport=custom,
    )


def fake_clients(monkeypatch, response=None, error=None):
    events: list[tuple[object, ...]] = []
    reply = (
        response
        if response is not None
        else httpx.Response(
            200,
            content=b'{"object":"response.input_tokens","input_tokens":7}',
            headers={"x-request-id": "request-offline-1"},
        )
    )

    class FakeClient:
        def __init__(self, **kwargs):
            events.append(("client", kwargs))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            events.append(("close",))

        def post(self, path, *, headers, content):
            events.append(("post", path, headers, content))
            if error is not None:
                raise error
            return reply

    class FakeAsyncClient(FakeClient):
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            events.append(("close",))

        async def post(self, path, *, headers, content):
            events.append(("post", path, headers, content))
            if error is not None:
                raise error
            return reply

    monkeypatch.setattr(collector.httpx, "Client", FakeClient)
    monkeypatch.setattr(collector.httpx, "AsyncClient", FakeAsyncClient)
    return events


def invoke(provider, payload, guard, asynchronous):
    if asynchronous:
        return asyncio.run(
            collector.aexecute_native_count(provider, payload, dispatch_guard=guard)
        )
    return collector.execute_native_count(provider, payload, dispatch_guard=guard)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_exact_bytes_one_post_and_native_target(monkeypatch, asynchronous):
    events = fake_clients(monkeypatch)
    result = invoke(
        adapter(),
        _PAYLOAD,
        lambda payload, target: events.append(("guard", payload, target)),
        asynchronous,
    )
    assert [item[0] for item in events] == ["guard", "client", "post", "close"]
    assert events[1][1]["base_url"] == "https://api.openai.com/v1/"
    assert events[1][1]["trust_env"] is False
    assert events[1][1]["follow_redirects"] is False
    assert events[2][1] == "responses/input_tokens"
    assert events[2][3] is _PAYLOAD
    assert events[2][2]["Content-Type"] == "application/json"
    assert dict(result.wire_target)["path"] == "responses/input_tokens"
    assert result.status_code == 200
    assert result.request_sha256 == digest(_PAYLOAD)
    assert result.response_request_id == "request-offline-1"
    assert (
        result.response_bytes == b'{"object":"response.input_tokens","input_tokens":7}'
    )


@pytest.mark.parametrize("asynchronous", [False, True])
def test_blocked_guard_creates_no_client_and_no_post(monkeypatch, asynchronous):
    events = fake_clients(monkeypatch)

    def blocked(_payload, _target):
        events.append(("guard",))
        raise ModelGatewayRejected(reason_code="BUDGET_BINDING_STALE")

    with pytest.raises(ModelGatewayRejected, match="model gateway request rejected"):
        invoke(adapter(), _PAYLOAD, blocked, asynchronous)
    assert events == [("guard",)]


def test_sync_collector_rejects_async_guard_before_client(monkeypatch):
    events = fake_clients(monkeypatch)

    async def async_guard(_payload, _target):
        events.append(("guard",))

    with pytest.raises(ModelGatewayRejected):
        collector.execute_native_count(adapter(), _PAYLOAD, dispatch_guard=async_guard)
    assert events == []


def test_async_collector_awaits_guard_before_client(monkeypatch):
    events = fake_clients(monkeypatch)

    async def async_guard(_payload, _target):
        events.append(("guard",))

    asyncio.run(
        collector.aexecute_native_count(adapter(), _PAYLOAD, dispatch_guard=async_guard)
    )
    assert [event[0] for event in events] == ["guard", "client", "post", "close"]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_http_error_body_is_retained_without_retry(monkeypatch, asynchronous):
    error_body = b'{"error":"offline refusal"}'
    events = fake_clients(
        monkeypatch,
        response=httpx.Response(500, content=error_body),
    )
    result = invoke(adapter(), _PAYLOAD, lambda *_args: None, asynchronous)
    assert result.status_code == 500
    assert result.response_bytes == error_body
    assert len([event for event in events if event[0] == "post"]) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
def test_timeout_is_safe_failure_without_retry(monkeypatch, asynchronous):
    events = fake_clients(monkeypatch, error=httpx.ReadTimeout("offline timeout"))
    with pytest.raises(ModelProviderTimeout) as failure:
        invoke(adapter(), _PAYLOAD, lambda *_args: None, asynchronous)
    assert failure.value.safe_transport_error_code == "READ_TIMEOUT"
    assert len([event for event in events if event[0] == "post"]) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
def test_guard_detects_adapter_mutation_before_client(monkeypatch, asynchronous):
    events = fake_clients(monkeypatch)
    provider = adapter()

    def mutate(_payload, _target):
        provider._base_url = "https://compatible.invalid/v1/"

    with pytest.raises(ModelGatewayRejected) as failure:
        invoke(provider, _PAYLOAD, mutate, asynchronous)
    assert failure.value.reason_code == "BUDGET_BINDING_STALE"
    assert events == []


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize(
    "invalid",
    [
        _PAYLOAD + b" ",
        _PAYLOAD.replace(b'"input":"task"', b'"input":["task"]'),
        _PAYLOAD.replace(b"gpt-5.6-luna", b"gpt-5.6-sol"),
        _PAYLOAD.replace(b'"json_object"', b'"json_schema"'),
        _PAYLOAD[:-1] + b',"max_output_tokens":2048}',
        _PAYLOAD[:-1] + b',"service_tier":"default"}',
        b'{"model":"gpt-5.6-luna","model":"gpt-5.6-luna"}',
        _PAYLOAD.replace(b'"input":"task"', b'"input":NaN'),
    ],
)
def test_invalid_count_payload_never_creates_client(monkeypatch, asynchronous, invalid):
    events = fake_clients(monkeypatch)
    if invalid == _PAYLOAD + b" ":
        # Whitespace is valid JSON; exact bytes are retained instead of reserialized.
        result = invoke(adapter(), invalid, lambda *_args: None, asynchronous)
        assert result.request_sha256 == digest(invalid)
        assert len([event for event in events if event[0] == "post"]) == 1
        return
    with pytest.raises(ModelGatewayRejected):
        invoke(adapter(), invalid, lambda *_args: None, asynchronous)
    assert events == []


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_adapter_and_mandatory_guard_only(monkeypatch, asynchronous):
    events = fake_clients(monkeypatch)

    class SubclassAdapter(ProtocolHTTPProviderAdapter):
        pass

    for provider in (
        adapter(base_url="https://compatible.invalid/v1"),
        adapter(custom=object()),
        adapter(cls=SubclassAdapter),
    ):
        with pytest.raises(ModelGatewayRejected):
            invoke(provider, _PAYLOAD, lambda *_args: None, asynchronous)
    with pytest.raises(ModelGatewayRejected):
        invoke(adapter(), _PAYLOAD, None, asynchronous)
    assert events == []


@pytest.mark.parametrize("asynchronous", [False, True])
def test_legacy_count_entrypoints_preserve_response_identity_and_one_guarded_post(
    monkeypatch: pytest.MonkeyPatch, asynchronous: bool,
) -> None:
    from importlib import import_module

    from embedded_copilot.model_gateway.invocation import (
        openai_count_collector as legacy,
    )

    native = import_module("embedded_copilot.model_gateway.adapters.openai_count_collector")
    assert native.CounterHTTPResponse is legacy.CounterHTTPResponse
    assert legacy.CounterHTTPResponse.__module__ == (
        "embedded_copilot.model_gateway.invocation.openai_count_collector"
    )
    events = fake_clients(monkeypatch)

    def guard(payload: bytes, target: tuple) -> None:
        assert payload is _PAYLOAD
        events.append(("guard", payload, target))

    if asynchronous:
        result = asyncio.run(legacy.aexecute_native_count(adapter(), _PAYLOAD, dispatch_guard=guard))
    else:
        result = legacy.execute_native_count(adapter(), _PAYLOAD, dispatch_guard=guard)
    assert type(result) is legacy.CounterHTTPResponse
    assert [event[0] for event in events] == ["guard", "client", "post", "close"]
    assert events[2][3] is _PAYLOAD
    assert result.request_sha256 == digest(_PAYLOAD)
    assert result.response_bytes == b'{"object":"response.input_tokens","input_tokens":7}'


def test_count_adapter_imports_in_fresh_process_without_creating_clients(tmp_path: Path) -> None:
    import subprocess
    import sys

    script = """
import httpx
class NoClient:
    def __init__(self, *args, **kwargs):
        raise AssertionError("IMPORT_MUST_NOT_CREATE_CLIENT")
httpx.Client = NoClient
httpx.AsyncClient = NoClient
from embedded_copilot.model_gateway.adapters.openai_count_collector import CounterHTTPResponse
from embedded_copilot.model_gateway.invocation import openai_count_collector as legacy
assert CounterHTTPResponse is legacy.CounterHTTPResponse
print("COUNT_IMPORT_OK")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path,
        capture_output=True, text=True, check=True, timeout=30,
    )
    assert completed.stdout == "COUNT_IMPORT_OK\n"
    assert completed.stderr == ""
    assert list(tmp_path.iterdir()) == []
