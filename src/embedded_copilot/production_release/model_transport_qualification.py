"""Host transport qualification adapter; preserves the exact native binding checks."""
from __future__ import annotations

from embedded_copilot.model_gateway.adapters.protocol_http import (
    ProtocolHTTPProviderAdapter,
)
from embedded_copilot.model_gateway.invocation.budget import reject, wire_target_digest
from embedded_copilot.model_gateway.invocation.openai_responses_qualification import (
    _CAP,
    _read_premise,
    count_request_from_final,
)
from embedded_copilot.model_gateway.invocation.qualified_budget_producer import (
    QualificationContext,
    TransportPremise,
)
from embedded_copilot.model_gateway.profiles import ProtocolFamily
from embedded_copilot.model_gateway.service import ModelGatewayService
from embedded_copilot.model_gateway.transport import (
    GatewayTransportBinding,
    ResolvedTransportEntry,
)

from .provider import ReloadableModelGateway


class OpenAIResponsesTransportSource:
    def __init__(self, authority_id: str, reader, gateway: ReloadableModelGateway):
        self.authority_id = authority_id
        self._reader = reader
        self._gateway = gateway

    def _native_binding(self, context: QualificationContext):
        if type(self._gateway) is not ReloadableModelGateway:
            reject()
        binding = self._gateway.transport_binding
        if type(binding) is not GatewayTransportBinding:
            reject()
        identity = context.selected_model
        entry = binding.resolve(
            (identity.provider_id, identity.endpoint_id, identity.credential_ref)
        )
        if (
            type(binding.gateway) is not ModelGatewayService
            or type(entry) is not ResolvedTransportEntry
            or type(entry.adapter) is not ProtocolHTTPProviderAdapter
            or entry.connection_id != context.connection_id
            or entry.endpoint_revision != context.endpoint_revision
            or entry.credential_revision != context.credential_revision
            or not any(
                current is entry for current in binding.gateway.transport_entries
            )
        ):
            reject()
        adapter = entry.adapter
        target = context.wire_target
        if type(target) is not tuple or len(target) != 7:
            reject()
        try:
            wire = dict(target)
        except (TypeError, ValueError):
            reject()
        if (
            len(wire) != 7
            or wire
            != {
                "provider_id": identity.provider_id,
                "endpoint_id": identity.endpoint_id,
                "base_url": "https://api.openai.com/v1/",
                "path": "responses",
                "protocol": ProtocolFamily.OPENAI_RESPONSES.value,
                "noncredential_headers": (("content-type", "application/json"),),
                "credential_header_names": ("authorization",),
            }
            or context.wire_target_sha256 != wire_target_digest(target)
            or adapter._provider != identity.provider_id
            or adapter._endpoint_id != identity.endpoint_id
            or adapter._base_url != "https://api.openai.com/v1/"
            or adapter._family is not ProtocolFamily.OPENAI_RESPONSES
            or adapter._supports_image_input is not False
            or adapter._sync_transport is not None
            or adapter._async_transport is not None
            or adapter._api_key is None
        ):
            reject()
        return binding, entry

    def read(self, context: QualificationContext) -> TransportPremise:
        count_request_from_final(context)
        binding, entry = self._native_binding(context)
        premise = _read_premise(
            self._reader, context, TransportPremise, self.authority_id
        )
        # A held binding is immutable, but its adapter and gateway can change
        # while the independent witness reader runs. Recheck their live facts.
        current_binding, current_entry = self._native_binding(context)
        if current_binding is not binding or current_entry is not entry:
            reject("BUDGET_BINDING_STALE")
        if (
            premise.final_transport_sha256 != context.final_transport_sha256
            or premise.wire_target_sha256 != context.wire_target_sha256
            or premise.protocol_family is not ProtocolFamily.OPENAI_RESPONSES
            or premise.provider_parameter != "max_output_tokens"
            or premise.output_token_limit != _CAP
            or premise.single_send_semantics is not True
        ):
            reject()
        return premise
