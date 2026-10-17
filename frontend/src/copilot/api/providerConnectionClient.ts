import { DESKTOP_SESSION_EXPIRED, isDesktopSessionExpired, mapDesktopSessionError, readDesktopSessionErrorCode } from "./sessionState";

export type ProviderConnectionStatus =
  | "AVAILABLE"
  | "PROVIDER_NOT_CONFIGURED"
  | "PROVIDER_AUTH_FAILED"
  | "PROVIDER_NETWORK_UNAVAILABLE"
  | "PROVIDER_MODEL_UNAVAILABLE"
  | "PROVIDER_RATE_LIMITED"
  | "PROVIDER_CONFIG_INVALID"
  | "PROVIDER_CONNECTION_UNVERIFIED"
  | "DISABLED"
  | "QUARANTINED"
  | string;

export type ProviderConnection = {
  connection_id: string;
  connection_name: string;
  legacy_source?: string | null;
  provider_id: string;
  provider_display_name: string;
  status: ProviderConnectionStatus;
  health_status?: string;
  block_reason?: string | null;
  endpoint_status?: string;
  endpoint_observed_at?: string | null;
  endpoint_freshness?: string;
  credential_status?: string;
  credential_freshness?: string;
  discovery_observed_at?: string | null;
  discovery_freshness?: string;
  endpoint_id: string;
  endpoint_url: string;
  protocol_family: string;
  credential_ref: string | null;
  credential_configured: boolean;
  credential_revision: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
  discovery_state: string;
  discovery_required: boolean;
  current_model_count: number;
  verification: {
    total: number;
    current: number;
    historical: number;
    ambiguous_legacy: number;
    latest_status: string | null;
    latest_at: string | null;
  };
  usage: {
    status: string;
    current_balance: number | null;
    last_known_balance: number | null;
    queried_at: string | null;
  };
};

export type ProviderConnectionTestResponse = ProviderConnection & {
  test_result: {
    status: ProviderConnectionStatus;
    reason_code: string | null;
    tested_at: string | null;
  };
};

export type ProviderConnectionModel = {
  model_profile_id: string;
  provider_id: string;
  endpoint_id: string;
  remote_model_id: string;
  display_name: string;
  advertised_family: string;
  capabilities: string[];
  quality_tier: string;
  billing_class: string;
  billing_provenance: string;
  billing_source: string | null;
  catalog_source: string;
  lifecycle_status: string;
  deprecated: boolean;
  metadata_updated_at: string | null;
  metadata_source: string | null;
  enabled: boolean;
  verification_status: string;
};

export type ProviderConnectionRefresh = {
  connection_id: string;
  provider_id: string;
  endpoint_id: string;
  status: string;
  raw_model_count: number | null;
  normalized_model_count: number;
  models: ProviderConnectionModel[];
  diagnostic_summary: Record<string, unknown> | null;
  error_code: string | null;
};

export type ProviderConnectionUsage = {
  connection_id: string | null;
  provider_id: string;
  endpoint_id: string;
  credential_ref: string | null;
  billing_class: string;
  current_balance: number | null;
  balance_status: string;
  balance_source: string | null;
  queried_at: string | null;
  last_known_balance: number | null;
  last_success_at: string | null;
  balance: number | null;
  currency: string | null;
  usage_today: number | null;
  usage_month: number | null;
  budget_remaining: number | null;
  quota_remaining: number | null;
  reset_at: string | null;
  status: string;
  source: string | null;
};

export type ProviderConnectionCredential = {
  connection_id: string;
  credential_ref: string | null;
  configured: boolean;
  credential_revision: string | null;
};

export type ProviderConnectionDelete = {
  connection_id: string;
  status: string;
};

export type ProviderVerificationReceipt = {
  verification_id: string;
  provider_id: string;
  connection_id: string | null;
  endpoint_id?: string | null;
  model_id?: string | null;
  requested_at: string;
  completed_at: string;
  latency_ms: number | null;
  connection_status: string;
  verification_status: string;
  normalized_error_code: string | null;
};

export type ProviderConnectionTestRecord = {
  test_id: string;
  connection_id: string;
  provider_id: string;
  endpoint_id: string;
  credential_ref: string | null;
  credential_revision: string;
  protocol: string;
  tested_at: string;
  latency_ms: number | null;
  status: string;
  error_code: string | null;
  model_id?: string | null;
};

export type ProviderConnectionRuntimeHealth = {
  identity: {
    provider_id: string;
    endpoint_id: string;
    credential_ref: string;
    model_profile_id: string;
    model_id: string;
  };
  endpoint_status: string;
  last_success_at: string | null;
  last_failure_at: string | null;
  error_rate: number | null;
  latency_ms: number | null;
  quota_status: string;
  observed_at: string | null;
  sample_count: number;
  evidence_freshness?: string;
};

export class ProviderConnectionRequestError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "ProviderConnectionRequestError";
    this.code = code;
  }
}

const base = "/api/v1/model-gateway";

async function call<T>(path: string, init: RequestInit = {}, fetcher: typeof fetch = fetch): Promise<T> {
  if (isDesktopSessionExpired()) throw new ProviderConnectionRequestError(DESKTOP_SESSION_EXPIRED);
  let response: Response;
  try {
    response = await fetcher(`${base}${path}`, {
      credentials: "same-origin",
      ...init,
      headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
    });
  } catch {
    throw new ProviderConnectionRequestError("PROVIDER_CONNECTION_NETWORK_UNAVAILABLE");
  }
  if (!response.ok) {
    let code = "PROVIDER_CONNECTION_UNAVAILABLE";
    try {
      const body = await response.json() as unknown;
      code = mapDesktopSessionError(response.status, readDesktopSessionErrorCode(body)) ?? code;
    } catch {
      // Preserve a fixed safe error code for non-JSON responses.
    }
    throw new ProviderConnectionRequestError(code);
  }
  if (response.status === 204) return undefined as T;
  try {
    return await response.json() as T;
  } catch {
    throw new ProviderConnectionRequestError("PROVIDER_CONNECTION_INVALID_RESPONSE");
  }
}

export async function fetchProviderConnections(fetcher: typeof fetch = fetch): Promise<ProviderConnection[]> {
  const value = await call<unknown>("/provider-connections", {}, fetcher);
  if (!Array.isArray(value)) throw new ProviderConnectionRequestError("PROVIDER_CONNECTION_INVALID_RESPONSE");
  return value as ProviderConnection[];
}

export function fetchProviderConnection(connectionId: string, fetcher: typeof fetch = fetch): Promise<ProviderConnection> {
  return call<ProviderConnection>(`/provider-connections/${encodeURIComponent(connectionId)}`, {}, fetcher);
}

export function createProviderConnection(
  payload: {
    provider_id: string;
    connection_name: string;
    endpoint_url: string;
    protocol_family?: string;
  },
  fetcher: typeof fetch = fetch,
): Promise<ProviderConnection> {
  return call<ProviderConnection>("/provider-connections", { method: "POST", body: JSON.stringify(payload) }, fetcher);
}

export function patchProviderConnection(
  connectionId: string,
  payload: { connection_name?: string; endpoint_url?: string; protocol_family?: string; enabled?: boolean },
  fetcher: typeof fetch = fetch,
): Promise<ProviderConnection> {
  return call<ProviderConnection>(`/provider-connections/${encodeURIComponent(connectionId)}`, { method: "PATCH", body: JSON.stringify(payload) }, fetcher);
}

export function deleteProviderConnection(connectionId: string, fetcher: typeof fetch = fetch): Promise<ProviderConnectionDelete> {
  return call<ProviderConnectionDelete>(`/provider-connections/${encodeURIComponent(connectionId)}`, { method: "DELETE" }, fetcher);
}

export function saveProviderConnectionCredential(
  connectionId: string,
  secret: string,
  fetcher: typeof fetch = fetch,
): Promise<ProviderConnectionCredential> {
  return call<ProviderConnectionCredential>(`/provider-connections/${encodeURIComponent(connectionId)}/credential`, { method: "PUT", body: JSON.stringify({ secret }) }, fetcher);
}

export function refreshProviderConnectionModels(connectionId: string, fetcher: typeof fetch = fetch): Promise<ProviderConnectionRefresh> {
  return call<ProviderConnectionRefresh>(`/provider-connections/${encodeURIComponent(connectionId)}/models/refresh`, { method: "POST" }, fetcher);
}

export function testProviderConnectionModel(
  connectionId: string,
  payload: { model_profile_id: string; model_id: string },
  fetcher: typeof fetch = fetch,
): Promise<ProviderConnectionTestResponse> {
  return call<ProviderConnectionTestResponse>(`/provider-connections/${encodeURIComponent(connectionId)}/test-connection`, { method: "POST", body: JSON.stringify(payload) }, fetcher);
}

export async function fetchProviderConnectionVerifications(connectionId: string, fetcher: typeof fetch = fetch): Promise<ProviderVerificationReceipt[]> {
  const value = await call<unknown>(`/provider-connections/${encodeURIComponent(connectionId)}/verifications`, {}, fetcher);
  if (!Array.isArray(value)) throw new ProviderConnectionRequestError("PROVIDER_CONNECTION_INVALID_RESPONSE");
  return value as ProviderVerificationReceipt[];
}

export async function fetchProviderConnectionTestHistory(connectionId: string, fetcher: typeof fetch = fetch): Promise<ProviderConnectionTestRecord[]> {
  const value = await call<unknown>(`/provider-connections/${encodeURIComponent(connectionId)}/test-history`, {}, fetcher);
  if (!Array.isArray(value)) throw new ProviderConnectionRequestError("PROVIDER_CONNECTION_INVALID_RESPONSE");
  return value as ProviderConnectionTestRecord[];
}

export async function fetchProviderConnectionRuntimeHealth(connectionId: string, fetcher: typeof fetch = fetch): Promise<ProviderConnectionRuntimeHealth[]> {
  const value = await call<unknown>(`/provider-connections/${encodeURIComponent(connectionId)}/runtime-health`, {}, fetcher);
  if (!Array.isArray(value)) throw new ProviderConnectionRequestError("PROVIDER_CONNECTION_INVALID_RESPONSE");
  return value as ProviderConnectionRuntimeHealth[];
}

export function fetchProviderConnectionUsage(connectionId: string, fetcher: typeof fetch = fetch): Promise<ProviderConnectionUsage> {
  return call<ProviderConnectionUsage>(`/provider-connections/${encodeURIComponent(connectionId)}/usage`, {}, fetcher);
}

export function refreshProviderConnectionUsage(connectionId: string, fetcher: typeof fetch = fetch): Promise<ProviderConnectionUsage> {
  return call<ProviderConnectionUsage>(`/provider-connections/${encodeURIComponent(connectionId)}/usage/refresh`, { method: "POST" }, fetcher);
}

