"""Thin additive API boundary for v7.0 release operations."""

from __future__ import annotations

import ipaddress
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from embedded_copilot.api.desktop_identity import (
    TrustedDesktopIdentity,
    desktop_identity,
    set_desktop_session_cookie,
)
from embedded_copilot.api.v70_release_models import (
    BootstrapExchangeInput,
    CredentialUpdateInput,
    FrontendProviderDiagnosticInput,
    ProviderConfigurationInput,
    ProviderConnectionTestInput,
    ProviderCredentialInput,
    RestoreAuthorizationInput,
    RestoreInput,
    TransmissionApprovalInput,
)
from embedded_copilot.model_gateway import ProviderConnection
from embedded_copilot.native_shell.handoff_telemetry import record_native_handoff
from embedded_copilot.production_release.backup import RestoreError
from embedded_copilot.production_release.credential_save_diagnostic import (
    emit as emit_configuration_diagnostic,
)
from embedded_copilot.production_release.credential_save_diagnostic import (
    emit as emit_credential_save_diagnostic,
)
from embedded_copilot.production_release.credential_save_diagnostic import (
    new_request_id as new_credential_save_request_id,
)
from embedded_copilot.production_release.credential_save_diagnostic import (
    request_scope as credential_save_request_scope,
)
from embedded_copilot.production_release.credentials import CredentialUnavailable
from embedded_copilot.production_release.provider import (
    ProviderConfigurationService,
    ProviderProjection,
    TransmissionApprovalService,
    TransmissionDecision,
)
from embedded_copilot.production_release.provider_verification import (
    ProviderVerificationReceipt,
)
from embedded_copilot.production_release.runtime import ProductionReleaseRuntime
from embedded_copilot.production_release.session import SessionRejected
from embedded_copilot.production_release.test_connection_diagnostic import (
    emit as emit_test_connection_diagnostic,
)
from embedded_copilot.production_release.webview_session_diagnostic import (
    backend_origin_header_class,
    origin_marker,
    request_id_for_scope,
)
from embedded_copilot.production_release.webview_session_diagnostic import (
    emit as emit_webview_diagnostic,
)
from embedded_copilot.production_release.webview_session_diagnostic import (
    enabled as webview_diagnostic_enabled,
)
from embedded_copilot.production_release.webview_session_diagnostic import (
    request_scope as webview_request_scope,
)

router = APIRouter(prefix="/api/v1/copilot/desktop/system", tags=["v7-production-release"])


def _runtime(request: Request) -> ProductionReleaseRuntime | None:
    value = getattr(request.app.state, "production_release_runtime", None)
    return value if isinstance(value, ProductionReleaseRuntime) else None


def _provider(request: Request) -> ProviderConfigurationService | None:
    value = getattr(request.app.state, "provider_configuration_service", None)
    return value if isinstance(value, ProviderConfigurationService) else None


def _legacy_connection(
    request: Request,
    provider: ProviderConfigurationService,
    provider_id: str | None,
    *,
    allow_unconfigured_onboarding: bool = False,
) -> tuple[ProviderConnection | None, JSONResponse | None]:
    """Resolve provider_id once for legacy routes; never select an arbitrary connection."""

    target = provider_id or provider.current_provider
    try:
        return provider.resolve_unique_active_connection(target), None
    except (TypeError, ValueError) as error:
        code = str(error).strip()
        if code == "PROVIDER_CONNECTION_MANAGEMENT_UNAVAILABLE":
            # Older isolated release tests and development composition do not
            # inject the canonical registry; preserve their existing service
            # boundary without inventing a connection identity.
            return None, None
        if code == "PROVIDER_CONNECTION_AMBIGUOUS":
            return None, _error(request, 409, code)
        if code in {"PROVIDER_NOT_CONFIGURED", "PROVIDER_CONNECTION_NOT_FOUND"}:
            if allow_unconfigured_onboarding and target != provider.current_provider:
                # Keep the pre-existing alternate-provider onboarding flow:
                # it does not select or execute a runtime connection.  The
                # current provider and every operational legacy read remain
                # strict 0 -> NOT_CONFIGURED resolutions.
                return None, None
            return None, _error(request, 404, "PROVIDER_CONNECTION_NOT_CONFIGURED")
        if code == "PROVIDER_CONNECTION_DELETED":
            return None, _error(request, 410, code)
        if code in {"PROVIDER_CONNECTION_DISABLED", "PROVIDER_CONNECTION_QUARANTINED"}:
            return None, _error(request, 409, code)
        return None, _error(request, 422, code or "PROVIDER_CONNECTION_INVALID")


def _approvals(request: Request) -> TransmissionApprovalService | None:
    value = getattr(request.app.state, "transmission_approval_service", None)
    return value if isinstance(value, TransmissionApprovalService) else None


def _is_loopback_request(request: Request) -> bool:
    client = request.client
    host = client.host if client is not None else ""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _error(request: Request, status: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": code, "trace_id": getattr(request.state, "trace_id", "")})


def _sync_provider_runtime_projection(
    request: Request,
    provider: ProviderConfigurationService,
    projection: ProviderProjection,
) -> None:
    """Refresh capability state after a provider configuration or probe mutation."""

    request.app.state.provider_projection = projection
    capability_statuses = dict(
        getattr(request.app.state, "runtime_capability_statuses", {})
    )
    component_statuses = dict(
        getattr(request.app.state, "runtime_component_statuses", {})
    )
    model_available = projection.model_available
    provider_configured = projection.credential_status == "CONFIGURED"
    chat_runtime_ready = getattr(
        request.app.state, "desktop_copilot_chat_runtime", None
    ) is not None
    gateway_available = provider.binding.is_configured
    capability_statuses.update(
        {
            "provider_configuration": "available"
            if provider_configured
            else "unavailable",
            "chat_model": "available" if model_available else "unavailable",
            "engineering_analysis_completion": (
                "available" if model_available and chat_runtime_ready else "unavailable"
            ),
            "model_gateway": "available" if gateway_available else "unavailable",
        }
    )
    component_statuses.update(
        {
            "model_gateway": "available" if gateway_available else "unavailable",
            "chat_model": "available" if model_available else "unavailable",
        }
    )
    request.app.state.runtime_capability_statuses = capability_statuses
    request.app.state.runtime_component_statuses = component_statuses


@router.get("/about")
def about(request: Request):
    runtime = _runtime(request)
    if runtime is None:
        return _error(request, 503, "PRODUCTION_RELEASE_UNAVAILABLE")
    return runtime.health()


@router.post("/bootstrap/exchange")
def bootstrap_exchange(payload: BootstrapExchangeInput, request: Request, response: Response):
    with webview_request_scope(request_id_for_scope(request.scope)):
        marker = origin_marker(request)
        emit_webview_diagnostic(
            "WEBVIEW_SESSION_EXCHANGE_ROUTE_ENTERED",
            result="ENTERED",
            route="SESSION_EXCHANGE",
            origin=marker,
        )
        runtime = _runtime(request)
        if runtime is None:
            return _error(request, 503, "PRODUCTION_RELEASE_UNAVAILABLE")
        if request.headers.get("x-embedded-copilot-native-handoff") == "H7_BOOTSTRAP_URL_READ":
            record_native_handoff("H7_BOOTSTRAP_URL_READ", "PASS", reason_code="FRONTEND_FRAGMENT_READ")
        record_native_handoff("H8_SESSION_EXCHANGE_REQUEST", "ENTER")
        try:
            token = runtime.sessions.exchange(payload.nonce)
        except SessionRejected:
            record_native_handoff("H8_SESSION_EXCHANGE_REQUEST", "PASS", reason_code="REQUEST_RECEIVED")
            record_native_handoff(
                "H9_SESSION_EXCHANGE_RESPONSE",
                "FAIL",
                exception_type="SessionRejected",
                reason_code="DESKTOP_BOOTSTRAP_REJECTED",
                http_status=401,
            )
            return _error(request, 401, "DESKTOP_BOOTSTRAP_REJECTED")
        emit_webview_diagnostic(
            "WEBVIEW_SESSION_CREATED",
            result="SUCCESS",
            route="SESSION_EXCHANGE",
            origin=marker,
        )
        record_native_handoff("H8_SESSION_EXCHANGE_REQUEST", "PASS", reason_code="REQUEST_RECEIVED")
        record_native_handoff("H9_SESSION_EXCHANGE_RESPONSE", "PASS", http_status=200)
        set_desktop_session_cookie(response, token, max_age=runtime.settings.desktop_session_ttl_seconds)
        emit_webview_diagnostic(
            "WEBVIEW_SESSION_COOKIE_SET",
            result="EMITTED",
            route="SESSION_EXCHANGE",
            origin=marker,
        )
        return {"status": "EXCHANGED"}


@router.get("/health")
def health(request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    del identity
    runtime = _runtime(request)
    if runtime is None:
        return _error(request, 503, "PRODUCTION_RELEASE_UNAVAILABLE")
    return runtime.health()


@router.post("/diagnostics/frontend-provider", status_code=204, include_in_schema=False)
def frontend_provider_diagnostic(payload: FrontendProviderDiagnosticInput, request: Request) -> Response:
    """Accept only fixed frontend evidence on an explicitly enabled local runtime."""

    if not webview_diagnostic_enabled() or not _is_loopback_request(request):
        return Response(status_code=204)
    if origin_marker(request) != "WEBVIEW":
        return Response(status_code=204)
    with webview_request_scope(payload.request_id):
        emit_webview_diagnostic(
            payload.stage,
            result=payload.result,
            route=payload.route,
            request_id=payload.request_id,
            fetch_attempt_id=payload.fetch_attempt_id,
            event_sequence=payload.event_sequence,
            fetch_attempt_started_at=payload.fetch_attempt_started_at,
            fetch_attempt_finished_at=payload.fetch_attempt_finished_at,
            fetch_attempt_duration_ms=payload.fetch_attempt_duration_ms,
            project_locations_attempt_started_at=payload.project_locations_attempt_started_at,
            project_locations_attempt_finished_at=payload.project_locations_attempt_finished_at,
            project_locations_attempt_duration_ms=payload.project_locations_attempt_duration_ms,
            frontend_fetch_resolved=payload.frontend_fetch_resolved,
            frontend_http_status=payload.frontend_http_status,
            final_result=payload.final_result,
            http_status=payload.http_status,
            safe_error_class_value=payload.safe_error_class,
            failure_stage=payload.failure_stage,
            projection_processing_result=payload.projection_processing_result,
            projection_state=payload.projection_state,
            credential_state=payload.credential_state,
            provider_present=payload.provider_present,
            fetcher_identity=payload.fetcher_identity,
            production_fetcher_identity=payload.production_fetcher_identity,
            frontend_origin_class=payload.frontend_origin_class,
            provider_target_origin_class=payload.provider_target_origin_class,
            frontend_provider_same_origin=payload.frontend_provider_same_origin,
            provider_request_url_strategy=payload.provider_request_url_strategy,
            request_credentials_mode=payload.request_credentials_mode,
            document_ready_state_at_fetch=payload.document_ready_state_at_fetch,
            navigation_state_at_fetch=payload.navigation_state_at_fetch,
            document_generation_at_fetch=payload.document_generation_at_fetch,
            document_generation_at_rejection=payload.document_generation_at_rejection,
            navigation_started_during_fetch=payload.navigation_started_during_fetch,
            document_context_changed_during_fetch=payload.document_context_changed_during_fetch,
            abort_signal_attached_to_provider_fetch=payload.abort_signal_attached_to_provider_fetch,
            abort_trigger_observed=payload.abort_trigger_observed,
            abort_trigger_source=payload.abort_trigger_source,
            fetch_rejection_error_class=payload.fetch_rejection_error_class,
            fetch_rejection_error_name=payload.fetch_rejection_error_name,
            fetch_rejection_message_category=payload.fetch_rejection_message_category,
            fetch_failure_kind=payload.fetch_failure_kind,
            fetch_failure_stage=payload.fetch_failure_stage,
            backend_origin_header_class=backend_origin_header_class(request),
            render_generation=payload.render_generation,
            build_id_value=payload.build_id,
            origin="WEBVIEW",
        )
        if payload.stage == "WEBVIEW_FRONTEND_READINESS_PASS" and payload.result == "SUCCESS":
            record_native_handoff(
                "H10_CHAT_READY",
                "PASS",
                reason_code="FRONTEND_REQUIRED_DEPENDENCIES_READY",
                http_status=204,
            )
    return Response(status_code=204)


@router.put("/credentials")
def update_credential(payload: CredentialUpdateInput, request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    del identity
    runtime = _runtime(request)
    if runtime is None:
        return _error(request, 503, "PRODUCTION_RELEASE_UNAVAILABLE")
    provider = _provider(request)
    if provider is None:
        return _error(request, 503, "PROVIDER_CONFIGURATION_UNAVAILABLE")
    try:
        result = provider.save_credential(payload.secret, provider=payload.provider)
        _sync_provider_runtime_projection(request, provider, provider.projection())
        return result
    except CredentialUnavailable:
        return _error(request, 503, "PROVIDER_CREDENTIAL_STORE_UNAVAILABLE")
    except (TypeError, ValueError):
        return _error(request, 422, "PROVIDER_CONFIG_INVALID")


@router.get("/provider")
def provider_configuration(
    request: Request,
    provider_id: str | None = Query(default=None, max_length=64),
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
):
    del identity
    provider = _provider(request)
    if provider is None:
        return _error(request, 503, "PROVIDER_CONFIGURATION_UNAVAILABLE")
    connection, error = _legacy_connection(request, provider, provider_id)
    if error is not None:
        return error
    try:
        return provider.projection(
            connection_id=connection.connection_id
            if connection is not None
            else None,
            provider_id=provider_id if connection is None else None,
            include_legacy_model=True,
        )
    except (TypeError, ValueError):
        return _error(request, 404, "PROVIDER_PROFILE_UNAVAILABLE")


@router.post("/provider/models/refresh")
def refresh_provider_models(
    request: Request,
    provider_id: str | None = Query(default=None, max_length=64),
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
):
    del identity
    provider = _provider(request)
    if provider is None:
        return _error(request, 503, "PROVIDER_CONFIGURATION_UNAVAILABLE")
    connection, error = _legacy_connection(request, provider, provider_id)
    if error is not None:
        return error
    try:
        if connection is not None:
            provider.refresh_model_pool(connection_id=connection.connection_id)
            return provider.projection(
                connection_id=connection.connection_id,
                include_legacy_model=True,
            )
        provider.refresh_model_pool(provider_id=provider_id)
        return provider.projection(provider_id=provider_id)
    except CredentialUnavailable:
        return _error(request, 503, "PROVIDER_CREDENTIAL_STORE_UNAVAILABLE")
    except (TypeError, ValueError, RuntimeError):
        return _error(request, 422, "PROVIDER_MODEL_DISCOVERY_UNAVAILABLE")


@router.put("/provider/configuration")
def save_provider_configuration(
    payload: ProviderConfigurationInput,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
):
    del identity
    emit_configuration_diagnostic("CONFIGURATION_ROUTE_ENTERED", result="ENTERED")
    provider = _provider(request)
    if provider is None:
        emit_configuration_diagnostic("CONFIGURATION_ROUTE_FAILURE", result="HTTP_503")
        return _error(request, 503, "PROVIDER_CONFIGURATION_UNAVAILABLE")
    connection, error = _legacy_connection(
        request,
        provider,
        payload.provider,
        allow_unconfigured_onboarding=True,
    )
    if error is not None:
        emit_configuration_diagnostic("CONFIGURATION_ROUTE_FAILURE", result="HTTP_409")
        return error
    try:
        result = provider.save_configuration(
            provider=payload.provider,
            connection_name=payload.connection_name,
            model=payload.model,
            base_url=payload.base_url,
            endpoint_id=payload.endpoint_id,
            model_profile_id=payload.model_profile_id,
            credential_ref=payload.credential_ref,
            protocol_family=payload.protocol_family,
            configuration_fingerprint=payload.configuration_fingerprint,
            connection_id=connection.connection_id if connection is not None else None,
        )
    except CredentialUnavailable as error:
        emit_configuration_diagnostic("CONFIGURATION_ROUTE_FAILURE", result="HTTP_503", error=error)
        return _error(request, 503, "PROVIDER_CREDENTIAL_STORE_UNAVAILABLE")
    except (TypeError, ValueError) as error:
        emit_configuration_diagnostic("CONFIGURATION_ROUTE_FAILURE", result="HTTP_422", error=error)
        return _error(request, 422, "PROVIDER_CONFIG_INVALID")
    emit_configuration_diagnostic("CONFIGURATION_ROUTE_SUCCESS", result="HTTP_200")
    _sync_provider_runtime_projection(request, provider, result)
    return result


@router.put("/provider/credential")
def save_provider_credential(
    payload: ProviderCredentialInput,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
):
    del identity
    request_id = new_credential_save_request_id()
    with credential_save_request_scope(request_id):
        emit_credential_save_diagnostic("HTTP_ROUTE_ENTERED", result="ENTERED")
        provider = _provider(request)
        if provider is None:
            emit_credential_save_diagnostic("HTTP_ROUTE_FAILURE", result="HTTP_503")
            return _error(request, 503, "PROVIDER_CONFIGURATION_UNAVAILABLE")
        connection, error = _legacy_connection(
            request,
            provider,
            payload.provider,
            allow_unconfigured_onboarding=True,
        )
        if error is not None:
            emit_credential_save_diagnostic("HTTP_ROUTE_FAILURE", result="HTTP_409")
            return error
        try:
            result = provider.save_credential(
                payload.secret,
                provider=payload.provider,
                connection_id=connection.connection_id if connection is not None else None,
                diagnostic_request_id=request_id,
            )
        except CredentialUnavailable as error:
            emit_credential_save_diagnostic("HTTP_ROUTE_FAILURE", result="HTTP_503", error=error)
            return _error(request, 503, "PROVIDER_CREDENTIAL_STORE_UNAVAILABLE")
        except (TypeError, ValueError) as error:
            emit_credential_save_diagnostic("HTTP_ROUTE_FAILURE", result="HTTP_422", error=error)
            return _error(request, 422, "PROVIDER_CONFIG_INVALID")
        emit_credential_save_diagnostic("HTTP_ROUTE_SUCCESS", result="HTTP_200")
        # Credential-only onboarding updates only the provider connection
        # profile; runtime model selection remains under governance.
        _sync_provider_runtime_projection(request, provider, provider.projection())
        return result


@router.delete("/provider/credential")
def remove_provider_credential(
    request: Request,
    provider_id: str | None = Query(default=None, max_length=64),
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
):
    del identity
    provider = _provider(request)
    if provider is None:
        return _error(request, 503, "PROVIDER_CONFIGURATION_UNAVAILABLE")
    connection, error = _legacy_connection(request, provider, provider_id)
    if error is not None:
        return error
    try:
        result = provider.remove_credential(
            connection_id=connection.connection_id if connection is not None else None,
            provider=provider_id if connection is None else None,
        )
        _sync_provider_runtime_projection(request, provider, result)
        return result
    except CredentialUnavailable:
        return _error(request, 503, "PROVIDER_CREDENTIAL_STORE_UNAVAILABLE")


@router.delete("/provider/connection")
def remove_provider_connection(
    request: Request,
    provider_id: str | None = Query(default=None, max_length=64),
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
):
    del identity
    provider = _provider(request)
    if provider is None:
        return _error(request, 503, "PROVIDER_CONFIGURATION_UNAVAILABLE")
    connection, error = _legacy_connection(request, provider, provider_id)
    if error is not None:
        return error
    try:
        result = provider.remove_connection(
            connection_id=connection.connection_id if connection is not None else None,
            provider=provider_id if connection is None else None,
        )
        _sync_provider_runtime_projection(request, provider, result)
        return result
    except CredentialUnavailable:
        return _error(request, 503, "PROVIDER_CREDENTIAL_STORE_UNAVAILABLE")
    except (TypeError, ValueError):
        return _error(request, 422, "PROVIDER_CONNECTION_NOT_FOUND")


@router.post("/provider/test-connection")
def test_provider_connection(
    request: Request,
    payload: ProviderConnectionTestInput | None = None,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
):
    del identity
    configured_provider = payload.provider_id if payload is not None else getattr(_provider(request), "current_provider", "unknown")
    emit_test_connection_diagnostic(
        "TEST_CONNECTION_ROUTE_ENTERED",
        result="ENTERED",
        provider=configured_provider,
    )
    provider = _provider(request)
    if provider is None:
        emit_test_connection_diagnostic(
            "TEST_CONNECTION_ROUTE_FAILURE",
            result="HTTP_503",
            provider=configured_provider,
        )
        return _error(request, 503, "PROVIDER_CONFIGURATION_UNAVAILABLE")
    connection, error = _legacy_connection(
        request,
        provider,
        configured_provider,
        allow_unconfigured_onboarding=payload is not None,
    )
    if error is not None:
        return error
    try:
        if payload is None and connection is None:
            result = provider.test_connection()
        elif payload is None:
            # Preserve the existing provider projection contract when the
            # resolved legacy provider is the current provider.  The service
            # still resolves the unique active connection internally; an
            # explicit connection id is required when a query selected a
            # different provider.
            result = (
                provider.test_connection()
                if connection.provider_id == provider.current_provider
                else provider.test_connection(connection_id=connection.connection_id)
            )
        else:
            draft = payload.model_dump(exclude_none=True)
            if connection is not None:
                draft["connection_id"] = connection.connection_id
            result = provider.test_connection(draft=draft)
    except CredentialUnavailable as error:
        emit_test_connection_diagnostic(
            "TEST_CONNECTION_ROUTE_FAILURE",
            result="HTTP_503",
            error=error,
            provider=configured_provider,
        )
        return _error(request, 503, "PROVIDER_CREDENTIAL_STORE_UNAVAILABLE")
    except OSError as error:
        emit_test_connection_diagnostic(
            "TEST_CONNECTION_ROUTE_FAILURE",
            result="HTTP_503",
            error=error,
            provider=configured_provider,
        )
        return _error(request, 503, "PROVIDER_CONFIGURATION_UNAVAILABLE")
    except (TypeError, ValueError) as error:
        emit_test_connection_diagnostic(
            "TEST_CONNECTION_ROUTE_FAILURE",
            result="HTTP_503",
            error=error,
            provider=configured_provider,
        )
        return _error(request, 503, "PROVIDER_CONFIGURATION_UNAVAILABLE")
    emit_test_connection_diagnostic(
        "TEST_CONNECTION_ROUTE_SUCCESS",
        result="HTTP_200",
        provider=configured_provider,
    )
    _sync_provider_runtime_projection(
        request, provider, provider.projection() if payload is not None else result
    )
    return result


@router.get("/provider-verifications/latest", response_model=ProviderVerificationReceipt)
def latest_provider_verification(
    request: Request,
    provider_id: str | None = None,
    model_id: str | None = None,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
):
    del identity
    provider = _provider(request)
    if provider is None:
        return _error(request, 503, "PROVIDER_CONFIGURATION_UNAVAILABLE")
    connection, error = _legacy_connection(request, provider, provider_id)
    if error is not None:
        return error
    try:
        receipt = (
            provider.latest_verification(
                connection_id=connection.connection_id,
                model_id=model_id,
                include_legacy=True,
            )
            if connection is not None
            else provider.latest_verification(provider_id=provider_id, model_id=model_id)
        )
    except (TypeError, ValueError):
        return _error(request, 422, "PROVIDER_VERIFICATION_QUERY_INVALID")
    if receipt is None:
        return _error(request, 404, "PROVIDER_VERIFICATION_NOT_FOUND")
    return receipt


@router.get(
    "/provider-verifications",
    response_model=tuple[ProviderVerificationReceipt, ...],
)
def list_provider_verifications(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    provider_id: str | None = None,
    model_id: str | None = None,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
):
    del identity
    provider = _provider(request)
    if provider is None:
        return _error(request, 503, "PROVIDER_CONFIGURATION_UNAVAILABLE")
    connection, error = _legacy_connection(request, provider, provider_id)
    if error is not None:
        return error
    try:
        return (
            provider.recent_verifications(
                limit=limit,
                connection_id=connection.connection_id,
                model_id=model_id,
                include_legacy=True,
            )
            if connection is not None
            else provider.recent_verifications(
                limit=limit,
                provider_id=provider_id,
                model_id=model_id,
            )
        )
    except (TypeError, ValueError):
        return _error(request, 422, "PROVIDER_VERIFICATION_QUERY_INVALID")


@router.post("/provider/transmission-approvals")
def create_transmission_approval(
    payload: TransmissionApprovalInput,
    request: Request,
    identity: TrustedDesktopIdentity = Depends(desktop_identity),
):
    provider = _provider(request)
    approvals = _approvals(request)
    chat = getattr(request.app.state, "desktop_copilot_chat_runtime", None)
    if provider is None or approvals is None or chat is None:
        return _error(request, 503, "PROVIDER_CONFIGURATION_UNAVAILABLE")
    try:
        project = chat.project(
            payload.session_id,
            requester=identity.requester,
            tenant_id=identity.tenant_id,
        )
        if project.project_id != payload.project_id:
            return _error(request, 403, "EXTERNAL_TRANSMISSION_APPROVAL_MISMATCH")
        available = {
            reference.fingerprint
            for reference in chat.references(
                payload.session_id,
                requester=identity.requester,
                tenant_id=identity.tenant_id,
            )
        }
        if any(item not in available for item in payload.context_fingerprints):
            return _error(request, 403, "EXTERNAL_TRANSMISSION_APPROVAL_MISMATCH")
        receipt = approvals.issue(
            tenant_id=identity.tenant_id,
            requester=identity.requester,
            session_id=payload.session_id,
            project_id=payload.project_id,
            request_id=payload.request_id,
            reference_fingerprints=payload.context_fingerprints,
            decision=TransmissionDecision(payload.decision),
        )
        return receipt
    except Exception:
        return _error(request, 403, "EXTERNAL_TRANSMISSION_APPROVAL_MISMATCH")


@router.post("/backups")
def create_backup(request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    runtime = _runtime(request)
    if runtime is None:
        return _error(request, 503, "PRODUCTION_RELEASE_UNAVAILABLE")
    result = runtime.backups.create(tenant_id=identity.tenant_id)
    return {"backup_id": result.backup_id, "manifest_fingerprint": result.manifest_fingerprint, "status": "CREATED"}


@router.post("/diagnostics")
def export_diagnostics(request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    del identity
    runtime = _runtime(request)
    if runtime is None:
        return _error(request, 503, "PRODUCTION_RELEASE_UNAVAILABLE")
    archive = runtime.diagnostics.export(runtime.health())
    return {"diagnostic_id": archive.stem, "status": "EXPORTED"}


@router.post("/restore-authorizations")
def authorize_restore(payload: RestoreAuthorizationInput, request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    runtime = _runtime(request)
    if runtime is None:
        return _error(request, 503, "PRODUCTION_RELEASE_UNAVAILABLE")
    authorization = secrets.token_urlsafe(32)
    approvals = getattr(request.app.state, "v70_restore_authorizations", {})
    approvals[authorization] = (payload.backup_id, identity.tenant_id, identity.requester, datetime.now(UTC) + timedelta(minutes=5))
    request.app.state.v70_restore_authorizations = approvals
    return {"authorization": authorization, "expires_in_seconds": 300, "status": "ISSUED"}


@router.post("/restore")
def restore(payload: RestoreInput, request: Request, identity: TrustedDesktopIdentity = Depends(desktop_identity)):
    runtime = _runtime(request)
    if runtime is None:
        return _error(request, 503, "PRODUCTION_RELEASE_UNAVAILABLE")
    approvals = getattr(request.app.state, "v70_restore_authorizations", {})
    value = approvals.pop(payload.authorization, None)
    if value is None or value[0] != payload.backup_id or value[1] != identity.tenant_id or value[2] != identity.requester or value[3] <= datetime.now(UTC):
        return _error(request, 403, "RESTORE_AUTHORIZATION_REJECTED")
    try:
        fingerprint = runtime.backups.restore(backup_id=payload.backup_id, tenant_id=identity.tenant_id)
    except RestoreError:
        return _error(request, 422, "RESTORE_REJECTED")
    return {"status": "RESTORED", "manifest_fingerprint": fingerprint, "restart_required": True}


__all__ = ("router",)
