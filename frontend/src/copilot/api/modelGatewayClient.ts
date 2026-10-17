export type ModelGatewayProvider = {
  provider_id: string;
  display_name: string;
  provider_kind: string;
  supported_protocols: string[];
  preset_status: string;
  enabled: boolean;
};

export type ModelGatewayProviderTemplate = ModelGatewayProvider & {
  default_endpoint_template: string | null;
  model_discovery_mode: string;
};

export class ModelGatewayRequestError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "ModelGatewayRequestError";
    this.code = code;
  }
}

export type ModelCapabilityProbeResult = {
  model_profile_id: string;
  probe_type: string;
  status: "PASS" | "FAIL" | "UNAVAILABLE" | string;
};

export type ModelInvocationBundleRoles = {
  budget_phase?: "PREPARED" | "COUNTER_INTENT" | "COUNTER_OBSERVED" | "GENERATION_INTENT" | "TERMINAL";
  counter_admission_fingerprint?: string | null;
  counter_observation_fingerprint?: string | null;
  counter_status?: "NOT_ATTEMPTED" | "DISPATCH_INTENT" | "COUNTED" | "HTTP_ERROR" | "INVALID_RESPONSE" | "OUTCOME_UNKNOWN";
  counter_transport_send_count?: number | null;
  counter_response_observed?: boolean;
  generation_transport_send_count?: number | null;
  total_external_request_count?: number | null;
  counter_http_status?: number | null;
  counter_input_tokens?: number | null;
};

export type ModelInvocationReceipt = ModelInvocationBundleRoles & {
  budget_envelope_id?: string | null;
  budget_evidence_fingerprint?: string | null;
  budget_reservation_id?: string | null;
  budget_reconciliation_state?: "REPORTED" | "ESTIMATED" | "UNKNOWN" | "BOUND_VIOLATION";
  request_id: string;
  trace_id: string | null;
  project_id: string;
  stage_id: string;
  policy_source: string;
  connection_id: string | null;
  selected_model: { provider_id: string; endpoint_id: string; model_profile_id: string; model_id: string } | null;
  result: string;
  error_code: string | null;
  started_at: string;
  completed_at: string | null;
  fingerprint: string;
  usage: ModelInvocationUsage | null;
  invocation_mode: "CHAT" | "TASK" | "LEGACY_UNBOUND";
  authority_reference: ModelInvocationAuthority | null;
  gateway_call_count: number | null;
  gateway_response_observed: boolean | null;
  transport_send_count: number | null;
};

export type ModelInvocationAuthority = {
  tenant_id: string;
  project_id: string;
  engineering_task_id: string;
  task_request_fingerprint: string;
  coordination_fingerprint: string;
  plan_id: string;
  plan_content_fingerprint: string;
  review_fingerprint: string;
  phase_id: string;
  phase_fingerprint: string;
  plan_task_id: string;
  operation_id: string;
  operation_binding_fingerprint: string;
};

export type ModelInvocationUsage = {
  provider_reported_cost?: number | null;
  estimated_cost?: number | null;
  currency?: string | null;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cached_input_tokens: number | null;
  cache_write_tokens: number | null;
  reasoning_tokens: number | null;
  usage_source: string;
};

export type ModelInvocationReceiptPage = {
  items: ModelInvocationReceipt[];
  total: number;
  limit: number;
  offset: number;
};

export type ModelInvocationAttempt = ModelInvocationBundleRoles & {
  budget_envelope_id?: string | null;
  budget_evidence_fingerprint?: string | null;
  budget_reservation_id?: string | null;
  budget_reconciliation_state?: "REPORTED" | "ESTIMATED" | "UNKNOWN" | "BOUND_VIOLATION";
  request_id: string;
  project_id: string;
  stage_id: string;
  policy_source: string;
  connection_id: string;
  status: "PREPARED" | "DISPATCH_INTENT" | "OUTCOME_UNKNOWN" | "COMPLETED";
  confirmed_provider_request_count: number | null;
  receipt_fingerprint: string | null;
  invocation_mode: "CHAT" | "TASK" | "LEGACY_UNBOUND";
  authority_reference: ModelInvocationAuthority | null;
  gateway_call_count: number | null;
  gateway_response_observed: boolean | null;
  transport_send_count: number | null;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function nonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function invocationEvidence(value: Record<string, unknown>): Pick<ModelInvocationAttempt, "invocation_mode" | "authority_reference" | "gateway_call_count" | "gateway_response_observed" | "transport_send_count"> & ModelInvocationBundleRoles | null {
  const mode = value.invocation_mode ?? "LEGACY_UNBOUND";
  if (mode !== "CHAT" && mode !== "TASK" && mode !== "LEGACY_UNBOUND") return null;
  const raw = value.authority_reference;
  if (mode === "TASK" && (!isRecord(raw) || ["tenant_id", "project_id", "engineering_task_id", "task_request_fingerprint", "coordination_fingerprint", "plan_id", "plan_content_fingerprint", "review_fingerprint", "phase_id", "phase_fingerprint", "plan_task_id", "operation_id", "operation_binding_fingerprint"].some((name) => typeof raw[name] !== "string" || !raw[name]) || raw.project_id !== value.project_id)) return null;
  if (mode !== "TASK" && raw !== null && raw !== undefined) return null;
  const gateway = value.gateway_call_count ?? null;
  const transport = value.transport_send_count ?? null;
  const observed = value.gateway_response_observed ?? null;
  if (!(gateway === null || gateway === 0 || gateway === 1) || !(transport === null || nonNegativeInteger(transport))) return null;
  if (mode === "LEGACY_UNBOUND" ? observed !== null : typeof observed !== "boolean") return null;
  if (observed === true && gateway !== 1) return null;
  const roles = bundleProjection(value);
  if (roles === null) return null;
  return { ...roles, invocation_mode: mode, authority_reference: mode === "TASK" ? raw as ModelInvocationAuthority : null, gateway_call_count: gateway, gateway_response_observed: observed as boolean | null, transport_send_count: transport };
}

function bundleProjection(value: Record<string, unknown>): ModelInvocationBundleRoles | null {
  if (value.budget_phase === undefined) return {};
  if (value.invocation_mode !== "TASK" || !["PREPARED", "COUNTER_INTENT", "COUNTER_OBSERVED", "GENERATION_INTENT", "TERMINAL"].includes(String(value.budget_phase)) || !["NOT_ATTEMPTED", "DISPATCH_INTENT", "COUNTED", "HTTP_ERROR", "INVALID_RESPONSE", "OUTCOME_UNKNOWN"].includes(String(value.counter_status)) || typeof value.counter_response_observed !== "boolean") return null;
  for (const key of ["counter_transport_send_count", "generation_transport_send_count"] as const) if (!(value[key] === null || value[key] === 0 || value[key] === 1)) return null;
  const counter = value.counter_transport_send_count as number | null;
  const generation = value.generation_transport_send_count as number | null;
  const total = counter === null || generation === null ? null : counter + generation;
  if (value.total_external_request_count !== total || value.transport_send_count !== generation) return null;
  for (const key of ["counter_admission_fingerprint", "counter_observation_fingerprint"] as const) if (!(value[key] === null || typeof value[key] === "string" && /^sha256:[a-f0-9]{64}$/.test(value[key]))) return null;
  if (value.counter_http_status !== null && !(nonNegativeInteger(value.counter_http_status) && value.counter_http_status >= 100 && value.counter_http_status <= 599)) return null;
  if (value.counter_input_tokens !== null && !nonNegativeInteger(value.counter_input_tokens)) return null;
  if (value.counter_response_observed && (counter !== 1 || value.counter_http_status === null || value.counter_observation_fingerprint === null)) return null;
  if (!value.counter_response_observed && (value.counter_http_status !== null || value.counter_input_tokens !== null)) return null;
  if (value.counter_status === "COUNTED" && (!value.counter_response_observed || value.counter_http_status !== 200 || !nonNegativeInteger(value.counter_input_tokens))) return null;
  if (generation === 1 && (value.counter_status !== "COUNTED" || !nonNegativeInteger(value.counter_input_tokens) || value.counter_input_tokens > 8192 || typeof value.budget_evidence_fingerprint !== "string")) return null;
  const names = ["budget_phase", "counter_admission_fingerprint", "counter_observation_fingerprint", "counter_status", "counter_transport_send_count", "counter_response_observed", "generation_transport_send_count", "total_external_request_count", "counter_http_status", "counter_input_tokens"];
  return Object.fromEntries(names.map((name) => [name, value[name]])) as ModelInvocationBundleRoles;
}

function budgetProjection(value: Record<string, unknown>): Partial<Pick<ModelInvocationReceipt, "budget_envelope_id" | "budget_evidence_fingerprint" | "budget_reservation_id" | "budget_reconciliation_state">> | null {
  const names = ["budget_envelope_id", "budget_evidence_fingerprint", "budget_reservation_id"] as const;
  const entries = names.map((name) => value[name]);
  const state = value.budget_reconciliation_state;
  if (entries.every((entry) => entry === undefined) && state === undefined) return {};
  if (value.invocation_mode !== "TASK") return null;
  if (!(entries.every((entry) => entry == null) || entries.every((entry) => typeof entry === "string" && entry.length > 0) || value.budget_phase !== undefined && typeof entries[0] === "string" && typeof entries[2] === "string" && entries[1] == null)) return null;
  if (typeof value.budget_evidence_fingerprint === "string" && !/^sha256:[a-f0-9]{64}$/.test(value.budget_evidence_fingerprint)) return null;
  if (state !== undefined && !["REPORTED", "ESTIMATED", "UNKNOWN", "BOUND_VIOLATION"].includes(String(state))) return null;
  return Object.fromEntries([...names, "budget_reconciliation_state"].filter((name) => value[name] !== undefined).map((name) => [name, value[name]])) as Partial<Pick<ModelInvocationReceipt, "budget_envelope_id" | "budget_evidence_fingerprint" | "budget_reservation_id" | "budget_reconciliation_state">>;
}

function usageFromBody(value: unknown): ModelInvocationUsage | null | undefined {
  if (value === null || value === undefined) return null;
  if (!isRecord(value) || !nonNegativeInteger(value.input_tokens) || !nonNegativeInteger(value.output_tokens) || !nonNegativeInteger(value.total_tokens) || value.total_tokens !== value.input_tokens + value.output_tokens || typeof value.usage_source !== "string" || !value.usage_source.trim()) return undefined;
  for (const key of ["provider_reported_cost", "estimated_cost"] as const) {
    if (value[key] != null && (typeof value[key] !== "number" || !Number.isFinite(value[key]) || value[key] < 0)) return undefined;
  }
  if (value.currency != null && (typeof value.currency !== "string" || !value.currency.trim())) return undefined;
  const cost = Object.fromEntries(["provider_reported_cost", "estimated_cost", "currency"].filter((key) => value[key] !== undefined).map((key) => [key, value[key]]));
  const optional = [value.cached_input_tokens, value.cache_write_tokens, value.reasoning_tokens];
  if (optional.some((item) => item !== null && item !== undefined && !nonNegativeInteger(item))) return undefined;
  const optionalToken = (item: unknown): number | null => item == null ? null : item as number;
  return {
    input_tokens: value.input_tokens,
    output_tokens: value.output_tokens,
    total_tokens: value.total_tokens,
    cached_input_tokens: optionalToken(value.cached_input_tokens),
    cache_write_tokens: optionalToken(value.cache_write_tokens),
    reasoning_tokens: optionalToken(value.reasoning_tokens),
    usage_source: value.usage_source,
    ...cost,
  };
}

function receiptFromBody(value: unknown, projectId: string): ModelInvocationReceipt | null {
  if (!isRecord(value) || value.project_id !== projectId || typeof value.request_id !== "string" || !value.request_id || typeof value.stage_id !== "string" || typeof value.policy_source !== "string" || !(typeof value.connection_id === "string" && value.connection_id.trim() || value.connection_id === null) || typeof value.result !== "string" || typeof value.started_at !== "string" || typeof value.fingerprint !== "string" || !(typeof value.trace_id === "string" || value.trace_id === null) || !(typeof value.completed_at === "string" || value.completed_at === null) || !(typeof value.error_code === "string" || value.error_code === null)) return null;
  const selected = value.selected_model;
  if (!(selected === null || isRecord(selected) && typeof selected.provider_id === "string" && typeof selected.endpoint_id === "string" && typeof selected.model_profile_id === "string" && typeof selected.model_id === "string")) return null;
  const budget = budgetProjection(value);
  if (budget === null) return null;
  const usage = usageFromBody(value.usage);
  if (usage === undefined) return null;
  const evidence = invocationEvidence(value);
  if (evidence === null) return null;
  return {
    request_id: value.request_id,
    trace_id: value.trace_id as string | null,
    project_id: projectId,
    stage_id: value.stage_id,
    policy_source: value.policy_source,
    connection_id: value.connection_id as string | null,
    selected_model: selected === null ? null : { provider_id: selected.provider_id as string, endpoint_id: selected.endpoint_id as string, model_profile_id: selected.model_profile_id as string, model_id: selected.model_id as string },
    result: value.result,
    error_code: value.error_code as string | null,
    started_at: value.started_at,
    completed_at: value.completed_at as string | null,
    fingerprint: value.fingerprint,
    usage,
    ...evidence,
    ...budget,
  };
}

export async function fetchModelInvocationReceipts(projectId: string, chatRequestId: string, fetcher: typeof fetch = fetch, limit = 20, offset = 0): Promise<ModelInvocationReceiptPage> {
  if (!projectId || !chatRequestId || !Number.isInteger(limit) || limit < 1 || limit > 100 || !Number.isInteger(offset) || offset < 0) throw new ModelGatewayRequestError("MODEL_INVOCATION_RECEIPTS_INVALID_REQUEST");
  const query = new URLSearchParams({ chat_request_id: chatRequestId, limit: String(limit), offset: String(offset) });
  let response: Response;
  try {
    response = await fetcher(`/api/v1/model-gateway/invocations/projects/${encodeURIComponent(projectId)}/receipts?${query.toString()}`, { credentials: "same-origin" });
  } catch {
    throw new ModelGatewayRequestError("MODEL_INVOCATION_RECEIPTS_NETWORK_UNAVAILABLE");
  }
  if (response.status === 403) throw new ModelGatewayRequestError("MODEL_INVOCATION_RECEIPTS_FORBIDDEN");
  if (response.status === 404) throw new ModelGatewayRequestError("MODEL_INVOCATION_RECEIPTS_NOT_FOUND");
  if (!response.ok) throw new ModelGatewayRequestError("MODEL_INVOCATION_RECEIPTS_UNAVAILABLE");
  let body: unknown;
  try { body = await response.json(); } catch { throw new ModelGatewayRequestError("MODEL_INVOCATION_RECEIPTS_INVALID_RESPONSE"); }
  if (!isRecord(body) || !Array.isArray(body.items) || !Number.isInteger(body.total) || !Number.isInteger(body.limit) || !Number.isInteger(body.offset) || (body.total as number) < 0 || body.limit !== limit || body.offset !== offset) throw new ModelGatewayRequestError("MODEL_INVOCATION_RECEIPTS_INVALID_RESPONSE");
  const items = body.items.map((item) => receiptFromBody(item, projectId));
  if (items.some((item) => item === null)) throw new ModelGatewayRequestError("MODEL_INVOCATION_RECEIPTS_INVALID_RESPONSE");
  return { items: items as ModelInvocationReceipt[], total: body.total as number, limit, offset };
}

export async function fetchModelInvocationAttempt(projectId: string, chatRequestId: string, fetcher: typeof fetch = fetch): Promise<ModelInvocationAttempt | null> {
  if (!projectId || !chatRequestId) throw new ModelGatewayRequestError("MODEL_INVOCATION_ATTEMPTS_INVALID_REQUEST");
  const requestId = `request-${chatRequestId}`;
  let response: Response;
  try {
    response = await fetcher(`/api/v1/model-gateway/invocations/projects/${encodeURIComponent(projectId)}/attempts/${encodeURIComponent(requestId)}`, { credentials: "same-origin" });
  } catch {
    throw new ModelGatewayRequestError("MODEL_INVOCATION_ATTEMPTS_NETWORK_UNAVAILABLE");
  }
  if (response.status === 404) return null;
  if (response.status === 403) throw new ModelGatewayRequestError("MODEL_INVOCATION_ATTEMPTS_FORBIDDEN");
  if (!response.ok) throw new ModelGatewayRequestError("MODEL_INVOCATION_ATTEMPTS_UNAVAILABLE");
  let value: unknown;
  try { value = await response.json(); } catch { throw new ModelGatewayRequestError("MODEL_INVOCATION_ATTEMPTS_INVALID_RESPONSE"); }
  if (!isRecord(value) || value.project_id !== projectId || value.request_id !== requestId || typeof value.stage_id !== "string" || !value.stage_id || typeof value.policy_source !== "string" || !value.policy_source || typeof value.connection_id !== "string" || !value.connection_id || !["PREPARED", "DISPATCH_INTENT", "OUTCOME_UNKNOWN", "COMPLETED"].includes(String(value.status)) || !(value.confirmed_provider_request_count === null || value.confirmed_provider_request_count === 1) || !(value.receipt_fingerprint === null || typeof value.receipt_fingerprint === "string")) throw new ModelGatewayRequestError("MODEL_INVOCATION_ATTEMPTS_INVALID_RESPONSE");
  const evidence = invocationEvidence(value);
  if (evidence === null) throw new ModelGatewayRequestError("MODEL_INVOCATION_ATTEMPTS_INVALID_RESPONSE");
  const budget = budgetProjection(value);
  if (budget === null) throw new ModelGatewayRequestError("MODEL_INVOCATION_ATTEMPTS_INVALID_RESPONSE");
  const completed = value.status === "COMPLETED";
  const receiptBound = typeof value.receipt_fingerprint === "string" && value.receipt_fingerprint.length > 0;
  if (value.budget_phase !== undefined) {
    if (completed && !receiptBound || receiptBound && value.budget_phase !== "TERMINAL" || receiptBound && !["COMPLETED", "OUTCOME_UNKNOWN"].includes(String(value.status))) throw new ModelGatewayRequestError("MODEL_INVOCATION_ATTEMPTS_INVALID_RESPONSE");
  } else {
    if (completed !== receiptBound) throw new ModelGatewayRequestError("MODEL_INVOCATION_ATTEMPTS_INVALID_RESPONSE");
    if (completed && (evidence.invocation_mode === "LEGACY_UNBOUND" ? value.confirmed_provider_request_count !== 1 : value.confirmed_provider_request_count !== null || evidence.gateway_call_count !== 1 || !evidence.gateway_response_observed)) throw new ModelGatewayRequestError("MODEL_INVOCATION_ATTEMPTS_INVALID_RESPONSE");
  }
  return {
    request_id: requestId,
    project_id: projectId,
    stage_id: value.stage_id,
    policy_source: value.policy_source,
    connection_id: value.connection_id,
    status: value.status as ModelInvocationAttempt["status"],
    confirmed_provider_request_count: value.confirmed_provider_request_count as number | null,
    receipt_fingerprint: value.receipt_fingerprint as string | null,
    ...evidence,
    ...budget,
  };
}

export async function probeModelTextCapability(modelProfileId: string, fetcher: typeof fetch = fetch): Promise<ModelCapabilityProbeResult> {
  let response: Response;
  try {
    response = await fetcher(`/api/v1/model-gateway/models/${encodeURIComponent(modelProfileId)}/capability-probes/TEXT`, {
      method: "POST",
      credentials: "same-origin",
    });
  } catch {
    throw new ModelGatewayRequestError("CAPABILITY_PROBE_NETWORK_UNAVAILABLE");
  }
  if (!response.ok) {
    let code = "CAPABILITY_PROBE_UNAVAILABLE";
    try {
      const body = await response.json() as { detail?: unknown };
      if (typeof body.detail === "string" && body.detail.trim()) code = body.detail;
    } catch {
      // Keep a stable code for non-JSON failures.
    }
    throw new ModelGatewayRequestError(code);
  }
  const body: unknown = await response.json();
  if (!body || typeof body !== "object") throw new ModelGatewayRequestError("CAPABILITY_PROBE_INVALID_RESPONSE");
  const result = body as Record<string, unknown>;
  if (result.model_profile_id !== modelProfileId || result.probe_type !== "TEXT" || typeof result.status !== "string") {
    throw new ModelGatewayRequestError("CAPABILITY_PROBE_INVALID_RESPONSE");
  }
  return { model_profile_id: modelProfileId, probe_type: "TEXT", status: result.status };
}

export async function fetchModelGatewayProviders(fetcher: typeof fetch = fetch): Promise<ModelGatewayProvider[]> {
  let response: Response;
  try {
    response = await fetcher("/api/v1/model-gateway/providers", { credentials: "same-origin" });
  } catch {
    throw new ModelGatewayRequestError("MODEL_GATEWAY_NETWORK_UNAVAILABLE");
  }
  if (!response.ok) {
    let code = "MODEL_GATEWAY_UNAVAILABLE";
    try {
      const body = await response.json() as { detail?: unknown };
      if (typeof body.detail === "string" && body.detail.trim()) code = body.detail;
    } catch {
      // Keep a fixed safe error code when the response is not JSON.
    }
    throw new ModelGatewayRequestError(code);
  }
  const body: unknown = await response.json();
  if (!Array.isArray(body)) throw new ModelGatewayRequestError("MODEL_GATEWAY_INVALID_RESPONSE");
  return body.flatMap((item): ModelGatewayProvider[] => {
    if (!item || typeof item !== "object") return [];
    const value = item as Record<string, unknown>;
    if (typeof value.provider_id !== "string" || typeof value.display_name !== "string" || typeof value.provider_kind !== "string" || !Array.isArray(value.supported_protocols) || !value.supported_protocols.every((protocol) => typeof protocol === "string") || typeof value.preset_status !== "string" || typeof value.enabled !== "boolean") return [];
    return [{
      provider_id: value.provider_id,
      display_name: value.display_name,
      provider_kind: value.provider_kind,
      supported_protocols: value.supported_protocols,
      preset_status: value.preset_status,
      enabled: value.enabled,
    }];
  });
}

export async function fetchModelGatewayProviderTemplates(fetcher: typeof fetch = fetch): Promise<ModelGatewayProviderTemplate[]> {
  let response: Response;
  try {
    response = await fetcher("/api/v1/model-gateway/provider-templates", { credentials: "same-origin" });
  } catch {
    throw new ModelGatewayRequestError("MODEL_GATEWAY_NETWORK_UNAVAILABLE");
  }
  if (!response.ok) throw new ModelGatewayRequestError("MODEL_GATEWAY_UNAVAILABLE");
  const body: unknown = await response.json();
  if (!Array.isArray(body)) throw new ModelGatewayRequestError("MODEL_GATEWAY_INVALID_RESPONSE");
  return body.flatMap((item): ModelGatewayProviderTemplate[] => {
    if (!item || typeof item !== "object") return [];
    const value = item as Record<string, unknown>;
    if (
      typeof value.provider_id !== "string" ||
      typeof value.display_name !== "string" ||
      typeof value.provider_kind !== "string" ||
      !Array.isArray(value.supported_protocols) ||
      !value.supported_protocols.every((protocol) => typeof protocol === "string") ||
      typeof value.preset_status !== "string" ||
      typeof value.default_endpoint_template !== "string" && value.default_endpoint_template !== null ||
      typeof value.model_discovery_mode !== "string" ||
      typeof value.enabled !== "boolean"
    ) return [];
    return [{
      provider_id: value.provider_id,
      display_name: value.display_name,
      provider_kind: value.provider_kind,
      supported_protocols: value.supported_protocols,
      preset_status: value.preset_status,
      enabled: value.enabled,
      default_endpoint_template: value.default_endpoint_template,
      model_discovery_mode: value.model_discovery_mode,
    }];
  });
}
