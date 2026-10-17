const DIAGNOSTIC_QUERY_PARAMETER = "webview_session_diagnostic";
const DIAGNOSTIC_ORIGIN_HEADER = "X-Embrix-Diagnostic-Origin";
const FRONTEND_DIAGNOSTIC_ROUTE = "/api/v1/copilot/desktop/system/diagnostics/frontend-provider";
export const FRONTEND_DIAGNOSTIC_BUILD_ID = "P0-1G15H2-FE" as const;
let nextFrontendProviderFetchAttempt = 0;

export const FRONTEND_PROVIDER_DIAGNOSTIC_STAGES = [
  "WEBVIEW_FRONTEND_PROVIDER_FETCH_BEGIN",
  "WEBVIEW_FRONTEND_PROVIDER_FETCHER_CALL_BEGIN",
  "WEBVIEW_FRONTEND_PROVIDER_FETCHER_CALL_RESOLVED",
  "WEBVIEW_FRONTEND_PROVIDER_FETCHER_CALL_REJECTED",
  "WEBVIEW_FRONTEND_PROVIDER_FETCH_PROMISE_RESOLVED",
  "WEBVIEW_FRONTEND_PROVIDER_FETCH_PROMISE_REJECTED",
  "WEBVIEW_FRONTEND_PROVIDER_RESPONSE_RECEIVED",
  "WEBVIEW_FRONTEND_PROVIDER_JSON_PARSE_SUCCESS",
  "WEBVIEW_FRONTEND_PROVIDER_JSON_PARSE_FAILURE",
  "WEBVIEW_FRONTEND_PROVIDER_PROJECTION_PROCESSING_SUCCESS",
  "WEBVIEW_FRONTEND_PROVIDER_PROJECTION_PROCESSING_FAILURE",
  "WEBVIEW_FRONTEND_PROVIDER_STATE_COMMIT",
  "WEBVIEW_FRONTEND_PROVIDER_RENDER_STATE",
  "WEBVIEW_FRONTEND_PROVIDER_FETCH_SUCCESS",
  "WEBVIEW_FRONTEND_PROVIDER_FETCH_FAILURE",
  "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_BEGIN",
  "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_PROMISE_RESOLVED",
  "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_SUCCESS",
  "WEBVIEW_FRONTEND_PROJECT_LOCATIONS_FETCH_FAILURE",
  "WEBVIEW_FRONTEND_READINESS_PASS",
] as const;

export type FrontendProviderDiagnosticStage = typeof FRONTEND_PROVIDER_DIAGNOSTIC_STAGES[number];
export type FrontendProviderDiagnosticRoute = "PROVIDER_PROJECTION" | "PROJECT_LOCATIONS";
export type FrontendProviderDiagnosticResult =
  | "ENTERED"
  | "RECEIVED"
  | "SUCCESS"
  | "FAILURE"
  | "NOT_APPLICABLE";
export type FrontendProviderProjectionState =
  | "NULL"
  | "LOADING"
  | "RECEIVED"
  | "VALIDATED"
  | "CONFIGURED"
  | "UNCONFIGURED"
  | "UNAVAILABLE"
  | "ERROR";
export type FrontendProviderCredentialState = "CONFIGURED" | "UNCONFIGURED" | "UNKNOWN";
export type FrontendProviderSafeErrorClass =
  | "NONE"
  | "FETCH_REJECTED"
  | "NETWORK_TRANSPORT_ERROR"
  | "UNKNOWN_FETCH_REJECTION"
  | "HTTP_STATUS_ERROR"
  | "JSON_PARSE_ERROR"
  | "PROJECTION_PROCESSING_ERROR"
  | "REQUEST_ABORTED"
  | "STATE_PROCESSING_ERROR"
  | "DIAGNOSTIC_DELIVERY_ERROR"
  | "UNKNOWN";
export type FrontendProviderDiagnosticFailureStage =
  | "FETCH"
  | "HTTP_STATUS"
  | "JSON_PARSE"
  | "PROJECTION_PROCESSING"
  | "STATE_PROCESSING"
  | "UNKNOWN";
export type FrontendProviderFetchFailureStage =
  | "FETCH_CALL"
  | "HTTP_STATUS"
  | "RESPONSE_BODY"
  | "PROJECTION_PROCESSING"
  | "STATE_PROCESSING"
  | "UNKNOWN";
export type FrontendProviderFetchFailureKind =
  | "PROMISE_REJECTION"
  | "HTTP_STATUS"
  | "JSON_PARSE"
  | "RESPONSE_BODY"
  | "ABORT"
  | "OTHER"
  | "UNKNOWN";
export type FrontendProviderProjectionProcessingResult = "SUCCESS" | "FAILURE" | "NOT_APPLICABLE";
export type FrontendProviderFetcherIdentity =
  | "GLOBAL_BROWSER_FETCH"
  | "GLOBAL_THIS_FETCH"
  | "WINDOW_FETCH"
  | "CUSTOM_WRAPPER"
  | "INJECTED_FETCHER"
  | "OTHER"
  | "UNKNOWN";
export type FrontendProviderOriginClass =
  | "LOOPBACK_HTTP"
  | "LOCALHOST_HTTP"
  | "FILE"
  | "CUSTOM_SCHEME"
  | "HTTPS_REMOTE"
  | "OTHER"
  | "UNKNOWN";
export type FrontendProviderTargetOriginClass =
  | "SAME_ORIGIN"
  | "LOOPBACK_CROSS_ORIGIN"
  | "LOCALHOST_CROSS_ORIGIN"
  | "CUSTOM_TO_LOOPBACK"
  | "OTHER"
  | "UNKNOWN";
export type FrontendProviderRequestUrlStrategy =
  | "RELATIVE_SAME_ORIGIN"
  | "ABSOLUTE_CURRENT_LOOPBACK"
  | "ABSOLUTE_DIFFERENT_LOOPBACK"
  | "CUSTOM_BACKEND_ORIGIN"
  | "OTHER";
export type FrontendProviderCredentialsMode = "SAME_ORIGIN" | "INCLUDE" | "OMIT" | "DEFAULT" | "UNKNOWN";
export type FrontendProviderDocumentReadyState = "LOADING" | "INTERACTIVE" | "COMPLETE" | "UNKNOWN";
export type FrontendProviderNavigationState = "STABLE" | "NAVIGATING" | "UNKNOWN";
export type FrontendProviderAbortSignalState = "NONE" | "ATTACHED" | "UNKNOWN";
export type FrontendProviderAbortTriggerSource = "NONE" | "COMPONENT_CLEANUP" | "NAVIGATION" | "SESSION_BOOTSTRAP" | "TIMEOUT" | "OTHER" | "UNKNOWN";
export type FrontendProviderFetchRejectionErrorClass = "ABORT_ERROR" | "TYPE_ERROR" | "DOM_EXCEPTION" | "OTHER" | "UNKNOWN";
export type FrontendProviderFetchRejectionErrorName =
  | "TypeError"
  | "AbortError"
  | "NetworkError"
  | "SecurityError"
  | "InvalidStateError"
  | "NotAllowedError"
  | "Other"
  | "Unknown";
export type FrontendProviderFetchRejectionMessageCategory =
  | "FAILED_TO_FETCH"
  | "LOAD_FAILED"
  | "NETWORK_ERROR"
  | "ILLEGAL_INVOCATION"
  | "BLOCKED_BY_CLIENT_OR_POLICY"
  | "CONNECTION_REFUSED"
  | "CONNECTION_RESET"
  | "OTHER_SANITIZED"
  | "EMPTY"
  | "UNKNOWN";

export type FrontendProviderFetchContext = {
  production_fetcher_identity: FrontendProviderFetcherIdentity;
  frontend_origin_class: FrontendProviderOriginClass;
  provider_target_origin_class: FrontendProviderTargetOriginClass;
  frontend_provider_same_origin: boolean;
  provider_request_url_strategy: FrontendProviderRequestUrlStrategy;
  request_credentials_mode: FrontendProviderCredentialsMode;
  document_ready_state_at_fetch: FrontendProviderDocumentReadyState;
  navigation_state_at_fetch: FrontendProviderNavigationState;
  document_generation_at_fetch: number;
  document_generation_at_rejection?: number;
  navigation_started_during_fetch?: boolean;
  document_context_changed_during_fetch?: boolean;
  abort_signal_attached_to_provider_fetch: FrontendProviderAbortSignalState;
  abort_trigger_observed: boolean;
  abort_trigger_source: FrontendProviderAbortTriggerSource;
};

export type FrontendProviderDiagnosticEvent = {
  stage: FrontendProviderDiagnosticStage;
  result: FrontendProviderDiagnosticResult;
  route: FrontendProviderDiagnosticRoute;
  fetch_attempt_id?: string;
  event_sequence?: number;
  fetch_attempt_started_at?: string;
  fetch_attempt_finished_at?: string;
  fetch_attempt_duration_ms?: number;
  project_locations_attempt_started_at?: string;
  project_locations_attempt_finished_at?: string;
  project_locations_attempt_duration_ms?: number;
  frontend_fetch_resolved?: boolean;
  frontend_http_status?: number | "UNKNOWN";
  final_result?: "SUCCESS" | "FAILURE";
  http_status?: number;
  projection_state?: FrontendProviderProjectionState;
  credential_state?: FrontendProviderCredentialState;
  provider_present?: boolean;
  fetcher_identity?: FrontendProviderFetcherIdentity;
  production_fetcher_identity?: FrontendProviderFetcherIdentity;
  frontend_origin_class?: FrontendProviderOriginClass;
  provider_target_origin_class?: FrontendProviderTargetOriginClass;
  frontend_provider_same_origin?: boolean;
  provider_request_url_strategy?: FrontendProviderRequestUrlStrategy;
  request_credentials_mode?: FrontendProviderCredentialsMode;
  document_ready_state_at_fetch?: FrontendProviderDocumentReadyState;
  navigation_state_at_fetch?: FrontendProviderNavigationState;
  document_generation_at_fetch?: number;
  document_generation_at_rejection?: number;
  navigation_started_during_fetch?: boolean;
  document_context_changed_during_fetch?: boolean;
  abort_signal_attached_to_provider_fetch?: FrontendProviderAbortSignalState;
  abort_trigger_observed?: boolean;
  abort_trigger_source?: FrontendProviderAbortTriggerSource;
  fetch_rejection_error_class?: FrontendProviderFetchRejectionErrorClass;
  fetch_rejection_error_name?: FrontendProviderFetchRejectionErrorName;
  fetch_rejection_message_category?: FrontendProviderFetchRejectionMessageCategory;
  fetch_failure_kind?: FrontendProviderFetchFailureKind;
  fetch_failure_stage?: FrontendProviderFetchFailureStage;
  safe_error_class?: FrontendProviderSafeErrorClass;
  failure_stage?: FrontendProviderDiagnosticFailureStage;
  projection_processing_result?: FrontendProviderProjectionProcessingResult;
  request_id?: string;
  render_generation?: number;
};

export type FrontendProviderDiagnosticSink = (event: FrontendProviderDiagnosticEvent) => void | Promise<boolean>;

const PROJECTION_STATES = new Set<FrontendProviderProjectionState>([
  "NULL",
  "LOADING",
  "RECEIVED",
  "VALIDATED",
  "CONFIGURED",
  "UNCONFIGURED",
  "UNAVAILABLE",
  "ERROR",
]);
const CREDENTIAL_STATES = new Set<FrontendProviderCredentialState>(["CONFIGURED", "UNCONFIGURED", "UNKNOWN"]);
const SAFE_ERROR_CLASSES = new Set<FrontendProviderSafeErrorClass>([
  "NONE",
  "FETCH_REJECTED",
  "NETWORK_TRANSPORT_ERROR",
  "UNKNOWN_FETCH_REJECTION",
  "HTTP_STATUS_ERROR",
  "JSON_PARSE_ERROR",
  "PROJECTION_PROCESSING_ERROR",
  "REQUEST_ABORTED",
  "STATE_PROCESSING_ERROR",
  "DIAGNOSTIC_DELIVERY_ERROR",
  "UNKNOWN",
]);
const FAILURE_STAGES = new Set<FrontendProviderDiagnosticFailureStage>([
  "FETCH",
  "HTTP_STATUS",
  "JSON_PARSE",
  "PROJECTION_PROCESSING",
  "STATE_PROCESSING",
  "UNKNOWN",
]);
const PROJECTION_PROCESSING_RESULTS = new Set<FrontendProviderProjectionProcessingResult>([
  "SUCCESS",
  "FAILURE",
  "NOT_APPLICABLE",
]);
const FETCHER_IDENTITIES = new Set<FrontendProviderFetcherIdentity>([
  "GLOBAL_BROWSER_FETCH",
  "GLOBAL_THIS_FETCH",
  "WINDOW_FETCH",
  "CUSTOM_WRAPPER",
  "INJECTED_FETCHER",
  "OTHER",
  "UNKNOWN",
]);
const ORIGIN_CLASSES = new Set<FrontendProviderOriginClass>(["LOOPBACK_HTTP", "LOCALHOST_HTTP", "FILE", "CUSTOM_SCHEME", "HTTPS_REMOTE", "OTHER", "UNKNOWN"]);
const TARGET_ORIGIN_CLASSES = new Set<FrontendProviderTargetOriginClass>(["SAME_ORIGIN", "LOOPBACK_CROSS_ORIGIN", "LOCALHOST_CROSS_ORIGIN", "CUSTOM_TO_LOOPBACK", "OTHER", "UNKNOWN"]);
const URL_STRATEGIES = new Set<FrontendProviderRequestUrlStrategy>(["RELATIVE_SAME_ORIGIN", "ABSOLUTE_CURRENT_LOOPBACK", "ABSOLUTE_DIFFERENT_LOOPBACK", "CUSTOM_BACKEND_ORIGIN", "OTHER"]);
const CREDENTIALS_MODES = new Set<FrontendProviderCredentialsMode>(["SAME_ORIGIN", "INCLUDE", "OMIT", "DEFAULT", "UNKNOWN"]);
const READY_STATES = new Set<FrontendProviderDocumentReadyState>(["LOADING", "INTERACTIVE", "COMPLETE", "UNKNOWN"]);
const NAVIGATION_STATES = new Set<FrontendProviderNavigationState>(["STABLE", "NAVIGATING", "UNKNOWN"]);
const ABORT_SIGNAL_STATES = new Set<FrontendProviderAbortSignalState>(["NONE", "ATTACHED", "UNKNOWN"]);
const ABORT_SOURCES = new Set<FrontendProviderAbortTriggerSource>(["NONE", "COMPONENT_CLEANUP", "NAVIGATION", "SESSION_BOOTSTRAP", "TIMEOUT", "OTHER", "UNKNOWN"]);
const REJECTION_ERROR_CLASSES = new Set<FrontendProviderFetchRejectionErrorClass>(["ABORT_ERROR", "TYPE_ERROR", "DOM_EXCEPTION", "OTHER", "UNKNOWN"]);
const DIAGNOSTIC_ROUTES = new Set<FrontendProviderDiagnosticRoute>(["PROVIDER_PROJECTION", "PROJECT_LOCATIONS"]);
const REJECTION_ERROR_NAMES = new Set<FrontendProviderFetchRejectionErrorName>(["TypeError", "AbortError", "NetworkError", "SecurityError", "InvalidStateError", "NotAllowedError", "Other", "Unknown"]);
const REJECTION_MESSAGE_CATEGORIES = new Set<FrontendProviderFetchRejectionMessageCategory>(["FAILED_TO_FETCH", "LOAD_FAILED", "NETWORK_ERROR", "ILLEGAL_INVOCATION", "BLOCKED_BY_CLIENT_OR_POLICY", "CONNECTION_REFUSED", "CONNECTION_RESET", "OTHER_SANITIZED", "EMPTY", "UNKNOWN"]);
const FETCH_FAILURE_KINDS = new Set<FrontendProviderFetchFailureKind>(["PROMISE_REJECTION", "HTTP_STATUS", "JSON_PARSE", "RESPONSE_BODY", "ABORT", "OTHER", "UNKNOWN"]);
const FETCH_FAILURE_STAGES = new Set<FrontendProviderFetchFailureStage>(["FETCH_CALL", "HTTP_STATUS", "RESPONSE_BODY", "PROJECTION_PROCESSING", "STATE_PROCESSING", "UNKNOWN"]);
const REQUEST_ID_PATTERN = /^(?:webview-session-[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$/;
const FETCH_ATTEMPT_ID_PATTERN = /^provider-fetch-[1-9][0-9]{0,8}$/;
const DIAGNOSTIC_TIMESTAMP_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;

function diagnosticTimestamp(value: unknown): string | undefined {
  return typeof value === "string" && value.length === 24 && DIAGNOSTIC_TIMESTAMP_PATTERN.test(value) ? value : undefined;
}

function diagnosticHttpStatus(value: unknown): number | "UNKNOWN" | undefined {
  if (value === "UNKNOWN") return value;
  return typeof value === "number" && Number.isInteger(value) && value >= 100 && value <= 599 ? value : undefined;
}

function diagnosticClock(): number {
  return typeof globalThis.performance?.now === "function" ? globalThis.performance.now() : Date.now();
}

function safeErrorProperty(error: unknown, property: "name" | "message"): string | undefined {
  if (error === null || error === undefined || (typeof error !== "object" && typeof error !== "function")) return undefined;
  try {
    const value = (error as Record<string, unknown>)[property];
    return typeof value === "string" ? value : undefined;
  } catch {
    return undefined;
  }
}

export function classifyFetchRejectionErrorName(error: unknown): FrontendProviderFetchRejectionErrorName {
  if (error === null || error === undefined) return "Unknown";
  const name = safeErrorProperty(error, "name");
  if (name !== undefined && REJECTION_ERROR_NAMES.has(name as FrontendProviderFetchRejectionErrorName)) {
    return name as FrontendProviderFetchRejectionErrorName;
  }
  return "Other";
}

export function classifyFetchRejectionMessageCategory(error: unknown): FrontendProviderFetchRejectionMessageCategory {
  if (error === null || error === undefined) return "UNKNOWN";
  const message = safeErrorProperty(error, "message");
  if (message === undefined) return "UNKNOWN";
  const normalized = message.slice(0, 512).trim().toLowerCase();
  if (!normalized) return "EMPTY";
  if (normalized.includes("failed to fetch")) return "FAILED_TO_FETCH";
  if (normalized.includes("load failed")) return "LOAD_FAILED";
  if (normalized.includes("illegal invocation")) return "ILLEGAL_INVOCATION";
  if (/(connection refused|err_connection_refused|econnrefused)/i.test(normalized)) return "CONNECTION_REFUSED";
  if (/(connection reset|err_connection_reset|econnreset)/i.test(normalized)) return "CONNECTION_RESET";
  if (normalized.includes("network error")) return "NETWORK_ERROR";
  if (normalized.includes("blocked by client") || normalized.includes("blocked by policy")) return "BLOCKED_BY_CLIENT_OR_POLICY";
  return "OTHER_SANITIZED";
}

function isDiagnosticStage(value: unknown): value is FrontendProviderDiagnosticStage {
  return typeof value === "string" && (FRONTEND_PROVIDER_DIAGNOSTIC_STAGES as readonly string[]).includes(value);
}

function sanitizeFrontendProviderDiagnosticEvent(
  input: FrontendProviderDiagnosticEvent,
): Record<string, string | number | boolean> {
  const event: Record<string, string | number | boolean> = {
    build_id: FRONTEND_DIAGNOSTIC_BUILD_ID,
    stage: input.stage,
    result: input.result,
    route: input.route,
  };
  if (typeof input.fetch_attempt_id === "string" && FETCH_ATTEMPT_ID_PATTERN.test(input.fetch_attempt_id)) {
    event.fetch_attempt_id = input.fetch_attempt_id;
  }
  if (input.event_sequence !== undefined && Number.isInteger(input.event_sequence) && input.event_sequence >= 1 && input.event_sequence <= 1_000_000) {
    event.event_sequence = input.event_sequence;
  }
  const fetchStartedAt = diagnosticTimestamp(input.fetch_attempt_started_at);
  if (fetchStartedAt !== undefined) event.fetch_attempt_started_at = fetchStartedAt;
  const fetchFinishedAt = diagnosticTimestamp(input.fetch_attempt_finished_at);
  if (fetchFinishedAt !== undefined) event.fetch_attempt_finished_at = fetchFinishedAt;
  if (input.fetch_attempt_duration_ms !== undefined && Number.isInteger(input.fetch_attempt_duration_ms) && input.fetch_attempt_duration_ms >= 0 && input.fetch_attempt_duration_ms <= 86_400_000) {
    event.fetch_attempt_duration_ms = input.fetch_attempt_duration_ms;
  }
  const locationsStartedAt = diagnosticTimestamp(input.project_locations_attempt_started_at);
  if (locationsStartedAt !== undefined) event.project_locations_attempt_started_at = locationsStartedAt;
  const locationsFinishedAt = diagnosticTimestamp(input.project_locations_attempt_finished_at);
  if (locationsFinishedAt !== undefined) event.project_locations_attempt_finished_at = locationsFinishedAt;
  if (input.project_locations_attempt_duration_ms !== undefined && Number.isInteger(input.project_locations_attempt_duration_ms) && input.project_locations_attempt_duration_ms >= 0 && input.project_locations_attempt_duration_ms <= 86_400_000) {
    event.project_locations_attempt_duration_ms = input.project_locations_attempt_duration_ms;
  }
  if (input.frontend_fetch_resolved !== undefined && typeof input.frontend_fetch_resolved === "boolean") event.frontend_fetch_resolved = input.frontend_fetch_resolved;
  const frontendHttpStatus = diagnosticHttpStatus(input.frontend_http_status);
  if (frontendHttpStatus !== undefined) event.frontend_http_status = frontendHttpStatus;
  if (input.final_result === "SUCCESS" || input.final_result === "FAILURE") event.final_result = input.final_result;
  if (input.http_status !== undefined && Number.isInteger(input.http_status) && input.http_status >= 100 && input.http_status <= 599) {
    event.http_status = input.http_status;
  }
  if (input.projection_state !== undefined && PROJECTION_STATES.has(input.projection_state)) {
    event.projection_state = input.projection_state;
  }
  if (input.credential_state !== undefined && CREDENTIAL_STATES.has(input.credential_state)) {
    event.credential_state = input.credential_state;
  }
  if (input.provider_present !== undefined && typeof input.provider_present === "boolean") {
    event.provider_present = input.provider_present;
  }
  if (input.fetcher_identity !== undefined && FETCHER_IDENTITIES.has(input.fetcher_identity)) {
    event.fetcher_identity = input.fetcher_identity;
  }
  if (input.production_fetcher_identity !== undefined && FETCHER_IDENTITIES.has(input.production_fetcher_identity)) event.production_fetcher_identity = input.production_fetcher_identity;
  if (input.frontend_origin_class !== undefined && ORIGIN_CLASSES.has(input.frontend_origin_class)) event.frontend_origin_class = input.frontend_origin_class;
  if (input.provider_target_origin_class !== undefined && TARGET_ORIGIN_CLASSES.has(input.provider_target_origin_class)) event.provider_target_origin_class = input.provider_target_origin_class;
  if (input.frontend_provider_same_origin !== undefined && typeof input.frontend_provider_same_origin === "boolean") event.frontend_provider_same_origin = input.frontend_provider_same_origin;
  if (input.provider_request_url_strategy !== undefined && URL_STRATEGIES.has(input.provider_request_url_strategy)) event.provider_request_url_strategy = input.provider_request_url_strategy;
  if (input.request_credentials_mode !== undefined && CREDENTIALS_MODES.has(input.request_credentials_mode)) event.request_credentials_mode = input.request_credentials_mode;
  if (input.document_ready_state_at_fetch !== undefined && READY_STATES.has(input.document_ready_state_at_fetch)) event.document_ready_state_at_fetch = input.document_ready_state_at_fetch;
  if (input.navigation_state_at_fetch !== undefined && NAVIGATION_STATES.has(input.navigation_state_at_fetch)) event.navigation_state_at_fetch = input.navigation_state_at_fetch;
  if (input.document_generation_at_fetch !== undefined && Number.isInteger(input.document_generation_at_fetch) && input.document_generation_at_fetch >= 0 && input.document_generation_at_fetch <= 1_000_000) event.document_generation_at_fetch = input.document_generation_at_fetch;
  if (input.document_generation_at_rejection !== undefined && Number.isInteger(input.document_generation_at_rejection) && input.document_generation_at_rejection >= 0 && input.document_generation_at_rejection <= 1_000_000) event.document_generation_at_rejection = input.document_generation_at_rejection;
  if (input.navigation_started_during_fetch !== undefined && typeof input.navigation_started_during_fetch === "boolean") event.navigation_started_during_fetch = input.navigation_started_during_fetch;
  if (input.document_context_changed_during_fetch !== undefined && typeof input.document_context_changed_during_fetch === "boolean") event.document_context_changed_during_fetch = input.document_context_changed_during_fetch;
  if (input.abort_signal_attached_to_provider_fetch !== undefined && ABORT_SIGNAL_STATES.has(input.abort_signal_attached_to_provider_fetch)) event.abort_signal_attached_to_provider_fetch = input.abort_signal_attached_to_provider_fetch;
  if (input.abort_trigger_observed !== undefined && typeof input.abort_trigger_observed === "boolean") event.abort_trigger_observed = input.abort_trigger_observed;
  if (input.abort_trigger_source !== undefined && ABORT_SOURCES.has(input.abort_trigger_source)) event.abort_trigger_source = input.abort_trigger_source;
  if (input.fetch_rejection_error_class !== undefined && REJECTION_ERROR_CLASSES.has(input.fetch_rejection_error_class)) event.fetch_rejection_error_class = input.fetch_rejection_error_class;
  if (input.fetch_rejection_error_name !== undefined && REJECTION_ERROR_NAMES.has(input.fetch_rejection_error_name)) event.fetch_rejection_error_name = input.fetch_rejection_error_name;
  if (input.fetch_rejection_message_category !== undefined && REJECTION_MESSAGE_CATEGORIES.has(input.fetch_rejection_message_category)) event.fetch_rejection_message_category = input.fetch_rejection_message_category;
  if (input.fetch_failure_kind !== undefined && FETCH_FAILURE_KINDS.has(input.fetch_failure_kind)) event.fetch_failure_kind = input.fetch_failure_kind;
  if (input.fetch_failure_stage !== undefined && FETCH_FAILURE_STAGES.has(input.fetch_failure_stage)) event.fetch_failure_stage = input.fetch_failure_stage;
  if (input.safe_error_class !== undefined && SAFE_ERROR_CLASSES.has(input.safe_error_class)) {
    event.safe_error_class = input.safe_error_class;
  }
  if (input.failure_stage !== undefined && FAILURE_STAGES.has(input.failure_stage)) {
    event.failure_stage = input.failure_stage;
  }
  if (input.projection_processing_result !== undefined && PROJECTION_PROCESSING_RESULTS.has(input.projection_processing_result)) {
    event.projection_processing_result = input.projection_processing_result;
  }
  if (typeof input.request_id === "string" && input.request_id.length <= 64 && REQUEST_ID_PATTERN.test(input.request_id)) {
    event.request_id = input.request_id;
  }
  if (input.render_generation !== undefined && Number.isInteger(input.render_generation) && input.render_generation >= 0 && input.render_generation <= 1_000_000) {
    event.render_generation = input.render_generation;
  }
  return event;
}

function safeEvent(event: FrontendProviderDiagnosticEvent): Record<string, string | number | boolean> | null {
  if (!event || typeof event !== "object") return null;
  if (!isDiagnosticStage(event.stage)) return null;
  if (!DIAGNOSTIC_ROUTES.has(event.route)) return null;
  if (!(event.result === "ENTERED" || event.result === "RECEIVED" || event.result === "SUCCESS" || event.result === "FAILURE" || event.result === "NOT_APPLICABLE")) return null;
  return sanitizeFrontendProviderDiagnosticEvent(event);
}

export function newFrontendProviderFetchAttemptId(): string {
  nextFrontendProviderFetchAttempt += 1;
  return `provider-fetch-${nextFrontendProviderFetchAttempt}`;
}

/** Classify only by safe function-reference equality; wrappers remain OTHER. */
export function classifyProviderFetcher(fetcher: typeof fetch): FrontendProviderFetcherIdentity {
  const globalFetch = globalThis.fetch;
  const windowFetch = typeof window !== "undefined" ? window.fetch : undefined;
  if (typeof windowFetch === "function" && fetcher === windowFetch) {
    return fetcher === globalFetch ? "GLOBAL_BROWSER_FETCH" : "WINDOW_FETCH";
  }
  if (typeof globalFetch === "function" && fetcher === globalFetch) return "GLOBAL_THIS_FETCH";
  return "OTHER";
}

let documentGeneration = 0;
let navigationStarted = false;
let navigationListenersInstalled = false;

function installDocumentLifecycleObservers(): void {
  if (navigationListenersInstalled || typeof window === "undefined") return;
  navigationListenersInstalled = true;
  documentGeneration = Math.max(documentGeneration, 1);
  const markNavigation = () => {
    navigationStarted = true;
    documentGeneration += 1;
  };
  window.addEventListener("beforeunload", markNavigation, { once: false });
  window.addEventListener("pagehide", markNavigation, { once: false });
}

function originClass(url: URL): FrontendProviderOriginClass {
  if (url.protocol === "file:") return "FILE";
  if (url.protocol === "http:" && url.hostname === "127.0.0.1") return "LOOPBACK_HTTP";
  if (url.protocol === "http:" && url.hostname === "localhost") return "LOCALHOST_HTTP";
  if (url.protocol === "https:") return "HTTPS_REMOTE";
  if (url.protocol === "http:" || url.protocol === "https:") return "OTHER";
  return "CUSTOM_SCHEME";
}

function targetOriginClass(frontend: URL, target: URL, sameOrigin: boolean): FrontendProviderTargetOriginClass {
  if (sameOrigin) return "SAME_ORIGIN";
  const frontendLoopback = frontend.hostname === "127.0.0.1" || frontend.hostname === "localhost";
  const targetLoopback = target.hostname === "127.0.0.1" || target.hostname === "localhost";
  if (frontendLoopback && targetLoopback) return "LOOPBACK_CROSS_ORIGIN";
  if ((frontend.protocol !== target.protocol || frontend.hostname !== target.hostname) && targetLoopback) return "CUSTOM_TO_LOOPBACK";
  return "OTHER";
}

/** Classify origins using scheme, exact hostname, and effective port; localhost is not 127.0.0.1. */
export function classifyProviderOriginContext(frontendHref: string, targetHref: string, requestUrlStrategy: FrontendProviderRequestUrlStrategy = "RELATIVE_SAME_ORIGIN"): Pick<FrontendProviderFetchContext, "frontend_origin_class" | "provider_target_origin_class" | "frontend_provider_same_origin" | "provider_request_url_strategy"> {
  try {
    const frontend = new URL(frontendHref);
    const target = new URL(targetHref, frontend);
    const sameOrigin = frontend.protocol === target.protocol && frontend.hostname === target.hostname && frontend.port === target.port;
    return {
      frontend_origin_class: originClass(frontend),
      provider_target_origin_class: targetOriginClass(frontend, target, sameOrigin),
      frontend_provider_same_origin: sameOrigin,
      provider_request_url_strategy: requestUrlStrategy,
    };
  } catch {
    return {
      frontend_origin_class: "UNKNOWN",
      provider_target_origin_class: "UNKNOWN",
      frontend_provider_same_origin: false,
      provider_request_url_strategy: "OTHER",
    };
  }
}

function currentProviderFetchContext(): Omit<FrontendProviderFetchContext, "production_fetcher_identity"> {
  installDocumentLifecycleObservers();
  const frontendHref = typeof window === "undefined" ? "" : window.location.href;
  const origin = classifyProviderOriginContext(frontendHref, frontendHref ? new URL("/api/v1/copilot/desktop/system/provider", frontendHref).href : "", "RELATIVE_SAME_ORIGIN");
  const readyState = typeof document === "undefined" ? "UNKNOWN" : document.readyState === "loading" ? "LOADING" : document.readyState === "interactive" ? "INTERACTIVE" : document.readyState === "complete" ? "COMPLETE" : "UNKNOWN";
  return {
    ...origin,
    request_credentials_mode: "SAME_ORIGIN",
    document_ready_state_at_fetch: readyState,
    navigation_state_at_fetch: navigationStarted ? "NAVIGATING" : "STABLE",
    document_generation_at_fetch: documentGeneration,
    abort_signal_attached_to_provider_fetch: "NONE",
    abort_trigger_observed: false,
    abort_trigger_source: "NONE",
  };
}

/** Generate a correlation id containing no session, cookie, or provider material. */
export function newFrontendDiagnosticRequestId(): string | undefined {
  try {
    if (typeof globalThis.crypto?.randomUUID === "function") return globalThis.crypto.randomUUID();
    if (typeof globalThis.crypto?.getRandomValues === "function") {
      const bytes = new Uint8Array(16);
      globalThis.crypto.getRandomValues(bytes);
      return `webview-session-${Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("")}`;
    }
  } catch {
    return undefined;
  }
  return undefined;
}

/** Send only the fixed, content-free frontend evidence when WebView diagnostics are enabled. */
export function recordFrontendProviderDiagnostic(
  event: FrontendProviderDiagnosticEvent,
  fetcher: typeof fetch = fetch,
): Promise<boolean> {
  if (!isWebViewSessionDiagnosticEnabled()) return Promise.resolve(false);
  const payload = safeEvent(event);
  if (payload === null) return Promise.resolve(false);
  try {
    return Promise.resolve(fetcher(FRONTEND_DIAGNOSTIC_ROUTE, {
      method: "POST",
      credentials: "omit",
      keepalive: true,
      headers: {
        "Content-Type": "application/json",
        [DIAGNOSTIC_ORIGIN_HEADER]: "webview",
      },
      body: JSON.stringify(payload),
    })).then((response) => response.ok).catch(() => false);
  } catch {
    // Diagnostic transport is deliberately fire-and-forget and non-blocking.
    return Promise.resolve(false);
  }
}

export type ProviderProjectionDiagnosticValue = {
  credential_status: "CONFIGURED" | "NOT_CONFIGURED" | "UNAVAILABLE";
};

export type ProviderFetchFailureDetails = {
  fetchRejectionErrorClass?: FrontendProviderFetchRejectionErrorClass;
  fetchRejectionErrorName?: FrontendProviderFetchRejectionErrorName;
  fetchRejectionMessageCategory?: FrontendProviderFetchRejectionMessageCategory;
  fetchFailureKind?: FrontendProviderFetchFailureKind;
  fetchFailureStage?: FrontendProviderFetchFailureStage;
};

export function providerProjectionSemanticState(value: ProviderProjectionDiagnosticValue | null): {
  projection_state: FrontendProviderProjectionState;
  credential_state: FrontendProviderCredentialState;
} {
  if (value === null) return { projection_state: "NULL", credential_state: "UNKNOWN" };
  if (value.credential_status === "CONFIGURED") return { projection_state: "CONFIGURED", credential_state: "CONFIGURED" };
  if (value.credential_status === "NOT_CONFIGURED") return { projection_state: "UNCONFIGURED", credential_state: "UNCONFIGURED" };
  return { projection_state: "UNAVAILABLE", credential_state: "UNKNOWN" };
}

function safeDiagnosticCall(callback: () => void | Promise<boolean>): void {
  try {
    const delivery = callback();
    if (delivery !== undefined) void Promise.resolve(delivery).catch(() => undefined);
  } catch {
    // A custom diagnostic sink must never change provider behavior.
  }
}

export type ProviderProjectionDiagnosticSession = {
  request_id?: string;
  fetch_attempt_id: string;
  fetch_attempt_started_at: string;
  fetchBegin(): void;
  fetcherCallBegin(fetcherIdentity: FrontendProviderFetcherIdentity): void;
  fetcherCallResolved(): void;
  fetcherCallRejected(safeErrorClass: FrontendProviderSafeErrorClass): void;
  fetchPromiseResolved(): void;
  fetchPromiseRejected(safeErrorClass: FrontendProviderSafeErrorClass): void;
  responseReceived(httpStatus: number): void;
  jsonParseSuccess(): void;
  jsonParseFailure(): void;
  projectionProcessingSuccess(): void;
  projectionProcessingFailure(): void;
  stateCommit(value: ProviderProjectionDiagnosticValue): void;
  renderState(value: ProviderProjectionDiagnosticValue | null, renderGeneration: number): void;
  fetchSuccess(): void;
  fetchFailure(safeErrorClass: FrontendProviderSafeErrorClass, failureStage: FrontendProviderDiagnosticFailureStage, httpStatus?: number, details?: ProviderFetchFailureDetails): void;
};

export function createProviderProjectionDiagnosticSession(
  sink: FrontendProviderDiagnosticSink = recordFrontendProviderDiagnostic,
): ProviderProjectionDiagnosticSession {
  const request_id = newFrontendDiagnosticRequestId();
  const fetch_attempt_id = newFrontendProviderFetchAttemptId();
  const fetch_attempt_started_at = new Date().toISOString();
  const fetchAttemptStartedClock = diagnosticClock();
  let event_sequence = 0;
  let fetchContext: Omit<FrontendProviderFetchContext, "production_fetcher_identity"> | undefined;
  let productionFetcherIdentity: FrontendProviderFetcherIdentity = "UNKNOWN";
  const emit = (event: Omit<FrontendProviderDiagnosticEvent, "route" | "request_id" | "fetch_attempt_id" | "event_sequence">): void => {
    event_sequence += 1;
    safeDiagnosticCall(() => sink({
      ...event,
      route: "PROVIDER_PROJECTION",
      fetch_attempt_id,
      event_sequence,
      ...(request_id ? { request_id } : {}),
    }));
  };
  const terminalAttemptWindow = (): Pick<FrontendProviderDiagnosticEvent, "fetch_attempt_started_at" | "fetch_attempt_finished_at" | "fetch_attempt_duration_ms"> => ({
    fetch_attempt_started_at,
    fetch_attempt_finished_at: new Date().toISOString(),
    fetch_attempt_duration_ms: Math.max(0, Math.round(diagnosticClock() - fetchAttemptStartedClock)),
  });
  return {
    request_id,
    fetch_attempt_id,
    fetch_attempt_started_at,
    fetchBegin: () => {
      fetchContext = currentProviderFetchContext();
      emit({ stage: "WEBVIEW_FRONTEND_PROVIDER_FETCH_BEGIN", result: "ENTERED", projection_state: "LOADING" });
    },
    fetcherCallBegin: (fetcherIdentity) => {
      productionFetcherIdentity = fetcherIdentity;
      emit({ stage: "WEBVIEW_FRONTEND_PROVIDER_FETCHER_CALL_BEGIN", result: "ENTERED", fetcher_identity: fetcherIdentity, production_fetcher_identity: fetcherIdentity });
    },
    fetcherCallResolved: () => emit({ stage: "WEBVIEW_FRONTEND_PROVIDER_FETCHER_CALL_RESOLVED", result: "RECEIVED" }),
    fetcherCallRejected: (safeErrorClass) => emit({ stage: "WEBVIEW_FRONTEND_PROVIDER_FETCHER_CALL_REJECTED", result: "FAILURE", safe_error_class: safeErrorClass, failure_stage: "FETCH" }),
    fetchPromiseResolved: () => emit({ stage: "WEBVIEW_FRONTEND_PROVIDER_FETCH_PROMISE_RESOLVED", result: "RECEIVED" }),
    fetchPromiseRejected: (safeErrorClass) => emit({ stage: "WEBVIEW_FRONTEND_PROVIDER_FETCH_PROMISE_REJECTED", result: "FAILURE", safe_error_class: safeErrorClass, failure_stage: "FETCH" }),
    responseReceived: (httpStatus) => emit({ stage: "WEBVIEW_FRONTEND_PROVIDER_RESPONSE_RECEIVED", result: "RECEIVED", http_status: httpStatus }),
    jsonParseSuccess: () => emit({ stage: "WEBVIEW_FRONTEND_PROVIDER_JSON_PARSE_SUCCESS", result: "SUCCESS" }),
    jsonParseFailure: () => emit({ stage: "WEBVIEW_FRONTEND_PROVIDER_JSON_PARSE_FAILURE", result: "FAILURE", safe_error_class: "JSON_PARSE_ERROR", failure_stage: "JSON_PARSE" }),
    projectionProcessingSuccess: () => emit({ stage: "WEBVIEW_FRONTEND_PROVIDER_PROJECTION_PROCESSING_SUCCESS", result: "SUCCESS", projection_processing_result: "SUCCESS" }),
    projectionProcessingFailure: () => emit({ stage: "WEBVIEW_FRONTEND_PROVIDER_PROJECTION_PROCESSING_FAILURE", result: "FAILURE", safe_error_class: "PROJECTION_PROCESSING_ERROR", failure_stage: "PROJECTION_PROCESSING", projection_processing_result: "FAILURE" }),
    stateCommit: (value) => emit({ stage: "WEBVIEW_FRONTEND_PROVIDER_STATE_COMMIT", result: "RECEIVED", ...providerProjectionSemanticState(value) }),
    renderState: (value, renderGeneration) => emit({ stage: "WEBVIEW_FRONTEND_PROVIDER_RENDER_STATE", result: "SUCCESS", render_generation: renderGeneration, provider_present: value !== null, ...providerProjectionSemanticState(value) }),
    fetchSuccess: () => emit({ stage: "WEBVIEW_FRONTEND_PROVIDER_FETCH_SUCCESS", result: "SUCCESS", projection_processing_result: "NOT_APPLICABLE", ...terminalAttemptWindow() }),
    fetchFailure: (safeErrorClass, failureStage, httpStatus, details = {}) => {
      const context = fetchContext ?? currentProviderFetchContext();
      const fetchFailureKind = details.fetchFailureKind ?? (
        failureStage === "FETCH" ? "PROMISE_REJECTION" : failureStage === "HTTP_STATUS" ? "HTTP_STATUS" : failureStage === "JSON_PARSE" ? "JSON_PARSE" : "UNKNOWN"
      );
      const fetchFailureStage = details.fetchFailureStage ?? (
        failureStage === "FETCH" ? "FETCH_CALL" : failureStage === "HTTP_STATUS" ? "HTTP_STATUS" : failureStage === "JSON_PARSE" ? "RESPONSE_BODY" : "UNKNOWN"
      );
      const rejectionContext = {
        ...context,
        document_generation_at_rejection: documentGeneration,
        navigation_started_during_fetch: navigationStarted,
        document_context_changed_during_fetch: documentGeneration !== context.document_generation_at_fetch,
        fetch_rejection_error_class: details.fetchRejectionErrorClass ?? "UNKNOWN",
        fetch_rejection_error_name: details.fetchRejectionErrorName ?? "Unknown",
        fetch_rejection_message_category: details.fetchRejectionMessageCategory ?? (fetchFailureKind === "PROMISE_REJECTION" || fetchFailureKind === "ABORT" ? "UNKNOWN" : "EMPTY"),
        fetch_failure_kind: fetchFailureKind,
        fetch_failure_stage: fetchFailureStage,
      };
      emit({ stage: "WEBVIEW_FRONTEND_PROVIDER_FETCH_FAILURE", result: "FAILURE", ...(httpStatus === undefined ? {} : { http_status: httpStatus }), safe_error_class: safeErrorClass, failure_stage: failureStage, production_fetcher_identity: productionFetcherIdentity, ...rejectionContext, ...terminalAttemptWindow() });
    },
  };
}

export type FrontendDiagnosticSequenceAnalysis = {
  fetch_attempt_id: string;
  observed_sequences: number[];
  missing_sequences: number[];
  diagnostic_event_loss_confirmed: boolean;
};

export function analyzeFrontendDiagnosticSequences(
  events: readonly Pick<FrontendProviderDiagnosticEvent, "fetch_attempt_id" | "event_sequence">[],
): FrontendDiagnosticSequenceAnalysis[] {
  const byAttempt = new Map<string, Set<number>>();
  for (const event of events) {
    if (!event.fetch_attempt_id || event.event_sequence === undefined) continue;
    const sequences = byAttempt.get(event.fetch_attempt_id) ?? new Set<number>();
    if (Number.isInteger(event.event_sequence) && event.event_sequence >= 1) sequences.add(event.event_sequence);
    byAttempt.set(event.fetch_attempt_id, sequences);
  }
  return Array.from(byAttempt.entries()).map(([fetch_attempt_id, sequenceSet]) => {
    const observed_sequences = Array.from(sequenceSet).sort((left, right) => left - right);
    const maxSequence = observed_sequences.at(-1) ?? 0;
    const observed = new Set(observed_sequences);
    const missing_sequences = Array.from({ length: maxSequence }, (_, index) => index + 1).filter((sequence) => !observed.has(sequence));
    return { fetch_attempt_id, observed_sequences, missing_sequences, diagnostic_event_loss_confirmed: missing_sequences.length > 0 };
  });
}

/**
 * The launcher adds this harmless query marker only when the independent
 * backend diagnostic flag is enabled.  It carries no session material.
 */
export function isWebViewSessionDiagnosticEnabled(): boolean {
  if (typeof window === "undefined") return false;
  return new URLSearchParams(window.location.search).get(DIAGNOSTIC_QUERY_PARAMETER) === "1";
}

/** Add the fixed origin marker only to diagnostic-mode WebView requests. */
export function webViewDiagnosticOriginHeaders(): Record<string, string> {
  return isWebViewSessionDiagnosticEnabled()
    ? { [DIAGNOSTIC_ORIGIN_HEADER]: "webview" }
    : {};
}
