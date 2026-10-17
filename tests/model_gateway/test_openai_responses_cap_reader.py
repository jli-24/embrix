"""Offline mapping of pinned official SDK bytes; no host qualification is implied."""

from dataclasses import replace
from pathlib import Path

import pytest

from embedded_copilot.model_gateway.exceptions import ModelGatewayRejected
from embedded_copilot.model_gateway.invocation.budget import digest
from embedded_copilot.model_gateway.invocation.openai_responses_cap_reader import (
    OpenAIResponsesSDKCapReader,
    _documented_cap,
)
from embedded_copilot.model_gateway.invocation.openai_responses_qualification import (
    OpenAIResponsesCapSource,
)
from embedded_copilot.model_gateway.invocation.qualified_budget_producer import (
    _canonical,
)
from embedded_copilot.model_gateway.invocation.qualified_bundle_producer import (
    QualifiedCounterBundleProducer,
)
from tests.model_gateway.test_counter_bundle_core import _case, _change
from tests.model_gateway.test_openai_responses_qualification import (
    _FINAL,
    Reader,
    context,
    header,
)
from tests.model_gateway.test_qualified_bundle_producer import _context, _producer

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "openai_responses_cap"
    / "response_create_params.py.txt"
)
_RAW = _FIXTURE.read_bytes()
_COMMIT = "2c1ae5266a67303d82ea46c57be854deaa6e5676"
_URL = (
    "https://raw.githubusercontent.com/openai/openai-python/"
    f"{_COMMIT}/src/openai/types/responses/response_create_params.py"
)
_EVIDENCE_ID = f"openai-responses-cap-sdk-{_COMMIT}"


def sdk_header(
    ctx, raw=_RAW, *, identity=_EVIDENCE_ID, revision=_COMMIT, expiry=None, scope=None
):
    head = header(ctx, "offline-cap", raw, revision=revision, valid_until=expiry)
    head = replace(
        head,
        evidence_identity=identity,
        scope_fingerprint=scope or ctx.scope_fingerprint,
    )
    material = (
        head.authority_id,
        head.evidence_identity,
        head.revision,
        head.raw_sha256,
        head.scope_fingerprint,
        head.observed_at.isoformat(),
        head.valid_until.isoformat(),
    )
    return replace(head, witness_fingerprint=digest(_canonical(material)))


def cap_source(ctx, head=None):
    reader = Reader(head if head is not None else sdk_header(ctx))
    sdk_reader = OpenAIResponsesSDKCapReader("offline-cap", reader)
    return OpenAIResponsesCapSource("offline-cap", sdk_reader), reader


class CurrentSDKWitnessReader:
    def __init__(self):
        self.calls = 0
        self.last_head = None

    def read(self, current):
        self.calls += 1
        self.last_head = sdk_header(current)
        return self.last_head


def v2_sdk_producer(inputs):
    producer, _ = _producer(inputs)
    witness_reader = CurrentSDKWitnessReader()
    native_cap = OpenAIResponsesCapSource(
        "offline-cap", OpenAIResponsesSDKCapReader("offline-cap", witness_reader)
    )
    kinds = ("grant", "counter_fee", "cap", "tariff", "transport")
    sources = dict(zip(kinds, producer._sources))
    sources["cap"] = native_cap
    producer = QualifiedCounterBundleProducer(
        **{kind + "_source": sources[kind] for kind in kinds}
    )
    return producer, witness_reader


def test_pinned_sdk_witness_flows_through_existing_source_and_v2_producer():
    ctx = context()
    source, _ = cap_source(ctx)
    premise = source.read(ctx)
    assert premise.witness.raw_witness is _RAW
    assert premise.output_token_limit == 2048
    assert premise.provider_parameter == "max_output_tokens"
    assert premise.covers_all_billable_output is True
    assert premise.includes_reasoning is True

    inputs, _ = _case()
    producer, witness_reader = v2_sdk_producer(inputs)
    envelope, admission = producer.prepare_counter(**_context(inputs))
    assert envelope.schema_version == 2
    assert admission.generation_ceiling.includes_reasoning is True
    assert (
        admission.generation_ceiling.cap_evidence_fingerprint
        == witness_reader.last_head.witness_fingerprint
    )
    assert witness_reader.calls == 1


def test_v2_counter_preflight_rejects_changed_connection_revision():
    inputs, _ = _case()
    producer, witness_reader = v2_sdk_producer(inputs)
    envelope, admission = producer.prepare_counter(**_context(inputs))
    current = _context(inputs)
    current["snapshot"] = _change(
        inputs["snapshot"], connection_revision=digest(b"changed-connection-revision")
    )
    with pytest.raises(ModelGatewayRejected):
        producer.qualify_counter(envelope=envelope, admission=admission, **current)
    assert witness_reader.calls == 2


@pytest.mark.parametrize(
    "change",
    (
        {"raw": _RAW + b"\n# tampered\n"},
        {"identity": "other-official-source"},
        {"revision": "different-commit"},
        {"expiry": context().now},
        {"scope": digest(b"foreign-scope")},
    ),
)
def test_wrong_raw_identity_revision_expiry_or_scope_rejected(change):
    ctx = context()
    source, _ = cap_source(ctx, sdk_header(ctx, **change))
    with pytest.raises(ModelGatewayRejected):
        source.read(ctx)


def test_missing_reasoning_documentation_is_rejected_by_parser():
    raw = _RAW.replace(b"[reasoning tokens]", b"[other tokens]")
    assert raw != _RAW
    with pytest.raises(ModelGatewayRejected):
        _documented_cap(raw)


def test_reader_current_head_change_or_revocation_rejected():
    ctx = context()
    source, reader = cap_source(ctx)
    assert source.read(ctx).output_token_limit == 2048
    reader.value = sdk_header(ctx, expiry=ctx.now)
    with pytest.raises(ModelGatewayRejected):
        source.read(ctx)
    reader.value = None
    with pytest.raises(ModelGatewayRejected):
        source.read(ctx)


@pytest.mark.parametrize(
    "final",
    (
        _FINAL.replace(b"gpt-5.6-luna", b"gpt-5.6-sol"),
        _FINAL.replace(b'"max_output_tokens":2048', b'"max_output_tokens":1024'),
        _FINAL.replace(b'"input":"task"', b'"input":"drift"'),
    ),
)
def test_wrong_model_cap_or_final_bytes_rejected(final):
    ctx = context(final)
    source, _ = cap_source(ctx)
    with pytest.raises(ModelGatewayRejected):
        source.read(ctx)


def test_stale_final_bytes_digest_rejected():
    ctx = context()
    source, _ = cap_source(ctx)
    with pytest.raises(ModelGatewayRejected):
        source.read(replace(ctx, final_transport_sha256=digest(b"drift")))
