"""Read/write projections for model catalog and policy metadata."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from embedded_copilot.api.desktop_identity import (
    ProductionLocalSessionIdentityAdapter,
    TrustedDesktopIdentity,
    desktop_identity,
)
from embedded_copilot.model_gateway.adapters.protocol_http import (
    SUPPORTED_PROTOCOL_FAMILIES,
)
from embedded_copilot.model_gateway.governance import (
    AllowedModelPolicy,
    BoundModelIdentity,
    EngineeringStage,
    PolicyScope,
    ProjectDefaultModelPolicy,
    StageModelPolicy,
)
from embedded_copilot.model_gateway.management import (
    ConnectionProbePort,
    ModelGatewayManagementService,
)
from embedded_copilot.model_gateway.presets import (
    official_model_catalog,
    provider_presets,
    provider_template_presets,
)
from embedded_copilot.model_gateway.profiles import (
    CapabilityStatus,
    CredentialProfile,
    DiscoveryStatus,
    EndpointProfile,
    EndpointType,
    IdentityStatus,
    ModelAvailability,
    ModelCatalogSource,
    ModelProfile,
    ProviderConnection,
    ProviderConnectionStatus,
    ProviderProfile,
    RecordProvenance,
    VerificationStatus,
)
from embedded_copilot.model_gateway.registry import CapabilityProbeResult
from embedded_copilot.model_gateway.selection import (
    ModelSelectionError,
    ModelSelectionService,
)
from embedded_copilot.model_gateway_storage import SQLiteModelGatewayStore
from embedded_copilot.production_release.credentials import CredentialUnavailable
from embedded_copilot.production_release.model_discovery import ModelDiscoveryFailure
from embedded_copilot.production_release.provider_verification import (
    ConnectionTestRecord,
    ModelRuntimeHealth,
    ProviderVerificationReceipt,
    projection_revision,
)
from embedded_copilot.project_catalog import (
    ProjectCatalogForbidden,
    ProjectCatalogNotFound,
    ProjectCatalogRuntime,
    ProjectCatalogUnavailable,
)

from .model_gateway_models import (
    AccountUsageView,
    AllowedModelPolicyInput,
    AllowedModelPolicyView,
    CredentialReferenceInput,
    CredentialView,
    EndpointView,
    EngineeringStageView,
    GlobalPolicyInput,
    GovernanceModelView,
    IdentityIntegrityIssueView,
    InvocationAttemptView,
    InvocationReceiptPageView,
    InvocationReceiptView,
    ModelMetricsView,
    ModelRuntimeHealthView,
    ModelRuntimeIdentityView,
    ModelView,
    OfficialCatalogModelView,
    ProjectDefaultModelPolicyInput,
    ProjectDefaultModelPolicyView,
    ProjectPolicyInput,
    ProviderConnectionCreateInput,
    ProviderConnectionCredentialInput,
    ProviderConnectionCredentialView,
    ProviderConnectionDeleteView,
    ProviderConnectionModelTestInput,
    ProviderConnectionPatchInput,
    ProviderConnectionRefreshView,
    ProviderConnectionTestResultView,
    ProviderConnectionTestView,
    ProviderConnectionUsageView,
    ProviderConnectionVerificationSummaryView,
    ProviderConnectionView,
    ProviderTemplateView,
    ProviderView,
    RequestOverrideInput,
    StageModelPolicyInput,
    StageModelPolicyView,
    TaskAssignmentInput,
    TaskProfileView,
    UsageView,
)

router = APIRouter(prefix="/api/v1/model-gateway", tags=["model-gateway"])
_IDENTITY_DEPENDENCY = Depends(desktop_identity)


def _receipt_store(request: Request) -> SQLiteModelGatewayStore:
    store = _service(request).receipt_store
    if not isinstance(store, SQLiteModelGatewayStore):
        raise HTTPException(status_code=503, detail="INVOCATION_RECEIPTS_UNAVAILABLE")
    return store


def _authorize_receipt_project(
    request: Request, identity: TrustedDesktopIdentity, project_id: str
) -> None:
    catalog = getattr(request.app.state, "project_catalog_runtime", None)
    if not isinstance(catalog, ProjectCatalogRuntime):
        raise HTTPException(status_code=503, detail="PROJECT_CATALOG_UNAVAILABLE")
    try:
        catalog.get_project_for_requester(
            project_id, tenant_id=identity.tenant_id, requester=identity.requester,
        )
    except ProjectCatalogNotFound:
        raise HTTPException(status_code=404, detail="PROJECT_NOT_FOUND") from None
    except ProjectCatalogForbidden:
        raise HTTPException(status_code=403, detail="PROJECT_ACCESS_DENIED") from None
    except ProjectCatalogUnavailable:
        raise HTTPException(status_code=503, detail="PROJECT_CATALOG_UNAVAILABLE") from None


def _mutation_identity(
    request: Request,
    identity: TrustedDesktopIdentity = _IDENTITY_DEPENDENCY,
) -> TrustedDesktopIdentity:
    provider = getattr(request.app.state, "desktop_identity_provider", None)
    if (
        not isinstance(provider, ProductionLocalSessionIdentityAdapter)
        or identity.authentication != "LOCAL_BOOTSTRAP_SESSION"
    ):
        raise HTTPException(status_code=401, detail="DESKTOP_SESSION_REQUIRED")
    request.state.model_gateway_identity = identity
    return identity


def _authorize_mutation_project(request: Request, project_id: str) -> None:
    identity = getattr(request.state, "model_gateway_identity", None)
    if not isinstance(identity, TrustedDesktopIdentity):
        raise HTTPException(status_code=401, detail="DESKTOP_SESSION_REQUIRED")
    _authorize_receipt_project(request, identity, project_id)


mutation_router = APIRouter(dependencies=[Depends(_mutation_identity)])


@router.get(
    "/invocations/projects/{project_id}/receipts",
    response_model=InvocationReceiptPageView,
)
def list_invocation_receipts(
    project_id: str,
    request: Request,
    request_id: str | None = Query(default=None, min_length=1, max_length=128),
    chat_request_id: str | None = Query(default=None, min_length=1, max_length=128),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity: TrustedDesktopIdentity = _IDENTITY_DEPENDENCY,
) -> InvocationReceiptPageView:
    _authorize_receipt_project(request, identity, project_id)
    if request_id is not None and chat_request_id is not None:
        raise HTTPException(status_code=422, detail="RECEIPT_REQUEST_FILTER_CONFLICT")
    effective_request_id = request_id if chat_request_id is None else "request-" + chat_request_id
    store = _receipt_store(request)
    try:
        items = store.list_receipts_for_project(
            project_id, request_id=effective_request_id, limit=limit, offset=offset,
        )
        total = store.count_receipts_for_project(project_id, request_id=effective_request_id)
    except (sqlite3.Error, ValueError):
        raise HTTPException(status_code=503, detail="INVOCATION_RECEIPTS_UNAVAILABLE") from None
    return InvocationReceiptPageView(
        items=tuple(InvocationReceiptView.from_domain(item) for item in items),
        total=total, limit=limit, offset=offset,
    )


@router.get(
    "/invocations/projects/{project_id}/receipts/{fingerprint}",
    response_model=InvocationReceiptView,
)
def get_invocation_receipt(
    project_id: str,
    fingerprint: str,
    request: Request,
    identity: TrustedDesktopIdentity = _IDENTITY_DEPENDENCY,
) -> InvocationReceiptView:
    _authorize_receipt_project(request, identity, project_id)
    try:
        receipt = _receipt_store(request).get_receipt_for_project(project_id, fingerprint)
    except (sqlite3.Error, ValueError):
        raise HTTPException(status_code=503, detail="INVOCATION_RECEIPTS_UNAVAILABLE") from None
    if receipt is None:
        raise HTTPException(status_code=404, detail="INVOCATION_RECEIPT_NOT_FOUND")
    return InvocationReceiptView.from_domain(receipt)


@router.get(
    "/invocations/projects/{project_id}/attempts/{request_id}",
    response_model=InvocationAttemptView,
)
def get_invocation_attempt(
    project_id: str,
    request_id: str,
    request: Request,
    identity: TrustedDesktopIdentity = _IDENTITY_DEPENDENCY,
) -> InvocationAttemptView:
    _authorize_receipt_project(request, identity, project_id)
    try:
        attempt = _receipt_store(request).get_attempt_for_project(project_id, request_id)
    except (sqlite3.Error, ValueError):
        raise HTTPException(status_code=503, detail="INVOCATION_ATTEMPTS_UNAVAILABLE") from None
    if attempt is None:
        raise HTTPException(status_code=404, detail="INVOCATION_ATTEMPT_NOT_FOUND")
    return InvocationAttemptView.from_domain(attempt)


def _service(request: Request) -> ModelGatewayManagementService:
    value = getattr(request.app.state, "model_gateway_management_service", None)
    if not isinstance(value, ModelGatewayManagementService):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="MODEL_GATEWAY_UNAVAILABLE")
    return value


def _persist(service: ModelGatewayManagementService) -> None:
    if service.persistent:
        service.save()


def _provider_connection_service(request: Request) -> object:
    value = getattr(request.app.state, "provider_configuration_service", None)
    if value is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PROVIDER_CONNECTION_SERVICE_UNAVAILABLE",
        )
    return value


def _connection_http_error(error: BaseException) -> HTTPException:
    code = str(error).strip() or "PROVIDER_CONNECTION_INVALID"
    known_codes = {
        "PROVIDER_CONNECTION_NOT_FOUND",
        "PROVIDER_NOT_CONFIGURED",
        "PROVIDER_CONNECTION_NOT_CONFIGURED",
        "PROVIDER_CONNECTION_DELETED",
        "PROVIDER_CONNECTION_AMBIGUOUS",
        "PROVIDER_CONNECTION_DISABLED",
        "PROVIDER_CONNECTION_QUARANTINED",
        "PROVIDER_CONNECTION_ENDPOINT_MISMATCH",
        "PROVIDER_CONNECTION_PROVIDER_MISMATCH",
        "PROVIDER_MODEL_MISMATCH",
        "PROVIDER_CREDENTIAL_REF_MISMATCH",
        "PROVIDER_PROTOCOL_MISMATCH",
        "ENDPOINT_ALREADY_REGISTERED",
        "PROVIDER_CONNECTION_MANAGEMENT_UNAVAILABLE",
        "PROVIDER_CONNECTION_ENDPOINT_NOT_FOUND",
        "PROVIDER_CREDENTIAL_STORE_UNAVAILABLE",
        "PROVIDER_PROTOCOL_REQUIRED",
        "PROVIDER_PROTOCOL_UNSUPPORTED",
    }
    if code not in known_codes:
        code = "PROVIDER_CONNECTION_INVALID"
    if code in {"PROVIDER_CONNECTION_NOT_FOUND", "PROVIDER_NOT_CONFIGURED", "PROVIDER_CONNECTION_NOT_CONFIGURED"}:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=code)
    if code == "PROVIDER_CONNECTION_DELETED":
        return HTTPException(status_code=status.HTTP_410_GONE, detail=code)
    if code in {
        "PROVIDER_CONNECTION_AMBIGUOUS",
        "PROVIDER_CONNECTION_DISABLED",
        "PROVIDER_CONNECTION_QUARANTINED",
        "PROVIDER_CONNECTION_ENDPOINT_MISMATCH",
        "PROVIDER_CONNECTION_PROVIDER_MISMATCH",
        "PROVIDER_MODEL_MISMATCH",
        "PROVIDER_CREDENTIAL_REF_MISMATCH",
        "PROVIDER_PROTOCOL_MISMATCH",
        "ENDPOINT_ALREADY_REGISTERED",
    }:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=code)
    if code in {
        "PROVIDER_CONNECTION_MANAGEMENT_UNAVAILABLE",
        "PROVIDER_CONNECTION_ENDPOINT_NOT_FOUND",
        "PROVIDER_CREDENTIAL_STORE_UNAVAILABLE",
    }:
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=code)
    if code.endswith(("_UNAVAILABLE", "_NOT_REGISTERED")):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=code)
    return HTTPException(status_code=422, detail=code)


def _connection_or_404(
    service: ModelGatewayManagementService, connection_id: str
) -> ProviderConnection:
    value = service.connection(connection_id)
    if value is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PROVIDER_CONNECTION_NOT_FOUND")
    return value


def _operational_connection(
    service: ModelGatewayManagementService, connection_id: str
) -> ProviderConnection:
    connection = _connection_or_404(service, connection_id)
    if connection.status is ProviderConnectionStatus.DELETED:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="PROVIDER_CONNECTION_DELETED")
    if connection.status is ProviderConnectionStatus.DISABLED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="PROVIDER_CONNECTION_DISABLED")
    if connection.status is ProviderConnectionStatus.QUARANTINED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="PROVIDER_CONNECTION_QUARANTINED")
    return connection


def _connection_health_projection(
    *,
    projection: object,
    endpoint: EndpointProfile,
    models: tuple[ModelProfile, ...],
    latest: ProviderVerificationReceipt | None,
) -> tuple[str, str | None, str, datetime | None, str, str, str, str, datetime | None, str]:
    """Project current governance facts without creating a second state store."""

    credential_status = str(getattr(projection, "credential_status", "UNKNOWN"))
    credential_freshness = "CURRENT" if credential_status == "CONFIGURED" else "UNKNOWN"
    discovery_state = str(getattr(projection, "discovery_state", "UNKNOWN"))
    discovery_freshness = {
        "READY": "CURRENT",
        "STALE_WITH_CACHE": "STALE",
        "FAILED": "UNAVAILABLE",
    }.get(discovery_state, "UNKNOWN")
    discovery_observed_at = max(
        (item.discovered_at for item in models if item.discovered_at is not None),
        default=None,
    )
    endpoint_status = str(endpoint.health_status or "UNKNOWN")
    endpoint_freshness = (
        "STALE" if endpoint_status == "STALE"
        else "UNKNOWN" if endpoint_status == "UNKNOWN"
        else "CURRENT"
    )
    projection_status = getattr(
        getattr(projection, "connection_status", None),
        "value",
        getattr(projection, "connection_status", "UNKNOWN"),
    )
    latest_error = (
        latest.normalized_error_code or latest.provider_error_code
        if latest is not None
        else None
    )
    block_reason = latest_error
    if credential_status in {"UNAVAILABLE", "UNKNOWN"}:
        block_reason = "ADAPTER_UNAVAILABLE"
    elif credential_status != "CONFIGURED" and endpoint.provider_id != "local":
        block_reason = "MISSING_CREDENTIAL"
    elif discovery_state == "FAILED":
        block_reason = "DISCOVERY_FAILED"
    elif discovery_state == "STALE_WITH_CACHE":
        block_reason = "DISCOVERY_STALE"
    elif projection_status == "PROVIDER_AUTH_FAILED":
        block_reason = "INVALID_CREDENTIAL"
    elif projection_status == "PROVIDER_NETWORK_UNAVAILABLE":
        block_reason = "ENDPOINT_UNAVAILABLE"
    elif projection_status == "PROVIDER_MODEL_UNAVAILABLE":
        block_reason = "MODEL_NOT_FOUND"
    elif projection_status == "PROVIDER_RATE_LIMITED":
        block_reason = "QUOTA_EXHAUSTED"
    elif endpoint_status == "STALE":
        block_reason = "ENDPOINT_STALE"
    elif endpoint_status == "UNKNOWN" and latest is None:
        block_reason = "ENDPOINT_HEALTH_UNKNOWN"
    elif not models and discovery_state == "READY":
        block_reason = "MODEL_NOT_FOUND"
    elif projection_status in {"PROVIDER_CONFIG_INVALID", "PROVIDER_CONNECTION_UNVERIFIED"}:
        block_reason = projection_status
    else:
        block_reason = None

    if credential_status != "CONFIGURED" and endpoint.provider_id != "local":
        health_status = "UNCONFIGURED"
    elif block_reason in {
        "ENDPOINT_UNAVAILABLE",
        "INVALID_CREDENTIAL",
        "MODEL_NOT_FOUND",
        "QUOTA_EXHAUSTED",
        "DISCOVERY_FAILED",
    }:
        health_status = "UNAVAILABLE"
    elif (
        block_reason is None
        and endpoint_freshness == "CURRENT"
        and discovery_freshness == "CURRENT"
        and models
    ):
        health_status = "HEALTHY"
    else:
        health_status = "PARTIAL"
    return (
        health_status,
        block_reason,
        endpoint_status,
        latest.completed_at if latest is not None else None,
        endpoint_freshness,
        credential_status,
        credential_freshness,
        discovery_state,
        discovery_observed_at,
        discovery_freshness,
    )


def _connection_view(
    request: Request,
    connection: ProviderConnection,
) -> ProviderConnectionView:
    service = _service(request)
    provider_service = _provider_connection_service(request)
    endpoint = service.endpoint_profile(connection.endpoint_id)
    provider = service.providers.get(connection.provider_id)
    if endpoint is None or provider is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="PROVIDER_CONNECTION_ENDPOINT_NOT_FOUND",
        )
    try:
        projection = provider_service.projection(connection_id=connection.connection_id)
        credential_revision = provider_service.credential_revision_for_connection(
            connection.connection_id
        )
        models = (
            provider_service.model_pool(connection_id=connection.connection_id)
            if connection.status is ProviderConnectionStatus.ACTIVE
            else ()
        )
        receipts = provider_service.recent_verifications(
            connection_id=connection.connection_id, limit=1000
        )
        provider_receipts = provider_service.recent_verifications(
            provider_id=connection.provider_id, limit=1000
        )
        latest = provider_service.latest_verification(
            connection_id=connection.connection_id
        )
        usage = service.read_account_usage(
            endpoint.endpoint_id,
            credential_ref=connection.credential_ref or endpoint.credential_ref,
            connection_id=connection.connection_id,
            credential_revision=credential_revision,
        )
    except (CredentialUnavailable, TypeError, ValueError, RuntimeError) as error:
        raise _connection_http_error(error) from None
    current = 1 if latest is not None else 0
    ambiguous_legacy = sum(
        1
        for receipt in provider_receipts
        if receipt.connection_id is None and receipt.endpoint_id == endpoint.endpoint_id
    )
    historical = max(len(receipts) - current, 0)
    latest_status = getattr(latest.verification_status, "value", latest.verification_status) if latest else None
    latest_at = latest.completed_at if latest is not None else None
    (
        health_status,
        block_reason,
        endpoint_status,
        endpoint_observed_at,
        endpoint_freshness,
        credential_status,
        credential_freshness,
        discovery_state,
        discovery_observed_at,
        discovery_freshness,
    ) = _connection_health_projection(
        projection=projection,
        endpoint=endpoint,
        models=models,
        latest=latest,
    )
    return ProviderConnectionView(
        connection_id=connection.connection_id,
        connection_name=connection.connection_name,
        legacy_source=connection.legacy_source,
        provider_id=connection.provider_id,
        provider_display_name=provider.display_name,
        status=connection.status,
        health_status=health_status,
        block_reason=block_reason,
        endpoint_status=endpoint_status,
        endpoint_observed_at=endpoint_observed_at,
        endpoint_freshness=endpoint_freshness,
        credential_status=credential_status,
        credential_freshness=credential_freshness,
        discovery_observed_at=discovery_observed_at,
        discovery_freshness=discovery_freshness,
        endpoint_id=endpoint.endpoint_id,
        endpoint_url=endpoint.base_url,
        protocol_family=getattr(endpoint.protocol_family, "value", endpoint.protocol_family),
        credential_ref=connection.credential_ref or endpoint.credential_ref,
        credential_configured=projection.credential_status == "CONFIGURED",
        credential_revision=credential_revision,
        created_at=connection.created_at,
        updated_at=connection.updated_at,
        deleted_at=connection.deleted_at,
        discovery_state=discovery_state,
        discovery_required=projection.discovery_required,
        current_model_count=len(models),
        verification=ProviderConnectionVerificationSummaryView(
            total=current + historical + ambiguous_legacy,
            current=current,
            historical=historical,
            ambiguous_legacy=ambiguous_legacy,
            latest_status=latest_status,
            latest_at=latest_at,
        ),
        usage=ProviderConnectionUsageView(
            status=usage.status.value,
            current_balance=usage.current_balance,
            last_known_balance=usage.last_known_balance,
            queried_at=(
                usage.updated_at
                if usage.status.value in {"AVAILABLE", "STALE", "AUTH_FAILED"}
                else None
            ),
        ),
    )


@mutation_router.get(
    "/provider-connections",
    response_model=tuple[ProviderConnectionView, ...],
)
def list_provider_connections(request: Request) -> tuple[ProviderConnectionView, ...]:
    service = _service(request)
    _provider_connection_service(request)
    return tuple(_connection_view(request, item) for item in service.list_connections())


@mutation_router.post(
    "/provider-connections",
    response_model=ProviderConnectionView,
    status_code=status.HTTP_201_CREATED,
)
def create_provider_connection(
    payload: ProviderConnectionCreateInput, request: Request
) -> ProviderConnectionView:
    provider_service = _provider_connection_service(request)
    try:
        connection = provider_service.create_connection(
            provider_id=payload.provider_id,
            connection_name=payload.connection_name,
            endpoint_url=payload.endpoint_url,
            endpoint_id=payload.endpoint_id,
            protocol_family=payload.protocol_family,
            credential_ref=payload.credential_ref,
        )
    except CredentialUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PROVIDER_CREDENTIAL_STORE_UNAVAILABLE",
        ) from None
    except (KeyError, TypeError, ValueError) as error:
        raise _connection_http_error(error) from None
    return _connection_view(request, connection)


@mutation_router.get(
    "/provider-connections/{connection_id}",
    response_model=ProviderConnectionView,
)
def get_provider_connection(connection_id: str, request: Request) -> ProviderConnectionView:
    connection = _connection_or_404(_service(request), connection_id)
    _provider_connection_service(request)
    return _connection_view(request, connection)


@mutation_router.patch(
    "/provider-connections/{connection_id}",
    response_model=ProviderConnectionView,
)
def patch_provider_connection(
    connection_id: str,
    payload: ProviderConnectionPatchInput,
    request: Request,
) -> ProviderConnectionView:
    _connection_or_404(_service(request), connection_id)
    provider_service = _provider_connection_service(request)
    try:
        provider_service.update_connection(
            connection_id,
            connection_name=payload.connection_name,
            endpoint_url=payload.endpoint_url,
            protocol_family=payload.protocol_family,
            enabled=payload.enabled,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise _connection_http_error(error) from None
    return _connection_view(request, _connection_or_404(_service(request), connection_id))


@mutation_router.delete(
    "/provider-connections/{connection_id}",
    response_model=ProviderConnectionDeleteView,
)
def delete_provider_connection(
    connection_id: str, request: Request
) -> ProviderConnectionDeleteView:
    _connection_or_404(_service(request), connection_id)
    provider_service = _provider_connection_service(request)
    try:
        provider_service.delete_connection(connection_id)
    except (CredentialUnavailable, KeyError, TypeError, ValueError) as error:
        raise _connection_http_error(error) from None
    connection = _connection_or_404(_service(request), connection_id)
    return ProviderConnectionDeleteView(
        connection_id=connection.connection_id,
        status=connection.status,
    )


@mutation_router.put(
    "/provider-connections/{connection_id}/credential",
    response_model=ProviderConnectionCredentialView,
)
def put_provider_connection_credential(
    connection_id: str,
    payload: ProviderConnectionCredentialInput,
    request: Request,
) -> ProviderConnectionCredentialView:
    connection = _operational_connection(_service(request), connection_id)
    provider_service = _provider_connection_service(request)
    try:
        projection = provider_service.save_credential(
            payload.secret, connection_id=connection.connection_id
        )
        credential_revision = provider_service.credential_revision_for_connection(connection_id)
    except CredentialUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PROVIDER_CREDENTIAL_STORE_UNAVAILABLE",
        ) from None
    except (KeyError, TypeError, ValueError) as error:
        raise _connection_http_error(error) from None
    return ProviderConnectionCredentialView(
        connection_id=connection_id,
        credential_ref=projection.credential_ref or connection.credential_ref,
        configured=projection.credential_status == "CONFIGURED",
        credential_revision=credential_revision,
    )


@mutation_router.post(
    "/provider-connections/{connection_id}/models/refresh",
    response_model=ProviderConnectionRefreshView,
)
def refresh_provider_connection_models(
    connection_id: str, request: Request
) -> ProviderConnectionRefreshView:
    connection = _operational_connection(_service(request), connection_id)
    provider_service = _provider_connection_service(request)
    try:
        result = provider_service.refresh_model_pool(connection_id=connection.connection_id)
    except (CredentialUnavailable, KeyError, TypeError, ValueError, RuntimeError) as error:
        raise _connection_http_error(error) from None
    return ProviderConnectionRefreshView(
        connection_id=connection.connection_id,
        provider_id=result.provider_id,
        endpoint_id=result.endpoint_id,
        status=result.status,
        raw_model_count=result.raw_model_count,
        normalized_model_count=len(result.models),
        models=tuple(ModelView.from_profile(item) for item in result.models),
        diagnostic_summary=(
            result.failure_evidence.as_api_detail()
            if result.failure_evidence is not None
            else None
        ),
        error_code=result.error_code,
    )


@mutation_router.post(
    "/provider-connections/{connection_id}/test-connection",
    response_model=ProviderConnectionTestView,
)
def test_provider_connection_canonical(
    connection_id: str,
    payload: ProviderConnectionModelTestInput,
    request: Request,
) -> ProviderConnectionTestView:
    service = _service(request)
    connection = _operational_connection(service, connection_id)
    endpoint = service.endpoint_profile(connection.endpoint_id)
    model = service.model_profile(payload.model_profile_id)
    if endpoint is None:
        raise HTTPException(status_code=409, detail="PROVIDER_CONNECTION_ENDPOINT_NOT_FOUND")
    if (
        model is None
        or model.provider_id != connection.provider_id
        or model.endpoint_id != connection.endpoint_id
        or model.remote_model_id != payload.model_id
    ):
        raise HTTPException(status_code=409, detail="PROVIDER_MODEL_MISMATCH")
    credential_ref = connection.credential_ref or endpoint.credential_ref
    if credential_ref is None:
        credential_ref = "credential:local"
    provider_service = _provider_connection_service(request)
    try:
        test_projection = provider_service.test_connection(
            draft={
                "connection_id": connection.connection_id,
                "provider_id": connection.provider_id,
                "endpoint_id": connection.endpoint_id,
                "credential_ref": credential_ref,
                "model_profile_id": payload.model_profile_id,
                "model_id": payload.model_id,
                "protocol_family": getattr(endpoint.protocol_family, "value", endpoint.protocol_family),
                "base_url": endpoint.base_url,
            }
        )
    except CredentialUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PROVIDER_CREDENTIAL_STORE_UNAVAILABLE",
        ) from None
    except (KeyError, TypeError, ValueError, RuntimeError) as error:
        raise _connection_http_error(error) from None
    test_status = test_projection.connection_status.value
    connection_view = _connection_view(request, connection)
    return ProviderConnectionTestView(
        **connection_view.model_dump(mode="python"),
        test_result=ProviderConnectionTestResultView(
            status=test_status,
            reason_code=(
                None if test_status == "AVAILABLE" else
                test_projection.connection_diagnostic.error_code
                if test_projection.connection_diagnostic is not None else test_status
            ),
            tested_at=test_projection.tested_at,
        ),
    )


@router.get(
    "/provider-connections/{connection_id}/verifications",
    response_model=tuple[ProviderVerificationReceipt, ...],
)
def list_provider_connection_verifications(
    connection_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
) -> tuple[ProviderVerificationReceipt, ...]:
    connection = _connection_or_404(_service(request), connection_id)
    provider_service = _provider_connection_service(request)
    try:
        return provider_service.recent_verifications(
            connection_id=connection.connection_id, limit=limit
        )
    except (KeyError, TypeError, ValueError, RuntimeError) as error:
        raise _connection_http_error(error) from None


@router.get(
    "/provider-connections/{connection_id}/test-history",
    response_model=tuple[ConnectionTestRecord, ...],
)
def list_provider_connection_test_history(
    connection_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
) -> tuple[ConnectionTestRecord, ...]:
    """Return immutable, secret-free test history projected from receipts."""

    connection = _connection_or_404(_service(request), connection_id)
    provider_service = _provider_connection_service(request)
    try:
        return provider_service.recent_connection_test_history(
            connection_id=connection.connection_id, limit=limit
        )
    except (KeyError, TypeError, ValueError, RuntimeError) as error:
        raise _connection_http_error(error) from None


@router.get(
    "/provider-connections/{connection_id}/runtime-health",
    response_model=tuple[ModelRuntimeHealthView, ...],
)
def read_provider_connection_runtime_health(
    connection_id: str, request: Request
) -> tuple[ModelRuntimeHealthView, ...]:
    service = _service(request)
    connection = _operational_connection(service, connection_id)
    endpoint = service.endpoint_profile(connection.endpoint_id)
    if endpoint is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="PROVIDER_CONNECTION_ENDPOINT_NOT_FOUND",
        )
    provider_service = _provider_connection_service(request)
    try:
        credential_revision = provider_service.credential_revision_for_connection(
            connection.connection_id
        )
        models = provider_service.model_pool(connection_id=connection.connection_id)
        receipts = provider_service.recent_verifications(
            connection_id=connection.connection_id, limit=1000
        )
    except (CredentialUnavailable, KeyError, TypeError, ValueError, RuntimeError) as error:
        raise _connection_http_error(error) from None

    credential_ref = connection.credential_ref or endpoint.credential_ref
    if credential_ref is None:
        credential_ref = "credential:local"
    endpoint_revision = projection_revision(
        {
            "endpoint_id": endpoint.endpoint_id,
            "provider_id": endpoint.provider_id,
            "protocol_family": getattr(
                endpoint.protocol_family, "value", endpoint.protocol_family
            ),
            "base_url": endpoint.base_url,
            "credential_ref": endpoint.credential_ref,
            "enabled": endpoint.enabled,
        }
    )
    projected: list[ModelRuntimeHealthView] = []
    for model in models:
        model_revision = projection_revision(
            {
                "model_profile_id": model.model_profile_id,
                "provider_id": model.provider_id,
                "endpoint_id": model.endpoint_id,
                "remote_model_id": model.remote_model_id,
            }
        )
        current = tuple(
            item
            for item in receipts
            if item.connection_id == connection.connection_id
            and item.provider_id == model.provider_id
            and item.endpoint_id == model.endpoint_id
            and item.credential_ref == credential_ref
            and item.model_id == model.remote_model_id
        )
        health = ModelRuntimeHealth.from_receipts(
            identity={
                "provider_id": model.provider_id,
                "endpoint_id": model.endpoint_id,
                "credential_ref": credential_ref,
                "model_profile_id": model.model_profile_id,
                "model_id": model.remote_model_id,
            },
            receipts=current,
            credential_revision=credential_revision or "UNKNOWN",
            endpoint_revision=endpoint_revision,
            model_revision=model_revision,
        )
        projected.append(
            ModelRuntimeHealthView(
                identity=ModelRuntimeIdentityView(**health.identity.model_dump()),
                endpoint_status=health.endpoint_status,
                last_success_at=health.last_success_at,
                last_failure_at=health.last_failure_at,
                error_rate=health.error_rate,
                latency_ms=health.latency_ms,
                quota_status=health.quota_status,
                observed_at=health.observed_at,
                sample_count=health.sample_count,
                evidence_freshness=health.evidence_freshness,
            )
        )
    return tuple(projected)


@mutation_router.get(
    "/provider-connections/{connection_id}/usage",
    response_model=AccountUsageView,
)
def read_provider_connection_usage(
    connection_id: str, request: Request
) -> AccountUsageView:
    service = _service(request)
    connection = _connection_or_404(service, connection_id)
    endpoint = service.endpoint_profile(connection.endpoint_id)
    if endpoint is None:
        raise HTTPException(status_code=409, detail="PROVIDER_CONNECTION_ENDPOINT_NOT_FOUND")
    provider_service = _provider_connection_service(request)
    try:
        revision = provider_service.credential_revision_for_connection(connection_id)
        snapshot = service.read_account_usage(
            endpoint.endpoint_id,
            credential_ref=connection.credential_ref or endpoint.credential_ref,
            connection_id=connection_id,
            credential_revision=revision,
        )
    except (KeyError, TypeError, ValueError, RuntimeError) as error:
        raise _connection_http_error(error) from None
    return AccountUsageView.from_snapshot(snapshot)


@mutation_router.post(
    "/provider-connections/{connection_id}/usage/refresh",
    response_model=AccountUsageView,
)
def refresh_provider_connection_usage(
    connection_id: str, request: Request
) -> AccountUsageView:
    service = _service(request)
    connection = _operational_connection(service, connection_id)
    endpoint = service.endpoint_profile(connection.endpoint_id)
    if endpoint is None:
        raise HTTPException(status_code=409, detail="PROVIDER_CONNECTION_ENDPOINT_NOT_FOUND")
    provider_service = _provider_connection_service(request)
    try:
        revision = provider_service.credential_revision_for_connection(connection_id)
        snapshot = service.read_account_usage(
            endpoint.endpoint_id,
            credential_ref=connection.credential_ref or endpoint.credential_ref,
            connection_id=connection_id,
            credential_revision=revision,
            force=True,
        )
    except (KeyError, TypeError, ValueError, RuntimeError) as error:
        raise _connection_http_error(error) from None
    return AccountUsageView.from_snapshot(snapshot)


def _configured_provider_ids(
    request: Request,
    service: ModelGatewayManagementService,
) -> set[str]:
    """Return connection identities, keeping preset templates out of the list."""

    provider_service = getattr(request.app.state, "provider_configuration_service", None)
    configured_ids = getattr(provider_service, "configured_provider_ids", None)
    if callable(configured_ids):
        return set(configured_ids())
    return {
        item.provider_id
        for item in service.list_credentials()
        if item.enabled
    } | {
        item.provider_id
        for item in service.list_models()
        if item.record_provenance in {
            RecordProvenance.ENDPOINT_DISCOVERY,
            RecordProvenance.EXPLICIT_CONFIGURATION,
        }
    }


def _current_endpoint_models(
    request: Request,
    service: ModelGatewayManagementService,
) -> tuple[ModelProfile, ...]:
    """Return only models admitted to the current provider endpoint pools.

    The management registry intentionally retains historical and quarantined
    records for audit.  Production projections must consume the provider
    service's revision-qualified pool instead of exposing that history as
    current candidates.  Test-only management apps without the production
    provider composition keep their existing full-registry behavior.
    """

    provider_service = getattr(request.app.state, "provider_configuration_service", None)
    configured_provider_ids = getattr(provider_service, "configured_provider_ids", None)
    current_model_pool = getattr(provider_service, "model_pool", None)
    if not callable(configured_provider_ids) or not callable(current_model_pool):
        return service.list_models()
    current: dict[str, ModelProfile] = {}
    try:
        connections = tuple(
            item
            for item in service.list_connections()
            if item.status is ProviderConnectionStatus.ACTIVE
            and (service.endpoint_profile(item.endpoint_id) is not None)
        )
        if connections:
            scopes = ((None, item.connection_id) for item in connections)
        else:
            scopes = ((provider_id, None) for provider_id in configured_provider_ids())
        for provider_id, connection_id in scopes:
            models = (
                current_model_pool(connection_id=connection_id)
                if connection_id is not None
                else current_model_pool(provider_id=provider_id)
            )
            for model in models:
                if (
                    (provider_id is not None and model.provider_id != provider_id)
                    or service.endpoint_profile(model.endpoint_id) is None
                ):
                    continue
                current[model.model_profile_id] = model
    except (TypeError, ValueError, RuntimeError):
        return ()
    return tuple(current.values())


def _endpoint_view(
    service: ModelGatewayManagementService,
    endpoint: EndpointProfile,
) -> EndpointView:
    configured_refs = {item.secret_ref for item in service.list_credentials()}
    return EndpointView(
        endpoint_id=endpoint.endpoint_id,
        provider_id=endpoint.provider_id,
        display_name=endpoint.display_name,
        endpoint_type=endpoint.endpoint_type,
        protocol_family=endpoint.protocol_family,
        base_url=endpoint.base_url,
        credential_configured=(
            endpoint.credential_ref in configured_refs
            if endpoint.credential_ref
            else endpoint.endpoint_type.value == "LOCAL"
        ),
        enabled=endpoint.enabled,
        health_status=endpoint.health_status,
    )


@router.get("/providers", response_model=tuple[ProviderView, ...])
def list_providers(request: Request) -> tuple[ProviderView, ...]:
    service = _service(request)
    configured_ids = _configured_provider_ids(request, service)
    return tuple(
        ProviderView.from_profile(item)
        for item in service.list_providers()
        if item.provider_id in configured_ids
    )


@router.get("/provider-templates", response_model=tuple[ProviderTemplateView, ...])
def list_provider_templates() -> tuple[ProviderTemplateView, ...]:
    backend_protocols = {item.value for item in SUPPORTED_PROTOCOL_FAMILIES}
    return tuple(
        ProviderTemplateView.from_profile(item).model_copy(
            update={
                "supported_protocols": tuple(
                    protocol
                    for protocol in ProviderTemplateView.from_profile(item).supported_protocols
                    if protocol in backend_protocols
                )
            }
        )
        for item in provider_template_presets()
    )


@mutation_router.post("/providers", response_model=ProviderView, status_code=status.HTTP_201_CREATED)
def configure_provider(payload: ProviderProfile, request: Request) -> ProviderView:
    service = _service(request)
    try:
        service.add_provider(payload)
    except ValueError:
        raise HTTPException(status_code=409, detail="PROVIDER_ALREADY_REGISTERED") from None
    _persist(service)
    return ProviderView.from_profile(payload)


@router.get("/endpoints", response_model=tuple[EndpointView, ...])
def list_endpoints(request: Request) -> tuple[EndpointView, ...]:
    service = _service(request)
    return tuple(_endpoint_view(service, item) for item in service.list_endpoints())


@router.get("/credentials", response_model=tuple[CredentialView, ...])
def list_credentials(request: Request) -> tuple[CredentialView, ...]:
    return tuple(
        CredentialView.from_profile(item)
        for item in _service(request).list_credentials()
    )


@mutation_router.post("/endpoints", response_model=EndpointView, status_code=status.HTTP_201_CREATED)
def configure_endpoint(payload: EndpointProfile, request: Request) -> EndpointView:
    service = _service(request)
    try:
        service.add_endpoint(payload)
    except ValueError as error:
        detail = "ENDPOINT_PROVIDER_NOT_REGISTERED" if "provider" in str(error) else "ENDPOINT_ALREADY_REGISTERED"
        raise HTTPException(status_code=409, detail=detail) from None
    _persist(service)
    return _endpoint_view(service, payload)


@mutation_router.post(
    "/endpoints/{endpoint_id}/test-connection",
    response_model=EndpointView,
)
def test_endpoint_connection(endpoint_id: str, request: Request) -> EndpointView:
    service = _service(request)
    probe = getattr(request.app.state, "model_gateway_connection_probe", None)
    if not isinstance(probe, ConnectionProbePort):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CONNECTION_TEST_UNAVAILABLE",
        )
    try:
        endpoint = service.test_connection(endpoint_id, probe)
    except ValueError:
        raise HTTPException(status_code=404, detail="ENDPOINT_NOT_FOUND") from None
    except (TypeError, RuntimeError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CONNECTION_TEST_UNAVAILABLE",
        ) from None
    _persist(service)
    return _endpoint_view(service, endpoint)


@mutation_router.post("/credentials", status_code=status.HTTP_201_CREATED)
def configure_credential(payload: CredentialReferenceInput, request: Request) -> dict[str, object]:
    service = _service(request)
    try:
        service.add_credential(CredentialProfile(**payload.model_dump()))
    except ValueError as error:
        detail = "CREDENTIAL_PROVIDER_NOT_REGISTERED" if "provider" in str(error) else "CREDENTIAL_ALREADY_REGISTERED"
        raise HTTPException(status_code=409, detail=detail) from None
    _persist(service)
    return {"credential_id": payload.credential_id, "provider_id": payload.provider_id, "configured": True}


@router.get("/models", response_model=tuple[ModelView, ...])
def list_models(request: Request) -> tuple[ModelView, ...]:
    service = _service(request)
    return tuple(ModelView.from_profile(item) for item in _current_endpoint_models(request, service))


@mutation_router.post("/models", response_model=ModelView, status_code=status.HTTP_201_CREATED)
def configure_model(payload: ModelProfile, request: Request) -> ModelView:
    service = _service(request)
    endpoint = service.endpoint_profile(payload.endpoint_id)
    if (
        endpoint is not None
        and endpoint.endpoint_type is EndpointType.OFFICIAL
        and not any(
            item.remote_model_id == payload.remote_model_id
            for item in official_model_catalog(payload.provider_id, endpoint.endpoint_id)
        )
    ):
        raise HTTPException(status_code=422, detail="MODEL_PROVIDER_MODEL_NOT_IN_CANONICAL_CATALOG")
    # This route is an explicit-configuration boundary.  A client cannot
    # manufacture discovery or verification evidence by posting a rich
    # ModelProfile; those fields are owned by the discovery/verification
    # services and the current endpoint.
    configured = payload.model_copy(
        update={
            "record_provenance": RecordProvenance.EXPLICIT_CONFIGURATION,
            "discovery_status": DiscoveryStatus.MANUAL,
            "availability": ModelAvailability.UNVERIFIED,
            "availability_status": ModelAvailability.UNVERIFIED,
            "availability_source": ModelCatalogSource.DECLARED,
            "availability_provenance": ModelCatalogSource.DECLARED,
            "verification_status": VerificationStatus.UNVERIFIED,
            "verified_at": None,
        }
    )
    try:
        service.add_model(configured)
    except ValueError as error:
        detail = "MODEL_PROVIDER_OR_ENDPOINT_INVALID" if "binding" in str(error) or "provider" in str(error) else "MODEL_ALREADY_REGISTERED"
        raise HTTPException(status_code=409, detail=detail) from None
    _persist(service)
    return ModelView.from_profile(configured)


@router.get("/policies/global", response_model=GlobalPolicyInput)
def read_global_policy(request: Request) -> GlobalPolicyInput:
    return GlobalPolicyInput.model_validate(_service(request).global_policy().model_dump(mode="python"))


@router.get("/task-profiles", response_model=tuple[TaskProfileView, ...])
def read_task_profiles(request: Request) -> tuple[TaskProfileView, ...]:
    return tuple(TaskProfileView.from_profile(item) for item in _service(request).task_profiles())


@mutation_router.put("/policies/global", response_model=GlobalPolicyInput)
def write_global_policy(payload: GlobalPolicyInput, request: Request) -> GlobalPolicyInput:
    service = _service(request)
    domain = payload.to_domain()
    service.set_global_policy(domain)
    _persist(service)
    return GlobalPolicyInput.model_validate(domain.model_dump(mode="python"))


@router.get("/policies/projects/{project_id}", response_model=ProjectPolicyInput)
def read_project_policy(project_id: str, request: Request) -> ProjectPolicyInput:
    value = _service(request).project_policy(project_id)
    if value is None:
        raise HTTPException(status_code=404, detail="PROJECT_POLICY_NOT_FOUND")
    return ProjectPolicyInput.model_validate(value.model_dump(mode="python"))


@router.get(
    "/assignments/{project_id}",
    response_model=tuple[TaskAssignmentInput, ...],
)
def read_task_assignments(request: Request, project_id: str) -> tuple[TaskAssignmentInput, ...]:
    value = _service(request).project_policy(project_id)
    if value is None:
        return ()
    return tuple(TaskAssignmentInput.model_validate(item.model_dump(mode="python")) for item in value.task_assignments)


@mutation_router.put(
    "/assignments/{project_id}/{task_type}",
    response_model=TaskAssignmentInput,
)
def write_task_assignment(
    project_id: str,
    task_type: str,
    payload: TaskAssignmentInput,
    request: Request,
) -> TaskAssignmentInput:
    _authorize_mutation_project(request, project_id)
    from embedded_copilot.model_gateway.profiles import ProjectModelPolicy, TaskType

    try:
        path_task = TaskType(task_type.upper())
    except ValueError:
        raise HTTPException(status_code=422, detail="TASK_TYPE_INVALID") from None
    if payload.task_type is not path_task:
        raise HTTPException(status_code=422, detail="TASK_ASSIGNMENT_BINDING_MISMATCH")
    service = _service(request)
    if service.models.get(payload.model_profile_id) is None:
        raise HTTPException(status_code=404, detail="MODEL_NOT_FOUND")
    current = service.project_policy(project_id) or ProjectModelPolicy(project_id=project_id)
    assignments = tuple(
        item
        for item in current.task_assignments
        if item.task_type is not path_task
    ) + (payload.to_domain(),)
    service.set_project_policy(current.model_copy(update={"task_assignments": assignments}))
    _persist(service)
    return payload


@mutation_router.put("/policies/projects/{project_id}", response_model=ProjectPolicyInput)
def write_project_policy(project_id: str, payload: ProjectPolicyInput, request: Request) -> ProjectPolicyInput:
    _authorize_mutation_project(request, project_id)
    if payload.project_id != project_id:
        raise HTTPException(status_code=422, detail="PROJECT_POLICY_BINDING_MISMATCH")
    service = _service(request)
    domain = payload.to_domain()
    service.set_project_policy(domain)
    _persist(service)
    return payload


@router.get("/usage", response_model=tuple[UsageView, ...])
def read_usage(request: Request) -> tuple[UsageView, ...]:
    return tuple(UsageView.from_aggregate(item) for item in _service(request).usage())


@router.get("/metrics", response_model=tuple[ModelMetricsView, ...])
def read_metrics(request: Request) -> tuple[ModelMetricsView, ...]:
    service = _service(request)
    reliability = {item.model_profile_id: item for item in service.reliability()}
    task_metrics = {(item.model_profile_id, item.task_type): item for item in service.task_metrics()}
    return tuple(
        ModelMetricsView.from_metrics(
            reliability.get(model.model_profile_id),
            next((value for (model_id, _), value in task_metrics.items() if model_id == model.model_profile_id), None),
            model.model_profile_id,
        )
        for model in _current_endpoint_models(request, service)
    )


@router.get("/usage/accounts", response_model=tuple[AccountUsageView, ...])
def read_account_usage(request: Request) -> tuple[AccountUsageView, ...]:
    return tuple(AccountUsageView.from_snapshot(item) for item in _service(request).account_usage_snapshots())


@mutation_router.post("/usage/accounts/{endpoint_id}/refresh", response_model=AccountUsageView)
def refresh_account_usage(endpoint_id: str, request: Request) -> AccountUsageView:
    return AccountUsageView.from_snapshot(_service(request).read_account_usage(endpoint_id, force=True))


@mutation_router.post("/overrides")
def request_override(payload: RequestOverrideInput, request: Request) -> dict[str, object]:
    raise HTTPException(status_code=403, detail="REQUEST_OVERRIDE_UNAUTHORIZED")


@mutation_router.post("/endpoints/{endpoint_id}/discover", response_model=tuple[ModelView, ...])
def discover_endpoint_models(endpoint_id: str, request: Request) -> tuple[ModelView, ...]:
    service = _service(request)
    provider_service = getattr(request.app.state, "provider_configuration_service", None)
    refresh_model_pool = getattr(provider_service, "refresh_model_pool", None)
    try:
        endpoint = service.endpoint_profile(endpoint_id)
        if endpoint is None:
            raise ValueError("endpoint is not registered")
        if callable(refresh_model_pool):
            refreshed = refresh_model_pool(provider_id=endpoint.provider_id)
            if refreshed.endpoint_id != endpoint_id:
                raise ValueError("provider endpoint is not registered")
            if refreshed.failure_evidence is not None:
                raise HTTPException(
                    status_code=503,
                    detail=refreshed.failure_evidence.as_api_detail(),
                )
            if refreshed.status != "UPDATED":
                raise RuntimeError("model discovery is unavailable")
            result = refreshed.models
        else:
            result = service.discover_models(endpoint_id)
    except ModelDiscoveryFailure as error:
        raise HTTPException(status_code=503, detail=error.evidence.as_api_detail()) from None
    except RuntimeError:
        raise HTTPException(status_code=503, detail="MODEL_DISCOVERY_UNAVAILABLE") from None
    except ValueError:
        raise HTTPException(status_code=404, detail="ENDPOINT_NOT_FOUND") from None
    return tuple(ModelView.from_profile(item) for item in result)


@mutation_router.post("/models/{model_profile_id}/capability-probes/{probe_type}", response_model=CapabilityProbeResult)
def probe_model_capability(model_profile_id: str, probe_type: str, request: Request) -> CapabilityProbeResult:
    service = _service(request)
    if not any(item.model_profile_id == model_profile_id for item in _current_endpoint_models(request, service)):
        raise HTTPException(status_code=404, detail="MODEL_NOT_IN_CURRENT_ENDPOINT_POOL")
    try:
        return service.probe_capability(model_profile_id, probe_type)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="CAPABILITY_PROBE_UNAVAILABLE") from None
    except ValueError as error:
        if str(error) == "capability probe model revision changed":
            raise HTTPException(status_code=409, detail="CAPABILITY_PROBE_STALE") from None
        if str(error) == "capability probe identity is invalid":
            raise HTTPException(status_code=409, detail="CAPABILITY_PROBE_IDENTITY_INVALID") from None
        raise HTTPException(status_code=404, detail="MODEL_NOT_FOUND") from None


def _governance_error(error: Exception) -> HTTPException:
    if isinstance(error, ModelSelectionError):
        return HTTPException(status_code=422, detail=error.code)
    if isinstance(error, KeyError):
        return HTTPException(status_code=404, detail=str(error).strip("'"))
    if isinstance(error, ValueError):
        message = str(error)
        if "already exists" in message:
            return HTTPException(status_code=409, detail="POLICY_ALREADY_EXISTS")
        return HTTPException(status_code=422, detail="POLICY_INVALID")
    return HTTPException(status_code=503, detail="MODEL_GOVERNANCE_UNAVAILABLE")


def _credential_ref(service: ModelGatewayManagementService, endpoint_id: str) -> str:
    endpoint = service.endpoint_profile(endpoint_id)
    if endpoint is None:
        raise ModelSelectionError("MODEL_ENDPOINT_MISMATCH")
    return endpoint.credential_ref or "credential:local"


@router.get("/governance/stages", response_model=tuple[EngineeringStageView, ...])
def list_engineering_stages(request: Request) -> tuple[EngineeringStageView, ...]:
    service = _service(request)
    return tuple(EngineeringStageView.from_domain(item) for item in service.engineering_stages())


@router.get("/governance/available-models", response_model=tuple[GovernanceModelView, ...])
def list_governance_models(
    request: Request,
    scope: PolicyScope = Query(default=PolicyScope.GLOBAL),
    project_id: str | None = Query(default=None, max_length=128),
    stage_id: EngineeringStage | None = Query(default=None),
) -> tuple[GovernanceModelView, ...]:
    service = _service(request)
    if scope is PolicyScope.PROJECT and not project_id:
        raise HTTPException(status_code=422, detail="PROJECT_ID_REQUIRED")
    if scope is PolicyScope.STAGE and (not project_id or stage_id is None):
        raise HTTPException(status_code=422, detail="STAGE_SCOPE_BINDING_REQUIRED")
    effective_stage = stage_id or EngineeringStage.REQUIREMENT_ANALYSIS
    required_capabilities = (
        next(
            (set(item.required_capabilities) for item in service.engineering_stages() if item.stage_id is stage_id),
            set(),
        )
        if scope is PolicyScope.STAGE
        else set()
    )
    applicable = ModelSelectionService.effective_policy_overlays(
        service.list_allowed_model_policies(),
        project_id=project_id or "global",
        stage_id=effective_stage,
    )
    by_identity = {service._identity_key(item): item for item in applicable}
    rows: list[GovernanceModelView] = []
    for model in _current_endpoint_models(request, service):
        endpoint = service.endpoint_profile(model.endpoint_id)
        if endpoint is None:
            continue
        provider = service.providers.get(model.provider_id)
        connection = next(
            (
                item
                for item in service.list_connections()
                if item.endpoint_id == model.endpoint_id
                and item.provider_id == model.provider_id
            ),
            None,
        )
        credential_ref = endpoint.credential_ref or "credential:local"
        identity = BoundModelIdentity(
            provider_id=model.provider_id,
            endpoint_id=model.endpoint_id,
            credential_ref=credential_ref,
            model_profile_id=model.model_profile_id,
            model_id=model.remote_model_id,
        )
        can_enable, enable_block_reason, identity_status = service.model_enablement(identity)
        if model.record_provenance not in {
            RecordProvenance.ENDPOINT_DISCOVERY,
            RecordProvenance.EXPLICIT_CONFIGURATION,
        }:
            continue
        if identity_status is not IdentityStatus.VALID:
            continue
        if (
            model.record_provenance is RecordProvenance.ENDPOINT_DISCOVERY
            and model.discovery_status.value != "DISCOVERED"
            and model.availability_status.value == ModelAvailability.UNKNOWN.value
        ):
            continue
        key = (model.provider_id, model.endpoint_id, credential_ref, model.model_profile_id, model.remote_model_id)
        policy = by_identity.get(key)
        capability_evidence = tuple(
            item.model_copy(update={"status": CapabilityStatus.STALE})
            if item.status is CapabilityStatus.PASS
            and not item.matches_model_identity(
                provider_id=model.provider_id,
                endpoint_id=model.endpoint_id,
                credential_ref=credential_ref,
                model_profile_id=model.model_profile_id,
                model_id=model.remote_model_id,
                credential_revision=model.credential_revision,
                endpoint_revision=model.endpoint_revision,
            )
            else item
            for item in model.capability_sources
        )
        capability_warning: str | None = None
        if required_capabilities:
            evidence = {item.capability: item for item in capability_evidence}
            missing = sorted(
                capability.value
                for capability in required_capabilities
                if capability not in model.capabilities
                or evidence.get(capability) is None
                or evidence[capability].status is not CapabilityStatus.PASS
                or not evidence[capability].matches_model_identity(
                    provider_id=model.provider_id,
                    endpoint_id=model.endpoint_id,
                    credential_ref=credential_ref,
                    model_profile_id=model.model_profile_id,
                    model_id=model.remote_model_id,
                    credential_revision=model.credential_revision,
                    endpoint_revision=model.endpoint_revision,
                )
            )
            if missing:
                capability_warning = "MISSING_CAPABILITY:" + ",".join(missing)
        rows.append(
            GovernanceModelView(
                model_profile_id=model.model_profile_id,
                provider_id=model.provider_id,
                provider_display_name=provider.display_name if provider is not None else model.provider_id,
                connection_id=connection.connection_id if connection is not None else None,
                connection_name=connection.connection_name if connection is not None else None,
                endpoint_id=model.endpoint_id,
                protocol_family=getattr(endpoint.protocol_family, "value", endpoint.protocol_family),
                credential_ref=credential_ref,
                model_id=model.remote_model_id,
                display_name=model.display_name,
                billing_class=model.billing_class,
                billing_provenance=model.billing_provenance,
                billing_source=model.billing_source,
                availability=model.availability_status.value,
                availability_status=model.availability_status.value,
                availability_provenance=model.availability_provenance.value,
                identity_status=identity_status,
                identity_status_reason=enable_block_reason if identity_status is not IdentityStatus.VALID else None,
                record_provenance=model.record_provenance,
                verification_status=model.verification_status,
                discovery_status=model.discovery_status,
                metadata_source=model.metadata_source,
                metadata_updated_at=model.metadata_updated_at.isoformat() if model.metadata_updated_at else None,
                discovered_at=model.discovered_at.isoformat() if model.discovered_at else None,
                verified_at=model.verified_at.isoformat() if model.verified_at else None,
                capabilities=model.capabilities,
                capability_provenance=capability_evidence,
                can_enable=can_enable,
                enable_block_reason=enable_block_reason,
                allowed=policy is not None and policy.enabled,
                policy_status=("ALLOWED" if policy is not None and policy.enabled else "BLOCKED"),
                policy_block_reason=(
                    None
                    if policy is not None and policy.enabled
                    else "POLICY_BLOCKED" if policy is not None else "MODEL_NOT_ALLOWED"
                ),
                credential_revision=model.credential_revision,
                endpoint_revision=model.endpoint_revision,
                policy_id=policy.id if policy is not None else None,
                policy_scope=policy.scope if policy is not None else None,
                policy_project_id=policy.project_id if policy is not None else None,
                policy_stage_id=policy.stage_id if policy is not None else None,
                capability_warning=capability_warning,
            )
        )
    return tuple(rows)


@router.get("/governance/official-catalog", response_model=tuple[OfficialCatalogModelView, ...])
def list_official_catalog(
    request: Request,
    provider_id: str | None = Query(default=None, max_length=128),
) -> tuple[OfficialCatalogModelView, ...]:
    providers = provider_presets()
    if provider_id is not None:
        providers = tuple(item for item in providers if item.provider_id == provider_id)
    rows: list[OfficialCatalogModelView] = []
    for provider in providers:
        for model in official_model_catalog(provider.provider_id, f"endpoint-{provider.provider_id}"):
            rows.append(
                OfficialCatalogModelView(
                    provider_id=model.provider_id,
                    model_id=model.remote_model_id,
                    display_name=model.display_name,
                    billing_class=model.billing_class,
                    billing_provenance=model.billing_provenance,
                    billing_source=model.billing_source,
                    capabilities=model.capabilities,
                    metadata_source=model.metadata_source,
                    metadata_updated_at=model.metadata_updated_at.isoformat() if model.metadata_updated_at else None,
                    deprecated=model.deprecated,
                )
            )
    return tuple(rows)


@router.get("/governance/integrity-issues", response_model=tuple[IdentityIntegrityIssueView, ...])
def list_integrity_issues(request: Request) -> tuple[IdentityIntegrityIssueView, ...]:
    return tuple(
        IdentityIntegrityIssueView.from_domain(item)
        for item in _service(request).integrity_issues()
    )


@router.get("/governance/allowed-models", response_model=tuple[AllowedModelPolicyView, ...])
def list_allowed_model_policies(request: Request) -> tuple[AllowedModelPolicyView, ...]:
    return tuple(AllowedModelPolicyView.from_domain(item) for item in _service(request).list_allowed_model_policies())


@mutation_router.post("/governance/allowed-models", response_model=AllowedModelPolicyView, status_code=status.HTTP_201_CREATED)
def create_allowed_model_policy(payload: AllowedModelPolicyInput, request: Request) -> AllowedModelPolicyView:
    if payload.project_id is not None:
        _authorize_mutation_project(request, payload.project_id)
    service = _service(request)
    try:
        result = service.create_allowed_model_policy(
            scope=payload.scope,
            project_id=payload.project_id,
            stage_id=payload.stage_id,
            identity=payload.identity(),
            enabled=payload.enabled,
            priority=payload.priority,
        )
        _persist(service)
        return AllowedModelPolicyView.from_domain(result)
    except Exception as error:
        raise _governance_error(error) from error


@mutation_router.put("/governance/allowed-models/{policy_id}", response_model=AllowedModelPolicyView)
def update_allowed_model_policy(policy_id: str, payload: AllowedModelPolicyInput, request: Request) -> AllowedModelPolicyView:
    service = _service(request)
    current = service.allowed_model_policy(policy_id)
    if current is None:
        raise HTTPException(status_code=404, detail="ALLOWED_MODEL_POLICY_NOT_FOUND")
    if current.project_id is not None:
        _authorize_mutation_project(request, current.project_id)
    if payload.project_id is not None:
        _authorize_mutation_project(request, payload.project_id)
    try:
        updated = AllowedModelPolicy(
            id=policy_id,
            scope=payload.scope,
            project_id=payload.project_id,
            stage_id=payload.stage_id,
            provider_id=payload.provider_id,
            endpoint_id=payload.endpoint_id,
            credential_ref=payload.credential_ref,
            model_profile_id=payload.model_profile_id,
            model_id=payload.model_id,
            enabled=payload.enabled,
            priority=payload.priority,
            created_at=current.created_at,
            updated_at=datetime.now(UTC),
        )
        result = service.update_allowed_model_policy(updated)
        _persist(service)
        return AllowedModelPolicyView.from_domain(result)
    except Exception as error:
        raise _governance_error(error) from error


@mutation_router.patch("/governance/allowed-models/{policy_id}/enabled", response_model=AllowedModelPolicyView)
def set_allowed_model_policy_enabled(policy_id: str, enabled: bool, request: Request) -> AllowedModelPolicyView:
    service = _service(request)
    current = service.allowed_model_policy(policy_id)
    if current is None:
        raise HTTPException(status_code=404, detail="ALLOWED_MODEL_POLICY_NOT_FOUND")
    if current.project_id is not None:
        _authorize_mutation_project(request, current.project_id)
    try:
        result = service.set_allowed_model_enabled(policy_id, enabled)
        _persist(service)
        return AllowedModelPolicyView.from_domain(result)
    except Exception as error:
        raise _governance_error(error) from error


@router.get("/governance/project-defaults/{project_id}", response_model=ProjectDefaultModelPolicyView)
def read_project_default_policy(project_id: str, request: Request) -> ProjectDefaultModelPolicyView:
    value = _service(request).project_default_model_policy(project_id)
    if value is None:
        raise HTTPException(status_code=404, detail="PROJECT_DEFAULT_POLICY_NOT_FOUND")
    return ProjectDefaultModelPolicyView.from_domain(value)


@mutation_router.put("/governance/project-defaults/{project_id}", response_model=ProjectDefaultModelPolicyView)
def write_project_default_policy(project_id: str, payload: ProjectDefaultModelPolicyInput, request: Request) -> ProjectDefaultModelPolicyView:
    _authorize_mutation_project(request, project_id)
    service = _service(request)
    current = service.project_default_model_policy(project_id)
    now = datetime.now(UTC)
    try:
        fixed, candidates = payload.to_values()
        policy = ProjectDefaultModelPolicy(
            id=current.id if current is not None else service._new_id("project-default-model"),
            project_id=project_id,
            selection_mode=payload.selection_mode,
            fixed_model=fixed,
            candidate_models=candidates,
            created_at=current.created_at if current is not None else now,
            updated_at=now,
        )
        result = service.set_project_default_model_policy(policy)
        _persist(service)
        return ProjectDefaultModelPolicyView.from_domain(result)
    except Exception as error:
        raise _governance_error(error) from error


@mutation_router.delete("/governance/project-defaults/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project_default_policy(project_id: str, request: Request) -> None:
    _authorize_mutation_project(request, project_id)
    service = _service(request)
    if not service.delete_project_default_model_policy(project_id):
        raise HTTPException(status_code=404, detail="PROJECT_DEFAULT_POLICY_NOT_FOUND")
    _persist(service)


@router.get("/governance/stage-policies/{project_id}", response_model=tuple[StageModelPolicyView, ...])
def read_stage_policies(project_id: str, request: Request) -> tuple[StageModelPolicyView, ...]:
    return tuple(StageModelPolicyView.from_domain(item) for item in _service(request).stage_model_policies(project_id))


@mutation_router.put("/governance/stage-policies/{project_id}/{stage_id}", response_model=StageModelPolicyView)
def write_stage_policy(project_id: str, stage_id: EngineeringStage, payload: StageModelPolicyInput, request: Request) -> StageModelPolicyView:
    _authorize_mutation_project(request, project_id)
    service = _service(request)
    current = service.stage_model_policy(project_id, stage_id)
    now = datetime.now(UTC)
    try:
        fixed, candidates = payload.to_values()
        policy = StageModelPolicy(
            id=current.id if current is not None else service._new_id("stage-model"),
            project_id=project_id,
            stage_id=stage_id,
            selection_mode=payload.selection_mode,
            fixed_model=fixed,
            candidate_models=candidates,
            candidate_group_id=payload.candidate_group_id,
            candidate_group_revision=payload.candidate_group_revision,
            created_at=current.created_at if current is not None else now,
            updated_at=now,
        )
        result = service.set_stage_model_policy(policy)
        _persist(service)
        return StageModelPolicyView.from_domain(result)
    except Exception as error:
        raise _governance_error(error) from error


@mutation_router.delete("/governance/stage-policies/{project_id}/{stage_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_stage_policy(project_id: str, stage_id: EngineeringStage, request: Request) -> None:
    _authorize_mutation_project(request, project_id)
    service = _service(request)
    if not service.delete_stage_model_policy(project_id, stage_id):
        raise HTTPException(status_code=404, detail="STAGE_MODEL_POLICY_NOT_FOUND")
    _persist(service)


router.include_router(mutation_router)

__all__ = ("router",)
