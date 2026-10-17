import { recordApprovalTrace, type ApprovalTraceSink } from "./approvalTrace";

import { DESKTOP_SESSION_EXPIRED, isDesktopSessionExpired, mapDesktopSessionError, readDesktopSessionErrorCode } from "./sessionState";
import {
  createProviderProjectionDiagnosticSession,
  classifyProviderFetcher,
  classifyFetchRejectionErrorName,
  classifyFetchRejectionMessageCategory,
  type ProviderProjectionDiagnosticSession,
  type FrontendProviderSafeErrorClass,
  type FrontendProviderFetchRejectionErrorClass,
  webViewDiagnosticOriginHeaders,
} from "./webviewSessionDiagnostic";

export type ProviderConnectionStatus =
  | "AVAILABLE"
  | "PROVIDER_NOT_CONFIGURED"
  | "PROVIDER_AUTH_FAILED"
  | "PROVIDER_NETWORK_UNAVAILABLE"
  | "PROVIDER_MODEL_UNAVAILABLE"
  | "PROVIDER_RATE_LIMITED"
  | "PROVIDER_CONFIG_INVALID"
  | "PROVIDER_CONNECTION_UNVERIFIED";

export type ProviderConnectionDiagnostic = {
  stage: "CONFIGURATION" | "CREDENTIAL" | "DNS" | "TCP" | "TLS" | "HTTP" | "PROVIDER_RESPONSE";
  http_method: "POST";
  request_url: string | null;
  http_status: number | null;
  provider_error_code: string | null;
  error_code?: "CREDENTIAL_NOT_CONFIGURED" | "DNS_FAILED" | "TCP_FAILED" | "TLS_FAILED" | "HTTP_401" | "HTTP_403" | "HTTP_429" | "PROVIDER_ERROR" | "VERIFIED" | string;
  normalized_error: string;
  transport_error_code: string | null;
};

export type ProviderModelProjection = {
  provider_id: string;
  endpoint_id: string;
  credential_ref?: string;
  model_profile_id: string;
  model_id: string;
  display_name: string;
  availability: "AVAILABLE" | "RATE_LIMITED" | "PERMISSION_DENIED" | "MODEL_UNAVAILABLE" | "DEPRECATED" | "UNVERIFIED" | "UNKNOWN" | string;
  availability_status?: string;
  availability_source: string;
  availability_provenance?: string;
  catalog_source?: string;
  record_provenance?: string;
  identity_status?: string;
  verification_status?: string;
  can_enable?: boolean;
  enable_block_reason?: string | null;
  billing_class?: "FREE" | "PAID" | "MIXED" | "METERED" | "SUBSCRIPTION" | "LOCAL" | "UNKNOWN" | string;
  billing_provenance?: string;
  billing_source?: string | null;
  billing_label?: "免费" | "付费" | "费用未知" | string;
  lifecycle_status?: string;
  deprecated?: boolean;
  officially_confirmed?: boolean;
  metadata_updated_at?: string | null;
  metadata_source?: string | null;
  capabilities: string[];
  verified_at: string | null;
};

export type ProviderProfileProjection = {
  provider_id: string;
  configured: boolean;
  selected_model: string | null;
  credential_status: string;
  connection_status: string;
  discovered_model_count: number;
  usable_model_count: number;
};

export type ProviderProjection = {
  provider: string;
  model: string;
  configuration_state?: string;
  discovery_state?: string;
  discovery_required?: boolean;
  connection?: "verified" | "failed" | "unverified" | "not_configured" | string;
  base_url: string;
  credential_status: "CONFIGURED" | "NOT_CONFIGURED" | "UNAVAILABLE";
  connection_status: ProviderConnectionStatus;
  last_test_status: ProviderConnectionStatus;
  tested_at: string | null;
  /** Safe configuration metadata; the credential value is never projected. */
  updated_at?: string | null;
  validation_status?: ProviderConnectionStatus | string | null;
  configuration_fingerprint: string;
  tested_configuration_fingerprint: string | null;
  transmission_status: "NOT_APPROVED";
  restart_required: boolean;
  fingerprint: string;
  endpoint_id?: string | null;
  credential_ref?: string | null;
  protocol_family?: string | null;
  model_profile_id?: string | null;
  model_id?: string | null;
  verified_model?: string | null;
  available_models?: string[];
  model_pool?: ProviderModelProjection[];
  provider_profiles?: ProviderProfileProjection[];
  active_provider_id?: string | null;
  model_available?: boolean | null;
  response_model?: string | null;
  response_id?: string | null;
  latency_ms?: number | null;
  http_status?: number | null;
  protocol_adapter?: string | null;
  connection_diagnostic?: ProviderConnectionDiagnostic | null;
};

export type AccountUsageProjection = {
  provider_id: string;
  endpoint_id: string;
  credential_ref: string | null;
  current_balance: number | null;
  balance_status: "AVAILABLE" | "UNSUPPORTED" | "UNAVAILABLE" | "AUTH_FAILED" | "STALE" | "UNKNOWN" | string;
  balance_source: string | null;
  queried_at: string | null;
  last_known_balance: number | null;
  last_success_at: string | null;
  currency: string | null;
};

export class ProviderRequestError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "ProviderRequestError";
  }
}

const base = "/api/v1/copilot/desktop/system";

function reportDiagnostic(
  diagnostic: ProviderProjectionDiagnosticSession | undefined,
  callback: (value: ProviderProjectionDiagnosticSession) => void,
): void {
  if (diagnostic === undefined) return;
  try {
    callback(diagnostic);
  } catch {
    // Diagnostic failures are observational and must never change provider behavior.
  }
}

function fetchRejectionErrorClass(error: unknown): FrontendProviderSafeErrorClass {
  return typeof DOMException !== "undefined" && error instanceof DOMException && error.name === "AbortError"
    ? "REQUEST_ABORTED"
    : "FETCH_REJECTED";
}

function fetchRejectionEvidenceClass(error: unknown): FrontendProviderFetchRejectionErrorClass {
  if (error === null || error === undefined) return "UNKNOWN";
  if (typeof DOMException !== "undefined" && error instanceof DOMException) {
    return error.name === "AbortError" ? "ABORT_ERROR" : "DOM_EXCEPTION";
  }
  if (error instanceof TypeError) return "TYPE_ERROR";
  return "OTHER";
}

async function call<T>(
  path: string,
  init: RequestInit = {},
  fetcher: typeof fetch = fetch,
  diagnostic?: ProviderProjectionDiagnosticSession,
): Promise<T> {
  if (isDesktopSessionExpired()) {
    // No fetch Promise exists on this guarded path, so do not manufacture a
    // Promise rejection boundary event. The backend session diagnostic owns
    // this already-expired request state.
    throw new ProviderRequestError(DESKTOP_SESSION_EXPIRED);
  }
  const method = String(init.method ?? "GET").toUpperCase();
  reportDiagnostic(diagnostic, (value) => value.fetchBegin());
  reportDiagnostic(diagnostic, (value) => value.fetcherCallBegin(classifyProviderFetcher(fetcher)));
  let response: Response;
  try {
    response = await fetcher(`${base}${path}`, {
      credentials: "same-origin",
      ...init,
      headers: {
        "Content-Type": "application/json",
        "X-Copilot-Requester": "desktop-user",
        "X-Environment-Tenant": "desktop-tenant",
        ...(method === "GET" ? webViewDiagnosticOriginHeaders() : {}),
        ...(init.headers ?? {}),
      },
    });
    reportDiagnostic(diagnostic, (value) => value.fetcherCallResolved());
  } catch (error: unknown) {
    const safeErrorClass = fetchRejectionErrorClass(error);
    const rejectionEvidenceClass = fetchRejectionEvidenceClass(error);
    const rejectionErrorName = classifyFetchRejectionErrorName(error);
    const rejectionMessageCategory = classifyFetchRejectionMessageCategory(error);
    const fetchFailureKind = rejectionErrorName === "AbortError" ? "ABORT" : "PROMISE_REJECTION";
    reportDiagnostic(diagnostic, (value) => {
      value.fetcherCallRejected(safeErrorClass);
      value.fetchPromiseRejected(safeErrorClass);
      value.fetchFailure(safeErrorClass, "FETCH", undefined, {
        fetchRejectionErrorClass: rejectionEvidenceClass,
        fetchRejectionErrorName: rejectionErrorName,
        fetchRejectionMessageCategory: rejectionMessageCategory,
        fetchFailureKind,
        fetchFailureStage: "FETCH_CALL",
      });
    });
    throw error;
  }
  reportDiagnostic(diagnostic, (value) => value.fetchPromiseResolved());
  reportDiagnostic(diagnostic, (value) => value.responseReceived(response.status));
  if (!response.ok) {
    reportDiagnostic(diagnostic, (value) => value.fetchFailure("HTTP_STATUS_ERROR", "HTTP_STATUS", response.status, {
      fetchFailureKind: "HTTP_STATUS",
      fetchFailureStage: "HTTP_STATUS",
    }));
    const payload = await response.json().catch(() => ({}));
    const code = mapDesktopSessionError(response.status, readDesktopSessionErrorCode(payload)) ?? "PROVIDER_CONFIGURATION_UNAVAILABLE";
    throw new ProviderRequestError(code);
  }
  try {
    const payload = await response.json() as T;
    reportDiagnostic(diagnostic, (value) => value.jsonParseSuccess());
    return payload;
  } catch (error: unknown) {
    reportDiagnostic(diagnostic, (value) => {
      value.jsonParseFailure();
      value.fetchFailure("JSON_PARSE_ERROR", "JSON_PARSE", response.status, {
        fetchFailureKind: "RESPONSE_BODY",
        fetchFailureStage: "RESPONSE_BODY",
      });
    });
    throw error;
  }
}

export const fetchProviderConfiguration = (
  fetcher: typeof fetch = fetch,
  diagnostic: ProviderProjectionDiagnosticSession = createProviderProjectionDiagnosticSession(),
  providerId?: string,
) => call<ProviderProjection>(
  providerId ? `/provider?provider_id=${encodeURIComponent(providerId)}` : "/provider",
  {},
  fetcher,
  diagnostic,
);

export const saveProviderConfiguration = (
  model: string | undefined,
  fetcher: typeof fetch = fetch,
  options: {
    provider?: string;
    connectionName?: string;
    baseUrl?: string;
    endpointId?: string;
    modelProfileId?: string;
    credentialRef?: string;
    protocolFamily?: string;
    configurationFingerprint?: string;
  } = {},
) => call<ProviderProjection>("/provider/configuration", {
  method: "PUT",
  body: JSON.stringify({
    provider: options.provider ?? "deepseek",
    ...(options.connectionName ? { connection_name: options.connectionName } : {}),
    ...(model ? { model } : {}),
    ...(options.baseUrl ? { base_url: options.baseUrl } : {}),
    ...(options.endpointId ? { endpoint_id: options.endpointId } : {}),
    ...(options.modelProfileId ? { model_profile_id: options.modelProfileId } : {}),
    ...(options.credentialRef ? { credential_ref: options.credentialRef } : {}),
    ...(options.protocolFamily ? { protocol_family: options.protocolFamily } : {}),
    ...(options.configurationFingerprint ? { configuration_fingerprint: options.configurationFingerprint } : {}),
  }),
}, fetcher);

export const saveProviderCredential = (
  secret: string,
  fetcher: typeof fetch = fetch,
  provider = "deepseek",
) => call<ProviderProjection>("/provider/credential", {
  method: "PUT",
  body: JSON.stringify({ provider, secret }),
}, fetcher);

export const removeProviderCredential = (
  fetcher: typeof fetch = fetch,
  provider = "deepseek",
) => call<ProviderProjection>(`/provider/credential?provider_id=${encodeURIComponent(provider)}`, { method: "DELETE" }, fetcher);

export const removeProviderConnection = (
  fetcher: typeof fetch = fetch,
  provider = "deepseek",
) => call<ProviderProjection>(`/provider/connection?provider_id=${encodeURIComponent(provider)}`, { method: "DELETE" }, fetcher);

export const testProviderConnection = (
  fetcher: typeof fetch = fetch,
  draft?: {
    providerId: string;
    endpointId: string;
    credentialRef: string;
    modelProfileId: string;
    modelId: string;
    baseUrl?: string;
    protocolFamily?: string;
    configurationFingerprint?: string;
  },
) => call<ProviderProjection>("/provider/test-connection", {
  method: "POST",
  ...(draft ? {
    body: JSON.stringify({
      provider_id: draft.providerId,
      endpoint_id: draft.endpointId,
      credential_ref: draft.credentialRef,
      model_profile_id: draft.modelProfileId,
      model_id: draft.modelId,
      ...(draft.baseUrl ? { base_url: draft.baseUrl } : {}),
      ...(draft.endpointId ? { endpoint_id: draft.endpointId } : {}),
      ...(draft.credentialRef ? { credential_ref: draft.credentialRef } : {}),
      ...(draft.protocolFamily ? { protocol_family: draft.protocolFamily } : {}),
      ...(draft.modelProfileId ? { model_profile_id: draft.modelProfileId } : {}),
      ...(draft.configurationFingerprint ? { configuration_fingerprint: draft.configurationFingerprint } : {}),
    }),
  } : {}),
}, fetcher);

export const refreshProviderModelPool = (
  fetcher: typeof fetch = fetch,
  providerId?: string,
) => call<ProviderProjection>(
  providerId
    ? `/provider/models/refresh?provider_id=${encodeURIComponent(providerId)}`
    : "/provider/models/refresh",
  { method: "POST" },
  fetcher,
);

export const fetchAccountUsage = (
  fetcher: typeof fetch = fetch,
) => call<AccountUsageProjection[]>("/usage/accounts", {}, fetcher);

export const refreshAccountUsage = (
  endpointId: string,
  fetcher: typeof fetch = fetch,
) => call<AccountUsageProjection>(`/usage/accounts/${encodeURIComponent(endpointId)}/refresh`, { method: "POST" }, fetcher);

export function createTransmissionApproval(
  input: {
    session_id: string;
    project_id: string;
    request_id: string;
    context_fingerprints: string[];
    decision: "APPROVED" | "DENIED";
  },
  fetcher: typeof fetch = fetch,
  traceSink: ApprovalTraceSink = (event) => recordApprovalTrace(
    event.stage,
    event.status,
    { reason_code: event.reason_code, http_status: event.http_status, decision: event.decision },
  ),
) {
  let requestStarted = false;
  let responseStatus: number | undefined;
  let responseReported = false;
  const tracedFetcher: typeof fetch = async (request, init) => {
    requestStarted = true;
    traceSink({
      event_name: "approval_runtime_trace",
      stage: "C3_APPROVAL_HTTP_REQUEST_SENT",
      status: "PASS",
    });
    const response = await fetcher(request, init);
    responseStatus = response.status;
    return response;
  };
  return call<{
    receipt_id: string;
    decision: "APPROVED" | "DENIED";
    context_fingerprint: string;
    provider_configuration_fingerprint: string;
    expires_at: string;
    fingerprint: string;
  }>("/provider/transmission-approvals", {
    method: "POST",
    body: JSON.stringify(input),
  }, tracedFetcher).then((receipt) => {
    if (!receipt.receipt_id) {
      responseReported = true;
      traceSink({
        event_name: "approval_runtime_trace",
        stage: "C4_APPROVAL_HTTP_RESPONSE",
        status: "FAIL",
        http_status: responseStatus,
        reason_code: "APPROVAL_RECEIPT_INVALID",
      });
      traceSink({
        event_name: "approval_runtime_trace",
        stage: "C5_APPROVAL_RECEIPT_CREATED",
        status: "FAIL",
        reason_code: "APPROVAL_RECEIPT_INVALID",
      });
      throw new ProviderRequestError("APPROVAL_RECEIPT_INVALID");
    }
    responseReported = true;
    traceSink({
      event_name: "approval_runtime_trace",
      stage: "C4_APPROVAL_HTTP_RESPONSE",
      status: "PASS",
      http_status: responseStatus,
    });
    traceSink({
      event_name: "approval_runtime_trace",
      stage: "C5_APPROVAL_RECEIPT_CREATED",
      status: "PASS",
    });
    return receipt;
  }).catch((error: unknown) => {
    if (!requestStarted) {
      traceSink({
        event_name: "approval_runtime_trace",
        stage: "C3_APPROVAL_HTTP_REQUEST_SENT",
        status: "FAIL",
        reason_code: "APPROVAL_HTTP_REQUEST_NOT_STARTED",
      });
    }
    if (!responseReported) {
      traceSink({
        event_name: "approval_runtime_trace",
        stage: "C4_APPROVAL_HTTP_RESPONSE",
        status: "FAIL",
        http_status: responseStatus,
        reason_code: error instanceof ProviderRequestError ? error.code : "APPROVAL_HTTP_FAILED",
      });
    }
    throw error;
  });
}

// Canonical Task D connection operations are re-exported here for callers that
// already depend on the provider client module. The legacy projection helpers
// above remain source-compatible but are not used by the Desktop connection UI.
export {
  createProviderConnection,
  deleteProviderConnection,
  fetchProviderConnection,
  fetchProviderConnectionUsage,
  fetchProviderConnections,
  patchProviderConnection,
  refreshProviderConnectionModels,
  refreshProviderConnectionUsage,
  saveProviderConnectionCredential,
  testProviderConnectionModel,
  ProviderConnectionRequestError,
} from "./providerConnectionClient";
export type {
  ProviderConnection,
  ProviderConnectionCredential,
  ProviderConnectionDelete,
  ProviderConnectionModel,
  ProviderConnectionRefresh,
  ProviderConnectionUsage,
} from "./providerConnectionClient";
