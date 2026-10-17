from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from embedded_copilot.correlation import CorrelationContext
from embedded_copilot.engineering_context import EngineeringContextSnapshot
from embedded_copilot.model_gateway import (
    AccountUsageSnapshot,
    BoundModelIdentity,
    CandidateGroup,
    CandidateSpec,
    CapabilityEvidence,
    CapabilityProvenance,
    CapabilityStatus,
    CredentialProfile,
    EndpointProfile,
    EndpointType,
    EngineeringStage,
    GateStatus,
    InvocationStatus,
    ManagedModelSelectionResolver,
    ModelAvailability,
    ModelCapability,
    ModelGatewayManagementService,
    ModelGatewayProviderFailure,
    ModelGatewayRejected,
    ModelGatewayService,
    ModelInvocationReceipt,
    ModelInvocationRequest,
    ModelInvocationService,
    ModelInvocationStatus,
    ModelInvocationTraceContext,
    ModelMessage,
    ModelPolicy,
    ModelProfile,
    ModelRequest,
    ModelResponse,
    ModelSelectionResult,
    ModelSelectionService,
    ModelSelectionTaskProfile,
    ModelUsage,
    PolicyScope,
    ProtocolFamily,
    ProviderCapability,
    ProviderConnection,
    ProviderProfile,
    ProviderUsageCapability,
    RecordProvenance,
    SelectionMode,
    SQLiteModelGatewayStore,
    StageModelPolicy,
    UsageSnapshotStatus,
    VerificationStatus,
)
from embedded_copilot.model_gateway._transport_metadata import (
    ProviderExecutionMetadata,
    ProviderExecutionResult,
)
from embedded_copilot.model_gateway.invocation.contracts import (
    AttemptStatus,
    InvocationMode,
    ModelExecutionSnapshot,
    TaskAuthorityReference,
)

IDENTITY = BoundModelIdentity(
    provider_id="relay",
    endpoint_id="endpoint-relay",
    credential_ref="credential:relay",
    model_profile_id="model-relay-firmware",
    model_id="firmware-model",
)


def _request() -> ModelRequest:
    snapshot = EngineeringContextSnapshot.create(
        project_id="project-1",
        task_description="firmware task",
        memory_evidence=(),
        graph_evidence=(),
        knowledge_evidence=(),
        multimodal_evidence=(),
        conflicts=(),
        confidence=0.5,
        provenance=(),
        verification_status="UNVERIFIED",
    )
    return ModelRequest(
        request_id="request-1",
        project_id="project-1",
        task_type="FIRMWARE",
        task="firmware task",
        engineering_context=snapshot,
        system_instruction="Use supplied evidence only.",
        model_policy=ModelPolicy(primary_provider="relay", model_name="firmware-model"),
        messages=(ModelMessage(role="user", content="firmware task"),),
    )


def test_correlation_context_is_strict_immutable_and_binds_approval_to_operation():
    context = CorrelationContext(
        request_id="request-1",
        trace_id="trace-1",
        project_id="project-1",
        workflow_run_id="workflow-1",
        task_id="task-1",
        operation_id="operation-1",
        approval_id="approval-1",
    )
    assert context.operation_id == "operation-1"
    with pytest.raises(Exception):
        context.trace_id = "trace-2"
    with pytest.raises(ValueError, match="approval requires operation"):
        CorrelationContext(
            request_id="request-1",
            trace_id="trace-1",
            project_id="project-1",
            approval_id="approval-1",
        )
    with pytest.raises(ValueError, match="trace_id is invalid"):
        CorrelationContext(
            request_id="request-1",
            trace_id="api_key:exposed",
            project_id="project-1",
        )


def test_invocation_request_builds_and_revalidates_correlation_context():
    invocation = ModelInvocationRequest.from_model_request(
        _request(),
        stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT,
    )
    assert invocation.trace_context.correlation == CorrelationContext(
        request_id="request-1",
        trace_id="request-1",
        project_id="project-1",
    )
    assert ModelInvocationRequest.model_validate(
        invocation.model_dump(mode="python")
    ) == invocation

    with pytest.raises(ValueError, match="correlation binding mismatch"):
        invocation.model_copy(
            update={
                "trace_context": ModelInvocationTraceContext(
                    trace_id="request-1",
                    correlation=CorrelationContext(
                        request_id="request-other",
                        trace_id="request-1",
                        project_id="project-1",
                    ),
                )
            }
        ).model_validate(
            invocation.model_copy(
                update={
                    "trace_context": ModelInvocationTraceContext(
                        trace_id="request-1",
                        correlation=CorrelationContext(
                            request_id="request-other",
                            trace_id="request-1",
                            project_id="project-1",
                        ),
                    )
                }
            ).model_dump(mode="python")
        )

    with pytest.raises(ValueError, match="trace correlation mismatch"):
        ModelInvocationTraceContext(
            trace_id="trace-1",
            correlation=CorrelationContext(
                request_id="request-1",
                trace_id="trace-other",
                project_id="project-1",
            ),
        )
class _Selection:
    def __init__(self, result: ModelSelectionResult, *, connection_id: str = "fake-connection") -> None:
        self.result = result
        self.calls = 0
        self.connection_id = connection_id

    def select_governed_model(self, task_profile, *, request_override=None):
        self.calls += 1
        return self.result

    def execution_snapshot(self, task_profile, selection):
        if selection != self.result or selection.selected_model is None:
            raise ValueError("fake snapshot mismatch")
        return ModelExecutionSnapshot(
            project_id=task_profile.project_id, stage_id=task_profile.stage,
            selected_model=selection.selected_model, connection_id=self.connection_id,
            credential_revision="fake-credential-revision", endpoint_revision="fake-endpoint-revision",
            policy_source="STAGE_POLICY", policy_fingerprint="sha256:" + "1" * 64,
            selection_fingerprint="sha256:" + "2" * 64,
        )


class _Gateway:
    def __init__(self) -> None:
        self.calls = 0

    def generate_bound(self, identity, request):
        self.calls += 1
        return ModelResponse(
            content="EMBRIX_RUNTIME_WIRING_OK",
            model_name=identity.model_id,
            provider=identity.provider_id,
            provider_id=identity.provider_id,
            endpoint_id=identity.endpoint_id,
            credential_ref=identity.credential_ref,
            request_id=request.request_id,
            provenance=request.engineering_context.provenance,
        )

    async def agenerate_bound(self, identity, request):
        return self.generate_bound(identity, request)


class _Provider:
    capabilities = (ProviderCapability.TEXT,)
    calls = 0

    def generate(self, request):
        self.calls += 1
        assert request.model_policy.fallback_providers == ()
        return ModelResponse(
            content="EMBRIX_RUNTIME_WIRING_OK",
            model_name=request.model_policy.model_name,
            provider=request.model_policy.primary_provider,
            provenance=request.engineering_context.provenance,
        )

    async def agenerate(self, request):
        return self.generate(request)


def test_fixed_invocation_executes_bound_identity_and_writes_versioned_receipt():
    selection = _Selection(
        ModelSelectionResult(selected_model=IDENTITY, selection_reason="STAGE_FIXED_MODEL")
    )
    gateway = _Gateway()
    receipts: list[ModelInvocationReceipt] = []
    service = ModelInvocationService(
        selection_resolver=selection,
        gateway=gateway,
        receipt_store=receipts,
        clock=lambda: datetime(2026, 9, 13, tzinfo=UTC),
    )

    result = service.invoke(
        ModelInvocationRequest.from_model_request(
            _request(), stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT
        )
    )

    assert result.status is ModelInvocationStatus.SUCCESS
    assert result.response is not None
    assert result.response.content == "EMBRIX_RUNTIME_WIRING_OK"
    assert result.context is not None
    assert result.context.selected_model == IDENTITY
    assert gateway.calls == 1
    assert len(receipts) == 1
    assert receipts[0].schema_version == 5
    assert receipts[0].gateway_call_count == 1
    assert receipts[0].gateway_response_observed is True
    assert receipts[0].transport_send_count is None
    assert receipts[0].http_status is None
    assert "firmware task" not in receipts[0].model_dump_json()


def test_transport_metadata_reaches_invocation_receipt_without_public_dto_changes():
    class _MetadataGateway(_Gateway):
        def _generate_bound_with_metadata(self, identity, request):
            self.calls += 1
            response = self.generate_bound(identity, request)
            return ProviderExecutionResult(
                response=response,
                metadata=ProviderExecutionMetadata(
                    provider_request_id="provider-request-1",
                    http_status=201,
                    latency_ms=12.5,
                    transport_name="httpx",
                ),
            )

    selection = _Selection(
        ModelSelectionResult(selected_model=IDENTITY, selection_reason="STAGE_FIXED_MODEL")
    )
    receipts: list[ModelInvocationReceipt] = []
    service = ModelInvocationService(
        selection_resolver=selection,
        gateway=_MetadataGateway(),
        receipt_store=receipts,
        clock=lambda: datetime(2026, 9, 13, tzinfo=UTC),
    )

    result = service.invoke(
        ModelInvocationRequest.from_model_request(
            _request(), stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT
        )
    )

    assert result.status is ModelInvocationStatus.SUCCESS
    assert result.receipt is not None
    assert result.receipt.provider_request_id == "provider-request-1"
    assert result.receipt.http_status == 201
    assert result.receipt.latency_ms == 12.5


def test_success_without_transport_metadata_cannot_infer_http_status():
    selection = _Selection(
        ModelSelectionResult(selected_model=IDENTITY, selection_reason="STAGE_FIXED_MODEL")
    )
    receipts: list[ModelInvocationReceipt] = []
    service = ModelInvocationService(
        selection_resolver=selection, gateway=_Gateway(), receipt_store=receipts
    )

    result = service.invoke(
        ModelInvocationRequest.from_model_request(
            _request(), stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT
        )
    )

    assert result.status is ModelInvocationStatus.SUCCESS
    assert result.receipt is not None
    assert result.receipt.http_status is None


@pytest.mark.parametrize(
    "reason, detail",
    [("AUTO_ROUTER_PENDING", "AUTO_ROUTER_PENDING"), ("CANDIDATE_GROUP_PENDING", "CANDIDATE_GROUP_PENDING")],
)
def test_pending_selection_never_calls_gateway(reason: str, detail: str):
    selection = _Selection(
        ModelSelectionResult(selected_model=None, selection_reason=reason)
    )
    gateway = _Gateway()
    service = ModelInvocationService(selection_resolver=selection, gateway=gateway)

    result = service.invoke(
        ModelInvocationRequest.from_model_request(
            _request(), stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT
        )
    )

    assert result.status is ModelInvocationStatus.REJECTED
    assert result.error_code == "MODEL_SELECTION_PENDING_ROUTER"
    assert result.detail == detail
    assert result.provider_request_count == 0
    assert gateway.calls == 0
    assert result.receipt is None


def test_canonical_gateway_bound_path_preserves_identity_and_clears_fallbacks():
    provider = _Provider()
    gateway = ModelGatewayService(providers={"relay": provider, "endpoint-relay": provider})
    response = gateway.generate_bound(IDENTITY, _request())
    assert response.provider_id == "relay"
    assert response.endpoint_id == "endpoint-relay"
    assert response.credential_ref == "credential:relay"
    assert response.model_name == "firmware-model"
    assert provider.calls == 1


def test_canonical_gateway_preserves_internal_transport_metadata():
    class _MetadataProvider(_Provider):
        def _generate_with_metadata(self, request):
            return ProviderExecutionResult(
                response=self.generate(request),
                metadata=ProviderExecutionMetadata(
                    provider_request_id="provider-request-2",
                    http_status=202,
                    latency_ms=4.25,
                    transport_name="httpx",
                ),
            )

    provider = _MetadataProvider()
    gateway = ModelGatewayService(providers={"relay": provider, "endpoint-relay": provider})

    execution = gateway._generate_bound_with_metadata(IDENTITY, _request())

    assert execution.response.provider_id == "relay"
    assert execution.metadata is not None
    assert execution.metadata.provider_request_id == "provider-request-2"
    assert execution.metadata.http_status == 202
    assert execution.metadata.latency_ms == 4.25


def test_receipts_share_sqlite_file_but_not_model_state_fingerprint(tmp_path):
    store = SQLiteModelGatewayStore(tmp_path / "model-gateway.sqlite3")
    selection = _Selection(
        ModelSelectionResult(selected_model=IDENTITY, selection_reason="STAGE_FIXED_MODEL")
    )
    service = ModelInvocationService(
        selection_resolver=selection,
        gateway=_Gateway(),
        receipt_store=store,
        clock=lambda: datetime(2026, 9, 13, tzinfo=UTC),
    )
    result = service.invoke(
        ModelInvocationRequest.from_model_request(
            _request(), stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT
        )
    )
    assert result.receipt is not None
    assert store.load() is None
    restored = SQLiteModelGatewayStore(tmp_path / "model-gateway.sqlite3")
    assert restored.get_receipt(result.receipt.fingerprint) == result.receipt
    assert restored.list_receipts() == (result.receipt,)


def test_receipt_append_is_idempotent_but_rejects_same_fingerprint_conflict(tmp_path):
    store = SQLiteModelGatewayStore(tmp_path / "model-gateway.sqlite3")
    receipt = ModelInvocationReceipt(
        request_id="request-1",
        trace_context={
            "trace_id": "trace-1",
            "correlation": {
                "request_id": "request-1",
                "trace_id": "trace-1",
                "project_id": "project-1",
            },
        },
        project_id="project-1",
        stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT,
        task_type="FIRMWARE",
        policy_source="STAGE_POLICY",
        selection_mode=SelectionMode.FIXED_MODEL,
        selection_reason="STAGE_FIXED_MODEL",
        selected_model=IDENTITY,
        required_capabilities=(),
        gates=tuple(
            {"name": name, "status": GateStatus.PASS}
            for name in ("capability", "availability", "credential", "endpoint_health", "quota")
        ),
        started_at=datetime(2026, 9, 13, tzinfo=UTC),
        completed_at=datetime(2026, 9, 13, 0, 0, 1, tzinfo=UTC),
        latency_ms=1.0,
        result=InvocationStatus.SUCCESS,
        provider_request_count=1,
    )
    assert store.append_receipt(receipt) == receipt
    assert store.append_receipt(receipt) == receipt
    assert store.list_receipts()[0].trace_context.correlation == receipt.trace_context.correlation
    assert store.list_receipts()[0].fingerprint == receipt.fingerprint
    conflict = receipt.model_copy(update={"latency_ms": 2.0})
    with pytest.raises(ValueError, match="receipt conflict"):
        store.append_receipt(conflict)

    legacy_material = receipt.model_dump(mode="python", exclude={"connection_id"})
    assert ModelInvocationReceipt.model_validate(legacy_material) == receipt
    assert ModelInvocationReceipt.model_validate(legacy_material).connection_id is None

    versioned = receipt.model_dump(mode="python")
    versioned.update(schema_version=3, connection_id="conn-at-invocation", fingerprint="sha256:" + "0" * 64)
    current = ModelInvocationReceipt.model_validate(versioned)
    assert current.connection_id == "conn-at-invocation"
    assert current.fingerprint != receipt.fingerprint
    assert ModelInvocationReceipt.model_validate(current.model_dump(mode="python")) == current
    tampered = current.model_dump(mode="python")
    tampered["connection_id"] = "conn-different"
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        ModelInvocationReceipt.model_validate(tampered)
    legacy_with_provenance = receipt.model_dump(mode="python")
    legacy_with_provenance["connection_id"] = "conn-at-invocation"
    with pytest.raises(ValueError):
        ModelInvocationReceipt.model_validate(legacy_with_provenance)


def test_receipt_rejects_mismatched_correlation_identity():
    with pytest.raises(ValueError, match="receipt correlation binding mismatch"):
        ModelInvocationReceipt(
            request_id="request-1",
            trace_context={
                "trace_id": "trace-1",
                "correlation": {
                    "request_id": "request-other",
                    "trace_id": "trace-1",
                    "project_id": "project-1",
                },
            },
            project_id="project-1",
            stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT,
            task_type="FIRMWARE",
            policy_source="STAGE_POLICY",
            selection_mode=SelectionMode.FIXED_MODEL,
            selection_reason="STAGE_FIXED_MODEL",
            required_capabilities=(),
            gates=tuple(
                {"name": name, "status": GateStatus.PASS}
                for name in (
                    "capability",
                    "availability",
                    "credential",
                    "endpoint_health",
                    "quota",
                )
            ),
            started_at=datetime(2026, 9, 13, tzinfo=UTC),
            completed_at=datetime(2026, 9, 13, 0, 0, 1, tzinfo=UTC),
            latency_ms=1.0,
            result=InvocationStatus.SUCCESS,
        )


def test_runtime_selection_rejects_unknown_health_and_quota():
    from embedded_copilot.model_gateway import (
        CapabilityEvidence,
        CapabilityProvenance,
        CapabilityStatus,
        ModelAvailability,
        ModelCapability,
    )

    model = ModelProfile(
        model_profile_id=IDENTITY.model_profile_id,
        provider_id=IDENTITY.provider_id,
        endpoint_id=IDENTITY.endpoint_id,
        remote_model_id=IDENTITY.model_id,
        display_name="firmware",
        advertised_family="relay",
        capabilities=(ModelCapability.TEXT, ModelCapability.CODING, ModelCapability.TOOL_CALLING),
        capability_sources=tuple(
            CapabilityEvidence(capability=cap, provenance=CapabilityProvenance.VERIFIED, status=CapabilityStatus.PASS, provider_id=IDENTITY.provider_id, endpoint_id=IDENTITY.endpoint_id, credential_ref=IDENTITY.credential_ref, model_profile_id=IDENTITY.model_profile_id, model_id=IDENTITY.model_id)
            for cap in (ModelCapability.TEXT, ModelCapability.CODING, ModelCapability.TOOL_CALLING)
        ),
        availability=ModelAvailability.AVAILABLE,
    )
    selector = ModelSelectionService(
        models=(model,),
        credential_ref_by_endpoint={IDENTITY.endpoint_id: IDENTITY.credential_ref},
        available_credential_refs=(IDENTITY.credential_ref,),
        endpoint_health={IDENTITY.endpoint_id: "UNKNOWN"},
        quota_available_by_endpoint={},
    )
    task = ModelSelectionTaskProfile(
        request_id="request-1",
        project_id="project-1",
        stage=EngineeringStage.FIRMWARE_DEVELOPMENT,
        task_type="FIRMWARE",
    )
    with pytest.raises(Exception, match="MODEL_AVAILABILITY_GATE_FAILED"):
        selector.select_model(task, None, (), request_override=IDENTITY)


def test_runtime_receipt_contains_all_governance_gates():
    selection = _Selection(
        ModelSelectionResult(selected_model=IDENTITY, selection_reason="STAGE_FIXED_MODEL")
    )
    receipts: list[ModelInvocationReceipt] = []
    service = ModelInvocationService(
        selection_resolver=selection,
        gateway=_Gateway(),
        receipt_store=receipts,
    )
    result = service.invoke(
        ModelInvocationRequest.from_model_request(
            _request(), stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT
        )
    )
    assert result.receipt is not None
    assert {gate.name for gate in result.receipt.gates} == {
        "capability",
        "availability",
        "credential",
        "endpoint_health",
        "quota",
    }


def test_managed_resolver_preserves_stage_policy_source_and_revision():
    class _Profile:
        credential_revision = "revision-1"

    class _Management:
        def select_governed_model(self, task_profile, **_kwargs):
            return ModelSelectionResult(selected_model=IDENTITY, selection_reason="STAGE_FIXED_MODEL")

        def stage_model_policy(self, _project_id, _stage):
            return object()

        def project_default_model_policy(self, _project_id):
            return None

        def model_profile(self, _model_profile_id):
            return _Profile()

        def _validate_runtime_eligibility(self, _identity, **_kwargs):
            return None

    resolver = ManagedModelSelectionResolver(
        _Management(), lambda: (IDENTITY.credential_ref,)
    )
    service = ModelInvocationService(
        selection_resolver=resolver,
        gateway=_Gateway(),
    )
    result = service.invoke(
        ModelInvocationRequest.from_model_request(
            _request(), stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT
        )
    )
    assert result.context.policy_source == "STAGE_POLICY"
    assert result.context.credential_revision == "revision-1"


_RUNTIME_NOW = datetime(2026, 9, 13, 12, tzinfo=UTC)
_RUNTIME_CONNECTION_ID = "connection-relay-1"
_RUNTIME_CREDENTIAL_REVISION = "credential-revision-1"
_RUNTIME_ENDPOINT_REVISION = "endpoint-revision-1"


class _PostSelectionManagement:
    """Inject a state change between governed selection and runtime revalidation."""

    def __init__(self, management: ModelGatewayManagementService) -> None:
        self._management = management
        self.after_selection: Callable[[], None] | None = None
        self.selection_calls = 0
        self.post_selection_mutations = 0

    def __getattr__(self, name: str):
        return getattr(self._management, name)

    def select_governed_model(self, *args, **kwargs):
        selection = self._management.select_governed_model(*args, **kwargs)
        self.selection_calls += 1
        if self.after_selection is not None:
            self.after_selection()
            self.post_selection_mutations += 1
        return selection


def _runtime_quota_snapshot(**changes) -> AccountUsageSnapshot:
    material = {
        "provider_id": IDENTITY.provider_id,
        "endpoint_id": IDENTITY.endpoint_id,
        "connection_id": _RUNTIME_CONNECTION_ID,
        "credential_ref": IDENTITY.credential_ref,
        "credential_revision": _RUNTIME_CREDENTIAL_REVISION,
        "status": UsageSnapshotStatus.AVAILABLE,
        "quota_remaining": 25,
        "updated_at": _RUNTIME_NOW - timedelta(seconds=30),
    }
    material.update(changes)
    return AccountUsageSnapshot(**material)


def _governed_runtime(*, receipt_store=None):
    management = ModelGatewayManagementService(
        clock=lambda: _RUNTIME_NOW,
        id_factory=lambda prefix: f"{prefix}-runtime-test",
    )
    management.add_provider(
        ProviderProfile(
            provider_id=IDENTITY.provider_id,
            display_name="Relay",
            supported_protocols=(ProtocolFamily.OPENAI_CHAT,),
        )
    )
    management.add_endpoint(
        EndpointProfile(
            endpoint_id=IDENTITY.endpoint_id,
            provider_id=IDENTITY.provider_id,
            display_name="Relay endpoint",
            endpoint_type=EndpointType.OFFICIAL,
            protocol_family=ProtocolFamily.OPENAI_CHAT,
            base_url="https://relay.example.test/v1",
            credential_ref=IDENTITY.credential_ref,
            health_status="AVAILABLE",
            created_at=_RUNTIME_NOW - timedelta(hours=1),
            updated_at=_RUNTIME_NOW - timedelta(hours=1),
        )
    )
    management.add_credential(
        CredentialProfile(
            credential_id="credential-relay-1",
            provider_id=IDENTITY.provider_id,
            display_name="Relay test credential",
            secret_ref=IDENTITY.credential_ref,
        )
    )
    management.add_connection(
        ProviderConnection(
            connection_id=_RUNTIME_CONNECTION_ID,
            provider_id=IDENTITY.provider_id,
            endpoint_id=IDENTITY.endpoint_id,
            credential_ref=IDENTITY.credential_ref,
        )
    )

    capabilities = tuple(ModelCapability)
    model = ModelProfile(
        model_profile_id=IDENTITY.model_profile_id,
        provider_id=IDENTITY.provider_id,
        endpoint_id=IDENTITY.endpoint_id,
        remote_model_id=IDENTITY.model_id,
        display_name="Firmware model",
        advertised_family="Relay",
        capabilities=capabilities,
        capability_sources=tuple(
            CapabilityEvidence(
                capability=capability,
                provenance=CapabilityProvenance.VERIFIED,
                status=CapabilityStatus.PASS,
                provider_id=IDENTITY.provider_id,
                endpoint_id=IDENTITY.endpoint_id,
                credential_ref=IDENTITY.credential_ref,
                model_profile_id=IDENTITY.model_profile_id,
                model_id=IDENTITY.model_id,
                credential_revision=_RUNTIME_CREDENTIAL_REVISION,
                endpoint_revision=_RUNTIME_ENDPOINT_REVISION,
            )
            for capability in capabilities
        ),
        availability=ModelAvailability.AVAILABLE,
        availability_status=ModelAvailability.AVAILABLE,
        enabled=True,
        credential_revision=_RUNTIME_CREDENTIAL_REVISION,
        endpoint_revision=_RUNTIME_ENDPOINT_REVISION,
        verification_status=VerificationStatus.VERIFIED,
        record_provenance=RecordProvenance.EXPLICIT_CONFIGURATION,
    )
    management.add_model(model)
    management.create_allowed_model_policy(
        scope=PolicyScope.GLOBAL,
        project_id=None,
        stage_id=None,
        identity=IDENTITY,
    )
    management.set_stage_model_policy(
        StageModelPolicy(
            id="firmware-fixed-runtime-test",
            project_id="project-1",
            stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT,
            selection_mode=SelectionMode.FIXED_MODEL,
            fixed_model=IDENTITY,
            created_at=_RUNTIME_NOW,
            updated_at=_RUNTIME_NOW,
        )
    )

    quota_snapshots = [_runtime_quota_snapshot()]
    # The management service reads this evidence once for policy selection and
    # again inside _validate_runtime_eligibility. Tests may change it between
    # those two reads to exercise the execution-time boundary.
    management.account_usage_snapshots = lambda: tuple(quota_snapshots)
    current_revisions = {
        "credential": _RUNTIME_CREDENTIAL_REVISION,
        "endpoint": _RUNTIME_ENDPOINT_REVISION,
    }
    post_selection_management = _PostSelectionManagement(management)
    resolver = ManagedModelSelectionResolver(
        post_selection_management,
        available_credentials=lambda: (IDENTITY.credential_ref,),
        credential_revision_for_connection=lambda connection_id: (
            current_revisions["credential"]
            if connection_id == _RUNTIME_CONNECTION_ID
            else None
        ),
        endpoint_revision=lambda _endpoint: current_revisions["endpoint"],
    )
    provider = _Provider()
    gateway = ModelGatewayService(
        providers={IDENTITY.provider_id: provider, IDENTITY.endpoint_id: provider}
    )
    invocation = ModelInvocationService(
        selection_resolver=resolver,
        gateway=gateway,
        receipt_store=receipt_store,
        clock=lambda: _RUNTIME_NOW,
    )
    return (
        management,
        quota_snapshots,
        current_revisions,
        post_selection_management,
        provider,
        invocation,
    )


def _run_invocation(invocation: ModelInvocationService, *, async_execution: bool):
    request = ModelInvocationRequest.from_model_request(
        _request(), stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT
    )
    if async_execution:
        return asyncio.run(invocation.ainvoke(request))
    return invocation.invoke(request)


def _task_authority() -> TaskAuthorityReference:
    fingerprint = "sha256:" + "a" * 64
    return TaskAuthorityReference(
        tenant_id="tenant-1", project_id="project-1", engineering_task_id="task-1",
        task_request_fingerprint=fingerprint, coordination_fingerprint=fingerprint,
        plan_id="plan-1", plan_content_fingerprint=fingerprint,
        review_fingerprint=fingerprint, phase_id="phase-1",
        phase_fingerprint=fingerprint, plan_task_id="plan-task-1",
        operation_id="HARDWARE_SELECTION_PROPOSAL", operation_binding_fingerprint=fingerprint,
    )


@pytest.mark.parametrize("async_execution", [False, True])
@pytest.mark.parametrize("source_state", ["current", "stale", "missing", "error"])
def test_task_authority_is_revalidated_before_dispatch(tmp_path, async_execution: bool, source_state: str) -> None:
    class _AuthoritySource:
        calls = 0

        def revalidate(self, reference):
            self.calls += 1
            if source_state == "error":
                raise ValueError("stale task")
            if source_state == "stale":
                return reference.model_copy(update={"review_fingerprint": "sha256:" + "b" * 64})
            return reference

    authority = _task_authority()
    request = ModelInvocationRequest.from_model_request(
        _request(), stage_id=EngineeringStage.HARDWARE_SELECTION,
    ).model_copy(update={"invocation_mode": InvocationMode.TASK, "authority_reference": authority})
    store = SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3")
    gateway = _Gateway()
    service = ModelInvocationService(
        selection_resolver=_Selection(ModelSelectionResult(selected_model=IDENTITY, selection_reason="STAGE_FIXED_MODEL")),
        gateway=gateway, receipt_store=store,
    )
    source = None if source_state == "missing" else _AuthoritySource()
    result = asyncio.run(service.ainvoke(request, authority_source=source)) if async_execution else service.invoke(request, authority_source=source)
    if source_state == "current":
        # Current Task authority alone cannot authorize an unbudgeted real send.
        assert result.status is InvocationStatus.REJECTED
        assert result.error_code == "BUDGET_EVIDENCE_UNAVAILABLE"
        assert gateway.calls == 0
        assert result.receipt is not None and result.receipt.schema_version == 6
        assert result.receipt.authority_reference == authority
        assert result.receipt.gateway_call_count == 0
        assert result.receipt.gateway_response_observed is False
        assert result.receipt.transport_send_count == 0
        assert result.receipt.provider_request_count == 0
        attempt = store.get_attempt_for_project("project-1", "request-1")
        assert attempt is not None and attempt.status is AttemptStatus.PREPARED
        assert attempt.authority_reference == authority
    else:
        assert result.status is InvocationStatus.REJECTED
        assert result.error_code == "TASK_AUTHORITY_STALE"
        assert gateway.calls == 0
        assert result.provider_request_count == 0
        assert result.receipt is not None
        assert result.receipt.gateway_call_count == 0
        assert result.receipt.gateway_response_observed is False
        assert result.receipt.transport_send_count == 0
        attempt = store.get_attempt_for_project("project-1", "request-1")
        assert attempt is None or attempt.status is AttemptStatus.PREPARED


@pytest.mark.parametrize("async_execution", [False, True])
def test_task_stage_mismatch_never_reaches_gateway(tmp_path, async_execution: bool) -> None:
    request = ModelInvocationRequest.from_model_request(
        _request(), stage_id=EngineeringStage.SCHEMATIC_DESIGN,
    ).model_copy(update={"invocation_mode": InvocationMode.TASK, "authority_reference": _task_authority()})
    gateway = _Gateway()
    service = ModelInvocationService(
        selection_resolver=_Selection(ModelSelectionResult(selected_model=IDENTITY, selection_reason="STAGE_FIXED_MODEL")),
        gateway=gateway, receipt_store=SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3"),
    )
    result = asyncio.run(service.ainvoke(request, authority_source=None)) if async_execution else service.invoke(request, authority_source=None)
    assert result.status is InvocationStatus.REJECTED
    assert result.error_code == "TASK_STAGE_BINDING_MISMATCH"
    assert gateway.calls == 0


def test_durable_attempt_prevents_restart_replay_after_confirmed_response(tmp_path) -> None:
    database = tmp_path / "gateway.sqlite3"
    store = SQLiteModelGatewayStore(database)
    _, _, _, _, provider, invocation = _governed_runtime(receipt_store=store)
    first = _run_invocation(invocation, async_execution=False)
    assert first.status is InvocationStatus.SUCCESS
    attempt = SQLiteModelGatewayStore(database).get_attempt_for_project("project-1", "request-1")
    assert attempt is not None
    assert attempt.status is AttemptStatus.COMPLETED
    assert attempt.confirmed_provider_request_count is None
    assert attempt.gateway_call_count == 1
    assert attempt.gateway_response_observed is True
    assert attempt.transport_send_count is None
    assert attempt.receipt_fingerprint == first.receipt.fingerprint
    restarted = _run_invocation(invocation, async_execution=False)
    assert restarted.status is InvocationStatus.REJECTED
    assert restarted.error_code == "MODEL_INVOCATION_ALREADY_ATTEMPTED"
    assert provider.calls == 1


@pytest.mark.parametrize("failed_step", ["begin", "dispatch"])
def test_attempt_persistence_failure_prevents_provider_call(tmp_path, failed_step: str) -> None:
    class _FailingStore(SQLiteModelGatewayStore):
        def begin_attempt(self, attempt):
            if failed_step == "begin":
                raise OSError("fake persistence failure")
            return super().begin_attempt(attempt)

        def mark_dispatch_intent(self, attempt):
            if failed_step == "dispatch":
                raise OSError("fake persistence failure")
            return super().mark_dispatch_intent(attempt)

    store = _FailingStore(tmp_path / "gateway.sqlite3")
    _, _, _, _, provider, invocation = _governed_runtime(receipt_store=store)
    result = _run_invocation(invocation, async_execution=False)
    assert result.status is InvocationStatus.FAILED
    assert result.provider_request_count == 0
    assert provider.calls == 0
    attempt = SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3").get_attempt_for_project("project-1", "request-1")
    assert (attempt is None) if failed_step == "begin" else attempt.status is AttemptStatus.PREPARED


def test_cancelled_dispatch_is_unknown_and_not_replayed(tmp_path) -> None:
    class _CancelledGateway(_Gateway):
        async def agenerate_bound(self, identity, request):
            self.calls += 1
            raise asyncio.CancelledError

    store = SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3")
    _, _, _, _, _, invocation = _governed_runtime(receipt_store=store)
    gateway = _CancelledGateway()
    invocation._gateway = gateway
    with pytest.raises(asyncio.CancelledError):
        _run_invocation(invocation, async_execution=True)
    attempt = SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3").get_attempt_for_project("project-1", "request-1")
    assert attempt is not None and attempt.status is AttemptStatus.OUTCOME_UNKNOWN
    assert attempt.confirmed_provider_request_count is None
    replay = _run_invocation(invocation, async_execution=False)
    assert replay.error_code == "MODEL_INVOCATION_ALREADY_ATTEMPTED"
    assert gateway.calls == 1


def test_response_receipt_failure_preserves_unknown_attempt_and_actual_count(tmp_path) -> None:
    class _FailingStore(SQLiteModelGatewayStore):
        def complete_attempt(self, attempt, receipt):
            raise OSError("fake receipt failure")

    store = _FailingStore(tmp_path / "gateway.sqlite3")
    _, _, _, _, provider, invocation = _governed_runtime(receipt_store=store)
    result = _run_invocation(invocation, async_execution=False)
    assert result.status is InvocationStatus.FAILED
    assert result.detail == "RECEIPT_PERSISTENCE_FAILED"
    assert result.provider_request_count == 0 and provider.calls == 1
    attempt = SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3").get_attempt_for_project("project-1", "request-1")
    assert attempt is not None and attempt.status is AttemptStatus.OUTCOME_UNKNOWN
    assert attempt.confirmed_provider_request_count is None
    assert store.list_receipts_for_project("project-1") == ()


def test_attempt_is_project_scoped_and_rejects_same_id_with_changed_request(tmp_path) -> None:
    store = SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3")
    _, _, _, _, provider, invocation = _governed_runtime(receipt_store=store)
    assert _run_invocation(invocation, async_execution=False).status is InvocationStatus.SUCCESS
    assert store.get_attempt_for_project("other-project", "request-1") is None
    original = ModelInvocationRequest.from_model_request(_request(), stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT)
    changed = original.model_copy(update={
        "generation_options": original.generation_options.model_copy(update={"max_tokens": 41}),
    })
    result = invocation.invoke(changed)
    assert result.error_code == "MODEL_INVOCATION_REQUEST_CONFLICT"
    assert provider.calls == 1


def test_provider_timeout_keeps_outcome_unknown(tmp_path) -> None:
    class _TimeoutGateway(_Gateway):
        def generate_bound(self, identity, request):
            self.calls += 1
            raise TimeoutError("fake provider timeout")

    store = SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3")
    _, _, _, _, _, invocation = _governed_runtime(receipt_store=store)
    gateway = _TimeoutGateway()
    invocation._gateway = gateway
    result = _run_invocation(invocation, async_execution=False)
    assert result.status is InvocationStatus.FAILED
    assert gateway.calls == 1
    attempt = store.get_attempt_for_project("project-1", "request-1")
    assert attempt is not None and attempt.status is AttemptStatus.OUTCOME_UNKNOWN
    assert attempt.confirmed_provider_request_count is None


def test_revision_change_after_selection_rejects_without_attempt_or_provider(tmp_path) -> None:
    store = SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3")
    _, _, revisions, post_selection, provider, invocation = _governed_runtime(receipt_store=store)
    post_selection.after_selection = lambda: revisions.update(credential="stale-revision")
    result = _run_invocation(invocation, async_execution=False)
    assert result.status is InvocationStatus.REJECTED
    assert provider.calls == 0
    assert store.get_attempt_for_project("project-1", "request-1") is None


def test_receipt_and_attempt_completion_roll_back_together(tmp_path) -> None:
    database = tmp_path / "gateway.sqlite3"
    store = SQLiteModelGatewayStore(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TRIGGER reject_attempt_completion BEFORE UPDATE ON model_invocation_attempts "
            "WHEN json_extract(NEW.payload, '$.status') = 'COMPLETED' "
            "BEGIN SELECT RAISE(ABORT, 'fake completion failure'); END"
        )
    _, _, _, _, provider, invocation = _governed_runtime(receipt_store=store)
    result = _run_invocation(invocation, async_execution=False)
    assert result.status is InvocationStatus.FAILED
    assert result.detail == "RECEIPT_PERSISTENCE_FAILED"
    assert result.provider_request_count == 0 and provider.calls == 1
    assert store.list_receipts_for_project("project-1") == ()
    attempt = store.get_attempt_for_project("project-1", "request-1")
    assert attempt is not None and attempt.status is AttemptStatus.OUTCOME_UNKNOWN


def test_attempt_status_tamper_is_rejected_on_restart(tmp_path) -> None:
    database = tmp_path / "gateway.sqlite3"
    store = SQLiteModelGatewayStore(database)
    _, _, _, _, _, invocation = _governed_runtime(receipt_store=store)
    assert _run_invocation(invocation, async_execution=False).status is InvocationStatus.SUCCESS
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT payload FROM model_invocation_attempts WHERE project_id = ? AND request_id = ?",
            ("project-1", "request-1"),
        ).fetchone()
        material = json.loads(row[0])
        material["status"] = "OUTCOME_UNKNOWN"
        material["confirmed_provider_request_count"] = None
        material["gateway_response_observed"] = False
        material["receipt_fingerprint"] = None
        connection.execute(
            "UPDATE model_invocation_attempts SET payload = ? WHERE project_id = ? AND request_id = ?",
            (json.dumps(material), "project-1", "request-1"),
        )
    with pytest.raises(ValueError, match="invocation attempt fingerprint mismatch"):
        SQLiteModelGatewayStore(database).get_attempt_for_project("project-1", "request-1")


@pytest.mark.parametrize("quota_query_supported", [False, True])
def test_fixed_model_without_usage_snapshot_respects_provider_quota_capability(quota_query_supported: bool) -> None:
    management, snapshots, _, _, provider, invocation = _governed_runtime()
    snapshots.clear()
    if quota_query_supported:
        profile = management.providers.get(IDENTITY.provider_id)
        assert profile is not None
        management.upsert_provider(profile.model_copy(update={"usage_capabilities": (ProviderUsageCapability.QUOTA_QUERY,)}))

    result = _run_invocation(invocation, async_execution=False)

    if quota_query_supported:
        assert result.status is InvocationStatus.REJECTED
        assert result.error_code == "MODEL_QUOTA_GATE_FAILED"
        assert provider.calls == 0
    else:
        assert result.status is InvocationStatus.SUCCESS
        assert provider.calls == 1


def _candidate_group_runtime(management: ModelGatewayManagementService) -> BoundModelIdentity:
    invalid = IDENTITY.model_copy(update={"model_profile_id": "model-unregistered", "model_id": "unregistered"})
    management.add_candidate_group(
        CandidateGroup(
            group_id="firmware-group",
            stage=EngineeringStage.FIRMWARE_DEVELOPMENT,
            candidates=(
                CandidateSpec(identity=invalid, priority=1),
                CandidateSpec(identity=IDENTITY, priority=2),
            ),
            revision=1,
        )
    )
    management.set_stage_model_policy(
        StageModelPolicy(
            id="firmware-group-policy",
            project_id="project-1",
            stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT,
            selection_mode=SelectionMode.CANDIDATE_GROUP,
            candidate_group_id="firmware-group",
            candidate_group_revision=1,
            created_at=_RUNTIME_NOW,
            updated_at=_RUNTIME_NOW,
        )
    )
    return invalid


class _CandidateReceiptStore:
    def append(self, receipt: ModelInvocationReceipt) -> ModelInvocationReceipt:
        return receipt


def _auto_runtime(management: ModelGatewayManagementService) -> None:
    management.set_stage_model_policy(
        StageModelPolicy(
            id="firmware-auto-policy",
            project_id="project-1",
            stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT,
            selection_mode=SelectionMode.AUTO,
            created_at=_RUNTIME_NOW,
            updated_at=_RUNTIME_NOW,
        )
    )


def test_auto_selects_governed_model_and_records_evidence() -> None:
    management, _, _, _, provider, invocation = _governed_runtime(receipt_store=_CandidateReceiptStore())
    _auto_runtime(management)

    result = _run_invocation(invocation, async_execution=False)

    assert result.status is InvocationStatus.SUCCESS
    assert result.context.selected_model == IDENTITY
    assert result.receipt is not None
    assert result.receipt.selection_mode is SelectionMode.AUTO
    assert result.receipt.auto_selection_evidence.selected_candidate == IDENTITY
    assert provider.calls == 1


@pytest.mark.parametrize("async_execution", [False, True], ids=["invoke", "ainvoke"])
@pytest.mark.parametrize(
    ("change", "expected_reason"),
    [
        ("disabled", "MODEL_DISABLED"),
        ("unverified", "MODEL_VERIFICATION_GATE_FAILED"),
        ("provenance", "MODEL_PROVENANCE_GATE_FAILED"),
        ("capability", "MODEL_CAPABILITY_MISMATCH"),
        ("quota", "MODEL_QUOTA_GATE_FAILED"),
    ],
)
def test_auto_rejects_invalid_candidate_without_provider(async_execution: bool, change: str, expected_reason: str) -> None:
    management, snapshots, _, _, provider, invocation = _governed_runtime(receipt_store=_CandidateReceiptStore())
    _auto_runtime(management)
    model = management.model_profile(IDENTITY.model_profile_id)
    assert model is not None
    if change == "disabled":
        management.models.upsert(model.model_copy(update={"enabled": False}))
    elif change == "unverified":
        management.models.upsert(model.model_copy(update={"verification_status": VerificationStatus.UNVERIFIED}))
    elif change == "provenance":
        management.models.upsert(model.model_copy(update={"record_provenance": RecordProvenance.UNKNOWN}))
    elif change == "capability":
        management.models.upsert(model.model_copy(update={"capabilities": (ModelCapability.TEXT,)}))
    else:
        snapshots[0] = snapshots[0].model_copy(update={"quota_remaining": 0})

    result = _run_invocation(invocation, async_execution=async_execution)

    assert result.status is InvocationStatus.REJECTED
    assert result.error_code == "MODEL_SELECTION_FAILED"
    assert result.provider_request_count == provider.calls == 0
    assert result.receipt.auto_selection_evidence.rejection_reasons[0].rejection_reason == expected_reason


def test_auto_evidence_is_deterministic_and_receipt_survives_restart(tmp_path) -> None:
    database_path = tmp_path / "gateway.sqlite3"
    store = SQLiteModelGatewayStore(database_path)
    management, _, _, _, provider, invocation = _governed_runtime(receipt_store=store)
    _auto_runtime(management)
    first = _run_invocation(invocation, async_execution=False)
    second = _run_invocation(invocation, async_execution=False)
    assert first.receipt.auto_selection_evidence is not None
    assert second.receipt is None
    assert SQLiteModelGatewayStore(database_path).get_receipt(first.receipt.fingerprint) == first.receipt
    assert first.receipt.schema_version == 5
    legacy_auto = first.receipt.model_dump(mode="python", exclude={"connection_id"})
    assert ModelInvocationReceipt.model_validate(legacy_auto).connection_id is None
    assert ModelInvocationReceipt.model_validate(legacy_auto).fingerprint == first.receipt.fingerprint
    current_auto_material = first.receipt.model_dump(mode="python", exclude={
        "invocation_mode", "authority_reference", "gateway_call_count",
        "gateway_response_observed", "transport_send_count",
    })
    current_auto_material.update(schema_version=3, connection_id="conn-auto", provider_request_count=1, fingerprint="sha256:" + "0" * 64)
    current_auto = ModelInvocationReceipt.model_validate(current_auto_material)
    assert current_auto.connection_id == "conn-auto"
    assert current_auto.fingerprint != first.receipt.fingerprint
    assert second.status is InvocationStatus.REJECTED
    assert second.error_code == "MODEL_INVOCATION_ALREADY_ATTEMPTED"
    assert provider.calls == 1
    tampered = first.receipt.model_dump(mode="python")
    tampered["auto_selection_evidence"]["selection_reason"] = "AUTO_SELECTION_FAILED"
    with pytest.raises(ValueError):
        ModelInvocationReceipt.model_validate(tampered)


def test_auto_selects_lowest_priority_eligible_identity() -> None:
    management, _, _, _, provider, invocation = _governed_runtime(receipt_store=_CandidateReceiptStore())
    _auto_runtime(management)
    base = management.model_profile(IDENTITY.model_profile_id)
    assert base is not None
    other = IDENTITY.model_copy(update={"model_profile_id": "model-priority-first", "model_id": "priority-first"})
    management.add_model(base.model_copy(update={
        "model_profile_id": other.model_profile_id,
        "remote_model_id": other.model_id,
        "capability_sources": tuple(item.model_copy(update={"model_profile_id": other.model_profile_id, "model_id": other.model_id}) for item in base.capability_sources),
    }))
    management.create_allowed_model_policy(scope=PolicyScope.GLOBAL, project_id=None, stage_id=None, identity=other, priority=1)

    result = _run_invocation(invocation, async_execution=False)

    assert result.status is InvocationStatus.SUCCESS
    assert result.context.selected_model == other
    assert tuple(item.identity for item in result.receipt.auto_selection_evidence.filtered_candidates) == (other, IDENTITY)
    assert provider.calls == 1

    selector = ModelSelectionService(
        models=management.models.list(),
        credential_ref_by_endpoint={IDENTITY.endpoint_id: IDENTITY.credential_ref},
        available_credential_refs=(IDENTITY.credential_ref,),
        endpoint_health={IDENTITY.endpoint_id: "AVAILABLE"},
        quota_available_by_endpoint={IDENTITY.endpoint_id: True},
    )
    task = ModelSelectionTaskProfile(
        request_id="request-auto-order",
        project_id="project-1",
        stage=EngineeringStage.FIRMWARE_DEVELOPMENT,
        task_type="FIRMWARE",
    )
    policies = management.list_allowed_model_policies()
    stage_policy = management.stage_model_policy(task.project_id, task.stage)
    normal = selector.select_model(task, stage_policy, policies, candidate_eligibility=lambda _identity: None)
    reversed_input = selector.select_model(task, stage_policy, tuple(reversed(policies)), candidate_eligibility=lambda _identity: None)
    assert normal == reversed_input
    assert provider.calls == 1


@pytest.mark.parametrize("async_execution", [False, True], ids=["invoke", "ainvoke"])
def test_auto_stale_quota_is_insufficient_evidence(async_execution: bool) -> None:
    management, snapshots, _, _, provider, invocation = _governed_runtime(receipt_store=_CandidateReceiptStore())
    _auto_runtime(management)
    snapshots[0] = snapshots[0].model_copy(update={"updated_at": _RUNTIME_NOW - timedelta(seconds=301)})

    result = _run_invocation(invocation, async_execution=async_execution)

    assert result.error_code == "INSUFFICIENT_EVIDENCE"
    assert result.provider_request_count == provider.calls == 0
    assert result.receipt.auto_selection_evidence.rejection_reasons[0].rejection_reason == "MODEL_QUOTA_GATE_FAILED"
    assert all(gate.status is not GateStatus.PASS for gate in result.receipt.gates)
    assert next(gate for gate in result.receipt.gates if gate.name == "quota").status is GateStatus.UNKNOWN


@pytest.mark.parametrize("async_execution", [False, True], ids=["invoke", "ainvoke"])
def test_auto_revalidation_failure_preserves_selection_evidence(async_execution: bool) -> None:
    management, _, _, selection, provider, invocation = _governed_runtime(receipt_store=_CandidateReceiptStore())
    _auto_runtime(management)
    selection.after_selection = lambda: management.models.upsert(
        management.model_profile(IDENTITY.model_profile_id).model_copy(update={"enabled": False})
    )

    result = _run_invocation(invocation, async_execution=async_execution)

    assert result.status is InvocationStatus.REJECTED
    assert result.provider_request_count == provider.calls == 0
    assert result.receipt.auto_selection_evidence.selected_candidate == IDENTITY
    assert result.receipt.error_code == "MODEL_DISABLED"
    assert all(gate.status is not GateStatus.PASS for gate in result.receipt.gates)


@pytest.mark.parametrize("async_execution", [False, True], ids=["invoke", "ainvoke"])
@pytest.mark.parametrize(
    ("change", "expected_code"),
    [
        ("allowed_pool", "MODEL_NOT_ALLOWED"),
        ("auto_policy", "MODEL_SELECTION_FAILED"),
        ("policy_revision", "MODEL_SELECTION_FAILED"),
    ],
)
def test_auto_revalidates_current_policy_authorization(
    async_execution: bool, change: str, expected_code: str
) -> None:
    management, _, _, selection, provider, invocation = _governed_runtime(receipt_store=_CandidateReceiptStore())
    _auto_runtime(management)
    if change == "allowed_pool":
        policy_id = management.list_allowed_model_policies()[0].id
        selection.after_selection = lambda: management.set_allowed_model_enabled(policy_id, False)
    elif change == "auto_policy":
        selection.after_selection = lambda: management.delete_stage_model_policy(
            "project-1", EngineeringStage.FIRMWARE_DEVELOPMENT
        )
    else:
        current = management.stage_model_policy("project-1", EngineeringStage.FIRMWARE_DEVELOPMENT)
        assert current is not None
        selection.after_selection = lambda: management.set_stage_model_policy(
            current.model_copy(update={"updated_at": _RUNTIME_NOW + timedelta(seconds=1)})
        )

    result = _run_invocation(invocation, async_execution=async_execution)

    assert result.status is InvocationStatus.REJECTED
    assert result.error_code == expected_code
    assert result.provider_request_count == provider.calls == 0
    assert result.receipt.auto_selection_evidence.selected_candidate == IDENTITY
    assert all(gate.status is not GateStatus.PASS for gate in result.receipt.gates)


@pytest.mark.parametrize("health,quota", [("UNKNOWN", {}), ("AVAILABLE", {})])
def test_direct_management_auto_requires_strict_runtime_evidence(health: str, quota: dict[str, bool]) -> None:
    management, _, _, _, provider, _ = _governed_runtime()
    _auto_runtime(management)
    task = ModelSelectionTaskProfile(
        request_id="request-direct-auto",
        project_id="project-1",
        stage=EngineeringStage.FIRMWARE_DEVELOPMENT,
        task_type="FIRMWARE",
    )

    result = management.select_governed_model(
        task,
        endpoint_health={IDENTITY.endpoint_id: health},
        quota_available_by_endpoint=quota,
    )

    assert result.selected_model is None
    assert result.selection_reason == "AUTO_INSUFFICIENT_EVIDENCE"
    assert result.auto_selection_evidence.selected_candidate is None
    assert provider.calls == 0


@pytest.mark.parametrize("async_execution", [False, True], ids=["invoke", "ainvoke"])
def test_candidate_group_selects_second_candidate_through_existing_invocation(async_execution: bool) -> None:
    management, _, _, _, provider, invocation = _governed_runtime(receipt_store=_CandidateReceiptStore())
    invalid = _candidate_group_runtime(management)

    result = _run_invocation(invocation, async_execution=async_execution)

    assert result.status is InvocationStatus.SUCCESS
    assert provider.calls == 1
    assert result.context.selected_model == IDENTITY
    assert result.receipt is not None
    assert result.receipt.selection_mode is SelectionMode.CANDIDATE_GROUP
    evidence = result.receipt.candidate_selection_evidence
    assert evidence is not None
    assert evidence.candidate_group_id == "firmware-group"
    assert evidence.candidate_group_revision == 1
    assert evidence.selected_candidate == IDENTITY
    assert [(item.candidate, item.rejection_reason) for item in evidence.rejected_candidates] == [
        (invalid, "MODEL_NOT_ALLOWED")
    ]


@pytest.mark.parametrize("async_execution", [False, True], ids=["invoke", "ainvoke"])
def test_candidate_group_all_invalid_fails_without_provider(async_execution: bool) -> None:
    management, _, _, _, provider, invocation = _governed_runtime(receipt_store=_CandidateReceiptStore())
    _candidate_group_runtime(management)
    model = management.model_profile(IDENTITY.model_profile_id)
    assert model is not None
    management.models.upsert(model.model_copy(update={"enabled": False}))

    result = _run_invocation(invocation, async_execution=async_execution)

    assert result.status is InvocationStatus.REJECTED
    assert result.error_code == "MODEL_SELECTION_FAILED"
    assert result.provider_request_count == provider.calls == 0
    assert result.receipt is not None
    evidence = result.receipt.candidate_selection_evidence
    assert evidence is not None
    assert evidence.selected_candidate is None
    assert [item.rejection_reason for item in evidence.rejected_candidates] == [
        "MODEL_NOT_ALLOWED", "MODEL_DISABLED"
    ]
    assert all(gate.status is GateStatus.UNKNOWN for gate in result.receipt.gates)


def test_candidate_group_selection_is_deterministic_for_same_evidence() -> None:
    management, _, _, _, provider, invocation = _governed_runtime(receipt_store=_CandidateReceiptStore())
    _candidate_group_runtime(management)
    first = _run_invocation(invocation, async_execution=False)
    second = _run_invocation(invocation, async_execution=False)
    assert first.context.selected_model == second.context.selected_model == IDENTITY
    assert first.receipt.candidate_selection_evidence == second.receipt.candidate_selection_evidence
    assert provider.calls == 2


def test_candidate_group_chooses_lowest_priority_among_eligible_candidates() -> None:
    management, _, _, _, provider, invocation = _governed_runtime(receipt_store=_CandidateReceiptStore())
    model = management.model_profile(IDENTITY.model_profile_id)
    assert model is not None
    first = IDENTITY.model_copy(update={"model_profile_id": "model-priority-first", "model_id": "priority-first"})
    first_model = model.model_copy(
        update={
            "model_profile_id": first.model_profile_id,
            "remote_model_id": first.model_id,
            "capability_sources": tuple(
                evidence.model_copy(update={"model_profile_id": first.model_profile_id, "model_id": first.model_id})
                for evidence in model.capability_sources
            ),
        }
    )
    management.add_model(first_model)
    management.create_allowed_model_policy(scope=PolicyScope.GLOBAL, project_id=None, stage_id=None, identity=first)
    management.add_candidate_group(
        CandidateGroup(
            group_id="priority-group",
            stage=EngineeringStage.FIRMWARE_DEVELOPMENT,
            candidates=(CandidateSpec(identity=IDENTITY, priority=20), CandidateSpec(identity=first, priority=10)),
            revision=1,
        )
    )
    management.set_stage_model_policy(
        StageModelPolicy(
            id="priority-policy",
            project_id="project-1",
            stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT,
            selection_mode=SelectionMode.CANDIDATE_GROUP,
            candidate_group_id="priority-group",
            candidate_group_revision=1,
            created_at=_RUNTIME_NOW,
            updated_at=_RUNTIME_NOW,
        )
    )

    result = _run_invocation(invocation, async_execution=False)

    assert result.status is InvocationStatus.SUCCESS
    assert result.context.selected_model == first
    assert result.receipt.candidate_selection_evidence.selected_candidate == first
    assert result.receipt.candidate_selection_evidence.rejected_candidates == ()
    assert provider.calls == 1


@pytest.mark.parametrize(
    ("change", "expected_reason"),
    [
        ("disabled", "MODEL_DISABLED"),
        ("unverified", "MODEL_VERIFICATION_GATE_FAILED"),
        ("provenance", "MODEL_PROVENANCE_GATE_FAILED"),
        ("capability", "MODEL_CAPABILITY_MISMATCH"),
        ("credential", "MODEL_CREDENTIAL_GATE_FAILED"),
        ("endpoint", "MODEL_ENDPOINT_MISMATCH"),
        ("quota", "MODEL_QUOTA_GATE_FAILED"),
    ],
)
def test_candidate_group_rejects_ineligible_candidates_before_provider(change: str, expected_reason: str) -> None:
    management, snapshots, revisions, _, provider, invocation = _governed_runtime(receipt_store=_CandidateReceiptStore())
    _candidate_group_runtime(management)
    model = management.model_profile(IDENTITY.model_profile_id)
    assert model is not None
    if change == "disabled":
        management.models.upsert(model.model_copy(update={"enabled": False}))
    elif change == "unverified":
        management.models.upsert(model.model_copy(update={"verification_status": VerificationStatus.UNVERIFIED}))
    elif change == "provenance":
        management.models.upsert(model.model_copy(update={"record_provenance": RecordProvenance.UNKNOWN}))
    elif change == "capability":
        management.models.upsert(model.model_copy(update={"capabilities": (ModelCapability.TEXT,)}))
    elif change == "credential":
        revisions["credential"] = "credential-revision-stale"
    elif change == "endpoint":
        revisions["endpoint"] = "endpoint-revision-stale"
    elif change == "quota":
        snapshots[0] = snapshots[0].model_copy(update={"quota_remaining": 0})

    result = _run_invocation(invocation, async_execution=False)

    assert result.error_code == "MODEL_SELECTION_FAILED"
    assert result.provider_request_count == provider.calls == 0
    assert result.receipt is not None
    assert result.receipt.candidate_selection_evidence is not None
    assert result.receipt.candidate_selection_evidence.rejected_candidates[-1].rejection_reason == expected_reason


def test_candidate_group_receipt_persists_with_evidence_and_fixed_receipt_remains_readable(tmp_path) -> None:
    store = SQLiteModelGatewayStore(tmp_path / "gateway.sqlite3")
    management, _, _, _, _, invocation = _governed_runtime(receipt_store=store)
    fixed = _run_invocation(invocation, async_execution=False)
    assert fixed.receipt is not None
    assert fixed.receipt.candidate_selection_evidence is None
    assert store.get_receipt(fixed.receipt.fingerprint) == fixed.receipt

    _candidate_group_runtime(management)
    second_model_request = ModelRequest(**{
        **_request().model_dump(mode="python", exclude={"fingerprint"}),
        "request_id": "request-2",
    })
    selected = invocation.invoke(ModelInvocationRequest.from_model_request(
        second_model_request, stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT,
    ))
    assert selected.receipt is not None
    assert selected.receipt.candidate_selection_evidence is not None
    assert store.get_receipt(selected.receipt.fingerprint) == selected.receipt


@pytest.mark.parametrize("async_execution", [False, True], ids=["invoke", "ainvoke"])
@pytest.mark.parametrize(
    ("eligibility_change", "expected_error"),
    [
        ("disabled", "MODEL_DISABLED"),
        ("unverified", "MODEL_VERIFICATION_GATE_FAILED"),
        ("unknown_provenance", "MODEL_PROVENANCE_GATE_FAILED"),
        ("official_catalog", "MODEL_PROVENANCE_GATE_FAILED"),
        ("credential_revision", "MODEL_CREDENTIAL_GATE_FAILED"),
        ("endpoint_revision", "MODEL_ENDPOINT_MISMATCH"),
    ],
)
def test_fixed_invocation_revalidates_runtime_eligibility_before_provider_call(
    async_execution: bool, eligibility_change: str, expected_error: str
) -> None:
    management, _, current_revisions, selection, provider, invocation = _governed_runtime()

    def change_runtime_state() -> None:
        if eligibility_change == "disabled":
            model = management.model_profile(IDENTITY.model_profile_id)
            management.models.upsert(model.model_copy(update={"enabled": False}))
        elif eligibility_change == "unverified":
            model = management.model_profile(IDENTITY.model_profile_id)
            management.models.upsert(
                model.model_copy(update={"verification_status": VerificationStatus.UNVERIFIED})
            )
        elif eligibility_change == "unknown_provenance":
            model = management.model_profile(IDENTITY.model_profile_id)
            management.models.upsert(
                model.model_copy(update={"record_provenance": RecordProvenance.UNKNOWN})
            )
        elif eligibility_change == "official_catalog":
            model = management.model_profile(IDENTITY.model_profile_id)
            management.models.upsert(
                model.model_copy(update={"record_provenance": RecordProvenance.OFFICIAL_CATALOG})
            )
        elif eligibility_change == "credential_revision":
            current_revisions["credential"] = "credential-revision-2"
        elif eligibility_change == "endpoint_revision":
            current_revisions["endpoint"] = "endpoint-revision-2"
        else:  # pragma: no cover - the parameter list is exhaustive
            raise AssertionError(f"unsupported eligibility change: {eligibility_change}")

    selection.after_selection = change_runtime_state
    result = _run_invocation(invocation, async_execution=async_execution)

    assert selection.selection_calls == 1
    assert selection.post_selection_mutations == 1
    assert result.status is ModelInvocationStatus.REJECTED
    assert result.error_code == expected_error
    assert result.provider_request_count == 0
    assert provider.calls == 0


@pytest.mark.parametrize("async_execution", [False, True], ids=["invoke", "ainvoke"])
def test_fixed_invocation_with_valid_runtime_evidence_calls_provider_once(
    async_execution: bool,
) -> None:
    _, _, _, selection, provider, invocation = _governed_runtime()

    result = _run_invocation(invocation, async_execution=async_execution)

    assert selection.selection_calls == 1
    assert selection.post_selection_mutations == 0
    assert result.status is ModelInvocationStatus.SUCCESS
    assert result.provider_request_count == 0
    assert provider.calls == 1


@pytest.mark.parametrize("async_execution", [False, True], ids=["invoke", "ainvoke"])
def test_invocation_accepts_current_available_quota_evidence(
    async_execution: bool,
) -> None:
    _, quota_snapshots, _, selection, provider, invocation = _governed_runtime()

    result = _run_invocation(invocation, async_execution=async_execution)

    assert quota_snapshots[0].provider_id == IDENTITY.provider_id
    assert quota_snapshots[0].endpoint_id == IDENTITY.endpoint_id
    assert quota_snapshots[0].credential_ref == IDENTITY.credential_ref
    assert quota_snapshots[0].credential_revision == _RUNTIME_CREDENTIAL_REVISION
    assert quota_snapshots[0].status is UsageSnapshotStatus.AVAILABLE
    assert quota_snapshots[0].quota_remaining > 0
    assert _RUNTIME_NOW - quota_snapshots[0].updated_at < timedelta(seconds=300)
    assert selection.selection_calls == 1
    assert result.status is ModelInvocationStatus.SUCCESS
    assert result.provider_request_count == 0
    assert provider.calls == 1


@pytest.mark.parametrize("async_execution", [False, True], ids=["invoke", "ainvoke"])
@pytest.mark.parametrize(
    "quota_case",
    [
        "stale",
        "unknown",
        "expired",
        "credential_mismatch",
        "endpoint_mismatch",
        "revision_mismatch",
        "conflicting_evidence",
    ],
)
def test_invocation_rejects_invalid_quota_evidence_before_provider_call(
    async_execution: bool, quota_case: str
) -> None:
    _, quota_snapshots, _, selection, provider, invocation = _governed_runtime()

    def change_quota_evidence() -> None:
        current = _runtime_quota_snapshot()
        if quota_case == "stale":
            replacement = current.model_copy(update={"status": UsageSnapshotStatus.STALE})
            quota_snapshots[:] = [replacement]
        elif quota_case == "unknown":
            replacement = current.model_copy(update={"status": UsageSnapshotStatus.UNKNOWN})
            quota_snapshots[:] = [replacement]
        elif quota_case == "expired":
            replacement = current.model_copy(
                update={"updated_at": _RUNTIME_NOW - timedelta(seconds=301)}
            )
            quota_snapshots[:] = [replacement]
        elif quota_case == "credential_mismatch":
            replacement = current.model_copy(update={"credential_ref": "credential:other"})
            quota_snapshots[:] = [replacement]
        elif quota_case == "endpoint_mismatch":
            replacement = current.model_copy(update={"endpoint_id": "endpoint-other"})
            quota_snapshots[:] = [replacement]
        elif quota_case == "revision_mismatch":
            replacement = current.model_copy(
                update={"credential_revision": "credential-revision-old"}
            )
            quota_snapshots[:] = [replacement]
        elif quota_case == "conflicting_evidence":
            conflict = current.model_copy(update={"status": UsageSnapshotStatus.STALE})
            quota_snapshots[:] = [current, conflict]
        else:  # pragma: no cover - the parameter list is exhaustive
            raise AssertionError(f"unsupported quota case: {quota_case}")

    selection.after_selection = change_quota_evidence
    result = _run_invocation(invocation, async_execution=async_execution)

    assert selection.selection_calls == 1
    assert selection.post_selection_mutations == 1
    assert result.status is ModelInvocationStatus.REJECTED
    assert result.error_code == "MODEL_QUOTA_GATE_FAILED"
    assert result.provider_request_count == 0
    assert provider.calls == 0


def test_reloadable_binding_bound_path_bypasses_legacy_router():
    from embedded_copilot.production_release.provider import ReloadableModelGateway

    provider = _Provider()
    canonical = ModelGatewayService(providers={"relay": provider, "endpoint-relay": provider})
    binding = ReloadableModelGateway()
    binding.install_routed(
        canonical,
        ModelPolicy(primary_provider="relay", model_name="firmware-model"),
        management=object(),
        available_credential_refs=lambda: (IDENTITY.credential_ref,),
    )
    response = binding.generate_bound(IDENTITY, _request())
    assert response.provider_id == IDENTITY.provider_id
    assert provider.calls == 1


@pytest.mark.parametrize("async_execution", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("failure", [False, True], ids=["success", "provider-failure"])
def test_receipt_provenance_uses_resolved_transport_snapshot(async_execution: bool, failure: bool, tmp_path) -> None:
    from embedded_copilot.model_gateway.transport import ResolvedTransportEntry
    from embedded_copilot.production_release.provider import ReloadableModelGateway

    class _Transport(_Provider):
        def generate(self, request):
            if failure:
                self.calls += 1
                error = ModelGatewayProviderFailure("PROVIDER_TRANSPORT_FAILED")
                error.connection_id = "conn-provider-claim"
                raise error
            return super().generate(request)

        async def agenerate(self, request):
            return self.generate(request)

    provider = _Transport()
    canonical = ModelGatewayService(providers={"relay": provider, "endpoint-relay": provider})
    binding = ReloadableModelGateway()
    binding.install_transport(canonical, (
        ResolvedTransportEntry(
            provider_id=IDENTITY.provider_id,
            endpoint_id=IDENTITY.endpoint_id,
            credential_ref=IDENTITY.credential_ref,
            adapter=provider,
            connection_id="conn-at-execution",
        ),
    ))
    database_path = tmp_path / "gateway.sqlite3"
    service = ModelInvocationService(
        selection_resolver=_Selection(ModelSelectionResult(
            selected_model=IDENTITY, selection_reason="STAGE_FIXED_MODEL",
        ), connection_id="conn-at-execution"), gateway=binding, receipt_store=SQLiteModelGatewayStore(database_path),
    )
    request = ModelInvocationRequest.from_model_request(
        _request(), stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT,
    )
    result = asyncio.run(service.ainvoke(request)) if async_execution else service.invoke(request)
    assert result.status is (InvocationStatus.FAILED if failure else InvocationStatus.SUCCESS)
    assert result.receipt is not None
    assert result.receipt.schema_version == 5
    assert result.receipt.connection_id == "conn-at-execution"
    assert result.provider_request_count == 0 and provider.calls == 1
    assert ModelInvocationReceipt.model_validate(result.receipt.model_dump(mode="python")) == result.receipt
    assert SQLiteModelGatewayStore(database_path).get_receipt(result.receipt.fingerprint) == result.receipt


def test_receipt_does_not_infer_connection_before_transport_resolution() -> None:
    from embedded_copilot.model_gateway.transport import ResolvedTransportEntry
    from embedded_copilot.production_release.provider import ReloadableModelGateway

    provider = _Provider()
    canonical = ModelGatewayService(providers={"relay": provider, "endpoint-relay": provider})
    binding = ReloadableModelGateway()
    binding.install_transport(canonical, (
        ResolvedTransportEntry(
            provider_id=IDENTITY.provider_id,
            endpoint_id="endpoint-other",
            credential_ref=IDENTITY.credential_ref,
            adapter=provider,
            connection_id="conn-unrelated",
        ),
    ))
    service = ModelInvocationService(
        selection_resolver=_Selection(ModelSelectionResult(
            selected_model=IDENTITY, selection_reason="STAGE_FIXED_MODEL",
        )), gateway=binding, receipt_store=[],
    )
    result = service.invoke(ModelInvocationRequest.from_model_request(
        _request(), stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT,
    ))
    assert result.receipt is not None
    assert result.receipt.connection_id is None
    assert result.receipt.schema_version == 5
    assert result.provider_request_count == provider.calls == 0


@pytest.mark.parametrize("binding_mode", ["entry_without_id", "legacy_without_entry"])
def test_receipt_ignores_provider_claim_without_resolved_connection(binding_mode: str) -> None:
    from embedded_copilot.model_gateway.transport import ResolvedTransportEntry
    from embedded_copilot.production_release.provider import ReloadableModelGateway

    class _ClaimingProvider(_Provider):
        def _generate_with_metadata(self, request):
            return ProviderExecutionResult(
                response=self.generate(request),
                metadata=ProviderExecutionMetadata(connection_id="conn-provider-claim"),
            )

    provider = _ClaimingProvider()
    canonical = ModelGatewayService(providers={"relay": provider, "endpoint-relay": provider})
    binding = ReloadableModelGateway()
    if binding_mode == "entry_without_id":
        binding.install_transport(canonical, (
            ResolvedTransportEntry(
                provider_id=IDENTITY.provider_id,
                endpoint_id=IDENTITY.endpoint_id,
                credential_ref=IDENTITY.credential_ref,
                adapter=provider,
            ),
        ))
    else:
        binding.install(canonical, ModelPolicy(primary_provider="relay", model_name="firmware-model"))
    service = ModelInvocationService(
        selection_resolver=_Selection(ModelSelectionResult(
            selected_model=IDENTITY, selection_reason="STAGE_FIXED_MODEL",
        )), gateway=binding, receipt_store=[],
    )
    result = service.invoke(ModelInvocationRequest.from_model_request(
        _request(), stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT,
    ))
    assert result.status is InvocationStatus.SUCCESS
    assert result.receipt is not None
    assert result.receipt.connection_id is None
    assert result.receipt.schema_version == 5


class _FailingGateway(_Gateway):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    def generate_bound(self, identity, request):
        self.calls += 1
        raise self.error

    async def agenerate_bound(self, identity, request):
        return self.generate_bound(identity, request)


class _RawGateway(_Gateway):
    def generate_bound(self, identity, request):
        self.calls += 1
        return {"unexpected": "payload"}

    async def agenerate_bound(self, identity, request):
        return self.generate_bound(identity, request)


def test_provider_failure_maps_to_stable_error_and_metadata_status():
    selection = _Selection(
        ModelSelectionResult(selected_model=IDENTITY, selection_reason="STAGE_FIXED_MODEL")
    )
    receipts: list[ModelInvocationReceipt] = []
    service = ModelInvocationService(
        selection_resolver=selection,
        gateway=_FailingGateway(
            ModelGatewayProviderFailure(
                "PROVIDER_HTTP_ERROR",
                provider_response_present=True,
                safe_http_status=503,
            )
        ),
        receipt_store=receipts,
    )

    result = service.invoke(
        ModelInvocationRequest.from_model_request(
            _request(), stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT
        )
    )

    assert result.status is ModelInvocationStatus.FAILED
    assert result.error_code == "MODEL_PROVIDER_REQUEST_FAILED"
    assert result.provider_request_count == 0
    assert result.receipt is not None
    assert result.receipt.http_status == 503
    assert result.receipt.gateway_call_count == 1
    assert result.receipt.gateway_response_observed is False
    assert result.receipt.transport_send_count is None
    assert result.receipt.provider_request_count == 0


def test_missing_adapter_fails_without_provider_request():
    selection = _Selection(
        ModelSelectionResult(selected_model=IDENTITY, selection_reason="STAGE_FIXED_MODEL")
    )
    service = ModelInvocationService(
        selection_resolver=selection,
        gateway=_FailingGateway(
            ModelGatewayRejected(reason_code="MODEL_PROTOCOL_ADAPTER_NOT_FOUND")
        ),
    )

    result = service.invoke(
        ModelInvocationRequest.from_model_request(
            _request(), stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT
        )
    )

    assert result.status is ModelInvocationStatus.FAILED
    assert result.error_code == "MODEL_PROTOCOL_ADAPTER_NOT_FOUND"
    assert result.provider_request_count == 0


def test_receipt_persistence_failure_cannot_claim_success():
    class _BrokenStore:
        def append_receipt(self, _receipt):
            raise OSError("storage unavailable")

    selection = _Selection(
        ModelSelectionResult(selected_model=IDENTITY, selection_reason="STAGE_FIXED_MODEL")
    )
    service = ModelInvocationService(
        selection_resolver=selection,
        gateway=_Gateway(),
        receipt_store=_BrokenStore(),
    )

    result = service.invoke(
        ModelInvocationRequest.from_model_request(
            _request(), stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT
        )
    )

    assert result.status is ModelInvocationStatus.FAILED
    assert result.error_code == "MODEL_GATEWAY_EXECUTION_FAILED"
    assert result.detail == "RECEIPT_PERSISTENCE_FAILED"


def test_provider_reported_usage_is_persisted_only_when_present() -> None:
    class _UsageGateway(_Gateway):
        def generate_bound(self, identity, request):
            self.calls += 1
            return ModelResponse(
                content="EMBRIX_RUNTIME_WIRING_OK",
                model_name=identity.model_id,
                provider=identity.provider_id,
                provider_id=identity.provider_id,
                endpoint_id=identity.endpoint_id,
                credential_ref=identity.credential_ref,
                request_id=request.request_id,
                provenance=request.engineering_context.provenance,
                usage=ModelUsage(input_tokens=3, output_tokens=2, total_tokens=5, usage_source="PROVIDER"),
            )

    selection = _Selection(ModelSelectionResult(selected_model=IDENTITY, selection_reason="STAGE_FIXED_MODEL"))
    receipts: list[ModelInvocationReceipt] = []
    service = ModelInvocationService(selection_resolver=selection, gateway=_UsageGateway(), receipt_store=receipts)
    result = service.invoke(ModelInvocationRequest.from_model_request(
        _request(), stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT
    ))

    assert result.status is InvocationStatus.SUCCESS
    assert result.receipt is not None
    assert result.receipt.schema_version == 5
    assert result.receipt.usage == ModelUsage(input_tokens=3, output_tokens=2, total_tokens=5, usage_source="PROVIDER")
    assert receipts == [result.receipt]


@pytest.mark.parametrize("async_execution", [False, True])
def test_untrusted_request_override_never_reaches_selection_or_provider(async_execution: bool) -> None:
    selection = _Selection(ModelSelectionResult(selected_model=IDENTITY, selection_reason="REQUEST_OVERRIDE"))
    gateway = _Gateway()
    receipts: list[ModelInvocationReceipt] = []
    service = ModelInvocationService(selection_resolver=selection, gateway=gateway, receipt_store=receipts)
    request = ModelInvocationRequest.from_model_request(
        _request(), stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT, request_override=IDENTITY
    )

    result = asyncio.run(service.ainvoke(request)) if async_execution else service.invoke(request)

    assert result.status is InvocationStatus.REJECTED
    assert result.error_code == "REQUEST_OVERRIDE_UNAUTHORIZED"
    assert result.provider_request_count == 0
    assert selection.calls == 0
    assert gateway.calls == 0
    assert len(receipts) == 1
    assert receipts[0].result is InvocationStatus.REJECTED


def test_invalid_provider_response_maps_to_stable_response_error():
    selection = _Selection(
        ModelSelectionResult(selected_model=IDENTITY, selection_reason="STAGE_FIXED_MODEL")
    )
    service = ModelInvocationService(
        selection_resolver=selection,
        gateway=_RawGateway(),
    )
    result = service.invoke(
        ModelInvocationRequest.from_model_request(
            _request(), stage_id=EngineeringStage.FIRMWARE_DEVELOPMENT
        )
    )
    assert result.status is ModelInvocationStatus.FAILED
    assert result.error_code == "MODEL_PROVIDER_RESPONSE_INVALID"
    assert result.provider_request_count == 0
