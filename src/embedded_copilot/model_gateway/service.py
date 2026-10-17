from __future__ import annotations

import asyncio
import copy
import threading
from collections.abc import Mapping, Sequence

from pydantic import ValidationError

from ._transport_metadata import ProviderExecutionResult
from .contracts import (
    ModelRequest,
    ModelResponse,
    ModelRoutingRecord,
    ProviderCapability,
    identifier,
    validate_model_request,
    validate_model_response,
)
from .exceptions import (
    ModelGatewayProviderFailure,
    ModelGatewayRejected,
    ModelGatewayUnavailable,
    ModelProviderAnswerEmpty,
    ModelProviderConfigurationError,
    ModelProviderError,
    ModelProviderHTTPError,
    ModelProviderModelUnavailable,
    ModelProviderParseError,
    ModelProviderRateLimited,
    ModelProviderUnauthorized,
)
from .governance import BoundModelIdentity
from .metrics import ModelReliability
from .ports import BoundModelGatewayPort, ModelGatewayPort, ModelProviderPort
from .profiles import ModelProfile, RoutingRequest
from .resilience import ModelErrorCode, RetryPolicy
from .router import ModelRouter
from .transport import ResolvedTransportEntry, TransportKey


class ModelGatewayService(ModelGatewayPort, BoundModelGatewayPort):
    """Deterministic policy-based gateway over injected provider adapters."""

    __slots__ = ("_providers", "_retry_policy", "_transport_entries", "_transport_lock")

    def __init__(
        self,
        *,
        providers: Mapping[str, ModelProviderPort],
        retry_policy: RetryPolicy | None = None,
        transport_entries: Mapping[TransportKey, ResolvedTransportEntry]
        | Sequence[ResolvedTransportEntry]
        | None = None,
    ) -> None:
        checked: dict[str, ModelProviderPort] = {}
        for name, provider in providers.items():
            provider_name = identifier(name, field="provider")
            if not isinstance(provider, ModelProviderPort):
                raise TypeError("model provider port is invalid")
            if ProviderCapability.TEXT not in provider.capabilities:
                raise TypeError("model provider does not support text")
            checked[provider_name] = provider
        self._providers = checked
        if retry_policy is not None and type(retry_policy) is not RetryPolicy:
            raise TypeError("retry policy is invalid")
        self._retry_policy = retry_policy or RetryPolicy(max_attempts=1)
        self._transport_lock = threading.RLock()
        self._transport_entries: dict[TransportKey, ResolvedTransportEntry] = {}
        if transport_entries is not None:
            self.install_transport_entries(transport_entries)

    def install_transport_entries(
        self,
        entries: Mapping[TransportKey, ResolvedTransportEntry]
        | Sequence[ResolvedTransportEntry],
    ) -> None:
        """Atomically replace the internal exact transport index.

        The index is derived from one ``GatewayTransportBinding`` snapshot by
        the composition root.  It is never consulted by generic policy routing.
        """

        if isinstance(entries, Mapping):
            resolved: list[ResolvedTransportEntry] = []
            for key, item in entries.items():
                if isinstance(item, ResolvedTransportEntry):
                    if key != item.key:
                        raise ValueError("transport entry key does not match metadata")
                    resolved.append(item)
                    continue
                if not isinstance(key, tuple) or len(key) != 3:
                    raise TypeError("transport entry key is invalid")
                resolved.append(
                    ResolvedTransportEntry(
                        provider_id=key[0],
                        endpoint_id=key[1],
                        credential_ref=key[2],
                        adapter=item,
                    )
                )
            values = tuple(resolved)
        else:
            values = tuple(entries)
        checked: dict[TransportKey, ResolvedTransportEntry] = {}
        for entry in values:
            if not isinstance(entry, ResolvedTransportEntry):
                raise TypeError("transport entry is invalid")
            if entry.key in checked:
                raise ValueError("transport entry key must be unique")
            checked[entry.key] = entry
        with self._transport_lock:
            self._transport_entries = checked

    @property
    def transport_entries(self) -> tuple[ResolvedTransportEntry, ...]:
        with self._transport_lock:
            return tuple(self._transport_entries.values())

    def _bound_provider(
        self, identity: BoundModelIdentity
    ) -> ModelProviderPort | None:
        with self._transport_lock:
            entries = self._transport_entries
            if entries:
                entry = entries.get(
                    (identity.provider_id, identity.endpoint_id, identity.credential_ref)
                )
                return entry.adapter if entry is not None else None
        # Compatibility for directly constructed gateways: endpoint aliases are
        # exact endpoint addresses; provider aliases are deliberately excluded.
        return self._providers.get(identity.endpoint_id)

    @staticmethod
    def _ensure_input_modalities_supported(
        request: ModelRequest, provider: ModelProviderPort
    ) -> None:
        if request.images and ProviderCapability.VISION not in provider.capabilities:
            raise ModelGatewayRejected(reason_code="VISION_INPUT_NOT_SUPPORTED")

    def _count_bound_with_metadata(self, entry, payload, *, dispatch_guard):
        from .adapters.openai_count_collector import execute_native_count

        def guard(raw, target):
            self._check_counter_entry(entry)
            dispatch_guard(raw, target)
            self._check_counter_entry(entry)

        self._check_counter_entry(entry)
        return execute_native_count(entry.adapter, payload, dispatch_guard=guard)

    async def _acount_bound_with_metadata(self, entry, payload, *, dispatch_guard):
        from .adapters.openai_count_collector import aexecute_native_count

        async def guard(raw, target):
            self._check_counter_entry(entry)
            await dispatch_guard(raw, target)
            self._check_counter_entry(entry)

        self._check_counter_entry(entry)
        return await aexecute_native_count(entry.adapter, payload, dispatch_guard=guard)

    def _check_counter_entry(self, entry):
        with self._transport_lock:
            current = self._transport_entries.get(entry.key)
        if current is not entry:
            raise ModelGatewayRejected(reason_code="BUDGET_BINDING_STALE")

    def generate_routed(
        self,
        request: ModelRequest,
        routing_request: RoutingRequest,
        models: Sequence[ModelProfile],
        *,
        reliability: Mapping[str, ModelReliability] | None = None,
    ) -> ModelResponse:
        """Route then execute through this same canonical gateway service."""

        selection = ModelRouter().select(
            routing_request, models, reliability=reliability
        )
        model = next(
            item
            for item in models
            if item.model_profile_id == selection.model_profile_id
        )
        provider_name = (
            model.provider_id
            if model.provider_id in self._providers
            else model.endpoint_id
        )
        provider = self._providers.get(provider_name)
        if provider is None:
            raise ModelGatewayUnavailable()
        self._ensure_input_modalities_supported(request, provider)
        policy = {
            **request.model_policy.model_dump(mode="python"),
            "primary_provider": provider_name,
            "fallback_providers": (),
            "model_name": model.remote_model_id,
            "fingerprint": "sha256:" + "0" * 64,
        }
        routed_policy = type(request.model_policy).model_validate(policy)
        payload = {
            **request.model_dump(mode="python"),
            "model_policy": routed_policy,
            "endpoint_id": model.endpoint_id,
            "fingerprint": "sha256:" + "0" * 64,
        }
        routed_request = ModelRequest.model_validate(payload)
        return self.generate_resilient(routed_request)

    async def agenerate_routed(
        self,
        request: ModelRequest,
        routing_request: RoutingRequest,
        models: Sequence[ModelProfile],
        *,
        reliability: Mapping[str, ModelReliability] | None = None,
    ) -> ModelResponse:
        selection = ModelRouter().select(
            routing_request, models, reliability=reliability
        )
        model = next(
            item
            for item in models
            if item.model_profile_id == selection.model_profile_id
        )
        provider_name = (
            model.provider_id
            if model.provider_id in self._providers
            else model.endpoint_id
        )
        if provider_name not in self._providers:
            raise ModelGatewayUnavailable()
        self._ensure_input_modalities_supported(request, self._providers[provider_name])
        policy = {
            **request.model_policy.model_dump(mode="python"),
            "primary_provider": provider_name,
            "fallback_providers": (),
            "model_name": model.remote_model_id,
            "fingerprint": "sha256:" + "0" * 64,
        }
        routed_policy = type(request.model_policy).model_validate(policy)
        payload = {
            **request.model_dump(mode="python"),
            "model_policy": routed_policy,
            "endpoint_id": model.endpoint_id,
            "fingerprint": "sha256:" + "0" * 64,
        }
        routed_request = ModelRequest.model_validate(payload)
        return await self.agenerate_resilient(routed_request)

    def generate_resilient(self, request: ModelRequest) -> ModelResponse:
        """Retry only sanitized transient gateway failures with a hard bound."""

        for attempt in range(self._retry_policy.max_attempts):
            try:
                return self.generate(request)
            except ModelGatewayProviderFailure as error:
                if (
                    error.reason_code != "PROVIDER_TRANSPORT_FAILED"
                    or not self._retry_policy.should_retry(
                        ModelErrorCode.PROVIDER_UNAVAILABLE, attempt
                    )
                ):
                    raise
            except ModelGatewayUnavailable:
                if not self._retry_policy.should_retry(
                    ModelErrorCode.PROVIDER_UNAVAILABLE, attempt
                ):
                    raise
        raise ModelGatewayUnavailable()

    def generate_bound(
        self, identity: BoundModelIdentity, request: ModelRequest
    ) -> ModelResponse:
        """Execute one governance-bound identity without routing or fallback."""

        return self._generate_bound_with_metadata(identity, request).response

    def generate_bound_from_transport(
        self,
        entry: ResolvedTransportEntry,
        identity: BoundModelIdentity,
        request: ModelRequest,
    ) -> ModelResponse:
        """Execute against a caller-held immutable entry snapshot."""

        if not isinstance(entry, ResolvedTransportEntry) or entry.key != (
            identity.provider_id,
            identity.endpoint_id,
            identity.credential_ref,
        ):
            raise ModelGatewayRejected(reason_code="MODEL_PROTOCOL_ADAPTER_NOT_FOUND")
        return self._generate_bound_with_metadata(
            identity, request, _transport_entry=entry
        ).response

    def generate_bound_with_transport_metadata(
        self,
        entry: ResolvedTransportEntry,
        identity: BoundModelIdentity,
        request: ModelRequest,
        *, _dispatch_guard=None,
    ) -> ProviderExecutionResult:
        if not isinstance(entry, ResolvedTransportEntry) or entry.key != (
            identity.provider_id,
            identity.endpoint_id,
            identity.credential_ref,
        ):
            raise ModelGatewayRejected(reason_code="MODEL_PROTOCOL_ADAPTER_NOT_FOUND")
        return self._generate_bound_with_metadata(
            identity, request, _transport_entry=entry, _dispatch_guard=_dispatch_guard
        )

    def _normalize_bound_request(self, identity: BoundModelIdentity, request: ModelRequest) -> ModelRequest:
        """Pure binding shared by preview and both execution paths."""
        if not isinstance(identity, BoundModelIdentity):
            raise ModelGatewayRejected(reason_code="MODEL_GATEWAY_EXECUTION_FAILED")
        try:
            checked = validate_model_request(request)
            with self._transport_lock:
                strict_transport_binding = bool(self._transport_entries)
            if (
                checked.model_policy.primary_provider != identity.provider_id
                or (
                    checked.preferred_provider is not None
                    and checked.preferred_provider != identity.provider_id
                )
                or (
                    strict_transport_binding
                    and checked.endpoint_id != identity.endpoint_id
                )
                or (
                    strict_transport_binding
                    and checked.credential_ref != identity.credential_ref
                )
            ):
                raise ValueError("bound transport identity mismatch")
            policy_material = {
                **checked.model_policy.model_dump(mode="python"),
                "primary_provider": identity.provider_id,
                "fallback_providers": (),
                "model_name": identity.model_id,
                "fingerprint": "sha256:" + "0" * 64,
            }
            material = {
                **checked.model_dump(mode="python"),
                "model_policy": policy_material,
                "endpoint_id": identity.endpoint_id,
                "credential_ref": identity.credential_ref,
                "preferred_model": identity.model_profile_id,
                "preferred_provider": identity.provider_id,
                "fingerprint": "sha256:" + "0" * 64,
            }
            if material["system_prompt"] == material["system_instruction"]:
                material["system_prompt"] = None
            bound = ModelRequest.model_validate(material)
        except (TypeError, ValueError, ValidationError) as error:
            raise ModelGatewayRejected(reason_code="MODEL_GATEWAY_EXECUTION_FAILED") from error
        return bound

    def prepare_bound_transport(self, identity: BoundModelIdentity, request: ModelRequest) -> tuple:
        """Prepare exact bytes and non-secret routing facts without dispatch."""
        bound = self._normalize_bound_request(identity, request)
        with self._transport_lock:
            entry = self._transport_entries.get((identity.provider_id, identity.endpoint_id, identity.credential_ref))
        if entry is None:
            raise ModelGatewayRejected(reason_code="BUDGET_EVIDENCE_UNAVAILABLE")
        provider = entry.adapter
        from .adapters.protocol_http import ProtocolHTTPProviderAdapter
        if type(provider) is not ProtocolHTTPProviderAdapter:
            raise ModelGatewayRejected(reason_code="BUDGET_EVIDENCE_UNAVAILABLE")
        self._ensure_input_modalities_supported(bound, provider)
        return bound, entry, *provider.prepare_transport(bound)

    def _generate_bound_with_metadata(
        self,
        identity: BoundModelIdentity,
        request: ModelRequest,
        *,
        _transport_entry: ResolvedTransportEntry | None = None,
        _dispatch_guard=None,
    ) -> ProviderExecutionResult:
        """Execute a bound provider and retain transport metadata internally."""

        bound = self._normalize_bound_request(identity, request)
        provider = (
            _transport_entry.adapter
            if _transport_entry is not None
            else self._bound_provider(identity)
        )
        if provider is None:
            raise ModelGatewayRejected(reason_code="MODEL_PROTOCOL_ADAPTER_NOT_FOUND")
        self._ensure_input_modalities_supported(bound, provider)
        guard = None
        if _dispatch_guard is not None:
            with self._transport_lock:
                held_entry = _transport_entry if _transport_entry is not None else self._transport_entries.get((identity.provider_id, identity.endpoint_id, identity.credential_ref))
            if held_entry is None or held_entry.adapter is not provider or not callable(getattr(provider, "_prepare_transport_bytes", None)):
                raise ModelGatewayRejected(reason_code="BUDGET_EVIDENCE_UNAVAILABLE")

            def check_entry():
                with self._transport_lock:
                    current = self._transport_entries.get(held_entry.key)
                if _transport_entry is None and (current is not held_entry or current.adapter is not provider):
                    raise ModelGatewayRejected(reason_code="BUDGET_BINDING_STALE")

            def guard(final_bound, protocol, payload, parameter, wire_target):
                check_entry()
                _dispatch_guard(final_bound, held_entry, protocol, payload, parameter, wire_target)
                check_entry()
        try:
            execute_with_metadata = getattr(provider, "_generate_with_metadata", None)
            if callable(execute_with_metadata):
                execution = execute_with_metadata(copy.deepcopy(bound), **({"_dispatch_guard": guard} if guard is not None else {}))
                if not isinstance(execution, ProviderExecutionResult):
                    raise TypeError("provider execution result is invalid")
                response = execution.response
                metadata = execution.metadata
            else:
                response = provider.generate(copy.deepcopy(bound))
                metadata = None
            return ProviderExecutionResult(
                response=self._response(bound, identity.provider_id, response),
                metadata=metadata,
            )
        except (ModelGatewayProviderFailure, ModelGatewayRejected):
            raise
        except ModelProviderUnauthorized as error:
            raise ModelGatewayProviderFailure(
                "PROVIDER_AUTH_FAILED",
                provider_response_present=error.safe_http_status is not None,
                validation_stage="PROVIDER_HTTP_STATUS" if error.safe_http_status is not None else "PROVIDER_TRANSPORT",
                safe_http_status=error.safe_http_status,
            ) from error
        except ModelProviderModelUnavailable as error:
            raise ModelGatewayProviderFailure(
                "PROVIDER_MODEL_UNAVAILABLE",
                provider_response_present=error.safe_http_status is not None,
                validation_stage="PROVIDER_HTTP_STATUS" if error.safe_http_status is not None else "PROVIDER_TRANSPORT",
                safe_http_status=error.safe_http_status,
            ) from error
        except ModelProviderRateLimited as error:
            raise ModelGatewayProviderFailure(
                "PROVIDER_RATE_LIMITED",
                provider_response_present=True,
                validation_stage="PROVIDER_HTTP_STATUS",
                safe_http_status=error.safe_http_status,
            ) from error
        except ModelProviderHTTPError as error:
            raise ModelGatewayProviderFailure(
                "PROVIDER_HTTP_ERROR",
                provider_response_present=True,
                validation_stage="PROVIDER_HTTP_STATUS",
                safe_http_status=error.safe_http_status,
            ) from error
        except ModelProviderParseError as error:
            raise ModelGatewayProviderFailure(
                "PROVIDER_PARSE_FAILED",
                provider_response_present=True,
                validation_stage="PROVIDER_PARSE",
            ) from error
        except ModelProviderError as error:
            raise ModelGatewayProviderFailure(
                "PROVIDER_TRANSPORT_FAILED",
                safe_transport_error_code=error.safe_transport_error_code,
                safe_transport_stage=error.safe_transport_stage,
            ) from error
        except (TypeError, ValueError, ValidationError) as error:
            raise ModelGatewayProviderFailure(
                "ANSWER_SCHEMA_INVALID",
                provider_response_present=True,
                validation_stage="MODEL_RESPONSE_NORMALIZATION",
            ) from error
        except Exception as error:
            raise ModelGatewayProviderFailure("PROVIDER_TRANSPORT_FAILED") from error

    async def agenerate_bound(
        self, identity: BoundModelIdentity, request: ModelRequest
    ) -> ModelResponse:
        return (await self._agenerate_bound_with_metadata(identity, request)).response

    async def agenerate_bound_from_transport(
        self,
        entry: ResolvedTransportEntry,
        identity: BoundModelIdentity,
        request: ModelRequest,
    ) -> ModelResponse:
        if not isinstance(entry, ResolvedTransportEntry) or entry.key != (
            identity.provider_id,
            identity.endpoint_id,
            identity.credential_ref,
        ):
            raise ModelGatewayRejected(reason_code="MODEL_PROTOCOL_ADAPTER_NOT_FOUND")
        return (
            await self._agenerate_bound_with_metadata(
                identity, request, _transport_entry=entry
            )
        ).response

    async def agenerate_bound_with_transport_metadata(
        self,
        entry: ResolvedTransportEntry,
        identity: BoundModelIdentity,
        request: ModelRequest,
        *, _dispatch_guard=None,
    ) -> ProviderExecutionResult:
        if not isinstance(entry, ResolvedTransportEntry) or entry.key != (
            identity.provider_id,
            identity.endpoint_id,
            identity.credential_ref,
        ):
            raise ModelGatewayRejected(reason_code="MODEL_PROTOCOL_ADAPTER_NOT_FOUND")
        return await self._agenerate_bound_with_metadata(
            identity, request, _transport_entry=entry, _dispatch_guard=_dispatch_guard
        )

    async def _agenerate_bound_with_metadata(
        self,
        identity: BoundModelIdentity,
        request: ModelRequest,
        *,
        _transport_entry: ResolvedTransportEntry | None = None,
        _dispatch_guard=None,
    ) -> ProviderExecutionResult:
        bound = self._normalize_bound_request(identity, request)
        provider = (
            _transport_entry.adapter
            if _transport_entry is not None
            else self._bound_provider(identity)
        )
        if provider is None:
            raise ModelGatewayRejected(reason_code="MODEL_PROTOCOL_ADAPTER_NOT_FOUND")
        self._ensure_input_modalities_supported(bound, provider)
        guard = None
        if _dispatch_guard is not None:
            with self._transport_lock:
                held_entry = _transport_entry if _transport_entry is not None else self._transport_entries.get((identity.provider_id, identity.endpoint_id, identity.credential_ref))
            if held_entry is None or held_entry.adapter is not provider or not callable(getattr(provider, "_prepare_transport_bytes", None)):
                raise ModelGatewayRejected(reason_code="BUDGET_EVIDENCE_UNAVAILABLE")

            def check_entry():
                with self._transport_lock:
                    current = self._transport_entries.get(held_entry.key)
                if _transport_entry is None and (current is not held_entry or current.adapter is not provider):
                    raise ModelGatewayRejected(reason_code="BUDGET_BINDING_STALE")

            async def guard(final_bound, protocol, payload, parameter, wire_target):
                check_entry()
                await _dispatch_guard(final_bound, held_entry, protocol, payload, parameter, wire_target)
                check_entry()
        try:
            execute_with_metadata = getattr(provider, "_agenerate_with_metadata", None)
            if callable(execute_with_metadata):
                execution = await execute_with_metadata(copy.deepcopy(bound), **({"_dispatch_guard": guard} if guard is not None else {}))
                if not isinstance(execution, ProviderExecutionResult):
                    raise TypeError("provider execution result is invalid")
                response = execution.response
                metadata = execution.metadata
            else:
                response = await provider.agenerate(copy.deepcopy(bound))
                metadata = None
            return ProviderExecutionResult(
                response=self._response(bound, identity.provider_id, response),
                metadata=metadata,
            )
        except asyncio.CancelledError:
            raise
        except (ModelGatewayProviderFailure, ModelGatewayRejected):
            raise
        except ModelProviderError as error:
            raise ModelGatewayProviderFailure("PROVIDER_REQUEST_FAILED", safe_http_status=error.safe_http_status, safe_transport_error_code=error.safe_transport_error_code, safe_transport_stage=error.safe_transport_stage) from error
        except (TypeError, ValueError, ValidationError) as error:
            raise ModelGatewayProviderFailure("ANSWER_SCHEMA_INVALID", provider_response_present=True, validation_stage="MODEL_RESPONSE_NORMALIZATION") from error
        except Exception as error:
            raise ModelGatewayProviderFailure("PROVIDER_TRANSPORT_FAILED") from error

    async def agenerate_resilient(self, request: ModelRequest) -> ModelResponse:
        for attempt in range(self._retry_policy.max_attempts):
            try:
                return await self.agenerate(request)
            except asyncio.CancelledError:
                raise
            except ModelGatewayProviderFailure as error:
                if (
                    error.reason_code != "PROVIDER_TRANSPORT_FAILED"
                    or not self._retry_policy.should_retry(
                        ModelErrorCode.PROVIDER_UNAVAILABLE, attempt
                    )
                ):
                    raise
            except ModelGatewayUnavailable:
                if not self._retry_policy.should_retry(
                    ModelErrorCode.PROVIDER_UNAVAILABLE, attempt
                ):
                    raise
        raise ModelGatewayUnavailable()

    @staticmethod
    def _response(
        request: ModelRequest, provider_name: str, value: object
    ) -> ModelResponse:
        try:
            response = validate_model_response(value)
        except (TypeError, ValueError, ValidationError) as error:
            raise ModelGatewayProviderFailure(
                "ANSWER_SCHEMA_INVALID",
                provider_response_present=True,
                validation_stage="MODEL_RESPONSE_NORMALIZATION",
            ) from error
        if (
            response.provider != provider_name
            or response.model_name != request.model_policy.model_name
            or response.provenance != request.engineering_context.provenance
        ):
            raise ModelGatewayProviderFailure(
                "ANSWER_CONTRACT_MISMATCH",
                provider_response_present=True,
                validation_stage="PROVENANCE_BINDING",
            )
        if response.provider_id is not None and response.provider_id != provider_name:
            raise ModelGatewayProviderFailure(
                "ANSWER_CONTRACT_MISMATCH",
                provider_response_present=True,
                validation_stage="PROVIDER_IDENTITY_BINDING",
            )
        if (
            request.endpoint_id is not None
            and response.endpoint_id is not None
            and response.endpoint_id != request.endpoint_id
        ):
            raise ModelGatewayProviderFailure(
                "ANSWER_CONTRACT_MISMATCH",
                provider_response_present=True,
                validation_stage="ENDPOINT_IDENTITY_BINDING",
            )
        if (
            request.credential_ref is not None
            and response.credential_ref is not None
            and response.credential_ref != request.credential_ref
        ):
            raise ModelGatewayProviderFailure(
                "ANSWER_CONTRACT_MISMATCH",
                provider_response_present=True,
                validation_stage="CREDENTIAL_IDENTITY_BINDING",
            )
        endpoint_id = response.endpoint_id or request.endpoint_id
        routing_record = response.routing_record
        if routing_record is None and endpoint_id is not None:
            routing_record = ModelRoutingRecord(
                requested_model=request.preferred_model
                or request.model_policy.model_name,
                selected_model=response.selected_model
                or request.model_policy.model_name,
                actual_model=response.actual_model or response.model_name,
                remote_model_id=response.remote_model_id or response.model_name,
                provider_id=provider_name,
                endpoint_id=endpoint_id,
            )
        material = response.model_dump(mode="python")
        for key, replacement in {
            "response_id": "",
            "fingerprint": "sha256:" + "0" * 64,
            "request_id": request.request_id,
            "provider_id": response.provider_id or provider_name,
            "endpoint_id": endpoint_id,
            "credential_ref": request.credential_ref or response.credential_ref,
            "requested_model": response.requested_model
            or request.model_policy.model_name,
            "actual_model": response.actual_model or response.model_name,
            "remote_model_id": response.remote_model_id or response.model_name,
            "routing_record": routing_record,
        }.items():
            material[key] = replacement
        return ModelResponse.model_validate(material)

    @staticmethod
    def _providers_for(request: ModelRequest) -> tuple[str, ...]:
        return (
            request.model_policy.primary_provider,
            *request.model_policy.fallback_providers,
        )

    def generate(self, request: ModelRequest) -> ModelResponse:
        try:
            checked = validate_model_request(request)
        except (TypeError, ValueError, ValidationError) as error:
            raise ModelGatewayRejected() from error
        provider_names = self._providers_for(checked)
        vision_rejected = False
        for index, provider_name in enumerate(provider_names):
            provider = (
                self._providers.get(checked.endpoint_id or "") if index == 0 else None
            ) or self._providers.get(provider_name)
            if provider is None:
                continue
            if checked.images and ProviderCapability.VISION not in provider.capabilities:
                vision_rejected = True
                continue
            try:
                value = provider.generate(copy.deepcopy(checked))
                return self._response(checked, provider_name, value)
            except ModelProviderUnauthorized as error:
                failure = ModelGatewayProviderFailure(
                    "PROVIDER_AUTH_FAILED",
                    provider_response_present=error.safe_http_status is not None,
                    validation_stage="PROVIDER_HTTP_STATUS"
                    if error.safe_http_status is not None
                    else "PROVIDER_TRANSPORT",
                    safe_http_status=error.safe_http_status,
                )
                if index + 1 < len(provider_names):
                    continue
                raise failure from error
            except ModelProviderModelUnavailable as error:
                failure = ModelGatewayProviderFailure(
                    "PROVIDER_MODEL_UNAVAILABLE",
                    provider_response_present=error.safe_http_status is not None,
                    validation_stage="PROVIDER_HTTP_STATUS"
                    if error.safe_http_status is not None
                    else "PROVIDER_TRANSPORT",
                    safe_http_status=error.safe_http_status,
                )
                if index + 1 < len(provider_names):
                    continue
                raise failure from error
            except ModelProviderRateLimited as error:
                failure = ModelGatewayProviderFailure(
                    "PROVIDER_RATE_LIMITED",
                    provider_response_present=True,
                    validation_stage="PROVIDER_HTTP_STATUS",
                    safe_http_status=error.safe_http_status,
                )
                if index + 1 < len(provider_names):
                    continue
                raise failure from error
            except ModelProviderConfigurationError as error:
                failure = ModelGatewayProviderFailure("PROVIDER_CONFIG_INVALID")
                if index + 1 < len(provider_names):
                    continue
                raise failure from error
            except ModelProviderAnswerEmpty:
                failure = ModelGatewayProviderFailure(
                    "ANSWER_EMPTY",
                    provider_response_present=True,
                    validation_stage="PROVIDER_PARSE",
                )
                if index + 1 < len(provider_names):
                    continue
                raise failure
            except ModelProviderParseError:
                failure = ModelGatewayProviderFailure(
                    "PROVIDER_PARSE_FAILED",
                    provider_response_present=True,
                    validation_stage="PROVIDER_PARSE",
                )
                if index + 1 < len(provider_names):
                    continue
                raise failure
            except ModelProviderHTTPError as error:
                failure = ModelGatewayProviderFailure(
                    "PROVIDER_HTTP_ERROR",
                    provider_response_present=True,
                    validation_stage="PROVIDER_HTTP_STATUS",
                    safe_http_status=error.safe_http_status,
                )
                if index + 1 < len(provider_names):
                    continue
                raise failure from error
            except ModelProviderError as error:
                failure = ModelGatewayProviderFailure(
                    "PROVIDER_TRANSPORT_FAILED",
                    safe_transport_error_code=error.safe_transport_error_code,
                    safe_transport_stage=error.safe_transport_stage,
                )
                if index + 1 < len(provider_names):
                    continue
                raise failure from error
            except ModelGatewayProviderFailure:
                if index + 1 < len(provider_names):
                    continue
                raise
            except ModelGatewayUnavailable:
                continue
            except (TypeError, ValueError, ValidationError, TimeoutError):
                continue
            except Exception:  # noqa: BLE001, S112 - provider diagnostics are deliberately redacted
                continue
        if vision_rejected:
            raise ModelGatewayRejected(reason_code="VISION_INPUT_NOT_SUPPORTED")
        raise ModelGatewayUnavailable()

    async def agenerate(self, request: ModelRequest) -> ModelResponse:
        try:
            checked = validate_model_request(request)
        except (TypeError, ValueError, ValidationError) as error:
            raise ModelGatewayRejected() from error
        provider_names = self._providers_for(checked)
        vision_rejected = False
        for index, provider_name in enumerate(provider_names):
            provider = (
                self._providers.get(checked.endpoint_id or "") if index == 0 else None
            ) or self._providers.get(provider_name)
            if provider is None:
                continue
            if checked.images and ProviderCapability.VISION not in provider.capabilities:
                vision_rejected = True
                continue
            try:
                value = await provider.agenerate(copy.deepcopy(checked))
                return self._response(checked, provider_name, value)
            except asyncio.CancelledError:
                raise
            except ModelProviderUnauthorized as error:
                failure = ModelGatewayProviderFailure(
                    "PROVIDER_AUTH_FAILED",
                    provider_response_present=error.safe_http_status is not None,
                    validation_stage="PROVIDER_HTTP_STATUS"
                    if error.safe_http_status is not None
                    else "PROVIDER_TRANSPORT",
                    safe_http_status=error.safe_http_status,
                )
                if index + 1 < len(provider_names):
                    continue
                raise failure from error
            except ModelProviderModelUnavailable as error:
                failure = ModelGatewayProviderFailure(
                    "PROVIDER_MODEL_UNAVAILABLE",
                    provider_response_present=error.safe_http_status is not None,
                    validation_stage="PROVIDER_HTTP_STATUS"
                    if error.safe_http_status is not None
                    else "PROVIDER_TRANSPORT",
                    safe_http_status=error.safe_http_status,
                )
                if index + 1 < len(provider_names):
                    continue
                raise failure from error
            except ModelProviderRateLimited as error:
                failure = ModelGatewayProviderFailure(
                    "PROVIDER_RATE_LIMITED",
                    provider_response_present=True,
                    validation_stage="PROVIDER_HTTP_STATUS",
                    safe_http_status=error.safe_http_status,
                )
                if index + 1 < len(provider_names):
                    continue
                raise failure from error
            except ModelProviderConfigurationError as error:
                failure = ModelGatewayProviderFailure("PROVIDER_CONFIG_INVALID")
                if index + 1 < len(provider_names):
                    continue
                raise failure from error
            except ModelProviderAnswerEmpty:
                failure = ModelGatewayProviderFailure(
                    "ANSWER_EMPTY",
                    provider_response_present=True,
                    validation_stage="PROVIDER_PARSE",
                )
                if index + 1 < len(provider_names):
                    continue
                raise failure
            except ModelProviderParseError:
                failure = ModelGatewayProviderFailure(
                    "PROVIDER_PARSE_FAILED",
                    provider_response_present=True,
                    validation_stage="PROVIDER_PARSE",
                )
                if index + 1 < len(provider_names):
                    continue
                raise failure
            except ModelProviderHTTPError as error:
                failure = ModelGatewayProviderFailure(
                    "PROVIDER_HTTP_ERROR",
                    provider_response_present=True,
                    validation_stage="PROVIDER_HTTP_STATUS",
                    safe_http_status=error.safe_http_status,
                )
                if index + 1 < len(provider_names):
                    continue
                raise failure from error
            except ModelProviderError as error:
                failure = ModelGatewayProviderFailure(
                    "PROVIDER_TRANSPORT_FAILED",
                    safe_transport_error_code=error.safe_transport_error_code,
                    safe_transport_stage=error.safe_transport_stage,
                )
                if index + 1 < len(provider_names):
                    continue
                raise failure from error
            except ModelGatewayProviderFailure:
                if index + 1 < len(provider_names):
                    continue
                raise
            except ModelGatewayUnavailable:
                continue
            except (TypeError, ValueError, ValidationError, TimeoutError):
                continue
            except Exception:  # noqa: BLE001, S112 - provider diagnostics are deliberately redacted
                continue
        if vision_rejected:
            raise ModelGatewayRejected(reason_code="VISION_INPUT_NOT_SUPPORTED")
        raise ModelGatewayUnavailable()


__all__ = ("ModelGatewayService",)
