"""Private one-shot HTTP collector for a guarded native Responses input count.

Only an already held gateway transport may call this adapter. These functions do not
register a source, qualify a witness, retry, persist, or dispatch on import.
"""

from __future__ import annotations

import asyncio
import inspect
import json

import httpx

from .._transport_metadata import _provider_request_id
from ..exceptions import (
    ModelGatewayRejected,
    ModelProviderNetworkError,
    ModelProviderTimeout,
)
from ..invocation.budget import digest
from ..invocation.openai_count_collector import CounterHTTPResponse
from ..profiles import ProtocolFamily

_COUNT_PATH = "responses/input_tokens"
_NATIVE_URL = "https://api.openai.com/v1/"
_COUNT_FIELDS = {"model", "instructions", "input", "text"}
_TEXT = {"format": {"type": "json_object"}}


def _reject(code="BUDGET_EVIDENCE_UNAVAILABLE"):
    raise ModelGatewayRejected(reason_code=code)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _reject()
        result[key] = value
    return result


def _nonfinite(_value):
    _reject()


def _validate_payload(payload: bytes) -> None:
    if type(payload) is not bytes or not payload:
        _reject()
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_constant=_nonfinite,
        )
    except (UnicodeError, ValueError, TypeError):
        _reject()
    if (
        type(value) is not dict
        or set(value) != _COUNT_FIELDS
        or value["model"] != "gpt-5.6-luna"
        or type(value["instructions"]) is not str
        or type(value["input"]) is not str
        or value["text"] != _TEXT
    ):
        _reject()


def _adapter_and_target(adapter, payload, dispatch_guard):
    # Local import avoids the adapter package import cycle.
    from .protocol_http import ProtocolHTTPProviderAdapter

    if type(adapter) is not ProtocolHTTPProviderAdapter or not callable(dispatch_guard):
        _reject()
    if (
        adapter._provider != "openai"
        or adapter._family is not ProtocolFamily.OPENAI_RESPONSES
        or adapter._base_url != _NATIVE_URL
        or adapter._sync_transport is not None
        or adapter._async_transport is not None
        or adapter._supports_image_input is not False
        or adapter._api_key is None
    ):
        _reject()
    _validate_payload(payload)
    identity = adapter._transport_identity()
    headers = adapter._headers()
    if (
        type(headers) is not dict
        or {name.lower() for name in headers} != {"content-type", "authorization"}
        or headers.get("Content-Type") != "application/json"
    ):
        _reject()
    target = adapter._budget_wire_target(_COUNT_PATH, headers)
    if target != (
        ("provider_id", "openai"),
        ("endpoint_id", adapter._endpoint_id),
        ("base_url", _NATIVE_URL),
        ("path", _COUNT_PATH),
        ("protocol", ProtocolFamily.OPENAI_RESPONSES.value),
        ("noncredential_headers", (("content-type", "application/json"),)),
        ("credential_header_names", ("authorization",)),
    ):
        _reject()
    return identity, headers, target


def _check_held(adapter, identity, headers, target) -> None:
    if (
        adapter._transport_identity() != identity
        or adapter._provider != "openai"
        or adapter._family is not ProtocolFamily.OPENAI_RESPONSES
        or adapter._base_url != _NATIVE_URL
        or adapter._sync_transport is not None
        or adapter._async_transport is not None
        or adapter._budget_wire_target(_COUNT_PATH, headers) != target
    ):
        _reject("BUDGET_BINDING_STALE")


def _request_id(response: httpx.Response) -> str | None:
    for name in ("x-request-id", "request-id", "trace-id"):
        candidate = response.headers.get(name)
        if candidate is None:
            continue
        try:
            return _provider_request_id(candidate)
        except (TypeError, ValueError):
            return None
    return None




def _result(
    response: httpx.Response, payload: bytes, target: tuple
) -> CounterHTTPResponse:
    return CounterHTTPResponse(
        status_code=response.status_code,
        response_bytes=response.content,
        request_sha256=digest(payload),
        wire_target=target,
        response_request_id=_request_id(response),
    )


def execute_native_count(
    adapter, payload: bytes, *, dispatch_guard
) -> CounterHTTPResponse:
    """Guard then POST exact bytes once; retain raw HTTP error bodies."""
    from .protocol_http import _httpx_error_code, _httpx_transport_stage

    identity, headers, target = _adapter_and_target(adapter, payload, dispatch_guard)
    guarded = dispatch_guard(payload, target)
    if inspect.isawaitable(guarded):
        if inspect.iscoroutine(guarded):
            guarded.close()
        _reject()
    _check_held(adapter, identity, headers, target)
    try:
        with httpx.Client(
            base_url=adapter._base_url,
            timeout=adapter._timeout_seconds,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            response = client.post(_COUNT_PATH, headers=headers, content=payload)
        return _result(response, payload, target)
    except httpx.TimeoutException as error:
        raise ModelProviderTimeout(
            safe_transport_error_code=_httpx_error_code(error),
            safe_transport_stage=_httpx_transport_stage(error),
        ) from None
    except httpx.HTTPError as error:
        raise ModelProviderNetworkError(
            safe_transport_error_code=_httpx_error_code(error),
            safe_transport_stage=_httpx_transport_stage(error),
        ) from None


async def aexecute_native_count(
    adapter, payload: bytes, *, dispatch_guard
) -> CounterHTTPResponse:
    """Async counterpart with the same pre-client guard and one POST."""
    from .protocol_http import _httpx_error_code, _httpx_transport_stage

    identity, headers, target = _adapter_and_target(adapter, payload, dispatch_guard)
    guarded = dispatch_guard(payload, target)
    if inspect.isawaitable(guarded):
        await guarded
    _check_held(adapter, identity, headers, target)
    try:
        async with httpx.AsyncClient(
            base_url=adapter._base_url,
            timeout=adapter._timeout_seconds,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            response = await client.post(_COUNT_PATH, headers=headers, content=payload)
        return _result(response, payload, target)
    except asyncio.CancelledError:
        raise
    except httpx.TimeoutException as error:
        raise ModelProviderTimeout(
            safe_transport_error_code=_httpx_error_code(error),
            safe_transport_stage=_httpx_transport_stage(error),
        ) from None
    except httpx.HTTPError as error:
        raise ModelProviderNetworkError(
            safe_transport_error_code=_httpx_error_code(error),
            safe_transport_stage=_httpx_transport_stage(error),
        ) from None
