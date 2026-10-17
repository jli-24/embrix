"""Approved §14 bounded count-plus-generation contracts; no execution or qualification."""

from __future__ import annotations

import base64
import hashlib
from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from ..contracts import (
    ModelGatewayContract,
    canonical_fingerprint,
    fingerprint,
    identifier,
)
from ..governance import BoundModelIdentity, EngineeringStage
from ..profiles import ProtocolFamily


class _BundleContract(ModelGatewayContract):
    fingerprint: str = "sha256:" + "0" * 64

    @model_validator(mode="after")
    def _identity(self):
        for name in type(self).model_fields:
            value = getattr(self, name)
            if value is None or name == "fingerprint":
                continue
            if name.endswith(("_fingerprint", "_sha256")):
                fingerprint(value, field=name)
            if isinstance(value, datetime) and value.utcoffset() is None:
                raise ValueError("bundle evidence time must be timezone aware")
        expected = canonical_fingerprint(self, exclude={"fingerprint"})
        if self.fingerprint == "sha256:" + "0" * 64:
            object.__setattr__(self, "fingerprint", expected)
        elif self.fingerprint != expected:
            raise ValueError("bundle evidence fingerprint mismatch")
        return self


class SingleInvocationBudgetEnvelopeV2(_BundleContract):
    schema_version: int = Field(default=2, strict=True, ge=2, le=2)
    envelope_id: str
    tenant_id: str
    project_id: str
    conversation_id: str
    task_id: str
    task_authority_fingerprint: str
    engineering_stage: EngineeringStage
    purpose: Literal["REAL_STAGE_ACCEPTANCE_COUNTER_AND_GENERATION"] = (
        "REAL_STAGE_ACCEPTANCE_COUNTER_AND_GENERATION"
    )
    currency: Literal["USD"] = "USD"
    max_cost_decimal: Literal["0.10"] = "0.10"
    max_requests: int = Field(default=2, strict=True, ge=2, le=2)
    max_counter_requests: int = Field(default=1, strict=True, ge=1, le=1)
    max_generation_requests: int = Field(default=1, strict=True, ge=1, le=1)
    input_admission_limit: int = Field(default=8192, strict=True, ge=8192, le=8192)
    total_output_limit: int = Field(default=2048, strict=True, ge=2048, le=2048)
    not_after: datetime

    @model_validator(mode="after")
    def _scope(self):
        for name in (
            "envelope_id",
            "tenant_id",
            "project_id",
            "conversation_id",
            "task_id",
        ):
            identifier(getattr(self, name), field=name)
        if self.engineering_stage is not EngineeringStage.HARDWARE_SELECTION:
            raise ValueError(
                "counter bundle is limited to the hardware selection stage"
            )
        return self


class ConditionalGenerationCeilingV1(_BundleContract):
    """A pricing calculation conditional on later count admission; never a token proof."""

    schema_version: int = Field(default=1, strict=True, ge=1, le=1)
    currency: Literal["USD"] = "USD"
    unit: str
    input_admission_limit: int = Field(default=8192, strict=True, ge=8192, le=8192)
    output_token_limit: int = Field(default=2048, strict=True, ge=2048, le=2048)
    input_price_decimal: str
    output_price_decimal: str
    cache_read_price_decimal: str | None = None
    cache_write_price_decimal: str | None = None
    cache_semantics: str
    additional_fees_state: str
    additional_cost_bound_decimal: str
    pricing_identity: str
    pricing_revision: str
    pricing_source_sha256: str
    pricing_conditions: tuple[tuple[str, str], ...]
    pricing_not_after: datetime
    output_not_after: datetime
    transport_not_after: datetime
    cap_evidence_fingerprint: str
    transport_evidence_fingerprint: str
    grant_evidence_fingerprint: str
    covers_all_billable_input: bool
    covers_all_billable_output: bool
    includes_reasoning: bool


class CounterAdmissionV1(_BundleContract):
    schema_version: int = Field(default=1, strict=True, ge=1, le=1)
    envelope_fingerprint: str
    request_fingerprint: str
    execution_snapshot_fingerprint: str
    task_authority_fingerprint: str
    policy_fingerprint: str
    selected_model: BoundModelIdentity
    connection_id: str
    connection_revision: str
    credential_revision: str
    endpoint_revision: str
    protocol_family: ProtocolFamily
    final_generation_bytes_sha256: str
    count_request_base64: str
    count_request_sha256: str
    counter_wire_target_sha256: str
    generation_wire_target_sha256: str
    input_admission_limit: int = Field(default=8192, strict=True, ge=8192, le=8192)
    total_output_limit: int = Field(default=2048, strict=True, ge=2048, le=2048)
    counter_fee_identity: str
    counter_fee_revision: str
    counter_fee_source_sha256: str
    counter_fee_upper_decimal: str
    counter_fee_currency: Literal["USD"] = "USD"
    counter_fee_unit: Literal["ENTIRE_COUNT_REQUEST_BOUND"] = (
        "ENTIRE_COUNT_REQUEST_BOUND"
    )
    counter_fee_evidence_fingerprint: str
    counter_fee_not_after: datetime
    generation_ceiling: ConditionalGenerationCeilingV1
    generation_cost_upper_fraction: str
    combined_cost_upper_fraction: str
    not_after: datetime

    @model_validator(mode="after")
    def _profile(self):
        for name in (
            "connection_id",
            "credential_revision",
            "endpoint_revision",
            "counter_fee_identity",
            "counter_fee_revision",
        ):
            identifier(getattr(self, name), field=name)
        if (
            self.protocol_family is not ProtocolFamily.OPENAI_RESPONSES
            or self.selected_model.provider_id != "openai"
            or self.selected_model.model_id != "gpt-5.6-luna"
        ):
            raise ValueError("counter admission must bind native Luna Responses")
        try:
            raw = base64.b64decode(self.count_request_base64, validate=True)
        except (ValueError, TypeError) as error:
            raise ValueError("counter request original is invalid") from error
        if (
            not raw
            or "sha256:" + hashlib.sha256(raw).hexdigest() != self.count_request_sha256
        ):
            raise ValueError("counter request original hash mismatch")
        return self


class CounterObservationV1(_BundleContract):
    schema_version: int = Field(default=1, strict=True, ge=1, le=1)
    envelope_id: str
    project_id: str
    request_id: str
    counter_admission_fingerprint: str
    execution_snapshot_fingerprint: str
    task_authority_fingerprint: str
    selected_model: BoundModelIdentity
    connection_id: str
    connection_revision: str
    credential_revision: str
    endpoint_revision: str
    final_generation_bytes_sha256: str
    count_request_sha256: str
    counter_wire_target_sha256: str
    count_request_base64: str
    raw_response_base64: str
    response_sha256: str
    http_status: int = Field(strict=True, ge=100, le=599)
    observation_status: Literal["COUNTED", "HTTP_ERROR", "INVALID_RESPONSE"]
    input_tokens: int | None = Field(default=None, strict=True, ge=0)
    provider_request_id: str | None = None
    current_witness_fingerprints: tuple[str, ...]
    observed_at: datetime
    not_after: datetime

    @model_validator(mode="after")
    def _originals(self):
        for name in (
            "envelope_id",
            "project_id",
            "request_id",
            "connection_id",
            "credential_revision",
            "endpoint_revision",
        ):
            identifier(getattr(self, name), field=name)
        if self.provider_request_id is not None:
            identifier(self.provider_request_id, field="provider_request_id")
        for name, digest_name in (
            ("count_request_base64", "count_request_sha256"),
            ("raw_response_base64", "response_sha256"),
        ):
            try:
                raw = base64.b64decode(getattr(self, name), validate=True)
            except (ValueError, TypeError) as error:
                raise ValueError("counter original is invalid") from error
            if "sha256:" + hashlib.sha256(raw).hexdigest() != getattr(
                self, digest_name
            ):
                raise ValueError("counter original hash mismatch")
        for value in self.current_witness_fingerprints:
            fingerprint(value)
        if self.observation_status == "COUNTED":
            if self.http_status != 200 or self.input_tokens is None:
                raise ValueError(
                    "counted observation requires a successful integer result"
                )
        elif self.input_tokens is not None:
            raise ValueError("failed counter response cannot claim an input count")
        if self.observation_status == "HTTP_ERROR" and self.http_status == 200:
            raise ValueError("HTTP counter error has successful status")
        return self
