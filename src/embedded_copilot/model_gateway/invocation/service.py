"""Governed invocation orchestration over the canonical model gateway."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, cast

from pydantic import ValidationError

from .._transport_metadata import ProviderExecutionMetadata, ProviderExecutionResult
from ..contracts import (
    ModelPolicy,
    ModelRequest,
    ModelResponse,
    canonical_fingerprint,
    identifier,
)
from ..exceptions import (
    ModelGatewayError,
    ModelGatewayProviderFailure,
    ModelGatewayRejected,
    ModelGatewayUnavailable,
)
from ..governance import (
    BoundModelIdentity,
    EngineeringStage,
    ModelSelectionResult,
    ModelSelectionTaskProfile,
    SelectionMode,
)
from ..ports import BoundModelGatewayPort
from ..profiles import EndpointProfile, ModelCapability
from ..selection import ModelSelectionError
from ..transport import ResolvedTransportEntry
from .bundle_runtime import BundleInvocationMixin
from .contracts import (
    GateStatus,
    InvocationMode,
    InvocationStatus,
    ModelExecutionSnapshot,
    ModelInvocationAttempt,
    ModelInvocationContext,
    ModelInvocationGate,
    ModelInvocationReceipt,
    ModelInvocationRequest,
    ModelInvocationResult,
    TaskAuthorityFailure,
)
from .ports import (
    ModelInvocationReceiptPort,
    ModelInvocationSelectionPort,
    TaskAuthorityPort,
    TrustedCounterBudgetPort,
    TrustedInvocationBudgetPort,
)


class _PreparedSelectionPort(Protocol):
    def select_governed_model(self, task_profile: ModelSelectionTaskProfile, *, request_override=None) -> ModelSelectionResult: ...
    def execution_snapshot(self, task_profile: ModelSelectionTaskProfile, selection: ModelSelectionResult) -> ModelExecutionSnapshot: ...


class _PreparedGatewayPort(Protocol):
    def prepare_bound_transport(self, identity: BoundModelIdentity, request: ModelRequest) -> tuple: ...


class ManagedModelSelectionResolver:
    """Adapt the management policy source without exposing provider state."""

    __slots__ = ("_available_credentials", "_credential_revision", "_endpoint_revision", "_management")

    def __init__(
        self,
        management,
        available_credentials: Callable[[], tuple[str, ...]] | None = None,
        credential_revision_for_connection: Callable[[str], str | None] | None = None,
        endpoint_revision: Callable[[EndpointProfile], str | None] | None = None,
    ) -> None:
        if not hasattr(management, "select_governed_model"):
            raise TypeError("model governance source is invalid")
        self._management = management
        self._available_credentials = available_credentials or (lambda: ())
        self._credential_revision = credential_revision_for_connection or (lambda _connection_id: None)
        self._endpoint_revision = endpoint_revision or (lambda _endpoint: None)

    def select_governed_model(self, task_profile, *, request_override=None):
        stage_policy = self._management.stage_model_policy(task_profile.project_id, task_profile.stage)
        project_policy = self._management.project_default_model_policy(task_profile.project_id)
        effective_policy = stage_policy or project_policy
        auto_mode = request_override is None and getattr(effective_policy, "selection_mode", None) is SelectionMode.AUTO
        auto_policy_revision = canonical_fingerprint(effective_policy, exclude=set()) if auto_mode else None

        def runtime_eligibility(identity: BoundModelIdentity, *, policy_revision: str | None = None) -> None:
            validator = getattr(self._management, "_validate_runtime_eligibility", None)
            if not callable(validator):
                raise ModelSelectionError("MODEL_CREDENTIAL_GATE_FAILED")
            auto_kwargs = (
                {"auto_task_profile": task_profile, "auto_policy_revision": policy_revision or auto_policy_revision}
                if auto_mode else {}
            )
            validator(
                identity,
                credential_revision_for_connection=self._credential_revision,
                endpoint_revision=self._endpoint_revision,
                **auto_kwargs,
            )

        selection_kwargs = {
            "request_override": request_override,
            "available_credential_refs": tuple(self._available_credentials()),
            "strict_gates": True,
        }
        if getattr(effective_policy, "selection_mode", None) in {SelectionMode.CANDIDATE_GROUP, SelectionMode.AUTO}:
            selection_kwargs["candidate_runtime_eligibility"] = runtime_eligibility
        selection = self._management.select_governed_model(task_profile, **selection_kwargs)
        if selection.selected_model is not None:
            try:
                runtime_eligibility(
                    selection.selected_model,
                    policy_revision=(
                        selection.auto_selection_evidence.auto_policy_revision
                        if auto_mode and selection.auto_selection_evidence is not None else None
                    ),
                )
            except ModelSelectionError as error:
                raise ModelSelectionError(error.code, insufficient=error.insufficient, selection=selection) from error
        return selection

    def _for_preparation(
        self, entries: tuple[ResolvedTransportEntry, ...]
    ) -> ManagedModelSelectionResolver:
        """Use held transport metadata without observing credential values."""
        from .._endpoint_revision import endpoint_revision as _stable_endpoint_revision

        if not entries or any(
            not isinstance(entry, ResolvedTransportEntry)
            or entry.connection_id is None
            or entry.credential_revision is None
            or entry.endpoint_revision is None
            for entry in entries
        ):
            raise ModelGatewayRejected(reason_code="BUDGET_BINDING_STALE")
        by_connection = {entry.connection_id: entry for entry in entries}
        by_key = {entry.key: entry for entry in entries}
        if len(by_connection) != len(entries) or len(by_key) != len(entries):
            raise ModelGatewayRejected(reason_code="BUDGET_BINDING_STALE")

        def credential_revision(connection_id: str) -> str | None:
            entry = by_connection.get(connection_id)
            connection = self._management.connection(connection_id)
            if entry is None or connection is None or (
                connection.provider_id, connection.endpoint_id, connection.credential_ref
            ) != entry.key:
                return None
            return entry.credential_revision

        def endpoint_revision(endpoint: EndpointProfile) -> str | None:
            entry = by_key.get(cast(tuple[str, str, str], (endpoint.provider_id, endpoint.endpoint_id, endpoint.credential_ref)))
            if entry is None:
                return None
            revision = _stable_endpoint_revision(endpoint)
            return revision if revision == entry.endpoint_revision else None

        return ManagedModelSelectionResolver(
            self._management,
            available_credentials=lambda: tuple(sorted({entry.credential_ref for entry in entries})),
            credential_revision_for_connection=credential_revision,
            endpoint_revision=endpoint_revision,
        )

    def credential_revision(self, identity: BoundModelIdentity) -> str | None:
        profile = self._management.model_profile(identity.model_profile_id)
        return None if profile is None else profile.credential_revision

    def policy_source(self, task_profile: ModelSelectionTaskProfile) -> str:
        if self._management.stage_model_policy(task_profile.project_id, task_profile.stage) is not None:
            return "STAGE_POLICY"
        if self._management.project_default_model_policy(task_profile.project_id) is not None:
            return "PROJECT_DEFAULT"
        return "AUTO_POOL"

    def execution_snapshot(
        self, task_profile: ModelSelectionTaskProfile, selection: ModelSelectionResult
    ) -> ModelExecutionSnapshot:
        current = self.select_governed_model(task_profile)
        if current != selection or selection.selected_model is None:
            raise ModelSelectionError("MODEL_SELECTION_FAILED")
        policy = self._management.stage_model_policy(task_profile.project_id, task_profile.stage)
        source = "STAGE_POLICY"
        if policy is None:
            policy = self._management.project_default_model_policy(task_profile.project_id)
            source = "PROJECT_DEFAULT"
        if policy is None:
            raise ModelSelectionError("MODEL_SELECTION_FAILED")
        policy_fingerprint = canonical_fingerprint(policy, exclude=set())
        connection_id, credential_revision, endpoint_revision = self._management._validate_runtime_eligibility(
            selection.selected_model,
            credential_revision_for_connection=self._credential_revision,
            endpoint_revision=self._endpoint_revision,
            **({"auto_task_profile": task_profile, "auto_policy_revision": policy_fingerprint}
               if policy.selection_mode is SelectionMode.AUTO else {}),
        )
        if credential_revision is None:
            raise ModelSelectionError("MODEL_CREDENTIAL_GATE_FAILED")
        return ModelExecutionSnapshot(
            schema_version=2,
            connection_revision=canonical_fingerprint(self._management.connection(connection_id), exclude=set()),
            project_id=task_profile.project_id,
            stage_id=task_profile.stage,
            selected_model=selection.selected_model,
            connection_id=connection_id,
            credential_revision=credential_revision,
            endpoint_revision=endpoint_revision,
            policy_source=source,
            policy_fingerprint=policy_fingerprint,
            selection_fingerprint=canonical_fingerprint(selection, exclude=set()),
        )



_PENDING_REASONS = frozenset(
    {
        "AUTO",
        "AUTO_ROUTER_PENDING",
        "ALLOWED_POOL_ROUTER_INPUT",
        "CANDIDATE_GROUP",
        "CANDIDATE_GROUP_PENDING",
    }
)
_GATE_BY_ERROR = {
    "MODEL_AVAILABILITY_GATE_FAILED": frozenset({"availability", "endpoint_health"}),
    "MODEL_CREDENTIAL_GATE_FAILED": frozenset({"credential"}),
    "MODEL_CREDENTIAL_MISMATCH": frozenset({"credential"}),
    "MODEL_QUOTA_GATE_FAILED": frozenset({"quota"}),
    "MODEL_CAPABILITY_MISMATCH": frozenset({"capability"}),
}
_GOVERNANCE_REJECTION_CODES = frozenset(
    {
        "BUDGET_EVIDENCE_UNAVAILABLE",
        "BUDGET_LIMIT_EXCEEDED",
        "BUDGET_ENVELOPE_CLAIMED",
        "BUDGET_BINDING_STALE",
        "TASK_AUTHORITY_STALE",
        "MODEL_NOT_FOUND",
        "MODEL_PROVIDER_MISMATCH",
        "MODEL_ENDPOINT_MISMATCH",
        "MODEL_MODEL_ID_MISMATCH",
        "MODEL_CREDENTIAL_MISMATCH",
        "MODEL_CREDENTIAL_GATE_FAILED",
        "MODEL_CAPABILITY_MISMATCH",
        "MODEL_NOT_ALLOWED",
        "MODEL_AVAILABILITY_GATE_FAILED",
        "MODEL_QUOTA_GATE_FAILED",
        "VISION_INPUT_NOT_SUPPORTED",
        "MODEL_SELECTION_PENDING_ROUTER",
    }
)
_TASK_OPERATION_STAGES = {
    "HARDWARE_SELECTION_PROPOSAL": EngineeringStage.HARDWARE_SELECTION,
    "SCHEMATIC_DESIGN_PROPOSAL": EngineeringStage.SCHEMATIC_DESIGN,
}


class ModelInvocationService(BundleInvocationMixin):
    """Resolve governance and execute exactly one already-bound model."""

    __slots__ = ("_clock", "_gateway", "_receipt_store", "_selection", "_budget_source", "_budget_bundle_source")

    def __init__(
        self,
        *,
        selection_resolver: ModelInvocationSelectionPort,
        gateway: BoundModelGatewayPort,
        receipt_store: ModelInvocationReceiptPort | None = None,
        clock: Callable[[], datetime] | None = None,
        trusted_budget_source: TrustedInvocationBudgetPort | None = None,
        trusted_budget_bundle_source: TrustedCounterBudgetPort | None = None,
    ) -> None:
        if not isinstance(selection_resolver, ModelInvocationSelectionPort):
            raise TypeError("model invocation selection port is invalid")
        if not isinstance(gateway, BoundModelGatewayPort):
            raise TypeError("bound model gateway port is invalid")
        if receipt_store is not None and not (
            hasattr(receipt_store, "append") or hasattr(receipt_store, "append_receipt")
        ):
            raise TypeError("model invocation receipt port is invalid")
        if trusted_budget_source is not None:
            if not isinstance(trusted_budget_source, TrustedInvocationBudgetPort):
                raise TypeError("trusted budget port is invalid")
            identifier(trusted_budget_source.trusted_host_identity, field="trusted_host_identity")
        if trusted_budget_bundle_source is not None:
            if not isinstance(trusted_budget_bundle_source, TrustedCounterBudgetPort):
                raise TypeError("trusted counter budget port is invalid")
            identifier(trusted_budget_bundle_source.trusted_host_identity, field="trusted_host_identity")
        if callable(getattr(trusted_budget_bundle_source, "phase_authorization", None)) and getattr(trusted_budget_bundle_source, "consumption_store", None) is not receipt_store:
            raise ValueError("formal consumption source must share the invocation receipt store")
        self._budget_bundle_source = trusted_budget_bundle_source
        self._budget_source = trusted_budget_source
        self._selection = selection_resolver
        self._gateway = gateway
        self._receipt_store = receipt_store
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def counter_bundle_enabled(self):
        return self._budget_bundle_source is not None

    @staticmethod
    def _build_request(request: ModelInvocationRequest, identity: BoundModelIdentity) -> ModelRequest:
        options = request.generation_options
        policy = ModelPolicy(
            primary_provider=identity.provider_id, model_name=identity.model_id,
            fallback_providers=(), temperature=options.temperature,
            max_output_tokens=options.max_tokens,
            service_tier=options.service_tier,
        )
        content = request.payload.model_dump(mode="python", exclude={"fingerprint"})
        return ModelRequest(
            **content, request_id=request.request_id, project_id=request.project_id,
            task_type=request.task_type, model_policy=policy,
            endpoint_id=identity.endpoint_id, credential_ref=identity.credential_ref,
            preferred_model=identity.model_profile_id, preferred_provider=identity.provider_id,
            required_capabilities=tuple(item.value for item in request.required_capabilities),
            **options.model_dump(mode="python", exclude={"service_tier"}),
        )

    def prepare_consumption_context(self, request: ModelInvocationRequest, *, authority_source: TaskAuthorityPort):
        """Resolve and prepare only; never create attempts, claims or clients."""
        from ..service import ModelGatewayService
        from ..transport import GatewayTransportBinding
        from .qualified_budget_producer import QualifiedInvocationBudgetProducer

        checked = ModelInvocationRequest.model_validate(request.model_dump(mode="python"))
        if checked.invocation_mode is not InvocationMode.TASK or checked.request_override is not None or checked.task_type != "HARDWARE_SELECTION_PROPOSAL":
            raise ModelGatewayRejected(reason_code="BUDGET_EVIDENCE_UNAVAILABLE")
        authority = authority_source.revalidate(checked.authority_reference)
        if authority != checked.authority_reference:
            raise TaskAuthorityFailure()
        resolver = cast(_PreparedSelectionPort, self._selection)
        binding = None
        entries: tuple[ResolvedTransportEntry, ...] = ()
        if isinstance(resolver, ManagedModelSelectionResolver):
            binding = getattr(self._gateway, "transport_binding", None)
            if isinstance(binding, GatewayTransportBinding):
                entries = binding.entries
            elif isinstance(self._gateway, ModelGatewayService):
                entries = self._gateway.transport_entries
            else:
                raise ModelGatewayRejected(reason_code="BUDGET_BINDING_STALE")
            resolver = resolver._for_preparation(entries)
        profile = self._task_profile(checked)
        selection = resolver.select_governed_model(profile, request_override=None)
        if selection.selected_model is None:
            raise ModelGatewayRejected(reason_code="BUDGET_EVIDENCE_UNAVAILABLE")
        snapshot = resolver.execution_snapshot(profile, selection)
        raw = self._build_request(checked, selection.selected_model)
        bound, entry, protocol, payload, parameter, target = cast(_PreparedGatewayPort, self._gateway).prepare_bound_transport(selection.selected_model, raw)
        if (entry.connection_id, entry.credential_revision, entry.endpoint_revision) != (snapshot.connection_id, snapshot.credential_revision, snapshot.endpoint_revision):
            raise ModelGatewayRejected(reason_code="BUDGET_BINDING_STALE")
        if authority_source.revalidate(checked.authority_reference) != checked.authority_reference:
            raise TaskAuthorityFailure()
        if resolver.execution_snapshot(profile, selection) != snapshot:
            raise ModelGatewayRejected(reason_code="BUDGET_BINDING_STALE")
        if entries:
            if binding is not None:
                if getattr(self._gateway, "transport_binding", None) is not binding:
                    raise ModelGatewayRejected(reason_code="BUDGET_BINDING_STALE")
                current_entries = (
                    cast(ModelGatewayService, cast(GatewayTransportBinding, binding).gateway).transport_entries
                    if isinstance(cast(GatewayTransportBinding, binding).gateway, ModelGatewayService) else cast(GatewayTransportBinding, binding).entries
                )
            else:
                current_entries = cast(ModelGatewayService, self._gateway).transport_entries
            if (
                len(current_entries) != len(entries)
                or any(current is not held for current, held in zip(current_entries, entries, strict=True))
                or not any(entry is held for held in entries)
            ):
                raise ModelGatewayRejected(reason_code="BUDGET_BINDING_STALE")
        return QualifiedInvocationBudgetProducer._context(request=checked, snapshot=snapshot, bound=bound,
            protocol=protocol, payload=payload, parameter=parameter, wire_target=target, now=self._clock())


    def _task_profile(self, request: ModelInvocationRequest) -> ModelSelectionTaskProfile:
        capabilities: tuple[ModelCapability, ...]
        values = request.required_capabilities
        try:
            capabilities = tuple(
                item if isinstance(item, ModelCapability) else ModelCapability(str(item).upper())
                for item in values
            )
        except ValueError as error:
            raise ModelSelectionError("MODEL_CAPABILITY_MISMATCH") from error
        return ModelSelectionTaskProfile(
            request_id=request.request_id,
            project_id=request.project_id,
            stage=request.stage_id,
            task_type=request.task_type,
            required_capabilities=capabilities,
        )

    @staticmethod
    def _fallback_profile(request: ModelInvocationRequest) -> ModelSelectionTaskProfile:
        """Build a safe context when capability projection itself is rejected."""

        return ModelSelectionTaskProfile(
            request_id=request.request_id,
            project_id=request.project_id,
            stage=request.stage_id,
            task_type=request.task_type,
            required_capabilities=(),
        )

    @staticmethod
    def _mode(reason: str, *, override: bool) -> SelectionMode:
        if override:
            return SelectionMode.FIXED_MODEL
        if reason.startswith("CANDIDATE_GROUP"):
            return SelectionMode.CANDIDATE_GROUP
        if reason.startswith("AUTO_"):
            return SelectionMode.AUTO
        if reason in _PENDING_REASONS:
            return SelectionMode.AUTO
        return SelectionMode.FIXED_MODEL

    @staticmethod
    def _source(reason: str, *, override: bool) -> str:
        if override:
            return "REQUEST_OVERRIDE"
        if reason.startswith(("STAGE_", "CANDIDATE_GROUP")):
            return "STAGE_POLICY"
        if reason.startswith("PROJECT_"):
            return "PROJECT_DEFAULT"
        return "AUTO_POOL"

    @staticmethod
    def _gates(*, failed_code: str | None = None, pending: bool = False) -> tuple[ModelInvocationGate, ...]:
        names = ("capability", "availability", "credential", "endpoint_health", "quota")
        failed_names = _GATE_BY_ERROR.get(failed_code or "", frozenset())
        return tuple(
            ModelInvocationGate(
                name=name,
                status=(
                    GateStatus.FAIL
                    if name in failed_names
                    else GateStatus.UNKNOWN
                    if pending
                    else GateStatus.PASS
                ),
                detail=failed_code if name in failed_names else None,
            )
            for name in names
        )

    def _policy_source(
        self,
        request: ModelInvocationRequest,
        profile: ModelSelectionTaskProfile,
        reason: str,
    ) -> str:
        if request.request_override is not None:
            return "REQUEST_OVERRIDE"
        resolver = getattr(self._selection, "policy_source", None)
        if callable(resolver):
            try:
                value = resolver(profile)
                if value in {"STAGE_POLICY", "PROJECT_DEFAULT", "AUTO_POOL"}:
                    return value
            except (AttributeError, TypeError, ValueError, RuntimeError):
                pass
        return self._source(reason, override=False)

    def _context(
        self,
        request: ModelInvocationRequest,
        profile: ModelSelectionTaskProfile,
        *,
        selection: ModelSelectionResult | None,
        reason: str,
        error_code: str | None = None,
        selected: BoundModelIdentity | None = None,
        credential_revision: str | None = None,
        revalidation_failure: bool = False,
        saved_policy_source: str | None = None,
    ) -> ModelInvocationContext:
        actual_selected = selected if selected is not None else (None if selection is None else selection.selected_model)
        pending = actual_selected is None and (
            error_code is None or error_code in {"MODEL_SELECTION_PENDING_ROUTER", "MODEL_SELECTION_FAILED", "INSUFFICIENT_EVIDENCE"}
        )
        pending = pending or (revalidation_failure and selection is not None and selection.auto_selection_evidence is not None)
        return ModelInvocationContext(
            task_profile=profile,
            policy_source=saved_policy_source if saved_policy_source is not None else self._policy_source(request, profile, reason),
            selection_mode=self._mode(reason, override=request.request_override is not None),
            selection_reason=reason,
            selected_model=actual_selected,
            credential_revision=credential_revision,
            gates=self._gates(failed_code=error_code, pending=pending),
            trace_context=request.trace_context,
        )

    def _receipt(
        self,
        request: ModelInvocationRequest,
        context: ModelInvocationContext,
        *,
        started: datetime,
        completed: datetime,
        status: InvocationStatus,
        error_code: str | None,
        selection: ModelSelectionResult | None,
        response: ModelResponse | None = None,
        http_status: int | None = None,
        execution_metadata: ProviderExecutionMetadata | None = None,
        connection_id: str | None = None,
        gateway_called: bool = False,
        attempt: ModelInvocationAttempt | None = None,
        send_count_override: int | None = None,
    ) -> ModelInvocationReceipt:
        latency = max((completed - started).total_seconds() * 1000.0, 0.0)
        if (attempt is None or attempt.attempt_schema_version != 4) and execution_metadata is not None and execution_metadata.latency_ms is not None:
            latency = execution_metadata.latency_ms
        provider_request_id = (
            execution_metadata.provider_request_id
            if execution_metadata is not None
            else None if response is None else response.provider_response_id
        )
        if execution_metadata is not None:
            http_status = execution_metadata.http_status
            connection_id = execution_metadata.connection_id
        if attempt is not None and attempt.attempt_schema_version == 4 and connection_id is None:
            connection_id = attempt.snapshot.connection_id
        budget_binding = {}
        projected_usage = None if response is None else response.usage
        if request.invocation_mode is InvocationMode.TASK:
            from .budget import reconcile_budget, reconciled_usage
            state = "UNKNOWN"
            if attempt is not None and attempt.budget_evidence_fingerprint is not None:
                envelope, evidence = self._receipt_store.budget_binding_for_attempt(attempt)
                state = reconcile_budget(evidence, None if response is None else response.usage, envelope)
                projected_usage = reconciled_usage(evidence, projected_usage, state)
            budget_binding = {
                "budget_envelope_id": None if attempt is None else attempt.budget_envelope_id,
                "budget_evidence_fingerprint": None if attempt is None else attempt.budget_evidence_fingerprint,
                "budget_reservation_id": None if attempt is None else attempt.budget_reservation_id,
                "budget_reconciliation_state": state,
            }
        roles = self._bundle_role_projection(attempt, response, http_status, send_count_override) if attempt is not None and attempt.attempt_schema_version == 4 else {}
        # A generation estimate never establishes the complete two-role bill.
        if roles and budget_binding["budget_reconciliation_state"] != "BOUND_VIOLATION":
            budget_binding["budget_reconciliation_state"] = "UNKNOWN"
        return ModelInvocationReceipt(
            **budget_binding,
            request_id=request.request_id,
            trace_context=context.trace_context,
            project_id=request.project_id,
            stage_id=request.stage_id,
            invocation_mode=request.invocation_mode,
            authority_reference=request.authority_reference,
            task_type=request.task_type,
            policy_source=context.policy_source,
            selection_mode=context.selection_mode,
            selection_reason=context.selection_reason,
            selected_model=context.selected_model,
            connection_id=connection_id,
            candidate_selection_evidence=None if selection is None else selection.candidate_selection_evidence,
            auto_selection_evidence=None if selection is None else selection.auto_selection_evidence,
            **roles,
            schema_version=7 if attempt is not None and attempt.attempt_schema_version == 4 else 6 if request.invocation_mode is InvocationMode.TASK else 5,
            usage=projected_usage,
            credential_revision=context.credential_revision,
            required_capabilities=context.task_profile.required_capabilities,
            input_modalities=("text", "image") if request.payload.images else ("text",),
            image_count=len(request.payload.images),
            gates=context.gates,
            started_at=started,
            completed_at=completed,
            latency_ms=latency,
            provider_request_id=provider_request_id,
            http_status=http_status,
            result=status,
            error_code=error_code,
            provider_request_count=0,
            gateway_call_count=1 if gateway_called else 0,
            gateway_response_observed=response is not None,
            transport_send_count=roles["generation_transport_send_count"] if roles else send_count_override if send_count_override is not None else (0 if not gateway_called or (attempt is not None and attempt.status.value == "PREPARED") else (1 if response is not None else None)) if request.invocation_mode is InvocationMode.TASK else None,
        )

    @staticmethod
    def _normalize_response(value: object) -> ModelResponse:
        material = (
            value.model_dump(mode="python")
            if isinstance(value, ModelResponse)
            else value
        )
        return ModelResponse.model_validate(copy.deepcopy(material))

    def _execute_bound(
        self, identity: BoundModelIdentity, request: ModelRequest, *, dispatch_guard=None
    ) -> tuple[ModelResponse, ProviderExecutionMetadata | None]:
        execute_with_metadata = getattr(self._gateway, "_generate_bound_with_metadata", None)
        if callable(execute_with_metadata):
            execution = execute_with_metadata(identity, request, **({"_dispatch_guard": dispatch_guard} if dispatch_guard is not None else {}))
            if not isinstance(execution, ProviderExecutionResult):
                raise TypeError("provider execution result is invalid")
            return self._normalize_response(execution.response), execution.metadata
        if dispatch_guard is not None:
            raise ModelGatewayRejected(reason_code="BUDGET_EVIDENCE_UNAVAILABLE")
        return self._normalize_response(self._gateway.generate_bound(identity, request)), None

    async def _aexecute_bound(
        self, identity: BoundModelIdentity, request: ModelRequest, *, dispatch_guard=None
    ) -> tuple[ModelResponse, ProviderExecutionMetadata | None]:
        execute_with_metadata = getattr(self._gateway, "_agenerate_bound_with_metadata", None)
        if callable(execute_with_metadata):
            execution = await execute_with_metadata(identity, request, **({"_dispatch_guard": dispatch_guard} if dispatch_guard is not None else {}))
            if not isinstance(execution, ProviderExecutionResult):
                raise TypeError("provider execution result is invalid")
            return self._normalize_response(execution.response), execution.metadata
        if dispatch_guard is not None:
            raise ModelGatewayRejected(reason_code="BUDGET_EVIDENCE_UNAVAILABLE")
        return self._normalize_response(await self._gateway.agenerate_bound(identity, request)), None

    def _persist(self, receipt: ModelInvocationReceipt) -> ModelInvocationReceipt | None:
        if self._receipt_store is None:
            return None
        append = getattr(self._receipt_store, "append", None) or self._receipt_store.append_receipt
        stored = append(receipt)
        if stored is None:
            return receipt
        if type(stored) is not ModelInvocationReceipt:
            raise TypeError("model invocation receipt is invalid")
        return ModelInvocationReceipt.model_validate(copy.deepcopy(stored.model_dump(mode="python")))

    def _prepare_attempt(
        self, request: ModelInvocationRequest, profile: ModelSelectionTaskProfile,
        selection: ModelSelectionResult,
        authority_source: TaskAuthorityPort | None,
    ) -> ModelInvocationAttempt | None:
        store = self._receipt_store
        if store is None or not hasattr(store, "begin_attempt"):
            if request.invocation_mode is InvocationMode.TASK:
                raise ModelSelectionError("TASK_AUTHORITY_STALE")
            return None
        request_fingerprint = canonical_fingerprint(request, exclude=set())
        existing = store.get_attempt_for_project(request.project_id, request.request_id)
        if existing is not None:
            raise ModelSelectionError(
                "MODEL_INVOCATION_ALREADY_ATTEMPTED"
                if existing.request_fingerprint == request_fingerprint
                else "MODEL_INVOCATION_REQUEST_CONFLICT"
            )
        snapshot_source = getattr(self._selection, "execution_snapshot", None)
        if not callable(snapshot_source):
            raise ModelSelectionError("MODEL_EXECUTION_SNAPSHOT_UNAVAILABLE")
        snapshot = snapshot_source(profile, selection)
        if type(snapshot) is not ModelExecutionSnapshot:
            raise ModelSelectionError("MODEL_EXECUTION_SNAPSHOT_UNAVAILABLE")
        bundle = (self.counter_bundle_enabled and request.invocation_mode is InvocationMode.TASK
                  and request.stage_id is EngineeringStage.HARDWARE_SELECTION)
        attempt = ModelInvocationAttempt(
            **({"budget_phase": "PREPARED", "counter_status": "NOT_ATTEMPTED",
                "counter_transport_send_count": 0, "generation_transport_send_count": 0,
                "total_external_request_count": 0} if bundle else {}),
            attempt_schema_version=4 if bundle else 3 if request.invocation_mode is InvocationMode.TASK and snapshot.schema_version == 2 else 2,
            project_id=request.project_id,
            request_id=request.request_id,
            request_fingerprint=request_fingerprint,
            snapshot=snapshot,
            invocation_mode=request.invocation_mode,
            authority_reference=request.authority_reference,
        )
        try:
            store.begin_attempt(attempt)
        except ValueError as error:
            raise ModelSelectionError("MODEL_INVOCATION_ALREADY_ATTEMPTED") from error
        if snapshot_source(profile, selection) != snapshot:
            raise ModelSelectionError("MODEL_EXECUTION_SNAPSHOT_STALE")
        if request.invocation_mode is InvocationMode.TASK:
            if authority_source is None or request.authority_reference is None:
                raise ModelSelectionError("TASK_AUTHORITY_STALE")
            try:
                current = authority_source.revalidate(request.authority_reference)
            except TaskAuthorityFailure as error:
                raise ModelSelectionError(error.code) from error
            except Exception as error:  # caller-bound authority fails closed
                raise ModelSelectionError("TASK_AUTHORITY_STALE") from error
            if current != request.authority_reference or current != attempt.authority_reference:
                raise ModelSelectionError("TASK_AUTHORITY_STALE")
        if request.invocation_mode is InvocationMode.TASK:
            if (self._budget_bundle_source if bundle else self._budget_source) is None or snapshot.schema_version != 2 or not hasattr(store, "claim_counter_intent" if bundle else "claim_budget_dispatch_intent"):
                raise ModelSelectionError("BUDGET_EVIDENCE_UNAVAILABLE")
            return attempt
        return store.mark_dispatch_intent(attempt)

    def _budget_guard(self, request, profile, selection, authority_source, attempt_state, *, asynchronous=False):
        if request.invocation_mode is not InvocationMode.TASK:
            return None
        if attempt_state[0] is not None and attempt_state[0].attempt_schema_version == 4:
            return self._bundle_guard(request, profile, selection, authority_source, attempt_state, asynchronous=asynchronous)
        from .budget import reject, verify_budget

        def check_current(entry, envelope, evidence, bound, protocol, payload, parameter, wire_target):
            attempt = attempt_state[0]
            if entry.connection_id != attempt.snapshot.connection_id or entry.credential_revision != attempt.snapshot.credential_revision or entry.endpoint_revision != attempt.snapshot.endpoint_revision:
                reject("BUDGET_BINDING_STALE")
            try:
                if self._selection.execution_snapshot(profile, selection) != attempt.snapshot:
                    reject("BUDGET_BINDING_STALE")
                current = authority_source.revalidate(request.authority_reference)
                if current != request.authority_reference:
                    reject("TASK_AUTHORITY_STALE")
                qualification = self._budget_source.qualify(envelope=envelope, evidence=evidence, request=request, snapshot=attempt.snapshot, bound=bound, protocol=protocol, payload=payload, parameter=parameter, wire_target=wire_target, now=self._clock())
                return verify_budget(request=request, bound=bound, snapshot=attempt.snapshot, envelope=envelope, evidence=evidence, protocol=protocol, payload=payload, parameter=parameter, wire_target=wire_target, now=self._clock(), qualification=qualification)
            except ModelGatewayRejected:
                raise
            except TaskAuthorityFailure as error:
                reject(error.code)
            except Exception:
                reject("BUDGET_BINDING_STALE")

        def commit(entry, envelope, evidence, bound, protocol, payload, parameter, wire_target):
            envelope, evidence = check_current(entry, envelope, evidence, bound, protocol, payload, parameter, wire_target)
            try:
                attempt_state[0] = self._receipt_store.claim_budget_dispatch_intent(attempt_state[0], envelope, evidence)
            except ValueError as error:
                reject("BUDGET_ENVELOPE_CLAIMED" if str(error) == "BUDGET_ENVELOPE_CLAIMED" else "BUDGET_EVIDENCE_UNAVAILABLE")
            except Exception:
                reject()
            # Claim is permanent, including a failure at this final checkpoint.
            check_current(entry, envelope, evidence, bound, protocol, payload, parameter, wire_target)

        def prepare_kwargs(bound, protocol, payload, parameter, wire_target):
            return dict(request=request, snapshot=attempt_state[0].snapshot, bound=bound, protocol=protocol, payload=payload, parameter=parameter, wire_target=wire_target, now=self._clock())

        def guard(bound, entry, protocol, payload, parameter, wire_target):
            try:
                envelope, evidence = self._budget_source.prepare(**prepare_kwargs(bound, protocol, payload, parameter, wire_target))
            except ModelGatewayRejected:
                raise
            except Exception:
                reject()
            commit(entry, envelope, evidence, bound, protocol, payload, parameter, wire_target)

        async def aguard(bound, entry, protocol, payload, parameter, wire_target):
            try:
                envelope, evidence = await self._budget_source.aprepare(**prepare_kwargs(bound, protocol, payload, parameter, wire_target))
            except ModelGatewayRejected:
                raise
            except asyncio.CancelledError:
                raise
            except Exception:
                reject()
            commit(entry, envelope, evidence, bound, protocol, payload, parameter, wire_target)

        return aguard if asynchronous else guard

    def _result(
        self,
        request: ModelInvocationRequest,
        profile: ModelSelectionTaskProfile,
        *,
        selection: ModelSelectionResult | None,
        reason: str,
        status: InvocationStatus,
        error_code: str | None,
        detail: str | None,
        count: int,
        started: datetime,
        response: ModelResponse | None = None,
        selected: BoundModelIdentity | None = None,
        http_status: int | None = None,
        execution_metadata: ProviderExecutionMetadata | None = None,
        connection_id: str | None = None,
        revalidation_failure: bool = False,
        attempt: ModelInvocationAttempt | None = None,
        gateway_called: bool = False,
        send_count_override: int | None = None,
        generation_http_failure=None,
    ) -> ModelInvocationResult:
        if attempt is not None and attempt.attempt_schema_version in {3, 4} and response is not None:
            from .budget import reconcile_budget
            envelope, evidence = self._receipt_store.budget_binding_for_attempt(attempt)
            if reconcile_budget(evidence, response.usage, envelope) == "BOUND_VIOLATION":
                status = InvocationStatus.FAILED
                error_code = "BUDGET_BOUND_VIOLATION"
                detail = "PROVIDER_BUDGET_BOUND_VIOLATION"
        bound_identity = selected if selected is not None else (None if selection is None else selection.selected_model)
        historical = attempt is not None and attempt.attempt_schema_version == 4 and detail == "MODEL_INVOCATION_HISTORY_ONLY"
        credential_revision = attempt.snapshot.credential_revision if historical else None
        if bound_identity is not None and not historical:
            resolver = getattr(self._selection, "credential_revision", None)
            if callable(resolver):
                try:
                    credential_revision = resolver(bound_identity)
                except (AttributeError, TypeError, ValueError, RuntimeError):
                    credential_revision = None
        context = self._context(
            request,
            profile,
            selection=selection,
            reason=reason,
            error_code=error_code,
            selected=selected,
            credential_revision=credential_revision,
            revalidation_failure=revalidation_failure,
            saved_policy_source=attempt.snapshot.policy_source if historical else None,
        )
        if attempt is not None and attempt.attempt_schema_version == 4 and detail == "MODEL_INVOCATION_HISTORY_ONLY":
            context = context.model_copy(update={"policy_source": attempt.snapshot.policy_source,
                "credential_revision": attempt.snapshot.credential_revision,
                "selected_model": attempt.snapshot.selected_model})
        completed = self._clock()
        receipt = self._receipt(
            request,
            context,
            started=started,
            completed=completed,
            status=status,
            error_code=error_code,
            selection=selection,
            response=response,
            http_status=http_status,
            execution_metadata=execution_metadata,
            connection_id=connection_id,
            gateway_called=gateway_called,
            attempt=attempt,
            send_count_override=send_count_override,
        )
        try:
            if attempt is not None and attempt.attempt_schema_version == 4:
                stored = self._receipt_store.complete_bundle_attempt(attempt, receipt, response=response if receipt.budget_reconciliation_state == "BOUND_VIOLATION" else None, http_failure=generation_http_failure)
            elif receipt.budget_reconciliation_state == "BOUND_VIOLATION":
                stored = self._receipt_store.complete_budget_violation(attempt, receipt, response)
            elif attempt is not None and status is InvocationStatus.SUCCESS:
                stored = self._receipt_store.complete_attempt(attempt, receipt)
            else:
                if attempt is not None and attempt.status.value == "DISPATCH_INTENT":
                    (self._receipt_store.mark_bundle_unknown if attempt.attempt_schema_version == 4 else self._receipt_store.mark_attempt_unknown)(attempt)
                stored = self._persist(receipt)
        except Exception:  # noqa: BLE001 - persistence is a fail-closed boundary
            if attempt is not None:
                try:
                    (self._receipt_store.mark_bundle_unknown if attempt.attempt_schema_version == 4 else self._receipt_store.mark_attempt_unknown)(attempt)
                except Exception:  # noqa: BLE001, S110 - DISPATCH_INTENT remains durable
                    pass
            return ModelInvocationResult(
                status=InvocationStatus.FAILED,
                context=context,
                error_code="MODEL_GATEWAY_EXECUTION_FAILED",
                detail="RECEIPT_PERSISTENCE_FAILED",
                provider_request_count=0,
            )
        return ModelInvocationResult(
            status=status,
            response=response,
            context=context,
            receipt=stored,
            error_code=error_code,
            detail=detail,
            provider_request_count=0,
        )

    def _prior_bundle_result(self, request, profile):
        # The formal Catalog entry authorizes the requester before calling this
        # internal service. A historical attempt never re-enters model selection.
        store = self._receipt_store
        if store is None or not hasattr(store, "get_attempt_for_project"):
            return None
        attempt = store.get_attempt_for_project(request.project_id, request.request_id)
        if attempt is None or attempt.attempt_schema_version != 4:
            return None
        if attempt.request_fingerprint != canonical_fingerprint(request, exclude=set()):
            context = self._context(request, profile, selection=None,
                reason="MODEL_INVOCATION_HISTORY_ONLY", error_code="MODEL_INVOCATION_REQUEST_CONFLICT", saved_policy_source=attempt.snapshot.policy_source)
            return ModelInvocationResult(status=InvocationStatus.REJECTED, context=context,
                error_code="MODEL_INVOCATION_REQUEST_CONFLICT", detail="MODEL_INVOCATION_HISTORY_ONLY", provider_request_count=0)
        return self._existing_budget_result(request, profile, None, "MODEL_INVOCATION_HISTORY_ONLY")

    def _existing_budget_result(self, request, profile, selection, reason):
        store = self._receipt_store
        if store is None or not hasattr(store, "get_attempt_for_project"):
            return None
        attempt = store.get_attempt_for_project(request.project_id, request.request_id)
        if attempt is None or attempt.attempt_schema_version not in {3, 4}:
            return None
        if attempt.request_fingerprint != canonical_fingerprint(request, exclude=set()):
            return None
        receipts = store.list_receipts_for_project(request.project_id, request_id=request.request_id, limit=1)
        if not receipts:
            if attempt.attempt_schema_version != 4:
                return None
            # A prior process may have stopped at any role. Recover evidence only;
            # no continuation, count, generation, or envelope release is permitted.
            return self._result(
                request, profile, selection=selection, reason=reason,
                status=InvocationStatus.FAILED, error_code="BUDGET_EVIDENCE_UNAVAILABLE" if attempt.budget_phase == "PREPARED" else "MODEL_INVOCATION_OUTCOME_UNKNOWN",
                detail="MODEL_INVOCATION_HISTORY_ONLY", count=0, started=self._clock(),
                selected=attempt.snapshot.selected_model, attempt=attempt,
                gateway_called=attempt.budget_phase != "PREPARED",
            )
        receipt = receipts[0]
        return ModelInvocationResult(
            status=receipt.result, receipt=receipt,
            context=self._context(request, profile, selection=None,
                reason=receipt.selection_reason, selected=receipt.selected_model,
                error_code=receipt.error_code, credential_revision=receipt.credential_revision, saved_policy_source=receipt.policy_source).model_copy(update={"policy_source": receipt.policy_source, "selection_mode": receipt.selection_mode}),
            error_code=receipt.error_code, detail="MODEL_INVOCATION_HISTORY_ONLY",
            provider_request_count=0,
        )

    def invoke(self, request: ModelInvocationRequest, *, authority_source: TaskAuthorityPort | None = None) -> ModelInvocationResult:
        if type(request) is not ModelInvocationRequest:
            raise TypeError("model invocation request is invalid")
        checked = ModelInvocationRequest.model_validate(copy.deepcopy(request.model_dump(mode="python")))
        started = self._clock()
        profile = self._fallback_profile(checked)
        try:
            profile = self._task_profile(checked)
            if checked.invocation_mode is InvocationMode.TASK:
                expected_stage = _TASK_OPERATION_STAGES.get(checked.authority_reference.operation_id)
                if expected_stage is None:
                    raise ModelSelectionError("TASK_MODEL_OPERATION_UNAVAILABLE")
                if checked.stage_id is not expected_stage:
                    raise ModelSelectionError("TASK_STAGE_BINDING_MISMATCH")
                if authority_source is None:
                    raise ModelSelectionError("TASK_AUTHORITY_STALE")
            if checked.request_override is not None:
                raise ModelSelectionError("REQUEST_OVERRIDE_UNAUTHORIZED")
            if checked.invocation_mode is InvocationMode.TASK:
                historical = self._prior_bundle_result(checked, profile)
                if historical is not None:
                    return historical
            selection = self._selection.select_governed_model(
                profile, request_override=checked.request_override
            )
            selection = ModelSelectionResult.model_validate(copy.deepcopy(selection.model_dump(mode="python")))
        except ModelSelectionError as error:
            return self._result(
                checked,
                profile,
                selection=error.selection,
                revalidation_failure=error.selection is not None,
                reason="GOVERNANCE_REJECTED" if error.selection is None else error.selection.selection_reason,
                status=InvocationStatus.REJECTED,
                error_code=error.code,
                detail=error.code,
                count=0,
                started=started,
            )
        except (TypeError, ValueError, ValidationError):
            return self._result(
                checked,
                profile,
                selection=None,
                reason="GOVERNANCE_REJECTED",
                status=InvocationStatus.REJECTED,
                error_code="MODEL_GATEWAY_EXECUTION_FAILED",
                detail="GOVERNANCE_RESULT_INVALID",
                count=0,
                started=started,
            )
        except Exception:  # noqa: BLE001 - governance boundary is fail-closed
            return self._result(
                checked,
                profile,
                selection=None,
                reason="GOVERNANCE_FAILURE",
                status=InvocationStatus.FAILED,
                error_code="MODEL_GATEWAY_EXECUTION_FAILED",
                detail="GOVERNANCE_FAILURE",
                count=0,
                started=started,
            )
        reason = selection.selection_reason
        if reason in _PENDING_REASONS:
            return self._result(
                checked,
                profile,
                selection=selection,
                reason=reason,
                status=InvocationStatus.REJECTED,
                error_code="MODEL_SELECTION_PENDING_ROUTER",
                detail=(
                    "CANDIDATE_GROUP_PENDING"
                    if reason.startswith("CANDIDATE_GROUP")
                    else "AUTO_ROUTER_PENDING"
                ),
                count=0,
                started=started,
            )
        if selection.selected_model is None:
            if selection.auto_selection_evidence is not None:
                code = "INSUFFICIENT_EVIDENCE" if reason == "AUTO_INSUFFICIENT_EVIDENCE" else "MODEL_SELECTION_FAILED"
                return self._result(checked, profile, selection=selection, reason=reason, status=InvocationStatus.REJECTED, error_code=code, detail=code, count=0, started=started)
            if selection.candidate_selection_evidence is not None:
                return self._result(checked, profile, selection=selection, reason=reason, status=InvocationStatus.REJECTED, error_code="MODEL_SELECTION_FAILED", detail="MODEL_SELECTION_FAILED", count=0, started=started)
            detail = "CANDIDATE_GROUP_PENDING" if reason.startswith("CANDIDATE_GROUP") else "AUTO_ROUTER_PENDING"
            return self._result(
                checked,
                profile,
                selection=selection,
                reason=reason,
                status=InvocationStatus.REJECTED,
                error_code="MODEL_SELECTION_PENDING_ROUTER",
                detail=detail,
                count=0,
                started=started,
            )
        identity = selection.selected_model
        try:
            bound_request = self._build_request(checked, identity)
            attempt = self._prepare_attempt(checked, profile, selection, authority_source)
        except ModelSelectionError as error:
            if error.code in {"MODEL_INVOCATION_ALREADY_ATTEMPTED", "MODEL_INVOCATION_REQUEST_CONFLICT"}:
                if error.code == "MODEL_INVOCATION_ALREADY_ATTEMPTED":
                    restored = self._existing_budget_result(checked, profile, selection, reason)
                    if restored is not None:
                        return restored
                return ModelInvocationResult(
                    status=InvocationStatus.REJECTED,
                    context=self._context(checked, profile, selection=selection, reason=reason, error_code=error.code),
                    error_code=error.code, detail=error.code, provider_request_count=0,
                )
            return self._result(checked, profile, selection=selection, reason=reason,
                                status=InvocationStatus.REJECTED, error_code=error.code,
                                detail=error.code, count=0, started=started)
        except Exception:  # noqa: BLE001 - pre-dispatch persistence must fail closed
            return self._result(checked, profile, selection=selection, reason=reason,
                                status=InvocationStatus.FAILED, error_code="MODEL_GATEWAY_EXECUTION_FAILED",
                                detail="ATTEMPT_PERSISTENCE_FAILED", count=0, started=started)
        attempt_state = [attempt]
        try:
            response, execution_metadata = self._execute_bound(
                identity,
                bound_request,
                dispatch_guard=self._budget_guard(checked, profile, selection, authority_source, attempt_state),
            )
        except ModelGatewayRejected as error:
            gateway_code = getattr(error, "reason_code", "MODEL_GATEWAY_EXECUTION_FAILED")
            gateway_status = (
                InvocationStatus.REJECTED
                if gateway_code in _GOVERNANCE_REJECTION_CODES
                else InvocationStatus.FAILED
            )
            return self._result(
                checked,
                profile,
                selection=selection,
                reason=reason,
                status=gateway_status,
                error_code=gateway_code,
                detail="GATEWAY_REJECTED" if gateway_status is InvocationStatus.REJECTED else "GATEWAY_FAILURE",
                count=0,
                started=started,
                selected=identity,
                attempt=attempt_state[0], gateway_called=True, send_count_override=0,
            )
        except ModelGatewayProviderFailure as error:
            from .bundle_http import observed_http_failure
            http_failure = observed_http_failure(error) if attempt_state[0] is not None and attempt_state[0].attempt_schema_version == 4 else None
            response_error = error.reason_code in {"ANSWER_SCHEMA_INVALID", "PROVIDER_PARSE_FAILED", "ANSWER_EMPTY", "ANSWER_CONTRACT_MISMATCH"}
            return self._result(
                checked,
                profile,
                selection=selection,
                reason=reason,
                status=InvocationStatus.FAILED,
                error_code="MODEL_PROVIDER_RESPONSE_INVALID" if response_error else "MODEL_PROVIDER_REQUEST_FAILED",
                detail="PROVIDER_RESPONSE_INVALID" if response_error else "PROVIDER_REQUEST_FAILED",
                count=1,
                started=started,
                selected=identity,
                http_status=http_failure.http_status if http_failure is not None else getattr(error, "safe_http_status", None),
                connection_id=getattr(error, "connection_id", None),
                generation_http_failure=http_failure,
                attempt=attempt_state[0], gateway_called=True,
            )
        except ModelGatewayUnavailable:
            return self._result(
                checked,
                profile,
                selection=selection,
                reason=reason,
                status=InvocationStatus.FAILED,
                error_code="MODEL_GATEWAY_EXECUTION_FAILED",
                detail="GATEWAY_UNAVAILABLE",
                count=0,
                started=started,
                selected=identity,
                attempt=attempt_state[0], gateway_called=True,
            )
        except (TypeError, ValueError, ValidationError):
            return self._result(
                checked,
                profile,
                selection=selection,
                reason=reason,
                status=InvocationStatus.FAILED,
                error_code="MODEL_PROVIDER_RESPONSE_INVALID",
                detail="PROVIDER_RESPONSE_INVALID",
                count=1,
                started=started,
                selected=identity,
                attempt=attempt_state[0], gateway_called=True,
            )
        except ModelGatewayError:
            return self._result(
                checked,
                profile,
                selection=selection,
                reason=reason,
                status=InvocationStatus.FAILED,
                error_code="MODEL_GATEWAY_EXECUTION_FAILED",
                detail="GATEWAY_FAILURE",
                count=1,
                started=started,
                selected=identity,
                attempt=attempt_state[0], gateway_called=True,
            )
        except Exception:  # noqa: BLE001 - provider boundary is redacted and fail-closed
            return self._result(
                checked,
                profile,
                selection=selection,
                reason=reason,
                status=InvocationStatus.FAILED,
                error_code="MODEL_GATEWAY_EXECUTION_FAILED",
                detail="GATEWAY_FAILURE",
                count=1,
                started=started,
                selected=identity,
                attempt=attempt_state[0], gateway_called=True,
            )
        return self._result(
            checked,
            profile,
            selection=selection,
            reason=reason,
            status=InvocationStatus.SUCCESS,
            error_code=None,
            detail=None,
            count=1,
            started=started,
            response=response,
            selected=identity,
            execution_metadata=execution_metadata,
            attempt=attempt_state[0], gateway_called=True,
        )

    async def ainvoke(self, request: ModelInvocationRequest, *, authority_source: TaskAuthorityPort | None = None) -> ModelInvocationResult:
        if type(request) is not ModelInvocationRequest:
            raise TypeError("model invocation request is invalid")
        checked = ModelInvocationRequest.model_validate(copy.deepcopy(request.model_dump(mode="python")))
        started = self._clock()
        profile = self._fallback_profile(checked)
        try:
            profile = self._task_profile(checked)
            if checked.invocation_mode is InvocationMode.TASK:
                expected_stage = _TASK_OPERATION_STAGES.get(checked.authority_reference.operation_id)
                if expected_stage is None:
                    raise ModelSelectionError("TASK_MODEL_OPERATION_UNAVAILABLE")
                if checked.stage_id is not expected_stage:
                    raise ModelSelectionError("TASK_STAGE_BINDING_MISMATCH")
                if authority_source is None:
                    raise ModelSelectionError("TASK_AUTHORITY_STALE")
            if checked.request_override is not None:
                raise ModelSelectionError("REQUEST_OVERRIDE_UNAUTHORIZED")
            if checked.invocation_mode is InvocationMode.TASK:
                historical = self._prior_bundle_result(checked, profile)
                if historical is not None:
                    return historical
            selection = self._selection.select_governed_model(
                profile, request_override=checked.request_override
            )
            selection = ModelSelectionResult.model_validate(copy.deepcopy(selection.model_dump(mode="python")))
        except ModelSelectionError as error:
            return self._result(checked, profile, selection=error.selection, reason="GOVERNANCE_REJECTED" if error.selection is None else error.selection.selection_reason, status=InvocationStatus.REJECTED, error_code=error.code, detail=error.code, count=0, started=started, revalidation_failure=error.selection is not None)
        except (TypeError, ValueError, ValidationError):
            return self._result(checked, profile, selection=None, reason="GOVERNANCE_REJECTED", status=InvocationStatus.REJECTED, error_code="MODEL_GATEWAY_EXECUTION_FAILED", detail="GOVERNANCE_RESULT_INVALID", count=0, started=started)
        except Exception:  # noqa: BLE001 - governance boundary is fail-closed
            return self._result(checked, profile, selection=None, reason="GOVERNANCE_FAILURE", status=InvocationStatus.FAILED, error_code="MODEL_GATEWAY_EXECUTION_FAILED", detail="GOVERNANCE_FAILURE", count=0, started=started)
        if selection.selection_reason in _PENDING_REASONS:
            detail = "CANDIDATE_GROUP_PENDING" if selection.selection_reason.startswith("CANDIDATE_GROUP") else "AUTO_ROUTER_PENDING"
            return self._result(checked, profile, selection=selection, reason=selection.selection_reason, status=InvocationStatus.REJECTED, error_code="MODEL_SELECTION_PENDING_ROUTER", detail=detail, count=0, started=started)
        if selection.selected_model is None:
            if selection.auto_selection_evidence is not None:
                code = "INSUFFICIENT_EVIDENCE" if selection.selection_reason == "AUTO_INSUFFICIENT_EVIDENCE" else "MODEL_SELECTION_FAILED"
                return self._result(checked, profile, selection=selection, reason=selection.selection_reason, status=InvocationStatus.REJECTED, error_code=code, detail=code, count=0, started=started)
            if selection.candidate_selection_evidence is not None:
                return self._result(checked, profile, selection=selection, reason=selection.selection_reason, status=InvocationStatus.REJECTED, error_code="MODEL_SELECTION_FAILED", detail="MODEL_SELECTION_FAILED", count=0, started=started)
            detail = "CANDIDATE_GROUP_PENDING" if selection.selection_reason.startswith("CANDIDATE_GROUP") else "AUTO_ROUTER_PENDING"
            return self._result(checked, profile, selection=selection, reason=selection.selection_reason, status=InvocationStatus.REJECTED, error_code="MODEL_SELECTION_PENDING_ROUTER", detail=detail, count=0, started=started)
        identity = selection.selected_model
        try:
            bound_request = self._build_request(checked, identity)
            attempt = self._prepare_attempt(checked, profile, selection, authority_source)
        except ModelSelectionError as error:
            if error.code in {"MODEL_INVOCATION_ALREADY_ATTEMPTED", "MODEL_INVOCATION_REQUEST_CONFLICT"}:
                if error.code == "MODEL_INVOCATION_ALREADY_ATTEMPTED":
                    restored = self._existing_budget_result(checked, profile, selection, selection.selection_reason)
                    if restored is not None:
                        return restored
                return ModelInvocationResult(
                    status=InvocationStatus.REJECTED,
                    context=self._context(checked, profile, selection=selection, reason=selection.selection_reason, error_code=error.code),
                    error_code=error.code, detail=error.code, provider_request_count=0,
                )
            return self._result(checked, profile, selection=selection, reason=selection.selection_reason,
                                status=InvocationStatus.REJECTED, error_code=error.code,
                                detail=error.code, count=0, started=started)
        except Exception:  # noqa: BLE001 - pre-dispatch persistence must fail closed
            return self._result(checked, profile, selection=selection, reason=selection.selection_reason,
                                status=InvocationStatus.FAILED, error_code="MODEL_GATEWAY_EXECUTION_FAILED",
                                detail="ATTEMPT_PERSISTENCE_FAILED", count=0, started=started)
        attempt_state = [attempt]
        try:
            response, execution_metadata = await self._aexecute_bound(
                identity,
                bound_request,
                dispatch_guard=self._budget_guard(checked, profile, selection, authority_source, attempt_state, asynchronous=True),
            )
        except ModelGatewayRejected as error:
            gateway_code = getattr(error, "reason_code", "MODEL_GATEWAY_EXECUTION_FAILED")
            gateway_status = (
                InvocationStatus.REJECTED
                if gateway_code in _GOVERNANCE_REJECTION_CODES
                else InvocationStatus.FAILED
            )
            return self._result(checked, profile, selection=selection, reason=selection.selection_reason, status=gateway_status, error_code=gateway_code, detail="GATEWAY_REJECTED" if gateway_status is InvocationStatus.REJECTED else "GATEWAY_FAILURE", count=0, started=started, selected=identity, attempt=attempt_state[0], gateway_called=True, send_count_override=0)
        except ModelGatewayProviderFailure as error:
            from .bundle_http import observed_http_failure
            http_failure = observed_http_failure(error) if attempt_state[0] is not None and attempt_state[0].attempt_schema_version == 4 else None
            response_error = error.reason_code in {"ANSWER_SCHEMA_INVALID", "PROVIDER_PARSE_FAILED", "ANSWER_EMPTY", "ANSWER_CONTRACT_MISMATCH"}
            return self._result(checked, profile, selection=selection, reason=selection.selection_reason, status=InvocationStatus.FAILED, error_code="MODEL_PROVIDER_RESPONSE_INVALID" if response_error else "MODEL_PROVIDER_REQUEST_FAILED", detail="PROVIDER_RESPONSE_INVALID" if response_error else "PROVIDER_REQUEST_FAILED", count=1, started=started, selected=identity, http_status=http_failure.http_status if http_failure is not None else getattr(error, "safe_http_status", None), connection_id=getattr(error, "connection_id", None), generation_http_failure=http_failure, attempt=attempt_state[0], gateway_called=True)
        except ModelGatewayUnavailable:
            return self._result(checked, profile, selection=selection, reason=selection.selection_reason, status=InvocationStatus.FAILED, error_code="MODEL_GATEWAY_EXECUTION_FAILED", detail="GATEWAY_UNAVAILABLE", count=0, started=started, selected=identity, attempt=attempt_state[0], gateway_called=True)
        except asyncio.CancelledError:
            try:
                if attempt_state[0] is not None and attempt_state[0].status.value == "DISPATCH_INTENT":
                    (self._receipt_store.mark_bundle_unknown if attempt_state[0].attempt_schema_version == 4 else self._receipt_store.mark_attempt_unknown)(attempt_state[0])
            except Exception:  # noqa: BLE001, S110 - durable DISPATCH_INTENT remains visible
                pass
            raise
        except Exception:  # noqa: BLE001 - provider boundary is redacted and fail-closed
            return self._result(checked, profile, selection=selection, reason=selection.selection_reason, status=InvocationStatus.FAILED, error_code="MODEL_GATEWAY_EXECUTION_FAILED", detail="GATEWAY_FAILURE", count=1, started=started, selected=identity, attempt=attempt_state[0], gateway_called=True)
        return self._result(checked, profile, selection=selection, reason=selection.selection_reason, status=InvocationStatus.SUCCESS, error_code=None, detail=None, count=1, started=started, response=response, selected=identity, execution_metadata=execution_metadata, attempt=attempt_state[0], gateway_called=True)


__all__ = ("ManagedModelSelectionResolver", "ModelInvocationService")
