"""Private, offline admission for one native OpenAI Responses budget slice.

Readers must be independent, host-held qualification authorities. A digest or
this module's validation never qualifies provider count, tariff, or cap facts.
There is no provider request, credential access, persistence, or registration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from ..profiles import ProtocolFamily
from ._budget_preflight import _UNIT_TOKENS, _exact_rate
from .budget import digest, reject, wire_target_digest
from .qualified_budget_producer import (
    CapPremise,
    CountPremise,
    QualificationContext,
    TariffPremise,
    WitnessHeader,
    _canonical,
)
from .qualified_budget_producer import (
    TransportPremise as TransportPremise,  # noqa: PLC0414 - preserve legacy named re-export.
)

_MODEL = "gpt-5.6-luna"
_CAP = 2048
_INPUT_LIMIT = 8192
_FINAL_FIELDS = {"model", "instructions", "input", "text", "max_output_tokens"}
_COUNT_FIELDS = {"model", "instructions", "input", "text"}
_TEXT = {"format": {"type": "json_object"}}
_TARIFF_CONDITIONS = {
    ("provider", "OPENAI"),
    ("model", _MODEL),
    ("region", "STANDARD"),
    ("service_tier", "DEFAULT"),
    ("input_token_band", "LE_8192"),
    ("normalized_usage_scope", "ALL_BILLABLE"),
}


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            reject()
        result[key] = value
    return result


def _nonfinite(_value):
    reject()


def _strict_object(raw: bytes) -> dict:
    if type(raw) is not bytes or not raw:
        reject()
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_constant=_nonfinite,
        )
    except (UnicodeError, ValueError, TypeError):
        reject()
    if type(value) is not dict:
        reject()
    return value


def _native_context(context: QualificationContext) -> None:
    if (
        type(context) is not QualificationContext
        or context.protocol_family is not ProtocolFamily.OPENAI_RESPONSES
        or context.selected_model.provider_id != "openai"
        or context.selected_model.model_id != _MODEL
        or context.provider_parameter != "max_output_tokens"
        or type(context.output_token_limit) is not int
        or context.output_token_limit != _CAP
        or context.execution_snapshot.schema_version != 2
        or context.execution_snapshot.selected_model != context.selected_model
    ):
        reject()


def _current_header(header: WitnessHeader, context: QualificationContext) -> None:
    if type(header) is not WitnessHeader or type(header.raw_witness) is not bytes:
        reject()
    if not header.raw_witness or header.raw_sha256 != digest(header.raw_witness):
        reject("BUDGET_BINDING_STALE")
    if header.scope_fingerprint != context.scope_fingerprint:
        reject("BUDGET_BINDING_STALE")
    material = (
        header.authority_id,
        header.evidence_identity,
        header.revision,
        header.raw_sha256,
        header.scope_fingerprint,
        header.observed_at.isoformat(),
        header.valid_until.isoformat(),
    )
    if header.witness_fingerprint != digest(_canonical(material)):
        reject()
    if (
        header.observed_at.utcoffset() is None
        or header.valid_until.utcoffset() is None
        or not header.observed_at <= context.now < header.valid_until
    ):
        reject("BUDGET_BINDING_STALE")


def _read_premise(reader, context, expected_type, authority_id):
    if reader is None or not callable(getattr(reader, "read", None)):
        reject()
    premise = reader.read(context)
    if type(premise) is not expected_type:
        reject()
    _current_header(premise.witness, context)
    if premise.witness.authority_id != authority_id:
        reject()
    return premise


def count_request_from_final(context: QualificationContext) -> bytes:
    """Project exact final generation bytes onto the four count endpoint fields."""
    _native_context(context)
    final = _strict_object(context.final_transport_bytes)
    if (
        digest(context.final_transport_bytes) != context.final_transport_sha256
        or (set(final) != _FINAL_FIELDS and not (set(final) == _FINAL_FIELDS | {"service_tier"} and final["service_tier"] == "default"))
        or final["model"] != _MODEL
        or type(final["instructions"]) is not str
        or type(final["input"]) is not str
        or final["text"] != _TEXT
        or type(final["max_output_tokens"]) is not int
        or final["max_output_tokens"] != _CAP
        or final["instructions"] != context.bound_request.system_instruction
        or final["input"] != context.bound_request.task
    ):
        reject()
    projected = {name: final[name] for name in final if name in _COUNT_FIELDS}
    if set(projected) != _COUNT_FIELDS:
        reject()
    return json.dumps(
        projected, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def parse_count_response(raw: bytes) -> int:
    """Accept only the documented object=response.input_tokens count shape."""
    response = _strict_object(raw)
    if set(response) != {"object", "input_tokens"}:
        reject()
    count = response["input_tokens"]
    if (
        response["object"] != "response.input_tokens"
        or type(count) is not int
        or not 0 <= count <= _INPUT_LIMIT
    ):
        reject()
    return count


def _native_wire_target(context: QualificationContext, path: str) -> tuple:
    identity = context.selected_model
    return (
        ("provider_id", identity.provider_id),
        ("endpoint_id", identity.endpoint_id),
        ("base_url", "https://api.openai.com/v1/"),
        ("path", path),
        ("protocol", ProtocolFamily.OPENAI_RESPONSES.value),
        ("noncredential_headers", (("content-type", "application/json"),)),
        ("credential_header_names", ("authorization",)),
    )


@dataclass(frozen=True)
class CountObservation:
    """Ephemeral raw observation carried by an external qualified host reader."""

    premise: CountPremise
    final_transport_sha256: str
    request_bytes: bytes
    request_sha256: str
    response_bytes: bytes
    response_sha256: str
    http_status: int
    counter_wire_target: tuple


class OpenAIResponsesCountSource:
    def __init__(self, authority_id: str, reader):
        self.authority_id = authority_id
        self._reader = reader

    def read(self, context: QualificationContext) -> CountPremise:
        expected_request = count_request_from_final(context)
        if self._reader is None or not callable(getattr(self._reader, "read", None)):
            reject()
        observed = self._reader.read(context)
        if type(observed) is not CountObservation:
            reject()
        premise = observed.premise
        if type(premise) is not CountPremise:
            reject()
        _current_header(premise.witness, context)
        witness_binding = _strict_object(premise.witness.raw_witness)
        native_generation_target = _native_wire_target(context, "responses")
        native_counter_target = _native_wire_target(context, "responses/input_tokens")
        if (
            premise.witness.authority_id != self.authority_id
            or context.wire_target != native_generation_target
            or context.wire_target_sha256
            != wire_target_digest(native_generation_target)
            or observed.counter_wire_target != native_counter_target
            or type(observed.http_status) is not int
            or observed.http_status != 200
            or observed.final_transport_sha256 != context.final_transport_sha256
            or observed.request_bytes != expected_request
            or observed.request_sha256 != digest(expected_request)
            or observed.response_sha256 != digest(observed.response_bytes)
            or witness_binding
            != {
                "generation_sha256": context.final_transport_sha256,
                "request_sha256": observed.request_sha256,
                "response_sha256": observed.response_sha256,
                "scope_fingerprint": context.scope_fingerprint,
                "counter_wire_target_sha256": wire_target_digest(native_counter_target),
                "http_status": 200,
            }
            or premise.count_kind != "EXACT"
            or premise.covers_all_billable_input is not True
        ):
            reject()
        count = parse_count_response(observed.response_bytes)
        if premise.input_token_bound != count:
            reject()
        return premise


class OpenAIResponsesCapSource:
    def __init__(self, authority_id: str, reader):
        self.authority_id = authority_id
        self._reader = reader

    def read(self, context: QualificationContext) -> CapPremise:
        count_request_from_final(context)
        premise = _read_premise(self._reader, context, CapPremise, self.authority_id)
        if (
            type(premise.output_token_limit) is not int
            or premise.output_token_limit != _CAP
            or premise.provider_parameter != "max_output_tokens"
            or premise.covers_all_billable_output is not True
            or premise.includes_reasoning is not True
        ):
            reject()
        return premise


@dataclass(frozen=True)
class TariffObservation:
    """Ephemeral tariff and independently witnessed effective processing facts."""

    premise: TariffPremise
    effective_conditions_witness: WitnessHeader
    effective_service_tier: str
    billing_region_class: str
    actual_processing_region: str
    processing_mode: str


class OpenAIResponsesTariffSource:
    def __init__(self, authority_id: str, reader):
        self.authority_id = authority_id
        self._reader = reader

    def read(self, context: QualificationContext) -> TariffPremise:
        count_request_from_final(context)
        if self._reader is None or not callable(getattr(self._reader, "read", None)):
            reject()
        observed = self._reader.read(context)
        if (
            type(observed) is not TariffObservation
            or type(observed.premise) is not TariffPremise
        ):
            reject()
        premise = observed.premise
        _current_header(premise.witness, context)
        if premise.witness.authority_id != self.authority_id:
            reject()
        settings = observed.effective_conditions_witness
        _current_header(settings, context)
        if (
            settings.authority_id == self.authority_id
            or type(settings.authority_id) is not str
            or not settings.authority_id.strip()
            or type(observed.actual_processing_region) is not str
            or not observed.actual_processing_region.strip()
            or observed.actual_processing_region
            != observed.actual_processing_region.strip()
            or any(char in observed.actual_processing_region for char in "\r\n")
            or observed.effective_service_tier != "DEFAULT"
            or observed.billing_region_class != "STANDARD"
            or observed.processing_mode != "STANDARD"
            or _strict_object(settings.raw_witness)
            != {
                "effective_service_tier": observed.effective_service_tier,
                "billing_region_class": observed.billing_region_class,
                "actual_processing_region": observed.actual_processing_region,
                "processing_mode": observed.processing_mode,
                "generation_sha256": context.final_transport_sha256,
                "scope_fingerprint": context.scope_fingerprint,
                "tariff_witness_fingerprint": premise.witness.witness_fingerprint,
            }
        ):
            reject()
        conditions = premise.pricing_conditions
        if (
            type(conditions) is not tuple
            or any(
                type(pair) is not tuple
                or len(pair) != 2
                or any(type(item) is not str for item in pair)
                for pair in conditions
            )
            or set(conditions) != _TARIFF_CONDITIONS
            or len(conditions) != len(_TARIFF_CONDITIONS)
            or premise.currency != "USD"
            or premise.unit not in _UNIT_TOKENS
        ):
            reject()
        try:
            _exact_rate(premise.input_price_decimal)
            _exact_rate(premise.output_price_decimal)
            _exact_rate(premise.additional_cost_bound_decimal)
            for value in (
                premise.cache_read_price_decimal,
                premise.cache_write_price_decimal,
            ):
                if value is not None:
                    _exact_rate(value)
        except (TypeError, ValueError):
            reject()
        cache_rates = (
            premise.cache_read_price_decimal,
            premise.cache_write_price_decimal,
        )
        if not (
            (premise.cache_semantics == "NONE" and cache_rates == (None, None))
            or (
                premise.cache_semantics == "EXCLUSIVE_REPLACEMENT"
                and all(value is not None for value in cache_rates)
            )
        ):
            reject()
        if not (
            premise.additional_fees_state == "KNOWN_NONE"
            and _exact_rate(premise.additional_cost_bound_decimal) == 0
            or premise.additional_fees_state == "QUALIFIED_BOUND"
        ):
            reject()
        return replace(
            premise,
            pricing_conditions=conditions
            + (
                ("effective_conditions_authority_id", settings.authority_id),
                ("effective_conditions_identity", settings.evidence_identity),
                ("effective_conditions_revision", settings.revision),
                ("effective_conditions_source_sha256", settings.raw_sha256),
                (
                    "effective_conditions_witness_fingerprint",
                    settings.witness_fingerprint,
                ),
                ("effective_conditions_not_after", settings.valid_until.isoformat()),
                ("actual_processing_region", observed.actual_processing_region),
                ("processing_mode", observed.processing_mode),
            ),
        )




_LAZY_EXPORTS = {"OpenAIResponsesTransportSource": "embedded_copilot.production_release.model_transport_qualification"}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    from importlib import import_module
    return getattr(import_module(target, __name__), name)

__all__ = ['count_request_from_final', 'parse_count_response', 'CountObservation', 'OpenAIResponsesCountSource', 'OpenAIResponsesCapSource', 'TariffObservation', 'OpenAIResponsesTariffSource', 'OpenAIResponsesTransportSource']  # noqa: RUF022 - preserve legacy export order.

if TYPE_CHECKING:
    from embedded_copilot.production_release.model_transport_qualification import (
        OpenAIResponsesTransportSource,
    )
