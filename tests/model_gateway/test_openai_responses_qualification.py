"""Offline only: synthetic host authorities are never provider qualification."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from embedded_copilot.model_gateway import ModelGatewayService
from embedded_copilot.model_gateway.adapters.protocol_http import (
    ProtocolHTTPProviderAdapter,
)
from embedded_copilot.model_gateway.exceptions import ModelGatewayRejected
from embedded_copilot.model_gateway.governance import (
    BoundModelIdentity,
    EngineeringStage,
)
from embedded_copilot.model_gateway.invocation.budget import digest, wire_target_digest
from embedded_copilot.model_gateway.invocation.contracts import (
    SingleInvocationBudgetEnvelopeV1,
)
from embedded_copilot.model_gateway.invocation.openai_responses_qualification import (
    CountObservation,
    OpenAIResponsesCapSource,
    OpenAIResponsesCountSource,
    OpenAIResponsesTariffSource,
    OpenAIResponsesTransportSource,
    TariffObservation,
    count_request_from_final,
    parse_count_response,
)
from embedded_copilot.model_gateway.invocation.qualified_budget_producer import (
    CapPremise,
    CountPremise,
    GrantedBudget,
    QualificationContext,
    QualifiedInvocationBudgetProducer,
    TariffPremise,
    TransportPremise,
    WitnessHeader,
    _canonical,
)
from embedded_copilot.model_gateway.profiles import ProtocolFamily
from embedded_copilot.model_gateway.transport import ResolvedTransportEntry
from embedded_copilot.production_release.provider import ReloadableModelGateway

_NOW = datetime(2026, 10, 14, tzinfo=UTC)
_FINAL = (
    b'{"model":"gpt-5.6-luna","instructions":"system","input":"task",'
    b'"max_output_tokens":2048,"text":{"format":{"type":"json_object"}}}'
)
_COUNT_REQUEST = (
    b'{"model":"gpt-5.6-luna","instructions":"system","input":"task",'
    b'"text":{"format":{"type":"json_object"}}}'
)
_RESPONSE = b'{"object":"response.input_tokens","input_tokens":8192}'


class Reader:
    def __init__(self, value, mutate=None):
        self.value = value
        self.mutate = mutate

    def read(self, _context):
        if self.mutate is not None:
            self.mutate()
        return self.value


def context(final=_FINAL, *, target_url="https://api.openai.com/v1/"):
    identity = BoundModelIdentity(
        provider_id="openai",
        endpoint_id="endpoint-openai",
        credential_ref="credential:offline",
        model_profile_id="luna-profile",
        model_id="gpt-5.6-luna",
    )
    target = (
        ("provider_id", "openai"),
        ("endpoint_id", identity.endpoint_id),
        ("base_url", target_url),
        ("path", "responses"),
        ("protocol", "OPENAI_RESPONSES"),
        ("noncredential_headers", (("content-type", "application/json"),)),
        ("credential_header_names", ("authorization",)),
    )
    fp = digest(b"offline")
    return QualificationContext(
        bound_request=SimpleNamespace(
            system_instruction="system",
            task="task",
            model_policy=SimpleNamespace(max_output_tokens=2048),
        ),
        execution_snapshot=SimpleNamespace(schema_version=2, selected_model=identity),
        selected_model=identity,
        task_authority=object(),
        final_transport_bytes=final,
        wire_target=target,
        request_fingerprint=fp,
        snapshot_fingerprint=fp,
        selected_model_fingerprint=fp,
        connection_id="connection-openai",
        connection_revision=fp,
        credential_revision="credential-r1",
        endpoint_revision="endpoint-r1",
        protocol_family=ProtocolFamily.OPENAI_RESPONSES,
        wire_target_sha256=wire_target_digest(target),
        final_transport_sha256=digest(final),
        provider_parameter="max_output_tokens",
        output_token_limit=2048,
        tenant_id="tenant",
        project_id="project",
        conversation_id="conversation",
        task_id="task-id",
        task_authority_fingerprint=fp,
        engineering_stage=EngineeringStage.HARDWARE_SELECTION,
        now=_NOW,
        scope_fingerprint=fp,
    )


def header(
    ctx,
    name,
    raw=b"externally qualified offline fixture",
    *,
    revision="revision-1",
    valid_until=None,
):
    raw_sha = digest(raw)
    observed = ctx.now - timedelta(minutes=1)
    expiry = valid_until or ctx.now + timedelta(minutes=1)
    material = (
        name,
        name + "-identity",
        revision,
        raw_sha,
        ctx.scope_fingerprint,
        observed.isoformat(),
        expiry.isoformat(),
    )
    return WitnessHeader(
        name,
        name + "-identity",
        revision,
        raw,
        raw_sha,
        digest(_canonical(material)),
        ctx.scope_fingerprint,
        observed,
        expiry,
    )


def count_source(ctx, response=_RESPONSE, *, witness_raw=None, **changes):
    request = count_request_from_final(ctx)
    request_sha = digest(request)
    response_sha = digest(response)
    counter_target = tuple(
        (key, "responses/input_tokens" if key == "path" else value)
        for key, value in ctx.wire_target
    )
    raw = witness_raw or _canonical(
        {
            "generation_sha256": ctx.final_transport_sha256,
            "request_sha256": request_sha,
            "response_sha256": response_sha,
            "scope_fingerprint": ctx.scope_fingerprint,
            "counter_wire_target_sha256": wire_target_digest(counter_target),
            "http_status": 200,
        }
    )
    witness = header(ctx, "offline-count", raw)
    premise = CountPremise(witness, "EXACT", 8192, True)
    observation = CountObservation(
        premise,
        ctx.final_transport_sha256,
        request,
        request_sha,
        response,
        response_sha,
        200,
        counter_target,
    )
    return OpenAIResponsesCountSource(
        "offline-count", Reader(replace(observation, **changes))
    )


def cap_source(ctx, premise=None):
    premise = premise or CapPremise(
        header(ctx, "offline-cap"), 2048, "max_output_tokens", True, True
    )
    return OpenAIResponsesCapSource("offline-cap", Reader(premise))


def tariff_source(ctx, premise=None, **changes):
    premise = premise or TariffPremise(
        header(ctx, "offline-tariff"),
        "USD",
        "PER_1M_TOKENS",
        "0.01",
        "0.02",
        None,
        None,
        "NONE",
        "KNOWN_NONE",
        "0",
        (
            ("provider", "OPENAI"),
            ("model", "gpt-5.6-luna"),
            ("region", "STANDARD"),
            ("service_tier", "DEFAULT"),
            ("input_token_band", "LE_8192"),
            ("normalized_usage_scope", "ALL_BILLABLE"),
        ),
    )
    settings_raw = _canonical(
        {
            "effective_service_tier": "DEFAULT",
            "billing_region_class": "STANDARD",
            "actual_processing_region": "us-east-1",
            "processing_mode": "STANDARD",
            "generation_sha256": ctx.final_transport_sha256,
            "scope_fingerprint": ctx.scope_fingerprint,
            "tariff_witness_fingerprint": premise.witness.witness_fingerprint,
        }
    )
    observed = TariffObservation(
        premise,
        header(ctx, "offline-effective-settings", settings_raw),
        "DEFAULT",
        "STANDARD",
        "us-east-1",
        "STANDARD",
    )
    return OpenAIResponsesTariffSource(
        "offline-tariff", Reader(replace(observed, **changes))
    )


def transport_source(
    ctx,
    *,
    base_url="https://api.openai.com/v1",
    custom=None,
    adapter_type=ProtocolHTTPProviderAdapter,
):
    adapter = adapter_type(
        provider="openai",
        endpoint_id=ctx.selected_model.endpoint_id,
        base_url=base_url,
        protocol_family=ProtocolFamily.OPENAI_RESPONSES,
        api_key="offline-placeholder",
        http_transport=custom,
    )
    entry = ResolvedTransportEntry(
        provider_id="openai",
        endpoint_id=ctx.selected_model.endpoint_id,
        credential_ref=ctx.selected_model.credential_ref,
        adapter=adapter,
        connection_id=ctx.connection_id,
        endpoint_revision=ctx.endpoint_revision,
        credential_revision=ctx.credential_revision,
    )
    service = ModelGatewayService(providers={"openai": adapter})
    gateway = ReloadableModelGateway()
    gateway.install_transport(service, (entry,))
    premise = TransportPremise(
        header(ctx, "offline-transport"),
        ctx.final_transport_sha256,
        ctx.wire_target_sha256,
        ctx.protocol_family,
        ctx.provider_parameter,
        ctx.output_token_limit,
        True,
    )
    source = OpenAIResponsesTransportSource(
        "offline-transport", Reader(premise), gateway
    )
    return source, gateway, service, entry


def test_strict_projection_and_count_boundary():
    ctx = context()
    assert count_request_from_final(ctx) == _COUNT_REQUEST
    assert parse_count_response(_RESPONSE) == 8192
    assert count_source(ctx).read(ctx).input_token_bound == 8192


@pytest.mark.parametrize(
    "raw",
    [
        b'{"model":"gpt-5.6-luna","model":"gpt-5.6-luna"}',
        _FINAL.replace(b'"max_output_tokens":2048', b'"max_output_tokens":true'),
        _FINAL.replace(b'"max_output_tokens":2048', b'"max_output_tokens":2048.0'),
        _FINAL.replace(b'"input":"task"', b'"input":["task"]'),
        _FINAL.replace(b'"instructions":"system"', b'"instructions":"wrong"'),
        _FINAL.replace(b'"input":"task"', b'"input":"wrong"'),
        _FINAL.replace(b'"json_object"', b'"json_schema"'),
        _FINAL.replace(b"gpt-5.6-luna", b"gpt-5.6-sol"),
        _FINAL[:-1] + b',"tools":[]}',
        _FINAL.replace(b'"input":"task"', b'"input":NaN'),
    ],
)
def test_wrong_final_generation_bytes_rejected(raw):
    with pytest.raises(ModelGatewayRejected):
        count_request_from_final(context(raw))


@pytest.mark.parametrize(
    "raw",
    [
        b'{"object":"response.input_tokens","input_tokens":8193}',
        b'{"object":"response.input_tokens","input_tokens":true}',
        b'{"object":"response.input_tokens","input_tokens":1.5}',
        b'{"object":"response.input_tokens","input_tokens":-1}',
        b'{"object":"response.input_tokens","input_tokens":NaN}',
        b'{"object":"response.input_tokens","input_tokens":1,"input_tokens":2}',
        b'{"object":"wrong","input_tokens":1}',
        b'{"object":"response.input_tokens","input_tokens":1,"extra":0}',
    ],
)
def test_invalid_count_result_rejected(raw):
    with pytest.raises(ModelGatewayRejected):
        parse_count_response(raw)


def test_count_requires_current_external_witness_and_matching_raw_hashes():
    ctx = context()
    source = count_source(ctx)
    for change in (
        {"final_transport_sha256": digest(b"other")},
        {"request_sha256": digest(b"other")},
        {"response_sha256": digest(b"other")},
        {"request_bytes": b"{}"},
    ):
        with pytest.raises(ModelGatewayRejected):
            count_source(ctx, **change).read(ctx)
    source._reader.value = replace(
        source._reader.value,
        premise=replace(
            source._reader.value.premise,
            witness=replace(
                source._reader.value.premise.witness,
                valid_until=ctx.now,
            ),
        ),
    )
    with pytest.raises(ModelGatewayRejected):
        source.read(ctx)


def test_count_requires_http_200_and_native_counter_wire_target():
    ctx = context()
    for change in (
        {"http_status": 500},
        {"http_status": True},
        {
            "counter_wire_target": tuple(
                (key, "responses" if key == "path" else value)
                for key, value in count_source(ctx)._reader.value.counter_wire_target
            )
        },
        {
            "counter_wire_target": tuple(
                (key, "https://compatible.invalid/v1/" if key == "base_url" else value)
                for key, value in count_source(ctx)._reader.value.counter_wire_target
            )
        },
    ):
        with pytest.raises(ModelGatewayRejected):
            count_source(ctx, **change).read(ctx)
    compatible_ctx = context(target_url="https://compatible.invalid/v1/")
    with pytest.raises(ModelGatewayRejected):
        count_source(compatible_ctx).read(compatible_ctx)


@pytest.mark.parametrize(
    "raw",
    [
        b"unrelated text containing sha256 strings",
        _canonical(
            {
                "generation_sha256": digest(b"wrong"),
                "request_sha256": digest(_COUNT_REQUEST),
                "response_sha256": digest(_RESPONSE),
                "scope_fingerprint": digest(b"offline"),
            }
        ),
        b'{"generation_sha256":"one","generation_sha256":"two"}',
    ],
)
def test_count_witness_binding_requires_exact_strict_fields(raw):
    ctx = context()
    with pytest.raises(ModelGatewayRejected):
        count_source(ctx, witness_raw=raw).read(ctx)


def test_cap_and_tariff_require_external_current_full_premises():
    ctx = context()
    assert cap_source(ctx).read(ctx).includes_reasoning is True
    assert tariff_source(ctx).read(ctx).input_price_decimal == "0.01"
    cap = cap_source(ctx)._reader.value
    tariff = tariff_source(ctx)._reader.value.premise
    for premise in (
        replace(cap, includes_reasoning=False),
        replace(cap, output_token_limit=1024),
    ):
        with pytest.raises(ModelGatewayRejected):
            cap_source(ctx, premise).read(ctx)
    for premise in (
        replace(tariff, pricing_conditions=()),
        replace(
            tariff,
            pricing_conditions=tariff.pricing_conditions + (("region", "OTHER"),),
        ),
        replace(tariff, additional_fees_state="UNKNOWN"),
        replace(tariff, cache_semantics="EXCLUSIVE_REPLACEMENT"),
        replace(tariff, input_price_decimal="0.1e-3"),
    ):
        with pytest.raises(ModelGatewayRejected):
            tariff_source(ctx, premise).read(ctx)
    with pytest.raises(ModelGatewayRejected):
        cap_source(
            ctx,
            replace(
                cap, witness=replace(cap.witness, scope_fingerprint=digest(b"wrong"))
            ),
        ).read(ctx)
    for source in (count_source(ctx), cap_source(ctx), tariff_source(ctx)):
        source._reader = Reader(None)
        with pytest.raises(ModelGatewayRejected):
            source.read(ctx)


def test_tariff_requires_independent_current_effective_processing_witness():
    ctx = context()
    current = tariff_source(ctx)._reader.value.effective_conditions_witness
    cases = (
        {"effective_conditions_witness": None},
        {"effective_conditions_witness": replace(current, valid_until=ctx.now)},
        {
            "effective_conditions_witness": replace(
                current, scope_fingerprint=digest(b"foreign")
            )
        },
        {"effective_service_tier": "AUTO"},
        {"billing_region_class": "FEDRAMP"},
        {"billing_region_class": "OTHER"},
        {"actual_processing_region": ""},
        {"processing_mode": "FLEX"},
    )
    for change in cases:
        with pytest.raises(ModelGatewayRejected):
            tariff_source(ctx, **change).read(ctx)
    expired = header(
        ctx,
        "offline-effective-settings",
        current.raw_witness,
        valid_until=ctx.now,
    )
    with pytest.raises(ModelGatewayRejected):
        tariff_source(ctx, effective_conditions_witness=expired).read(ctx)


def test_settings_revision_is_bound_into_returned_tariff_conditions():
    ctx = context()
    source = tariff_source(ctx)
    original = source._reader.value.premise
    first = source.read(ctx)
    settings = source._reader.value.effective_conditions_witness
    changed = header(
        ctx,
        "offline-effective-settings",
        settings.raw_witness,
        revision="revision-2",
    )
    source._reader.value = replace(
        source._reader.value, effective_conditions_witness=changed
    )
    second = source.read(ctx)
    assert (
        original.pricing_conditions == source._reader.value.premise.pricing_conditions
    )
    assert first.witness is original.witness
    assert second.witness is original.witness
    assert first.pricing_conditions != second.pricing_conditions
    assert (
        dict(second.pricing_conditions)["effective_conditions_revision"] == "revision-2"
    )
    assert (
        dict(second.pricing_conditions)["effective_conditions_witness_fingerprint"]
        == changed.witness_fingerprint
    )


def test_transport_is_native_exact_and_rejects_custom_compatible_or_swap():
    ctx = context()
    source, gateway, service, entry = transport_source(ctx)
    assert source.read(ctx).single_send_semantics is True
    with pytest.raises(ModelGatewayRejected):
        transport_source(context(target_url="https://compatible.invalid/v1/"))[0].read(
            context(target_url="https://compatible.invalid/v1/")
        )
    with pytest.raises(ModelGatewayRejected):
        transport_source(ctx, base_url="https://compatible.invalid/v1")[0].read(ctx)
    with pytest.raises(ModelGatewayRejected):
        transport_source(ctx, custom=object())[0].read(ctx)

    class SubclassAdapter(ProtocolHTTPProviderAdapter):
        pass

    with pytest.raises(ModelGatewayRejected):
        transport_source(ctx, adapter_type=SubclassAdapter)[0].read(ctx)
    missing = transport_source(ctx)[0]
    missing._reader = Reader(None)
    with pytest.raises(ModelGatewayRejected):
        missing.read(ctx)
    source._reader.mutate = lambda: gateway.rebind_transport(service, (entry,))
    with pytest.raises(ModelGatewayRejected):
        source.read(ctx)


def test_existing_producer_composes_only_offline_mock_authorities():
    ctx = context()
    envelope = SingleInvocationBudgetEnvelopeV1(
        envelope_id="offline-envelope",
        tenant_id=ctx.tenant_id,
        project_id=ctx.project_id,
        conversation_id=ctx.conversation_id,
        task_id=ctx.task_id,
        task_authority_fingerprint=ctx.task_authority_fingerprint,
        engineering_stage=ctx.engineering_stage,
        not_after=ctx.now + timedelta(minutes=1),
    )
    grant = Reader(GrantedBudget(header(ctx, "offline-grant"), envelope))
    grant.authority_id = "offline-grant"
    count = count_source(ctx)
    cap = cap_source(ctx)
    tariff = tariff_source(ctx)
    transport = transport_source(ctx)[0]
    producer = QualifiedInvocationBudgetProducer(
        grant_source=grant,
        count_source=count,
        cap_source=cap,
        tariff_source=tariff,
        transport_source=transport,
    )
    _, values, heads = producer._collect(ctx)
    assert len(heads) == 5
    assert values["count_kind"] == "EXACT"
    assert values["input_token_bound"] == 8192
    assert values["output_token_limit"] == 2048
    settings = tariff._reader.value.effective_conditions_witness
    tariff._reader.value = replace(
        tariff._reader.value,
        effective_conditions_witness=header(
            ctx,
            "offline-effective-settings",
            settings.raw_witness,
            revision="revision-2",
        ),
    )
    _, changed_values, changed_heads = producer._collect(ctx)
    assert changed_heads == heads
    assert changed_values["pricing_conditions"] != values["pricing_conditions"]


@pytest.mark.parametrize(
    ("attribute", "changed"),
    [
        ("_provider", "other"),
        ("_endpoint_id", "other"),
        ("_base_url", "https://compatible.invalid/v1/"),
        ("_family", ProtocolFamily.OPENAI_CHAT),
        ("_supports_image_input", True),
        ("_sync_transport", object()),
        ("_async_transport", object()),
        ("_api_key", None),
    ],
)
def test_transport_rechecks_adapter_after_external_reader(attribute, changed):
    ctx = context()
    source, _, _, entry = transport_source(ctx)
    source._reader.mutate = lambda: setattr(entry.adapter, attribute, changed)
    with pytest.raises(ModelGatewayRejected):
        source.read(ctx)


def test_transport_rechecks_gateway_entry_after_external_reader():
    ctx = context()
    source, _, service, _ = transport_source(ctx)
    source._reader.mutate = lambda: setattr(service, "_transport_entries", {})
    with pytest.raises(ModelGatewayRejected):
        source.read(ctx)
